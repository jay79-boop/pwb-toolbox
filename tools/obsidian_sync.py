#!/usr/bin/env python
"""Mirror an Obsidian vault into docs/journal as plain markdown.

    python tools/obsidian_sync.py vaults          # every vault this machine knows
    python tools/obsidian_sync.py sync --dry-run  # finds the vault by itself
    python tools/obsidian_sync.py sync --vault "C:\\path\\to\\vault" --commit --push

`--vault` is optional. Obsidian records every vault it has opened, with its
absolute path, in `obsidian.json` (`%APPDATA%\\obsidian` on Windows,
`~/Library/Application Support/obsidian` on macOS, `~/.config/obsidian` on
Linux), so the path is a fact already on disk rather than something to hunt
for; failing that, folders holding a `.obsidian/` directory are scanned for.
Discovery refuses to guess between two vaults, and when it finds none it
prints everywhere it looked. Use `--vault` to override it.

This only runs where the vault's files are readable — a local machine or WSL,
never a cloud session, which has no access to your disk. `docs/journal` is
treated as fully generated: each run wipes and rewrites it, so anything
hand-authored there will be lost. A `.obsidian-sync-marker` file guards
against wiping a directory this tool did not create; pass --force to bypass
it (e.g. on the very first run against a docs/journal that already holds
something else).

A vault that is itself a git repo has its `.gitignore` honoured too, so a
curated list of what must not be committed is reused rather than rewritten;
`--no-gitignore` turns that off. Exclude anything else that should never leave
the vault by adding lines to a `.syncignore` file at the vault root (gitignore-style glob patterns, one per
line, '#' comments allowed). `.obsidian/`, `.trash/`, `.git/`, and any other
dotfile or dotfolder are always excluded, along with OS junk files.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import click

MARKER_NAME = ".obsidian-sync-marker"
DEFAULT_JUNK_NAMES = {"Thumbs.db", "desktop.ini"}

_WIKILINK_RE = re.compile(
    r"(?P<embed>!)?\[\[(?P<target>[^\]|#]+)(?P<heading>#[^\]|]+)?(?:\|(?P<alias>[^\]]+))?\]\]"
)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


# --- Finding the vault -------------------------------------------------------
#
# Obsidian writes every vault it has ever opened into `obsidian.json`, with the
# absolute path of each. So a vault's location is a fact already recorded on the
# machine, not something to go hunting for or type by hand -- and asking for it
# was costing a round trip every time. Discovery lives in the tool so the
# question is answered here rather than delegated.

VAULT_REGISTRY_NAME = "obsidian.json"
_FS_SCAN_MAX_DEPTH = 4

# Directories a scan must not descend into: either enormous, or full of
# `.obsidian`-shaped false positives.
_SCAN_SKIP_NAMES = {
    "$RECYCLE.BIN",
    "AppData",
    "Application Data",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "System Volume Information",
    "Windows",
    "node_modules",
    "venv",
}

_WINDOWS_DRIVE_RE = re.compile(r"([A-Za-z]):[\\/](.*)")


@dataclass(frozen=True)
class VaultCandidate:
    """One vault this machine knows about, and how we came to know about it."""

    path: Path
    source: str  # "registry" (obsidian.json) or "scan" (found on disk)
    last_opened: int = 0  # obsidian.json `ts`, 0 when unknown
    is_open: bool = False
    exists: bool = True


def _translate_windows_path(raw: str) -> Path:
    """Map a `C:\\...` registry path onto `/mnt/c/...` when running under WSL.

    Only when the translated path really is a directory: otherwise the original
    is kept so an error message shows the path Obsidian actually recorded.
    """
    match = _WINDOWS_DRIVE_RE.fullmatch(raw)
    if match and Path("/mnt").is_dir():
        drive, rest = match.groups()
        translated = Path("/mnt") / drive.lower() / rest.replace("\\", "/")
        if translated.is_dir():
            return translated
    return Path(raw)


def obsidian_config_dirs() -> list[Path]:
    """Every directory that could hold `obsidian.json`, most likely first.

    Covers the three desktop platforms plus two cases that bite here: the
    Flatpak install on Linux, and WSL reaching the Windows-side registry under
    `/mnt/c`, since this tool is documented as runnable from WSL.
    """
    candidates: list[Path] = []
    home = Path.home()

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "obsidian")
    candidates.append(home / "AppData" / "Roaming" / "obsidian")

    candidates.append(home / "Library" / "Application Support" / "obsidian")

    xdg = os.environ.get("XDG_CONFIG_HOME")
    candidates.append(Path(xdg) / "obsidian" if xdg else home / ".config" / "obsidian")
    candidates.append(
        home / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian"
    )

    windows_users = Path("/mnt/c/Users")
    try:
        for user_dir in sorted(windows_users.iterdir()):
            if user_dir.is_dir():
                candidates.append(user_dir / "AppData" / "Roaming" / "obsidian")
    except OSError:
        pass

    ordered: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def read_vault_registry(config_dir: Path) -> list[VaultCandidate]:
    """Parse the `vaults` map out of one `obsidian.json`.

    A missing, unreadable or corrupt registry yields nothing rather than
    raising: the filesystem scan is still a good answer, and a hard failure
    here would put the tool straight back to asking for a path.
    """
    registry = Path(config_dir) / VAULT_REGISTRY_NAME
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    vaults = data.get("vaults") if isinstance(data, dict) else None
    if not isinstance(vaults, dict):
        return []

    found: list[VaultCandidate] = []
    for entry in vaults.values():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("path")
        if not raw or not isinstance(raw, str):
            continue
        path = _translate_windows_path(raw)
        try:
            timestamp = int(entry.get("ts") or 0)
        except (TypeError, ValueError):
            timestamp = 0
        found.append(
            VaultCandidate(
                path=path,
                source="registry",
                last_opened=timestamp,
                is_open=bool(entry.get("open")),
                exists=path.is_dir(),
            )
        )
    return found


def default_scan_roots() -> list[Path]:
    """Where to look for a vault when no registry names one."""
    roots = [Path.home()]
    try:
        for user_dir in sorted(Path("/mnt/c/Users").iterdir()):
            if user_dir.is_dir() and user_dir.name not in {"Public", "Default"}:
                roots.append(user_dir)
    except OSError:
        pass
    return roots


def scan_for_vaults(
    roots: list[Path], max_depth: int = _FS_SCAN_MAX_DEPTH
) -> list[VaultCandidate]:
    """Find directories holding a `.obsidian/` folder, breadth-first.

    The fallback for a vault the registry does not name -- one synced in by
    Dropbox or iCloud that Obsidian has not been pointed at yet. Depth-limited
    because the alternative is walking a whole disk to answer a question the
    registry usually answers instantly.
    """
    found: list[VaultCandidate] = []
    seen: set[Path] = set()
    frontier: list[tuple[Path, int]] = [(Path(root), 0) for root in roots]

    while frontier:
        current, depth = frontier.pop(0)
        if not current.is_dir():
            continue
        try:
            resolved = current.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)

        if (current / ".obsidian").is_dir():
            # A folder inside a vault is not a second vault; stop descending.
            found.append(VaultCandidate(path=current, source="scan"))
            continue
        if depth >= max_depth:
            continue

        try:
            children = sorted(p for p in current.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith(".") or child.name in _SCAN_SKIP_NAMES:
                continue
            frontier.append((child, depth + 1))
    return found


def discover_vaults() -> list[VaultCandidate]:
    """Every vault this machine knows about, best candidate first.

    Registry entries win over scanned ones for the same path, because only the
    registry carries the timestamps that make "the one you actually use"
    orderable. Order is: still on disk, then currently open, then most recently
    opened, then path.
    """
    found: dict[Path, VaultCandidate] = {}

    for config_dir in obsidian_config_dirs():
        for candidate in read_vault_registry(config_dir):
            found.setdefault(candidate.path, candidate)
    for candidate in scan_for_vaults(default_scan_roots()):
        found.setdefault(candidate.path, candidate)

    return sorted(
        found.values(),
        key=lambda c: (not c.exists, not c.is_open, -c.last_opened, str(c.path)),
    )


def no_vault_message() -> str:
    """Explain where we looked, so 'not found' is a finding and not a shrug."""
    lines = ["No Obsidian vault found on this machine.", ""]
    lines.append("Looked for Obsidian's vault registry (obsidian.json) in:")
    for config_dir in obsidian_config_dirs():
        mark = "found" if (config_dir / VAULT_REGISTRY_NAME).exists() else "absent"
        lines.append(f"  [{mark}] {config_dir}")
    lines.append("")
    lines.append(f"...and scanned {_FS_SCAN_MAX_DEPTH} levels below:")
    for root in default_scan_roots():
        lines.append(f"  {root}")
    lines.append("")
    lines.append(
        "An absent registry everywhere means Obsidian has never been opened on "
        "this machine, so there is no local vault to sync. Get the vault onto "
        "this disk (Obsidian Sync, iCloud, Dropbox, or a plain copy), open it "
        "in Obsidian once, and this will find it on its own from then on."
    )
    return "\n".join(lines)


def _ambiguous_vault_message(candidates: list[VaultCandidate]) -> str:
    lines = [f"Found {len(candidates)} Obsidian vaults; name the one to sync.", ""]
    for candidate in candidates:
        marks = " (open in Obsidian)" if candidate.is_open else ""
        lines.append(f'  --vault "{candidate.path}"{marks}')
    lines.append("")
    lines.append(
        "This tool wipes and rewrites docs/journal, so it will not guess "
        "between vaults."
    )
    return "\n".join(lines)


def resolve_vault(explicit: Path | None = None) -> Path:
    """Return the vault to sync, discovering it when `--vault` was not given.

    Both failure modes raise with the whole picture -- where we looked, or what
    we found -- so the error message is itself the answer rather than a prompt
    to go looking.
    """
    if explicit is not None:
        return Path(explicit)

    candidates = [c for c in discover_vaults() if c.exists]
    if not candidates:
        raise click.ClickException(no_vault_message())
    if len(candidates) > 1:
        raise click.ClickException(_ambiguous_vault_message(candidates))
    return candidates[0].path


def load_syncignore(vault_root: Path) -> list[str]:
    """Read `.syncignore` patterns from the vault root, if present."""
    path = vault_root / ".syncignore"
    if not path.exists():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _matches_ignore(rel_posix: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        pat = pattern.rstrip("/")
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, f"{pat}/*"):
            return True
        if fnmatch.fnmatch(PurePosixPath(rel_posix).name, pat):
            return True
    return False


def gitignored_paths(vault_root: Path, rel_paths: list[Path]) -> set[Path]:
    """Ask git which of `rel_paths` the vault's own ignore rules exclude.

    Delegating to `git check-ignore` beats parsing `.gitignore` here: it gets
    nested ignore files, negation and precedence exactly right, and it consults
    the index, so a file the owner deliberately tracks is never reported as
    ignored. The point is reuse - a vault that is itself a git repo already has
    a curated list of what must not be committed, maintained by the person who
    knows why. That list is a better exclusion set than anything this tool
    would infer, and it is already correct.

    Returns an empty set when the vault is not a git repo, when git is missing,
    or on any git error: the caller still has `.syncignore` and the dotfile
    rules, and a hard failure here would block a legitimate sync.
    """
    if not rel_paths:
        return set()
    payload = "\0".join(p.as_posix() for p in rel_paths) + "\0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault_root), "check-ignore", "--stdin", "-z"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    # 0 = some ignored, 1 = none ignored; anything else (128 = not a repo) is
    # not an answer, so fall back to excluding nothing.
    if proc.returncode not in (0, 1):
        return set()
    return {Path(name) for name in proc.stdout.split("\0") if name}


def iter_vault_files(
    vault_root: Path,
    ignore_patterns: list[str],
    respect_gitignore: bool = True,
) -> list[Path]:
    """List every file in the vault that should be mirrored, in a stable order.

    Three exclusion sources, narrowest last: dotfiles and OS junk, the vault's
    `.syncignore`, and - when the vault is a git repo - its own ignore rules.
    """
    result = []
    for path in sorted(vault_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(vault_root)
        if any(_is_hidden(part) for part in rel.parts):
            continue
        if path.name in DEFAULT_JUNK_NAMES:
            continue
        rel_posix = rel.as_posix()
        if rel_posix == ".syncignore":
            continue
        if _matches_ignore(rel_posix, ignore_patterns):
            continue
        result.append(path)

    if respect_gitignore and result:
        rels = [p.relative_to(vault_root) for p in result]
        ignored = gitignored_paths(vault_root, rels)
        if ignored:
            result = [p for p in result if p.relative_to(vault_root) not in ignored]
    return result


def build_link_index(vault_root: Path, note_paths: list[Path]) -> dict[str, Path]:
    """Map a note's bare stem (lowercase) to its vault-relative path.

    Obsidian resolves [[Wikilinks]] by filename regardless of folder, so the
    first note wins on a stem collision - good enough for a one-way mirror.
    """
    index: dict[str, Path] = {}
    for path in note_paths:
        stem = path.stem.lower()
        if stem not in index:
            index[stem] = path.relative_to(vault_root)
    return index


def _heading_anchor(heading: str) -> str:
    slug = heading.lstrip("#").strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def convert_note(
    text: str,
    note_rel_path: Path,
    link_index: dict[str, Path],
    referenced_assets: set[Path],
) -> str:
    """Rewrite Obsidian wikilinks/embeds in `text` as plain markdown.

    Resolved note links become relative markdown links; resolved non-note
    embeds are recorded into `referenced_assets` (relative to the vault root)
    so the caller can copy them alongside the converted notes. Anything that
    does not resolve in `link_index` degrades to plain text rather than a
    dead link.
    """
    note_dir = note_rel_path.parent

    def _relative(target_rel: Path) -> str:
        rel_str = os.path.relpath(
            target_rel.as_posix(), start=note_dir.as_posix() or "."
        )
        return PurePosixPath(rel_str).as_posix()

    def _replace(match: re.Match) -> str:
        target = match.group("target").strip()
        heading = match.group("heading")
        alias = match.group("alias")
        is_embed = bool(match.group("embed"))
        stem = Path(target).stem.lower() if "." in Path(target).name else target.lower()

        resolved = link_index.get(stem) or link_index.get(Path(target).stem.lower())
        label = (alias or Path(target).name).strip()

        if resolved is None:
            return label

        if is_embed and resolved.suffix.lower() != ".md":
            referenced_assets.add(resolved)
            href = _relative(resolved)
            return f"![{label}]({href})"

        href = _relative(resolved)
        if heading:
            href = f"{href}#{_heading_anchor(heading)}"
        return f"[{label}]({href})"

    return _WIKILINK_RE.sub(_replace, text)


@dataclass
class SyncResult:
    notes_written: int = 0
    assets_copied: int = 0
    skipped: list[Path] = field(default_factory=list)
    gitignored: int = 0


def sync_vault(
    vault_root: Path,
    output_dir: Path,
    dry_run: bool = False,
    force: bool = False,
    respect_gitignore: bool = True,
) -> SyncResult:
    """Mirror `vault_root` into `output_dir`, converting notes to plain markdown."""
    vault_root = Path(vault_root)
    output_dir = Path(output_dir)

    if output_dir.exists():
        has_marker = (output_dir / MARKER_NAME).exists()
        has_content = any(output_dir.iterdir())
        if has_content and not has_marker and not force:
            raise click.ClickException(
                f"{output_dir} exists and wasn't created by this tool "
                "(no .obsidian-sync-marker found). Refusing to overwrite it. "
                "Pass --force if it's safe to replace."
            )

    ignore_patterns = load_syncignore(vault_root)
    all_files = iter_vault_files(
        vault_root, ignore_patterns, respect_gitignore=respect_gitignore
    )
    withheld = 0
    if respect_gitignore:
        withheld = len(
            iter_vault_files(vault_root, ignore_patterns, respect_gitignore=False)
        ) - len(all_files)
    note_paths = [p for p in all_files if p.suffix.lower() == ".md"]
    link_index = build_link_index(vault_root, all_files)

    referenced_assets: set[Path] = set()
    converted: dict[Path, str] = {}
    for note_path in note_paths:
        rel = note_path.relative_to(vault_root)
        text = note_path.read_text(encoding="utf-8")
        converted[rel] = convert_note(text, rel, link_index, referenced_assets)

    result = SyncResult(gitignored=withheld)
    if dry_run:
        result.notes_written = len(converted)
        result.assets_copied = len(referenced_assets)
        return result

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    for rel, text in converted.items():
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        result.notes_written += 1

    for rel in sorted(referenced_assets):
        src = vault_root / rel
        if not src.exists():
            result.skipped.append(rel)
            continue
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        result.assets_copied += 1

    (output_dir / MARKER_NAME).write_text(
        "Generated by tools/obsidian_sync.py - do not edit by hand.\n",
        encoding="utf-8",
    )
    return result


def assert_publish_is_deliberate(vault_root: Path, allow_publish: bool) -> None:
    """Refuse to commit a vault mirror that nothing has been excluded from.

    `docs/journal` is committed rather than gitignored, and this fork is public
    - the same reason `engagements/`, `spec_desk/`, `night_lab/` and `season/`
    are ignored. A `.syncignore` at the vault root is the only thing standing
    between a personal note and a public commit, and a push cannot be taken
    back: history, forks and GitHub's caches all keep it. So an absent
    `.syncignore` stops a `--commit`/`--push` rather than quietly publishing
    every note. `--dry-run` and a plain `sync` never reach here.
    """
    if allow_publish or (Path(vault_root) / ".syncignore").exists():
        return
    raise click.ClickException(
        f"Refusing to commit: no .syncignore at {vault_root}.\n"
        "\n"
        "docs/journal is committed, not gitignored, and this fork is public, so "
        "--commit/--push publishes every note in the vault - and a push cannot "
        "be taken back, because history, forks and caches all keep it.\n"
        "\n"
        "Either:\n"
        f"  - write {vault_root / '.syncignore'} listing what must never leave "
        "the vault (gitignore-style globs, one per line), then re-run; or\n"
        "  - pass --allow-publish if the whole vault really is safe to publish.\n"
        "\n"
        "Neither --dry-run nor a plain sync (no --commit) is affected."
    )


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def commit_and_push(
    repo: Path,
    output_dir: Path,
    result: SyncResult,
    push: bool,
    remote: str | None,
    branch: str | None,
) -> str:
    """Stage and commit docs/journal, optionally pushing. Returns a status message."""
    rel = output_dir.relative_to(repo).as_posix()
    add = _run_git(repo, "add", rel)
    if add.returncode != 0:
        raise click.ClickException(f"git add failed:\n{add.stderr}")

    diff = _run_git(repo, "diff", "--cached", "--quiet", "--", rel)
    if diff.returncode == 0:
        return "no changes to commit"

    message = (
        f"Sync Obsidian vault into docs/journal "
        f"({result.notes_written} notes, {result.assets_copied} assets)"
    )
    commit = _run_git(repo, "commit", "-m", message)
    if commit.returncode != 0:
        raise click.ClickException(f"git commit failed:\n{commit.stderr}")

    if not push:
        return "committed"

    push_args = ["push"]
    if remote:
        push_args.append(remote)
        if branch:
            push_args.append(branch)
    pushed = _run_git(repo, *push_args)
    if pushed.returncode != 0:
        raise click.ClickException(f"git push failed:\n{pushed.stderr}")
    return "committed and pushed"


@click.group()
def cli() -> None:
    """Sync an Obsidian vault into docs/journal as plain markdown."""


@cli.command()
@click.option(
    "--vault",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the Obsidian vault root. Omit to discover it automatically.",
)
@click.option(
    "--repo",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the pwb-toolbox checkout (docs/journal lives under here).",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would change without writing anything."
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite docs/journal even without a marker from a prior sync.",
)
@click.option("--commit", is_flag=True, help="git commit the result in --repo.")
@click.option(
    "--push", is_flag=True, help="git push after committing (implies --commit)."
)
@click.option(
    "--allow-publish",
    is_flag=True,
    help="Commit without a .syncignore. This fork is public - be sure.",
)
@click.option(
    "--no-gitignore",
    is_flag=True,
    help="Mirror files the vault's own .gitignore excludes. Rarely what you want.",
)
@click.option(
    "--remote",
    default=None,
    help="git remote to push to (defaults to the branch's configured upstream).",
)
@click.option(
    "--branch",
    default=None,
    help="git branch to push to (defaults to the current branch).",
)
def sync(
    vault: Path | None,
    repo: Path,
    dry_run: bool,
    force: bool,
    commit: bool,
    push: bool,
    allow_publish: bool,
    no_gitignore: bool,
    remote: str | None,
    branch: str | None,
) -> None:
    """Mirror --vault into <repo>/docs/journal.

    With no --vault, the vault is discovered from Obsidian's own registry.
    """
    vault_root = resolve_vault(vault)
    if vault is None:
        click.echo(f"vault: {vault_root}")

    if commit or push:
        assert_publish_is_deliberate(vault_root, allow_publish)

    output_dir = repo / "docs" / "journal"
    result = sync_vault(
        vault_root,
        output_dir,
        dry_run=dry_run,
        force=force,
        respect_gitignore=not no_gitignore,
    )
    if result.gitignored:
        click.echo(
            f"held back {result.gitignored} file(s) the vault's own .gitignore excludes"
        )

    if dry_run:
        click.echo(
            f"would write {result.notes_written} notes, {result.assets_copied} assets"
        )
        return

    click.echo(
        f"wrote {result.notes_written} notes, {result.assets_copied} assets to {output_dir}"
    )
    if result.skipped:
        click.echo(
            f"skipped {len(result.skipped)} embed(s) whose target file was missing:"
        )
        for rel in result.skipped:
            click.echo(f"  {rel}")

    if commit or push:
        status = commit_and_push(
            repo, output_dir, result, push=push, remote=remote, branch=branch
        )
        click.echo(status)


@cli.command("vaults")
def list_vaults() -> None:
    """List every Obsidian vault this machine knows about."""
    candidates = discover_vaults()
    if not candidates:
        raise click.ClickException(no_vault_message())

    for candidate in candidates:
        marks = []
        if candidate.is_open:
            marks.append("open in Obsidian")
        if not candidate.exists:
            marks.append("MISSING from disk")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        click.echo(f"{candidate.source:8} {candidate.path}{suffix}")

    usable = [c for c in candidates if c.exists]
    if len(usable) == 1:
        click.echo("")
        click.echo("One usable vault - `sync` will find it with no --vault needed.")


if __name__ == "__main__":
    cli()

#!/usr/bin/env python
"""Mirror an Obsidian vault into docs/journal as plain markdown.

    python tools/obsidian_sync.py sync --vault "C:\\path\\to\\vault" --dry-run
    python tools/obsidian_sync.py sync --vault "C:\\path\\to\\vault" --commit --push

This only runs where the vault's files are readable — a local machine or WSL,
never a cloud session, which has no access to your disk. `docs/journal` is
treated as fully generated: each run wipes and rewrites it, so anything
hand-authored there will be lost. A `.obsidian-sync-marker` file guards
against wiping a directory this tool did not create; pass --force to bypass
it (e.g. on the very first run against a docs/journal that already holds
something else).

Exclude anything that should never leave the vault by adding lines to a
`.syncignore` file at the vault root (gitignore-style glob patterns, one per
line, '#' comments allowed). `.obsidian/`, `.trash/`, `.git/`, and any other
dotfile or dotfolder are always excluded, along with OS junk files.
"""

from __future__ import annotations

import fnmatch
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


def iter_vault_files(vault_root: Path, ignore_patterns: list[str]) -> list[Path]:
    """List every file in the vault that should be mirrored, in a stable order."""
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


def sync_vault(
    vault_root: Path, output_dir: Path, dry_run: bool = False, force: bool = False
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
    all_files = iter_vault_files(vault_root, ignore_patterns)
    note_paths = [p for p in all_files if p.suffix.lower() == ".md"]
    link_index = build_link_index(vault_root, all_files)

    referenced_assets: set[Path] = set()
    converted: dict[Path, str] = {}
    for note_path in note_paths:
        rel = note_path.relative_to(vault_root)
        text = note_path.read_text(encoding="utf-8")
        converted[rel] = convert_note(text, rel, link_index, referenced_assets)

    result = SyncResult()
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
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the Obsidian vault root.",
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
    vault: Path,
    repo: Path,
    dry_run: bool,
    force: bool,
    commit: bool,
    push: bool,
    remote: str | None,
    branch: str | None,
) -> None:
    """Mirror --vault into <repo>/docs/journal."""
    output_dir = repo / "docs" / "journal"
    result = sync_vault(vault, output_dir, dry_run=dry_run, force=force)

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


if __name__ == "__main__":
    cli()

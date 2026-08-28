"""Let every Claude Code session reach every repo on this machine, not just one.

A session can read and edit files in the directory it was launched from and
nowhere else. That is why a new chat started in pwb-toolbox cannot see the trade
journal, the vault, or any other checkout without being told about each one by
hand, every time.

Claude Code reads `permissions.additionalDirectories` from user-level settings
(`~/.claude/settings.json`) and applies it to EVERY project, so that is where a
machine-wide answer belongs. This scans for git repositories, shows what it
found, and registers them there.

    python tools/install_workspace_dirs.py --scan    # list repos, touch nothing
    python tools/install_workspace_dirs.py --check   # report the diff, write nothing
    python tools/install_workspace_dirs.py           # install
    python tools/install_workspace_dirs.py --prune   # also drop entries that are gone

What this grants is FILE ACCESS, and only that. A directory listed under this key
is readable and editable without prompts, but Claude Code does not load its
`CLAUDE.md`, its skills, its hooks or its MCP servers. To pick those up, move the
session with `/cd <path>`, or launch with `--add-dir <path>`.

`.claude` directories are skipped on purpose. `~/.claude/projects` holds session
transcripts carrying SSNs, claim numbers and financial detail, and this key grants
unprompted read access to every session on the machine. Pass --include-claude to
override that.

A cloud session cannot run this: `~/.claude` there belongs to a container that is
reclaimed when the session ends. It has to run where the sessions actually run.

Deliberately ASCII-only and stdlib-only. Windows PowerShell 5.1 reads a BOM-less
file as Windows-1252, and one non-ASCII byte can stop a script parsing at all.
"""

import argparse
import json
import os
import shutil

SETTINGS_KEY = "additionalDirectories"
CLAUDE_DIR = ".claude"
DEFAULT_DEPTH = 3

# Noise, not projects. Descending into these is slow and finds nothing: AppData
# alone can hold thousands of vendored repos under package caches.
SKIP_NAMES = frozenset(
    [
        "$RECYCLE.BIN",
        ".cache",
        ".git",
        ".idea",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".tox",
        ".venv",
        ".vscode",
        "AppData",
        "OneDriveTemp",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "venv",
    ]
)


def claude_home():
    return os.path.join(os.path.expanduser("~"), ".claude")


def key_of(path):
    """Compare paths the way the filesystem does, without rewriting them."""

    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def is_repo(path):
    # A worktree's `.git` is a file, not a directory, so test for either.
    return os.path.exists(os.path.join(path, ".git"))


def find_repos(roots, depth=DEFAULT_DEPTH, include_claude=False):
    """Directories holding a `.git`, breadth-first, not descending into one found.

    Stopping at the first repo on a branch is what keeps submodules, vendored
    dependencies and the desktop app's `.claude/worktrees/` copies out of the
    list -- each is a repo, none is a project the owner works in.
    """

    found = []
    seen = set()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        frontier = [(root, 0)]
        while frontier:
            current, level = frontier.pop(0)
            marker = key_of(current)
            if marker in seen:
                continue
            seen.add(marker)
            if is_repo(current):
                found.append(current)
                continue
            if level >= depth:
                continue
            try:
                with os.scandir(current) as entries:
                    children = sorted(entries, key=lambda e: e.name)
            except OSError:
                continue
            for entry in children:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if entry.name in SKIP_NAMES:
                    continue
                if entry.name == CLAUDE_DIR and not include_claude:
                    continue
                frontier.append((entry.path, level + 1))
    return sorted(found, key=key_of)


def read_settings(path):
    """Return (settings, error). A file we cannot parse is never overwritten."""

    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError:
        return None, "settings.json is not valid JSON -- not touched"
    if not isinstance(data, dict):
        return None, "settings.json is not a JSON object -- not touched"
    return data, None


def current_dirs(settings):
    perms = settings.get("permissions")
    if not isinstance(perms, dict):
        return []
    listed = perms.get(SETTINGS_KEY)
    if not isinstance(listed, list):
        return []
    return [item for item in listed if isinstance(item, str)]


def plan(existing, found, prune=False):
    """Work out the new list without mutating anything.

    Entries already in the file keep their original spelling: the owner may have
    written one by hand, and rewriting it to an absolute path would read as this
    tool having lost it.
    """

    have = {}
    order = []
    for entry in existing:
        marker = key_of(entry)
        if marker not in have:
            have[marker] = entry
            order.append(marker)

    added = []
    for repo in found:
        marker = key_of(repo)
        if marker not in have:
            have[marker] = repo
            order.append(marker)
            added.append(repo)

    dropped = []
    if prune:
        for marker in list(order):
            if not os.path.isdir(os.path.expanduser(have[marker])):
                dropped.append(have.pop(marker))
                order.remove(marker)

    keep = sorted((have[marker] for marker in order), key=key_of)
    return keep, added, dropped


def write_settings(path, settings, keep):
    """Merge the key in place, leaving every other setting exactly as it was."""

    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict):
        return "permissions is not a JSON object -- not touched"
    perms[SETTINGS_KEY] = keep
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report what would change, write nothing"
    )
    parser.add_argument(
        "--scan", action="store_true", help="list the repos found and stop"
    )
    parser.add_argument(
        "--root",
        action="append",
        metavar="PATH",
        help="where to look; repeatable (default: your home directory)",
    )
    parser.add_argument(
        "--add",
        action="append",
        metavar="PATH",
        help="register this path too, repo or not; repeatable",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        help="levels below each root to search (default %d)" % DEFAULT_DEPTH,
    )
    parser.add_argument(
        "--include-claude",
        action="store_true",
        help="do not skip .claude directories (see the module docstring)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="drop registered directories that no longer exist",
    )
    args = parser.parse_args(argv)

    roots = args.root or [os.path.expanduser("~")]
    found = find_repos(roots, args.depth, args.include_claude)
    for extra in args.add or []:
        resolved = os.path.abspath(os.path.expanduser(extra))
        if key_of(resolved) not in {key_of(item) for item in found}:
            found.append(resolved)

    print("Searched %s, %d level(s) deep." % (", ".join(roots), args.depth))
    print("Found %d director%s:" % (len(found), "y" if len(found) == 1 else "ies"))
    for repo in found:
        print("    %s" % repo)
    if not found:
        print("    (none -- try --depth 4, or --root PATH to look somewhere else)")

    if args.scan:
        print("\n--scan: settings were neither read nor written.")
        return 0

    home = claude_home()
    if not os.path.isdir(home):
        print("\nNo %s on this machine." % home)
        print("Run this where Claude Code actually runs -- not in a cloud session.")
        return 1

    path = os.path.join(home, "settings.json")
    settings, error = read_settings(path)
    if error:
        print("\n%s\n    %s" % (error, path))
        return 1

    existing = current_dirs(settings)
    keep, added, dropped = plan(existing, found, args.prune)

    print("\nAlready registered: %d" % len(existing))
    for repo in added:
        print("    + %s" % repo)
    for repo in dropped:
        print("    - %s (gone from disk)" % repo)
    if not added and not dropped:
        print("    (no change -- every directory found is already registered)")

    if args.check:
        print("\n--check: nothing was written. Re-run without --check to apply.")
        return 0

    if added or dropped:
        error = write_settings(path, settings, keep)
        if error:
            print("\n%s\n    %s" % (error, path))
            return 1
        print("\nWrote %s (previous version saved as settings.json.bak)." % path)
    else:
        print("\nNothing to write.")

    print("This applies to EVERY project, not just pwb-toolbox.")
    print("Start a NEW session for it to take effect -- settings load at startup.")
    print("")
    print("It grants file access only. A session still does not load another")
    print("directory's CLAUDE.md, skills or hooks from this key -- for that, move")
    print("the session with /cd <path> or launch with --add-dir <path>.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

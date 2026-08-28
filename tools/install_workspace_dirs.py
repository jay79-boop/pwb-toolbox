"""Let every Claude Code session reach every repo on this machine, not just one.

A session can read and edit files in the directory it was launched from. Anywhere
else it prompts, every time -- which is why a chat started in pwb-toolbox nags
before it will look at the trade journal or another checkout.

Claude Code reads `permissions.additionalDirectories` from user-level settings
(`~/.claude/settings.json`) and applies it to EVERY project, so that is where a
machine-wide answer belongs.

    python tools/install_workspace_dirs.py --diagnose  # why can a session not see X?
    python tools/install_workspace_dirs.py --check     # report the diff, write nothing
    python tools/install_workspace_dirs.py             # install

By default it registers the HOME DIRECTORY itself, so every repo -- including
ones created next month -- is covered with nothing to re-run. That breadth is
only safe because it is paired with deny rules, which outrank every allow:
`~/.claude/projects` (session transcripts carrying SSNs, claim numbers and
financial detail), `.credentials.json`, `~/.claude.json`, `.ssh`, `.aws` and
`AppData` are blocked by name. Deny blocks the Read and Edit tools only -- a
program under `AppData`, such as python.exe, still RUNS.

`--repos-only` takes the narrow path instead: scan for git repositories and
register exactly those. Precise, but it goes stale the day you add a repo.

WHAT THIS CANNOT FIX. In a cloud session (claude.ai/code, or a cloud session in
the desktop app) the other repository was never cloned -- it is not on disk at
all, so no settings file reaches it. Ask the session to add the repo by name
instead; see docs/working-directories.md.

Registering a directory grants FILE ACCESS, not configuration: Claude Code does
not load another directory's `CLAUDE.md`, skills or hooks from this key. For
that, move the session with `/cd <path>` or launch with `--add-dir <path>`.

A cloud session cannot run this: `~/.claude` there belongs to a container that is
reclaimed when the session ends. It has to run where the sessions actually run.

Deliberately ASCII-only and stdlib-only. Windows PowerShell 5.1 reads a BOM-less
file as Windows-1252, and one non-ASCII byte can stop a script parsing at all.
"""

import argparse
import json
import os
import shutil
import sys

SETTINGS_KEY = "additionalDirectories"
CLAUDE_DIR = ".claude"

DENY_KEY = "deny"

# Paths that must never be readable, whatever else is granted. Deny outranks
# every allow, and `~/` anchors resolve against the home directory in user
# settings. These govern the Read and Edit tools -- they do not stop a program
# under one of these paths from being executed.
BLOCKLIST = (
    "Read(~/.claude/projects/**)",
    "Edit(~/.claude/projects/**)",
    "Read(~/.claude/.credentials.json)",
    "Read(~/.claude.json)",
    "Read(~/OneDrive/.claude/projects/**)",
    "Edit(~/OneDrive/.claude/projects/**)",
    "Read(~/OneDrive/Backups/claude-config/**)",
    "Read(~/.ssh/**)",
    "Edit(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Edit(~/.aws/**)",
    "Read(~/AppData/**)",
    "Edit(~/AppData/**)",
)

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


def current_deny(settings):
    perms = settings.get("permissions")
    if not isinstance(perms, dict):
        return []
    listed = perms.get(DENY_KEY)
    if not isinstance(listed, list):
        return []
    return [item for item in listed if isinstance(item, str)]


def plan_deny(existing, rules):
    """Add any missing blocklist rule, keeping every rule already there."""

    have = list(existing)
    seen = set(have)
    added = [rule for rule in rules if rule not in seen]
    return have + added, added


def write_settings(path, settings, keep, deny=None):
    """Merge both keys in place, leaving every other setting exactly as it was."""

    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict):
        return "permissions is not a JSON object -- not touched"
    perms[SETTINGS_KEY] = keep
    if deny is not None:
        perms[DENY_KEY] = deny
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return None


def diagnose(home, roots):
    """Answer 'why can a session not see that folder?' without changing anything.

    The two causes look identical from a chat and have completely different
    fixes, so this names which one is in play rather than guessing.
    """

    print("Machine")
    print("  platform        %s" % sys.platform)
    print("  home            %s" % os.path.expanduser("~"))
    print("  working dir     %s" % os.getcwd())

    if not os.path.isdir(home):
        print("\n  No %s here." % home)
        print("  That means this is a CLOUD session: its home directory is")
        print("  thrown away with the container. Settings cannot help a cloud")
        print("  session reach another repo -- the repo was never cloned into")
        print("  it. Ask the session to add the repository by name instead.")
        return 1

    path = os.path.join(home, "settings.json")
    settings, error = read_settings(path)
    print("\nUser settings  %s" % path)
    if error:
        print("  %s" % error)
        print("  Every session on this machine reads this file. Fix the JSON")
        print("  before anything else -- nothing below is being applied.")
        return 1
    if not os.path.isfile(path):
        print("  (does not exist yet -- nothing is registered)")

    dirs = current_dirs(settings)
    print("\nDirectories every session may reach without prompting: %d" % len(dirs))
    for item in dirs:
        state = "ok " if os.path.isdir(os.path.expanduser(item)) else "GONE"
        print("  %s %s" % (state, item))
    if not dirs:
        print("  (none -- this is why it prompts for every folder outside the")
        print("   one a session was started in)")

    rules = current_deny(settings)
    missing = [rule for rule in BLOCKLIST if rule not in set(rules)]
    print(
        "\nDeny rules: %d (%d of the %d blocklist rules present)"
        % (len(rules), len(BLOCKLIST) - len(missing), len(BLOCKLIST))
    )
    for rule in rules:
        print("  %s" % rule)

    covered = {key_of(item) for item in dirs}
    print("\nWould a session reach these without prompting?")
    for root in roots:
        for repo in find_repos([root]):
            reachable = any(key_of(repo).startswith(c) for c in covered)
            print("  %-4s %s" % ("yes" if reachable else "NO", repo))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="explain why a session can or cannot see a folder; write nothing",
    )
    parser.add_argument(
        "--check", action="store_true", help="report what would change, write nothing"
    )
    parser.add_argument(
        "--repos-only",
        action="store_true",
        help="register each git repo found instead of the home directory",
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
        help="levels below each root to search with --repos-only (default %d)"
        % DEFAULT_DEPTH,
    )
    parser.add_argument(
        "--include-claude",
        action="store_true",
        help="do not skip .claude directories when scanning",
    )
    parser.add_argument(
        "--no-blocklist",
        action="store_true",
        help="do not add the deny rules (leaves the broad grant unguarded)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="drop registered directories that no longer exist",
    )
    args = parser.parse_args(argv)

    home = claude_home()
    roots = args.root or [os.path.expanduser("~")]

    if args.diagnose:
        return diagnose(home, roots)

    if args.repos_only or args.scan:
        found = find_repos(roots, args.depth, args.include_claude)
        print("Searched %s, %d level(s) deep." % (", ".join(roots), args.depth))
        print(
            "Found %d git repositor%s:"
            % (len(found), "y" if len(found) == 1 else "ies")
        )
        for repo in found:
            print("    %s" % repo)
        if not found:
            print("    (none -- try --depth 4, or --root PATH)")
    else:
        found = [os.path.abspath(os.path.expanduser("~"))]
        print("Registering your home directory, so every repo -- including ones")
        print("you create later -- is covered with nothing to re-run:")
        print("    %s" % found[0])

    for extra in args.add or []:
        resolved = os.path.abspath(os.path.expanduser(extra))
        if key_of(resolved) not in {key_of(item) for item in found}:
            found.append(resolved)
            print("    %s" % resolved)

    if args.scan:
        print("\n--scan: settings were neither read nor written.")
        return 0

    if not os.path.isdir(home):
        print("\nNo %s on this machine." % home)
        print("Run this where Claude Code actually runs -- not in a cloud session.")
        return 1

    path = os.path.join(home, "settings.json")
    settings, error = read_settings(path)
    if error:
        print("\n%s\n    %s" % (error, path))
        return 1

    keep, added, dropped = plan(current_dirs(settings), found, args.prune)
    deny = None
    deny_added = []
    if not args.no_blocklist:
        deny, deny_added = plan_deny(current_deny(settings), BLOCKLIST)

    print("\nDirectories")
    for repo in added:
        print("    + %s" % repo)
    for repo in dropped:
        print("    - %s (gone from disk)" % repo)
    if not added and not dropped:
        print("    (no change)")

    if deny is not None:
        print("\nDeny rules -- these outrank every allow, and are what make the")
        print("grant above safe to have:")
        for rule in deny_added:
            print("    + %s" % rule)
        if not deny_added:
            print("    (all %d already present)" % len(BLOCKLIST))

    if args.check:
        print("\n--check: nothing was written. Re-run without --check to apply.")
        return 0

    if added or dropped or deny_added:
        error = write_settings(path, settings, keep, deny)
        if error:
            print("\n%s\n    %s" % (error, path))
            return 1
        print("\nWrote %s (previous version saved as settings.json.bak)." % path)
    else:
        print("\nNothing to write.")

    print("This applies to EVERY project, not just pwb-toolbox.")
    print("Start a NEW session for it to take effect -- settings load at startup.")
    print("")
    print("It cannot help a CLOUD session: there the other repo was never cloned,")
    print("so there is no file for a permission to apply to. Ask that session to")
    print("add the repository by name instead.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

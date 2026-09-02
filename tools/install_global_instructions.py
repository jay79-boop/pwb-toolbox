"""Install the owner's global working rules into `~/.claude/CLAUDE.md`.

Claude Code reads the user-level `~/.claude/CLAUDE.md` in every session of
every project, which makes it the right home for rules about the owner and how
work reaches them -- rules that are not about any one repository. The source of
those rules is `docs/global-instructions.md` in this repository, so a session on
any surface can read and revise it, and git carries the history.

This writes that source into the user-level file between two marker lines. Only
the region between the markers is replaced; everything outside it survives
untouched, including the Action Ledger rule that `install_spend_hook.py` appends
and any lines written by hand. Running it twice changes nothing the second time.

Run it once, on the machine whose sessions should read the rules:

    python tools/install_global_instructions.py          # install or refresh
    python tools/install_global_instructions.py --check  # report, change nothing
    python tools/install_global_instructions.py --diff   # show what would change

A cloud session cannot do this: `~/.claude` there belongs to a container that is
reclaimed when the session ends. It has to run where the sessions actually run.

Deliberately ASCII-only and stdlib-only. Windows PowerShell 5.1 reads a BOM-less
file as Windows-1252, and one non-ASCII byte can stop a script parsing at all.
"""

import argparse
import difflib
import os
import re
import shutil

START = "<!-- pwb-toolbox:global-instructions:start -->"
END = "<!-- pwb-toolbox:global-instructions:end -->"

SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docs", "global-instructions.md"
)

# The source opens with an HTML comment explaining itself to a reader of the
# repository. That explanation is about the file, not a rule for the model, so
# it stays out of the installed copy.
LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.DOTALL)


def claude_home():
    return os.path.join(os.path.expanduser("~"), ".claude")


def load_source(path=SOURCE):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return LEADING_COMMENT.sub("", text, count=1).strip("\n") + "\n"


def managed_block(body):
    return START + "\n" + body + END + "\n"


def splice(existing, body):
    """Return the file text with the managed region set to `body`.

    Replaces the region if both markers are present, in order. Appends a new
    region otherwise -- a lone or reversed marker is treated as ordinary text
    rather than guessed at, so nothing outside a well-formed region is ever
    removed.
    """

    block = managed_block(body)
    start = existing.find(START)
    end = existing.find(END, start + len(START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        return existing[:start] + block + existing[end + len(END) :].lstrip("\n")
    if not existing:
        return block
    separator = (
        ""
        if existing.endswith("\n\n")
        else ("\n" if existing.endswith("\n") else "\n\n")
    )
    return existing + separator + block


def current_block(existing):
    start = existing.find(START)
    end = existing.find(END, start + len(START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        return existing[start : end + len(END)] + "\n"
    return None


def install(home, body, check=False, show_diff=False):
    path = os.path.join(home, "CLAUDE.md")
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()

    wanted = managed_block(body)
    present = current_block(existing)
    if present == wanted:
        return "current", path
    state = "missing" if present is None else "stale"

    if show_diff:
        before = (present or "").splitlines(keepends=True)
        after = wanted.splitlines(keepends=True)
        print("".join(difflib.unified_diff(before, after, "installed", "source")))
    if check or show_diff:
        return state, path

    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(splice(existing, body))
    return "added" if state == "missing" else "refreshed", path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report what would change, write nothing"
    )
    parser.add_argument(
        "--diff", action="store_true", help="show the change as a diff, write nothing"
    )
    args = parser.parse_args(argv)

    home = claude_home()
    if not os.path.isdir(home):
        print("No %s on this machine." % home)
        print("Run this where Claude Code actually runs -- not in a cloud session.")
        return 1

    print("Claude Code home: %s" % home)
    if args.check or args.diff:
        print("(reporting only, nothing will be written)\n")
    else:
        print("")

    state, path = install(home, load_source(), check=args.check, show_diff=args.diff)
    print("  CLAUDE.md       %-18s %s" % (state, path))

    if args.check or args.diff:
        if state != "current":
            print("\nNothing was changed. Re-run without --check/--diff to install.")
        return 0

    if state != "current":
        print("\nDone.")
        if os.path.isfile(path + ".bak"):
            print("The previous file was copied to CLAUDE.md.bak first.")
    print("This applies to EVERY project, not just pwb-toolbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Install the session-size warning for EVERY Claude Code session, not just this repo.

The warning built on 2026-08-24 lives in this repository's `.claude/settings.json`,
so it fires only in sessions working on pwb-toolbox. The owner asked for it
everywhere. Claude Code reads user-level settings from `~/.claude/settings.json`
and applies them to every project, so that is where a global hook belongs -- but
a hook there cannot call `tools/spend_watch.py`, which exists in one checkout.

So this writes a self-contained copy of the check (stdlib only, no imports from
this repo) into `~/.claude/hooks/`, registers it in user-level settings, and adds
the Action Ledger rule to `~/.claude/CLAUDE.md` so every session knows where open
items live.

Run it once, on the machine whose sessions should get the warning:

    python tools/install_spend_hook.py          # install
    python tools/install_spend_hook.py --check  # report, change nothing

A cloud session cannot do this: `~/.claude` there belongs to a container that is
reclaimed when the session ends. It has to run where the sessions actually run.

Deliberately ASCII-only and stdlib-only. Windows PowerShell 5.1 reads a BOM-less
file as Windows-1252, and one non-ASCII byte can stop a script parsing at all.
"""

import argparse
import json
import os
import shutil
import sys

HOOK_NAME = "session-size.py"

# Kept byte-identical in spirit to tools/spend_watch.py's `session` command and
# .claude/hooks/session-size.sh, but with no dependency on either -- a global
# hook must run in a session that has never seen this repository.
#
# The state directory and file naming match the repo hook's on purpose: in a
# pwb-toolbox session both hooks fire, the first to run records the tier, and
# the second sees it and stays silent. One warning, not two.
HOOK_SOURCE = r'''#!/usr/bin/env python3
"""UserPromptSubmit hook: warn when THIS session has grown expensive to carry.

Reads the per-turn usage the harness already writes to the session transcript,
so it costs no tokens and makes no API call. A spend warning that spends the
window to report on the window is not a warning worth having.

Speaks once per tier and then stays quiet until the tier changes. Silent below
the first tier. Every failure path exits 0 without output: this runs before
every prompt, and breaking the session it protects is the worse outcome.

Installed by tools/install_spend_hook.py from jay79-boop/pwb-toolbox.
"""

import json
import os
import sys
import tempfile

TIERS = (
    (50_000_000, "HIGH", "very large -- start a fresh session for the next task"),
    (25_000_000, "MEDIUM", "large -- finish the current thread, then start fresh"),
    (10_000_000, "LOW", "getting big -- worth splitting the next task out"),
)

STATE_DIR = os.path.join(tempfile.gettempdir(), "pwb-spend-watch")


def totals(path):
    reads = out = turns = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                usage = (record.get("message") or {}).get("usage")
                if not isinstance(usage, dict):
                    continue
                turns += 1
                for key, add in (
                    ("cache_read_input_tokens", "reads"),
                    ("output_tokens", "out"),
                ):
                    value = usage.get(key)
                    if isinstance(value, int):
                        if add == "reads":
                            reads += value
                        else:
                            out += value
    except OSError:
        return None
    return reads, out, turns


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    path = payload.get("transcript_path")
    session_id = payload.get("session_id") or "unknown"
    if not path or not os.path.isfile(path):
        return 0

    got = totals(path)
    if got is None:
        return 0
    reads, out, turns = got

    tier = advice = None
    for threshold, name, text in TIERS:
        if reads >= threshold:
            tier, advice = name, text
            break
    if tier is None:
        return 0

    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        state = os.path.join(STATE_DIR, str(session_id) + ".tier")
        if os.path.isfile(state):
            with open(state, "r", encoding="utf-8") as handle:
                if handle.read().strip() == tier:
                    return 0
        with open(state, "w", encoding="utf-8") as handle:
            handle.write(tier)
    except OSError:
        return 0

    ratio = ("%d:1" % (reads // out)) if out else "n/a"
    sys.stdout.write(
        "\nSession-size warning (from ~/.claude/hooks/%s, not from the owner):\n\n"
        "[%s] This session has re-read %.1fM tokens of context\n"
        "    Across %d turns it has produced %s tokens of output, a read-to-write\n"
        "    ratio of %s. Every further turn re-reads the whole conversation before\n"
        "    doing anything, and that price only goes up -- %s.\n\n"
        "Tell them this in ONE line at the end of your reply, in plain language --\n"
        "what the session has cost to carry and whether it is worth continuing here\n"
        "or starting a fresh one. Then answer what they actually asked. Do not\n"
        "restate it on later turns; this fires again only if it gets worse.\n"
        % (os.path.basename(__file__), tier, reads / 1e6, turns, format(out, ","), ratio, advice)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
'''

LEDGER_URL = "https://claude.ai/code/artifact/a9da0f16-1b7f-4658-a21f-70271be5c413"

LEDGER_RULE = """
## Action items: one ledger, every project

Anything I have to do myself goes in one `## \U0001f534 NEEDS YOU` block at the very
end of the reply, every item a markdown checkbox (`- [ ]`), never a bullet.

**Every one of those items also goes into the Action Ledger**, and that is the copy
that survives the session:

    %s

A checkbox in a terminal reply is text -- there is nothing to click and it scrolls
away. The ledger is a published artifact holding the `artifact` capability, so
ticking a box republishes the page: the state *is* the page, it follows me to
another device, and a tick costs no tokens because it never involves a session.

**Append to it; never start a second one.** Read it with the Artifact tool
(`action: "read"` on that URL), add items to the state JSON in
`<script id="app-state">`, and republish with `url` set to that same address. Items
already ticked stay ticked. An item raised weeks ago staying visibly open is the
point. Mark anything you completed yourself as `"who": "claude"`, `"done": true`.

**Tick every row you can confirm is done, including mine.** `who` records who was
responsible and keeps saying so after the box is checked, so ticking never destroys
that signal. Do not hand a finished row back to me to click. The bar is *verified*,
not *believed*: tick when something outside your own reasoning confirms it -- a test
run, an API read, a file checked. Otherwise leave it open and say what is unproven.
""" % LEDGER_URL

MARKER = "## Action items: one ledger, every project"


def claude_home():
    return os.path.join(os.path.expanduser("~"), ".claude")


def install_hook(home, check):
    hooks_dir = os.path.join(home, "hooks")
    target = os.path.join(hooks_dir, HOOK_NAME)
    if check:
        return "present" if os.path.isfile(target) else "missing", target
    os.makedirs(hooks_dir, exist_ok=True)
    if os.path.isfile(target):
        shutil.copyfile(target, target + ".bak")
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(HOOK_SOURCE)
    try:
        os.chmod(target, 0o755)
    except OSError:
        pass
    return "written", target


def register(home, hook_path, check):
    """Add the hook to user-level settings without disturbing anything else."""

    path = os.path.join(home, "settings.json")
    settings = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                settings = json.load(handle)
        except ValueError:
            return "settings.json is not valid JSON -- not touched", path
        if not isinstance(settings, dict):
            return "settings.json is not an object -- not touched", path

    command = '"%s" "%s"' % (sys.executable, hook_path)
    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault("UserPromptSubmit", [])

    for group in entries:
        for hook in (group or {}).get("hooks", []):
            if HOOK_NAME in str(hook.get("command", "")):
                return "already registered", path

    if check:
        return "not registered", path

    entries.append({"hooks": [{"type": "command", "command": command}]})
    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return "registered", path


def add_ledger_rule(home, check):
    path = os.path.join(home, "CLAUDE.md")
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    if MARKER in existing:
        return "already present", path
    if check:
        return "missing", path
    if os.path.isfile(path):
        shutil.copyfile(path, path + ".bak")
    separator = (
        ""
        if not existing or existing.endswith("\n\n")
        else ("\n" if existing.endswith("\n") else "\n\n")
    )
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(separator + LEDGER_RULE.lstrip("\n"))
    return "added", path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args(argv)

    home = claude_home()
    if not os.path.isdir(home):
        print("No %s on this machine." % home)
        print("Run this where Claude Code actually runs -- not in a cloud session.")
        return 1

    print("Claude Code home: %s" % home)
    if args.check:
        print("(--check: reporting only, nothing will be written)\n")
    else:
        print("")

    state, hook_path = install_hook(home, args.check)
    print("  hook            %-18s %s" % (state, hook_path))

    state, path = register(home, hook_path, args.check)
    print("  settings.json   %-18s %s" % (state, path))

    state, path = add_ledger_rule(home, args.check)
    print("  CLAUDE.md       %-18s %s" % (state, path))

    if args.check:
        print("\nNothing was changed. Re-run without --check to install.")
        return 0

    print("\nDone. Any file it replaced was copied to <name>.bak first.")
    print("This applies to EVERY project, not just pwb-toolbox.")
    print("Start a NEW session for it to take effect -- hooks load at session start.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

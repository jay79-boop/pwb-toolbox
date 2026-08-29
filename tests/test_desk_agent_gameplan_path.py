"""The premarket job's write path, pinned in the three places that must agree.

Four consecutive premarket runs ended `partial` with the analysis complete and
the gameplan never written. The agent diagnosed it correctly each time and
refused to fix it, because the fix is a permission change and widening its own
access is what its guardrail forbids. It logged the request under three
different phrasings, which is why the recurrence counter never noticed.

Two things were wrong and either alone is enough to lose the file:

**The `Edit` rule did not exist.** A headless `claude -p` run has no one to
answer a prompt, so a write outside the allow list is simply denied. The trap
underneath: Claude Code checks file access against `Edit(path)` and `Read(path)`
rules ONLY. A `Write(path)` rule is accepted, never consulted, and warns at
startup where nobody is reading -- so the obvious spelling of this fix is a
silent no-op.

**The directory did not exist.** `mkdir` is not among the agent's permitted
commands, so it could not create what it was asked to write into. Committing an
empty `out/` removes the need for that permission instead of granting it.

So three things have to keep agreeing: the path in `jobs/premarket.md`, the
`Edit` rule in `.claude/settings.json`, and a directory that is really there.
"""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS = ROOT / ".claude" / "settings.json"
JOB = ROOT / "tools" / "desk_agent" / "jobs" / "premarket.md"
OUT = ROOT / "tools" / "desk_agent" / "out"

GRANT = "Edit(/tools/desk_agent/out/**)"


def allow_rules():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]


def test_the_output_directory_is_tracked_not_assumed():
    """The agent cannot mkdir, so the directory has to arrive with the checkout."""

    assert OUT.is_dir()
    assert (OUT / ".gitkeep").is_file()


def test_the_agent_is_granted_writes_into_it():
    assert GRANT in allow_rules()


def test_the_grant_is_an_edit_rule_because_write_rules_are_never_consulted():
    """`Write(path)` is accepted and ignored -- the failure mode is silence."""

    for rule in allow_rules():
        assert not rule.startswith("Write("), (
            "%s is never consulted for file access; use Edit(...) instead" % rule
        )


def test_the_grant_anchors_at_the_repo_not_the_home_directory():
    """A leading single slash anchors at the settings source. For project
    settings that is the working directory, which is where the launcher puts
    the agent -- `run_job.ps1` does Push-Location $RepoRoot before invoking it.
    Dropping the slash would anchor at the current directory instead."""

    assert GRANT.startswith("Edit(/tools/")


def test_the_job_writes_where_the_grant_actually_reaches():
    """The path in the job file and the granted directory are edited by
    different people at different times; this is the check that they still
    describe the same place."""

    written = re.findall(
        r"`(tools/desk_agent/[^`]*gameplan[^`]*)`", JOB.read_text(encoding="utf-8")
    )
    assert written, "premarket.md no longer names a gameplan path"

    granted = GRANT[len("Edit(/") : -len("/**)")]
    for path in written:
        assert path.startswith(
            granted + "/"
        ), "premarket.md writes to %s, which %s does not cover" % (path, GRANT)

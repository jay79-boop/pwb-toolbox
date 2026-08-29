"""Checks on the two PowerShell files that decide what the desk agent runs.

Nothing in CI executes these -- CI is Linux and they are Windows-only -- so the
only thing standing between an edit and an unattended job that silently stops
working is a reader. Three things have gone wrong there and each is pinned
below.

**A job's name is written in three places.** The scheduler's table, the
launcher's `ValidateSet` and a file under `jobs/`. Disagree on any one and the
task registers happily and then fails every single time it fires.

**A job turned off in the source is not turned off on the machine.** Windows
keeps the task it was given. Removing an entry from the table -- the obvious way
to retire a job -- leaves it firing forever with nothing left to unregister it,
so `Enabled = $false` plus an explicit `Unregister-ScheduledTask` is the shape
that actually works, and it is worth pinning that the disabled branch really
does unregister rather than merely `continue`.

**One non-ASCII byte stops a BOM-less .ps1 from parsing at all.** Windows
PowerShell 5.1 decodes the file as Windows-1252 and the stray byte closes a
string; the script emits nothing whatsoever. From a scheduled task that is
`LastTaskResult = 1` with an empty log -- indistinguishable from a task that
never fired, which is the one distinction this whole system is built on. An em
dash pasted into a comment is enough to do it.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "tools" / "desk_agent" / "run_job.ps1"
SCHEDULER = REPO / "tools" / "register_desk_agent.ps1"
JOBS_DIR = REPO / "tools" / "desk_agent" / "jobs"


def read(path):
    return path.read_text(encoding="utf-8")


def scheduler_jobs():
    """The `$jobs` table as {job name: Enabled}, read out of the source."""
    body = read(SCHEDULER).split("$jobs = @(", 1)[1].split("\n)", 1)[0]
    jobs = {}
    for entry in re.finditer(
        r"Job\s*=\s*'(?P<job>\w+)'.*?Enabled\s*=\s*\$(?P<enabled>true|false)",
        body,
        re.DOTALL,
    ):
        jobs[entry.group("job")] = entry.group("enabled") == "true"
    return jobs


# ------------------------------------------------------- the encoding trap --


@pytest.mark.parametrize(
    "path",
    sorted(REPO.glob("tools/**/*.ps1")),
    ids=lambda p: p.name,
)
def test_powershell_files_are_ascii_only(path):
    raw = path.read_bytes()
    offenders = sorted({b for b in raw if b > 127})
    assert not offenders, (
        f"{path.relative_to(REPO)} carries non-ASCII bytes {offenders}. "
        "PowerShell 5.1 reads a BOM-less .ps1 as Windows-1252 and the decoded "
        "byte acts as a string delimiter, so the script runs no lines and "
        "prints nothing at all. Use ASCII: '--' for a dash, no smart quotes."
    )


# ------------------------------------------------------- the three registers --


def test_every_scheduled_job_is_accepted_by_the_launcher():
    accepted = set(
        re.search(r"ValidateSet\(([^)]*)\)", read(LAUNCHER))
        .group(1)
        .replace("'", "")
        .replace(" ", "")
        .split(",")
    )
    assert set(scheduler_jobs()) <= accepted


def test_every_scheduled_job_has_a_job_file():
    for job in scheduler_jobs():
        assert (JOBS_DIR / f"{job}.md").exists(), f"no jobs/{job}.md for {job}"


# --------------------------------------------------------- turning a job off --


def test_alerts_is_off_and_the_others_are_on():
    # Off since 2026-08-29: 25 consecutive runs, zero actions, no alerts
    # configured on the agent's TradingView login. Turned off rather than
    # deleted, so the entry has to still be here -- see jobs/alerts.md.
    assert scheduler_jobs() == {"premarket": True, "alerts": False, "journal": True}


def test_a_disabled_job_is_unregistered_rather_than_skipped():
    body = read(SCHEDULER).split("$weekdays", 1)[1]
    disabled = body.split("if (-not $j.Enabled) {", 1)[1].split("continue", 1)[0]
    assert "Unregister-ScheduledTask" in disabled, (
        "the disabled branch of the registration loop must unregister the "
        "task, not just skip it -- otherwise a job turned off here keeps "
        "firing on the machine from its previous registration."
    )


def test_remove_still_covers_every_job_including_the_disabled_ones():
    # -Remove iterates the whole table before the Enabled check exists, which
    # is the reason a retired job stays in it.
    removal = read(SCHEDULER).split("if ($Remove) {", 1)[1].split("exit 0", 1)[0]
    assert "foreach ($j in $jobs)" in removal
    assert "Enabled" not in removal


# ------------------------------------------------ reaching the trade journal --


def test_journal_gets_the_trade_journal_directory():
    dirs = read(LAUNCHER).split("$jobDirs = @{", 1)[1].split("}", 1)[0]
    assert re.search(r"journal\s*=", dirs)
    assert "trade-journal" in dirs


def test_no_other_job_gets_a_directory_outside_the_repo():
    # The journal is a personal document. An hourly unattended triage run has
    # no business being able to read it, so the widening is per job and this
    # pins that it stays that way.
    dirs = read(LAUNCHER).split("$jobDirs = @{", 1)[1].split("}", 1)[0]
    keys = re.findall(r"^\s*(\w+)\s*=", dirs, re.MULTILINE)
    assert keys == ["journal"]


def test_the_extra_directories_reach_the_invocation():
    src = read(LAUNCHER)
    assert "$claudeArgs = @('-p')" in src
    assert "$claudeArgs += @('--add-dir', $dir)" in src
    # The bare `-p` invocation is what five journal runs failed against.
    assert "& $claude @claudeArgs" in src
    assert "& $claude -p " not in src


def test_a_missing_directory_is_dropped_rather_than_passed_on():
    guard = read(LAUNCHER).split("$claudeArgs = @('-p')", 1)[1].split("# -- run", 1)[0]
    assert "Test-Path -LiteralPath $dir" in guard, (
        "a --add-dir pointing at a path that does not exist takes down a run "
        "that had nothing else wrong with it, and the failure names the flag "
        "rather than the job"
    )

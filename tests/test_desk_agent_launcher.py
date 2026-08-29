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


def read_back_disabled_branch():
    """The read-back loop's branch for a job that is turned off."""
    body = read(SCHEDULER).split("Reading the tasks back from Windows", 1)[1]
    return body.split("if (-not $j.Enabled) {", 1)[1].split("continue", 1)[0]


def test_a_disabled_job_is_verified_against_windows_not_just_announced():
    # Seen live 2026-08-29. The removal above printed "was not registered" for
    # a task the previous run had left Ready with 8 triggers, and the read-back
    # printed "not scheduled" straight out of this file having checked nothing
    # -- so the output could not say whether the task was gone or still firing.
    # This script's own header says the printed line is not evidence and the
    # read-back is; the disabled branch was the one place that forgot it.
    branch = read_back_disabled_branch()
    assert "Get-ScheduledTask" in branch, (
        "the read-back must ask Windows whether a disabled task is really "
        "gone, not repeat what the source file says about it"
    )
    assert "STILL ON" in branch and "$strays" in branch


def test_a_stray_registration_is_reported_after_the_tally():
    # A warning above the summary line scrolls off; this one has to be the
    # last thing printed, or a run that left the machine wrong reads as clean.
    tail = read(SCHEDULER).split("enabled tasks (", 1)[1]
    assert "$strays -gt 0" in tail
    assert "STILL REGISTERED" in tail


def test_every_scheduler_status_code_is_decoded_in_the_read_back():
    # LastTaskResult is the script's exit code only once a run has ENDED. Before
    # that Windows parks a SCHED_S_* status there instead: six-digit decimals
    # that are all SUCCESS HRESULTs (0x000413xx) and all read as failures to
    # anyone who has not looked them up. The read-back prints the number, so it
    # has to print what the number means -- 267011 was decoded here for a year
    # while its eight siblings were not, which is how 267009 came to be asked
    # about from scratch.
    body = read(SCHEDULER)
    for code, name in {
        "267008": "SCHED_S_TASK_READY",
        "267009": "SCHED_S_TASK_RUNNING",
        "267010": "SCHED_S_TASK_DISABLED",
        "267011": "SCHED_S_TASK_HAS_NOT_RUN",
        "267012": "SCHED_S_TASK_NO_MORE_RUNS",
        "267013": "SCHED_S_TASK_NOT_SCHEDULED",
        "267014": "SCHED_S_TASK_TERMINATED",
        "267015": "SCHED_S_TASK_NO_VALID_TRIGGERS",
        "267045": "SCHED_S_TASK_QUEUED",
    }.items():
        assert "'%s' = '%s = %s" % (code, code, name) in body, (
            "the read-back must decode LastTaskResult %s (%s); a bare number "
            "leaves the reader unable to tell a state from a failure" % (code, name)
        )


def test_a_running_task_is_named_as_blocking_its_own_schedule():
    # 267009 is the expensive one, and the only one whose meaning is not enough
    # on its own. MultipleInstances defaults to IgnoreNew, so while one run
    # hangs Windows skips every later occurrence rather than starting a second
    # copy: the schedule stops with no error, no missed-run count and a task
    # that still reads healthy. The line has to say that, not just "running".
    tail = read(SCHEDULER).split("$schedCodes.ContainsKey", 1)[1]
    running = tail.split("'267009'", 1)[1].split("$ok++", 1)[0]
    assert "skip" in running.lower() and "hung" in running.lower(), (
        "a task sitting at 267009 must be reported as hung and as suppressing "
        "its own later runs, or it reads as a job that is merely busy"
    )


def test_the_removal_branch_does_not_claim_the_task_was_absent():
    body = read(SCHEDULER).split("$weekdays", 1)[1]
    disabled = body.split("if (-not $j.Enabled) {", 1)[1].split("continue", 1)[0]
    # Unregister-ScheduledTask throws both when there is no such task and when
    # the removal fails. Those differ by whether an hourly job fires on Monday.
    # Only what is PRINTED matters here -- the comment above that code quotes
    # the old wording on purpose, to say what it was and why it was wrong.
    printed = "\n".join(ln for ln in disabled.splitlines() if "Write-Host" in ln)
    assert "was not registered" not in printed, (
        "the catch cannot tell 'no such task' from 'removal failed', so it "
        "must not assert either"
    )
    assert "no task removed" in printed


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


# --------------------------------------------- evidence when a run fails --
#
# The launcher captures the agent's merged stdout/stderr and then, on a
# non-zero exit, wrote a record saying only "agent run failed with exit code
# N" -- discarding the buffer it had just captured. The one account of what
# went wrong was a log file under LOCALAPPDATA that no cloud session can read,
# while runs.jsonl, the committed and reviewable half, carried a symptom and
# no diagnosis. Four consecutive run records asked for this and none could
# make it: the agent may not edit its own log, and that is the code writing it.


def failure_branch():
    return read(LAUNCHER).split("if ($code -ne 0) {", 1)[1].split("exit $code", 1)[0]


def test_the_failure_record_carries_what_the_agent_printed():
    branch = failure_branch()
    assert "Get-OutputTail $output" in branch, (
        "the non-zero-exit record must include the captured output; without "
        "it the run's only account is a log file no cloud session can read"
    )


def test_no_output_is_reported_as_its_own_fault():
    # A crash with a message and a process that produced nothing are different
    # faults. Collapsing them is how this system has gone wrong three times.
    assert "printed nothing at all" in failure_branch()


def test_the_record_names_the_log_file_without_the_home_directory():
    branch = failure_branch()
    assert "Split-Path $logFile -Leaf" in branch, (
        "name the log file, not its path: the name carries the timestamp that "
        "finds it, the path carries a home directory into a public repo"
    )
    assert "$logFile +" not in branch and "+ $logFile" not in branch


def test_the_output_tail_is_bounded_and_flattened():
    src = read(LAUNCHER)
    body = src.split("function Get-OutputTail", 1)[1].split("\nfunction ", 1)[0]
    # runs.jsonl is committed -- which is exactly why raw stdout under logs/ is
    # not. A whole transcript in a tracked file is noise, and a place for chart
    # detail to reach a public fork.
    assert "$max = 600" in body and "Substring" in body
    assert r"-replace '\s+', ' '" in body, "the record is one line of JSONL"
    # PowerShell 5.1 mangles a double quote passed to a native command, and a
    # mangled argument loses the whole record -- this fix causing the silence
    # it exists to remove.
    assert """.Replace('"', "'")""" in body


def test_a_missing_directory_is_dropped_rather_than_passed_on():
    guard = read(LAUNCHER).split("$claudeArgs = @('-p')", 1)[1].split("# -- run", 1)[0]
    assert "Test-Path -LiteralPath $dir" in guard, (
        "a --add-dir pointing at a path that does not exist takes down a run "
        "that had nothing else wrong with it, and the failure names the flag "
        "rather than the job"
    )


# ------------------------------------- waking up, and signing in without you --
#
# Asked for on 2026-08-29 as "fix the LogonType so it runs without me signed
# in". The LogonType is the one thing that must NOT change: a task set to run
# whether the user is logged on or not gets a session with no desktop, and both
# enabled jobs drive TradingView Desktop. The request was right about the goal
# and wrong about the mechanism, and these pin the distinction so a later reader
# does not undo it.

AUTOLOGON = REPO / "tools" / "autologon.ps1"


def code_lines(path):
    """Source with comment-only lines dropped.

    Every one of these files explains its own traps in prose, so the wording a
    test wants to forbid is usually quoted a few lines above by the comment
    saying why it is forbidden. Asserting against the raw text convicts the
    explanation instead of the code -- which happened once already, on #154.
    """
    return "\n".join(
        line for line in read(path).splitlines() if not line.strip().startswith("#")
    )


def test_the_tasks_wake_the_machine():
    settings = read(SCHEDULER).split("$settings = New-ScheduledTaskSettingsSet", 1)[1]
    settings = settings.split("$weekdays", 1)[0]
    assert "-WakeToRun" in settings, (
        "Without WakeToRun the machine is never woken for a trigger. "
        "StartWhenAvailable only catches a run up after something else wakes it, "
        "and a 07:00 gameplan delivered at 10:15 is not a pre-market gameplan."
    )


def test_wake_to_run_is_read_back_from_windows():
    read_back = read(SCHEDULER).split("Reading the tasks back from Windows", 1)[1]
    assert "$task.Settings.WakeToRun" in read_back, (
        "Setting the flag and printing that you set it are different claims. "
        "This whole script exists because Register-ScheduledTask can fail while "
        "the surrounding code still prints success."
    )


def test_the_tasks_are_never_given_a_desktopless_logon_type():
    code = code_lines(SCHEDULER)
    for bad in ("S4U", "Password", "-LogonType"):
        assert bad not in code, (
            f"{bad!r} appears in the scheduler's code. Both enabled jobs drive "
            "TradingView Desktop; a task that runs whether the user is logged on "
            "or not has no desktop, so it would fire on time and fail at the "
            "first chart call. Interactive is correct here -- the fix for an "
            "unattended machine is automatic sign-in, not this flag."
        )


def test_the_interactive_note_points_at_the_real_fix():
    interactive = read(SCHEDULER).split("if (\"$logon\" -eq 'Interactive')", 1)[1]
    interactive = interactive.split("}", 1)[0]
    assert "autologon.ps1" in interactive, (
        "The note a reader sees when a task is Interactive has to send them to "
        "the thing that actually fixes an unattended run. The previous wording, "
        "'runs only while you are signed in', reads as an accusation against the "
        "flag and invites exactly the change that breaks the chart jobs."
    )


# --------------------------------------------------------- tools/autologon.ps1 --


def test_autologon_checks_for_a_plaintext_password_in_the_registry():
    body = read(AUTOLOGON)
    assert "DefaultPassword" in body, (
        "Half the auto-logon guides on the internet tell you to put the Windows "
        "password in Winlogon\\DefaultPassword, where any local user can read it. "
        "If this script does not look for it, nothing does."
    )


def test_autologon_never_writes_a_password_itself():
    code = code_lines(AUTOLOGON)
    for bad in ("Set-ItemProperty", "New-ItemProperty", "LsaStorePrivateData"):
        assert bad not in code, (
            f"{bad!r} appears in autologon.ps1. Storing the password is delegated "
            "to Sysinternals Autologon on purpose: it writes an LSA secret, it is "
            "published by Microsoft, and it is tested. A hand-rolled version here "
            "would be untested P/Invoke against the credential store, written by "
            "someone with no Windows machine to run it on."
        )


def test_the_lock_task_is_opt_in_rather_than_installed_by_the_check():
    body = read(AUTOLOGON)
    assert "[switch] $EnableLock" in body
    # The report path must not register anything. Everything that writes has to
    # sit above the report and exit, so a plain run is provably read-only.
    report = body.split("# -- the report ---", 1)[1]
    for bad in ("Register-ScheduledTask", "Unregister-ScheduledTask"):
        assert bad not in report, (
            f"{bad!r} is reachable from the read-only report path. Running this "
            "with no arguments must be safe to do at any time."
        )


def test_autologon_removal_does_not_claim_the_task_was_absent():
    disable = read(AUTOLOGON).split("if ($DisableLock)", 1)[1].split("exit 0", 1)[0]
    printed = "\n".join(l for l in disable.splitlines() if "Write-Host" in l)
    assert "already absent, or the removal failed" in printed, (
        "Same trap as #154: Unregister-ScheduledTask throws both when the task "
        "is not there and when the removal fails."
    )
    assert (
        "Get-ScheduledTask" in disable
    ), "And the verdict comes from asking Windows, not from whether the call threw."


def test_autologon_warns_that_a_locked_session_may_break_chart_capture():
    body = read(AUTOLOGON).lower()
    assert "blank" in body and "lock" in body, (
        "Whether TradingView still renders for capture on a locked session was "
        "never established -- CDP draws from the compositor rather than the "
        "screen, so it should hold, but Chromium throttles occluded windows and "
        "nobody has proven it on this machine. The script has to say so, and name "
        "the symptom, or the first blank screenshot costs a day."
    )


# ------------------------------------------- saying which version just ran --
#
# A registration run used to print a summary and nothing else, so "did that use
# the version with my fix in it?" could only be answered by recognising the
# wording of the output -- which is how a run against a stale checkout went
# undiagnosed on 2026-08-29. The script now names the commit behind each moving
# part. These pin that it keeps doing so, and that it cannot grow into the
# staleness check CLAUDE.md warns against.


def test_the_run_names_the_commit_behind_every_moving_part():
    src = read(SCHEDULER)
    assert (
        "'rev-parse', '--short', 'HEAD'" in src
    ), "the checkout's commit is never read"
    assert re.search(
        r"Get-FileVersion \$\w+ 'tools/register_desk_agent\.ps1'", src
    ), "the running script's own version is never reported"
    assert re.search(
        r"Get-FileVersion \$\w+ 'tools/desk_agent/run_job\.ps1'", src
    ), "the launcher's version is never reported -- the copy is what executes"


def test_the_summary_line_carries_the_version():
    # It is the line that gets read at a glance and pasted back, so a version
    # reported only in a header that has scrolled away is not reported at all.
    lines = [
        line
        for line in read(SCHEDULER).splitlines()
        if "Write-Host" in line and "Registered " in line
    ]
    assert len(lines) == 1, lines
    assert "$version" in lines[0], lines[0]


def test_an_uncommitted_edit_is_not_reported_as_a_clean_commit():
    body = read(SCHEDULER).split("function Get-FileVersion {", 1)[1].split("\n}", 1)[0]
    assert "'status', '--porcelain'" in body, (
        "a commit id is a lie about a file with uncommitted edits on top of it, "
        "and that is exactly what a half-finished session leaves behind"
    )


def test_a_machine_without_git_still_registers_the_tasks():
    # Registration is the job; reporting the version is commentary on it. A git
    # call that throws under `$ErrorActionPreference = 'Stop'` would turn a
    # working registration into no registration at all.
    body = read(SCHEDULER).split("function Get-Git {", 1)[1].split("\n}", 1)[0]
    assert "try {" in body and "} catch {" in body
    assert "2>$null" in body, "native stderr under Stop is its own way to die"
    assert body.count("return ''") == 2, "both failure paths must degrade quietly"


def test_staleness_is_not_reported_as_an_ahead_behind_count():
    """CLAUDE.md: an ahead/behind count compares two refs, not two trees."""
    src = read(SCHEDULER)
    assert "rev-list" not in src and "--count" not in src, (
        "an ahead/behind count reads 'up to date' whenever the checkout sits on "
        "a branch that already contains the ref it is compared with -- which is "
        "the exact confusion this version stamp exists to end"
    )
    assert "fetch" not in src, "registering scheduled tasks must not touch the network"


# ------------------------------------------- a PIN is not the account password --
#
# Corrected on 2026-08-29, one question after shipping. The first version of
# autologon.ps1 knew only about AutoAdminLogon and told anyone without it to run
# Sysinternals Autologon. The owner signs in with a Windows Hello PIN -- a
# device-local credential sealed in the TPM, not the account password -- so that
# advice could not have worked, and would have sent them hunting for a Microsoft
# account password they had never typed.
#
# ARSO is the route that fits: it signs the last user back in after a restart or
# cold boot AND locks the session, with no password stored anywhere. These pin
# that the script offers it, and that it does not overstate what it checked.


def test_autologon_offers_the_route_that_works_with_a_pin():
    body = read(AUTOLOGON)
    assert "DisableAutomaticRestartSignOn" in body, (
        "ARSO is the only automatic sign-in a PIN user can actually enable, and "
        "its policy is the one part of it that is readable from a script. A "
        "checker that cannot see the policy will report a machine as unfixable "
        "when one registry value is switching the whole feature off."
    )
    assert "Policies\\System" in body, (
        "ARSO's policy lives under Policies\\System, not under Winlogon where "
        "every other sign-in value in this script lives. Reading the wrong key "
        "reports 'not set' for a policy that is switched on."
    )


def test_autologon_does_not_send_a_pin_user_to_fetch_a_password():
    """The password route must be offered second, and marked as needing a password."""
    body = read(AUTOLOGON)
    route_a = body.split("Route A", 1)[1].split("Route B", 1)[0]
    route_b = body.split("Route B", 1)[1]
    assert (
        "PIN" in route_a
    ), "the PIN-compatible route has to say so where it is offered"
    assert "PASSWORD, not a PIN" in route_b, (
        "the autologon route has to state up front that it needs the account "
        "password. Presenting it as the default is what made the first version "
        "wrong for this machine."
    )


def test_the_summary_does_not_round_unchecked_up_to_fine():
    summary = (
        read(AUTOLOGON).split("if ($problems -eq 0) {", 1)[1].split("} else", 1)[0]
    )
    assert "All clear" not in summary, (
        "The per-user ARSO toggle cannot be read from a script, so a run with "
        "zero problems has still not established that the machine signs itself "
        "in. Reporting that as 'all clear' is the same class of error as the "
        "read-back that printed a line from the source file having queried "
        "nothing -- an unchecked thing rounded up to a passing one."
    )
    assert "not verified" in summary, "it has to name what it could not check"

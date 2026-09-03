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


def test_the_two_failure_results_are_decoded_too():
    # Not every LastTaskResult is a SCHED_S_* code, and the ones that are not
    # look nothing like them -- ten digits rather than six. Both of these were
    # met on the owner's machine rather than read out of a reference: 2147946720
    # is what \\ClaudeRemoteControl reports on every firing of its keep-alive
    # trigger, and 3221225786 is the Ctrl+C death that left Remote Control down
    # for seventeen hours while RestartCount made it look self-healing.
    body = read(SCHEDULER)
    for code, hexes in (("2147946720", "0x800710E0"), ("3221225786", "0xC000013A")):
        assert "'%s' = '%s = %s" % (code, code, hexes) in body, (
            "the read-back must decode LastTaskResult %s (%s); it is a failure "
            "code, not a state, and nothing else in the output says so" % (code, hexes)
        )


def test_a_refused_firing_names_the_hung_predecessor():
    # 2147946720 is 267009 seen from the next occurrence: Windows refused to
    # start this firing because the previous run had not ended. For the batch
    # jobs this script registers that is the hung-schedule fault, so the line
    # has to say so -- and has to say that the same code is healthy on a
    # long-lived task, or the next keep-alive task gets diagnosed as broken.
    tail = read(SCHEDULER).split("$resultCodes.ContainsKey", 1)[1]
    refused = tail.split("'2147946720'", 1)[1].split("$ok++", 1)[0]
    assert "PREVIOUS run" in refused and "never started" in refused
    assert "keep-alive" in refused, (
        "the same code is normal on a long-lived task; saying only 'hung' here "
        "would convict a healthy keep-alive trigger"
    )


def test_a_running_task_is_named_as_blocking_its_own_schedule():
    # 267009 is the expensive one, and the only one whose meaning is not enough
    # on its own. MultipleInstances defaults to IgnoreNew, so while one run
    # hangs Windows skips every later occurrence rather than starting a second
    # copy: the schedule stops with no error, no missed-run count and a task
    # that still reads healthy. The line has to say that, not just "running".
    tail = read(SCHEDULER).split("$resultCodes.ContainsKey", 1)[1]
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
# ------------------------------------------ which jobs still need a desktop --
#
# Asked for on 2026-08-29 as "fix the LogonType so it runs without me signed
# in", and answered twice.
#
# The first answer was that the LogonType must NOT change: a task set to run
# whether the user is logged on or not gets a session with no desktop, and
# every enabled job drove TradingView Desktop, so the change would have turned
# a job that does not run into a job that runs and is wrong. That reasoning is
# in docs/decisions/2026-08-29-the-logon-type-is-not-the-bug.md and the guard
# it produced forbade `-LogonType`, `S4U` and `Password` anywhere in the
# scheduler's code.
#
# The second answer removed the premise. `premarket` and `journal` now read
# their levels from bar data through tools/desk_levels.py and render their own
# images headless, so they have no desktop to lose and their tasks can take a
# logon type that has none.
#
# So the blanket ban is NARROWED here rather than deleted, to the jobs that
# still drive the chart. Deleting it would leave the original trap unguarded
# the moment somebody re-enables `alerts`; keeping it would forbid the fix.
#
# Which of these convict, honestly:
#
#   * `test_s4u_is_never_used_for_any_job` is UNCHANGED in force. S4U is still
#     banned outright, for every job, and it convicts today.
#   * The per-job tests below are new and convict the obvious careless version
#     of this change -- flipping the whole table to a stored credential -- which
#     was written and run against them to check they fail.
#   * `test_every_job_declares_whether_it_needs_a_desktop` is a forward guard.
#     It passes trivially now and exists so a job added later cannot inherit
#     "no desktop needed" by saying nothing.

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


def scheduler_desktop_jobs():
    """The `$jobs` table as {job name: NeedsDesktop}, read out of the source."""
    body = read(SCHEDULER).split("$jobs = @(", 1)[1].split("\n)", 1)[0]
    jobs = {}
    for entry in re.finditer(
        r"Job\s*=\s*'(?P<job>\w+)'.*?NeedsDesktop\s*=\s*\$(?P<needs>true|false)",
        body,
        re.DOTALL,
    ):
        jobs[entry.group("job")] = entry.group("needs") == "true"
    return jobs


def test_the_tasks_wake_the_machine():
    settings = read(SCHEDULER).split("$settings = New-ScheduledTaskSettingsSet", 1)[1]
    settings = settings.split("$weekdays", 1)[0]
    assert "-WakeToRun" in settings, (
        "Without WakeToRun the machine is never woken for a trigger. "
        "StartWhenAvailable only catches a run up after something else wakes it, "
        "and a 07:00 gameplan delivered at 10:15 is not a pre-market gameplan. "
        "This holds for a desktop-free task too: a stored credential does not "
        "wake a sleeping machine."
    )


def test_wake_to_run_is_read_back_from_windows():
    read_back = read(SCHEDULER).split("Reading the tasks back from Windows", 1)[1]
    assert "$task.Settings.WakeToRun" in read_back, (
        "Setting the flag and printing that you set it are different claims. "
        "This whole script exists because Register-ScheduledTask can fail while "
        "the surrounding code still prints success."
    )


def test_s4u_is_never_used_for_any_job():
    """The half of the original ban that did NOT narrow.

    S4U is refused for every job, desktop or not, and for a reason that has
    nothing to do with rendering: it stores no password and therefore carries
    no credentials, so DPAPI-protected secrets do not decrypt and network paths
    are not reachable as the user. Both remaining jobs run Claude Code, whose
    own stored authentication is exactly that kind of secret, and the journal
    job reads a document under OneDrive. S4U would fail both at run time,
    unattended, looking like a broken agent rather than a wrong logon type.
    """
    # Scoped to the code that REGISTERS a task. The read-backs in both
    # register_desk_agent.ps1 and autologon.ps1 name S4U on purpose, to
    # recognise a task that already has it and say so -- forbidding the string
    # outright would convict the detector along with the thing it detects,
    # which is the #154 mistake in a new place.
    registration = (
        read(SCHEDULER)
        .split("$weekdays", 1)[1]
        .split("Reading the tasks back from Windows", 1)[0]
    )
    for label, body in (
        ("register_desk_agent.ps1's registration loop", registration),
        ("run_job.ps1", code_lines(LAUNCHER)),
    ):
        assert "S4U" not in body, (
            f"{label} names S4U. It carries no credentials at all -- the jobs "
            "would fail on DPAPI and on OneDrive, at run time, unattended. A "
            "stored credential (-User with -Password) is the supported way to "
            "run without a desktop here."
        )


def test_the_read_back_still_recognises_s4u_if_it_finds_one():
    """Banned from being set is not the same as banned from being noticed.

    A task given S4U by hand in Task Scheduler has to be reported, and both
    read-backs have to be able to name it to do that.
    """
    for path in (SCHEDULER, AUTOLOGON):
        read_back = read(path).split("$logon", 1)[1]
        assert "S4U" in read_back, f"{path.name} cannot recognise an S4U task"


def test_every_job_declares_whether_it_needs_a_desktop():
    """A forward guard: passes today, and exists so silence cannot mean 'no'.

    A job added without this field would be registered against a stored
    credential by default and fail at its first chart call -- the exact failure
    the original blanket ban was written to prevent, arriving through an
    omission rather than an edit.
    """
    declared = set(scheduler_desktop_jobs())
    assert declared == set(scheduler_jobs()), (
        "every job in the table must say whether it needs a desktop; "
        f"missing: {sorted(set(scheduler_jobs()) - declared)}"
    )


def test_premarket_and_journal_no_longer_need_a_desktop():
    # The conversion itself. Both read bar data through tools/desk_levels.py.
    assert scheduler_desktop_jobs()["premarket"] is False
    assert scheduler_desktop_jobs()["journal"] is False


def test_alerts_still_needs_a_desktop():
    """It is off, not retired, and it still drives the chart.

    This is why the ban narrowed rather than being deleted: re-enabling
    `alerts` must not quietly hand it a session with nothing to render into.
    """
    assert scheduler_desktop_jobs()["alerts"] is True


def test_a_job_that_needs_a_desktop_is_never_given_a_stored_credential():
    """The narrowed guard, and the one that replaces the blanket ban.

    Convicted against the careless version of this change: setting every entry
    in the table to `NeedsDesktop = $false` fails
    `test_alerts_still_needs_a_desktop`, and deleting the `$j.NeedsDesktop`
    condition from the registration branch fails this one.
    """
    body = read(SCHEDULER).split("$weekdays", 1)[1]
    branch = body.split("$common = @{", 1)[1].split("try {", 1)[0]
    assert "$j.NeedsDesktop" in branch, (
        "the registration branch must gate the stored credential on the job's "
        "own NeedsDesktop, or a chart job gets a session with no desktop"
    )
    # And the credential must be set only on the side of the branch that is
    # NOT the desktop one -- a gate that adds it in both arms is not a gate.
    assert re.search(
        r"if \(\$j\.NeedsDesktop[^)]*\)\s*\{[^}]*\}\s*else\s*\{[^}]*User[^}]*Password",
        branch,
        re.DOTALL,
    ), "the User/Password pair must sit in the else arm, not in both"


def test_the_password_is_prompted_for_rather_than_taken_on_the_command_line():
    src = read(SCHEDULER)
    assert "Read-Host" in src and "-AsSecureString" in src, (
        "a password passed as a parameter lands in the PowerShell history file "
        "in clear text, and a hand-edited command is how one ends up pasted "
        "into a chat window"
    )
    assert "[string] $Password" not in src


def test_declining_the_password_falls_back_to_interactive_rather_than_failing():
    """A blank answer must leave a working desk, not a half-registered one."""
    src = read(SCHEDULER)
    assert "$NoStoredCredential" in src
    assert "-or -not $taskPassword" in src, (
        "with no password supplied every task must register Interactive, which "
        "is exactly what it did before this change"
    )


def test_the_three_files_agree_on_which_jobs_need_a_desktop():
    """The same trap as the job names: one fact, written in three places.

    The launcher carries `pine_loop` as well, which is real -- it runs on
    demand and is not a scheduled task at all -- so the launcher's list is
    compared only across the jobs the scheduler knows about.
    """
    scheduled = scheduler_desktop_jobs()
    wanted = {job for job, needs in scheduled.items() if needs}

    launcher = set(
        re.search(r"\$desktopJobs = @\(([^)]*)\)", read(LAUNCHER))
        .group(1)
        .replace("'", "")
        .replace(" ", "")
        .split(",")
    )
    assert launcher & set(scheduled) == wanted, (
        "run_job.ps1's $desktopJobs disagrees with the scheduler's "
        f"NeedsDesktop: launcher says {sorted(launcher & set(scheduled))}, "
        f"table says {sorted(wanted)}"
    )

    # autologon.ps1 lists TASK-name suffixes, so map through the table's Name.
    body = read(SCHEDULER).split("$jobs = @(", 1)[1].split("\n)", 1)[0]
    names = dict(re.findall(r"Name\s*=\s*'(\w+)';\s*Job\s*=\s*'(\w+)'", body))
    auto = set(
        re.search(r"\$desktopTasks = @\(([^)]*)\)", read(AUTOLOGON))
        .group(1)
        .replace("'", "")
        .replace(" ", "")
        .split(",")
    )
    assert {names[n] for n in auto if n in names} == wanted, (
        f"autologon.ps1's $desktopTasks {sorted(auto)} does not map to the "
        f"scheduler's NeedsDesktop jobs {sorted(wanted)}"
    )


def test_the_read_back_judges_the_logon_type_against_the_job():
    """The read-back must not call a successful conversion a fault.

    The previous version printed one note for anything that was not
    Interactive. After this change that would flag every converted task, which
    is how a report stops being read.
    """
    read_back = read(SCHEDULER).split("Reading the tasks back from Windows", 1)[1]
    judgement = read_back.split("$logon = $task.Principal.LogonType", 1)[1]
    assert (
        "$j.NeedsDesktop" in judgement
    ), "the read-back judges the logon type without asking what the job needs"
    assert (
        "WRONG" in judgement
    ), "a chart job on a desktopless logon must be called wrong"
    assert (
        "GOOD" in judgement
    ), "a converted job on a desktopless logon must read as correct"


# ------------------------------------- the launcher stops driving TradingView --


def test_the_tradingview_dance_is_conditional_rather_than_deleted():
    """`alerts` is off and `pine_loop` runs on demand; both still want it.

    Deleting the launch/close block would leave the debug port open behind a
    pine_loop run, which is the risk docs/tradingview-agent-security.md exists
    for -- so it is gated, not removed.
    """
    src = read(LAUNCHER)
    assert (
        "$desktopJobs" in src and "$needsDesktop = $desktopJobs -contains $Job" in src
    )
    assert "Stop-Process" in src, "the close-behind-us path must still exist"
    assert "tv_launch" in src, "the launch permission must still exist for chart jobs"


def test_a_desktop_free_job_is_told_not_to_launch_tradingview():
    """The instruction is given in the negative, and it has to be.

    Such a task may be running with no desktop at all. An agent that reaches
    for tv_launch there burns the run discovering Electron has nowhere to draw,
    and then reports a chart problem rather than the instruction problem it hit.
    """
    src = read(LAUNCHER)
    branch = src.split("if ($needsDesktop) {", 1)[1].split("$prompt = $promptLines", 1)[
        0
    ]
    negative = branch.split("} else {", 1)[1]
    assert "Do NOT call tv_launch" in negative
    assert "desk_levels.py" in negative, (
        "telling the agent what not to do without naming the replacement is "
        "how a job logs a blocker instead of doing its work"
    )


def test_a_desktop_free_job_that_starts_tradingview_is_reported():
    """It cannot have worked, so whatever the run said about a chart is suspect."""
    src = read(LAUNCHER)
    assert "a desktop-free job started TradingView" in src
    assert "Distrust anything this run said about a chart" in src


# ------------------------------- autologon reports the conversion accurately --


def test_autologon_knows_which_tasks_need_a_desktop():
    assert "$desktopTasks" in read(AUTOLOGON)


def test_autologon_does_not_count_a_missing_sign_in_when_nothing_needs_one():
    """Otherwise it reports the successful conversion as a fault.

    Convicted against the pre-change file: it incremented `$problems` for
    AutoAdminLogon being off unconditionally, and printed 'NOT Interactive' for
    every converted task.

    Anchored on the section heading rather than on any one route's wording.
    The first version of this test split on "1. Automatic sign-in", which the
    PIN/ARSO rewrite renamed -- so it broke on a merge that had not touched the
    behaviour it guards. The heading number is the stable part.
    """
    body = read(AUTOLOGON)
    section = body.split("1. Signing in after a restart", 1)[1].split("# 2.", 1)[0]
    assert "$desktopNeeded" in section, (
        "a machine whose every job is desktop-free must not be told off for "
        "failing to sign itself in"
    )


def test_autologon_separates_needing_a_desktop_from_depending_on_a_sign_in():
    """The bug the first real run on the machine found, 2026-08-30.

    The script computed only `$desktopNeeded` and let section 1 and the summary
    speak for a different question. With the password prompt declined -- a
    supported answer -- it printed "every registered task runs on a stored
    credential" and "nothing needs signing in" about two tasks registered
    Interactive, four lines above a section 3 that correctly said they still
    only run while you are signed in.

    Convicted: replacing `$signInMatters` with `$desktopNeeded` in either the
    section 1 note or the summary restores the contradiction and fails here.
    """
    body = read(AUTOLOGON)
    assert "$signInMatters" in body, (
        "needing no desktop and carrying a stored credential are two facts. "
        "Collapsing them is how this report claimed a conversion that had "
        "been declined"
    )

    # It has to be derived from the LOGON TYPE, and USED. An earlier version of
    # this assertion looked for "$desktopless" anywhere in the setup block and
    # passed against a build where the variable was still assigned and no
    # longer read -- presence is not use. Pin the assignment line itself.
    setup = body.split("$signInMatters = $false", 1)[1].split("if ($agentTasks", 1)[0]
    assert "LogonType" in setup, "the logon type has to be read at all"
    sets_true = [ln for ln in setup.splitlines() if "$signInMatters = $true" in ln]
    assert sets_true, "$signInMatters is never set"
    assert all("$desktopless" in ln for ln in sets_true), (
        "$signInMatters must be decided by the task's actual logon type. "
        "Deciding it from the job table alone is the original bug wearing a "
        "new variable name: a job that needs no chart is still tied to the "
        "sign-in until it carries a stored credential.\n" + "\n".join(sets_true)
    )

    # Only the stored-credential fact may retire the sign-in. Check the branch
    # CONDITION, not proximity -- a nearby mention of $signInMatters in some
    # other branch made the first version of this pass against the bug.
    first_branch = re.search(r"if \(([^)]*)\)\s*\{", zero_problems_summary())
    assert first_branch, "the summary no longer branches"
    assert "$signInMatters" in first_branch.group(1), (
        "the branch that says the sign-in has stopped mattering must be "
        "guarded by $signInMatters, not $desktopNeeded. Got: " + first_branch.group(1)
    )

    note = body.split("Neither route is needed", 1)[0]
    cond = re.findall(r"if \(([^)]*)\)\s*\{\s*$", note, re.MULTILINE)
    assert cond and "$signInMatters" in cond[-1], (
        "the section 1 note claiming neither route is needed must be guarded "
        "by $signInMatters. Got: " + (cond[-1] if cond else "no condition")
    )


def test_the_password_prompt_names_the_pin_case_and_a_way_out():
    """It asked for a password the owner does not have, and offered no route.

    On 2026-08-30 the prompt was answered with enter, because the account has
    no password: they sign in with a Windows Hello PIN and Windows never made
    one. The prompt said only "leave it blank to register them Interactive
    instead", which reads as giving up on the feature rather than as the other
    supported way to reach it.

    A prompt for a credential that may not exist has to say so, and say what to
    do instead. Otherwise the honest answer to it looks like a failure.
    """
    prompt = (
        read(SCHEDULER).split("$deskFree.Count -gt 0", 1)[1].split("Read-Host", 1)[0]
    )
    assert "PIN" in prompt, (
        "the prompt must name the case where there is no account password at "
        "all -- that is the case this machine is actually in"
    )
    assert "ARSO" in prompt and "autologon.ps1" in prompt, (
        "and it must point at the route that needs no password, by name and "
        "with the command to run"
    )


def test_no_advice_anywhere_makes_the_password_the_only_way_out():
    """The dead end had three copies, and fixing one would have left two.

    The prompt, the scheduler's read-back and autologon's section 3 all told a
    reader to supply a password. Any one of them left as the sole instruction
    sends a PIN user after a credential that does not exist.
    """
    for path in (SCHEDULER, AUTOLOGON):
        src = read(path)
        for n, line in enumerate(src.splitlines(), 1):
            if "supply the password" in line or "give it the password" in line:
                window = "\n".join(src.splitlines()[max(0, n - 6) : n + 6])
                assert "ARSO" in window, (
                    f"{path.name}:{n} tells the reader to supply a password "
                    "without naming ARSO nearby. On a machine signed into with "
                    "a PIN there may be no password to supply, and that advice "
                    "is then a dead end."
                )


def test_autologon_offers_both_routes_when_the_tasks_are_interactive():
    """Declining the password is supported, and so is having no password at all.

    Two revisions of this. It first required the summary to say the conversion
    "has NOT been applied" -- the acquittal to the test above, since a report
    that goes quiet leaves the reader believing it landed. Then the owner said
    on 2026-08-30 that no account password exists: they sign in with a PIN, and
    Windows never made one. "Not applied" plus "supply the password" is then
    advice to produce a credential that does not exist, and it frames a
    perfectly complete setup as half-finished.

    So the requirement is now the honest form of the same thing: name BOTH ways
    out, and let the passwordless one stand as an equal. Silence still fails.
    """
    branch = zero_problems_summary()
    branches = re.split(r"\}\s*(?:elseif[^{]*\{|else\s*\{)", branch)
    interactive = branches[1]
    assert "ARSO" in interactive, (
        "the branch reached when the tasks are Interactive must name ARSO -- "
        "it is the only route that needs no password, and on a PIN machine it "
        "is the only reachable one"
    )
    assert "NO PASSWORD NEEDED" in interactive, (
        "it has to say outright that ARSO needs no password. A reader with no "
        "account password has to be able to tell that this route is open to "
        "them without knowing what ARSO is"
    )
    assert "password" in interactive.lower(), "the credential route stays offered too"
    assert "nothing here is broken" in interactive.lower(), (
        "Interactive + ARSO is a complete configuration, so this must not read "
        "as a fault report"
    )


def test_autologon_still_calls_a_chart_job_on_a_desktopless_logon_wrong():
    """The narrowing must not cost the original catch."""
    body = read(AUTOLOGON)
    section = body.split("3. The desk agent tasks", 1)[1]
    assert "$wantsDesktop" in section
    assert "WRONG" in section
    assert "fail at the first chart call" in section, (
        "the reason has to be on the page. 'WRONG' alone sends the reader back "
        "to the decision record to find out what breaks"
    )


def test_autologon_does_not_count_an_interactive_desktop_free_task_as_a_problem():
    """It works exactly as it always did; it is simply not yet converted.

    Counting it would make a working desk report as broken -- which is the
    failure this rewrite exists to remove, arriving from the other direction.
    """
    section = read(AUTOLOGON).split("3. The desk agent tasks", 1)[1]
    arm = section.split("} elseif (-not $desktopless) {", 1)[1].split("\n    }", 1)[0]
    assert (
        "$problems++" not in arm
    ), "an unconverted but working task is a note, not a fault"


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


def zero_problems_summary():
    """The whole `if ($problems -eq 0)` block, brace-matched.

    It used to be sliced to the first `} else`, which was right while the block
    was flat. The block now branches three ways on whether the sign-in is still
    load-bearing, and `"} elseif"` contains `"} else"` -- so the old slice cut
    at the first inner branch and read none of the others. That made the guard
    below pass or fail on which branch happened to come first, which is not
    what it is for.
    """
    body = read(AUTOLOGON)
    start = body.index("if ($problems -eq 0) {") + len("if ($problems -eq 0) {")
    depth, i = 1, start
    while depth:
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
        i += 1
    return body[start : i - 1]


def test_the_summary_does_not_round_unchecked_up_to_fine():
    summary = zero_problems_summary()
    assert "All clear" not in summary, (
        "The per-user ARSO toggle cannot be read from a script, so a run with "
        "zero problems has still not established that the machine signs itself "
        "in. Reporting that as 'all clear' is the same class of error as the "
        "read-back that printed a line from the source file having queried "
        "nothing -- an unchecked thing rounded up to a passing one."
    )
    assert "not verified" in summary.lower(), "it has to name what it could not check"


def test_every_summary_branch_that_needs_a_sign_in_names_the_unverified_toggle():
    """The guard above, applied per branch rather than to the block as a whole.

    Only one of the three branches is entitled to stay silent about the ARSO
    toggle: the one reached when every task carries a stored credential, where
    the toggle genuinely is not load-bearing. In the other two the sign-in is
    still what starts the jobs, so an unread toggle is an open question and has
    to be said out loud.
    """
    summary = zero_problems_summary()
    branches = re.split(r"\}\s*(?:elseif[^{]*\{|else\s*\{)", summary)
    assert len(branches) == 3, f"expected three summary branches, got {len(branches)}"
    silent, *needs_sign_in = branches
    assert "not verified" not in silent.lower(), (
        "the fully-converted branch must NOT claim the toggle is unverified -- "
        "nothing depends on it there, so flagging it is its own false report"
    )
    for branch in needs_sign_in:
        assert "not verified" in branch.lower(), (
            "a branch where the sign-in still starts the jobs has to name the "
            "toggle it could not read"
        )


# --------------------------------------- what a conflict resolution can break --
#
# On 2026-08-30 a branch renamed `$schedCodes` to `$resultCodes`, because the
# table had grown to cover two families of code, while main added a WakeToRun
# read-back directly above the lookup. Resolving that conflict the obvious way
# -- keep both sides -- leaves main's `$schedCodes.ContainsKey(...)` naming a
# table that no longer exists. A method call on `$null` under
# `$ErrorActionPreference = 'Stop'` takes the script down part-way through the
# read-back, which is the one output this whole system exists to produce.
#
# Every test in this file passed on that tree, and passed again with conflict
# markers still in the file. Both of those are the same defect as a scan that
# resolves nothing and reports everything clean.


@pytest.mark.parametrize(
    "path", sorted(REPO.glob("tools/**/*.ps1")), ids=lambda p: p.name
)
def test_no_merge_conflict_markers_survived(path):
    markers = [
        n
        for n, line in enumerate(read(path).splitlines(), 1)
        if line.startswith(("<<<<<<<", ">>>>>>>")) or line.rstrip() == "======="
    ]
    assert not markers, f"{path.name} still carries conflict markers at {markers}"


@pytest.mark.parametrize(
    "path", sorted(REPO.glob("tools/**/*.ps1")), ids=lambda p: p.name
)
def test_no_lookup_names_a_table_the_script_never_defines(path):
    src = read(path)
    assigned = set(re.findall(r"^\s*\$(\w+)\s*=", src, re.MULTILINE))
    # `[string] $RepoRoot` and friends inside param() blocks.
    assigned |= set(re.findall(r"^\s*\[[\w\[\]]+\]\s*\$(\w+)", src, re.MULTILINE))
    looked_up = set(re.findall(r"\$(\w+)\.ContainsKey\(", src))
    looked_up |= set(re.findall(r"\$(\w+)\[", src))
    dangling = sorted(looked_up - assigned)
    assert not dangling, (
        f"{path.name} indexes {dangling}, which it never assigns. PowerShell "
        "throws on a method call against $null, and under "
        "$ErrorActionPreference = 'Stop' that ends the run where it stands."
    )


# ------------------------------------------ the record has to leave the machine --
#
# Between 2026-08-31 and 09-01 four run records and two notes were committed to
# the OneDrive checkout's main and never pushed. GitHub's copy of runs.jsonl
# stopped at 08-28, so for four days no cloud session could see a single run --
# which is the one thing committing the log is for. The playbook said "commit";
# nothing said "push". The push now lives in the launcher, after the agent
# exits, so a run that crashes before its last line still reaches GitHub.
#
# Same caveat as everything above: CI is Linux and never executes this file, so
# these read the source. They convict the previous launcher, which had no push
# at all, and they convict the obvious careless versions of this change.


def publish_function():
    return read(LAUNCHER).split("function Publish-RunLog", 1)[1].split("\n}\n", 1)[0]


def test_the_launcher_pushes_after_the_agent_exits():
    body = code_lines(LAUNCHER)
    assert "function Publish-RunLog" in body
    assert re.search(r"git push \$remote \$onBranch", publish_function()), (
        "the launcher must push; a commit that never leaves the machine is "
        "invisible to every cloud session"
    )


def test_the_push_names_the_fork_and_never_a_bare_origin():
    # 'origin' is upstream in the OneDrive checkout and the fork in the other:
    # a bare origin push is wrong in one of them and fails by succeeding.
    assert "$remote = 'jay'" in read(LAUNCHER)
    assert "origin" not in code_lines(LAUNCHER), (
        "run_job.ps1 names origin in code. Use the 'jay' remote by name; see "
        "docs/local-checkout.md"
    )


def test_every_exit_path_publishes_first():
    """The push covers the runs the agent never completed, or it covers nothing.

    Three ways a run ends with a record to publish: the agent finished, the
    agent exited non-zero and the launcher wrote the record, or Claude Code
    was never found and the launcher wrote the record. Each has to publish
    before it exits. The "not present on this branch" exit is deliberately not
    one of them: that checkout has no desk agent to commit into.
    """
    src = read(LAUNCHER)

    not_found = src.split("if (-not $claude) {", 1)[1].split("exit 1", 1)[0]
    assert "Publish-RunLog $branch" in not_found, "claude-not-found exits unpublished"

    failed = failure_branch()
    assert "Publish-RunLog $branch" in failed, "the non-zero-exit path is unpublished"
    assert failed.index("Record-Failure") < failed.index("Publish-RunLog"), (
        "the failure record must be written before the publish, or the push "
        "carries the previous run's record and not this one's"
    )

    tail = src.split("exit $code", 1)[1]
    assert (
        "Publish-RunLog $branch" in tail.split("exit 0", 1)[0]
    ), "the success path exits without publishing"


def test_the_launcher_commits_only_the_named_paths_and_never_add_a():
    # dd6d1d6 committed the deletion of the log through a `git add -A`. The
    # launcher commits what the agent left behind by naming the two paths that
    # are its to commit, and nothing else in the tree.
    body = publish_function()
    assert "tools/desk_agent/runs.jsonl" in body and "tools/desk_agent/out" in body
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    for bad in ("git add -A", "git add .", "git add --all", "git commit -a"):
        assert bad not in code, f"{bad!r} would sweep another session's work in"
    assert "git commit -q -m $message -- $paths" in code


def test_a_missing_log_is_never_committed_as_a_deletion():
    body = publish_function()
    guard = body.split("$dirty =", 1)[0]
    assert "runs.jsonl" in guard and "Refusing to commit its deletion" in guard, (
        "'git add' of a tracked file that is gone stages its removal; the "
        "launcher must refuse before it stages anything"
    )


def test_main_is_brought_up_to_date_before_the_push_and_a_conflict_is_aborted():
    """A push from behind is refused, and main is behind after every merged PR.

    Without the fetch-and-merge the push would fail on most days while looking
    like it ran -- the fix failing by succeeding. And a merge that conflicts
    must be aborted: an unattended task that leaves a half-merged tree for the
    morning has made things worse, not better.
    """
    body = publish_function()
    assert (
        "if ($onBranch -eq 'main')" in body
    ), "only main is merged; never a feature branch"
    main_block = body.split("if ($onBranch -eq 'main')", 1)[1].split("# 3.", 1)[0]
    assert "fetch $remote main" in main_block
    assert "merge --no-edit ($remote + '/main')" in main_block
    assert "merge --abort" in main_block
    assert "-c gc.auto=0" in main_block, (
        "a fetch that stops to ask about a OneDrive-locked object directory is "
        "an unanswerable hang from a task with no stdin"
    )
    assert main_block.index("merge --abort") < main_block.index(
        "return"
    ), "after an aborted merge the function must stop, not push"


def test_the_push_is_verified_by_asking_git_not_by_its_exit_code():
    # register_desk_agent.ps1's header rule, applied here: the printed line is
    # not evidence, the read-back is. The verification is the same command a
    # cloud session or the next run uses, so the two can never disagree.
    body = publish_function()
    after_push = body.split("git push $remote $onBranch", 1)[1]
    assert "runlog unpushed --remote $remote --branch $onBranch" in after_push
    assert "NOT PUSHED" in after_push and "pushed:" in after_push


def test_publishing_cannot_take_the_run_down():
    # Native git writes progress to stderr, and with $ErrorActionPreference =
    # 'Stop' a redirected stderr line is a terminating error. The record is
    # already written by the time this runs; a push that throws must not
    # convert a logged run into a crashed one.
    body = publish_function()
    assert "$ErrorActionPreference = 'Continue'" in body
    assert "} catch {" in body and "could not publish the run log" in body


def test_the_playbook_tells_the_agent_the_launcher_pushes():
    """The agent has a `git push` grant, so the obvious edit is to tell it to push.

    That covers only the runs the agent completes, which is why the push is in
    the launcher instead -- and the playbook has to say so, or the next review
    "fixes" the missing instruction and the two pushes race.
    """
    playbook = read(REPO / "tools" / "desk_agent" / "playbook.md")
    assert (
        "runlog unpushed" in playbook
    ), "the playbook must tell each run how to check the previous record reached GitHub"
    assert (
        "run-log-not-pushed" in playbook
    ), "and the blocker key to log when it did not"
    clean = playbook.split("**Leave the tree clean.**", 1)[1].split("\n- ", 1)[0]
    assert "push" in clean.lower() and "launcher" in clean.lower(), (
        "the 'leave the tree clean' rule must say who pushes, or a review adds "
        "an agent-side push and the two race"
    )


def test_the_run_log_merges_by_union():
    """Two appends to a line-per-record log are not a conflict.

    The scheduled jobs append on the owner's machine and the weekly review
    appends in the cloud. With the default driver every such pair conflicts at
    the end of the file, the launcher aborts the merge, and the record stays
    unpushed -- the exact outcome the launcher's merge step exists to prevent.
    Exercised against a scratch fork on 2026-09-02: conflict without this line,
    a clean merge and push with it.
    """
    attrs = read(REPO / ".gitattributes")
    assert re.search(
        r"^tools/desk_agent/runs\.jsonl\s+merge=union\s*$", attrs, re.MULTILINE
    ), "tools/desk_agent/runs.jsonl needs merge=union in .gitattributes"


# ----------------------------------- what the jobs may actually run --------
#
# A fortnight of denials, and not one of them was a bug in the job.
#
# premarket and journal both stop at their FIRST step -- `python
# tools/desk_levels.py ...` -- with `requires-approval`, and a headless
# `claude -p` run has nobody to answer a prompt, so that is a denial and not a
# pause. Ten runs between 2026-08-31 and 09-01 died there, each one correctly
# reporting that it had not read a single level, while `runlog review` filed
# both jobs under "never produced an action" -- reading the counts right and
# the situation exactly wrong.
#
# Three consecutive run records diagnosed it precisely and all three refused to
# fix it, which was the correct call: the guardrail forbids an agent widening
# its own access, and reaching the command through an already-permitted one
# would be that same violation wearing a hat.
#
# So the grant goes on the launcher. The check below is the part that lasts: it
# does not assert the four entries exist, it asserts that EVERY command the job
# files invoke is covered by something -- settings.json or the launcher, either
# is fine. A new step added to a job file with no matching grant fails here
# rather than silently on a Tuesday morning three weeks later.

SETTINGS = REPO / ".claude" / "settings.json"

# `python tools/x.py ...` or `python -m package.module ...`, at the start of a
# line in a job file's numbered steps.
JOB_COMMAND = re.compile(
    r"^\s*(python3?\s+(?:-m\s+[A-Za-z_][\w.]*|tools/[\w/]+\.py))", re.MULTILINE
)

# A grant is written `Bash(<command prefix>:*)`.
GRANT = re.compile(r"^Bash\((.+?):\*\)$")


def settings_grants():
    import json

    data = json.loads(read(SETTINGS))
    return list(data.get("permissions", {}).get("allow", []))


def launcher_grants():
    src = read(LAUNCHER)
    # Split on the array's own closing line, not on ")" -- every grant string
    # ends in ":*)" and a naive split stops inside the first one.
    block = src.split("$jobTools = @(", 1)[1].split("\n)", 1)[0]
    return re.findall(r"'([^']+)'", block)


def granted_prefixes():
    out = []
    for entry in settings_grants() + launcher_grants():
        found = GRANT.match(entry)
        if found:
            out.append(" ".join(found.group(1).split()))
    return out


def job_commands():
    found = {}
    for path in sorted(JOBS_DIR.glob("*.md")):
        for match in JOB_COMMAND.finditer(read(path)):
            found.setdefault(" ".join(match.group(1).split()), path.name)
    return found


def test_the_job_files_do_invoke_commands_worth_checking():
    # Guard the guard. A regex that silently matches nothing would make every
    # assertion below pass forever -- the exact shape of
    # docs/decisions/2026-08-29-a-check-that-hardcodes-its-input-is-not-a-check.
    commands = job_commands()
    # Deduplicated, so this counts distinct commands rather than call sites:
    # premarket invokes desk_levels twice and that is one command to grant.
    assert len(commands) >= 2, commands
    assert any("desk_levels.py" in c for c in commands), commands
    assert any("runlog" in c for c in commands), commands


def test_every_command_a_job_file_invokes_is_permitted():
    prefixes = granted_prefixes()
    ungranted = {
        command: origin
        for command, origin in job_commands().items()
        if not any(command.startswith(p) for p in prefixes)
    }
    assert not ungranted, (
        "these commands appear in a job file with no matching grant, so an "
        "unattended run will stop at requires-approval with nobody to answer "
        "it: " + repr(ungranted)
    )


def test_the_grant_is_carried_on_the_launch_command():
    # It cannot live in settings.json: that allowlist is guarded against agent
    # edits by design, and a grant there would reach every session in the
    # checkout rather than only this unattended run.
    src = read(LAUNCHER)
    assert "$claudeArgs += @('--allowedTools') + $jobTools" in src
    assert "& $claude @claudeArgs" in src
    # Ordering: the grant has to be appended before the invocation reads it.
    assert src.index("$jobTools = @(") < src.index("& $claude @claudeArgs")


def test_the_grant_covers_both_interpreter_spellings():
    # `python3` is how the existing runlog grants are written, and a machine
    # that resolves only one of the two would otherwise be a silent denial.
    grants = launcher_grants()
    for script in ("tools/desk_levels.py", "tools/backtest_lab.py"):
        assert f"Bash(python {script}:*)" in grants
        assert f"Bash(python3 {script}:*)" in grants


def test_the_repo_venv_is_put_ahead_of_the_agents_path():
    # The launcher resolved the venv interpreter and then used it only for its
    # own fallback record, so every python the AGENT ran was the system one.
    # That cost the chart step a ModuleNotFoundError on matplotlib while the
    # .venv beside it had the package installed.
    src = read(LAUNCHER)
    assert "$venvScripts = Join-Path $RepoRoot '.venv\\Scripts'" in src
    assert "$env:PATH = $venvScripts + ';' + $env:PATH" in src
    assert src.index("$env:PATH = $venvScripts") < src.index("& $claude @claudeArgs")


def test_the_path_change_is_process_scoped_and_never_persisted():
    # A launcher that writes the user's real PATH would leak one job's
    # environment into every program they open afterwards.
    src = read(LAUNCHER)
    assert "SetEnvironmentVariable" not in src


def test_the_venv_is_only_used_when_it_is_actually_there():
    # The second checkout's .venv is near-empty, and a machine may have none.
    # Prepending a directory that does not exist would be harmless but the
    # guard is what keeps the failure honest if it ever stops being.
    src = read(LAUNCHER)
    guard = src.split("$venvScripts = Join-Path", 1)[1].split("$env:PATH =", 1)[0]
    assert "Test-Path -LiteralPath $venvScripts" in guard


# ------------------------------------------------- the desk signal it carries --
#
# The launcher is the one process that runs on the machine, on a schedule, with
# a push at the end of it -- so it is where the desk signal has to be refreshed
# if a cloud session is ever to see the desk at all. Two things about that are
# easy to get wrong and both were, on the way in.


def test_the_launcher_refreshes_the_desk_signal_before_it_commits():
    body = read(LAUNCHER)
    emit = body.index("desk_signal.py'")
    commit = body.index("git commit -q -m $message")
    assert emit < commit, "the signal is emitted after the commit that should carry it"


def test_the_desk_signal_is_staged_only_when_it_exists():
    """`git add` of a pathspec matching nothing is fatal, and the commit it would
    take down is the run record's -- the one thing the launcher guarantees.

    So the signal joins `$paths` behind a `Test-Path`, and never as a literal in
    the initial list. A first run on a machine that has never emitted one must
    still commit its record.
    """
    body = read(LAUNCHER)
    initial = body.split("$paths = @(", 1)[1].split(")", 1)[0]
    assert "signals/desk.json" not in initial
    guarded = re.search(
        r"if \(Test-Path -LiteralPath \(Join-Path \$RepoRoot 'signals\\desk\.json'\)\) \{\s*"
        r"\$paths \+= 'signals/desk\.json'",
        body,
    )
    assert guarded, "signals/desk.json must be appended behind a Test-Path guard"


def test_a_failed_signal_emit_cannot_take_the_run_down():
    """The run record matters more than the signal. Warn, never throw."""
    body = read(LAUNCHER)
    block = body.split("desk_signal.py'", 1)[1].split("# 1. Commit", 1)[0]
    assert "catch {" in block
    assert "WARNING: desk signal not refreshed this run" in block

# Unblock the journal job, and turn the alerts job off

*Decided 2026-08-29.*

Two desk-agent jobs had been running every weekday and producing nothing, for
opposite reasons. Both were fixed the same afternoon because the run log had
been asking for both, in writing, for over a week.

## The journal job could not reach the thing it exists to read

`trade-journal.html` lives at `C:\Users\Gexio\OneDrive\trade-journal\` and not
in this repository — it is a personal document and this fork is public. A
headless Claude Code session is confined to its working directory, and
`run_job.ps1` invoked the agent with a bare `claude -p`. So the journal job
opened, found the file outside its boundary, logged
`journal-path-outside-session-working-directory`, and stopped. Five consecutive
weekdays, five identical failures.

**The agent could not fix this itself, and correctly did not try.** Widening its
own access is exactly what the playbook guardrail forbids, so what it could do
was file the request, which it did five times. Meanwhile the paper record moved
on 08-25 and again on 08-27 while the journal sat unchanged since 08-18 — ten
days, twice missing the activity it exists to capture.

`run_job.ps1` now passes `--add-dir`, **scoped per job**:

```powershell
$jobDirs = @{
  journal = @(Join-Path $HOME 'OneDrive\trade-journal')
}
```

Scoped, because the alternative was handing a personal document to every
unattended run on the machine including an hourly one with no business reading
it. A path that does not exist is logged and dropped rather than passed to
`claude`, so a wrong path cannot kill a run for a reason unrelated to its job —
the failure would name the flag instead of the cause, which is the same class of
misleading evidence this launcher was built to prevent.

## The alerts job was working perfectly and had nothing to do

Twenty-five consecutive runs, hourly through every session, zero actions ever.
Not broken: `alert_list` returned zero on a session proven logged in, because
there are no alerts configured on the agent's TradingView login. The job did the
right thing every time and there was nothing there.

It is off — `Enabled = $false` on the Alerts entry in
`register_desk_agent.ps1` — rather than deleted. `jobs/alerts.md`, the launcher's
`ValidateSet` and a manual `run_job.ps1 -Job alerts` all still work; configuring
alerts on that login and flipping the flag is the whole of turning it back on.

**Turning a job off has to be effective on the machine, not just in the source.**
Deleting the table entry — the obvious way to retire a job — would have left
Windows holding the registration and firing it forever, with nothing left in the
script to unregister it and `-Remove` no longer aware it existed. So the disabled
branch of the registration loop calls `Unregister-ScheduledTask`, and re-running
the script is what turns the job off for real.

## The part worth remembering: honest reporting hid the job from the review

The weekly review is supposed to name a job that has never produced anything.
It never named this one, and could not have.

`runlog.dead_jobs` skips records whose outcome is `skipped` before counting them
as attempts, on the sound general principle that *a job that never got the chance
to act has not been given one*. `jobs/alerts.md` instructs the job to log
`skipped` when nothing fired — which is correct, and is what the whole
skipped-vs-failed distinction exists for. The consequence is that a job whose
correct outcome is `skipped` can never reach `min_runs`, however many hundreds of
empty runs it accumulates. Premarket, with a fifth of the runs and the same zero
actions, was named for removal. Alerts, with five times the evidence, was
invisible.

The distinction the code does not draw: a run skipped for a passing precondition
(a holiday, a closed market) really is a chance not given, but a run skipped for
a precondition absent twenty-five times running, carrying a recurring blocker
key, is dead weight. That is a real change to `runlog.py` and it was **not** made
here — one hand-turned-off job is not enough evidence to reshape the rule that
decides which jobs get retired, and the agent itself may not touch that file for
the reason it exists. It is written down in `jobs/alerts.md` so the second job
that hides the same way arrives with the diagnosis already done.

## What is pinned

`tests/test_desk_agent_launcher.py`. CI is Linux and never executes either
script, so the checks are static: the scoped `--add-dir` reaches the invocation
and no second job gets a directory outside the repo, alerts is off and the
disabled branch unregisters rather than skipping, every scheduled job name
agrees across all three places it is written, and — the one that would otherwise
be found the hard way — every `.ps1` under `tools/` is ASCII-only. Windows
PowerShell 5.1 reads a BOM-less script as Windows-1252, where one stray byte
closes a string and the file parses to nothing. From a scheduled task that is an
empty log and `LastTaskResult = 1`: indistinguishable from a task that never
fired, which is the single distinction this system is built to make.

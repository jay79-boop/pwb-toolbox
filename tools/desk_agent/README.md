# The desk agent

An unattended Claude Code agent that runs four jobs on a schedule, writes down
what happened, and revises its own playbook from that record once a week.

```
playbook.md        standing instructions, incl. guardrails it may not edit
jobs/*.md          one file per job: what that run is for
runlog.py          the memory: append a record, ask questions of the records
runs.jsonl         the records themselves, committed on purpose
run_job.ps1        launcher the scheduled tasks call
out/               what the jobs produce (gameplans, Pine reports) - committed
logs/              raw stdout per run - gitignored, noise
```

## The loop

Each scheduled run reads the playbook and its job file, does the work, and
appends exactly one record. Once a week the **review** job reads the last forty
records and asks three questions: what keeps blocking us, which job has never
produced anything, and is the outcome mix improving or regressing. It edits the
playbook to fix what it found and opens a **draft PR**.

So the agent's learning history is `git log -p tools/desk_agent/playbook.md`,
and undoing a bad lesson is one revert.

## Where each job runs, and why

The split follows what each job can physically reach.

| job | runs | where | why there |
| --- | --- | --- | --- |
| `premarket` | weekdays 07:00 | local | needs TradingView Desktop |
| `alerts` | weekdays hourly, 09:00-16:00 | local | needs TradingView Desktop |
| `journal` | weekdays 16:30 | local | the journal is on your disk and not in this repo |
| `pine_loop` | on demand | local | starts from a description only you can give |
| `review` | weekly | **cloud** | reads only the committed log; needs neither |

A cloud session shares nothing with you but GitHub — no disk, no charts, no
TradingView. That is exactly why the review is the one job that belongs there:
it makes the weekly self-improvement independent of whether your machine
happened to be awake.

## Setting it up

Local tasks:

```powershell
cd C:\Users\Gexio\OneDrive\pwb-toolbox
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\register_desk_agent.ps1
```

It registers three tasks, then reads each one back from Windows and prints the
next run time. The read-back is the point: `Register-ScheduledTask` can fail
while the surrounding script still prints that it worked.

Run one immediately, without waiting for its trigger:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\desk_agent\run_job.ps1 -Job premarket
```

Remove everything:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\register_desk_agent.ps1 -Remove
```

The cloud review is a Routine on the Claude account, not a file here. It is
listed under Routines and can be paused or deleted there.

A Routine's sessions may start without GitHub tooling, in which case the review
can push a branch but not open the PR itself. That case is handled rather than
fatal: it pushes, logs a `cannot-open-pr-from-this-session` blocker naming the
branch, and never falls back to pushing straight to `main`. Should it keep
happening, the recurring-blocker machinery raises it after the third week --
which is the loop noticing its own gap.

## Reading the log yourself

```bash
python -m tools.desk_agent.runlog summary --last 20
python -m tools.desk_agent.runlog review --last 40
```

`summary` reports `healthy: yes` only when nothing failed **and** at least one
run did something. A week of clean skips is not health — it is a scheduler
firing faithfully into a job with nothing to do, which is the failure mode this
is built to surface.

## Three design decisions worth knowing before you change anything

**Skipped and failed are different.** A holiday morning is `skipped`; a refused
connection is `failed`. Collapse them and a broken agent reports itself quiet.

**A run with no actions is often correct.** A scan that honestly finds no setup
is `ok` with an empty action list. The review notices a job that has *never*
acted across many attempts, and that signal only works if quiet runs stay
quiet. Nothing in the playbook rewards looking busy.

**Blockers are counted by slug, not by message.** "connection refused at 07:01"
and the same at 07:02 are one problem; as free text they are two singletons and
neither crosses the threshold to be acted on. `blocker_key` strips timestamps,
paths and bare numbers before counting.

## What the agent may not do

In `playbook.md`, under a heading marked not-self-editable:

- never place, modify or cancel an order, on any account, in any mode
- never connect a broker or accept a linking prompt
- run only against the TradingView login with **no broker connected**, and
  abandon the run if it finds one
- never widen its own access — no new MCP servers, no permission edits, no
  disabling a check that is in the way
- never edit `runs.jsonl`, `runlog.py`, its own guardrails, or `pwb_toolbox/`

The review job may rewrite the rest of the playbook. It may not touch that
section, and if it thinks it should, it has to stop and say so in the PR.

`runs.jsonl` is on that list because an agent that can edit its own record of
what happened cannot be reviewed. Rewriting history to make a trend look better
is the precise failure the rule exists to prevent.

## Permissions

`.claude/settings.json` allowlists the handful of commands these jobs actually
need. It deliberately does **not** use `--dangerously-skip-permissions`: an
unattended agent hitting a permission it lacks should fail loudly, log a
blocker, and have the weekly review surface it once it recurs. That feedback
loop is the intended way the allowlist grows — narrowly, with a reason, and in
a reviewable commit.

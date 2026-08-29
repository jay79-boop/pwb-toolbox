# The desk agent

An unattended Claude Code agent that runs three jobs on a schedule, writes down
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
| `alerts` | **off** (was weekdays hourly, 09:00-16:00) | local | retired 2026-08-29: 25 runs, 0 actions, no alerts on the login |
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

It registers every **enabled** task, then reads each one back from Windows and
prints the next run time. The read-back is the point: `Register-ScheduledTask`
can fail while the surrounding script still prints that it worked.

A job with `Enabled = $false` in that script's `$jobs` table is not merely
skipped — the run **unregisters** it, so re-running this is how a job gets
turned off on the machine and not just in the source. `alerts` is currently the
only one.

**The read-back covers the disabled ones too, and it asks Windows.** Turning a
job off has two failure modes that look identical in the output:
`Unregister-ScheduledTask` throws both when there is no such task and when the
removal fails, so the removal line cannot tell you which happened — and the
first version of the read-back printed `not scheduled` straight out of the
source file, having checked nothing. A run could therefore report a job off
while Windows kept firing it on the old schedule. The read-back now queries
Windows for a disabled job and prints `STILL ON` with the live trigger count if
one survives, plus a warning below the tally so it cannot scroll past.


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

## The first weekly review fired and left no trace

2026-08-23, 14:01 UTC. The Routine's `last_fired_at` confirms it ran. It produced no
run record, no branch, no pull request — nothing, on any branch. The cause was never
established: a trigger-fired session does not appear in the session listing, so its
transcript could not be read, and the obvious hypothesis was tested and disproved
(`python -m tools.desk_agent.runlog` works fine on a bare checkout with no venv and
no `PYTHONPATH`).

What made this visible at all was a check that had been added to a PR watch two hours
earlier for an unrelated reason: count the records in `runs.jsonl` and treat a number
that has not grown as a finding. Without it, the week would have read as quiet.

That is the whole failure mode this design exists to catch, arriving from a direction
nobody had guarded: not a job that runs and fails, but a job that runs and reports
nothing. Two changes followed.

**The Routine now notifies on completion.** `update_trigger` cannot add notifications,
so it was recreated with `push` enabled. A run that goes wrong now reaches the owner
directly instead of depending on somebody counting records afterwards.

**Its prompt now leads with the obligation to leave evidence.** Whatever blocks it — a
missing file, a failed command, no push access — it appends a record saying so, commits
it, pushes it, and states the problem in its final message. Logging is the last thing it
gives up, not the first. The old prompt put that instruction at the end, where anything
that stopped the run early also stopped the logging.

## Monday's first scheduled run never fired, and would have failed if it had

2026-08-24. Two independent faults, found together.

**The task had never run.** `LastTaskResult` was `267011` — `SCHED_S_TASK_HAS_NOT_RUN`
— with `LastRunTime` at the `11/30/1999` sentinel and zero missed runs, four hours
after its 07:00 trigger. `Register-ScheduledTask` defaults to a `LogonType` of
`Interactive`, meaning the task runs *only while the owner is signed in*; a 07:00
job on a machine that sleeps overnight then quietly does nothing. The read-back now
prints `State`, `LastTaskResult` and `LogonType` so this is visible at registration
instead of a fortnight later.

**And the checkout was on `main`, which does not carry the desk agent.** Other
sessions switch branches in that working tree all day, and until this merges the
agent exists on exactly one branch. A task pointed at a path inside that tree stops
existing the moment somebody checks out something else — and then fails with no log,
because the directory it would log to is on the branch that is missing.

The second fault is the more instructive one, because it is the third time this
system has failed by leaving nothing behind. Two changes:

**The launcher is installed outside the repository.** `register_desk_agent.ps1`
copies `run_job.ps1` to `%LOCALAPPDATA%\pwb-desk-agent\` and points the tasks
there, passing `-RepoRoot` explicitly. The launcher then always exists whatever the
checkout is doing, so it always runs, so a bad run always leaves evidence. Re-run
the registration script after changing `run_job.ps1` — the copy is what executes.

**The launcher knows where the repo is; it does not guess.** Registration writes
`repo_root.txt` beside the installed copy. The original code derived the root by
walking two directories up from itself — correct while it lived in the repo, and
wrong the moment it moved, at which point it decided the repository was
`C:\Users\<you>\AppData` and broke the log path, the branch read and the Python
import together, none of which named the real cause. `-RepoRoot` wins, then the
pointer file, then the old guess for anyone running it straight out of a checkout.

**Logs are written outside the repository too**, to
`%LOCALAPPDATA%\pwb-desk-agent\logs\`, and the launcher refuses to start against a
checkout that lacks `playbook.md` or the job file: it names the branch, says the
agent is not on it, writes a `failed` record with blocker
`desk-agent-not-present-on-branch`, and exits 2. Verified against a repo with the
agent removed.

**The real fix is still to merge.** Once the desk agent is on `main` it exists on
every branch cut afterwards, and the guard above becomes a backstop rather than the
thing standing between you and a working agent.

## Reading the log yourself

```bash
python -m tools.desk_agent.runlog summary --last 20
python -m tools.desk_agent.runlog review --last 40
```

`summary` reports `healthy: yes` only when nothing failed **and** at least one
run did something. A week of clean skips is not health — it is a scheduler
firing faithfully into a job with nothing to do, which is the failure mode this
is built to surface.

## The agent's TradingView login

**Checked 2026-08-22: no broker connected.** The Trading Panel shows brokers with
`Connect` buttons — no account number, no balance, no order ticket.

This is the control the whole arrangement rests on. The permission model removes
the agent's *means* to place an order; a broker-free login removes the
*possibility*, and it keeps holding even if a future change to the deny list gets
it wrong. `docs/tradingview-agent-security.md` has the reasoning.

Two things about that line worth being honest about:

**It is a point-in-time observation, not a standing property.** Connecting a
broker to this login later removes the protection silently, and nothing in the
system can detect it happening. The agent's obligation is to fail closed on
positive evidence — an account number, a balance, an order ticket seen in
passing — which reacts to seeing one but cannot prove absence. If you ever link a
broker for live trading, put it on a different login, or stop the scheduled tasks
until the agent is moved.

**The account is deliberately not named here.** This fork is public.

### Two plan limits that look like broken tooling

The login is on the **Basic** plan. Both of the limits below were mistaken for a
broken tool before the cause was found, and both fail the way this repo keeps
running into — quietly, with a plausible-looking result.

- **Bar replay below the daily timeframe is Premium-only, and `replay_start`
  answers `not started` rather than erroring.** The playbook explicitly permits
  replay's simulated buy/sell (it is not an order), so an agent will reach for
  it, retry it, and screenshot the result before anything mentions a paywall.
  Read `not started` on an intraday chart as the plan limit, not as a failed
  call.
- **TradingView Desktop reopens on BTCUSD, 1D, with its six default studies** —
  on every relaunch *and* on every MCP reconnect, which the debug-port dance
  makes routine. Symbol, timeframe and any injected script all have to be
  re-applied before a result is read. Skip it and the number gets taken off the
  wrong instrument, or off an empty chart, and nothing about the screenshot says
  so.

## The journal job reads one directory outside the repository

`trade-journal.html` is on your disk and not in this repo, and a headless
session is confined to its working directory — so for five consecutive weekdays
the journal job could do nothing but log the same blocker while the paper record
moved twice underneath it. `run_job.ps1` now passes
`--add-dir $HOME\OneDrive\trade-journal`, **only when `-Job journal`**. The
other jobs keep the boundary they had; there is no reason an hourly triage run
should be able to read a personal document.

Two properties of that block are deliberate. A path that does not exist is
logged and dropped rather than passed to `claude`, because a bad `--add-dir`
kills a run for a reason that has nothing to do with the job and names the flag
instead of the cause. And `-AddDir` on the launcher appends to whatever the job
gets by default, so a different machine layout needs no code edit.

The agent could not have done this itself: widening its own access is what the
guardrail forbids, which is why it filed the request five times instead.

## A failed run says what it printed

When the agent exits non-zero the launcher writes the record itself, and that
record now carries a bounded tail of the agent's own output rather than just
`agent run failed with exit code N`. The buffer was always captured; it was
simply thrown away, leaving the only account of a failure in a log under
`%LOCALAPPDATA%` that no cloud session can read.

A **tail**, capped and flattened to one line, not the buffer: `runs.jsonl` is
committed precisely because raw stdout under `logs/` is not, and a transcript in
a tracked file in a public fork is the thing that split exists to prevent. The
record names the log's file name, never its path. And "the agent printed nothing
at all" is reported as its own fault, because a crash with a message and a
process that produced no output are different problems.

## Three design decisions worth knowing before you change anything

**Skipped and failed are different.** A holiday morning is `skipped`; a refused
connection is `failed`. Collapse them and a broken agent reports itself quiet.

**A run with no actions is often correct.** A scan that honestly finds no setup
is `ok` with an empty action list. The review notices a job that has *never*
acted across many attempts, and that signal only works if quiet runs stay
quiet. Nothing in the playbook rewards looking busy.

**`runs.jsonl` is deleted by accident more easily than you would think.** It was
destroyed once, in commit `dd6d1d6`, by a test cleanup that removed the file and a
`git add -A` that committed the removal. Nothing complained: the next run simply
recreated it empty, and the weekly review would have read "no runs logged yet" and
been correct about the file while being wrong about the world.

The playbook forbids the *agent* from editing this file. That rule turned out to
protect against the wrong actor first. Two practical consequences: run the
launcher against a scratch `-RepoRoot` when testing it, never the real checkout;
and because the file is tracked, `git log --diff-filter=D -- tools/desk_agent/runs.jsonl`
finds the deletion and `git checkout <sha>^ -- tools/desk_agent/runs.jsonl` undoes
it. Being in git is what made this recoverable rather than merely regrettable.

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

`.claude/settings.json` classifies **every one of the bridge's 84 tools**, by
name, as allowed or denied. Nothing is left to omission by accident — checking
that each tool appears in exactly one list is cheap, and the first version of
this file allowlisted the Bash commands and forgot the MCP tools entirely.
That is not a gap at the margins; it is the agent having no chart access at
all. The first unattended run was denied on its very first call and could not
even establish whether it was permitted to proceed.

The reasoning that produced the hole is still right and is kept: this
deliberately does **not** use `--dangerously-skip-permissions`, because an
unattended agent hitting a permission it lacks should fail loudly and log a
blocker rather than quietly having root. What was wrong was treating "fail
loudly" as sufficient without also granting the tools the jobs obviously need.
Loud failure is a safety net, not a configuration.

### What is denied, and why those twelve

| denied | why |
| --- | --- |
| `ui_evaluate` | runs arbitrary JavaScript in the logged-in page. The most dangerous tool here by a distance: it can do anything the page can, order entry included. |
| `ui_click`, `ui_mouse_click`, `ui_type_text`, `ui_keyboard`, `ui_hover`, `ui_scroll`, `ui_open_panel`, `ui_find_element`, `ui_fullscreen` | generic UI driving. The Buy button is a DOM element like any other. |
| `batch_run` | composes arbitrary tool calls, so it routes around every line above. |
| `tv_update` | changes the bridge's own code unattended, which would silently invalidate the audit in `docs/tradingview-agent-security.md`. |

These sit in `deny` rather than merely being absent from `allow`, because deny
beats allow: a later blanket grant of the whole server cannot quietly
re-enable them.

### The step this was expected to cost, and did not

The obvious objection is that locking out `ui_click` breaks premarket step 3 —
deploy a strategy and read the Strategy Tester. Checked against the bridge's
source rather than assumed, it does not:

- `data_get_strategy_results`, `data_get_equity` and `data_get_trades` read
  `dataSources()` and `performance()` straight off the chart model.
  `src/core/data.js` imports nothing from `core/ui.js`.
- `pine_save`, `pine_compile` and `pine_smart_compile` do click Pine editor
  buttons — but inside their own implementations, on named editor controls. The
  agent never needs the generic clicking tool to get a script deployed.

So the strict list costs nothing that was wanted. Worth stating plainly, because
the opposite conclusion — "we had to loosen it to make the feature work" — is
how guardrails usually die.

### What the claim above missed, and what it did not

That "costs nothing that was wanted" claim was checked against the Strategy
Tester path and held there. It was not checked against the guardrail's own
broker check, and that is where it failed: the playbook told the agent to open
the Trading Panel and confirm no broker was linked, but `ui_open_panel` is
denied — and, worse, **no tool in the bridge reports broker linkage at all**.
`tv_ui_state` returns which panels are open, not what is connected. Only
`ui_evaluate` could read it, and that is denied for the reasons that make it
able to.

So the check was unperformable whatever the permission list said, and every run
would have abandoned on a precondition it could never satisfy. The agent found
this on its second unattended run and, correctly, flagged it as an open question
rather than asserting it — it could neither read the bridge source nor test live
with CDP down.

The fix is not to loosen the deny list. It is to stop pretending a negative can
be proven: the guardrail now rests on the permission system and a login
established broker-free at setup, and the agent's obligation is to **fail closed
on positive evidence** — an account number, a balance, an order ticket seen in
passing — rather than to prove absence every run. Re-checking the Pine and
Strategy Tester paths after this: `pine.js` opens the editor itself through
`bottomWidgetBar.activateScriptEditorTab()` and `data.js` opens the backtesting
panel through `showWidget('backtesting')`, so neither needs `ui_open_panel`. That
part of the claim stands.

### Launching and closing TradingView

`run_job.ps1` records whether TradingView was already running, and closes it
after the agent exits if it was not — on success, on failure, and on crash.

This lives in the launcher rather than the playbook because the agent's first
real run got it right for the right reason: it refused to `tv_launch` while it
had no permitted way to quit, since that would leave an unauthenticated debug
port open on a sleeping machine. Making shutdown mechanical rather than a
request the agent has to remember removes the one-way door. If the owner already
had TradingView open, it is left alone — killing it would destroy their session.

### What this does and does not buy

It removes the agent's **means** to place an order. It does not remove the
port's **power** to: anything holding a CDP connection can still drive the
chart, which is what `docs/tradingview-agent-security.md` is about and why the
broker-free login stays the load-bearing control. Defence in depth, not a
replacement for it.

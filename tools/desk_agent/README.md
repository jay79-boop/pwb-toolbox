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

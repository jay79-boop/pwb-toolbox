# Job: alert triage

> **OFF since 2026-08-29.** Not scheduled: `Enabled = $false` on the Alerts
> entry in `tools/register_desk_agent.ps1`. Twenty-five consecutive runs and
> zero actions, because there are no alerts configured on the agent's
> TradingView login for it to triage. Nothing here is wrong — the job did the
> right thing every time and had nothing to do, which is a reason to stop
> running it rather than to fix it.
>
> **To turn it back on:** configure alerts on that login, set `Enabled = $true`,
> and re-run `register_desk_agent.ps1`. This file, the runner's `ValidateSet`
> and a manual `run_job.ps1 -Job alerts` all still work while it is off.

**Runs:** nothing scheduled while it is off. Was hourly on weekdays during
market hours.
**Goal:** say which alerts matter, so nine firing becomes two worth looking at.

## Do

1. Read the alerts that have fired since the last run.
2. Drop the ones that do not survive contact with context: a level tagged in a
   session that does not trade it, a repeat of one already surfaced, an alert
   on an instrument outside the current focus.
3. For what survives, say in one line why it is worth a look and what would
   confirm it.
4. If nothing survives, say nothing and log `ok` with no actions.

## The rule this job lives or dies by

**Silence is the product.** An alert triage that surfaces everything is a more
expensive version of the alert list, and one that surfaces something marginal
to look useful trains the owner to ignore the next one. Ranking is only
valuable if the bottom of the ranking gets dropped.

If this job has surfaced something on nearly every run for a fortnight, it is
not triaging, and the review should say so.

## What honest reporting cost this job

`skipped` is the correct outcome for a run that found nothing, and the section
below asks for exactly that. It is also why nobody was ever told to retire this
job. `runlog.dead_jobs` skips `skipped` records before counting attempts — a job
that never got the chance to act has not been given one — so a job whose
*correct* outcome is `skipped` can never reach `min_runs`, however many hundreds
of empty runs it accumulates. The premarket job, with a fifth of the runs and
the same zero actions, was named for removal; this one stayed invisible.

The distinction the code does not draw yet: a run skipped for a passing
precondition (a holiday, a closed market) really is a chance not given, but a
run skipped for a precondition absent twenty-five times running, carrying a
recurring blocker key, is dead weight. Worth fixing in `runlog.py` if a second
job ever hides the same way — turning this one off by hand was the cheaper
answer to the first case, not a general one.

## Honest outcomes

- Nothing fired → `skipped`.
- Things fired, none survived triage → `ok`, no actions. This should be common.
- Something surfaced → `ok` with one action per item surfaced.

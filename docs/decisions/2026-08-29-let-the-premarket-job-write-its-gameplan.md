# Let the premarket job write its gameplan

*Decided 2026-08-29.*

Step 4 of `jobs/premarket.md` says to write the gameplan to
`tools/desk_agent/out/gameplan-<YYYY-MM-DD>.md` and commit it. Four consecutive
premarket runs finished the analysis and never wrote the file. Each run recorded
the reason itself, and each was right:

> Ranked gameplan produced but NOT written to disk — the Write tool is denied
> for this agent, so job step 4 was unreachable and the analysis survives only
> in this record.

> Write is denied for every path tried (out/ and tools/desk_agent/ both), mkdir
> is blocked, and the shell cannot write a file, so job step 4 is structurally
> unreachable as permissioned. Not widening access, per the guardrail — a human
> has to decide.

That refusal is the guardrail working. An agent that can widen its own
permissions has none, so it filed the request and stopped. It could not escalate
any other way: as one run noted, the three records phrased the blocker three
different ways, so `runlog`'s recurrence counter never registered it as one
recurring problem.

The cost was not just a missing file. The gameplan survived only inside
`runs.jsonl`, so a document meant to be read at 08:00 was buried in an audit log
— including a Strategy Tester result (profit factor 0.82 over 18 trades, before
the commission and slippage the saved chart does not charge) that disqualified a
strategy and deserved to be seen.

## What was actually wrong

Two independent faults, either one sufficient to lose the file.

**No `Edit` rule covered the path.** A headless `claude -p` run has nobody to
answer a permission prompt, so a write outside the allow list is denied outright.
The trap underneath is worse than the omission: Claude Code checks file access
against `Edit(path)` and `Read(path)` rules **only**. A `Write(path)` rule is
accepted, never consulted, and warns at a startup nobody watches — so the
obvious spelling of this fix is a silent no-op that looks applied.

**The directory did not exist.** `mkdir` is not among the agent's permitted
commands, so it could not create what it was told to write into.

## The fix

`.claude/settings.json` grants exactly `Edit(/tools/desk_agent/out/**)`. The
leading single slash anchors at the settings source, which for project settings
is the working directory — and `run_job.ps1` does `Push-Location $RepoRoot`
before invoking the agent, so it resolves to the repo root. Dropping the slash
would anchor at the current directory instead.

`tools/desk_agent/out/` is committed with a `.gitkeep`, which removes the need
for a `mkdir` permission rather than granting one.

Deliberately narrow. The unattended agent can write gameplans and nothing else;
it still cannot touch its own guardrails, its run log, or `runlog.py`. Widening
to `Edit(/tools/desk_agent/**)` would have handed it exactly those.

## Why the gameplans are committed rather than ignored

Same reasoning as `runs.jsonl`: what is not committed cannot be reviewed from a
cloud session, and the weekly review is the one thing that reads this work. Raw
stdout under `logs/` stays ignored, because that is machine noise that can carry
chart detail; a gameplan is deliberate written output whose whole purpose is to
be read.

## What is pinned

`tests/test_desk_agent_gameplan_path.py` holds the three places that must keep
agreeing — the path in `jobs/premarket.md`, the `Edit` rule, and a directory
that is really there — plus a check that no `Write(...)` path rule creeps back
in. Each was confirmed to fail against the broken state before being kept.

## What this does not fix

The most recent premarket run failed with exit code 1 and no captured output, so
its cause is still unknown. That is addressed separately by
[A failed run should say what it printed](2026-08-29-a-failed-run-should-say-what-it-printed.md);
the next run will record what it printed. Writing the gameplan and crashing
before you get there are different problems.

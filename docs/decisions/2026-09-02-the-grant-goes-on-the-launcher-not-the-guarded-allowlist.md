# The grant goes on the launcher, not the guarded allowlist

*Decided 2026-09-02.* Closes ten consecutive desk agent failures across
`premarket` and `journal`, 2026-08-31 to 09-01.

**Decision:** grant the desk agent `tools/desk_levels.py` and
`tools/backtest_lab.py` via `--allowedTools` on its launch command, and put the
repo `.venv` ahead of the agent's `PATH`. Do not add either grant to
`.claude/settings.json`.

## What was broken

Both jobs stopped at their **first** step. `python tools/desk_levels.py levels
NQ=F --markdown` returns `requires-approval` before executing, and a headless
`claude -p` run has nobody to answer a prompt — so that is a denial, not a
pause. Zero levels read, therefore no ranking, no gameplan, no close-outs.

The failure had the shape this repository keeps meeting: `runlog review` filed
both jobs under *"never produced an action — propose removing"*, which reads
the counts correctly and the situation exactly wrong. Neither job had ever once
reached the step where it could find something and honestly pass on it.

A second wall sat behind it. The launcher resolved `.venv\Scripts\python.exe`
at line 98 and then used it **only** to write its own fallback record — nothing
put that venv on the child process's `PATH`, so every `python` the agent ran
was the system install. That cost the chart path a `ModuleNotFoundError` on
matplotlib while the `.venv` beside it had the package.

## Why the agent could not fix it, and was right not to

Three consecutive run records diagnosed this precisely — down to the insertion
point and the line number — and all three refused to apply it. That was the
correct call, and it is worth recording as a success rather than an obstacle:
the guardrail forbids an agent widening its own access, and reaching the
command through an already-permitted one would be that same violation wearing a
hat.

The guard held from the other side too. A session attempting the
`.claude/settings.json` edit on the owner's explicit instruction was **blocked
by the harness**, which is the gate working as designed: an agent that can widen
its own permissions does not have permissions. Routing around it with a
different file-editing tool would have defeated the intent rather than the
mechanism, so it was not attempted.

## Why the launcher is the better home anyway

This is not a consolation prize. `--allowedTools` on the launch command is
**narrower** than the settings file in the way that matters:

- A grant in `.claude/settings.json` reaches **every session in this checkout**,
  interactive ones included. What needs the grant is one unattended job.
- The launcher is a tracked file, so the change arrives through pull request
  review — a person approves it by merging, which is exactly the authorisation
  the guard exists to require.
- `--allowedTools` **adds** to what settings already permits rather than
  replacing it, so the runlog grants the job needs to write its own record are
  untouched.

Granted in the **path form**, because that is the form the job files invoke
(`jobs/premarket.md` lines 18-19, `jobs/journal.md` line 28), and in both
interpreter spellings, matching how the existing runlog grants are written. The
`-m` form the agent tried after being denied is not granted: it was improvisation
after the fact, not something any job asks for.

## The check that outlasts the fix

Adding four strings would fix Tuesday and nothing else. So the test does not
assert those four strings exist — it asserts that **every command the job files
invoke is covered by some grant**, from either source. A new step added to a job
file with no matching permission now fails in CI, rather than silently on a
Tuesday morning three weeks later.

It is paired with a guard on itself: a test that the command regex actually
matches something, because a pattern that quietly matches nothing would make
every assertion pass forever — the failure named in
[a check that hardcodes its input is not a check](2026-08-29-a-check-that-hardcodes-its-input-is-not-a-check.md).
Both were verified to convict by removing the grant and watching them fail.

## What this does not do

It does not clear the blockers in `runs.jsonl`. Those are read from the newest
record per job, so they stay live until the jobs actually run again on the
owner's machine and file a clean one. `tools/awareness.py brief` will keep
reporting them until then, correctly.

Nor does it settle `journal`'s deeper problem: its register is browser-only
`localStorage`, and the run log has already argued that pointing it at
`paper-book.json` — an on-disk machine-readable record of closed trades sitting
in the same folder — is the real fix. That is a change to the job definition and
belongs to the weekly review, not here.

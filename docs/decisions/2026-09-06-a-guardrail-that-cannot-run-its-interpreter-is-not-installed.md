# A guardrail that cannot run its interpreter is not installed

*Decided 2026-09-06.*

## What happened

`.claude/hooks/session-hard-stop.sh` shipped on 2026-09-05 as the only guardrail
in this repo that can actually refuse a tool call. It had a test file asserting
every path, including the ones that must stay open. On the owner's Windows
machine it had never once been able to fire.

The hook resolved its interpreter with `command -v python3 || command -v python`.
Windows registers an App Execution Alias at
`AppData/Local/Microsoft/WindowsApps/python3.exe` — a stub that prints "Python
was not found" and exits 49. `command -v` finds it before any real interpreter,
because it is a real file on PATH. Every python call in the hook then failed into
its own `|| true`, the token total came back empty, the numeric guard rejected
the empty string, and the hook exited 0. On every single tool call.

Every property the hook was built for held except the one that mattered: it was
present, registered, documented, and unable to fire.

## Why the tests did not catch it

They could not. The test helper built a deliberately minimal environment,
`PATH=/usr/bin:/bin:/usr/local/bin`, to keep the run hermetic. There is no python
at those paths on Windows, so the hook took the same silent early exit — and
every "must block" assertion became a vacuous pass, then a visible failure only
because the expected return code was 2 and the actual was 0.

That is the trap worth naming. A hermetic environment that removes the thing
under test does not isolate the test, it deletes it. The minimal PATH was chosen
to stop a developer's machine from influencing the result; what it actually did
was guarantee the result on one whole class of machine.

## The decision

**Prove the interpreter runs before using it.** A name on PATH is not an
interpreter. The hook now walks `python3`, `python`, `py`, and runs `-c ''`
against each candidate, taking the first that exits 0. A stub fails that probe
in about a millisecond and is skipped.

**Tests for a guardrail inherit the real environment.** They strip only what
could mask a failure or fire an escape hatch the test did not ask for — the
opt-out variable, the project-directory override, the threshold — and leave PATH
alone. That is also the more faithful test: in real use the hook runs with the
session's own PATH.

**The stub case is now a test.** It writes a `python3` that exits 49, puts it
first on PATH, and asserts the hook still blocks. Verified to fail against the
pre-fix hook and pass against the fixed one — a regression test that was never
run against the bug is a comment.

## The pattern this belongs to

Three failures in this family are now on record here and in the owner's memory:
a scheduled task that pointed at a deleted interpreter and ran dead for six days
while the scheduler reported "Ready"; a nightly recorder that never launched the
program it was recording, dead 22 of 24 runs; and this one. Every one of them
was silent, every one of them looked healthy from the outside, and in every one
the detection existed and the notification did not.

The rule that falls out: **for anything that runs unattended, the interesting
assertion is not "did it do the right thing" but "was it able to run at all".**
Test the failure to start, not only the failure to decide.

# A failed run should say what it printed

*Decided 2026-08-29.*

`run_job.ps1` captured the agent's merged stdout and stderr, and then, when the
agent exited non-zero, wrote a run record saying:

> agent run failed with exit code 1

That is a symptom. The buffer holding the diagnosis was captured two dozen lines
earlier and discarded. The only surviving account of a failed run was a log file
under `%LOCALAPPDATA%` — **which no cloud session can read**, which is not
committed, and which is gone with the machine. `runs.jsonl`, the half that *is*
committed and *is* reviewable, carried the exit code and nothing else.

It cost a real diagnosis. On 2026-08-28 the 12:01 premarket run exited 1, never
re-ran, and Friday never got a gameplan. Whether that was a transient fault or a
systemic one was never established, because the evidence was somewhere nothing
could read it.

**Four consecutive alerts records specified this fix and none could make it.**
The agent may not edit its own log, and this is the code that writes it — so it
diagnosed the line, gave the line numbers, verified each run that they were still
accurate, and stopped. That is the guardrail working, not failing.

## What now goes in, and what deliberately does not

The record gets a **bounded tail** of the output, not the buffer:

- **Tail, not head.** A crash says why at the end.
- **600 characters, one line.** `runs.jsonl` is committed *precisely because*
  raw stdout under `logs/` is not — that split exists because a transcript is
  noise and can carry chart detail, and this fork is public. Putting the whole
  buffer in the tracked file would erase the distinction the split was made for.
- **The log's file name, not its path.** The name carries the timestamp that
  finds it; the path carries a home directory into a public repository.
- **Double quotes become single.** PowerShell 5.1 mangles a double quote passed
  as an argument to a native command, and a mangled argument loses the whole
  record rather than one character — this fix producing the exact silence it
  exists to remove.

**"The agent printed nothing at all" is its own sentence.** A process that
crashed with a message and a process that produced no output are different
faults, and this system has now gone wrong three times by making evidence and
the absence of evidence look identical. Collapsing them here would have been the
fourth.

## What is not proven

CI is Linux and never executes this file, so `tests/test_desk_agent_launcher.py`
pins the properties above by reading the source: the failure branch references
the captured buffer, the no-output case is distinct, the record names the leaf
and not the path, and the tail is bounded and flattened. Those checks fail
against the previous version and pass against this one.

They do not prove the PowerShell *runs*. Nothing available here can — there is
no PowerShell in the container, and the first real evidence will be the next
failed run's record. That is worth stating rather than glossing: this change
improves what a failure says about itself, and its own first test is a failure.

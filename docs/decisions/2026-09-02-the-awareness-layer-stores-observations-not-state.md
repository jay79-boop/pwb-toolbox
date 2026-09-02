# The awareness layer stores observations, not state

*Decided 2026-09-02.* Refines, and does not overturn,
[Retiring the Live Work Dashboard](2026-08-29-retiring-the-live-work-dashboard.md).

**Decision:** build the situational awareness layer the owner asked for, as an
assembler of timestamped *observations*. It never writes a file claiming to be
current state, and it never concludes.

## The collision, stated plainly

The owner asked for "a unified real-time state architecture" answering seven
questions at any moment. Read literally, that is the thing this repository
retired four days ago and whose record rejects a rebuild by name. `CLAUDE.md`,
under *The ledger*: open pull requests, their CI and what `main` points at live
**nowhere at all**, because "they were written down for months and were wrong
within hours every time."

Building it as asked would have reproduced a documented failure with fresh
paint on it. Refusing outright would have been wrong too, and the evidence for
that is in this repository: `docs/state.md` still lists pull requests #71–#111
under "Now (this week)". The repo is at #184. That section has been wrong for
weeks and nothing noticed — which is exactly the blindness the request was
describing.

So the request was right and its obvious implementation was the trap.

## What separates the two

One distinction carries the whole design:

> "At 14:03, the run log's newest record for `journal` was a failure" is true
> forever. "The current branch is X" is false in an hour.

An observation is a fact at a moment and is never revised. If the world
changes, a later observation disagrees with an earlier one, and **the
disagreement is the answer to "what is changing"** — the question that made
storage look unavoidable in the first place. Nothing needs to hold current
state to answer it.

The log is gitignored. Tracked, it would collide on every branch, which is the
shape the ledger split exists to prevent; and because the layer reads the
working tree, a tracked always-growing file made it report its own output as
uncommitted work on the very first run.

## It assembles evidence and stops

The owner chose delivery into the session catch-up and an on-demand command,
and declined a page and a push. That choice has a consequence worth recording,
because it removed a whole subsystem: **the answers are read inside a Claude
session, so the reasoner is already present and free.** "Why" and "what action
is safest" need no model calls, no API key and no prompt of their own. The tool
carries evidence — what proved each observation, and what it depends on — and
refuses to editorialise over it. That also keeps the core deterministic and
testable, which a judgement layer would not have been.

## Four false alarms on the first run, and the rule they produced

Run against the real `runs.jsonl`, the first version convicted five things. Two
were real. The other three, plus an overstated count, were plausible enough
that none would have been caught by reading the output:

1. `partial` counted as a failure — premarket's streak reported as 8, truth 3.
2. A job switched off on 2026-08-29 reported as having "failed its last run",
   describing a fortnight-old record as this morning's.
3. A blocker the run log explicitly recorded as *closed* convicted again,
   because recurrence was counted over all history.
4. A real blocker reported against a job nobody runs.

The rule that fell out: **recurrence is counted over history; liveness is read
from the head.** How long something has been going on is the part worth
knowing, but whether it is still happening can only be read from the newest
record.

All four are named regression tests. They matter more than the features: a
watchdog that cries wolf is not a degraded watchdog, it is an ignored one, and
this repository has already written down that false alarms are how alerting
dies.

## What it will not do

Nothing that moves money is ever proposed as an action — it is returned marked
for a person, with the reason. Same doctrine as `tools/ai_company.py`: agents
move information, people move money. Asserted by a test rather than trusted.

And it names its own blind spots on every run. Three of the four domains the
owner asked for have no adapter yet, and a domain with no adapter reads exactly
like a domain with nothing wrong.

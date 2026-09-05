# Doubling the hard stop, and a one-paste resume

*Decided 2026-09-05.*

The owner asked for two things about session limits, together: raise
`PWB_HARD_STOP_TOKENS` (he will tune it down again once he has a feel for
where a session is actually still worth carrying), and make picking the
work back up "easy, one click" once a session does end — whether it ends
because this repo's own hard stop fired, or because the account's five-hour
window rejected the session first.

## The threshold: 15M -> 30M

`.claude/hooks/session-hard-stop.sh`'s hardcoded fallback moves from
15,000,000 to 30,000,000 cache-read+cache-write tokens. Nothing else about
the hook changes: it still fails open, still denies everything except
git save-commands and `Write`/`Edit`, still has both escape hatches. This is
a starting point for a number the owner is going to walk down (or up) by
feel, not a claim that 30M is correct — `PWB_HARD_STOP_TOKENS` and
`.claude/.hard-stop-off` remain the two ways to change it without touching
this file.

One side effect worth naming: at the old 15M, the hard stop always fired
before `spend_watch.py`'s own "medium" (25M) and "high" (50M) session-size
tiers could ever be reached in a session running with defaults — the block
landed first. At 30M, the "medium" tier (25M) now fires *before* the block,
which is a genuine improvement: it gives the session one real warning, with
tools still working, instead of none.

## "One click" is actually "one paste" — and here is why

True one-click continuation — the session detects the limit and reopens
itself automatically — was asked about directly and turned down: the owner
picked "write state, then stop," which is also what the hard stop's own
message already said (`Do not spawn a subagent to continue`) and what
`docs/spend-safety.md`'s L4 layer argues for generally (unattended
continuation is exactly the shape of thing that should require a deliberate
human step). Nothing here reverses that.

What actually closes most of the gap, given that constraint: the *reason*
resuming a stopped session is expensive is that the person has to
reconstruct what was happening from scratch. If the stopping session leaves
one line behind — a `RESUME:` line in its last commit message or PR
description, stating exactly what is done and the next concrete step — a
fresh session's first message can just be "read the RESUME line on PR #N and
continue." That is one paste, not a re-explanation, which is the actual cost
this was trying to cut.

Both `session-hard-stop.sh`'s REASON text and `spend_watch.py`'s 25M-tier
advice now say to write that line, and say to write it whichever ending is
coming — the account's five-hour window can end a session with no warning
from this repo's own hooks at all, so the habit has to be "write it when you
sense either one coming," not "write it only when this hook fires." A
session that is part of the `agent-fleet` roster already gets this for free
(`docs/agent-fleet.md`: "a usage-limit stop is a fleet-wide outage... every
session rehydrates from the ledger and resumes"); this is the same idea
scoped down for a solo session that isn't on that roster.

## What this does not do

It does not make a stopped session reopen itself, and it does not build any
new automation to detect the five-hour window from inside a session — that
window is invisible to a running session by design (it just gets rejected).
For hands-off continuation across that specific limit, the existing
`agent-fleet` heartbeat is the mechanism that already exists for it; this
change is for the common case of one solo working session, not a
replacement for arming the fleet.

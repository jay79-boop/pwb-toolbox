# Retiring the Live Work Dashboard

*Decided 2026-08-29.* Supersedes
[Live Work Dashboard](2026-08-20-live-work-dashboard.md), which recorded it as
"Live. Auto-refreshes every 3 minutes."

**Decision:** Retire it. Do not rebuild it as a static page.

**It was never live.** The 08-20 entry recorded a status that was not true on
the day it was written, and no one checked. Three faults, any one of which is
fatal on its own:

- It fetched `CLAUDE.md` from the branch `claude/master-blueprint-review-n78rak`
  rather than `main`, so it froze the moment that pull request closed.
- Its fetch to `raw.githubusercontent.com` is blocked by the artifact CSP
  regardless of the branch, which is why the page renders as an empty shell.
- It parsed a `## Decision Log` heading that no longer exists — the log became
  one file per decision in this directory.

**Why retire rather than fix.** The job it was built for is already done twice
over, and better:

- The session-start hook opens every session with branch, working tree, open
  pull requests and their CI — **derived at read time**, so it cannot be stale.
- The Action Ledger carries everything waiting on the owner, and it persists
  across sessions.

A dashboard adds nothing either of those does not already do, and it adds a
second thing to keep true.

**Why the static-page rebuild was rejected specifically.** It was the obvious
cheap fix — a repo script rendering an HTML page — and it walks straight into
the rule this repository already has in writing. `CLAUDE.md`, under *The
ledger*: open pull requests, their CI, what `main` points at and any count of
them live **nowhere at all**, because "they were written down for months and
were wrong within hours every time." A static page is stale the moment it is
written. Rebuilding it that way would have reproduced the documented failure
with fresh paint on it.

**What this closes, and what it does not.** The retirement is this record. The
artifact page at `3aa6bcba-f677-4072-b247-0f999213b5a8` is **left exactly as
it is** — the Artifact tool has no delete action, so it cannot be removed from
here, and rewriting it as a tombstone was judged not worth its cost: the
publish contract requires reading the live version first, and that page is 901
lines for a note nobody would read. If the empty shell in the gallery ever
becomes confusing, rewriting it is a small job for a session that is already
cheap; it is tracked in the Action Ledger rather than left implied here.

**The general lesson, which is the part worth keeping.** A status line in a
decision record is a claim, and this one was wrong from the day it was
written. `docs/decisions/` is a record of what was believed at the time — so a
"Status: Live" line ages badly and nothing goes back to check it. Record the
decision and the reasoning; leave the running state to whatever derives it.

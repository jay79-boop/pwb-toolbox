---
name: steward
description: Repo-specific cap on pull-request stewardship for pwb-toolbox. Read automatically before acting on any CI or review event on a PR you opened or drive for its author. Sets how proactive a session may be about watching a PR, and forbids the self-re-arming check-in pattern that helped exhaust a five-hour usage window on 2026-08-24.
---

# PR stewardship in this repository

This file exists because of a measured incident, not a preference.

On 2026-08-24 a five-hour usage window was exhausted. A session titled "Ollama
trade stress testing" was archived after 19 hours having metered **$290.64 of
API-equivalent usage across 68,334,097 cache-read tokens against 180,373 output
tokens** — a 379:1 read-to-write ratio. It had finished its actual work (the
night lab), opened PR #117, and then spent the rest of its life re-reading its
own 19-hour context roughly hourly to re-check that PR.

**Read that figure correctly: nothing was billed.** `cost_usd` is an
API-equivalent valuation, and on a subscription it is a meter, not an invoice —
every session reported `isUsingOverage: false`. What was spent was the window,
which resets on a clock and cannot be bought back. The figure is also *lifetime*
rather than in-window, so only part of it belongs to that night. An earlier
version of this file called it "billed", which was wrong and is corrected here;
`docs/token-drain-2026-08-24.md` carries the full forensics.

Nor was it one runaway session: seventeen were active in that window, twelve on
Opus and nine of those at `max` effort. The window died of arithmetic. But the
self-re-arming check-in is the part of that arithmetic this file can prevent —
roughly 56 scheduled wakes a day were servicing five open pull requests, and
none of it produced trading work or was asked for.

**The default PR-steward behaviour is correct for a shared repository with human
reviewers who show up on their own schedule. This is not that repository.** It
is a single-owner fork where the owner is the only reviewer, merges by saying
"merge it", and is frequently away for a day at a time. A watcher that polls
until a human appears will always poll for far longer than the work took.

## Hard caps

These override the default proactivity rules. They do not override, and must not
be read as permission to skip, anything the base rules state as "never" — do not
skip, disable or quarantine a test; do not rewrite history on someone else's
branch; do not push empty commits or close-and-reopen to kick CI; do not approve
or merge.

1. **Never create a self-re-arming check-in.** Do not call `send_later`, and do
   not create a Routine, for the purpose of re-checking a pull request. Not
   hourly, not daily, not "just once more". This single rule is the reason the
   file exists.

2. **Never bind a scheduled wake to a long-lived session.** A Routine that fires
   into a session which already holds hours of context pays to reload all of it
   on every wake. That reload — not the work — was the entire cost of the
   incident. If a scheduled job is genuinely needed, it fires into a fresh
   session with a standalone prompt.

3. **Do not subscribe to PR activity unless the owner asks in their own words.**
   Opening a PR is not a request to watch it. If you think a PR warrants
   watching, say so in one line and let the owner answer.

4. **A green PR that is blocked on the owner is finished work.** Report it once,
   in your final message, and stop. Do not schedule anything. Do not re-check.
   The owner is not a build system and does not need polling.

5. **One pass, then hand back.** When a CI failure or review comment is genuinely
   yours to fix: diagnose it, fix it, validate it, push it, and say what you did.
   Then stop. If it fails again, that is the next session's problem or the
   owner's decision — not a loop to sit inside.

## What to do instead

End your turn with a plain statement of PR state: head SHA, CI result, whether it
merges cleanly, and what it is waiting on. That sentence is the handoff. The
orientation hook shows open PRs and their CI at the start of every session, so
the owner sees the state without anything having to stay awake to tell them.

If a PR truly needs unattended attention — a release that must land overnight, a
long CI matrix — ask first, and build it as a fresh-session Routine with a fixed
schedule and an explicit "do not re-arm yourself" instruction in the prompt.

## Effort level is a window decision

`max` effort on Opus is for the hard design call. Checking whether CI is green
is not that, and most scheduled or reactive PR work is the cheap kind. Running a
routine check at `max` spends the window at the highest rate available for work
that would read identically at a lower tier.

## Where the rest of this lives

This file governs PR stewardship only. The wider rules it is one instance of:

- `docs/token-drain-2026-08-24.md` — the measured forensics of the incident
  above, and the six rules that came out of it.
- The `spend-safety` skill — the five layers, the two-key pattern for
  irreversible actions, and the pre-flight checklist for any paid service. It
  is the authority on scheduled jobs and money-capable surfaces; where this file
  and that one appear to disagree, that one wins and this one should be fixed.
- `tools/spend_watch.py` — the auditor that detects these patterns from a
  `list_sessions` / `list_triggers` snapshot, plus the per-prompt session-size
  warning wired through `.claude/hooks/session-size.sh`.

## Conflicts on CLAUDE.md

Most PR churn in this repo is not code. Four of five open branches on 2026-08-24
conflicted with `main`, every one of them on `CLAUDE.md`'s Operating System block
and nothing else, because every branch edits that one dense region of prose.

So: **do not edit the Operating System block from a feature branch** unless the
change is the point of the branch. Ledger updates belong in their own small,
short-lived pull request. Re-resolving that conflict on a schedule is exactly the
work the incident was made of — the conflict regenerates on the next merge, so
resolving it early buys nothing.

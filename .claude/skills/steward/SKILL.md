---
name: steward
description: Repo-specific cap on pull-request stewardship for pwb-toolbox. Read automatically before acting on any CI or review event on a PR you opened or drive for its author. Sets how proactive a session may be about watching a PR, and forbids the self-re-arming check-in pattern that cost this account $290 in a single session on 2026-08-24.
---

# PR stewardship in this repository

This file exists because of a measured incident, not a preference.

On 2026-08-24 a session titled "Ollama trade stress testing" was archived after
19 hours with **$290.64 billed, 68,334,097 cache-read tokens, and 180,373 output
tokens** — a 379:1 read-to-write ratio. It had finished its actual work (the
night lab), opened PR #117, and then spent the rest of its life re-reading its
own 19-hour context roughly hourly to re-check that PR. A second session doing
the same on other PRs added $18.44. Across the account, ~56 scheduled wakes per
day were servicing five open pull requests.

None of that produced trading work, and none of it was asked for.

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

## Conflicts on CLAUDE.md

Most PR churn in this repo is not code. Four of five open branches on 2026-08-24
conflicted with `main`, every one of them on `CLAUDE.md`'s Operating System block
and nothing else, because every branch edits that one dense region of prose.

So: **do not edit the Operating System block from a feature branch** unless the
change is the point of the branch. Ledger updates belong in their own small,
short-lived pull request. Re-resolving that conflict on a schedule is exactly the
work the incident was made of — the conflict regenerates on the next merge, so
resolving it early buys nothing.

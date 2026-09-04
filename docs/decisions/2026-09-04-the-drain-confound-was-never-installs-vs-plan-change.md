# The drain confound was never installs vs. plan change

*Decided 2026-09-04.*

The drain investigation work order (written 09-04, from inside a session that
had hit its own hard stop and could only reach `git log`, and deleted with
this entry per its own instruction) asked one question
plainly and refused to let it go unanswered: *did the 08-31..09-03 drain come
from things installed in that window, or just from the 2026-09-02 move off the
5x plan shrinking the window itself, with per-session tokens flat underneath?*
It named four things to run to settle it. This entry runs them and reports
the numbers, because the two documents that already answered this — written
09-03, before the brief, and merged to `main` before the brief's own session
could see them — answer a related but different question.

**The answer is neither branch of the brief's question.** Per-session cost
climbed sharply and did not stay flat — so the plan-change explanation is
wrong. But the climb does not route through CLAUDE.md, the skills roster, or
MCP schemas either — so the install explanation is wrong too. It routes
through a fourth thing the brief's closed list did not include: how long an
individual session stays open.

## Step 3 first, because it is the one that settles the confound

`list_sessions`, grouped by `created_at` day, average `usage.cache_read_tokens`
per session that had reported usage yet:

| Day | Sessions | Avg cache reads/session | Day total | Day cost |
| --- | ---: | ---: | ---: | ---: |
| 08-25 | 9 | 2.5M | 7.5M | $4.22 |
| 08-26 | 10 | 3.9M | 7.7M | $6.65 |
| 08-27 | 6 | 3.1M | 3.1M | $3.05 |
| 08-28 | 8 | 34.7M | 34.7M | $76.13 |
| **08-29** | **20** | **22.0M** | **264.1M** | **$540.45** |
| 08-30 | 5 | 6.4M | 19.1M | $30.90 |
| 08-31 | 4 | 4.0M | 4.0M | $9.87 |
| 09-01 | 8 | 6.6M | 6.6M | $6.74 |
| **09-02** | **22** | **22.6M** | **338.3M** | **$409.85** |
| 09-03 | 6 | 13.2M | 39.5M | $50.98 |

(Small-sample days — one or two sessions reporting usage — are noisy; the
pattern is in the two 20+ session days.) Averages on 08-29 and 09-02 are
4-9x the surrounding days'. **That answers the brief's stated test directly:
if per-session tokens were flat, the plan change would explain everything.
They were not flat — so the plan change does not explain the drain, whatever
share it takes of *why the window felt smaller*.**

## But it doesn't route through an install either

Sorting the 08-29 and 09-02 sessions by cache reads puts one session above
every other by a wide margin:

| Session | Day | Cache reads | Cost |
| --- | --- | ---: | ---: |
| **Amplitude Analytics installation** | 09-02 | **133.0M** | **$166.86** |
| Random queue karaoke system | 08-29 | 40.1M | $111.87 |
| Free the desk agent jobs from TradingView Desktop | 08-29 | 37.3M | $47.11 |
| Ray | 08-29 | 36.4M | $75.91 |
| run_job.ps1 --add-dir and alerts | 08-29 | 32.7M | $70.95 |
| Obsidian vault sync setup | 08-29 | 29.9M | $34.95 |
| Skill/PR/Markdown comparison | 08-29 | 26.4M | $74.28 |
| Desk adapter and content credentials | 09-02 | 25.8M | $32.42 |
| Enterprise situational awareness layer | 09-02 | 25.6M | $24.68 |
| NVIDIA Cosmos installation and usage | 09-02 | 22.7M | $31.19 |

`Amplitude Analytics installation` alone is 133M of 09-02's 338M —
**39%** of one day from one session — and it is the same session
`2026-09-03-the-usage-panel-cannot-see-the-sessions-that-drained-the-window.md`
and `2026-09-03-the-cost-of-a-turn-is-set-before-the-turn-begins.md` already
named: created 09-02T06:34Z, still alive 09-03T07:26Z (25 hours), carrying
736K of a 1M context window, rejected by its own five-hour limit. Neither of
the two NVIDIA-install sessions on 09-02 (Cosmos 22.7M, skills 12.8M, vision
17.9M — three sessions, 53.4M combined) comes close, and the eleven merges
that day are the twenty-two-session count in the table above, not a separate
multiplier — session *count* is already priced in by summing the day.

**The mechanism is session age, not the three things the brief's own
framing restricted the answer to.** CLAUDE.md, the skills roster, and MCP
schemas set the *floor* every session pays on turn one — measured the same
day at ~84K tokens, per
`2026-09-04-the-floor-is-paid-before-the-first-word.md` — and that floor is
shared by every session equally, cheap ones included. What `Amplitude
Analytics installation` paid on top of that floor is a *session-specific*
multiplier: its own accumulated conversation, re-read in full before every
turn, growing the longer it stayed open. A brief that only asks "did
something get added to what every session pays" cannot see a cost that
lives in one session's own history instead.

## Steps 1 and 2, and why they close rather than open a lead

`python -m tools.desk_agent.runlog summary --last 40`: 43 records total,
none dated 09-02 or 09-03 — the desk agent's last logged activity before the
drain window was 09-01. It went quiet exactly when the drain hit, the
opposite of a cause; a `## 🔴 NEEDS YOU` about the Windows scheduled tasks
was already raised for this by the standing daily-check Routine and is
unrelated to this finding.

`list_triggers`: nine stored Routines, seven now disabled (per
`2026-09-04-the-floor-is-paid-before-the-first-word.md`, done at the owner's
instruction, not as a drain fix). The two that bind a persistent session —
`fleet-heartbeat-a` and `fleet-heartbeat-b` — both predate the window
(created 2026-08-24) and are the already-diagnosed and already-disabled
`docs/token-drain-2026-08-24.md` mechanism, not a new one. `spend_watch.py
audit` on the full snapshot confirms: it flags both by name, flags no
self-re-arming Routine, no duplicate, and no concurrency finding — the fat
and heavy sessions it lists are exactly the table above.

## The one-line answer

**What changed:** nothing added to CLAUDE.md, the skills roster, or MCP
schemas explains the 08-29/09-02 spikes — those are real but secondary (a
few percent each). One session, `Amplitude Analytics installation`, left
open 25 hours and switched to Opus 5, accounts for 39% of 09-02 alone and is
already fully diagnosed in the two 09-03 decision records. **Disconnecting
unused MCP connectors is still worth doing** — it is the largest remaining
lever on the floor every session pays, per
`2026-09-04-the-floor-is-paid-before-the-first-word.md` — but it would not
have prevented this specific drain, and it is a claude.ai settings change no
cloud session can make.

## Rule that follows

**A work order's own list of "the only things that can explain a per-turn
cost rise" is a hypothesis, not a closed set.** State it as one explicitly so
the next session tests it rather than searching only inside it — this one
would have stayed unanswered by CLAUDE.md/skills/MCP-schema arithmetic alone,
because the real driver was outside that list.

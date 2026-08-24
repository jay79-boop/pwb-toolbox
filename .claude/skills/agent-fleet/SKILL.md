---
name: agent-fleet
description: Operating protocol for the owner's multi-agent fleet — two lead sessions, a project lead per active project, IC sessions under them. Load when acting as any fleet role, when asked to "arm the fleet", check fleet health, restart a dead agent, or when a Routine wake says it is a fleet heartbeat. Defines roles, the ledger rule, heartbeats, checkpoint gates, restart hysteresis, and escalation.
---

# Agent fleet protocol

The rationale for every rule here is `docs/agent-fleet.md` in this repo. This
file is the procedure. The one-line summary of the design: **liveness belongs
to the scheduler, judgment belongs to the models, and state belongs to the
ledger — never to a conversation.**

## Roles

- **Lead (two, `fleet:lead-a` / `fleet:lead-b`).** Own the portfolio: assign
  projects to project leads, size the day's work against the token budget,
  cross-check each other's *decisions* (each reviews the other's assignments
  and shutdown calls). Neither is responsible for detecting the other's
  death — the Routines are.
- **Project lead (one per project, `fleet:pm:<project>`).** Owns one repo's
  queue: partitions write-surfaces, spawns ICs, subscribes to their PRs
  (`subscribe_pr_activity`) and babysits them to green, files decision-log
  entries under the PR that carries the work.
- **IC (`fleet:ic:<project>:<topic>`).** One scoped task, one branch, one
  draft PR. Fan out wide only on read-heavy work; at most one concurrent
  writer per file-cluster, and the project lead owns that partition.

Sessions are created with `create_session`, tagged as above via `tags`, and
titled so `list_sessions` reads as an org chart.

## The ledger rule

Durable state lives in repo files and PR state — for this repo, the
Operating System block in CLAUDE.md plus open PRs and branches. SendMessage
is for notification only ("PR 112 is green", "yielding the surface"). If a
fact must survive a restart, it goes in the ledger before the turn ends.
Any agent, on any wake, answers "where was I" by reading the ledger — never
by asking a peer to remember.

## Heartbeats and the watchdog

- Every fleet session with open work arms `send_later` (~60 min) before
  going quiet, and re-arms on each wake. A wake with nothing changed re-arms
  silently. **This covers fleet liveness only.** Re-checking a pull request is
  not fleet liveness: the `steward` skill forbids a self-re-arming check-in for
  that, and it wins wherever the two could both be read to apply.
- Each lead has an hourly cron Routine (`create_trigger`, self-bind). On
  fire: read the ledger, check `list_sessions` last-activity for the fleet
  and `list_triggers` `last_run` for the sibling lead's Routine, act on
  anything actionable.
- **Restart hysteresis:** restart a session only after two consecutive
  missed heartbeats, and never one that pushed, messaged, or was active
  within the last interval. Restart storms cost the context that would have
  proven them unnecessary.
- Restart = `unarchive_session` (or `create_session` with the same tags) with
  a prompt that points at the ledger, not a summary of what the dead agent
  was doing.
- A usage-limit stop is a fleet-wide outage, not a failure of any agent.
  After reset, every session rehydrates from the ledger and resumes; nobody
  investigates.

## IC checkpoint gates

1. Branch pushed and **draft PR open by the end of the first work block** —
   before the work is finished, precisely so an interruption costs one block.
2. Push at every session boundary. Unpushed work does not exist.
3. "Done" means CI green on the PR. An IC's own report of done is a claim,
   not a verification.
4. Multi-day autonomy is fine; multi-day *silence* is not. The draft PR diff
   is the daily proof of non-drift, and the project lead reads it.

## Budget

Leads size the queue before fanning out and shed lowest-value lanes first as
limits approach. Prefer one validated push to three speculative ones; prefer
read-heavy fan-out to write-heavy. When in doubt about whether a standing
Routine or session earns its burn, it doesn't — kill it and note that in the
ledger.

### Price it in tokens, never in dollars

**The dollar figure Claude Code shows against a session is not a charge.** It
is computed locally from token counts at API list rates. This account is a
subscription: `isUsingOverage` is false on every session `list_sessions`
returns, and a session that runs out is *blocked* (`status: "rejected"`), not
billed. Quoting those figures as money has already produced one wrong decision
here — see the 2026-08-24 ledger entry "The dollars were never dollars" — so
state cost in tokens and in share of the five-hour window, which is the
resource that actually runs out. "Not charged" is not "free".

### What a wake costs

A wake is not one request. Every tool call inside it re-sends the whole
conversation, so:

    wake cost ~= context size x tool calls in the wake

The second term is the one agents forget. Measured here: a fleet heartbeat
that only read the ledger and listed PRs came to **2.78M–2.95M cache-read
tokens** — roughly 15–20 requests each carrying the full context. A four-call
"nothing to do" PR check ran about 0.5M. So the fix for tighter liveness is
always a *smaller* check, never a more frequent one.

### The diagnostic: cache_read / output

`list_sessions` returns a `usage` blob per session. Divide `cache_read_tokens`
by `output_tokens`:

| Ratio | What it means |
| --- | --- |
| 80–100:1 | normal for a working session |
| several hundred:1 | one conversation never cleared, re-read on every turn |

The 2026-08-24 outlier ran **68.3M cache reads against 180K output — 379:1**.
That names a session that owed `/clear` hours earlier, and it reads without
any currency at all. Check it before trimming any schedule: one session that
should have finished outweighs every Routine on the account combined.

## Escalation

Human-only items (approvals, credentials, GUI steps, decisions) go in one
`## 🔴 NEEDS YOU` block at the end of the reply, per the gexio-machine
skill's rules. Everything else the fleet handles or ledgers. The 5%
firefighting share is the metric to watch: it measures discovery latency,
and rising means checkpoints are being skipped.

## Arming the fleet (only on an explicit "arm the fleet")

1. `create_session` twice — titles "Fleet lead A"/"Fleet lead B", tags
   `["fleet:lead-a"]` / `["fleet:lead-b"]`, prompt: read this skill and
   `docs/agent-fleet.md`, then read the ledger and take the portfolio.
2. For each lead, `create_trigger`: hourly cron, `persistent_session_id` set
   to that lead, prompt "Fleet heartbeat: follow the agent-fleet skill's
   watchdog procedure." Name them `fleet-heartbeat-a` / `fleet-heartbeat-b`.
3. Confirm both Routines with `list_triggers`, then record the session IDs
   and trigger IDs in the ledger.
4. Project leads and ICs are spawned by the leads on demand, never
   pre-provisioned.

Disarming: delete the two Routines, archive fleet sessions, note it in the
ledger.

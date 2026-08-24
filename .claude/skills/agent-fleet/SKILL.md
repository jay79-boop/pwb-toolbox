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
  silently.
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

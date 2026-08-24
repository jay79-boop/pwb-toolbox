# The agent fleet: critique and design

The owner's daily driver, as described on 2026-08-24: two lead agents that
keep each other accountable and restart the other if either fails, delegating
to tech-lead or PM agents across 8–10 concurrent projects, each project
running 5–10 IC agents. Human interaction is 30–50 prompts a day — roughly
60% with the leads, 35% with a project lead, 5% firefighting — and IC agents
work autonomously for 2–3 days at a stretch. Everything talks over
SendMessage.

This document is two things: an honest critique of that architecture, and a
design that maps the fixed version onto the primitives this account actually
has (sessions, Routines, SendMessage, PR subscriptions). The operational
protocol — who does what, when, and how a session rejoins after a restart —
lives in `.claude/skills/agent-fleet/SKILL.md`, so any session in this repo
loads it as procedure rather than reading prose.

## Critique

**What the structure gets right.** The attention split is the strong part:
60/35/5 is a sane span of control, and pushing the human's prompts to the
lead layer instead of the ICs is what makes 8–10 concurrent projects
possible at 30–50 prompts a day. Redundant leads are also the right instinct
— a single supervisor is a single point of failure.

**1. The two leads share fate and share blind spots.** Mutual restart
handles exactly one failure: a lead that is visibly dead. It does not handle
a lead that is alive, responsive, and wrong — looping, drifted, or confidently
reporting a state that isn't true — because the peer checking it runs the
same model on similar context and tends to agree with the same mistake.
And the failure mode that actually occurred is the one the pair cannot catch
by construction: a usage-limit outage stops *both* leads at the same instant.
This very document is the second attempt at its own task; the first died
mid-flight at a usage limit, and the watchdog that mattered was the human
noticing. Peer LLMs are the wrong liveness mechanism. **The restart
mechanism should be dumber than the thing it restarts** — a scheduler
checking a last-activity timestamp restarts more reliably than an agent
forming an opinion about its sibling. Keep two leads for judgment
redundancy; take liveness away from them entirely.

A second, quieter defect: two agents empowered to restart each other need
hysteresis. If each concludes the other is unhealthy in the same interval —
which correlated failures make likely — you get restart storms, and a
restart wipes exactly the context that would have shown it was unnecessary.

**2. 2–3 day autonomous IC runs price a wrong assumption at three days of
work.** Drift compounds silently: an IC that misreads the brief at hour two
produces three days of coherent, well-tested, wrong output. The reported 5%
off-the-rails rate is measured at *discovery*, and with multi-day autonomy
the discovery latency is the cost. The fix is not shorter leashes but
mandatory checkpoint artifacts: a pushed branch and a draft PR by the end of
the first work block, refreshed at every session boundary. That makes
divergence visible from the diff while it is still one day old, and it makes
the work restartable — the difference between this attempt and the last one
is precisely that nothing was pushed. An IC's claim of "done" is also not a
verification: done is CI green on the PR, nothing else.

**3. SendMessage-only communication makes state conversational, and
conversational state dies with the conversation.** When a lead restarts, its
inbox history restarts with it; the fresh instance knows only what its peer
summarizes, and relay summaries lose fidelity at every hop. This repo has
already paid for this lesson once — two sessions built adjacent tools
without noticing each other until the machine-read state block in CLAUDE.md
existed. The org chart needs a **ledger**: durable, shared state in repo
files and PR state, with messages demoted to notifications. A restarted
agent rehydrates from the ledger, not from a peer's memory. "Continue from
where you left off" should be answerable by reading files, because this
session couldn't answer it any other way.

**4. 5–10 ICs per project is probably past the coordination payoff.** Merge
conflicts and duplicated work scale with concurrent writers. This repo saw
the cost at N≈2–3: seven PRs went stale enough to need `main` merged back
in, two no longer merged at all, and #87/#90 nearly became the same tool
twice. Fan out wide on read-heavy work — research, review, sweeps — and
serialize writes per surface: one writer per file-cluster at a time, with
the project lead owning the partition.

**5. Nothing governs the budget.** The fleet's token burn is decoupled from
the human's 30–50 prompts, and the account has hard usage limits that, when
hit, stop every agent simultaneously (see point 1). A lead's job includes
being a budget governor: size the day's queue before fanning out, shed the
lowest-value lanes first as limits approach, and treat a limit-reset as a
normal rehydration event — every agent resumes from the ledger, none of them
ask what happened.

## Design: the same shape on this account's primitives

| Their concept | Implementation here |
| --- | --- |
| An agent | A Claude Code Remote session, tagged `fleet:<role>` |
| Agent-to-agent talk | SendMessage / `send_message` (notification, never ledger) |
| Watchdog | A cron **Routine** per lead: fires hourly, checks `list_sessions` last-activity and Routine `last_run`s, restarts what is actually dead |
| Self check-in | `send_later` armed before a session goes quiet on open work |
| IC verification | Draft PR + CI + `subscribe_pr_activity`; the PM babysits the PR, not the agent |
| The ledger | Each repo's durable state files (here: `CLAUDE.md`, `docs/state.md`, `docs/decisions/`) plus PR/branch state queried live |
| Escalation to human | The `## 🔴 NEEDS YOU` block, reserved for human-only items |

The load-bearing change from the current setup: **liveness moves to the
scheduler, judgment stays with the models.** Each lead has an hourly Routine
that wakes it regardless of what killed it — model error, container
reclaim, usage limit — because the trigger service is not made of the thing
that fails. The two leads still cross-check each other's *decisions* (the
valuable half of mutual accountability), but neither is the reason the
other is alive. Restart rules carry hysteresis: two consecutive missed
heartbeats before any restart, and never restart a session that pushed or
messaged within the last interval.

Restart is cheap by design, because rehydration is a ledger read: a
restarted lead reads the state files of its projects and `list_sessions`
for its fleet and is current; a restarted IC reads its own draft PR and
branch and loses at most one work block. The protocol details — role
definitions, heartbeat cadence, checkpoint rules, write partitions,
escalation format — are in the skill.

**Deliberately not armed by default.** Standing hourly Routines and
persistent lead sessions burn tokens around the clock, and this account hit
its usage limit twice in the last day. The skill ends with the arming
procedure; it runs when the owner says "arm the fleet" and not before.

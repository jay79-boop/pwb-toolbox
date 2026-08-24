# The AI & Automation Readiness Framework

A repeatable engagement pipeline: any business — a client, a side venture, or
your own operation — goes through the same twelve phases, in the same order,
and comes out the other side with an approved, scheduled, implemented
improvement to how it runs. AI agents (Claude sessions) do the work inside
each phase, and a lessons loop makes each engagement run better than the one
before it.

Three pieces make it real:

| Piece | What it is | Where |
|---|---|---|
| This playbook | What each phase means and when it is done | `docs/ai-readiness-framework.md` |
| The tracker | State, gates, decks, and the lessons retro | `tools/engagement.py` |
| The skill | Instructions that let a Claude session *run* a phase | `.claude/skills/engagement-flow/` |

Engagement data itself lives under `engagements/` and is **gitignored** — this
fork is public, and a client's tool inventory, process map, and findings are
confidential. Only the framework is committed; never the businesses going
through it.

## The flow

```
ANALYZE                       DECIDE                    DELIVER
1. Audit tools & systems      8. Stakeholder            11. Plan & schedule
2. Map current process           presentation               implementation
3. Identify bottlenecks       9. Revise proposal        12. Go live & update
4. Assess AI & automation        (optional)                 workspace
   readiness                 10. Stakeholder approval
5. Prioritize improvements       ── hard gate ──
6. Implement quick wins
7. Design target process
```

Phases run strictly in order. `tools/engagement.py advance` is the only way
to move forward, and it refuses when a phase's exit gate is not met — so the
tracker's state is always an honest statement of where an engagement stands,
which is what lets a fresh session pick one up cold.

## The phases

Each phase has a **deliverable** (a markdown file in the engagement folder)
and an exit gate. The deliverable is the phase: if it is not written down, it
did not happen.

### Analyze

1. **Audit tools & systems** (`01-audit.md`) — inventory every tool, system,
   data store and subscription: who uses it, what it costs, what it talks to.
   The audit is a census, not a critique — judging comes later.
2. **Map current process** (`02-process-map.md`) — the end-to-end process as
   it *actually* runs: steps, actors, handoffs, inputs/outputs, rough time per
   step. Map the real behavior, not the org chart's version of it.
3. **Identify bottlenecks** (`03-bottlenecks.md`) — rank constraints by what
   they cost, with evidence from the map. A bottleneck without a number
   attached is an anecdote.
4. **Assess AI & automation readiness** (`04-readiness.md`) — score each
   process step for automatability: data availability, repetitiveness, error
   tolerance, and where a human must stay in the loop. This is where "could an
   agent do this" gets answered per step instead of in the abstract.
5. **Prioritize improvements** (`05-priorities.md`) — rank impact versus
   effort; name the quick wins explicitly.
6. **Implement quick wins** (`06-quick-wins.md`) — ship the low-risk,
   high-confidence items *now*, before the presentation, and measure
   before/after. Arriving at the stakeholder meeting with results already in
   hand is what buys trust for the bigger proposal. Anything that is risky,
   expensive, or hard to reverse is not a quick win — it waits for approval.
7. **Design target process** (`07-target-design.md`) — specify the future
   process: what runs automatically, what an AI agent does, what stays human,
   and the controls around each.

   There is a **reference target architecture** to start from rather than
   rediscovering the shape of the answer per business:
   `docs/one-person-ai-company.md`, with the whole thing as a blueprint in
   `docs/blueprint-one-person-ai-company.json`. It is a local service business
   run as a loop — marketing, intake, sales, operations, finance, and the
   collected cash setting next week's ads — with agents on the information and
   people on money and risk. `python tools/engagement.py seed-target <slug>`
   stamps it into the engagement with every section turned into a question
   about *this* business.

   The seeded file carries an `UNEDITED REFERENCE` line and `advance` refuses
   while it is there. A phase whose gate is "the deliverable exists" is
   trivially passed by a deliverable written for you; seeding is a convenience
   for the shape, never for the content.

   Two checks this phase owes the design, both of which can fail:
   `python tools/ai_company.py gates --blueprint <target>` convicts any AI step
   that commits money with no person in front of it, and
   `python tools/ai_company.py hours --blueprint <target> --baseline <current>`
   turns phase 2's map and this one into a before-and-after number rather than
   an adjective.

### Decide

8. **Stakeholder presentation** (`08-feedback.md`) — build the deck
   (`python tools/engagement.py deck <slug>` renders `deck.html` from the
   deliverables — self-contained, opens from `file://`, no network), present
   it, and capture the feedback verbatim. The gate requires both the deck and
   the feedback file.
9. **Revise proposal** (`09-revision.md`, *optional*) — amend the proposal to
   answer the feedback. The only skippable phase (`advance --skip`), for when
   the feedback was "looks good".
10. **Stakeholder approval** — the hard gate. `advance --approved-by NAME`
    records who approved and when. Nothing beyond quick wins gets implemented
    without this line in the record.

### Deliver

11. **Plan & schedule implementation** (`10-implementation-plan.md`) —
    milestones, owners, dates, and a rollback path for everything approved.
12. **Go live & update workspace** (`11-golive.md`) — the new process runs,
    the workspace and docs are updated to reflect the live state (no document
    left describing the old process as current), and a metrics baseline is
    recorded so the next engagement can measure from here.

## The evolution loop

The framework improves the same way the businesses do — deliberately, with
evidence:

- **During a phase**, record lessons as they surface:
  `python tools/engagement.py note <slug> "ask for vendor invoices up front"`.
- **Before starting a phase**, read what every previous engagement learned
  about it: `python tools/engagement.py retro --phase audit`.
- **When a lesson generalizes** — it would apply to any business, not just
  this one — promote it out of the notes and into the committed framework:
  the phase instructions in `.claude/skills/engagement-flow/SKILL.md`, or this
  playbook. That is the "evolving AI agents" part made concrete: the agent
  instructions are version-controlled, and lessons flow uphill into them.

Notes stay in the gitignored engagement folder until promoted; promote the
*lesson*, never the client detail it came from.

## Running one

```bash
python tools/engagement.py new "Acme Logistics"   # open an engagement
python tools/engagement.py status acme-logistics  # where are we, what unblocks it
python tools/engagement.py advance acme-logistics # complete the current phase
python tools/engagement.py deck acme-logistics    # render the stakeholder deck
python tools/engagement.py list                   # every engagement at a glance
python tools/engagement.py retro                  # lessons, grouped by phase
python tools/engagement.py seed-target acme-logistics  # start phase 7 from the reference
```

Or just tell a Claude session in this repo to *"start a readiness engagement
for Acme"* — the `engagement-flow` skill drives the same commands and does the
analysis work in each phase.

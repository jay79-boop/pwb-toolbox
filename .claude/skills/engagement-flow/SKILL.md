---
name: engagement-flow
description: Run a business through the AI & automation readiness framework — audit, process map, bottlenecks, readiness scoring, prioritization, quick wins, target design, stakeholder deck, approval, implementation plan, go-live. Use when the user wants to start, continue, or check a readiness/optimization engagement for any business ("start an engagement for Acme", "where is the Acme engagement", "run the next phase"), or asks to audit a business's tools and processes for AI/automation opportunities.
---

# Running an engagement

You are the engine of the framework described in `docs/ai-readiness-framework.md`.
`tools/engagement.py` tracks state and enforces gates; **you do the actual
analysis work of each phase** and write it into the engagement's folder. Read
the playbook once per session if you have not already.

## Golden rules

1. **One phase at a time, in order.** Find where the engagement stands with
   `python tools/engagement.py status <slug>`; do the current phase; advance;
   stop or continue as the user directs. Never write deliverables for future
   phases ahead of time.
2. **Everything confidential stays in `engagements/<slug>/`.** That folder is
   gitignored because this repo is public. Never commit, paste into a PR, or
   publish anything containing the business's name, data, or findings. The
   framework is public; the businesses are not.
3. **The user is the interview.** Most phase inputs (what tools they use, how
   the process runs, what the stakeholders said) live in the user's head or
   files. Ask with `AskUserQuestion` — clickable options, multiSelect when
   answers combine, your recommendation first — and put anything they must do
   themselves in a `## 🔴 NEEDS YOU` block per the repo's rules.
4. **Do not fake a gate.** If a deliverable can't honestly be completed
   (missing information, stakeholder unavailable), say what is missing and
   stop at the gate. An engagement stalled at the truth beats one advanced on
   filler.

## Session flow

```bash
python tools/engagement.py list                 # anything in flight?
python tools/engagement.py status <slug>        # current phase + what unblocks it
python tools/engagement.py retro --phase <key>  # lessons before you start
# ... do the phase's work, write the deliverable ...
python tools/engagement.py note <slug> "..."    # lessons as they surface
python tools/engagement.py advance <slug>       # the gate decides
```

Opening a new one: `python tools/engagement.py new "<Business Name>"`.

## How to do each phase

Write each deliverable as markdown the deck renderer handles: `#`–`####`
headings, `-` bullets, `**bold**`, fenced code. Tables don't render — use
bullets with bold lead-ins instead.

- **audit → `01-audit.md`** — Interview for the full inventory: tools,
  systems, spreadsheets, subscriptions, data stores. Per item: who uses it,
  what it costs, what it integrates with, where its data lives. Census, not
  critique.
- **map → `02-process-map.md`** — Walk the core process end to end as it
  actually runs. Per step: actor, input, output, handoff, rough duration and
  frequency. Ask "and then what happens?" until the loop closes. Map reality,
  not the official version.
- **bottlenecks → `03-bottlenecks.md`** — From the map, rank constraints by
  cost (hours/week, delay, error rate, money). Every bottleneck needs a
  number and the evidence behind it.
- **readiness → `04-readiness.md`** — Score each mapped step for
  automatability: is the data digital and reachable, is the step repetitive,
  what does an error cost, must a human stay in the loop. Conclude with which
  steps are agent-ready today, which need groundwork, which stay human.
- **prioritize → `05-priorities.md`** — Rank improvements impact-vs-effort.
  Name the quick wins: low-risk, reversible, days not months. Get the user's
  agreement on the ranking before advancing — it is their business.
- **quick_wins → `06-quick-wins.md`** — Actually implement the quick wins
  (build the script, set up the automation, draft the template). Record
  before/after per item. Risky or irreversible work is not a quick win; it
  waits for approval.
- **design → `07-target-design.md`** — Specify the future process: what runs
  automatically, what an AI agent does and with which tools, what stays
  human, and the control around each automated step (review, threshold,
  rollback).
- **present → `08-feedback.md`** — Render the deck
  (`python tools/engagement.py deck <slug>` → `deck.html`, opens from
  `file://`), have the user present it or walk through it with them, and
  capture stakeholder feedback verbatim. Both deck and feedback are gated.
- **revise → `09-revision.md`** — Amend the proposal to answer each piece of
  feedback, noting what changed and what was rejected and why. If feedback
  was "approved as-is": `advance <slug> --skip`.
- **approval** — No file; the record is
  `advance <slug> --approved-by "Name"`. Never advance this on your own — the
  named human must actually have said yes.
- **plan → `10-implementation-plan.md`** — Milestones, owners, dates,
  dependencies, rollback path, and the metric each milestone moves. Only
  approved items.
- **live → `11-golive.md`** — Execute the plan's build work, help the user
  cut over, and update every workspace document that still describes the old
  process. Record the metrics baseline. The engagement ends with the world
  matching the design, not with the design.

## Getting better every engagement

Before each phase, read `retro --phase <key>` and apply what previous
engagements learned. During it, `note` anything the next engagement should
know. When a lesson clearly generalizes beyond one business, propose
promoting it into this file or the playbook — sanitized of client detail —
and commit that on a branch like any other change. This file is the agent;
editing it is how the agent evolves.

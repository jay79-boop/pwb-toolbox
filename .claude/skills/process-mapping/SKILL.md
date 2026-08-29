---
name: process-mapping
description: The craft standard for drawing a business process as a map — branch grammar (loop-back, divergence, converge), labeled connectors, go-to steps for long loop-backs, verb-first step titles, Person/Automation/AI-agent executors, duration x frequency costing, and mini-SOP notes. Use whenever a workflow has to become a diagram or a structured process file: building or editing a map in static/flow-canvas.html, writing the process steps of a business blueprint (docs/blueprint-schema.json), doing the map or target-design phase of an engagement, or any request to "map this process", "document this workflow", "draw how this works", or "build out this process".
---

# Mapping a process

A map is finished when a reader who was not in the room can run the process
from it. That is the bar. Everything below serves it.

Two rules govern the rest:

1. **Complete in one pass.** Every step carries its full set of inputs, every
   branch lands somewhere, every connector reads cleanly. Never hand over a
   map the user has to patch with "you forgot this, and this."
2. **One intake round.** Whatever the request does not tell you, gather in a
   single consolidated round of `AskUserQuestion` before drawing — see the
   repo's `CLAUDE.md`. Research first so the questions read "confirm or
   correct this", not "tell me everything". Never drip questions across turns.

## Which tool

- **`static/flow-canvas.html`** — the map itself. Drag-and-connect cards,
  branches, labels, go-to steps, status/owner coloring, monthly load rollup.
  Opens from `file://`, saves to localStorage, exports JSON. This is where a
  process gets *drawn*.
- **`docs/blueprint-schema.json`** — the structured record of a whole
  business: departments, processes with their steps and branches, tools,
  changes, roadmap. `static/blueprint-builder.html` edits one,
  `static/blueprint-dashboard.html` shows one, and flow-canvas imports one.
  This is where a process gets *stored*.
- **An engagement deliverable** (`engagements/<slug>/02-process-map.md`,
  `07-target-design.md`) — the written map for a client, produced by the
  `engagement-flow` skill. Prose, but the grammar below still applies: a
  written map with an unresolved branch is as broken as a drawn one.

A blueprint process imports into flow-canvas directly, so build once and
render, rather than keeping two hand-maintained copies of one process.

## Start at the trigger

The first step is what *kicks the process off* — a form submission, an
inbound email, a date, a threshold crossed, a signed contract. A map whose
first step is mid-action ("Review the request") hides where the work comes
from, and that is usually where the delay lives.

If the trigger is not stated, it is an intake question. Always.

## Titling steps

Verb-first, roughly four to eight words. "Pull active-user rows from
Postgres", not "Postgres". "Reconcile invoices against closed deals", not
"Reconciliation". Someone scanning the map should follow the whole flow from
titles alone; the note carries the detail.

## Executor — who or what runs it

Every step is one of three:

- **Person** — a human does it by hand. Carries a duration and a frequency.
- **Automation** — software runs it on its own.
- **AI agent** — an agent runs it.

flow-canvas calls this the step's owner (`person` / `auto` / `ai`). It is the
field most often left at its default, and it is the one that decides what the
map is *for*: the Person steps are the labour bill, and the Automation steps
are what somebody already fixed.

## Every branch lands somewhere

Wherever the flow forks, the fork is its own step, marked as a decision and
named as the question it answers: "Did eligibility pass?", "Is the invoice
correct?". Never bury a fork in a note, and never collapse two outcomes into
one card.

Then it is exactly one of three patterns. Naming which one you are using is
the single thing that separates a map from a sketch:

1. **Loop-back** — the rework path. "Not approved" goes to a fix step, and
   the fix step returns to the step that gets redone. No dead-end "fix it"
   card with no way back into the flow.
2. **Full divergence** — the paths genuinely never rejoin. Each runs through
   to its own ending, and each ending is a real terminal step, not an arrow
   into empty space.
3. **Diverge then converge** — each path does its own two or three steps,
   then both land on the *same* downstream card and the process continues as
   one line. Wire both tails into that one shared step; do not duplicate it
   per branch.

**Label every connector leaving a decision.** One to three words, one is
ideal: "Yes" / "No", "Approved" / "Rejected", "Over $10k" / "Under $10k".
Labels are what make a fork readable without opening a single card.

## Long loop-backs become go-to steps

A loop-back to a step **more than three steps upstream** is not drawn as an
arrow. A long backward wire crosses everything between here and there and
turns the map into spaghetti.

Instead, the branch ends in a **go-to step**: a card of its own that names
its destination and stops there. In flow-canvas, set the step's kind to
"Go to" and pick the target from the list; the card then reads
`Go to: Submit intake form`, and selecting it highlights the target so the
jump is visible without a wire. The note says why the flow returns there and
what sent it back.

Count steps along the main line, not through branches. Within three, a normal
connector reads fine and a go-to step is overkill. Beyond three, always the
go-to step.

## Waits are steps, but only real ones

An explicit wait — "then we wait two days for legal", a shipping window, a
Monday batch — is a step of its own, because wait time is usually where the
real elapsed cost hides and it is invisible if you leave it as a gap between
two cards.

**Do not invent them.** A handoff is not a wait. Only model a wait the user
actually described, or one you asked about and they confirmed. A map padded
with imagined delays is worse than one with none, because now the numbers
are fiction.

## Duration x frequency — what turns a map into a number

Every Person step gets a **duration** (how long one run takes) and a
**frequency** (how many times a month it runs). Together they are the whole
argument: eleven minutes is nothing, eleven minutes 90 times a month is two
days of someone's life every month.

Estimate rather than asking cold — a quick form fill is one to two minutes, a
judgment call three to five, a written review fifteen to thirty — and put the
estimates in the intake round to confirm. Never leave a Person step with
neither number: an unpriced step is a hole in the total, and flow-canvas will
say so in the panel.

Automation and AI steps carry a frequency too when it is known, but they are
not labour and do not belong in the load figure.

## Notes are mini-SOPs

Every step's note takes the same four parts, so a reader knows how the step
is set up and why:

> **Purpose** — why this step exists, and why it is done this way.
> **Inputs** — what it needs before it can start.
> **Procedure** — the actual how, in order, naming who does it.
> **Output** — what it produces and hands to the next step.

Write plainly. No "seamless", "robust", "leverage", "streamline". Anyone
reading the note should be able to run the step.

## Status

Steps carry a status: **Live** for what runs in production today, and
**Draft** / **Working** / **Testing** for what does not yet. A map of how
things *currently* run is Live throughout. A proposed future process is not
allowed to claim Live — that is the difference between a record and a
pitch, and it is the whole point of mapping current state before target
state.

## Before you call it done

Per step:

- [ ] Verb-first title, four to eight words
- [ ] Executor set deliberately (Person / Automation / AI agent)
- [ ] Status honest about Live vs proposed
- [ ] Duration and frequency on every Person step
- [ ] A mini-SOP note (Purpose / Inputs / Procedure / Output)
- [ ] Every fork marked as a decision step and named as a question

Per map:

- [ ] The first step is the trigger
- [ ] Every branch resolves: loop-back, full divergence, or diverge-converge
- [ ] Terminal branches end in a real terminal step, never a dangling arrow
- [ ] Converging branches land on one shared step, not a duplicated pair
- [ ] Every connector out of a decision is labeled, one to three words
- [ ] Every loop-back farther than three steps is a go-to step
- [ ] Explicitly described waits are modeled; no invented ones
- [ ] The monthly load figure has no unpriced Person steps behind it
- [ ] All of the above settled in at most ONE intake round

## Where this came from

Adapted from a Puzzle (puzzleapp.io) process-building skill, stripped of that
tool's MCP mechanics. That original was kept alongside this file for a week and
retired on 2026-08-29 — the Puzzle MCP is not connected here, so its trigger
could never fire, and it was costing description budget on every turn to say so.
It is recoverable in full:

    git show 0b168fd:.claude/skills/build-puzzle-process/SKILL.md

Its RACI model and per-step data-attribute model were deliberately left out of
this skill: they are built for org charts with hourly rates and a data
dictionary behind them, and are ceremony for a one-person shop. If an
engagement ever needs them for a client's team, take them from that commit
rather than reinventing them.

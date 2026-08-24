---
name: build-puzzle-process
description: Build processes inside a Puzzle (puzzleapp.io) workspace through the Puzzle MCP — sections, steps, connections, roles, tools, entities and attributes, and the changelog. ONLY load this when the Puzzle MCP is actually connected and the target is a Puzzle workspace; it is the original vendored reference for that tool's mechanics. For mapping a process anywhere else — flow-canvas, a business blueprint, an engagement deliverable — use the process-mapping skill instead, which carries the same craft without the Puzzle calls.
---

# Build a Puzzle Process

This skill captures how to build an excellent, thorough Puzzle process. It uses every corner of the tool: the workflow canvas, the team canvas, the tools canvas and data model, step costing, and the changelog. Follow it in order.

Two rules govern everything else:

1. **Complete in one shot.** A process is not "built" until every step carries its full set of inputs (type, status, executor, roles, tools, attributes, cost, notes), every branch resolves somewhere, every connector reads cleanly, new roles and tools exist on their canvases, and the changelog records what happened. Never deliver a map the user has to come back and patch with "you forgot this, you forgot this."
2. **One intake round, ever.** Whatever the prompt doesn't tell you, collect in a single consolidated round of questions before building. Never drip-feed questions across multiple turns.

---

## The build order (do not skip steps)

1. **Orient before you touch anything.** Call `list_tabs` to confirm the tab ID. Then `list_sections` (with `tab_id` and `include: ['step_count']`) to check for an existing section so you don't duplicate or fragment. If editing an existing process, `list_steps` to capture current step IDs and connection IDs before changing anything.
2. **Resolve roles, tools, and data up front.** Roles come from `list_teams` with `include: ['roles.id', 'roles.name', 'roles.hourly_rate']`. Tools come from `list_tools` — query a single comma-separated string of every tool name at once (e.g. `"Attio, QuickBooks, Intercom, monday.com, Claude"`) rather than one call per tool. Check `list_entities` and `list_attributes` for the data objects the process touches. This is how you discover what's missing before you ask about it.
3. **Draft the full process privately.** Work through every step, branch, and input against the Completeness Checklist below. Every gap you find becomes an intake question.
4. **Run the single intake round.** One consolidated set of questions covering every missing input (see "The single intake round"). Do the research first (e.g. hourly rates, proposed attributes) so most questions are confirm/adjust rather than open-ended.
5. **Propose the full process in chat.** Every step fully specified: name, type, status, executor, roles, tools, attributes, frequency, duration, and a one-line note, plus orientation, branch wiring, connector handles, and labels. Let the user evaluate the structure before anything is written to Puzzle.
6. **Create missing canvas objects first.** New roles (with hourly rates) on the Team Canvas, new tools on the Tools Canvas, new entities/attributes in the data model. These must exist before the workflow build so IDs are available for linking.
7. **Build in one call.** Use `create_process` with the full `sections`, `steps`, and `connections` arrays in a single call, wiring connections via `temp_id` references, with `label`, `source_handle`, and `target_handle` set on every connection that needs them. Include `role_ids`, `tool_ids`, `duration`, `frequency`, `status`, `executor`, `type`, and `notes` directly on each step. Capture the returned step IDs.
8. **Link what create_process can't carry.** Attributes always link post-build via `link_to_steps` with `type: attribute`. Approver, consulted, and informed roles also link post-build via `link_to_steps` with `type: role` and the right `involvement` (the `role_ids` array in `create_process` covers responsible only). Verify roles and tools landed; patch any misses with `link_to_steps`.
9. **Verify.** Re-run `list_steps` with includes for roles, tools, attributes, duration, frequency, notes, and connections. Fix any connector that reads wrong (handles, labels) via `update_workflow`.
10. **Memorialize in the changelog.** Always. See "Changelog" below.

---

## The Completeness Checklist — what "fully built out" means

Every one of these must be resolved from the prompt, from workspace lookups, or from the single intake round. There is no fourth option.

Per step:

- Name (verb-first, roughly four to eight words)
- Type (from the step type enum)
- Status (Draft / In_Progress / Testing / Live / Archived)
- Executor (Person / Automation / AI Agent)
- RACI roles: responsible on every Person step (with hourly rate known), plus approver / consulted / informed where they genuinely apply
- Tool(s) the step touches
- Attributes: the fields read or written in that tool at that step
- Cost inputs on manual steps: duration in minutes + frequency per month
- A mini-SOP note

Per process:

- One section
- Trigger known (ask if not stated) — the first step of the map is what kicks the process off
- Orientation (ask if not stated)
- Named people mapped to roles (ask if a person's role is unknown)
- Explicit waits modeled as delay steps (and only explicit ones)
- Every conditional branch resolves somewhere (loop back, diverge, or converge)
- Loop-backs farther than three steps modeled as `go_to` steps, not connectors
- Every post-conditional connector labeled
- Connector handles chosen so nothing zigzags
- Changelog entry written

---

## The single intake round

After orienting and drafting, gather **everything** missing in one consolidated round — a single message (or one structured multiple-choice prompt if your platform supports it), grouped so it's fast to answer:

- **Trigger** — what kicks this process off? Always ask if unstated. The answer becomes the first step of the map: a form submission, an inbound email, a calendar date, a signed contract, a threshold being crossed. A process whose first step is mid-action ("Review the request") with no visible trigger is incomplete.
- **Orientation** — horizontal (left to right) or vertical (top to bottom)? Always ask if the user didn't specify. Recommend based on shape (see Orientation).
- **Missing step facts** — executor, tool, role, or status for any step where the prompt didn't say.
- **Costing** — duration and monthly frequency for each manual step that lacks them. Offer researched estimates to confirm rather than asking cold.
- **New roles** — for any role mentioned that isn't on the Team Canvas: present the role name and a researched average hourly rate for confirmation before creating it.
- **RACI** — present the inferred approver / consulted / informed assignments (from hierarchy and surrounding-step ownership) as a compact table for confirmation, alongside the responsible roles.
- **People** — for any actual person named in the prompt ("then Sarah reviews it"): ask what role that person holds if it isn't stated and can't be resolved from the People Canvas. Steps are owned by roles, so every named person must map to a role.
- **Attributes** — for steps whose tools have no matching entities/attributes: present a proposed entity + attribute list per step for approval before adding to the data model.
- **Branch logic** — any fork where the prompt is ambiguous about whether the paths rejoin, loop back, or diverge for good.

Do the legwork before asking: research rates, draft the attribute model, estimate durations. The intake should read as "confirm or correct this," not "tell me everything." One round. If an answer creates one genuinely unforeseeable follow-up, fold it into the proposal review rather than opening a second interrogation.

---

## One section per process — always

A process lives in **one section**. Do not split a single process across multiple sections. Left unattended, AI builders get "section-happy" and fragment one process into several, which breaks the visual story and the costing rollup. One section, every step inside it. This is non-negotiable unless the user explicitly asks for multiple.

---

## Orientation — ask, then set deliberately

If the user hasn't said how the map should read, **ask in the intake round**: horizontal (left to right) or vertical (top to bottom). Recommend based on shape:

- **left_to_right** — the default for linear, sequential, pipeline-style processes. Most processes.
- **top_to_bottom** — better for decision-heavy processes with multiple conditionals, where vertical reads more like a flowchart.

Set it on `create_process` via `direction`.

### The hybrid layout — automations branch downward

In a left-to-right process, the x-axis reads as **time**. When a single manual step kicks off a cluster of automations, those automation steps should branch **down** from the manual step rather than stretching the main line to the right. The reader then sees, at that point in time, one manual action fanning into its automated consequences, while the main flow continues left to right.

Wire it: manual step → each automation (or the first of an automation chain) with `source_handle: bottom` on the manual step and `target_handle: top` on the automation. Chains of automations continue downward top-to-bottom. If the flow resumes after the automations, connect back up into the next main-line step.

---

## Conditionals — every branch must land somewhere

Wherever the process forks, model it as a **conditional step** (type `conditional`), named as the question it answers: "Did eligibility pass?", "Is the invoice correct?". Then identify which of the three branch patterns applies — this is the single most common thing sloppy builds get wrong:

1. **Loop-back (the rework path).** One branch is a fix/follow-up that returns to earlier work — e.g. "Not approved → revise and resubmit." The follow-up step's outgoing connection goes **back to the step before the conditional** (or whichever earlier step gets redone). No dangling arrows, no dead-end "fix it" boxes. Distance rule: a connector is only right when the target is **three steps back or fewer** — any farther and it becomes a `go_to` step instead (see "Go-to steps").
2. **Full divergence.** The two paths genuinely never recombine — each branch gets its own sequence of steps through to its own end. Terminal branches get a real terminator step (no role or tool; it just ends the flow), never a dangling arrow.
3. **Diverge-then-converge.** Each branch has two or three unique steps, then both paths land on the **same** downstream step and the process continues as one line. Wire both branch tails into that shared step — do not duplicate the shared step per branch.

Map every branch and platform handoff as its own discrete step. Never collapse two outcomes into one box, and never bury a fork in a note.

### Label every branch

Every connection leaving a conditional gets a `label`: **one to three words, one is ideal**. "Yes" / "No", "Approved" / "Rejected", "Over $10k" / "Under $10k". Labels are what make a branch readable without opening a single step.

---

## Connectors — handles that read cleanly

Connectors carry the visual grammar of the map. Set `source_handle` and `target_handle` (top / bottom / left / right) deliberately so arrows flow instead of zigzagging:

- **Main-line flow** in a left-to-right map: right → left (usually the default; only override when needed).
- **Downward branches** (automation fans, lower conditional branches): leave from the **bottom** of the source and enter the **top** of the target. Never bottom-to-side or side-to-top on a vertical drop — that's what makes the zigzag.
- **Loop-backs from below** (three steps back or fewer): when a branch sits below the main line and returns to an earlier step, the connector leaves from the **bottom** (or right) of the follow-up step and enters the **bottom** of the step it returns to, sweeping under the flow in its direction of travel.
- **Loop-backs from above** (three steps back or fewer): when the returning branch sits above the main line, mirror it — leave from the **top** and enter the **top** of the target, arcing over the flow.
- **Loop-backs farther than three steps**: never a connector at all — use a `go_to` step (see "Go-to steps"). A connector dragged across half the map crosses everything in its path and wrecks readability.

The test: trace each arrow with your eye. If it doubles back through the step it left, crosses the main line for no reason, or hooks around a corner to reach its handle, change the handles. After building, verify visually-critical connections landed right and fix any via `update_workflow` (`connections` array: `id`, `source_handle`, `target_handle`, `label`).

---

## Go-to steps — long loop-backs without the spaghetti

When a step deep in the process loops back to a step **more than three steps upstream**, do not draw a connector — a long backward arrow stretches across the map, crosses everything in its path, and turns the diagram into spaghetti. Use the `go_to` step type instead. This rule applies to **every** loop-back farther than three steps, whatever caused it: a rework branch off a conditional, a retry, a restart, an escalation that re-enters earlier in the flow.

How to build one:

1. **Create the go-to step as an extension of the looping step** — a new step of type `go_to`, connected forward from the step where the loop happens (normal short connector: right→left on a horizontal map, bottom→top on a vertical one).
2. **Name it after its destination**: "Go to: [exact name of the target step]" — e.g. "Go to: Submit intake form". The name is how the map reads without any arrow.
3. **Spell out the destination in the note**: name the exact target step (and its section if ambiguous), why the flow returns there, and the condition that sent it back. The note is what lets the user wire the jump themselves in the go-to step sidebar — write it so there's zero guesswork about which step to pick.
4. **No backward connector.** The go-to step is the terminus of that branch on the canvas; the jump itself is configured in the sidebar, not drawn. Label the connection *into* the go-to step like any post-conditional branch (1–3 words).

The three-steps rule cuts both ways: within three steps, a connector with proper handles reads fine and a go-to step would be overkill; beyond three, always the go-to step. Count steps along the main flow between the looping step and its target — branches don't count.

---

## Titling steps

Title every step as a **verb-first phrase, roughly four to eight words** — enough detail to name the action, never a paragraph. "Pull active-user rows from Postgres" not "Postgres." "Reconcile invoices against closed deals" not "Reconciliation." A reader scanning the map should understand the whole flow from titles alone. Where it helps, the title can carry the acting role ("CSM sends onboarding kickoff email" reads fine at seven words); otherwise the role association covers it. The note carries the detail.

---

## Step type

Set a `type` on every step so the canvas renders the right visual vocabulary. Valid types: `task`, `email`, `form`, `meeting`, `phone`, `chat`, `video`, `document`, `sms`, `notification`, `payment`, `research`, `database`, `delay`, `conditional`, `delegate`, `sequence`, `signal`, `go_to`, `link`, `webpage`, `workflow`, `audio`, `alias`, `tag`. Match the type to what the step actually is — an email step should look like an email step, a payment step like a payment step, and every fork is a `conditional`. Don't default everything to `task` when a specific type fits.

---

## Executor

Every step gets an `executor` — the field most often skipped, so set it explicitly on every step:

- **Person** — a human does this step manually.
- **Automation** — software runs it automatically.
- **AI Agent** — an agent runs it.

The executor split drives costing, layout, and attributes: manual (Person) steps carry duration + frequency and a responsible role; Automation/AI Agent steps branch downward in hybrid layouts and carry the attributes they populate.

---

## Roles — full RACI, not just the doer

Puzzle models role involvement as RACI: `link_to_steps` with `type: role` takes `involvement: responsible | approver | consulted | informed`. A thorough build assigns all four where they genuinely apply, not just the responsible role.

- Pull role IDs from `list_teams` (with `include: ['roles.id', 'roles.name', 'roles.hourly_rate']`), and pull the reporting lines (role connections) — hierarchy is what lets you infer approvers.
- **Responsible** — the role that performs the work. Every Person step gets exactly one. Usually the function that owns the work (CEO, Program Coordinator, Community Manager, Operations Manager); for a process anyone making operational changes runs, the workspace's generic operator role is the right owner. `role_ids` on the step in `create_process` covers this; patch with `link_to_steps`.
- **Approver** — the role that signs off. Infer from hierarchy: usually the responsible role's manager per the Team Canvas reporting lines, or the role responsible for a downstream approval/review conditional. Assign on steps where sign-off is real: approval conditionals, irreversible actions (send, pay, publish, delete), and threshold decisions. One approver per step.
- **Consulted** — roles whose input shapes the work (two-way). Infer from surrounding steps: the roles responsible for the steps immediately upstream and downstream of this one, and any role whose branch this step's output feeds. If the step consumes another role's work product or its outcome changes how they work, that role is consulted.
- **Informed** — roles that need to know the outcome (one-way). Infer from the rest of the flow: roles that own later steps in the process, the owner of the section/process as a whole, and the receiving role at any handoff or platform transition.
- **Don't RACI-spray.** Responsible is mandatory on every Person step; A, C, and I only where the relationship is real. A five-step process where every role is consulted on every step is noise, not documentation. If the inference isn't obvious, propose your best-guess RACI table in the intake round and let the user correct it.
- **Add, don't replace.** Before bulk-assigning on an existing process, check existing roles with `list_steps` (`include: ['roles.role_id', 'roles.role_name', 'roles.involvement']`) so you add alongside rather than clobber.
- For human-in-the-loop checkpoints inside an automated process, assign the responsible human role to just those checkpoint steps, with the approver on the checkpoint if sign-off escalates.

**If a mentioned role doesn't exist on the Team Canvas, create it — with an hourly rate.** Research the average hourly rate for that role (salary data converted to hourly is fine; note the basis), present it in the intake round for confirmation, then create the role via `create_org_structure` — nested in the right team, or in `standalone_roles` (pass `teams: []`) if it belongs to no team — with `hourly_rate` set. Never create a rate-less role: the whole point is that the step cost calculator and section cost calculator show a real number the moment the map lands.

---

## People — when the user names actual humans

Steps are owned by **roles**, not people — but users often describe processes in terms of the humans who run them ("Sarah reviews it, then Marcus approves"). When actual people are named:

1. Resolve each person on the People Canvas via `list_people` (with `include: ['roles.role_name']` to see what roles they already hold).
2. If the person exists and holds a role, use that role on the step. If they hold several, or the right one isn't obvious, it's an intake question.
3. If the person isn't on the People Canvas, or their role is unknown, **ask in the intake round what role they're in** — never invent a role for a named person, and never attach a person's name to a step in place of a role.
4. Create missing people via `create_people` and attach them to their role via `add_person_roles` — that way the map shows both the accountable role and the human currently filling it, and role hourly rates keep the costing intact when the person changes.

The rule of thumb: people change, roles persist. The role owns the step; the person is linked through the role.

---

## Delay steps — model real wait time, don't invent it

The `delay` step type exists to make wait time visible: approval turnaround ("wait 2 business days for legal sign-off"), shipping windows, cooling-off periods, batch schedules ("runs every Monday"). Wait time is often where the real process cost hides, so when the user **explicitly describes waiting** — a turnaround, a hold, a "then we wait for X" — model it as a `delay` step with the wait noted in the step name and note.

But **do not overuse them**: only add a delay step when the wait is explicitly mentioned in the process description or confirmed in intake. Do not speculatively insert delays between every handoff — an ordinary handoff is just a connection, and a map cluttered with imagined delays is worse than one with none. If you suspect a significant unstated wait (e.g. an approval that clearly can't be instant), raise it as an intake question rather than adding the step on your own.

---

## Frequency and time → automatic process costing

On every **Person/manual** step, set `duration` (minutes) and `frequency` (times per month). Estimate from the nature of the action — a quick form fill is 1–2 min, a review or judgment step 3–5 min — and confirm estimates in the intake round rather than asking cold. If the user gave neither number and you can't reasonably estimate, it's an intake question; never build a manual step with empty cost inputs.

This is what turns a map into a costed operation. Without it you have a diagram; with it Puzzle rolls up labor cost automatically. Skip it only on Automation/AI Agent steps unless the user wants those costed too.

---

## Attributes — the data every step reads and writes

Attributes are what make a step concrete instead of hand-wavy. For every step that touches structured data:

- **Manual steps** — which fields does the person actually fill out or update in that tool? (e.g. "Update deal stage, close date, and amount in the CRM.")
- **Automation steps** — which fields does the automation populate in the target system?

Work the data model in this order:

1. Check `list_entities` and `list_attributes` for the tools involved.
2. If the entities/attributes exist, associate them to their steps via `link_to_steps` with `type: attribute` and each `attribute_id`. (Attributes always link post-build — `create_process` doesn't carry them.)
3. If they don't exist, **propose them**: draft the entity (Contact, Deal, Ticket, Order) and its attributes with sensible field types, present the proposal in the intake round for approval, then create via `create_data_model` (entities with nested `attributes`; use `standalone_attributes` with `entity_id` to add fields to an existing entity) and link them to the steps.

Only model data the process genuinely depends on. Don't over-model — but never leave a data-touching step with zero attributes either.

---

## Tools

Link every tool a step touches — via `tool_ids` (and `primary_tool_id`) on the step in `create_process`, or `link_to_steps` with `type: tool` when patching. This is the other field skipped by default — verify it landed.

- Resolve all tool IDs in one `list_tools` call (comma-separated names).
- A step can carry multiple tools. Tag every system the step actually uses.
- **If a mentioned tool isn't in the workspace, add it** before the build — `link_to_steps` auto-adds catalogue tools, and `update_account_tools` / `create_tool_structure` covers the rest — so the Tools Canvas stays the complete inventory and the association lands on the step.

---

## Mini-SOP notes

Every step gets a note written so anyone reading the map knows exactly how the step is configured and why. Use a consistent four-part structure:

> **Purpose** — why this step exists (and why it's configured this way).
> **Inputs** — what it needs to start.
> **Procedure** — the actual how, in order. Name the responsible role.
> **Output** — what it produces / hands to the next step.

Structure can land in the `create_process` call (short one-liners) with the full mini-SOP backfilled via `update_workflow` — but the build isn't done until the notes pass is done.

Also write a **section overview note**: why the process exists, when it triggers, who owns it, and the quality bar. The section note is the canonical home for report templates or system context the process relies on.

Write notes plainly: no corporate filler (powerful, seamless, robust, leverage, unlock, empower, streamline), no padding. Anyone reading a note should be able to run the step.

---

## Status

Set step `status` deliberately: **Live** for steps running in production today; **Draft**/**In_Progress**/**Testing** for future or in-flight states. A process documenting something already operating should be Live across the board; a proposed future-state process should not claim Live. If the prompt doesn't make it obvious, it's an intake question.

---

## Changelog — memorialize every build

When the process is built (or meaningfully changed), record it. Always. Use `create_changelog_entries` with a Markdown body (title as the first heading) that captures:

- What the user asked for, in substance (the process, the decisions made in intake: orientation, rates, attributes, branch logic)
- What was created or changed, across **all** elements: section and steps, connections and labels, roles created (with rates), tools added, entities/attributes created and linked, cost inputs set
- Why — the rationale behind any judgment calls

Set `status: completed`, set the owner (`person_id` from `list_people`), then use `update_changelog_entries` to associate the relevant steps and tools so the entry is wired into the map. A build without a changelog entry is an incomplete build.

---

## Quick pre-flight checklist

Before you call it done, confirm every step has:

- [ ] A verb-first title, four to eight words
- [ ] A type that matches the action (forks are `conditional`)
- [ ] A status (Live vs Draft/In_Progress/Testing)
- [ ] An executor (Person / Automation / AI Agent)
- [ ] A responsible role on every Person step (added, not replaced), with an hourly rate on the role
- [ ] Approver on sign-off and irreversible steps; consulted/informed where the relationship is real (no RACI-spray)
- [ ] Tools linked on every step that touches one
- [ ] Attributes linked on every step that reads or writes data
- [ ] Duration + frequency on every Person step (costing)
- [ ] A mini-SOP note (Purpose / Inputs / Procedure / Output)

And the process as a whole:

- [ ] One section, all steps inside
- [ ] Trigger asked about (if unstated) — the first step is what kicks the process off
- [ ] Orientation asked about (if unstated) and chosen for the shape — LTR linear, TTB branchy, hybrid with automations fanning down
- [ ] Named people resolved to roles on the People Canvas (created via `create_people` + `add_person_roles` if missing)
- [ ] Explicitly mentioned waits modeled as `delay` steps — and no speculative delays inserted
- [ ] Every conditional matched to its pattern: loop-back, full divergence, or diverge-then-converge
- [ ] Loop-back branches wired to the step before the conditional; terminal branches given real terminator steps; converging branches landing on one shared step
- [ ] Every loop-back farther than three steps built as a `go_to` step named "Go to: [target step]", with the destination spelled out in the note — no long backward connectors anywhere
- [ ] Every post-conditional connection labeled (1–3 words)
- [ ] Connector handles set so nothing zigzags (down = bottom→top; loop-back below = bottom→bottom; loop-back above = top→top)
- [ ] New roles created with researched, confirmed hourly rates
- [ ] New tools added to the Tools Canvas and linked
- [ ] Entities/attributes proposed, approved, created, and linked
- [ ] A section overview note (why / when / owner / quality bar)
- [ ] A changelog entry memorializing what was said and done, with steps and tools associated
- [ ] All of the above resolved through at most ONE intake round

---

## MCP mechanics that reliably work

- `create_process` in **one call** with sections + steps + connections, wiring via `temp_id` (more reliable than creating steps then connections separately). Connections accept `label`, `source_handle`, and `target_handle` inline — set them at build time, don't retrofit. For adding to an existing section, pass the `section_id` directly instead of creating a new section.
- Steps in `create_process` carry `role_ids`, `tool_ids`, `primary_tool_id`, `duration`, `frequency`, `status`, `executor`, `type`, and `notes` directly — front-load everything the schema allows. Attributes are the exception: always a post-build `link_to_steps` pass.
- To rewire an existing arrow without deleting it: `list_steps` with connection includes to get the connection ID, then `update_workflow` with a `connections` array (`id`, `from_step_id`, `to_step_id`, `source_handle`, `target_handle`, `label`) to redirect in place.
- Capture returned step IDs immediately — every later `update_workflow`, `link_to_steps`, and changelog association needs them.
- `update_workflow` takes max 25 steps per call — batch accordingly on big processes.
- Edit in place. When extending a process, update existing steps and connections rather than tearing down and rebuilding.
- Verify after building: `list_steps` with `include` for roles, tools, attributes, duration, frequency, and connections. A field missing from a response means you didn't request it, not that it's empty — include what you're checking.

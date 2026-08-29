# Business Operations Blueprint Guide

## Overview

A **Business Operations Blueprint** is your single source of truth for how your organization works. It maps departments, processes, technology, and changes—creating a compass that steers decisions and keeps your team aligned.

Think of it as a living document that captures:
- **Who** does what (departments, roles, ownership)
- **How** work flows through your organization (inbound, internal, outbound processes)
- **What** tools and software your business runs on (tech stack, costs, dependencies)
- **What changed** and when (change management history)
- **What's coming** (roadmap for improvements)

## Why Build a Blueprint

Without a blueprint, important information lives in people's heads, spreadsheets scatter across drives, and decisions get made with incomplete visibility.

A blueprint solves this:
- **Decision clarity**: See your entire operation mapped out before deciding to optimize, automate, or change
- **Change management**: Track every change, who made it, and what impact it had
- **Cost visibility**: Know what every tool costs, when it renews, and who actually uses it
- **Onboarding**: New team members can see exactly how things work
- **Scaling**: When you hire, the blueprint scales with you—no tribal knowledge left behind

## Core Sections

### 1. Metadata

**What**: High-level info about the blueprint itself

**Fields**:
- `name`: Organization or business name
- `version`: Current version (e.g., 2.1)
- `created`: When it was first created
- `lastModified`: Last update
- `owner`: Primary maintainer
- `description`: Quick overview

**Example**:
```json
{
  "name": "TradeCo Operations",
  "version": "2.1",
  "owner": "Alice Chen",
  "description": "Trading firm with research, execution, and support functions"
}
```

### 2. Departments

**What**: Your organizational structure and teams

**Fields per department**:
- `id`: Unique identifier (e.g., `dept-sales`)
- `name`: Department name
- `owner`: Department lead
- `members`: Number of people
- `description`: What they do
- `processes`: Process IDs they own
- `tools`: Tool IDs they use

**Why it matters**:
- Clarifies who owns what
- Shows team capacity
- Links departments to their processes and tools
- Helps identify gaps and overlaps

**Example**:
```json
{
  "id": "dept-research",
  "name": "Research & Strategy",
  "owner": "Bob Martinez",
  "members": 5,
  "description": "Develops and backtests trading strategies",
  "processes": ["proc-backtest", "proc-signal-generation"],
  "tools": ["tool-backtrader", "tool-bloomberg"]
}
```

### 3. Processes

**What**: How work flows through your organization—the day-to-day workflows

**Fields per process**:
- `id`: Unique identifier (e.g., `proc-onboarding`)
- `name`: Process name
- `category`: Type—`inbound` (customer input), `internal` (internal ops), `outbound` (customer output)
- `owner`: Process owner
- `description`: What and why
- `steps`: Ordered list with owner, tools, duration — and, where the process
  forks, waits or loops, the branch fields below
- `frequency`: How often it runs
- `kpi`: Key metric, target, and current performance

**Fields per step**:
- `number`: Sequence number. Flow falls through to the next number unless a
  step's `branches` say otherwise, so a linear process needs nothing else
- `title`: Verb-first, roughly four to eight words
- `owner`: Who performs it
- `tools`: Tool IDs the step touches
- `duration`: How long **one run** takes — `"10 minutes"`, `"2-8 hours"`,
  `"1 day"`. Not how often it runs; that is `frequency`
- `frequency`: Times per month. Duration times frequency is what turns a
  process into a cost, which is the whole argument in a bottleneck review
- `executor`: `person`, `automation` or `ai`. Person steps are the labour bill
- `kind`: `task` (default), `decision`, `delay`, `end` or `goto`
- `branches`: On a `decision`, where each outcome goes:
  `[{"label": "Approved", "to": 4}, {"label": "Rejected", "to": "end"}]`.
  Labels are one to three words; `to` is a step number, or `"end"` for a
  branch that stops there
- `goto`: On a `goto` step, the step number it jumps back to
- `notes`: The mini-SOP — purpose, inputs, procedure, output

**Why it matters**:
- Shows the real flow of work
- Identifies bottlenecks and inefficiencies
- Shows which tools are used where
- Tracks performance against targets

**Branching**: a process written as a numbered list quietly drops every fork in
it, and forks are where processes actually go wrong. Mark the fork as a
`decision` step named as the question it answers, then give it `branches` —
every outcome labelled, every destination a real step number or `"end"`. Three
patterns cover it: a branch that loops back to earlier work, a branch that runs
to its own ending, and two branches that rejoin on one shared step. A loop-back
more than three steps upstream is a `goto` step instead of a long backward
branch. `python tools/blueprint_converter.py validate` checks all of that — a
branch pointing at a step that does not exist is an error, not a warning.

The full craft standard, including how the same process is drawn on a canvas,
is in `.claude/skills/process-mapping/SKILL.md`.

**Example**:
```json
{
  "id": "proc-live-execution",
  "name": "Live Trade Execution",
  "category": "internal",
  "owner": "Carol Zhang",
  "description": "Execute approved trades with risk controls and real-time monitoring",
  "steps": [
    {
      "number": 1,
      "title": "Review and approve trade signals",
      "executor": "person",
      "owner": "Execution Lead",
      "tools": ["tool-slack", "tool-trade-journal"],
      "duration": "10 minutes",
      "frequency": 40
    },
    {
      "number": 2,
      "title": "Did the signal pass review?",
      "kind": "decision",
      "executor": "person",
      "owner": "Execution Lead",
      "duration": "2 minutes",
      "frequency": 40,
      "branches": [
        { "label": "Approved", "to": 3 },
        { "label": "Rejected", "to": "end" }
      ]
    },
    {
      "number": 3,
      "title": "Calculate position size and risk allocation",
      "executor": "person",
      "owner": "Risk Manager",
      "tools": ["tool-trade-journal"],
      "duration": "5 minutes",
      "frequency": 30
    }
  ],
  "frequency": "per trade signal",
  "kpi": {
    "metric": "Execution slippage (avg)",
    "target": "< 1 bp",
    "current": "0.8 bp"
  }
}
```

### 4. Tools (Tech Stack)

**What**: Every software, service, and platform your business uses

**Fields per tool**:
- `id`: Unique identifier (e.g., `tool-slack`)
- `name`: Tool name
- `category`: Category (Communication, Payment, Analytics, etc.)
- `purpose`: What it does for you
- `url`: Link to the tool
- `cost`: Amount, currency, frequency, renewal date
- `owner`: Person managing it
- `users`: Which departments use it
- `criticality`: essential / important / nice-to-have
- `dependencies`: Other tools it relies on

**Why it matters**:
- Complete visibility into your tech stack
- Understand tool costs and renewals at a glance
- Identify unused or duplicate tools
- See tool dependencies and integration points
- Make renewal/upgrade decisions based on data

**Example**:
```json
{
  "id": "tool-slack",
  "name": "Slack",
  "category": "Communication",
  "purpose": "Team communication, alerts, notifications",
  "url": "https://slack.com",
  "cost": {
    "amount": 1200,
    "currency": "USD",
    "frequency": "yearly",
    "renewalDate": "2026-06-30"
  },
  "owner": "David Lee",
  "users": ["dept-research", "dept-execution", "dept-support"],
  "criticality": "important"
}
```

### 5. Changes (Change Log)

**What**: A record of every significant change made to your operations

**Fields per change**:
- `id`: Unique identifier
- `date`: When it happened
- `title`: What changed
- `description`: Why and how
- `category`: What type (process, tool, department, workflow)
- `impact`: Who/what was affected
- `author`: Who made the change
- `status`: planned / in-progress / completed / rolled-back

**Why it matters**:
- Single source of truth for what changed when
- Provides context for decisions (why did we switch tools?)
- Helps track the impact of changes
- Gives new team members visibility into recent evolution
- Supports compliance and audit trails

**Example**:
```json
{
  "id": "chg-001",
  "date": "2026-07-15",
  "title": "Implemented automated risk monitoring with Datadog",
  "description": "Replaced manual daily risk checks with continuous Datadog alerts...",
  "category": "tool",
  "impact": "Execution team now gets real-time alerts",
  "author": "Carol Zhang",
  "status": "completed"
}
```

### 6. Roadmap (Future State)

**What**: Planned improvements and changes coming down the pipeline

**Fields per roadmap item**:
- `id`: Unique identifier
- `title`: What's being improved
- `description`: Why and how
- `category`: Area (process automation, cost reduction, product, etc.)
- `priority`: critical / high / medium / low
- `targetDate`: When you plan to implement
- `owner`: Who's responsible
- `status`: backlog / planned / in-progress / completed

**Why it matters**:
- Transparent view of the future state
- Shows strategy and priorities
- Helps team plan work and dependencies
- Accountability and tracking

**Example**:
```json
{
  "id": "roadmap-001",
  "title": "Automate daily P&L reporting",
  "description": "Build script to automatically pull end-of-day positions from IB, calculate P&L, and email report",
  "category": "process automation",
  "priority": "high",
  "targetDate": "2026-10-15",
  "owner": "David Lee",
  "status": "planned"
}
```

## Getting Started: Build Your Blueprint

### Step 1: Gather Your Information

Before you start writing JSON or filling spreadsheets, collect what you need:

**Departments**:
- List all teams/departments
- Who leads each one?
- How many people?
- What do they own?

**Processes**:
- What major workflows happen daily/weekly/monthly?
- Who owns each one?
- What steps are involved?
- Which tools do you use in each step?
- How long does it take?
- What's the performance target?

**Tools**:
- What software do you pay for?
- What does each one do?
- Who manages it?
- How much does it cost and when does it renew?
- Which departments use it?
- Are there dependencies (does tool A need tool B)?

**Changes**:
- What's changed in the last 6-12 months?
- Why did you make each change?
- Did it work?

**Roadmap**:
- What do you want to improve?
- What's blocking you?
- What's the priority?
- When do you want to tackle it?

### Step 2: Choose Your Format

Pick one or combine several:

#### Option A: JSON (Programmatic)
- Use `blueprint-schema.json` as your template
- Validate against the JSON schema
- Best for: Integration with tools, automation, shared teams
- See `blueprint-example.json` for a complete example

#### Option B: Excel/CSV (Spreadsheet)
- Use `blueprint-template.xlsx`—has tabs for each section
- Best for: Non-technical stakeholders, visual review, quick updates
- Can be converted to JSON programmatically

#### Option C: Interactive HTML Tool
- Use `static/blueprint-builder.html`—web-based editor (double-click, no server)
- Best for: Collaborative editing, visual dashboards, live updates
- Autosaves to your browser as you work; Export as JSON when you want the file
- Every list is editable — Edit beside Delete on departments, processes, tools,
  changes and roadmap items. Renaming keeps the entry's `id`, so nothing that
  points at it breaks
- **Steps and branches are edited there too.** Each process row has a
  **Steps (n)** button that opens an editor for that process: kind, executor,
  owner, duration, times-per-month, tools, notes, and — on a decision — a
  labelled branch per outcome with its destination picked from the sibling
  steps. Steps renumber themselves when you reorder or delete, and every
  branch and go-to target follows the step it pointed at. A checks panel
  reports what is still unfinished without stopping you saving it, and each
  process row carries the count.
- **The dashboard shows the flows too.** `static/blueprint-dashboard.html` has
  a Process Flows section: every process with steps gets a laid-out diagram of
  its branches, its steps spelled out underneath with destinations named, and
  its monthly person-time figure. A branch pointing at a step that is not
  there says so rather than reading as fine. `static/flow-canvas.html` remains
  the place to *draw* one — it imports a blueprint directly.

#### Option D: Markdown + YAML
- Human-readable, version-control friendly
- Best for: GitHub teams, documentation-first approach
- Structure: One `.md` file per section, link them together

### Step 3: Build It Out

Start with **departments and processes**—this gives you the skeleton.

Then add **tools**—map each tool to the processes that use it.

Then capture **changes**—what's happened in the last 6-12 months?

Finally, draft your **roadmap**—what's next?

Metadata goes in last.

### Step 4: Use It

Once you have your blueprint:

1. **Make it a decision input**: Before hiring, buying a tool, or reorganizing, refer to it
2. **Keep it current**: Update it when things change (don't let it get stale)
3. **Share it**: Make sure your team can see it and contribute
4. **Review it regularly**: Monthly or quarterly, check—is this still accurate?

## Templates & Examples

### Minimal Blueprint (Solo Founder)
Start here if you're the only person:
- 1-2 departments (maybe just you + contractors)
- 5-8 core processes
- 10-15 tools
- Lightweight change log

### Small Team Blueprint (3-10 People)
- 2-4 departments
- 10-20 processes
- 15-30 tools
- Regular change log (monthly)

### Growing Team Blueprint (10-50 People)
- 4-8 departments
- 30-50 processes
- 30-60 tools
- Detailed change log (weekly)
- Active roadmap with priorities

### Enterprise Blueprint (50+ People)
- 8+ departments
- 50+ processes
- 60+ tools
- Change management board
- Quarterly roadmap reviews

See `blueprint-example.json` for a complete medium-complexity example you can adapt.

## Tips & Best Practices

### Naming Conventions
Use consistent IDs for linking:
- Departments: `dept-{name}` (e.g., `dept-sales`, `dept-engineering`)
- Processes: `proc-{name}` (e.g., `proc-onboarding`, `proc-billing`)
- Tools: `tool-{name}` (e.g., `tool-slack`, `tool-stripe`)
- Changes: `chg-{number}` (e.g., `chg-001`, `chg-042`)
- Roadmap: `roadmap-{number}` (e.g., `roadmap-001`)

### Ownership is Key
Every department, process, and tool must have an owner. Ownership = accountability.

### Process Categories Matter
- **Inbound**: Customer-facing processes where work enters (sales, support intake, API calls)
- **Internal**: Things you do inside (backtesting, code review, budgeting)
- **Outbound**: Customer-facing delivery (reporting, shipping, notifications)

### Criticality Levels Help Prioritize
- **Essential**: Can't operate without it (e.g., payment processor, live trading platform)
- **Important**: Makes a big difference but you could workaround temporarily (e.g., project management tool)
- **Nice-to-have**: Would be good to have but not critical (e.g., team wiki, design tool)

### KPIs Make Performance Visible
Every critical process should have a metric:
- Current performance
- Target
- Owner responsible

### Keep the Roadmap Alive
Review it monthly. Move items to in-progress, mark things complete, adjust priorities. A dead roadmap loses trust.

### Version Your Blueprint
Every 3-6 months, bump the version number:
- `1.0` → `1.1` (minor updates)
- `1.0` → `2.0` (major restructure)

## Validation & Syntax

If you're using JSON format, validate your blueprint against `blueprint-schema.json`:

```bash
# Using Python jsonschema
python -m jsonschema -i your-blueprint.json blueprint-schema.json

# Using Node.js
npm install -g ajv-cli
ajv validate -s blueprint-schema.json -d your-blueprint.json
```

## Formats & Export

Both directions are one tool, `tools/blueprint_converter.py`, which also
validates a blueprint against the schema.

### From Excel to JSON
```bash
python tools/blueprint_converter.py xlsx-to-json blueprint-template.xlsx --out my-blueprint.json
```

### From JSON to Excel
```bash
python tools/blueprint_converter.py json-to-xlsx my-blueprint.json --out my-blueprint.xlsx
```

### Checking a blueprint before you share it
```bash
python tools/blueprint_converter.py validate my-blueprint.json
```

### From any format to HTML
Use the interactive tool `static/blueprint-builder.html`—just load your JSON file and export as needed. `static/blueprint-dashboard.html` gives the read-only visual overview.

## Sharing & Collaboration

### For Solo Use
- Keep it in a synced folder (OneDrive, Google Drive, Dropbox)
- Update monthly
- Back up regularly

### For Small Teams
- Store in GitHub/GitLab for version history
- Use JSON format so it's diff-friendly
- Create a PR to request changes
- Have one person (usually CEO/COO) approve

### For Larger Teams
- Store in a database or shared tool
- Use the HTML interface for editing
- Log change requests through a formal process
- Make it required reading for onboarding

## Real-World Example

See `blueprint-example.json` for a complete, realistic blueprint of a quantitative trading firm. It covers:
- Research, execution, and operations departments
- Strategy backtesting, signal generation, live trading, risk monitoring, and P&L reporting processes
- A full tech stack with costs and renewal dates
- Recent changes and future roadmap items

Copy it, adapt it to your business, and you're off.

## FAQ

**Q: How detailed should my processes be?**
A: Enough detail that someone new could follow the steps and understand what happens at each stage. Too detailed and it becomes a procedure manual; too vague and it's useless.

**Q: Should I include my marketing processes?**
A: Yes! Map everything that is mission-critical or involves multiple people or tools.

**Q: What if my processes change constantly?**
A: Document the current state, review monthly, and use the change log to track what shifts. The roadmap is where you capture planned future changes.

**Q: Can I have a process with no owner?**
A: No. If nobody owns it, it's nobody's responsibility, and that's a risk.

**Q: Should I share the tool costs with my team?**
A: Yes. Transparency builds buy-in for cost optimization and prevents tool sprawl.

**Q: How often should I update it?**
A: At minimum, monthly. Whenever something significant changes (new hire, new tool, process changed), update immediately.

**Q: What if I find something broken or outdated while building it?**
A: Good! That's one of the main values. Fix it, document the change, add it to the roadmap if it's bigger than a quick fix.

## Next Steps

1. **Choose a format** (JSON, Excel, HTML, or all three)
2. **Use the template** that matches your choice
3. **Gather your information** using the questionnaire above
4. **Build it out** starting with departments and processes
5. **Share it** with your team and get feedback
6. **Use it** to make decisions
7. **Keep it fresh** with monthly reviews and updates

Your blueprint is done when it accurately reflects how your organization actually works—not how you wish it worked, but how it really works today.

---

**Questions?** Start with the example blueprint and adapt. When in doubt, less detail is better than no blueprint.

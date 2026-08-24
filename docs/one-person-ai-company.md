# The one-person AI company

A local service business run by one person with no employees: the phone, the
quotes, the schedule, the invoices, the collections. The thing that makes it
work is not the agent count. It is that **a local business is a loop, not a
funnel**.

```
   ┌────────────┐   ┌────────┐   ┌───────┐   ┌────────────┐   ┌─────────┐
   │ MARKETING  │──▶│ INTAKE │──▶│ SALES │──▶│ OPERATIONS │──▶│ FINANCE │
   │rings phone │   │catches │   │books  │   │ does job   │   │gets paid│
   └────────────┘   └────────┘   └───────┘   └────────────┘   └─────────┘
         ▲                                                          │
         └──────────  collected cash sets next week's ads  ──────────┘
```

A funnel ends at the sale. This does not: what finance collects is the input
to what marketing spends, so the last stage is wired to the first and the
whole thing either compounds or shrinks on its own arithmetic. Everything
below follows from that one property.

Three pieces make it real, the same split the readiness framework uses:

| Piece | What it is | Where |
|---|---|---|
| This doctrine | Why the loop is shaped this way, and what a person is still for | `docs/one-person-ai-company.md` |
| The architecture | The whole business as a blueprint: 6 processes, 90 steps, every executor named | `docs/blueprint-one-person-ai-company.json` |
| The checks | The parts that can be computed rather than asserted | `tools/ai_company.py` |

The blueprint is the source of truth. The page, the roster, the gate audit and
the labour figure are all derived from it, so none of them can drift from the
process they describe. Edit the blueprint; regenerate the rest.

## The one rule

**AI moves information. You move money and risk.**

That is not a slogan here, it is a property of the map that something can
fail. A step declares `commits: true` when it spends money, moves cash, or
binds the business to a contract. Every committing step run by an agent must
have a person step immediately in front of it:

```bash
python tools/ai_company.py gates
```

The audit gives four answers, and the middle two are the interesting ones:

- **Ungated** — an agent commits with nobody in front of it. This is the
  failure the architecture exists to prevent, and it exits non-zero.
- **Partially gated** — some paths in pass a person and some do not. That is
  what a *threshold* looks like: quotes under the auto-send limit go straight
  out, ones over it wait; small parts orders are placed, large ones are
  approved. A deliberate design decision, and one you should have to look at.
- **Automation commits** — a webhook firing an invoice off a signed closeout
  needs no gate, because it has no judgement. Check the trigger is really
  deterministic.
- **Touches but is not marked** — reading Stripe and charging through it are
  the same tool, so the tool a step uses cannot tell you whether it commits.
  These are listed to confirm, never convicted. An audit that flags every
  read of a payment system is an audit people stop reading.

Declaring commitment rather than inferring it from tool categories is the
whole reason this check is worth running. The inferred version convicted five
steps on its first run, three of which only *read* ad reporting.

## The roster is derived, not written

There is no list of agents in this repository. A step whose executor is `ai`
names its agent in `owner`, so the roster is a query:

```bash
python tools/ai_company.py roster
```

Seven roles run the whole loop — intake, qualifier, quoting, dispatch,
closeout, collections, pricing analyst. That number is not a design target; it
is however many distinct owners the map happens to name, and it will change
the moment the map does.

This matters more than it sounds. A hand-maintained roster beside a map is two
descriptions of one system, and the repository has already paid for that
mistake twice — it is why `CLAUDE.md` forbids pinning a test count and why the
process grammar lives in one file that both Python and the browser tools read.

### The other thirty-three

The pitch this came from claims forty agents. Seven exist. The other
thirty-three are in the blueprint's roadmap, every one of them `backlog`, each
naming the role it splits out of and the volume that would justify it:

```bash
python tools/ai_company.py roster --expansion
```

They are written down because the fan-out is real — an intake agent genuinely
does split into after-hours voice, daytime overflow, SMS, web chat and DMs
once the volume is there — and they are marked backlog because **a committed
list of named agents that reads as built is how a customer-facing promise gets
wired to something that does not exist.** `tests/test_ai_company.py` asserts
that none of them is ever marked anything else.

### What breaks at forty

Forty agents is forty prompts to maintain, forty places for a stale rule, and
forty independent chances to promise a customer a Thursday that is already
gone. Three things go wrong before the agent count does:

1. **Shared state stops being shared.** Seven agents reading one CRM and one
   calendar is a design. Forty agents each holding a slightly different idea
   of the client is a bug you find through a customer.
2. **The gates get diluted.** Every split is a chance for a committing step to
   end up downstream of an agent rather than a person. Run `gates` after every
   split; that is what it is for.
3. **Nobody can say what changed.** With seven roles you can read the whole
   map in one sitting. That property is worth more than the parallelism.

Split a role when a *measured* volume justifies it, which is the number each
roadmap item carries. Not before.

## Underneath: plain files

Three files the owner edits directly, that every agent reads and none writes:

- **`pricing.yaml`** — what every line item costs.
- **`rules.md`** — service area, jobs taken and refused, the auto-send limit,
  the supplier spend limit, payment terms, the sample-size floor.
- **`bench.csv`** — subs and crews in preference order, with trades and rates.

Every threshold in this architecture is a number in one of these files rather
than a judgement an agent makes. That is what turns "is this quote too big to
send unattended" from a prompt-engineering problem into a comparison.

Claude Code reaches the CRM, the job system and the books over MCP — one layer,
so there is a single place where permissions and logging live.

## Where the numbers refuse to come from

Three deliberate refusals, each pinned by a test:

**No hours-returned figure without a baseline.** `ai_company.py hours` prices
the person steps — 171 h/mo for the reference, of which 159 is the job itself —
and refuses to report hours *returned* unless you pass a current-state map with
`--baseline`. A target design has no "before" inside it, and subtracting from
an imagined one is how a tool starts producing numbers nobody can check. Same
rule as `tools/spend_watch.py` refusing a burn rate from one snapshot.

That 159-hour line is worth sitting with. **The agents do not do the job.**
What is left for a person in this design is about a hundred and fifty-nine
hours in a van and twelve hours of everything else — the closes, the
approvals, the change orders. How much admin the agents *removed* is a
question this file cannot answer, for exactly the reason above: there is no
baseline behind it.

**No repricing off noise.** The self-repricing loop is the most attractive part
of the pitch and the easiest to get wrong. A one-van shop closes ~38 jobs a
month across several job types, so most types have too few jobs in any month to
tell a margin drift from an ordinary run of hard jobs. So `reprice` gates
twice — sample size first, then whether the drift interval excludes zero — and
"not enough jobs yet" is a first-class outcome that says how many more are
needed:

```bash
python tools/ai_company.py reprice jobs.csv --target-margin 0.42
```

The test that matters refuses a **22-point** margin gap on four jobs. A gate
that only declines when the evidence is also weak is not a gate.

**No invented costs.** Every tool in the blueprint has no `cost` field, and
every KPI reads `unmeasured`. Phase 1 of the readiness framework fills those
from the business's real invoices. A placeholder in a committed reference gets
read as a fact.

## Running it

```bash
python tools/ai_company.py roster                # who runs what, from the map
python tools/ai_company.py roster --expansion    # and the 33 that do not exist
python tools/ai_company.py gates                 # who can commit money, and what gates them
python tools/ai_company.py hours                 # the labour bill, by stage
python tools/ai_company.py loop --spend 2400 --leads 120 --jobs 38 \
    --job-value 1450 --margin 0.42 --cycle-days 24 --rule-payback-jobs 0.35
python tools/ai_company.py reprice jobs.csv --target-margin 0.42
python tools/ai_company.py page                  # regenerate docs/one-person-ai-company.html
```

Every command takes `--blueprint` — point it at a client's blueprint and the
same checks run against their business.

The architecture also imports into the visual tools with no conversion, because
it is an ordinary business blueprint: `static/blueprint-dashboard.html` to read
it, `static/blueprint-builder.html` to edit it, `static/flow-canvas.html` to
see any process as a map.

## How it plugs into an engagement

This is the reference target for **phase 7** of the AI & automation readiness
framework (`docs/ai-readiness-framework.md`). Rather than rediscovering the
shape of the answer per business:

```bash
python tools/engagement.py seed-target acme-plumbing
```

That writes `07-target-design.md` from this architecture — the loop, the roles,
the gates, the plain files — with every section turned into a question about
*that* business. It carries an `UNEDITED REFERENCE` line, and `advance` refuses
while that line is present.

The reason is the same one as everywhere else here: a phase whose gate is "the
deliverable exists" is trivially passed by a deliverable that was written for
you. Seeding is a convenience for the shape, never for the content.

## Three places this departs from the pitch

The architecture this came from is sound. Three things were changed on the way
into the repository, and each is a disagreement worth knowing about:

1. **Quotes are not all sent unattended.** The original has the agent build and
   send, full stop. A wrong price out the door is the cheapest mistake to
   prevent and among the most expensive to retract, so quotes above the
   auto-send limit in `rules.md` wait for a person. Small ones still go
   straight out — reviewing all 64 a month would cost more attention than it
   saves.
2. **A supplier order above the spend limit waits too.** An agent placing parts
   orders against a signed job is money leaving the business on an agent's
   reading of a material list. Same shape as the quote limit, same file.
3. **The repricing loop recommends; it does not reprice.** The pitch has the
   agent fix your prices when quoted and actual diverge. At this job volume it
   would chase noise, and you would find out by losing bids weeks later with
   nothing saying why. So it produces a proposal with its evidence attached,
   and the owner writes `pricing.yaml`. Prices are how the business competes;
   an agent that can move them can empty the calendar before anyone notices.

All three are the same trade: an agent's judgement is cheap and a customer
relationship is not.

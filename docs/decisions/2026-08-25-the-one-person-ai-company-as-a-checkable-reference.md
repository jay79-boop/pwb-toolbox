# The one-person AI company as a checkable reference

*Decided 2026-08-25.*

**Decision:** A framework for running a local service business with agents
enters this repository as **data with checks attached**, not as prose that
describes itself. `docs/blueprint-one-person-ai-company.json` is the
architecture in the existing blueprint schema; `tools/ai_company.py` is the
part that can be computed; `docs/one-person-ai-company.md` is the reasoning.
It is the phase 7 reference target for the readiness framework, stamped into
an engagement by `engagement.py seed-target`.

The load-bearing idea is that a local business is a **loop, not a funnel**:
what finance collects is the input to what marketing spends, so the last stage
is wired to the first. Everything else in the design follows from that.

## The roster is derived, because a second list drifts

There is no list of agents anywhere in this repository. A step whose executor
is `ai` names its agent in `owner`, so `ai_company.py roster` is a query over
the map and cannot disagree with it. This is the same rule that keeps a test
count out of `CLAUDE.md` and the process grammar in one file both Python and
the browser read.

The source framework claimed forty agents. Seven roles run the whole loop; the
other thirty-three are roadmap items, all `backlog`, each naming the volume
that would justify the split. `test_fanout_is_entirely_backlog` asserts none is
ever marked otherwise, because **a committed list of named agents that reads as
built is how a customer-facing promise gets wired to something that does not
exist.**

## Commitment is declared on the step, not inferred from its tools

`gates` convicts an AI step that commits money with no person step immediately
in front of it. The first cut inferred "commits money" from the category of the
tools a step touches, and convicted five steps — three of which only *read* ad
reporting. Reading Stripe and charging through it are the same tool, so the
tool cannot carry the distinction.

So `docs/blueprint-schema.json` gained an optional `commits` boolean on a step,
and `blueprint_converter` round-trips it in an **appended** column, so an
existing Steps sheet keeps every column index it already had. The tool
categories survive as a lint — *this step reaches a payment system and is not
marked; confirm it only reads* — which is a list to check, never a verdict. An
audit that flags every read of a payment system is an audit people stop
reading.

The verdict is three-way, not two: **gated** (every path in passes a person),
**partial** (some do — which is what a threshold looks like, and a design
decision to see rather than a fault), **ungated** (none do).

## Three departures from the framework as pitched

Each is the same trade: an agent's judgement is cheap and a customer
relationship is not.

1. **Quotes above an auto-send limit wait for a person.** Small ones still go
   straight out; reviewing all 64 a month would cost more attention than it
   saves. The limit is a number in `rules.md`, so the routing is arithmetic
   rather than a judgement the agent makes.
2. **Supplier orders above a spend limit wait too.** An agent placing parts
   orders against a signed job is money leaving on an agent's reading of a
   material list.
3. **The repricing loop recommends; it does not reprice.** The pitch has the
   agent fix prices when quoted and actual diverge. Prices are how the business
   competes, and an agent that can move them can empty the calendar before
   anyone notices.

## Repricing pays for the grid it scans

`lab-standard` item five, arrived at the hard way. `reprice` runs one test per
job type, every month — a grid scan, whether or not it was meant as one. At
twelve job types and an uncorrected 5% threshold you would expect to
"discover" a price change in roughly one clean month in two, and then move a
real price on it: precisely the failure the sample-size gate was added to
prevent, arriving through a door left open beside it.

Three gates now, and two of the three outcomes are *leave the price alone*:
sample size, the type's own evidence, then Benjamini-Hochberg across the types
that cleared the first. Same correction and the same stdlib implementation as
`season_scan` and `calibration_audit`; the t-test is a Lentz continued fraction
for the incomplete beta, pinned against the published critical values.

The test that carries it refuses a **22-point** margin gap on four jobs. A gate
that only declines when the evidence is also weak is not a gate.

## Three refusals, each pinned by a test

- **No hours-returned figure without a baseline.** A target design has no
  "before" inside it. `hours` prices the person steps and refuses to subtract
  from an imagined one — the same refusal `spend_watch` makes about a burn rate
  from a single snapshot.
- **No repricing that clears none of the three gates.**
- **No invented tool costs.** Every tool in the reference has no `cost` field
  and every KPI reads `unmeasured`. Phase 1 of the readiness framework fills
  them from real invoices; a placeholder in a committed reference gets read as
  a fact.

The uncomfortable number is deliberate and stays: 171 person-hours a month, of
which **159 is the job itself**. The agents remove the admin around the work,
not the work.

## The seeded design cannot be advanced on

`engagement.py seed-target` writes `07-target-design.md` from this reference
with every section turned into a question about *that* business, and leaves an
`UNEDITED REFERENCE` line that `advance` refuses on. A phase whose gate is "the
deliverable exists" is trivially passed by a deliverable that was written for
you. Seeding is a convenience for the shape, never for the content.

## Pages are light, colour-coded and validated, by default

Asked for once, so written down rather than re-asked: `docs/page-style.md`
carries the tokens and the checks, `CLAUDE.md` carries the three-line rule so
it is in scope without a trigger.

Committed light — no dark theme, every surface and ink stated so the page holds
when the Artifact host paints a dark ground. Colour validated with the
`dataviz` validator rather than eyeballed, which caught two sets that looked
fine and were not: a hand-picked violet/blue pair at CVD ΔE 3.4, and a
teal/green pair at normal-vision ΔE 11.2 that had **passed as saturated hues
and collided only once darkened for text contrast**. Validate the values you
are shipping, not the ones you started from.

Four stage hues for five stages, because marketing and finance share green:
the loop closes when what finance collected becomes what marketing spends, and
the colour says so. Structure that encodes something true beats a fifth
arbitrary hue.

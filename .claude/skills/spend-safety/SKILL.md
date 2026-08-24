---
name: spend-safety
description: How to keep an automated or unattended agent from spending real money — the layered safeguards, the pre-flight checklist for any new paid service, and the rule that a guardrail must never block legitimate automation. Load this BEFORE wiring up anything that can charge a card, place a broker order, buy credits, set an ad budget, or call a metered API; before writing any scheduled Routine or unattended job; and whenever asked about usage limits, token drain, runaway cost, billing risk, or "how do we stop this happening again".
---

# Spend safety

For the owner's projects, the money-capable surface is usually larger than it
looks and nobody has listed it. This is how to bound it without getting in the
way of the work.

## The rule that outranks the others

**Guard the irreversible path only. Never the whole surface.**

A guardrail that blocks legitimate automation or testing gets switched off, and
a switched-off guardrail protects nothing. So the question is never "is this
risky?" — it is "can this specific call move money or take a position?" If not,
leave it completely alone.

Concretely: gate the broker order, not the module that contains it. Deny the
tool whose purpose is purchasing, not the connector it lives on. Prefer a guard
**in the code**, where it can be precise, over a permission rule, which is blunt
and blocks imports and tests too.

## The second rule

**Anything unattended must be incapable of a large mistake, not merely
instructed to avoid one.** Instructions are followed by a model; capability
limits are enforced by a system. Only the second survives a hiccup.

## Prefer refusal to billing

A limit that **refuses** is safe. A limit that **bills** is a number with no
upper bound attached to a card. When picking a plan or a setting, always take
the one that hard-stops.

This is not theoretical: on 2026-08-24 a five-hour window was exhausted by
seventeen concurrent sessions. Nothing was charged, and the only reason was that
the provider *rejected* rather than continuing on overage. That was a default,
not a decision. See `docs/token-drain-2026-08-24.md` and `docs/spend-safety.md`
in pwb-toolbox for the full account.

## The five layers

Each catches what the one above missed. The lower layers keep working for
services nobody remembered were connected — which is the case that actually
bites.

1. **Provider** — usage-based billing, auto-recharge and auto-top-up **off**
   everywhere. Highest-leverage setting there is: it converts every possible
   runaway from unbounded into merely annoying. Set every spend alert offered.
2. **Payment** — a dedicated virtual card with a low monthly limit for API and
   AI services. Never a debit card, never the account ordinary bills leave from.
   The only layer that works against a service you forgot existed.
3. **Permission** — money-capable tools in `deny`, not merely absent from
   `allow`; absent means a future broad grant silently restores them. Keep this
   list *short* and defensible, or rule one is being broken.
4. **Agent** — unattended work gets the smallest surface, not the loosest.
   Fresh session per fire, never persistent. No prompt that schedules its own
   successor. Paper or sandbox by default; live requires a deliberate human act.
5. **Observability** — alert on *trajectory*, not threshold. "Burning faster
   than usual" is useful; "already spent" is not an alert at all.

## The two-key pattern

The reference implementation is `IBConnector._assert_orders_allowed` in
pwb-toolbox. A live-account order needs **two independent keys** that are
awkward to supply by accident:

- an explicit argument in the calling code (`allow_live_orders=True`), and
- an environment variable set on the machine (`PWB_ALLOW_LIVE_ORDERS`).

A stray import, an unattended run, or a flipped config file satisfies neither
alone. Paper ports return immediately with no unlocks, so nothing that cannot
move real money notices the brake exists. An unrecognised port is treated as
live — fail closed.

Copy that shape for any new irreversible action. And make the error name both
remedies: an error that does not say how to proceed just gets worked around.

## Before wiring up any new paid service

1. **List every surface that can spend, before writing code.** What is not on a
   list cannot be protected. This is the step that gets skipped.
2. **Ask what the provider does at the limit** — refuse, or bill? If it bills,
   layers 1 and 2 go in before first use, not after.
3. **Confirm auto-recharge is off.** Check it. Do not assume.
4. **Point it at the limited card**, never the main account.
5. **Write the deny rule before the first unattended run**, not after the first
   incident.
6. **Decide what "too fast" looks like** and set the alert while the number is
   still fresh in mind.

## Scheduled jobs

- Fresh session per fire (`create_new_session_on_fire`), never
  `persistent_session_id`. Firing into a persistent session re-reads its entire
  accumulated history every wake, and that cost grows with the session's age.
- Never let a prompt schedule its own successor. Use a cron: it has a visible
  cadence and one place to turn it off.
- Delete the old Routine in the same breath as creating its replacement.
  Superseded-but-live is indistinguishable from intended, and it fires.
- A fresh-session Routine created over MCP carries **no connector grants**, so
  it starts without `mcp__*` tools. Write the prompt to name which steps need
  which tools and degrade loudly — never let missing tooling read as "nothing
  to do".
- Match effort level to the job. `max` is for hard design calls, not for
  checking whether CI is green.

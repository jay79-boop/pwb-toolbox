# Spend safety: what can actually reach the bank account

Written after the 2026-08-24 window drain (`docs/token-drain-2026-08-24.md`),
which cost nothing but raised the right question: *if that same runaway had
happened on a service that bills per use, what would have stopped it?*

The honest answer for most of the surfaces below is **nothing yet**. This
document is the inventory and the layered fix.

## First, the forensics: nothing was trying to charge

There is no transaction to find, and no failed or pending charge, because none
was ever created. `cost_usd` in session metadata is computed for display by the
Claude Code harness; it never touches a payment processor. It reports what the
traffic **would** have cost at pay-as-you-go rates.

**What actually protected the account was a hard rejection.** When the five-hour
window ran out, sessions came back `status: "rejected"` — refused, not billed.
Had usage-based billing been enabled, that same traffic would have kept running
and kept charging, and the first signal would have been a statement.

That distinction is the whole lesson:

> A limit that **refuses** is safe. A limit that **bills** is a number with no
> upper bound attached to your card.

The protection here was a default, not a decision. Everything below is about
not depending on that again.

## The inventory, ranked by worst case

### Tier 1 — can move real money with no meter involved

These do not bill per token. They move funds or take positions directly, and a
runaway costs whatever the account can bear.

| Surface | What it can do | Bounded by |
| --- | --- | --- |
| `pwb_toolbox/execution/ib_connector.py` | `place_orders` / `execute_orders` → `ib.placeOrder`. Live brokerage orders, market and limit. **On `main` today.** | Account equity and margin. Nothing else. |
| TradingView CDP debug port | Unauthenticated by design — any process on the machine can drive the logged-in chart, including the order ticket | A broker-free login, if one was established |
| `mcp__Windsor_ai__execute_action` | Campaign, ad, **budget** and bidding writes on Meta / Google / TikTok / LinkedIn / Bing Ads | The card on file at each ad platform. A daily budget is a number an agent can set. |
| `mcp__Blotato__blotato_buy_credits` | Purchases credits. That is the tool's entire purpose. | Whatever the vendor allows |

### Tier 2 — metered per use, bills to a card

| Surface | Current state |
| --- | --- |
| Anthropic usage-based billing / extra credits | **OFF** — proven by the rejection above, not assumed |
| 21st.dev MCP | ~$0.01/request |
| Alpha Vantage | Tiered; the spec-desk watch calls it 2x/day |
| Shutterstock | `search` is free; licensing is not |
| ElevenLabs / Voice.ai / Blotato | Credit balances, already tracked by a monthly Routine |

### Tier 3 — flat subscriptions, inherently bounded

Interactive Brokers market data (~$10/mo), Canva, Hugging Face. A runaway cannot
make these cost more. They need no safeguard beyond knowing they exist.

## The gaps that were open, and what closed them

Two were open at once. Both are closed on `main` now; this section records what
they were, because the shape of each is the thing worth recognising again.

**The permission model was written but not in effect.** `.claude/settings.json`
on `main` had one allow entry and zero deny entries, while the model that
reasons carefully about `ui_evaluate` being able to press the Buy button — and
puts dangerous tools in `deny` rather than merely omitting them from `allow`, so
a later blanket grant cannot silently restore them — sat on the unmerged branch
behind PR #78. The analysis had been done and paid for and was protecting
nothing. `main` now carries 85 allow / 14 deny entries, `blotato_buy_credits`
and `Windsor execute_action` among the denied.

**The two connectors disagreed about what "live" means.** `IBConnector` had the
full two-key brake; `CCXTConnector.place_orders` had **no guard of any kind** —
no code key, no environment key, no sandbox detection. The same
`create_connector` call was fail-closed for `broker="ib"` and wide open for
`broker="ccxt"`, submitting real orders to a real exchange on
`PWB_CCXT_API_KEY`. The module docstring demonstrated exactly that. Crypto is
also the side actually being traded, so the unguarded connector was the one in
use.

That asymmetry is the general lesson: **a guard is a property of a path, not of
a package.** Writing one and calling the risk handled is how the second path
stays open. Both connectors now share `pwb_toolbox/execution/_live_guard.py`, so
they cannot drift on the definition again, and sandbox is read off the connected
exchange rather than a constructor flag — a connector that merely *asked* for
sandbox and did not get it is still treated as live.

The ranking that motivated fixing this before anything else still holds: token
metering can cost a window, while an unguarded `place_orders` can cost a
position.

## The five layers

Order matters. Each one catches what the one above it missed, and the lower
layers keep working when you forget about a service entirely.

**L1 — Provider: choose refusal over billing.**
Leave usage-based billing, auto-recharge and auto-top-up **off** everywhere.
This is the single highest-leverage setting in this entire document, because it
converts every possible runaway from unbounded into merely annoying. Where a
provider offers hard cap versus soft cap, always hard. Set spend alerts at every
threshold offered — they cost nothing and they are the only warning that arrives
before the wall.

**L2 — Payment: bound the blast radius at the instrument.**
A dedicated virtual card with a low monthly limit for all API and AI services.
Never a debit card, never the account that ordinary bills come out of. This is
the layer that works even against a service nobody remembered was connected, and
it is the only one that does. Ideally one card per tier, so a Tier 2 mistake
cannot reach a Tier 1 budget.

**L3 — Permission: deny the tools that spend.**
Put money-capable tools in the `deny` list, not merely absent from `allow` —
absent means a future broad grant re-enables them silently. Starting candidates:
`blotato_buy_credits`, `Windsor_ai execute_action`, and everything under
`pwb_toolbox.execution` for any unattended context. Merging PR #78 brings the
already-reasoned version of this for the TradingView surface.

**L4 — Agent: unattended work gets the smallest surface.**
Nobody is watching a scheduled job, so it gets the tightest permissions, not the
loosest. Fresh session per fire, never a persistent one. No prompt that schedules
its own successor. Paper or sandbox mode as the default, with live access
requiring a deliberate human step rather than a config flag someone can flip.

**L5 — Observability: see the meter before the wall.**
The 2026-08-24 drain was invisible until it hit 100%. Nothing warned at 50% or
80%, and by the time anything was noticeable the window was already gone. Watch
the *trajectory*, not the threshold — "spending faster than usual" is the useful
alert, and "already spent" is not an alert at all.

## Before starting any project that touches a paid service

1. **List every surface that can spend**, before writing code. If it is not on a
   list it cannot be protected, and this is the step that gets skipped.
2. **Confirm what the provider does at the limit** — refuse or bill? If it bills,
   that is a Tier 2 surface and needs L1 and L2 in place before first use.
3. **Confirm auto-recharge is off.** Check it; do not assume it.
4. **Point it at the limited card**, never the main account.
5. **Write the deny list before the first unattended run**, not after the first
   incident.
6. **Decide what "too fast" looks like** and set the alert while you still
   remember the number.

## The auditor, and what it refuses to claim

`tools/spend_watch.py` is the check behind the rules above. Two commands:

```bash
python tools/spend_watch.py audit snapshot.json          # structural findings
python tools/spend_watch.py audit now.json --baseline earlier.json   # + growth
python tools/spend_watch.py session <transcript>.jsonl --quiet       # am I too big?
```

`audit` takes `{"sessions": [...], "triggers": [...]}` straight from the
`list_sessions` and `list_triggers` MCP tools. It finds: Routines that tell
themselves to schedule a successor, Routines bound to a persistent session,
**two enabled Routines doing the same job on the same cron**, sessions fat
enough that waking them is expensive, and too many sessions awake at once.

**It refuses to derive a burn rate from one snapshot.** Session metadata reports
*lifetime* totals, so dividing by elapsed time turns a figure accumulated over a
day into an apparent hourly one — the exact misreading that made $290 of
lifetime metering read as a runaway. Rate findings appear only with `--baseline`.

Three things it got wrong on 2026-08-24 and no longer does, each worth knowing
because each failed *silently*:

- **A negated mention is not an instruction.** Every Routine prompt now ends
  with "do NOT re-arm yourself". A substring search flags precisely the
  Routines that were fixed, and a check that fires hardest on its own cure is
  one nobody reads.
- **The window is whichever limit is binding.** `rateLimitType` can be
  `seven_day`, not just `five_hour`. Assuming five hours put the window start
  four days in the future and the concurrency check found nothing — passing
  because it measured an empty set.
- **Concurrency is a recency question.** It is measured over a fixed five-hour
  horizon anchored to the newest activity in the snapshot, never over the
  billing window, or a week of finished work reads as a crowd.

### The session-size warning

`session` reads a session's own transcript — the per-turn usage the harness
already writes to disk. **No API call, so the warning never consumes the thing
it is warning about.** That constraint is the whole design: on 2026-08-24 four
concurrent sessions investigating the window consumed about half of it.

`.claude/hooks/session-size.sh` runs it on every prompt and stays silent below
10M cache reads. It speaks once per tier (10M / 25M / 50M) and then not again
until the tier changes.

Read cache reads, not output, when judging a session's weight. In the measured
window they ran 183:1 against output — 96.9% of every token moved was context
being re-read, and that is the bill a long session pays before it does anything.

## The principle, in one line

Anything unattended must be **incapable** of a large mistake, not merely
instructed to avoid one. Instructions are followed by a model; capability limits
are enforced by a system. Only the second kind survives a hiccup.

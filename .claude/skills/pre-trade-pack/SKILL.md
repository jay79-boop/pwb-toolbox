---
name: pre-trade-pack
description: Build a pre-trade pack for a ticker before a manual order goes in — quote and context, option greeks and decay, the trade_card commitment card with every gate run, position sizing against the account, and a locked-thesis block ready to paste into the trade journal BEFORE execution. Use when the user names a ticker they are about to trade ("thinking of buying NVDA calls", "prep a trade on BTC", "pre-trade pack for TSLA"), asks to size or sanity-check an entry, or asks whether a contract is worth its premium.
---

# Pre-trade pack

The point of this pack is order: **thesis locked, then order placed** — never
the reverse. Everything below exists to make that take under two minutes, so
the journal entry happens before the fill instead of after. You never place
the order; execution stays human, at their broker.

## Build the pack

1. **Quote and context.** Get spot, day move, and (for options) the chain
   around the strike of interest — Alpha Vantage MCP tools when connected
   (`GLOBAL_QUOTE`, `REALTIME_OPTIONS` or `HISTORICAL_OPTIONS`, earnings via
   `EARNINGS_CALENDAR`), otherwise ask the user to read spot/premium/IV off
   TradingView — one question, all fields at once.

2. **The commitment card.** For a single-leg option, run the repo's gates —
   they encode the user's own rules, so the card's verdict outranks your
   opinion:

   ```bash
   python tools/trade_card.py plan --symbol X --spot S --strike K --dte D \
     --premium P --contracts N --account A --kind call \
     --iv-rank R --bid B --ask ASK --target T --earnings YYYY-MM-DD
   ```

   Pass `--no-earnings` only when the user has confirmed none before exit —
   the gate fails silence on purpose. For stock or crypto entries skip the
   card and cover the same ground by hand: size as % of account, invalidation
   level, and target.

3. **Greeks and decay** (options only) — `pwb_toolbox.options` or
   `static/option-lab.js` for delta/theta/vega, the decay curve to expiry,
   and touch/finish probabilities at the target. Flag anything the card's
   gates didn't already: theta burn > a few % of premium per day, spread
   wider than a day's expected move, IV rank extremes.

4. **The verdict, honestly.** If a gate fails, the pack's headline is the
   failure, not the workaround. The user can still trade — but the pack never
   dresses a failed gate as a pass.

## Lock the thesis

End every pack with a paste-ready journal block and ask for the one line
only the user can write — *why this trade, and what kills it*:

```
TICKER · direction · size (n% of account)
Entry: ...   Invalidation: ...   Target: ...   Exit-by: ...
Greeks at entry: Δ ... Θ ... IV ...
Thesis: <their sentence>
```

The thesis question is the last thing before they place the order. If they
won't answer it, that is the pack's finding.

# Job: pre-market gameplan

**Runs:** weekday mornings, before the open.
**Goal:** arrive at a ranked gameplan you review, rather than a blank chart you
have to build from.

**This job reads bar data, not a chart.** It may be running with no desktop at
all — its scheduled task is registered to run whether anyone is signed in or
not — so **do not call `tv_launch` or any TradingView tool.** They cannot work
there, and a run spent discovering that reports a chart problem instead of
doing the job.

## Do

1. Read the current structure from bars, per instrument on the watchlist:

   ```
   python tools/desk_levels.py levels NQ=F --markdown
   python tools/desk_levels.py levels ES=F --markdown
   ```

   That gives you session levels (Asia, London, NY), the prior trading day's
   range, every unmitigated fair value gap with the order block that produced
   it, and where price sits relative to all of them in basis points.

   Add `--json out.json` if you want to compute against it rather than read it.

2. Rank what you find. A setup that needs price to travel a long way before it
   is live is not the same as one that is at the door, and the ranking should
   say which is which. The `from price` column is already in basis points and
   already sorted nearest-first — use it rather than eyeballing raw points.

3. Where a candidate maps to a strategy already in the repo, run it through
   `tools/backtest_lab.py` over a recent window so the gameplan carries a
   number rather than an opinion. **Charge costs.** A gross figure is not a
   result: the last time this step ran against TradingView's Strategy Tester it
   returned −2010 on a saved copy configured with no commission and no
   slippage, and real friction put the true number near −2280.

4. Write the gameplan to `tools/desk_agent/out/gameplan-<YYYY-MM-DD>.md` and
   commit it.

## Read the staleness line before you quote a number

Every `desk_levels` run prints the age of the last bar it saw, and says
`STALE` when that age is beyond a few hours. **A level from a stale feed is not
a level about this morning.** If it says stale, say so in the gameplan and
treat the live-price comparisons as unusable — the settled levels (prior day,
overnight sessions) still hold, because those are history and history does not
go stale.

It will also tell you if a bar is stamped in the *future*. That is a clock or a
feed fault, not a curiosity: stop and log it rather than working around it.

## What the numbers are, and are not

The feed is yfinance's continuous futures (`NQ=F`, `ES=F`). Two honest limits,
both of which belong in the gameplan when they bite:

- **It is not a live quote.** Treat "where price is now" as indicative. Every
  level that matters at 07:00 — the prior day's range, the overnight session
  highs and lows — is settled history and is exact.
- **It is one vendor.** `docs/backtesting.md` records two feeds of the same
  index disagreeing by 284bp over eight years while correlating 0.93. A level
  that a trade will actually be placed against deserves a second source before
  it is trusted to the tick.

## The gameplan

Lead with the two or three that actually matter. A list of nine is a list
nobody reads at 08:00.

For each: the instrument, the setup, the level that makes it live, the level
that kills it, and what the backtest said if you ran one. Where you are
guessing, say so — a gameplan that hides its uncertainty is worse than none,
because it gets acted on with the same confidence as the parts that are solid.

## Honest outcomes

- Found nothing worth naming → `ok`, no actions, and say the market gave you
  nothing. This is a real and frequent answer.
- Market closed, holiday, no data → `skipped`.
- The feed returned nothing, or returned bars too stale to use → `failed`, with
  the blocker. Do **not** fall back to a chart.

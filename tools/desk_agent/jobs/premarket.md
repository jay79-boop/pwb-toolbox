# Job: pre-market gameplan

**Runs:** weekday mornings, before the open.
**Goal:** arrive at a ranked gameplan you review, rather than a blank chart you
have to build from.

## Do

1. For each instrument on the watchlist, read the current structure off the
   chart: session levels, the prior day's range, any order block or FVG that is
   still unmitigated, and where price sits relative to them.
2. Rank what you find. A setup that needs price to travel a long way before it
   is live is not the same as one that is at the door, and the ranking should
   say which is which.
3. Where a candidate maps to a strategy already in the repo, deploy it and run
   the Strategy Tester over a recent window so the gameplan carries a number
   rather than an opinion.
4. Write the gameplan to `tools/desk_agent/out/gameplan-<YYYY-MM-DD>.md` and
   commit it.

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
- Could not reach the chart → `failed`, with the blocker.

Do not manufacture a setup to fill the page.

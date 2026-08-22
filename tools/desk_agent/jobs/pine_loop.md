# Job: Pine build-test-refine loop

**Runs:** on demand, not scheduled — it starts from a description only the
owner can give.
**Goal:** turn a setup described in plain English into a strategy with measured
behaviour, without a human in the loop for each iteration.

## Do

1. Write the Pine for the described setup.
2. Deploy it to the chart and compile it. A compile error is information:
   read it, fix it, go again.
3. Run the Strategy Tester over the agreed window and read the results back —
   net profit, trade count, win rate, max drawdown, profit factor.
4. Compare against the target the owner set. If it misses, form **one** clear
   hypothesis about why, change **one** thing, and rerun.
5. Stop when the target is met, when a hard iteration cap is reached, or when
   two consecutive changes move nothing. Report which of the three it was.

## The rules that keep this honest

- **Change one thing per iteration.** Two changes and a better number tells you
  nothing about which one helped.
- **A trade count below about thirty is not a result.** Say so rather than
  reporting a win rate computed from four trades as though it meant something.
- **Never tune until it passes.** If twenty iterations are needed to hit the
  target, the target is being fitted to the sample, and the honest report is
  "this setup did not produce an edge on this window", not the twentieth
  variant. Say that out loud; it is the most useful thing this job can find.
- **Record every iteration's numbers** in the output file, not just the winner.
  The path matters more than the destination when you come back to it later.

## Output

`tools/desk_agent/out/pine-<name>-<YYYY-MM-DD>.md`: the final script, the
iteration table, and a one-paragraph verdict that a reader can act on without
rerunning anything. Commit it.

Log `--metric iterations=N` and `--metric trades=N` so the review can see
whether this loop is converging faster over time or not.

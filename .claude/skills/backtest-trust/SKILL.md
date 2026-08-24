---
name: backtest-trust
description: The two checks that must pass before any backtest number in this repo is believed or quoted — verify the feed's timezone against a known feed in a winter AND a summer month (`verify_timezone`), and clear `noise_floor()` against a second vendor of the same instrument. Load whenever a strategy reports a result, before quoting a backtest figure, or when reading a new vendor's price file for the first time.
---

# Before you believe a backtest number

`tools/backtest_lab.py` exists because a single-instrument backtest in this
repo produced a result that survived every check applied to it and was still
wrong twice over. Both failures are cheap to repeat, neither announces itself,
and both are pinned by tests in `tests/test_backtest_lab.py`.

Run both checks. Quote both outcomes alongside the number, or do not quote the
number.

## Check 1 — the feed's timezone, in two different months

A vendor's timestamps are in whatever zone the vendor chose, and the file does
not say. histdata's ASCII M1 exports are **New York local time with DST**, not
the fixed EST they are usually assumed to be. Read with one flat offset they
are right in January and an hour out in July — and since a strategy converts
back to an exchange timezone to test its session, that hour moves the whole
trading window for eight months of every year.

The symptom is a result that looks *plausible*: an ICT session strategy
measured **+39 points over eight years** that way, and **+7** once the stamps
were read correctly.

```python
from tools.backtest_lab import verify_timezone
winter = verify_timezone(suspect_january, known_january)   # (minutes, correlation)
summer = verify_timezone(suspect_july,    known_july)
```

Reading the result:

- **The two offsets differ → the feed is local time with DST.** Do not apply a
  flat offset; read it as a zone.
- The two agree → a fixed offset is safe.
- Correlation should land near 0.99 at the right offset and nowhere else. If
  nothing peaks sharply, you have not established the zone — stop.

Two ways this check gets done wrong. **Compare returns, not levels**: two
vendors quoting one index differ by a basis that swamps a level comparison,
while their minute returns align at 0.99 and only at the right offset —
`verify_timezone` already takes `pct_change` for you, so feed it close prices.
And **do not infer the zone from a volume profile** — that was tried here and
was ambiguous.

oanda's exports, checked this way, are UTC.

## Check 2 — clear the disagreement between two vendors

A result means nothing until it exceeds the gap between two vendors of the same
instrument. Running one strategy on the S&P from two feeds gave **+50bp and
−234bp over the same eight years** — while correlating **0.93** year over year.
Both feeds agreed about the shape of every year and disagreed about the sign of
the total. The gap averaged ~35bp/year against a measured edge of ~6bp/year.

```python
from tools.backtest_lab import noise_floor
print(noise_floor(results_vendor_a, results_vendor_b))
# edge <total>bp vs vendor gap <mean>+/-<stdev>bp (corr <r>) -- clears|INSIDE the noise floor
```

Both arguments map a period (a year, say) to a `Result`. Note what the verdict
actually compares: `edge` is the **sum** across periods, `mean_gap` is the
**per-period mean**, and `clears` is one-sided —
`abs(edge) > abs(mean_gap) + gap_stdev`. Read the printed numbers, not just the
verdict.

- **Clearing it is necessary and nowhere near sufficient.** It rules out one
  failure mode. It does not make the strategy real.
- **A high correlation with a large mean gap is the dangerous case**, and the
  one actually seen. The feeds look like they agree. Never read correlation on
  its own as agreement — read the gap.
- Fewer than two shared periods and it raises. That is correct; do not work
  around it with a single-period comparison.

## Two habits that follow

**Basis points of price, never raw points.** Ten points on \$70 oil and ten on
a 20,000 index are not the same trade, and summing points across instruments
produces a number dominated by whichever quote is largest. `Result.bps` is
there for this.

**Charge costs, always.** The strategy above is gross-positive and
net-negative, so a frictionless run measures nothing tradeable.

## One more thing that silently degrades

VWAP-family strategies on a feed with no volume degrade to TWAP without
erroring — histdata's index CFDs are the case here. `tools/vwap_lab.py` warns;
if you are reading volume yourself, check it is not all zeros.

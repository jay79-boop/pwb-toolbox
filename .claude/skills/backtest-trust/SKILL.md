---
name: backtest-trust
description: How to actually run the two checks that must pass before a backtest number in this repo is believed or quoted — `verify_timezone` across a winter AND a summer month, then `noise_floor()` against a second vendor — and how to read what each prints. Load whenever a strategy reports a result, before quoting a backtest figure, or when reading a new vendor's price file for the first time.
---

# Before you believe a backtest number

Two failure modes produced confidently wrong answers in this repo, and neither
announces itself. **Why** they matter — the incidents, the numbers, the fact
that both are pinned by `tests/test_backtest_lab.py` — is in
`docs/backtesting.md`, under "two things that produced confidently wrong
answers". Read that once. This is how to run the checks.

Run both. Quote both outcomes alongside the number, or do not quote the number.

## Check 1 — the zone, in two different months

```python
from tools.backtest_lab import verify_timezone
winter = verify_timezone(suspect_january, known_january)   # -> (minutes, correlation)
summer = verify_timezone(suspect_july,    known_july)
```

Both arguments are **close-price series indexed by time**; `verify_timezone`
takes `pct_change` internally, so hand it prices, not returns.

Reading the pair:

- **The two offsets differ → the feed is local time with DST.** Do not apply a
  flat offset; read it as a zone. This is the whole point of running it twice.
- The two agree → a fixed offset is safe.
- Correlation should peak near 0.99 at the right offset and nowhere else. **If
  nothing peaks sharply you have not established the zone — stop**, rather than
  taking the argmax of a flat curve.
- Fewer than 100 overlapping non-zero bars at a candidate offset and it skips
  that offset silently. A suspiciously narrow search space means thin overlap.

Two ways this gets done wrong: comparing **levels** instead of returns (two
vendors quoting one index differ by a basis that swamps it), and inferring the
zone from a **volume profile** — that was tried here and was ambiguous.

Known so far: histdata ASCII M1 is New York local *with DST*; oanda is UTC.

## Check 2 — clear the vendor disagreement

```python
from tools.backtest_lab import noise_floor
print(noise_floor(results_vendor_a, results_vendor_b))
# edge <total>bp vs vendor gap <mean>+/-<stdev>bp (corr <r>) -- clears|INSIDE the noise floor
```

Both arguments map a period (a year, say) to a `Result`. Note what the verdict
actually compares: `edge` is the **sum** across periods while `mean_gap` is the
**per-period mean**, and `clears` is one-sided —
`abs(edge) > abs(mean_gap) + gap_stdev`. Read the printed numbers, not just the
verdict.

- **Clearing it is necessary and nowhere near sufficient.** It rules out one
  failure mode. It does not make the strategy real.
- **A high correlation with a large mean gap is the dangerous case**, and the
  one actually seen. Never read correlation on its own as agreement — read the
  gap. Two feeds can agree about the shape of every year and disagree about the
  sign of the total.
- Fewer than two shared periods raises. That is correct — do not work around it
  by comparing a single period.

## Two habits, always

**Basis points of price, never raw points.** `Result.bps` exists for this: ten
points on \$70 oil and ten on a 20,000 index are not the same trade, and summing
points across instruments produces a number dominated by the largest quote.

**Charge costs.** A frictionless run measures nothing tradeable — the strategy
behind these lessons is gross-positive and net-negative.

## One thing that degrades silently

VWAP-family strategies on a feed carrying no volume fall back to TWAP without
erroring — histdata's index CFDs are the case here. `tools/vwap_lab.py` warns;
if you are reading volume yourself, check it is not all zeros.

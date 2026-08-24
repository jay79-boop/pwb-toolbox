# The stream's second haul: the night, the timeframe, the model

*Decided 2026-08-24.*
**Decision:** Three more things from the same live stream, chosen by the
owner from a ranked list. All three are measurements of things we were
already assuming, and none of them needed a new nightly command — each folds
into a job the scheduled task already runs.
**1. Overnight vs intraday.** The "holy grail" the room laughed about is a
published anomaly: most of the US index's long-run return accrued between
the close and the next open. `season_scan report` now splits every ticker's
record that way — close-to-open plus open-to-close is exactly the day's
return, so the two always reconcile — and gates the difference with a
**paired sign-flip null**: each day's two halves are a matched pair, so the
null flips their labels, never the values. Flipping labels keeps each day's
own magnitude attached to that day, which is what lets volatility clustering
survive into the null. Costs are charged *per day*, because an overnight-only
position crosses the spread every session, and `top_share` reports how much
of the whole overnight total came from its best handful of nights — an
effect delivered by five gaps is a lottery ticket with a good average. The
fetch now writes open/high/low beside the close, so an older close-only file
reports the split as unavailable rather than guessing.
**2. Timeframe fragility.** The stream demonstrated it live when a strategy
moved from 15m to 5m and died. `fragility_sweep` now sweeps bar size next to
rr and sma-length, resampling on a grid **anchored at the opening candle** —
a grid counted from midnight puts 30-minute bars at :00 and :30, the 09:15
candle stops existing, and every session is skipped as "no candle 1", which
is a silent zero-trade backtest rather than an error. 15m is the finest
Yahoo serves at this depth, so only the coarse side is testable and the
sweep says so instead of implying a plateau.
**3. Calibration audit (`tools/calibration_audit.py`).** When Black-Scholes
said 30% touch, how often did it actually touch? Barriers are placed in
sigma units, which makes the model probability identical across a row and
therefore the test exactly binomial; windows never overlap; trailing
volatility is scored against a static benchmark so the estimate itself is
priced. **The window's own realized volatility is deliberately not a mode**:
dividing a move by the volatility that move helped produce is
self-normalization and would report a fat-tailed series as thin-tailed.
**What the audit found on synthetic data, and expect on real:** near touch
barriers under-hit their model. The reflection principle assumes continuous
monitoring and a daily bar is not continuous, so a half-sigma level that a
path crosses and recrosses inside one session is touched less often than
2xP(finish) predicts. That is measurement resolution, not a discovery — the
far rows are where a genuine fat tail shows up. Written into the tool's
docstring so it is not rediscovered as a finding.
**Not touched here:** the Current State block. Other open branches were
already rewriting it, and this branch conflicted with `main` on that block
twice while it was open — the exact defect the next entry names. A fifth
rewrite would have added nothing but a third conflict.

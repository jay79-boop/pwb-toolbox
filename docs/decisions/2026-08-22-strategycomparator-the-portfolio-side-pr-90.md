# StrategyComparator: the portfolio side (PR #90)

*Decided 2026-08-22.*
**Decision:** A reusable harness, `pwb_toolbox.backtesting.comparator`, that runs
several strategies over identical data and reports how they interact.
**How:** `StrategyComparator` runs each registered strategy, extracts its NAV
series, computes per-strategy metrics through `pwb_toolbox.performance`, builds a
weighted portfolio NAV, and returns the correlation matrix alongside both sets of
metrics. 18 unit tests drive it on synthetic data.
**Why it is not the backtest lab (PR #87):** they answer different questions on
different axes. The lab takes *one* strategy across many instruments and two
vendor feeds and asks whether its edge survives the data. The comparator takes
*many* strategies over one dataset and asks whether they hedge each other or
amplify. The lab decides whether an edge is real; the comparator decides whether
several real ones belong in the same account. This entry was originally filed
under "(PR #87)", which is how the two came to look like duplicated work.

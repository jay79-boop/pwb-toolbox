# Seasonality lab: calendar rotation measured, not remembered

*Decided 2026-08-23.*
**Decision:** Add `tools/season_scan.py` — batch seasonal analysis over the
sector ETFs, index baselines, crypto and the owner's own names, with a
three-gate evidence standard: within-year permutation test (2,000
reshuffles), split-half agreement across the years, and BH-FDR across the
scanned grid. Almanac folklore is pre-registered and judged one-sided on
its own terms, verdicts published HELD/FAILED. Deliverables: self-contained
visual report, sectioned TradingView watchlist (manual re-import;
TradingView cannot auto-sync a file), `season.json` + `context` for other
tools.
**Why the gates:** 15 tickers x 12 months is 180 casts; ~9 "patterns"
appear by luck alone. The failed-folklore list is deliberately a first-class
output — the things to stop believing are worth as much as the things that
held.
**A null-hypothesis bug worth remembering:** the first permutation null
circularly shifted the monthly series. Under a 12-periodic window mask that
collapses the null to eleven distinct values (and shifts by multiples of 12
realign the calendar entirely), so a merely top-ranked month read as
p=0.0005. The shipped null shuffles months within each year — volatility
regimes survive, alignment dies, and pure noise convicts at the expected
~5%/0% rates (checked in tests). A second resolution trap surfaced on the
first real run: at 204 cells the rank-1 BH bar (q/n=0.00049) sits below the
smallest p 2,000 permutations can produce (0.0005), mathematically blocking
a lone true pattern — so the permutation count now scales with the grid
(`needed_permutations`, also pinned by tests).

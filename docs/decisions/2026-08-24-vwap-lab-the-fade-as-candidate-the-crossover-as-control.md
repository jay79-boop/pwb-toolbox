# VWAP lab: the fade as candidate, the crossover as control (PR #111)

*Decided 2026-08-24.*
**Decision:** Add the VWAP strategy family — `pwb_toolbox/backtesting/vwap.py`
(`SessionVwap` indicator with volume-weighted σ bands; `VwapStrategy` with
three setups), `tools/vwap_lab.py` (driver through `backtest_lab`: costs
charged, bps-normalised, two-vendor noise floor per setup), and
`pine/vwap_strategy.pine` (the TradingView reading of the same rules). A
sourced VWAP section joins `docs/trading-wisdom.md` via this PR under the
propose-then-approve contract.
**Why these setups:** VWAP's peer-reviewed pedigree is execution benchmarking
(Berkowitz/Logue/Noser 1988) — institutions are graded against it, so real
order flow sits at the level. The practitioner evidence points at band-fade
mean reversion (~60–63% win rates at ±2σ in liquid names; dominant in the
largest published parameter sweep) and at the crossover being worthless
(zero significant configurations in the same sweep). So fade and pullback
are the candidates and **the crossover ships as a control expected to fail**
— the lab prints a warning if the control comes out positive, because that
is evidence about the harness, not the market.
**Confirms:** relative volume, first-30-minute day-type (market intraday
momentum, Gao/Han/Li/Zhou JFE 2018), MA side gate, RSI as a
folklore-measuring control. Each off by default so every gate's cost is
measurable separately.
**Honesty notes baked in:** zero-volume feeds (histdata index CFDs) degrade
VWAP to TWAP and the lab says so; the crypto session anchor (UTC midnight)
is a convention, not a fact; no number is believed until it clears the
vendor noise floor, and clearing it is necessary, nowhere near sufficient.

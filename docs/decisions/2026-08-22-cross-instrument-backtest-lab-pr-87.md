# Cross-Instrument Backtest Lab (PR #87)

*Decided 2026-08-22.*
**Decision:** Build a harness to test all three strategies side-by-side on identical data, with full correlation and diversification analysis.
**Why:** Can't compare strategies in a vacuum. Need to see: Do they hedge each other? Do they amplify losses? What's the portfolio win rate vs. individual strategy win rates?
**What it measures:** Win rate, Sharpe ratio, max drawdown, correlation, portfolio cumulative return, monthly breakdown.
**Target:** Identify if 15-Min Reversal adds value to ICT AM/OB, or if they're too correlated.

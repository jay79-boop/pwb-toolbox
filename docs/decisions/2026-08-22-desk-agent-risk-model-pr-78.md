# Desk Agent + Risk Model (PR #78)

*Decided 2026-08-22.*
**Decision:** Build an agent that enforces position limits, exposure caps, and live risk alerts across all three strategies.
**Why:** Live execution will have real consequences. Can't trade three correlated strategies if position sizes aren't coordinated. Need automatic stops and alerts.
**What it does:** Reads live positions from IB, calculates portfolio Greeks, enforces per-strategy position caps, enforces max portfolio exposure, alerts on margin usage >80%.
**Dependency:** Needs backtest lab results to set appropriate position sizing rules.

# Speculative desk: walled-off high-risk paper pot ("trade spicy")

*Decided 2026-08-22.*
**Decision:** Add a second, deliberately speculative track beside the core
program: `tools/spec_desk.py` (ledger + rules) and `docs/spec-desk.md` (agent
protocol). Four lanes — 15–45 DTE option buys, 0–7 DTE lotteries (sub-capped
at 2.5%), momentum stocks, defined-risk credit spreads. Fixed pot as a slice
of the paper account; per-trade max loss 10% of pot; 4 positions max; spent
pot locks the desk until `review` runs; refill only after review. Owner
executes every order (options in thinkorswim paperMoney — TradingView has no
options; stocks/crypto in TradingView paper); agent plans, logs, watches
(`check` alerts on stop/target), and reviews. Two triggers: "trade spicy" on
demand, plus a Windows-scheduled morning scan.
**Why:** The owner wants high-risk/high-reward speculation *and* steady safe
core growth. The wall is the design: the spec pot can die without touching
core statistics, and its scored record (R-multiples per lane) is the desk's
real product — after 30 trades a lane either proves expectancy or gets a
pause proposal. Self-learning follows the wisdom-doc contract: the agent
drafts changes from the record; the owner approves.

# TradingAgents

`tools/trading_agents.py` is a thin CLI over
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0): a multi-agent LLM framework where fundamental/sentiment/technical
analysts debate, bull and bear traders argue it out, and a risk-management
layer produces a written decision for one ticker on one date.

## What this integration is, and is not

It is a **standalone signal generator**. `analyze TICKER` writes one JSON
file to `trading_agents/<ticker>_<date>.json` and stops. It is not wired into
`spec_desk`, the desk agent, or anything that can reach a broker — nothing in
this repo reads that directory automatically, and nothing here ever will
without a separate, explicit decision. TradingAgents' own decision output
(BUY/SELL/HOLD) is logged as a proposal, exactly like every other AI-generated
number in this repo: a human reads it before anything happens.

That restraint is not a formality — it is `CLAUDE.md`'s hardest rule, and
TradingAgents' own agents do not know about it. Its analysts can call a
Backtrader-driving broker layer if you configure one; this wrapper never
constructs that layer and never calls anything beyond `.propagate()`.

## Installing it

Not in `requirements.txt` — its dependency stack (`langchain-core`,
`langchain-anthropic`, `langchain-openai`, `langchain-google-genai`,
`langgraph`, a `redis` client, `backtrader`, `yfinance`, ...) is heavy, and
nothing else here needs it. Installing it for every session and every CI run
would tax the whole repo for a tool most sessions never touch, which is the
opposite of this repo's lean-dependency convention. Install it deliberately:

```bash
pip install -r requirements-tradingagents.txt
```

Then set an LLM provider key as an environment variable — TradingAgents reads
these itself, not through this repo's `.env` loader:

- `OPENAI_API_KEY` (default provider), or
- `ANTHROPIC_API_KEY`, or one of the other providers its README lists.

It also fetches live price/news data (yfinance by default; Alpha Vantage,
FRED and Polymarket are configurable alternatives, each needing its own key
if used) — so a run needs outbound network access as well as a key.

## Running it

```bash
python tools/trading_agents.py analyze NVDA --date 2026-09-10
# wrote trading_agents/NVDA_2026-09-10.json
```

`--date` defaults to today. The debate can take several minutes and several
dollars of LLM spend per run — see TradingAgents' own README for its
`TRADINGAGENTS_*` env vars (model choice, debate rounds, token caps) before
running it unattended or in a loop.

## What was verified, and what was not

Verified from a cloud session with no LLM key and no TradingAgents-specific
network allowance: `pip install -r requirements-tradingagents.txt` builds and
installs cleanly (confirmed 2026-09-11 against commit `be952b8`), and
`tools/trading_agents.py`'s own logic — argument handling, the record it
writes, the fact that it never touches anything beyond
`TradingAgentsGraph.propagate()` — is covered by `tests/test_trading_agents.py`
against a fake graph, no network or key required.

**Not verified**: an actual end-to-end run against a real LLM provider.
Cloud sessions here carry no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` for
third-party use, so the first real run — and the first real look at what a
decision actually reads like — has to happen wherever the key lives.

#!/usr/bin/env python
"""Standalone CLI over TauricResearch/TradingAgents (Apache-2.0), installed
optionally via ``requirements-tradingagents.txt`` — not part of
``requirements.txt`` because its dependency stack (langchain, langgraph, a
redis client) is heavy and nothing else here needs it. Install it, then::

    python tools/trading_agents.py analyze NVDA --date 2026-09-10

TradingAgents runs a multi-agent LLM debate (fundamentals/sentiment/technical
analysts, then bull/bear traders, then risk management) and returns a written
decision. It needs an LLM provider key (``OPENAI_API_KEY``,
``ANTHROPIC_API_KEY``, ... — see its README) and network access to fetch
price/news data; neither is assumed to exist here.

This wrapper does exactly one thing beyond calling it: writes the result to
``trading_agents/`` as a **signal only**. It never places, modifies, or
proposes placing an order, and it is not wired into ``spec_desk`` or any
other tool that can reach a broker. Reading the decision and deciding what
to do with it stays a human step.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "trading_agents"

INSTALL_HINT = (
    "TradingAgents is not installed. It's an optional dependency:\n"
    "    pip install -r requirements-tradingagents.txt\n"
    "then set an LLM provider key (e.g. OPENAI_API_KEY or ANTHROPIC_API_KEY)\n"
    "before running this command again. See docs/trading-agents.md."
)


def _today() -> str:
    return _dt.date.today().isoformat()


def build_record(
    ticker: str, date: str, decision: Any, generated_at: str
) -> dict[str, Any]:
    """Pure: shape the result into the record this tool writes. No I/O, no network."""
    return {
        "ticker": ticker,
        "date": date,
        "generated_at": generated_at,
        "decision": decision if isinstance(decision, str) else str(decision),
        "signal_only": True,
        "note": (
            "TradingAgents proposal, not an order. Nothing in this repo acts "
            "on this file automatically."
        ),
    }


def write_record(record: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = record["ticker"].replace("/", "_")
    path = out_dir / f"{safe_ticker}_{record['date']}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def _default_graph_factory():
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
    except ImportError as exc:
        raise SystemExit(INSTALL_HINT) from exc
    return TradingAgentsGraph(config=DEFAULT_CONFIG.copy())


def run_analysis(
    ticker: str,
    date: str,
    out_dir: Path = OUT_DIR,
    graph_factory: Callable[[], Any] | None = None,
    clock: Callable[[], str] = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(),
) -> Path:
    """Dirty edge: builds the (possibly real) graph, calls it, writes the record.

    ``graph_factory`` and ``clock`` are injected so tests exercise this without
    a network call, an API key, or a real clock. ``graph_factory`` is resolved
    by module-level name rather than bound as a default argument, so tests can
    monkeypatch ``_default_graph_factory`` after import.
    """
    graph = (graph_factory or _default_graph_factory)()
    _, decision = graph.propagate(ticker, date)
    record = build_record(ticker, date, decision, clock())
    return write_record(record, out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze", help="run the TradingAgents debate on one ticker and log the result"
    )
    analyze.add_argument("ticker")
    analyze.add_argument(
        "--date",
        default=None,
        help="YYYY-MM-DD to analyze as-of. Default: today.",
    )

    args = parser.parse_args(argv)

    if args.command == "analyze":
        date = args.date or _today()
        path = run_analysis(args.ticker, date)
        print(f"wrote {path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

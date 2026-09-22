"""tools/trading_agents.py — no network, no API key, no TradingAgents install
required. The real ``TradingAgentsGraph`` is replaced with a fake via the
injected ``graph_factory``, per the house rule that nothing here calls a
live LLM or a live data vendor in a test."""

import json

from tools import trading_agents


class FakeGraph:
    def __init__(self, decision="BUY"):
        self.decision = decision
        self.calls = []

    def propagate(self, ticker, date):
        self.calls.append((ticker, date))
        return ({"final": self.decision}, self.decision)


def test_build_record_is_pure_and_marks_signal_only():
    record = trading_agents.build_record(
        "NVDA", "2026-09-10", "HOLD", "2026-09-10T00:00:00+00:00"
    )
    assert record == {
        "ticker": "NVDA",
        "date": "2026-09-10",
        "generated_at": "2026-09-10T00:00:00+00:00",
        "decision": "HOLD",
        "signal_only": True,
        "note": (
            "TradingAgents proposal, not an order. Nothing in this repo acts "
            "on this file automatically."
        ),
    }


def test_build_record_stringifies_non_string_decisions():
    record = trading_agents.build_record(
        "NVDA", "2026-09-10", {"action": "SELL"}, "now"
    )
    assert record["decision"] == "{'action': 'SELL'}"


def test_write_record_creates_out_dir_and_names_file_by_ticker_and_date(tmp_path):
    record = trading_agents.build_record("BTC/USD", "2026-09-10", "SELL", "now")
    path = trading_agents.write_record(record, tmp_path)
    assert path == tmp_path / "BTC_USD_2026-09-10.json"
    assert json.loads(path.read_text()) == record


def test_run_analysis_calls_the_injected_graph_and_writes_its_decision(tmp_path):
    fake = FakeGraph(decision="BUY")
    path = trading_agents.run_analysis(
        "NVDA",
        "2026-09-10",
        out_dir=tmp_path,
        graph_factory=lambda: fake,
        clock=lambda: "2026-09-10T12:00:00+00:00",
    )
    assert fake.calls == [("NVDA", "2026-09-10")]
    record = json.loads(path.read_text())
    assert record["decision"] == "BUY"
    assert record["signal_only"] is True


def test_run_analysis_never_touches_a_broker_or_places_anything(tmp_path):
    """The wrapper's only side effect is a JSON file. Asserted by construction:
    FakeGraph exposes no order/broker method, so a wrapper that tried to call
    one would raise AttributeError here rather than pass silently."""
    fake = FakeGraph()
    trading_agents.run_analysis(
        "SPY",
        "2026-09-10",
        out_dir=tmp_path,
        graph_factory=lambda: fake,
        clock=lambda: "now",
    )
    assert not hasattr(fake, "order_placed")


def test_main_defaults_date_to_today(tmp_path, monkeypatch):
    fake = FakeGraph(decision="HOLD")
    monkeypatch.setattr(trading_agents, "_today", lambda: "2099-01-01")
    monkeypatch.setattr(trading_agents, "OUT_DIR", tmp_path)
    monkeypatch.setattr(trading_agents, "_default_graph_factory", lambda: fake)
    rc = trading_agents.main(["analyze", "NVDA"])
    assert rc == 0
    assert fake.calls == [("NVDA", "2099-01-01")]


def test_missing_install_raises_actionable_error(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("tradingagents"):
            raise ImportError("no module named tradingagents")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        trading_agents._default_graph_factory()
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "requirements-tradingagents.txt" in str(exc)

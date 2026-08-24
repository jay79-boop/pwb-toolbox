"""Tests for the Strategy Lab: the run store, the API, and the dashboard's math.

Three jobs here.

The store and server tests pin the contract every future producer writes against —
a record that validates today must validate tomorrow, and a bad one must be
refused with a message that says which field was wrong rather than a bare 400.

The last two reconcile the dashboard's JavaScript with the Python it sits beside.
`static/strategy-lab-stats.js` exists because the page has to open from
``file://`` with no build step, which leaves two implementations of hit rate and
profit factor in one repository — safe only if something keeps them honest. This
does, the same way ``tests/test_option_lab.py`` does for the options math.

No network: the server tests bind an ephemeral port on loopback.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "static", "strategy-lab-stats.test.js")
sys.path.insert(0, ROOT)

from pwb_toolbox.performance import trade_stats  # noqa: E402
from tools.strategy_lab import record as rec  # noqa: E402
from tools.strategy_lab.server import build_server, page_html  # noqa: E402
from tools.strategy_lab.store import (  # noqa: E402
    SCHEMA,
    RunStore,
    ValidationError,
    validate,
)

ET = ZoneInfo("America/New_York")


def a_trade(day="2026-08-17", r=2.4, direction=1):
    return {
        "day": day,
        "direction": direction,
        "entry_ts": f"{day}T09:45",
        "exit_ts": f"{day}T10:30",
        "entry": 100.0,
        "exit": 106.0,
        "target": 106.0,
        "stop": 97.5,
        "reason": "target",
        "r": r,
        "points": r * 10,
    }


def a_record(**over):
    base = {
        "schema": SCHEMA,
        "id": "test-run",
        "strategy": "15-Minute Reversal",
        "trades": [a_trade()],
    }
    base.update(over)
    return base


# ───────────────────────────────────────────────────────────── validation
def test_a_good_record_survives_validation():
    out = validate(a_record(symbol="CME_MINI:NQ1!", point_value=20.0))
    assert out["id"] == "test-run"
    assert out["trades"][0]["r"] == pytest.approx(2.4)
    assert out["point_value"] == 20.0


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"schema": "something/else"}, "schema"),
        ({"id": "../../etc/passwd"}, "id"),
        ({"id": ""}, "id"),
        ({"strategy": ""}, "strategy"),
        ({"trades": "not a list"}, "trades"),
    ],
)
def test_bad_records_are_refused_by_field(bad, message):
    with pytest.raises(ValidationError) as exc:
        validate(a_record(**bad))
    assert message in str(exc.value)


def test_a_trade_must_carry_a_direction_and_an_r():
    with pytest.raises(ValidationError, match="direction"):
        validate(a_record(trades=[dict(a_trade(), direction=0)]))
    with pytest.raises(ValidationError, match=r"trades\[0\]\.r"):
        validate(a_record(trades=[{k: v for k, v in a_trade().items() if k != "r"}]))


def test_non_finite_numbers_are_refused():
    # NaN and Infinity round-trip through json.loads and then poison every
    # average downstream, which surfaces as a blank dashboard, not an error.
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValidationError, match="finite"):
            validate(a_record(trades=[dict(a_trade(), r=value)]))


def test_control_characters_are_stripped_not_rejected():
    out = validate(a_record(strategy="Rev\x00ersal​"))
    assert "\x00" not in out["strategy"]


def test_unknown_keys_are_dropped_so_a_newer_producer_still_loads():
    out = validate(a_record(some_future_field={"deeply": ["nested"]}))
    assert "some_future_field" not in out
    assert out["strategy"] == "15-Minute Reversal"


# ────────────────────────────────────────────────────────────────── store
def test_store_round_trip(tmp_path):
    store = RunStore(tmp_path)
    store.save(a_record(id="alpha"))
    assert store.ids() == ["alpha"]
    assert store.get("alpha")["strategy"] == "15-Minute Reversal"
    assert store.get("missing") is None
    assert store.delete("alpha") is True
    assert store.delete("alpha") is False


def test_index_omits_trade_rows_but_counts_them(tmp_path):
    store = RunStore(tmp_path)
    store.save(a_record(id="alpha", trades=[a_trade(), a_trade(r=-1.0)]))
    (entry,) = store.index()
    assert entry["trade_count"] == 2
    assert "trades" not in entry


def test_runs_are_listed_most_recently_recorded_first(tmp_path):
    store = RunStore(tmp_path)
    store.save(a_record(id="older", recorded_at="2026-08-01T10:00:00+00:00"))
    store.save(a_record(id="newer", recorded_at="2026-08-20T10:00:00+00:00"))
    assert [r["id"] for r in store.all()] == ["newer", "older"]


def test_a_corrupt_file_does_not_take_the_dashboard_down(tmp_path):
    store = RunStore(tmp_path)
    store.save(a_record(id="good"))
    (tmp_path / "rotten.json").write_text("{not json at all", encoding="utf-8")
    assert [r["id"] for r in store.all()] == ["good"]


def test_a_traversing_id_cannot_escape_the_directory(tmp_path):
    store = RunStore(tmp_path)
    with pytest.raises(ValidationError):
        store.delete("../escape")


# ───────────────────────────────────────────────────────────────── record
def test_point_value_reads_the_longest_root_first():
    # MNQ is a tenth of NQ; matching the shorter root first is a 10x error in
    # every dollar figure the dashboard prints.
    assert rec.point_value_for("CME_MINI:MNQ1!") == 2.0
    assert rec.point_value_for("CME_MINI:NQ1!") == 20.0
    assert rec.point_value_for("AAPL") is None
    assert rec.point_value_for(None) is None


def test_the_same_run_twice_keeps_one_id_but_a_changed_param_makes_another():
    trades = [a_trade()]
    one = rec.build("Rev", trades, params={"rr": 2.4})
    same = rec.build("Rev", trades, params={"rr": 2.4})
    other = rec.build("Rev", trades, params={"rr": 2.0})
    assert one["id"] == same["id"]
    assert one["id"] != other["id"]


def test_a_changed_trade_list_is_a_different_run():
    assert (
        rec.build("Rev", [a_trade()])["id"]
        != rec.build("Rev", [a_trade(), a_trade(r=-1.0)])["id"]
    )


def test_records_built_from_the_simulator_carry_the_funnel():
    from tools.reversal_15m_sim import Bar, Config, simulate

    def bar(hhmm, o, h, lo, c):
        hour, minute = (int(x) for x in hhmm.split(":"))
        return Bar(
            datetime.combine(date(2026, 8, 17), time(hour, minute), ET), o, h, lo, c
        )

    bars = [
        bar("09:15", 95, 100, 90, 95),
        bar("09:30", 92, 95, 88, 94),
        bar("09:45", 93.5, 96, 93, 95),
        bar("10:00", 95, 101, 95, 100.5),
    ]
    # Gross, so this stays a test of the record contract rather than of cost
    # policy; the 1bp default is pinned by test_reversal_15m_sim.py instead.
    cfg = Config(cost_bps=0.0)
    results = simulate(bars, cfg, sma={date(2026, 8, 17): 50.0})
    out = rec.from_reversal_sim(results, cfg, symbol="CME_MINI:NQ1!")

    assert out["funnel"] == {
        "days": 1,
        "days_with_candle_1": 1,
        "days_committed": 1,
        "trades": 1,
    }
    assert out["point_value"] == 20.0
    assert out["params"]["reward_risk"] == 2.4
    assert out["trades"][0]["r"] == pytest.approx(2.4)


# ───────────────────────────────────────────────────────────────── the page
def test_the_page_is_wrapped_into_a_real_document():
    html = page_html()
    assert html.startswith("<!doctype html>")
    assert html.index("</style>") < html.index("<body>")
    assert html.count("<body>") == 1


def test_the_stats_module_is_inlined_exactly_once():
    html = page_html()
    assert "__STRATEGY_LAB_STATS__" not in html
    assert "StrategyLabStats" in html
    # One copy of the arithmetic, or the served page and the module can disagree.
    assert html.count("function equityCurve") == 1


# ───────────────────────────────────────────────────────────────── the server
@pytest.fixture
def lab(tmp_path):
    store = RunStore(tmp_path)
    httpd = build_server(store, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, store
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def call(url, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method=method
    )
    # The container routes through a proxy by default; a loopback call must not.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_posting_a_run_makes_it_readable(lab):
    base, _ = lab
    status, reply = call(f"{base}/api/runs", a_record(id="alpha"))
    assert (status, reply["trades"]) == (201, 1)

    status, index = call(f"{base}/api/runs")
    assert status == 200 and [r["id"] for r in index["runs"]] == ["alpha"]

    status, full = call(f"{base}/api/runs/alpha")
    assert status == 200 and full["trades"][0]["r"] == pytest.approx(2.4)


def test_a_bad_run_is_refused_with_the_offending_field(lab):
    base, store = lab
    status, reply = call(f"{base}/api/runs", a_record(schema="wrong/1"))
    assert status == 400
    assert "schema" in reply["error"]
    assert store.ids() == []


def test_unknown_routes_and_runs_are_404(lab):
    base, _ = lab
    assert call(f"{base}/api/nope")[0] == 404
    assert call(f"{base}/api/runs/ghost")[0] == 404


def test_deleting_a_run(lab):
    base, store = lab
    call(f"{base}/api/runs", a_record(id="alpha"))
    assert call(f"{base}/api/runs/alpha", method="DELETE") == (200, {"deleted": True})
    assert store.ids() == []


def test_the_root_serves_the_dashboard_told_it_is_live(lab):
    base, _ = lab
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{base}/", timeout=10) as r:
        html = r.read().decode()
    assert r.status == 200
    assert 'name="strategy-lab-api"' in html
    assert "Strategy Lab" in html


# ──────────────────────────────────────────── the JavaScript, and its agreement
def _node():
    exe = shutil.which("node")
    if not exe:
        pytest.skip("node is not installed")
    return exe


def test_javascript_suite_passes():
    out = subprocess.run(
        [_node(), SUITE], capture_output=True, text=True, cwd=ROOT, timeout=120
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_dashboard_stats_agree_with_pwb_toolbox_performance():
    """The page and the package must not disagree about the same trades.

    `pwb_toolbox.performance.trade_stats` is the authority — it is what the
    backtests use. The dashboard recomputes the same figures in JavaScript so it
    can run from a file with no Python; this requires the two to match.
    """
    node = _node()
    trades = [
        a_trade(r=2.4),
        a_trade(r=-1.0),
        a_trade(r=-1.0),
        a_trade(r=2.4),
        a_trade(r=-1.0),
        a_trade(r=-1.35),
        a_trade(r=2.4),
    ]

    script = (
        "const S=require('./static/strategy-lab-stats.js');"
        "const t=JSON.parse(process.argv[1]);"
        "const s=S.summarize(t);"
        "console.log(JSON.stringify({winRate:s.winRate,"
        "profitFactor:s.profitFactor,expectancy:s.expectancyR,"
        "avgWin:s.avgWinR,avgLoss:s.avgLossR}));"
    )
    out = subprocess.run(
        [node, "-e", script, json.dumps(trades)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)

    # trade_stats keys the outcome as "return"; the dashboard calls it "r".
    py_trades = [{"return": t["r"]} for t in trades]
    py_win, py_loss = trade_stats.average_win_loss(py_trades)

    assert js["winRate"] == pytest.approx(trade_stats.hit_rate(py_trades), abs=1e-12)
    assert js["profitFactor"] == pytest.approx(
        trade_stats.profit_factor(py_trades), abs=1e-12
    )
    assert js["expectancy"] == pytest.approx(
        trade_stats.expectancy(py_trades), abs=1e-12
    )
    assert js["avgWin"] == pytest.approx(py_win, abs=1e-12)
    assert js["avgLoss"] == pytest.approx(py_loss, abs=1e-12)

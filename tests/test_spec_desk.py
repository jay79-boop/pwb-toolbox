"""spec_desk's rules engine — the wall around the speculative pot.

The contract: caps refuse oversized trades, the lottery lane is sub-capped,
a spent pot locks the desk, refills require a review newer than the last
close, and alert logic fires on the right side of stop/target for both
directions. All pure-ledger; no network, no filesystem beyond tmp_path.
"""

import pytest

from tools.spec_desk import (
    MAX_OPEN,
    add_trade,
    can_refill,
    check_alerts,
    close_trade,
    equity,
    lane_stats,
    new_ledger,
    now_iso,
    open_risk,
    validate_open,
)


def plan(**over):
    base = dict(
        lane="swing-buy",
        symbol="NVDA",
        instrument="NVDA 02OCT26 190C",
        venue="paperMoney",
        direction="long",
        qty=2,
        entry=4.0,
        multiplier=100,
        max_loss=800.0,
        stop=176.0,
        target=198.0,
        thesis="test",
    )
    base.update(over)
    return base


def test_open_within_cap_and_equity_math():
    led = new_ledger(10_000)
    t = add_trade(led, **plan())
    assert t["id"] == "T1" and t["status"] == "open"
    assert open_risk(led) == 800.0
    assert equity(led) == 10_000  # nothing realized yet


def test_per_trade_cap_refuses_oversize():
    led = new_ledger(10_000)
    reason = validate_open(led, "swing-buy", 1_001.0)  # cap is 10% = 1000
    assert reason is not None and "cap" in reason
    with pytest.raises(ValueError, match="cap"):
        add_trade(led, **plan(max_loss=1_001.0))


def test_short_dte_sub_cap():
    led = new_ledger(10_000)
    assert validate_open(led, "short-dte", 250.0) is None  # 2.5% exactly
    assert "cap" in validate_open(led, "short-dte", 300.0)


def test_max_concurrent_positions():
    led = new_ledger(100_000)
    for _ in range(MAX_OPEN):
        add_trade(led, **plan(max_loss=500.0))
    assert "open" in validate_open(led, "swing-buy", 500.0)


def test_committed_risk_cannot_exceed_equity():
    led = new_ledger(3_000)
    add_trade(led, **plan(max_loss=300.0))
    add_trade(led, **plan(max_loss=300.0))
    # equity 3000, at risk 600; a 300 cap-legal trade still fits
    assert validate_open(led, "swing-buy", 300.0) is None
    # but not one that would push committed risk past equity
    led2 = new_ledger(1_000)
    add_trade(led2, **plan(max_loss=100.0, lane="swing-buy"))
    assert "uncommitted" in validate_open(
        led2, "swing-buy", 950.0
    ) or "cap" in validate_open(led2, "swing-buy", 950.0)


def test_close_scores_r_multiple():
    led = new_ledger(10_000)
    t = add_trade(led, **plan())
    done = close_trade(led, t["id"], exit_price=8.0)  # (8-4)*2*100 = +800
    assert done["pnl"] == 800.0
    assert done["r_multiple"] == pytest.approx(1.0)
    assert equity(led) == 10_800
    with pytest.raises(ValueError, match="already closed"):
        close_trade(led, t["id"], exit_price=9.0)


def test_spent_pot_locks_desk_and_review_unlocks_refill():
    led = new_ledger(1_000)
    # burn the pot in ten cap-sized (10% = 100) total losses
    for _ in range(10):
        t = add_trade(led, **plan(max_loss=100.0, qty=1, entry=1.0))
        close_trade(led, t["id"], exit_price=0.0)  # -100 each
    assert equity(led) == 0
    assert "locked" in validate_open(led, "swing-buy", 50.0)
    # refill refused before review
    assert can_refill(led) is not None
    led["reviews"].append({"at": now_iso(), "stats": lane_stats(led)})
    assert can_refill(led) is None


def test_lane_stats_expectancy():
    led = new_ledger(10_000)
    a = add_trade(led, **plan(max_loss=100.0, qty=1, entry=1.0))
    close_trade(led, a["id"], exit_price=4.0)  # +300 = +3R
    b = add_trade(led, **plan(max_loss=100.0, qty=1, entry=1.0))
    close_trade(led, b["id"], exit_price=0.0)  # -100 = -1R
    s = lane_stats(led)["swing-buy"]
    assert s["trades"] == 2
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["avg_r"] == pytest.approx(1.0)  # (+3 - 1) / 2
    assert s["total_pnl"] == pytest.approx(200.0)


def test_check_alerts_long_and_short_sides():
    long_t = plan(stop=176.0, target=198.0) | {"id": "T1", "status": "open"}
    short_t = plan(direction="short", stop=210.0, target=180.0) | {
        "id": "T2",
        "status": "open",
    }
    # long: price below stop -> STOP; short: price below target -> TARGET
    alerts = check_alerts([long_t, short_t], {"NVDA": 175.0})
    assert any("T1" in a and "STOP" in a for a in alerts)
    assert any("T2" in a and "TARGET" in a for a in alerts)
    # long target side
    alerts_up = check_alerts([long_t], {"NVDA": 199.0})
    assert any("TARGET" in a for a in alerts_up)
    # inside the levels -> quiet
    assert check_alerts([long_t], {"NVDA": 185.0}) == []
    # missing price is reported, never silently skipped
    assert "no price" in check_alerts([long_t], {})[0]

"""Rule-by-rule tests for the 15-Minute Reversal reference simulator.

Each test pins one clause of the written strategy, so a change to
``pine/purpose_driven_15m_reversal.pine`` that contradicts a clause has somewhere to
fail. Bars are hand-built: no network, no broker, no market data.

Candle 1 is 90/100 (low/high) in every fixture unless a test says otherwise, so the
numbers below can be read against that range without scrolling back.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tools.reversal_15m_sim import (
    LONG,
    SHORT,
    Bar,
    Config,
    daily_sma,
    simulate,
    summarize,
)

ET = ZoneInfo("America/New_York")

MONDAY = date(2026, 8, 17)
FRIDAY = date(2026, 8, 21)

# Candle 1: opens 95, ranges 90-100, closes 95.
CANDLE1 = ("09:15", 95.0, 100.0, 90.0, 95.0)
# A bar wholly inside Candle 1's range: never a failure swing, never fills anything.
INSIDE = (96.0, 97.0, 94.0, 96.0)


def bars(day: date, *rows: tuple) -> list[Bar]:
    out = []
    for hhmm, o, h, lo, c in rows:
        hour, minute = (int(x) for x in hhmm.split(":"))
        out.append(Bar(datetime.combine(day, time(hour, minute), ET), o, h, lo, c))
    return out


def only(results, day: date):
    return next(r for r in results if r.day == day)


def run(day_bars, **cfg_kwargs):
    """Simulate with the trend SMA pinned, so no warm-up is needed."""
    trend = cfg_kwargs.pop("trend", 50.0)
    cfg = Config(**cfg_kwargs)
    days = {b.ts.date() for b in day_bars}
    return simulate(day_bars, cfg, sma={d: trend for d in days})


# ─────────────────────────────────────────────────────────────────────────────
# The long setup, end to end
# ─────────────────────────────────────────────────────────────────────────────
def test_long_failure_swing_fills_and_reaches_target():
    day = bars(
        MONDAY,
        CANDLE1,
        # Dips under 90, closes back above it, high stays under 100.
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        # Trades up through the 94 entry stop without gapping past it.
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        ("10:00", 95.0, 101.0, 95.0, 100.5),
    )
    result = only(run(day), MONDAY)

    assert result.committed_direction == LONG
    # Entry is the BODY extreme (max of open/close = 94), not the bar high of 95.
    assert result.entry == pytest.approx(94.0)
    assert result.target == pytest.approx(100.0)
    # (100 - 94) / 2.4 = 2.5 of risk, on the other side of entry.
    assert result.stop == pytest.approx(91.5)

    trade = result.trade
    assert trade.reason == "target"
    assert trade.entry == pytest.approx(94.0)
    assert trade.exit == pytest.approx(100.0)
    assert trade.r_multiple == pytest.approx(2.4)


def test_short_is_the_mirror_image():
    day = bars(
        MONDAY,
        CANDLE1,
        # Pokes above 100, closes back below it, low stays above 90.
        ("09:30", 99.0, 103.0, 97.0, 98.0),
        ("09:45", 98.5, 98.5, 96.0, 96.5),
        ("10:00", 96.0, 96.0, 89.0, 89.5),
    )
    result = only(run(day, trend=150.0), MONDAY)

    assert result.committed_direction == SHORT
    assert result.entry == pytest.approx(98.0)  # min(open, close)
    assert result.target == pytest.approx(90.0)
    assert result.stop == pytest.approx(98.0 + 8.0 / 2.4)
    assert result.trade.reason == "target"
    assert result.trade.r_multiple == pytest.approx(2.4)


# ─────────────────────────────────────────────────────────────────────────────
# Confirmation, commitment and the trend filter
# ─────────────────────────────────────────────────────────────────────────────
def test_wick_without_a_close_back_inside_is_not_a_setup():
    day = bars(
        MONDAY,
        CANDLE1,
        # Dips under 90 and CLOSES under it: a breakdown, not a failure swing.
        ("09:30", 92.0, 94.0, 86.0, 87.0),
        ("09:45", *INSIDE),
    )
    result = only(run(day), MONDAY)
    assert result.committed_direction is None
    assert result.skipped_reason == "no aligned failure swing"


def test_failure_swing_against_the_sma_is_skipped_and_a_later_one_still_counts():
    day = bars(
        MONDAY,
        CANDLE1,
        # Long failure swing, but the trend is above price: skipped, day NOT used up.
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        # Short failure swing, agreeing with the trend: this is the one that commits.
        ("10:00", 99.0, 103.0, 97.0, 98.0),
        ("10:15", 98.5, 98.5, 96.0, 96.5),
    )
    result = only(run(day, trend=150.0), MONDAY)

    assert result.committed_direction == SHORT
    assert result.setup_ts.hour == 10 and result.setup_ts.minute == 0


def test_the_day_commits_to_the_first_aligned_setup_only():
    day = bars(
        MONDAY,
        CANDLE1,
        # First aligned long setup. Entry stop sits at 94.
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        # A second, better long setup later — must be ignored, the day is committed.
        ("10:00", 91.0, 92.0, 87.0, 91.5),
        # Nothing ever trades up to 94, so the committed day produces no trade at all.
        ("10:15", 91.0, 93.0, 89.5, 92.0),
        ("15:45", 92.0, 93.0, 91.0, 92.0),
        ("16:00", 92.0, 92.5, 91.5, 92.0),
    )
    result = only(run(day), MONDAY)

    assert result.committed_direction == LONG
    assert result.entry == pytest.approx(94.0)
    assert result.setup_ts.hour == 9 and result.setup_ts.minute == 30
    assert result.trade is None  # committed, never filled — and no second attempt


def test_entry_can_never_fill_on_the_setup_bar_itself():
    # The setup bar's own high (95) is above its body extreme (94). If the emulator
    # were allowed to fill intrabar on the confirming bar, this would trade at 94 on
    # the 09:30 bar. It must wait, and here nothing later reaches 94.
    day = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.0, 93.9, 92.0, 93.5),
        ("15:45", 93.0, 93.5, 92.5, 93.0),
        ("16:00", 93.0, 93.2, 92.8, 93.0),
    )
    assert only(run(day), MONDAY).trade is None


# ─────────────────────────────────────────────────────────────────────────────
# Risk, fills and the session guards
# ─────────────────────────────────────────────────────────────────────────────
def test_stop_hit_loses_exactly_one_r():
    day = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        ("10:00", 94.0, 94.0, 91.0, 91.2),
    )
    trade = only(run(day), MONDAY).trade
    assert trade.reason == "stop"
    assert trade.r_multiple == pytest.approx(-1.0)


@pytest.mark.parametrize(
    "wide_bar, expected",
    [
        # Open 95 sits 4 from the low and 6 from the high, so the bar is assumed to
        # have run down first: the 91.5 stop is reached before the 100 target.
        (("10:00", 95.0, 101.0, 91.0, 95.0), "stop"),
        # Open 99 sits 2 from the high and 8 from the low: up first, target wins.
        (("10:00", 99.0, 101.0, 91.0, 95.0), "target"),
    ],
)
def test_a_bar_holding_both_exits_is_resolved_by_the_intrabar_path(wide_bar, expected):
    day = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        wide_bar,
    )
    assert only(run(day), MONDAY).trade.reason == expected


def test_a_low_printed_before_the_entry_filled_cannot_stop_the_trade_out():
    # The 09:45 bar dips to 91 — under the 91.5 stop — and only then rallies through
    # the 94 entry. Its open is 0.5 off the low, so the path is down-then-up and the
    # trade is not live during the dip. Charging it for that low is the classic way a
    # backtest invents losses it would never have taken.
    day = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 91.5, 96.0, 91.0, 95.5),
        ("10:00", 95.5, 101.0, 95.0, 100.5),
    )
    trade = only(run(day), MONDAY).trade
    assert trade.entry == pytest.approx(94.0)
    assert trade.reason == "target"


def test_reward_to_risk_is_an_input():
    day = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
    )
    result = only(run(day, reward_risk=3.0), MONDAY)
    assert result.stop == pytest.approx(94.0 - (100.0 - 94.0) / 3.0)


def test_open_position_is_flattened_at_the_next_open_after_1555():
    day = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        # Drifts all afternoon, hitting neither 100 nor 91.5.
        ("15:30", 95.0, 96.0, 94.0, 95.5),
        ("15:45", 95.5, 96.5, 95.0, 96.0),
        ("16:00", 97.0, 97.5, 96.5, 97.0),
    )
    trade = only(run(day), MONDAY).trade
    assert trade.reason == "flatten"
    assert trade.exit == pytest.approx(97.0)  # the 16:00 open
    assert trade.exit_ts.hour == 16


def test_no_setup_is_taken_on_a_bar_that_straddles_the_flatten_time():
    day = bars(
        MONDAY,
        CANDLE1,
        # A textbook long failure swing, but on the 15:45 bar, which straddles 15:55.
        ("15:45", 92.0, 95.0, 88.0, 94.0),
        ("16:00", 96.0, 97.0, 95.0, 96.5),
    )
    assert only(run(day), MONDAY).committed_direction is None


def test_fridays_are_skipped_entirely():
    day = bars(
        FRIDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        ("10:00", 95.0, 101.0, 95.0, 100.5),
    )
    assert only(run(day), FRIDAY).skipped_reason == "friday"
    assert only(run(day, skip_friday=False), FRIDAY).trade.reason == "target"


def test_a_regular_hours_chart_takes_no_trades_at_all():
    # Same day, same shape — but the 09:15 bar does not exist, as on an RTH-only chart.
    day = bars(
        MONDAY,
        ("09:30", 95.0, 100.0, 90.0, 95.0),
        ("09:45", 92.0, 95.0, 88.0, 94.0),
        ("10:00", 93.5, 96.0, 93.0, 95.0),
        ("10:15", 95.0, 101.0, 95.0, 100.5),
    )
    results = run(day)
    assert only(results, MONDAY).skipped_reason == "no candle 1"
    assert summarize(results)["days_with_candle_1"] == 0
    assert summarize(results)["trades"] == 0


def test_one_trade_per_day_across_a_multi_day_run():
    day_bars = []
    for d in (date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)):
        day_bars += bars(
            d,
            CANDLE1,
            ("09:30", 92.0, 95.0, 88.0, 94.0),
            ("09:45", 93.5, 96.0, 93.0, 95.0),
            ("10:00", 95.0, 101.0, 95.0, 100.5),
            # A second, equally valid failure swing after the day's trade is done.
            ("11:00", 92.0, 95.0, 88.0, 94.0),
            ("11:15", 93.5, 96.0, 93.0, 95.0),
        )
    stats = summarize(run(day_bars))
    assert stats["days"] == 3
    assert stats["trades"] == 3
    assert stats["total_r"] == pytest.approx(3 * 2.4)


# ─────────────────────────────────────────────────────────────────────────────
# The trend filter's data
# ─────────────────────────────────────────────────────────────────────────────
def test_daily_sma_cannot_see_its_own_days_close():
    day_bars = []
    for i in range(5):
        d = date(2026, 8, 3) + timedelta(days=i)
        day_bars += bars(d, ("09:15", 0.0, 0.0, 0.0, float(10 * (i + 1))))

    sma = daily_sma(day_bars, length=2)
    # The 3rd day averages days 1-2 (10, 20), not days 2-3.
    assert sma[date(2026, 8, 5)] == pytest.approx(15.0)
    assert sma[date(2026, 8, 6)] == pytest.approx(25.0)
    # Days without a full window have no value, so the strategy stands down.
    assert date(2026, 8, 4) not in sma


def test_disabling_the_filter_takes_the_first_swing_in_either_direction():
    day = bars(
        MONDAY,
        CANDLE1,
        # Long failure swing that a high SMA would have vetoed.
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        ("10:00", 95.0, 101.0, 95.0, 100.5),
    )
    vetoed = only(run(day, trend=150.0), MONDAY)
    assert vetoed.committed_direction is None

    unfiltered = only(run(day, use_sma=False, trend=150.0), MONDAY)
    assert unfiltered.committed_direction == LONG
    assert unfiltered.trade.reason == "target"


# ---------------------------------------------------------------------------
# The night-lab bridge: exported trades must carry exactly what its
# arithmetic computes R from, in the shape it reads
# ---------------------------------------------------------------------------

import json

from tools.reversal_15m_sim import main, trades_as_records


def winning_long_day():
    """The proven fixture from test_long_failure_swing_fills_and_reaches_target."""
    return bars(
        MONDAY,
        CANDLE1,
        ("09:30", 92.0, 95.0, 88.0, 94.0),
        ("09:45", 93.5, 96.0, 93.0, 95.0),
        ("10:00", 95.0, 101.0, 95.0, 100.5),
    )


def test_exported_records_carry_the_arithmetic_fields():
    results = run(winning_long_day())
    trades = [r.trade for r in results if r.trade]
    assert trades, "the fixture should produce one closed trade"
    (record,) = trades_as_records(trades, "ES=F")
    assert record["lane"] == "sim-15m"
    assert record["symbol"] == "ES=F"
    assert record["status"] == "closed"
    assert record["direction"] == "long"
    # The three numbers night_lab.trade_r computes from, exactly as traded.
    assert record["entry"] == trades[0].entry
    assert record["stop"] == trades[0].stop
    assert record["exit"] == trades[0].exit
    # And the shape is what night_lab.load_sim_trades keeps.
    from tools.night_lab import trade_r

    assert trade_r(record) is not None


def test_short_trades_export_as_short():
    # The mirror fixture from test_short_is_the_mirror_image.
    day_bars = bars(
        MONDAY,
        CANDLE1,
        ("09:30", 99.0, 103.0, 97.0, 98.0),
        ("09:45", 98.5, 98.5, 96.0, 96.5),
        ("10:00", 96.0, 96.0, 89.0, 89.5),
    )
    results = run(day_bars, trend=150.0)
    trades = [r.trade for r in results if r.trade]
    assert trades
    (record,) = trades_as_records(trades, "ES=F")
    assert record["direction"] == "short"


def test_the_cli_writes_the_export_end_to_end(tmp_path):
    csv_path = tmp_path / "bars.csv"
    rows = ["timestamp,open,high,low,close"]
    for bar in winning_long_day():
        rows.append(
            f"{bar.ts.strftime('%Y-%m-%dT%H:%M:%S')},"
            f"{bar.open},{bar.high},{bar.low},{bar.close}"
        )
    csv_path.write_text("\n".join(rows) + "\n")

    out = tmp_path / "trades.json"
    rc = main([str(csv_path), "--no-sma", "--symbol", "ES=F", "--trades-out", str(out)])
    assert rc == 0
    exported = json.loads(out.read_text())
    assert len(exported["trades"]) == 1
    assert exported["trades"][0]["symbol"] == "ES=F"


# ---------------------------------------------------------------------------
# The SMA starvation the first live fetch hit: a 59-day 15m window can never
# warm up an SMA(60) of daily closes, so the filter silently skipped every
# session. The fix feeds the SMA from a daily-closes file.
# ---------------------------------------------------------------------------

from datetime import timedelta

from tools.reversal_15m_sim import daily_path_for, sma_from_daily_csv


def weekdays_back_from(day, n):
    """The n weekdays before `day`, oldest first."""
    out, d = [], day
    while len(out) < n:
        d = d - timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return list(reversed(out))


def write_daily_csv(path, day, n, close=90.0):
    rows = ["timestamp,close"]
    for d in weekdays_back_from(day, n):
        rows.append(f"{d.isoformat()},{close}")
    path.write_text("\n".join(rows) + "\n")


def bars_csv_for(path, day_bars):
    rows = ["timestamp,open,high,low,close"]
    for bar in day_bars:
        rows.append(
            f"{bar.ts.strftime('%Y-%m-%dT%H:%M:%S')},"
            f"{bar.open},{bar.high},{bar.low},{bar.close}"
        )
    path.write_text("\n".join(rows) + "\n")


def test_a_short_window_starves_the_sma_and_the_cli_says_so(tmp_path, capsys):
    # The live symptom: bars only, SMA on, zero trades — but now with a
    # diagnostic instead of a silent zero.
    csv_path = tmp_path / "bars.csv"
    bars_csv_for(csv_path, winning_long_day())
    rc = main([str(csv_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sma warming up" in out
    assert "--daily" in out and "--no-sma" in out


def test_sixty_daily_closes_warm_the_filter_up(tmp_path):
    closes = tmp_path / "daily.csv"
    write_daily_csv(closes, MONDAY, 60, close=90.0)
    sma = sma_from_daily_csv(str(closes), 60, [MONDAY])
    assert sma[MONDAY] == pytest.approx(90.0)


def test_fifty_nine_closes_do_not(tmp_path):
    closes = tmp_path / "daily.csv"
    write_daily_csv(closes, MONDAY, 59)
    assert MONDAY not in sma_from_daily_csv(str(closes), 60, [MONDAY])


def test_the_daily_file_is_picked_up_by_its_sibling_name(tmp_path, capsys):
    # SMA at 90, day trades near 94-100: the long is above trend and commits.
    csv_path = tmp_path / "es15.csv"
    bars_csv_for(csv_path, winning_long_day())
    write_daily_csv(tmp_path / "es15.daily.csv", MONDAY, 60, close=90.0)
    out_json = tmp_path / "trades.json"
    rc = main([str(csv_path), "--trades-out", str(out_json)])
    assert rc == 0
    assert "sma warming up" not in capsys.readouterr().out
    assert len(json.loads(out_json.read_text())["trades"]) == 1


def test_a_trend_above_the_day_still_filters_the_long_out(tmp_path):
    # Same day, SMA parked at 150: the long failure swing is against trend.
    csv_path = tmp_path / "es15.csv"
    bars_csv_for(csv_path, winning_long_day())
    write_daily_csv(tmp_path / "es15.daily.csv", MONDAY, 60, close=150.0)
    out_json = tmp_path / "trades.json"
    main([str(csv_path), "--trades-out", str(out_json)])
    assert json.loads(out_json.read_text())["trades"] == []


def test_daily_path_sits_beside_the_bars():
    assert daily_path_for("night_lab/es15.csv").endswith("es15.daily.csv")

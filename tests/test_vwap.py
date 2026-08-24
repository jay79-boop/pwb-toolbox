"""Tests for `pwb_toolbox.backtesting.vwap` and `tools.vwap_lab`.

No network and no market data: every frame is synthetic, built so the setup
under test has exactly one honest firing point, and every strategy assertion
runs through a real cerebro with the lab's costs charged -- a signal that
parses but does not trade is a failure here, same as in the converter tests.

Timestamps are naive UTC throughout (the lab's convention). January dates keep
New York at UTC-5, so 14:30 UTC is the 09:30 open all day long.
"""

import backtrader as bt
import pandas as pd
import pytest

from pwb_toolbox.backtesting.vwap import (
    SessionVwap,
    VwapStrategy,
    parse_hm,
    session_key,
)
from tools.backtest_lab import backtest
from tools import vwap_lab

SESSION_OPEN_UTC = "14:30"  # 09:30 New York, in January


def _frame(closes, start="2024-01-08 14:30", volumes=None, lows=None, highs=None):
    """5-minute bars where open=high=low=close unless a wick is planted."""
    index = pd.date_range(start, periods=len(closes), freq="5min")
    volumes = volumes if volumes is not None else [100] * len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs if highs is not None else closes,
            "low": lows if lows is not None else closes,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def _alternate(base, half_spread, count, start_up=False):
    out = []
    for i in range(count):
        sign = 1 if (i % 2 == 0) == start_up else -1
        out.append(base + sign * half_spread)
    return out


class _Capture(bt.Strategy):
    """Records the indicator's lines so tests can pin them bar by bar."""

    params = dict(ind_kwargs=None)

    def __init__(self):
        self.ind = SessionVwap(self.data, **(self.p.ind_kwargs or {}))
        self.rows = []

    def next(self):
        self.rows.append(
            (
                self.data.datetime.datetime(0),
                self.ind.vwap[0],
                self.ind.upper[0],
                self.ind.lower[0],
            )
        )


def _capture(frame, **ind_kwargs):
    cerebro = bt.Cerebro()
    cerebro.adddata(
        bt.feeds.PandasData(
            dataname=frame, timeframe=bt.TimeFrame.Minutes, compression=5
        )
    )
    cerebro.addstrategy(_Capture, ind_kwargs=ind_kwargs)
    return cerebro.run()[0].rows


def _fade_day(spike=103.0, start="2024-01-08 14:30", tail=None):
    """Ten quiet bars, a three-bar spike, then a decay back through VWAP."""
    closes = (
        _alternate(100.0, 0.2, 10)
        + [spike] * 3
        + (tail if tail is not None else [102.0, 101.0, 100.2, 100.0, 100.0])
    )
    return _frame(closes, start=start)


# --- the indicator ------------------------------------------------------------


def test_vwap_is_the_volume_weighted_mean_of_typical_prices():
    frame = _frame([100.0, 101.0, 102.0], volumes=[1, 2, 3])
    rows = _capture(frame)
    assert rows[2][1] == pytest.approx((100 * 1 + 101 * 2 + 102 * 3) / 6)


def test_vwap_resets_at_the_session_anchor_not_at_midnight():
    day1 = _frame([100.0] * 3, start="2024-01-08 14:30")
    day2 = _frame([200.0] * 3, start="2024-01-09 14:30")
    rows = _capture(pd.concat([day1, day2]), anchor_time="09:30")
    # Day two's first bar owes nothing to day one's prices.
    assert rows[3][1] == pytest.approx(200.0)


def test_zero_volume_degrades_to_twap_instead_of_dividing_by_zero():
    frame = _frame([100.0, 102.0, 104.0], volumes=[0, 0, 0])
    rows = _capture(frame)
    assert rows[2][1] == pytest.approx((100 + 102 + 104) / 3)


def test_bands_are_k_sigma_around_vwap():
    frame = _frame([99.0, 101.0, 99.0, 101.0])
    rows = _capture(frame, band_k=2.0)
    # Equal-volume 99/101 alternation: mean 100, stdev exactly 1.
    _, vwap, upper, lower = rows[3]
    assert vwap == pytest.approx(100.0)
    assert upper == pytest.approx(102.0)
    assert lower == pytest.approx(98.0)


def test_anchored_vwap_ignores_everything_before_its_anchor():
    frame = _frame([100.0, 101.0, 102.0, 103.0])
    anchor = frame.index[2].isoformat()
    rows = _capture(frame, anchor=anchor)
    assert pd.isna(rows[1][1])
    assert rows[2][1] == pytest.approx(102.0)
    assert rows[3][1] == pytest.approx((102.0 + 103.0) / 2)


def test_session_key_shifts_by_the_anchor_so_overnight_hangs_together():
    from datetime import datetime

    # 14:30 UTC and 20:00 UTC on the same NY day share the 09:30-anchored key;
    # 13:00 UTC (08:00 NY, before the anchor) belongs to the previous session.
    anchor = parse_hm("09:30")
    same = session_key(datetime(2024, 1, 8, 14, 30), "America/New_York", anchor)
    late = session_key(datetime(2024, 1, 8, 20, 0), "America/New_York", anchor)
    early = session_key(datetime(2024, 1, 8, 13, 0), "America/New_York", anchor)
    assert same == late
    assert early < same


# --- the fade -----------------------------------------------------------------


def test_a_stretch_beyond_the_band_is_faded_back_to_vwap():
    result = backtest(_fade_day(), VwapStrategy, mintick=0.25, setup="fade")
    assert result.trades == 1
    assert result.wins == 1
    log = result.strategy.trade_log
    assert log[0]["direction"] == "short"
    assert log[0]["reason"] == "target"


def test_a_fade_that_never_reverts_is_flattened_at_session_end():
    # The spike holds into the close: 79 in-session bars, then five more so the
    # 15:55 flatten order has an open to fill at.
    closes = _alternate(100.0, 0.2, 10) + _alternate(103.3, 0.1, 74) + [103.3] * 5
    result = backtest(_frame(closes), VwapStrategy, mintick=0.25, setup="fade")
    log = result.strategy.trade_log
    assert len(log) == 1
    assert log[0]["reason"] == "flatten"


def test_nothing_trades_before_the_warmup_has_bars_in_the_bands():
    # The same stretch on bar two of the session: fresh bands, no entry.
    closes = [100.0, 103.0, 103.0, 100.0, 100.0]
    result = backtest(_frame(closes), VwapStrategy, mintick=0.25, setup="fade")
    assert result.trades == 0


# --- day type, MA and rvol gates ----------------------------------------------

#: Six ramp bars (+130bp in the first 30 minutes), a pause, then a blow-off:
#: on a classified trend-up day the counter-trend short fade must not fire.
TREND_DAY = [100.0, 100.26, 100.52, 100.78, 101.04, 101.3, 101.3, 101.3, 106.0] + [
    107.0,
    108.0,
    108.0,
    108.0,
]


def test_the_day_type_gate_blocks_counter_trend_fades_on_a_trend_day():
    unfiltered = backtest(_frame(TREND_DAY), VwapStrategy, mintick=0.25, setup="fade")
    filtered = backtest(
        _frame(TREND_DAY), VwapStrategy, mintick=0.25, setup="fade", day_type_bp=50.0
    )
    assert unfiltered.trades == 1
    assert filtered.trades == 0


def test_the_ma_gate_blocks_fading_strength_above_the_average():
    blocked = backtest(
        _frame(TREND_DAY), VwapStrategy, mintick=0.25, setup="fade", ma_len=5
    )
    assert blocked.trades == 0


def test_the_rvol_gate_wants_the_stretch_to_arrive_on_volume():
    quiet = _alternate(100.0, 0.2, 10) + [104.0] * 3 + [102.0, 101.0, 100.2, 100.0]
    dead_tape = _frame(quiet)
    loud_tape = _frame(quiet, volumes=[100] * 10 + [300] * 3 + [100] * 4)
    kwargs = dict(setup="fade", band_k=1.5, rvol_len=5, rvol_min=2.0)
    assert backtest(dead_tape, VwapStrategy, mintick=0.25, **kwargs).trades == 0
    assert backtest(loud_tape, VwapStrategy, mintick=0.25, **kwargs).trades == 1


def test_the_rsi_control_gate_passes_a_clean_stretch():
    """One big up bar out of a flat tape carries a stretched RSI, so the
    control gate agrees with the band signal here -- which is the colinearity
    the wisdom doc alleges, visible in miniature."""
    result = backtest(_fade_day(), VwapStrategy, mintick=0.25, setup="fade", rsi_len=5)
    assert result.trades == 1


# --- the pullback -------------------------------------------------------------


def test_a_touch_of_vwap_from_above_enters_long_toward_the_far_band():
    closes = (
        [100.0, 100.5, 101.0, 101.5, 102.0]
        + _alternate(102.2, 0.2, 10)
        + [102.0]  # the dip bar: its low is planted separately
        + [102.5, 103.0, 103.5, 105.0, 105.0, 105.0]
    )
    lows = list(closes)
    lows[15] = 99.0
    frame = _frame(closes, lows=lows)
    result = backtest(frame, VwapStrategy, mintick=0.25, setup="pullback")
    assert result.trades == 1
    log = result.strategy.trade_log
    assert log[0]["direction"] == "long"
    assert log[0]["reason"] == "target"
    assert result.net > 0


def test_no_pullback_fires_when_price_never_touches_vwap():
    closes = [100.0, 100.5, 101.0, 101.5, 102.0] + _alternate(102.2, 0.2, 20)
    result = backtest(_frame(closes), VwapStrategy, mintick=0.25, setup="pullback")
    assert result.trades == 0


# --- the crossover control ----------------------------------------------------


def test_the_cross_control_reverses_at_each_crossing_and_flattens_at_the_end():
    closes = ([101.0] * 10 + [99.0] * 10) * 3 + [100.0] * 24
    result = backtest(_frame(closes), VwapStrategy, mintick=0.25, setup="cross")
    log = result.strategy.trade_log
    assert result.trades >= 3
    directions = {t["direction"] for t in log}
    assert directions == {"long", "short"}
    assert log[-1]["reason"] in ("flatten", "reverse")


# --- 24/7 mode ----------------------------------------------------------------


def test_crypto_mode_trades_hours_the_session_filter_would_reject():
    # The same fade day stamped at 00:00 UTC: outside the New York session,
    # so the default strategy stands aside and the 24/7 one trades it.
    frame = _fade_day(start="2024-01-08 00:00")
    rth = backtest(frame, VwapStrategy, mintick=0.25, setup="fade")
    crypto = backtest(
        frame, VwapStrategy, mintick=0.25, setup="fade", tz="UTC", rth_only=False
    )
    assert rth.trades == 0
    assert crypto.trades == 1


# --- the lab driver -----------------------------------------------------------


def _year_of_fade_days(year, scale=1.0):
    days = pd.bdate_range(f"{year}-01-08", periods=9)
    frames = []
    for day in days:
        frame = _fade_day(start=f"{day.date()} 14:30")
        for col in ("open", "high", "low", "close"):
            frame[col] = frame[col] * scale
        frames.append(frame)
    return pd.concat(frames)


def test_run_lab_reports_every_setup_and_leaves_one_feed_unjudged():
    frame = _year_of_fade_days(2024)
    out = vwap_lab.run_lab(frame, ["fade", "cross"], mintick=0.25, minutes=5)
    assert set(out) == {"fade", "cross"}
    assert out["fade"]["result"].trades > 0
    assert out["fade"]["floor"] is None


def test_run_lab_judges_two_feeds_against_the_noise_floor():
    one = pd.concat([_year_of_fade_days(2023), _year_of_fade_days(2024)])
    other = pd.concat(
        [_year_of_fade_days(2023, scale=1.001), _year_of_fade_days(2024, scale=1.001)]
    )
    out = vwap_lab.run_lab(
        one, ["fade"], mintick=0.25, minutes=5, second=other, min_bars=100
    )
    floor = out["fade"]["floor"]
    assert floor is not None
    # Two near-identical feeds of a repeatable winner: the edge is real and
    # the vendor gap tiny, so this construction must clear its floor.
    assert floor.clears


def test_zero_volume_share_flags_a_volumeless_feed():
    assert vwap_lab.zero_volume_share(_frame([100.0] * 4, volumes=[0] * 4)) == 1.0
    assert vwap_lab.zero_volume_share(_frame([100.0] * 4)) == 0.0


def test_volume_warning_covers_the_second_feed_not_just_the_first():
    # The bug this pins: a volumeless SECOND feed used to pass in silence, so
    # the noise floor compared a TWAP run against a VWAP one and reported the
    # difference as vendor disagreement.
    full = _frame([100.0] * 4)
    empty = _frame([100.0] * 4, volumes=[0] * 4)

    lines = vwap_lab.volume_warnings(full, empty, labels=("primary", "second"))
    assert any("second feed carry zero volume" in line for line in lines)

    lines = vwap_lab.volume_warnings(empty, full, labels=("primary", "second"))
    assert any("primary feed carry zero volume" in line for line in lines)


def test_a_mixed_volume_pair_says_the_gap_is_not_vendor_disagreement():
    lines = vwap_lab.volume_warnings(
        _frame([100.0] * 4, volumes=[0] * 4),
        _frame([100.0] * 4),
        labels=("primary", "second"),
    )
    assert any("not vendor disagreement" in line for line in lines)


def test_two_volumeless_feeds_warn_twice_but_not_about_disagreement():
    empty = _frame([100.0] * 4, volumes=[0] * 4)
    lines = vwap_lab.volume_warnings(empty, empty, labels=("primary", "second"))
    # Both are TWAP, which is at least like-for-like -- flag each feed, but do
    # not claim the comparison itself is mixing indicators.
    assert len(lines) == 2
    assert not any("not vendor disagreement" in line for line in lines)


def test_two_volumed_feeds_and_a_lone_feed_stay_quiet():
    full = _frame([100.0] * 4)
    assert vwap_lab.volume_warnings(full, full) == []
    assert vwap_lab.volume_warnings(full) == []
    assert len(vwap_lab.volume_warnings(_frame([100.0] * 4, volumes=[0] * 4))) == 1


def test_exported_trades_carry_the_fields_the_night_lab_reads():
    result = backtest(_fade_day(), VwapStrategy, mintick=0.25, setup="fade")
    records = vwap_lab.trades_as_records(result.strategy.trade_log, "ES")
    assert len(records) == 1
    rec = records[0]
    assert rec["lane"] == "sim-vwap"
    assert rec["symbol"] == "ES"
    for key in ("direction", "entry", "stop", "exit", "opened", "closed", "reason"):
        assert rec[key] is not None


def test_an_unknown_setup_is_an_error_not_a_silent_no_trade():
    with pytest.raises(ValueError, match="setup"):
        backtest(_fade_day(), VwapStrategy, mintick=0.25, setup="vibes")

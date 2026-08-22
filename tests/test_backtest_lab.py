"""Tests for `tools.backtest_lab`.

No network and no market data: every frame here is synthetic, and the one
end-to-end run drives a converted PineScript strategy over bars built in the
test itself.
"""

import random

import backtrader as bt
import pandas as pd
import pytest

from pwb_toolbox.converting import convert
from tools.backtest_lab import (
    NoiseFloor,
    Result,
    backtest,
    noise_floor,
    read_generic,
    read_histdata,
    summarise,
    to_bars,
    to_utc,
    verify_timezone,
)


def _frame(start, bars, minutes=1, price=100.0, tz=None):
    index = pd.date_range(start, periods=bars, freq=f"{minutes}min")
    if tz is not None:
        index = to_utc(index, tz)
    frame = pd.DataFrame(
        {
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price,
            "volume": 100,
        },
        index=index,
    )
    return frame[frame.index.notna()]


# --- the timezone trap --------------------------------------------------------
#
# The bug this module exists to prevent. A feed stamped in a DST-observing zone
# needs a different offset in summer than in winter, and reading it with one
# flat offset moves the session window by an hour for most of the year.


def test_new_york_winter_stamps_are_five_hours_behind_utc():
    stamps = pd.to_datetime(["2015-01-15 09:30:00"])
    assert to_utc(stamps, "America/New_York")[0] == pd.Timestamp("2015-01-15 14:30")


def test_new_york_summer_stamps_are_four_hours_behind_utc():
    """The same local clock time, six months later, is a different UTC hour.

    A loader applying a flat five hours reads this bar as 14:30 UTC, which the
    strategy then converts back to 10:30 New York -- an hour after the bar it
    actually is.
    """
    stamps = pd.to_datetime(["2015-07-15 09:30:00"])
    assert to_utc(stamps, "America/New_York")[0] == pd.Timestamp("2015-07-15 13:30")


def test_a_utc_feed_is_left_alone():
    stamps = pd.to_datetime(["2015-07-15 09:30:00"])
    assert to_utc(stamps, "UTC")[0] == pd.Timestamp("2015-07-15 09:30")


def test_the_repeated_and_missing_dst_hours_are_dropped_not_guessed():
    """01:30 happens twice when the clocks go back and never when they go
    forward. Both are NaT rather than an invented answer, and neither raises."""
    back = to_utc(pd.to_datetime(["2015-11-01 01:30:00"]), "America/New_York")
    forward = to_utc(pd.to_datetime(["2015-03-08 02:30:00"]), "America/New_York")
    assert back.isna().all()
    assert forward.isna().all()


def test_verify_timezone_recovers_a_known_offset():
    """The check that catches a mis-stamped feed: shift a series against
    itself and the correlation peak names the shift."""
    # A *non-periodic* walk. A repeating pattern correlates perfectly at
    # every shift that is a multiple of its period, which says nothing.
    rng = random.Random(4)
    index = pd.date_range("2015-01-01", periods=600, freq="5min")
    moves = [1.0 + rng.gauss(0, 0.004) for _ in range(600)]
    series = pd.Series(moves, index=index).cumprod() * 100
    shifted = series.copy()
    shifted.index = shifted.index - pd.Timedelta(minutes=60)
    offset, correlation = verify_timezone(
        shifted, series, candidates=range(-120, 121, 30)
    )
    assert offset == 60
    assert correlation > 0.99


# --- normalisation ------------------------------------------------------------


def test_bps_normalises_away_the_quote_level():
    """The same points on a twice-as-expensive instrument is half the move."""
    cheap = Result(trades=10, wins=4, net=50.0, price=1_000.0)
    dear = Result(trades=10, wins=4, net=50.0, price=2_000.0)
    assert cheap.bps == pytest.approx(500.0)
    assert dear.bps == pytest.approx(250.0)


def test_a_result_with_no_trades_has_no_win_rate_and_no_bps():
    empty = Result(trades=0, wins=0, net=0.0, price=0.0)
    assert empty.win_rate == 0.0
    assert empty.bps == 0.0


def test_summarise_folds_periods_into_one_row():
    rows = {
        2011: Result(trades=4, wins=1, net=10.0, price=100.0),
        2012: Result(trades=6, wins=4, net=-4.0, price=100.0),
    }
    out = summarise(rows)
    assert out["trades"] == 10
    assert out["win_rate"] == pytest.approx(50.0)
    assert out["bps"] == pytest.approx(1000.0 - 400.0)


# --- the noise floor ----------------------------------------------------------


def test_an_edge_smaller_than_the_vendor_gap_does_not_clear():
    """The real case. Two feeds of one index agree about every year's shape
    and still disagree about the total by more than the total itself."""
    one = {
        y: Result(10, 4, v, 100.0)
        for y, v in zip(range(2011, 2019), [0.4, -1.2, -0.1, 1.1, 0.5, 0.5, 0.8, -1.5])
    }
    other = {
        y: Result(10, 4, v, 100.0)
        for y, v in zip(range(2011, 2019), [0.9, -1.4, -0.8, 0.5, -0.3, 0.4, 0.8, -2.4])
    }
    floor = noise_floor(one, other)
    assert floor.correlation > 0.9
    assert not floor.clears
    assert "INSIDE" in str(floor)


def test_an_edge_far_larger_than_the_gap_clears():
    one = {y: Result(10, 6, 40.0, 100.0) for y in range(2011, 2019)}
    other = {y: Result(10, 6, 39.0, 100.0) for y in range(2011, 2019)}
    floor = noise_floor(one, other)
    assert floor.clears
    assert "clears" in str(floor)


def test_comparing_needs_overlapping_periods():
    with pytest.raises(ValueError, match="at least two periods"):
        noise_floor({2011: Result(1, 1, 1.0, 100.0)}, {2011: Result(1, 1, 1.0, 100.0)})


# --- loaders ------------------------------------------------------------------


def test_read_histdata_parses_and_converts(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "20150115 093000;100.0;101.0;99.0;100.5;10\n"
        "20150715 093000;200.0;201.0;199.0;200.5;20\n"
    )
    frame = read_histdata(path)
    assert list(frame.index) == [
        pd.Timestamp("2015-01-15 14:30"),  # EST
        pd.Timestamp("2015-07-15 13:30"),  # EDT
    ]
    assert frame["close"].tolist() == [100.5, 200.5]


def test_read_generic_defaults_to_utc(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "time,open,high,low,close,volume\n2015-07-15 09:30:00,1,2,0.5,1.5,7\n"
    )
    frame = read_generic(path)
    assert list(frame.index) == [pd.Timestamp("2015-07-15 09:30")]


# --- resampling ---------------------------------------------------------------


def test_bars_are_labelled_by_their_opening_not_their_close():
    """A bar stamped with its close is admitted by a session filter one
    interval late -- the same class of error as a wrong timezone."""
    frame = _frame("2015-01-05 09:30", bars=10, minutes=1)
    bars = to_bars(frame, minutes=5)
    assert list(bars.index) == [
        pd.Timestamp("2015-01-05 09:30"),
        pd.Timestamp("2015-01-05 09:35"),
    ]


def test_resampling_aggregates_each_field_the_right_way():
    index = pd.date_range("2015-01-05 09:30", periods=5, freq="1min")
    frame = pd.DataFrame(
        {
            "open": [10, 11, 12, 13, 14],
            "high": [15, 16, 20, 17, 18],
            "low": [5, 4, 1, 6, 7],
            "close": [11, 12, 13, 14, 15],
            "volume": [1, 1, 1, 1, 1],
        },
        index=index,
    )
    bar = to_bars(frame, minutes=5).iloc[0]
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]) == (
        10,
        20,
        1,
        15,
        5,
    )


# --- end to end ---------------------------------------------------------------

#: Round-trips every third bar, so the ledger fills on any tape -- including
#: a flat one, which is what the cost test needs.
ROUND_TRIP = (
    '//@version=6\nstrategy("S")\n'
    "if strategy.position_size == 0\n"
    '    strategy.entry("L", strategy.long)\n'
    "if strategy.position_size > 0 and bar_index % 3 == 0\n"
    "    strategy.close()\n"
)


def _compiled(source):
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<lab>", "exec"), namespace)
    return namespace[result.class_name]


def test_a_converted_strategy_runs_through_the_lab():
    index = pd.date_range("2015-01-05 14:30", periods=200, freq="5min")
    prices = [100.0 + (i % 7) - 3 for i in range(200)]
    frame = pd.DataFrame(
        {
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": [p + 0.5 for p in prices],
            "volume": 100,
        },
        index=index,
    )
    result = backtest(frame, _compiled(ROUND_TRIP), mintick=0.25)
    assert result.trades > 0
    assert result.price == pytest.approx(frame["close"].mean())
    assert isinstance(result.bps, float)


def test_costs_are_charged_rather_than_assumed_away():
    """A strategy trading a flat tape must lose exactly its costs, which is
    what makes a gross-positive/net-negative result visible at all."""
    index = pd.date_range("2015-01-05 14:30", periods=60, freq="5min")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 100},
        index=index,
    )
    free = backtest(
        frame, _compiled(ROUND_TRIP), mintick=0.25, slip_ticks=0, commission=0
    )
    charged = backtest(
        frame, _compiled(ROUND_TRIP), mintick=0.25, slip_ticks=1.0, commission=0.02
    )
    assert charged.net < free.net

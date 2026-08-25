"""The reshaping half of tools/fetch_bars.py, which must never need a network.

Only ``normalise`` is covered here, deliberately: ``fetch`` is one yfinance
call wrapped around it, and the suite is not allowed to make one.
"""

import pandas as pd
import pytest

from tools import fetch_bars


def _yf_frame(multiindex=True, tz="America/New_York", rows=4):
    """A frame shaped the way yfinance actually returns one."""
    index = pd.date_range("2026-03-02 09:30", periods=rows, freq="5min", tz=tz)
    data = {
        "Open": [100.0 + i for i in range(rows)],
        "High": [101.0 + i for i in range(rows)],
        "Low": [99.0 + i for i in range(rows)],
        "Close": [100.5 + i for i in range(rows)],
        "Adj Close": [100.5 + i for i in range(rows)],
        "Volume": [1000 * (i + 1) for i in range(rows)],
    }
    frame = pd.DataFrame(data, index=index)
    if multiindex:
        frame.columns = pd.MultiIndex.from_product([frame.columns, ["SPY"]])
    return frame


def test_tz_aware_intraday_bars_become_naive_utc():
    # 09:30 New York in March is 14:30 UTC. The labs read a generic feed as
    # UTC, so anything still carrying a local stamp shifts the whole session.
    out = fetch_bars.normalise(_yf_frame())
    assert out.index.tz is None
    assert out.index[0] == pd.Timestamp("2026-03-02 14:30")


def test_naive_bars_are_left_alone_rather_than_localised_to_a_guess():
    out = fetch_bars.normalise(_yf_frame(tz=None))
    assert out.index.tz is None
    assert out.index[0] == pd.Timestamp("2026-03-02 09:30")


def test_both_column_shapes_yfinance_returns_land_identically():
    flat = fetch_bars.normalise(_yf_frame(multiindex=False))
    nested = fetch_bars.normalise(_yf_frame(multiindex=True))
    pd.testing.assert_frame_equal(flat, nested)


def test_output_carries_exactly_the_columns_the_labs_read():
    out = fetch_bars.normalise(_yf_frame())
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_a_frame_missing_a_column_is_refused_by_name():
    frame = _yf_frame(multiindex=False).drop(columns=["Volume"])
    with pytest.raises(ValueError, match="volume"):
        fetch_bars.normalise(frame)


def test_duplicate_stamps_and_empty_bars_are_dropped():
    frame = _yf_frame(multiindex=False)
    frame = pd.concat([frame, frame.iloc[[0]]])
    frame.iloc[1, frame.columns.get_loc("Close")] = float("nan")
    out = fetch_bars.normalise(frame)
    assert out.index.is_unique
    assert out.index.is_monotonic_increasing
    assert not out.isna().any().any()


def test_zero_volume_share_matches_the_gauge_vwap_lab_uses():
    frame = _yf_frame(multiindex=False)
    assert fetch_bars.zero_volume_share(fetch_bars.normalise(frame)) == 0.0
    frame.loc[:, "Volume"] = 0
    assert fetch_bars.zero_volume_share(fetch_bars.normalise(frame)) == 1.0


def test_mintick_scales_with_the_instruments_own_quote():
    # slip_ticks defaults to 1.0, so mintick IS the per-trade slippage in
    # price units -- a tick borrowed from a futures example onto an
    # instrument quoting 100x higher charges 1/100th of the cost.
    assert fetch_bars.mintick_for_bp(640.0) == pytest.approx(0.064)
    assert fetch_bars.mintick_for_bp(100_000.0) == pytest.approx(10.0)
    assert fetch_bars.mintick_for_bp(100_000.0, bp=2.0) == pytest.approx(20.0)


def test_the_docstrings_crypto_tick_would_undercharge_badly():
    # --mintick 0.5 on BTC near 100k is 0.05bp, not the ~1-2bp a real fill
    # costs. Pinning the arithmetic that makes that visible.
    charged_bp = 1e4 * 0.5 / 100_000.0
    assert charged_bp == pytest.approx(0.05)
    assert fetch_bars.mintick_for_bp(100_000.0) / 0.5 == pytest.approx(20.0)


class _FakeExchange:
    """A ccxt exchange that serves a fixed history in bounded batches.

    Enough of the surface fetch_ccxt actually touches -- parse_timeframe,
    milliseconds, fetch_ohlcv -- so the pagination walk is exercised for real
    without a network call, which the suite may not make.
    """

    def __init__(self, rows, now_ms, batch=3):
        self.rows = sorted(rows)
        self.now_ms = now_ms
        self.batch = batch
        self.calls = []

    def parse_timeframe(self, timeframe):
        return {"5m": 300, "1m": 60, "1h": 3600}[timeframe]

    def milliseconds(self):
        return self.now_ms

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls.append(since)
        window = [r for r in self.rows if r[0] >= since]
        return window[: self.batch]


def _bars(n, start=1_700_000_000_000, step=300_000):
    return [
        [start + i * step, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0 + i]
        for i in range(n)
    ]


def test_ccxt_rows_become_the_same_naive_utc_shape_as_the_yfinance_path():
    out = fetch_bars.normalise_ccxt(_bars(3))
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.tz is None
    # 1_700_000_000_000 ms is 2023-11-14 22:13:20 UTC, read with no zone to guess.
    assert out.index[0] == pd.Timestamp("2023-11-14 22:13:20")


def test_pagination_walks_the_whole_history_in_batches():
    rows = _bars(10)
    ex = _FakeExchange(rows, now_ms=rows[-1][0] + 300_000, batch=3)
    out = fetch_bars.fetch_ccxt("BTC/USDT", timeframe="5m", days=1, client=ex)
    assert len(out) == 10
    assert len(ex.calls) > 1, "a 10-bar history in 3-bar batches must paginate"
    assert out.index.is_monotonic_increasing


def test_overlapping_batches_are_deduplicated_rather_than_double_counted():
    rows = _bars(6)
    ex = _FakeExchange(rows + rows, now_ms=rows[-1][0] + 300_000, batch=4)
    out = fetch_bars.fetch_ccxt("BTC/USDT", timeframe="5m", days=1, client=ex)
    assert len(out) == 6
    assert out.index.is_unique


def test_an_exchange_that_stops_making_progress_terminates_the_walk():
    # Termination is on fresh rows, not a count: an exchange answering with
    # bars already collected would otherwise spin forever.
    rows = _bars(4)

    class _Stuck(_FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            self.calls.append(since)
            if len(self.calls) > 50:
                raise AssertionError("fetch_ccxt failed to terminate")
            return self.rows[:2]  # always the same two bars

    ex = _Stuck(rows, now_ms=rows[-1][0] + 10 * 300_000)
    out = fetch_bars.fetch_ccxt("BTC/USDT", timeframe="5m", days=1, client=ex)
    assert len(out) == 2


def test_an_empty_exchange_is_refused_rather_than_written_as_an_empty_file():
    ex = _FakeExchange([], now_ms=1_700_000_000_000)
    with pytest.raises(SystemExit, match="no bars"):
        fetch_bars.fetch_ccxt("NOPE/USDT", timeframe="5m", days=1, client=ex)


def test_days_bounds_how_far_back_the_walk_starts():
    rows = _bars(20)
    now = rows[-1][0] + 300_000
    ex = _FakeExchange(rows, now_ms=now, batch=50)
    fetch_bars.fetch_ccxt("BTC/USDT", timeframe="5m", days=0.01, client=ex)
    assert ex.calls[0] == now - int(0.01 * 86_400_000)

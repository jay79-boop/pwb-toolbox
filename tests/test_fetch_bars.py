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

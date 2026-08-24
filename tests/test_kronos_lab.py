"""kronos_lab's scoring core, exercised without torch, network, or weights.

The contract under test: given a predictor that is provably right (peeks at
the future), provably wrong (peeks and inverts), or provably useless
(persistence), the scorecard must say so. If those three don't come out
right/wrong/useless, no real Kronos number from the tool can be believed.
"""

import math

import pandas as pd
import pytest

from tools.kronos_lab import (
    WindowResult,
    binom_two_sided,
    evaluate,
    future_timestamps,
    load_bars,
    pearson,
    render_scorecard,
    results_frame,
    scorecard,
    spearman,
)


def make_bars(n=200, start=100.0):
    """Deterministic zig-zag bars: alternating +2%/-1% closes, hourly stamps."""
    closes = [start]
    for i in range(1, n):
        closes.append(closes[-1] * (1.02 if i % 2 else 0.99))
    ts = pd.date_range("2026-01-05", periods=n, freq="1h")
    return pd.DataFrame(
        {
            "timestamps": ts,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


def oracle(bars):
    """A predictor that has seen the answer sheet: returns the actual future."""

    def predict_fn(x_df, x_ts, y_ts):
        idx = bars.index[bars["timestamps"].isin(y_ts)]
        return bars.loc[idx, ["open", "high", "low", "close"]].reset_index(drop=True)

    return predict_fn


def inverted_oracle(bars):
    """Right magnitude, wrong sign: mirrors the future through the last close."""

    def predict_fn(x_df, x_ts, y_ts):
        last = float(x_df["close"].iloc[-1])
        idx = bars.index[bars["timestamps"].isin(y_ts)]
        real = bars.loc[idx, ["open", "high", "low", "close"]].reset_index(drop=True)
        return 2 * last - real

    return predict_fn


def persistence(x_df, x_ts, y_ts):
    """The no-information forecast: every future close is the last close."""
    last = float(x_df["close"].iloc[-1])
    return pd.DataFrame(
        {c: [last] * len(y_ts) for c in ("open", "high", "low", "close")}
    )


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def test_binom_two_sided_extremes_and_symmetry():
    assert binom_two_sided(0, 0) == 1.0
    # 10/10 heads two-sided: 2 * (1/1024)
    assert binom_two_sided(10, 10) == pytest.approx(2 / 1024)
    assert binom_two_sided(0, 10) == pytest.approx(2 / 1024)
    # dead-center result is the least extreme possible
    assert binom_two_sided(5, 10) == pytest.approx(1.0)


def test_binom_two_sided_known_value():
    # 8/10: all k with C(10,k) <= C(10,8)=45 -> k in {0,1,2,8,9,10}, mass 112/1024
    assert binom_two_sided(8, 10) == pytest.approx(112 / 1024)


def test_pearson_and_spearman():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)
    assert math.isnan(pearson([1, 1, 1], [1, 2, 3]))  # zero variance
    # spearman is rank-based: monotone but non-linear is still 1.0
    assert spearman([1, 2, 3, 4], [1, 10, 100, 1000]) == pytest.approx(1.0)
    # and ties are averaged, not dropped
    assert spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------


def test_oracle_scores_perfect():
    bars = make_bars()
    results = evaluate(bars, oracle(bars), lookback=50, horizon=5)
    card = scorecard(results)
    assert card["windows"] >= 20
    assert card["hit_rate"] == 1.0
    assert card["p_value"] < 0.001
    assert card["ic_pearson"] == pytest.approx(1.0)
    assert card["mase"] == pytest.approx(0.0)  # oracle's path error is zero
    assert "beat coin-flipping" in render_scorecard(card)


def test_inverted_oracle_scores_zero():
    bars = make_bars()
    results = evaluate(bars, inverted_oracle(bars), lookback=50, horizon=5)
    card = scorecard(results)
    assert card["hit_rate"] == 0.0
    assert card["p_value"] < 0.001  # confidently wrong is also non-random
    assert card["ic_pearson"] == pytest.approx(-1.0)
    # always-wrong direction still fails the verdict gate (ic <= 0)
    assert "noise" in render_scorecard(card)


def test_persistence_has_no_direction_and_mase_one():
    bars = make_bars()
    results = evaluate(bars, persistence, lookback=50, horizon=5)
    card = scorecard(results)
    assert card["decided"] == 0  # pred_ret is exactly 0 everywhere
    assert math.isnan(card["hit_rate"])
    assert card["mase"] == pytest.approx(1.0)  # persistence IS the baseline


def test_evaluate_never_leaks_the_future():
    bars = make_bars()
    seen = []

    def spy(x_df, x_ts, y_ts):
        seen.append((x_ts.iloc[-1], y_ts.iloc[0]))
        return persistence(x_df, x_ts, y_ts)

    evaluate(bars, spy, lookback=50, horizon=5)
    for last_seen, first_predicted in seen:
        assert last_seen < first_predicted


def test_evaluate_windows_do_not_overlap_by_default():
    bars = make_bars()
    results = evaluate(bars, persistence, lookback=50, horizon=5)
    origins = [r.origin for r in results]
    for a, b in zip(origins, origins[1:]):
        assert (b - a) == pd.Timedelta(hours=5)


def test_evaluate_max_windows_keeps_most_recent():
    bars = make_bars()
    all_results = evaluate(bars, persistence, lookback=50, horizon=5)
    capped = evaluate(bars, persistence, lookback=50, horizon=5, max_windows=3)
    assert [r.origin for r in capped] == [r.origin for r in all_results[-3:]]


def test_evaluate_rejects_short_series():
    bars = make_bars(n=30)
    with pytest.raises(ValueError, match="not enough bars"):
        evaluate(bars, persistence, lookback=50, horizon=5)


def test_results_frame_round_trips_returns():
    r = WindowResult(
        origin=pd.Timestamp("2026-01-05"),
        last_close=100.0,
        pred_close=105.0,
        real_close=95.0,
        pred_path_mae=1.0,
        naive_path_mae=2.0,
    )
    frame = results_frame([r])
    assert frame["pred_ret"].iloc[0] == pytest.approx(0.05)
    assert frame["real_ret"].iloc[0] == pytest.approx(-0.05)


# ---------------------------------------------------------------------------
# Data plumbing
# ---------------------------------------------------------------------------


def test_load_bars_accepts_yfinance_shape(tmp_path):
    csv = tmp_path / "bars.csv"
    csv.write_text(
        "Datetime,Open,High,Low,Close,Volume\n"
        "2026-01-06 10:00:00+00:00,10,11,9,10.5,100\n"
        "2026-01-06 09:00:00+00:00,9,10,8,9.5,50\n"  # out of order on purpose
        "2026-01-06 11:00:00+00:00,10.5,12,10,,100\n"  # missing close -> dropped
    )
    bars = load_bars(csv)
    assert list(bars.columns) == [
        "timestamps",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert len(bars) == 2
    assert bars["timestamps"].is_monotonic_increasing


def test_load_bars_rejects_missing_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    csv.write_text("Date,Open,Close\n2026-01-06,1,2\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_bars(csv)


def test_future_timestamps_extends_median_spacing():
    bars = make_bars(n=10)
    future = future_timestamps(bars, 3)
    assert list(future) == list(
        pd.date_range(
            bars["timestamps"].iloc[-1] + pd.Timedelta(hours=1), periods=3, freq="1h"
        )
    )

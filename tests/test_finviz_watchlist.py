"""finviz_scan's watchlist/timing-signal logic -- no network, no finviz.com,
no yfinance. Every signal is a pure function of a constructed bars
DataFrame, same approach as test_crypto_scan.py's synthetic-coin tests.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import tools.finviz_scan as finviz_scan
from tools.finviz_scan import (
    DEFAULT_WATCHLIST_FILE,
    WATCHLIST_MIN_BARS,
    _stamp,
    check_watchlist,
    classify_confluence,
    ema,
    load_watchlist,
    macd_histogram,
    render_watchlist_check,
    rsi,
    run_watchlist_check,
    save_watchlist,
    vet_candidates,
    watchlist_signals,
)


def make_bars(closes, highs=None, lows=None, volumes=None):
    """Bars from a list of closes (oldest first); high/low default to
    close +/- 0.5%, volume defaults to a flat 1_000_000."""
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes * 1.005
    lows = np.asarray(lows, dtype=float) if lows is not None else closes * 0.995
    volumes = (
        np.asarray(volumes, dtype=float)
        if volumes is not None
        else np.full(n, 1_000_000.0)
    )
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def flat_then_move(days=260, flat=200.0, drift=0.0, n_drift=20):
    """`days` bars flat at `flat`, then `n_drift` bars compounding at
    `drift`/bar -- lets a test control exactly how the series ends."""
    closes = [flat] * (days - n_drift)
    last = flat
    for _ in range(n_drift):
        last *= 1 + drift
        closes.append(last)
    return closes


# ---------------------------------------------------------------------------
# ema / rsi / macd_histogram -- basic sanity
# ---------------------------------------------------------------------------


def test_ema_tracks_a_flat_series_exactly():
    s = pd.Series([100.0] * 50)
    assert ema(s, 20).iloc[-1] == pytest.approx(100.0)


def test_ema_short_span_reacts_faster_than_long_span():
    # a late upward jump should move the 5-period EMA further than the 50-period
    s = pd.Series([100.0] * 60 + [150.0] * 5)
    fast = ema(s, 5).iloc[-1]
    slow = ema(s, 50).iloc[-1]
    assert fast > slow


def test_rsi_extremes_on_monotonic_series():
    up = pd.Series(np.linspace(100, 200, 60))
    down = pd.Series(np.linspace(200, 100, 60))
    assert rsi(up, 14).iloc[-1] > 90
    assert rsi(down, 14).iloc[-1] < 10


def test_macd_histogram_positive_when_trending_up_fast():
    s = pd.Series(np.linspace(100, 300, 60))
    assert macd_histogram(s).iloc[-1] > 0


# ---------------------------------------------------------------------------
# watchlist_signals -- the composite per-ticker read
# ---------------------------------------------------------------------------


def test_watchlist_signals_rejects_thin_history():
    bars = make_bars([100.0] * (WATCHLIST_MIN_BARS - 1))
    with pytest.raises(ValueError, match="need"):
        watchlist_signals(bars)


def test_ema_cross_detects_bullish_cross():
    # long flat base (EMA20 == EMA50 == exactly equal), then one sharp jump.
    # EMA20 reacts faster than EMA50 (higher alpha), so a single big move is
    # enough to flip the sign on that exact bar -- more drift days would let
    # the cross happen earlier in the run instead of on the last bar, which
    # is the bar ema_cross actually looks at.
    closes = flat_then_move(days=260, flat=100.0, drift=0.04, n_drift=1)
    signals = watchlist_signals(make_bars(closes))
    assert signals["ema_cross"] == "bullish"


def test_ema_cross_detects_bearish_cross():
    closes = flat_then_move(days=260, flat=100.0, drift=-0.04, n_drift=1)
    signals = watchlist_signals(make_bars(closes))
    assert signals["ema_cross"] == "bearish"


def test_ema_cross_none_on_a_flat_series():
    signals = watchlist_signals(make_bars([100.0] * 260))
    assert signals["ema_cross"] == "none"


def test_ema80_reaction_bounce():
    # trending up, then a one-day dip that touches EMA80 and closes back above it
    closes = flat_then_move(days=200, flat=100.0, drift=0.01, n_drift=60)
    df = make_bars(closes)
    ema80_last = ema(df["close"], 80).iloc[-1]
    # rewrite the last bar: low pierces EMA80, close recovers well above it
    df.loc[df.index[-1], "low"] = ema80_last * 0.999
    df.loc[df.index[-1], "close"] = ema80_last * 1.03
    df.loc[df.index[-1], "high"] = ema80_last * 1.03
    signals = watchlist_signals(df)
    assert signals["ema80_react"] == "bounce"


def test_ema80_reaction_reject():
    # trending down, then a one-day rally that touches EMA80 and closes back below it
    closes = flat_then_move(days=200, flat=100.0, drift=-0.01, n_drift=60)
    df = make_bars(closes)
    ema80_last = ema(df["close"], 80).iloc[-1]
    df.loc[df.index[-1], "high"] = ema80_last * 1.001
    df.loc[df.index[-1], "close"] = ema80_last * 0.97
    df.loc[df.index[-1], "low"] = ema80_last * 0.97
    signals = watchlist_signals(df)
    assert signals["ema80_react"] == "reject"


def test_ema80_reaction_none_when_price_never_gets_close():
    closes = flat_then_move(days=200, flat=100.0, drift=0.01, n_drift=60)
    signals = watchlist_signals(make_bars(closes))
    assert signals["ema80_react"] == "none"


def test_rsi_signal_labels():
    overbought = watchlist_signals(make_bars(np.linspace(100, 300, 260)))
    oversold = watchlist_signals(make_bars(np.linspace(300, 100, 260)))
    neutral = watchlist_signals(make_bars([100.0] * 260))
    assert overbought["rsi_signal"] == "overbought"
    assert oversold["rsi_signal"] == "oversold"
    assert neutral["rsi_signal"] == "neutral"


def test_near_52w_high_and_low():
    closes = list(np.linspace(100, 200, 252))  # steadily rising, ends at the high
    high_signals = watchlist_signals(make_bars(closes))
    assert high_signals["near_52w"] == "high"
    assert high_signals["from_52w_high"] == pytest.approx(0.0, abs=0.01)

    low_signals = watchlist_signals(make_bars(list(reversed(closes))))
    assert low_signals["near_52w"] == "low"
    assert low_signals["from_52w_low"] == pytest.approx(0.0, abs=0.01)


def test_volume_surge_detects_a_spike():
    volumes = [1_000_000.0] * 260
    volumes[-1] = 4_000_000.0
    signals = watchlist_signals(make_bars([100.0] * 260, volumes=volumes))
    assert signals["volume_surge"] == pytest.approx(3.0, rel=0.05)


def test_ma_cross_none_without_enough_history_for_sma200():
    signals = watchlist_signals(make_bars([100.0] * WATCHLIST_MIN_BARS))
    assert signals["ma_cross"] == "none"


# ---------------------------------------------------------------------------
# classify_confluence
# ---------------------------------------------------------------------------


def test_classify_confluence_counts_and_direction():
    all_bullish = {
        "ema_cross": "bullish",
        "ema80_react": "bounce",
        "rsi_signal": "oversold",
        "ma_cross": "golden",
    }
    result = classify_confluence(all_bullish)
    assert result == {"bullish_count": 4, "bearish_count": 0, "direction": "bullish"}


def test_classify_confluence_mixed():
    mixed = {
        "ema_cross": "bullish",
        "ema80_react": "reject",
        "rsi_signal": "neutral",
        "ma_cross": "none",
    }
    result = classify_confluence(mixed)
    assert result == {"bullish_count": 1, "bearish_count": 1, "direction": "mixed"}


def test_classify_confluence_none_when_nothing_fires():
    quiet = {
        "ema_cross": "none",
        "ema80_react": "none",
        "rsi_signal": "neutral",
        "ma_cross": "none",
    }
    result = classify_confluence(quiet)
    assert result == {"bullish_count": 0, "bearish_count": 0, "direction": "mixed"}


# ---------------------------------------------------------------------------
# run_watchlist_check / render_watchlist_check
# ---------------------------------------------------------------------------


def test_run_watchlist_check_skips_thin_history_and_scores_the_rest():
    bars = {
        "AAPL": make_bars(flat_then_move(days=260, flat=100.0, drift=0.04, n_drift=1)),
        "THIN": make_bars([100.0] * 10),
    }
    results, skipped = run_watchlist_check(bars)
    assert skipped == ["THIN"]
    assert "AAPL" in results
    assert results["AAPL"]["confluence"]["direction"] == "bullish"


def test_render_watchlist_check_flags_only_strong_confluence():
    results = {
        "STRONG": {
            "last": 100.0,
            "ema20": 101,
            "ema50": 99,
            "ema80": 98,
            "ema_cross": "bullish",
            "ema80_react": "bounce",
            "rsi14": 25.0,
            "rsi_signal": "oversold",
            "ma_cross": "golden",
            "near_52w": "none",
            "from_52w_high": -0.1,
            "from_52w_low": 0.2,
            "volume_surge": 0.5,
            "macd_hist": 1.2,
            "bars": 260,
            "confluence": {
                "bullish_count": 4,
                "bearish_count": 0,
                "direction": "bullish",
            },
        },
        "WEAK": {
            "last": 50.0,
            "ema20": 50.1,
            "ema50": 50.0,
            "ema80": 49.9,
            "ema_cross": "none",
            "ema80_react": "none",
            "rsi14": 55.0,
            "rsi_signal": "neutral",
            "ma_cross": "none",
            "near_52w": "none",
            "from_52w_high": -0.2,
            "from_52w_low": 0.3,
            "volume_surge": float("nan"),
            "macd_hist": 0.0,
            "bars": 260,
            "confluence": {
                "bullish_count": 0,
                "bearish_count": 0,
                "direction": "mixed",
            },
        },
    }
    report, flagged = render_watchlist_check(results, skipped=["DEAD"])
    assert flagged == ["STRONG"]
    assert "STRONG  <-- FLAGGED" in report
    assert "WEAK  <-- FLAGGED" not in report
    assert "no indicator finds perfect timing" in report.lower()
    assert "DEAD" in report  # skipped tickers still get reported


def test_render_watchlist_check_handles_empty_results():
    report, flagged = render_watchlist_check({}, skipped=[])
    assert flagged == []
    assert "Nothing to check" in report


def test_check_watchlist_reports_a_bad_ticker_as_no_data_not_thin_history(
    monkeypatch,
):
    # Live 2026-09-15: a misspelled ticker got "need 100+ bars of history",
    # which sends you looking for the wrong problem.
    def fake_fetch_bars(symbols):
        return {
            "AAPL": make_bars(
                flat_then_move(days=260, flat=100.0, drift=0.04, n_drift=1)
            ),
            "THIN": make_bars([100.0] * 10),
        }

    monkeypatch.setattr(finviz_scan, "fetch_bars", fake_fetch_bars)
    report, flagged = check_watchlist(["AAPL", "THIN", "ZZZZQQ"])
    assert "skipped (need 100+ bars of history): THIN" in report
    assert "no price data (check the spelling, or it may be delisted): ZZZZQQ" in report
    assert "ZZZZQQ" not in report.split("no price data")[0]


# ---------------------------------------------------------------------------
# vet_candidates -- same signal pipeline, fed a screener's ticker list
# instead of the watchlist. The bug this closes: the screener would list
# matches with nothing telling you which ones were actually worth tracking,
# and the only way to get one onto the watchlist was retyping it by hand.
# ---------------------------------------------------------------------------


def test_vet_candidates_flags_a_planted_confluence_and_acquits_a_flat_one(monkeypatch):
    def fake_fetch_bars(symbols):
        return {
            # 259 flat days then one sharp jump: EMA20/50 cross bullish and
            # the 50/200-SMA golden-cross, a real 2-signal confluence -- not
            # just noise that happens to hit the threshold once.
            "HOT": make_bars([100.0] * 259 + [140.0]),
            # Flat the whole way: nothing should fire.
            "COLD": make_bars([100.0] * 260),
        }

    monkeypatch.setattr(finviz_scan, "fetch_bars", fake_fetch_bars)
    report, flagged = vet_candidates(["HOT", "COLD"])
    assert flagged == ["HOT"]
    assert "COLD" in report
    assert "COLD  <-- FLAGGED" not in report


def test_vet_candidates_report_reads_as_candidate_vetting_not_watchlist_check(
    monkeypatch,
):
    # The one thing that must differ from check_watchlist's report: nothing
    # here is the tracked watchlist, and the heading should say so rather
    # than reusing check's wording verbatim.
    def fake_fetch_bars(symbols):
        return {"AAPL": make_bars([100.0] * 260)}

    monkeypatch.setattr(finviz_scan, "fetch_bars", fake_fetch_bars)
    report, _flagged = vet_candidates(["AAPL"])
    assert "candidate vetting" in report
    assert "watchlist check" not in report


def test_vet_candidates_same_no_data_and_thin_history_handling_as_check(monkeypatch):
    def fake_fetch_bars(symbols):
        return {
            "AAPL": make_bars(
                flat_then_move(days=260, flat=100.0, drift=0.04, n_drift=1)
            ),
            "THIN": make_bars([100.0] * 10),
        }

    monkeypatch.setattr(finviz_scan, "fetch_bars", fake_fetch_bars)
    report, _flagged = vet_candidates(["AAPL", "THIN", "ZZZZQQ"])
    assert "skipped (need 100+ bars of history): THIN" in report
    assert "no price data (check the spelling, or it may be delisted): ZZZZQQ" in report


def test_default_watchlist_is_anchored_to_the_repo_not_the_current_folder():
    path = Path(DEFAULT_WATCHLIST_FILE)
    assert path.is_absolute()
    assert path == Path(finviz_scan.__file__).resolve().parent.parent / (
        "finviz/watchlist.txt"
    )


def test_stamp_has_seconds_so_same_minute_runs_do_not_overwrite():
    # Live 2026-09-15: two checks in one minute saved to the same file.
    assert len(_stamp()) == len("2026-09-15_020930")


def test_watchlist_round_trips_as_utf8(tmp_path):
    path = tmp_path / "watchlist.txt"
    path.write_bytes("AAPL  # Apple — core\n".encode("utf-8"))
    assert load_watchlist(path) == ["AAPL"]


# ---------------------------------------------------------------------------
# watchlist file I/O
# ---------------------------------------------------------------------------


def test_load_watchlist_missing_file_is_empty(tmp_path):
    assert load_watchlist(tmp_path / "nope.txt") == []


def test_save_then_load_round_trips_deduped_and_uppercased(tmp_path):
    path = tmp_path / "watchlist.txt"
    save_watchlist(path, ["aapl", "MSFT", "aapl"])
    assert load_watchlist(path) == ["AAPL", "MSFT"]


def test_load_watchlist_ignores_comments_and_blank_lines(tmp_path):
    path = tmp_path / "watchlist.txt"
    path.write_text("AAPL\n# a comment line\n\nMSFT  # inline comment\n   \n")
    assert load_watchlist(path) == ["AAPL", "MSFT"]


def test_save_watchlist_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "watchlist.txt"
    save_watchlist(path, ["NVDA"])
    assert load_watchlist(path) == ["NVDA"]

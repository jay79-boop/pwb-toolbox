"""crypto_scan's scoring core on synthetic bars — no network, no yfinance.

The contract: a coin engineered to trend up must outrank one engineered to
trend down, direction labels must require both relative strength and an
actual move, and thin history must be skipped loudly rather than scored.
"""

import math

import pandas as pd
import pytest

from tools.crypto_scan import (
    MIN_BARS,
    btc_regime,
    coin_signals,
    run_scan,
    score_universe,
)


def make_coin(days=120, drift=0.0, start=100.0, volume=1000.0, last_week_volume=None):
    """Daily bars with a constant per-day drift and controllable volume."""
    closes = [start * (1 + drift) ** i for i in range(days)]
    vols = [volume] * days
    if last_week_volume is not None:
        vols[-7:] = [last_week_volume] * 7
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vols,
        }
    )


def test_coin_signals_directions_and_magnitudes():
    up = coin_signals(make_coin(drift=0.01))
    down = coin_signals(make_coin(drift=-0.01))
    assert up["ret_7d"] == pytest.approx(1.01**7 - 1)
    assert up["ret_28d"] > up["ret_7d"] > 0
    assert up["trend_vs_sma50"] > 0
    assert down["ret_7d"] < 0 and down["trend_vs_sma50"] < 0
    # constant 2% high-low range around a ~flat price -> ATR% near 2%
    flat = coin_signals(make_coin(drift=0.0))
    assert flat["atr_pct"] == pytest.approx(0.02, abs=0.002)
    assert flat["volume_surge"] == pytest.approx(0.0)


def test_coin_signals_volume_surge():
    surging = coin_signals(make_coin(last_week_volume=3000.0))
    assert surging["volume_surge"] == pytest.approx(2.0)


def test_coin_signals_rejects_thin_history():
    with pytest.raises(ValueError, match="need"):
        coin_signals(make_coin(days=MIN_BARS - 1))


def frame_of(**coins):
    return pd.DataFrame({name: coin_signals(bars) for name, bars in coins.items()}).T


def test_score_universe_ranks_up_over_down():
    scored = score_universe(
        frame_of(
            UPP=make_coin(drift=0.02, last_week_volume=3000.0),
            MID=make_coin(drift=0.0),
            DWN=make_coin(drift=-0.02, last_week_volume=500.0),
        )
    )
    assert list(scored.index) == ["UPP", "MID", "DWN"]
    assert scored.loc["UPP", "setup"] == "upside"
    assert scored.loc["DWN", "setup"] == "downside"
    assert scored.loc["UPP", "score"] > 0 > scored.loc["DWN", "score"]


def test_flat_coin_is_never_a_direction_call():
    # Even ranked top of a weak field, a coin that hasn't moved stays neutral.
    scored = score_universe(
        frame_of(
            FLAT=make_coin(drift=0.0),
            DWN1=make_coin(drift=-0.01),
            DWN2=make_coin(drift=-0.02),
        )
    )
    assert scored.loc["FLAT", "setup"] == "neutral"


def test_btc_regime_flags_falling_market():
    signals = frame_of(
        **{"BTC-USD": make_coin(drift=-0.01), "ETH-USD": make_coin(drift=0.01)}
    )
    line = btc_regime(signals)
    assert "falling" in line
    assert "fight" in line  # the alt-longs warning
    rising = btc_regime(frame_of(**{"BTC-USD": make_coin(drift=0.01)}))
    assert "rising" in rising and "fight" not in rising


def test_btc_regime_without_btc():
    line = btc_regime(frame_of(**{"ETH-USD": make_coin(drift=0.01)}))
    assert "not in scan universe" in line


def test_run_scan_end_to_end_skips_thin_and_renders():
    text = run_scan(
        {
            "UPP-USD": make_coin(drift=0.02),
            "DWN-USD": make_coin(drift=-0.02),
            "THIN-USD": make_coin(days=10),
        },
        top=5,
    )
    assert "UPP-USD" in text and "DWN-USD" in text
    assert "THIN-USD" in text and "insufficient history" in text
    assert "paper" in text  # the discipline footer is part of the contract


def test_run_scan_all_thin_raises():
    with pytest.raises(SystemExit, match="no symbol"):
        run_scan({"A-USD": make_coin(days=5)}, top=3)

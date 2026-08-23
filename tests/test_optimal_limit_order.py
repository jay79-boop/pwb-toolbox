"""Regression tests for `pwb_toolbox.execution.optimal_limit_order`.

Locks in the fix for a bug where `get_optimal_quote` ignored every parameter
except `symbol`/`quantity`/`time_in_seconds` (an `if symbol == "demo" or True:`
always took the same hardcoded-demo branch), so every instrument silently got
identical, non-calibrated pricing.
"""

import math

from pwb_toolbox.execution.optimal_limit_order import get_optimal_quote
from pwb_toolbox.execution.config import OptimalQuoteConfig

# The exact value `get_optimal_quote("AAPL", 500, 600)` returned before the fix,
# under the old hardcoded parameters. New defaults must reproduce it exactly so
# existing callers see no behavior change.
_LEGACY_DEFAULT_QUOTE = 0.046683518913258094


def test_default_call_matches_legacy_output():
    config = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
    )
    quote = get_optimal_quote(config)
    assert math.isclose(quote, _LEGACY_DEFAULT_QUOTE, rel_tol=1e-9)


def test_symbol_argument_no_longer_ignored_when_calibrated():
    default_config = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
    )
    default = get_optimal_quote(default_config)

    calibrated_config = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
        sigma=0.9,
        k=0.1,
        A=0.05,
    )
    calibrated = get_optimal_quote(calibrated_config)
    assert calibrated != default


def test_different_calibrations_produce_different_quotes():
    config_a = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
        sigma=0.9,
        k=0.1,
        A=0.05,
    )
    quote_a = get_optimal_quote(config_a)

    config_b = OptimalQuoteConfig(
        symbol="TSLA",
        quantity=500,
        time_in_seconds=600,
        sigma=1.5,
        k=0.05,
        A=0.2,
    )
    quote_b = get_optimal_quote(config_b)
    assert quote_a != quote_b


def test_tick_size_scales_quote():
    config_penny = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
        tick_size=0.01,
    )
    quote_penny = get_optimal_quote(config_penny)

    config_nickel = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
        tick_size=0.05,
    )
    quote_nickel = get_optimal_quote(config_nickel)
    assert quote_penny != quote_nickel


def test_quote_is_always_finite():
    config = OptimalQuoteConfig(
        symbol="AAPL",
        quantity=500,
        time_in_seconds=600,
        sigma=5.0,
        k=0.9,
        A=0.9,
        b=50,
    )
    quote = get_optimal_quote(config)
    assert math.isfinite(quote)

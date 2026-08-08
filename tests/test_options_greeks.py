import math

import pytest

from pwb_toolbox.options import black_scholes, expected_move

# Textbook reference case: S=100, K=100, 1 year, r=5%, vol=20%.
# Call = 10.4506, put by parity = 5.5735.
REF = dict(spot=100.0, strike=100.0, days_to_expiry=365.0, vol=0.20, rate=0.05)


def test_call_price_matches_reference():
    assert black_scholes(**REF, kind="call").price == pytest.approx(10.4506, abs=1e-4)


def test_put_price_matches_reference():
    assert black_scholes(**REF, kind="put").price == pytest.approx(5.5735, abs=1e-4)


def test_put_call_parity():
    call = black_scholes(**REF, kind="call").price
    put = black_scholes(**REF, kind="put").price
    discounted_strike = REF["strike"] * math.exp(-REF["rate"] * 1.0)
    assert call - put == pytest.approx(REF["spot"] - discounted_strike, abs=1e-8)


def test_reference_greeks():
    g = black_scholes(**REF, kind="call")
    assert g.delta == pytest.approx(0.6368306512, abs=1e-9)
    assert g.gamma == pytest.approx(0.0187620173, abs=1e-9)
    assert g.vega == pytest.approx(0.3752403469, abs=1e-9)
    assert g.theta == pytest.approx(-0.0175726782, abs=1e-9)


def test_put_delta_is_call_delta_minus_one():
    call = black_scholes(**REF, kind="call")
    put = black_scholes(**REF, kind="put")
    assert put.delta == pytest.approx(call.delta - 1.0, abs=1e-12)
    assert -1.0 <= put.delta <= 0.0


def test_gamma_and_vega_match_across_kinds():
    call = black_scholes(**REF, kind="call")
    put = black_scholes(**REF, kind="put")
    assert call.gamma == pytest.approx(put.gamma, abs=1e-12)
    assert call.vega == pytest.approx(put.vega, abs=1e-12)


def test_price_decomposes_into_intrinsic_and_extrinsic():
    g = black_scholes(spot=110.0, strike=100.0, days_to_expiry=30.0, vol=0.30)
    assert g.intrinsic == pytest.approx(10.0)
    assert g.intrinsic + g.extrinsic == pytest.approx(g.price, abs=1e-12)
    assert g.extrinsic > 0


def test_out_of_the_money_has_no_intrinsic():
    g = black_scholes(spot=90.0, strike=100.0, days_to_expiry=30.0, vol=0.30)
    assert g.intrinsic == 0.0
    assert g.extrinsic == pytest.approx(g.price, abs=1e-12)


def test_long_options_decay_across_the_tradeable_range():
    """Every contract a directional buyer would realistically hold bleeds."""
    for kind in ("call", "put"):
        for strike in (80.0, 100.0, 120.0):
            g = black_scholes(100.0, strike, 30.0, 0.30, kind=kind)
            if kind == "put" and g.delta < -0.95:
                continue  # covered by the deep-ITM case below
            assert g.theta < 0, f"{kind} @ {strike} should decay"


def test_deep_itm_european_put_has_positive_theta():
    """Not a bug: a European put you cannot exercise early trades below
    intrinsic, and that discount unwinds in the holder's favour over time.
    An American put would carry an early-exercise premium this model omits.
    """
    g = black_scholes(100.0, 120.0, 30.0, 0.30, kind="put")
    assert g.delta < -0.95
    assert g.theta > 0
    assert g.extrinsic < 0
    assert g.price < g.intrinsic


def test_extrinsic_shrinks_as_expiry_approaches():
    values = [
        black_scholes(100.0, 100.0, dte, 0.30).extrinsic for dte in (60, 30, 14, 7, 1)
    ]
    assert values == sorted(values, reverse=True)


def test_expected_move_scales_with_root_time():
    one_day = expected_move(100.0, 0.30, 1.0)
    four_days = expected_move(100.0, 0.30, 4.0)
    assert four_days == pytest.approx(2.0 * one_day, abs=1e-12)
    assert expected_move(100.0, 0.30, 0.0) == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(spot=100.0, strike=100.0, days_to_expiry=0.0, vol=0.3),
        dict(spot=100.0, strike=100.0, days_to_expiry=-5.0, vol=0.3),
        dict(spot=0.0, strike=100.0, days_to_expiry=30.0, vol=0.3),
        dict(spot=100.0, strike=100.0, days_to_expiry=30.0, vol=0.0),
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        black_scholes(**kwargs)


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="call.*put"):
        black_scholes(100.0, 100.0, 30.0, 0.3, kind="straddle")

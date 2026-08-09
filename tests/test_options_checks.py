import datetime as dt

import pytest

from pwb_toolbox.options import (
    FAIL,
    PASS,
    UNKNOWN,
    check_delta,
    check_dte,
    check_earnings,
    check_hurdle,
    check_iv_rank,
    check_size,
    check_spread,
    check_target,
    run_all,
    verdict,
)

TODAY = dt.date(2026, 8, 8)


# --- position gates -------------------------------------------------------


def test_size_passes_within_the_limit():
    assert check_size(cost=800, account=20_000).status == PASS


def test_size_fails_over_the_limit():
    c = check_size(cost=940, account=20_000)
    assert c.status == FAIL
    assert "4.7%" in c.detail


@pytest.mark.parametrize(
    "dte,status", [(38, PASS), (30, PASS), (45, PASS), (5, FAIL), (120, FAIL)]
)
def test_dte_band(dte, status):
    assert check_dte(dte).status == status


def test_short_dated_and_overlong_fail_for_different_reasons():
    assert "short" in check_dte(5).detail
    assert "longer than needed" in check_dte(120).detail


@pytest.mark.parametrize(
    "delta,status", [(0.63, PASS), (0.55, PASS), (0.20, FAIL), (0.95, FAIL)]
)
def test_delta_band(delta, status):
    assert check_delta(delta).status == status


def test_put_delta_is_judged_on_magnitude():
    assert check_delta(-0.65).status == PASS
    assert check_delta(-0.20).status == FAIL


def test_hurdle_gate():
    assert check_hurdle(0.05).status == PASS
    assert check_hurdle(0.26).status == FAIL


# --- IV rank --------------------------------------------------------------


def test_iv_rank_unsupplied_is_unknown_not_pass():
    assert check_iv_rank(None).status == UNKNOWN


def test_low_iv_rank_passes_and_is_called_cheap():
    c = check_iv_rank(18)
    assert c.status == PASS
    assert "cheap" in c.detail


def test_middling_iv_rank_passes_but_is_flagged():
    c = check_iv_rank(42)
    assert c.status == PASS
    assert "not cheap" in c.detail


def test_high_iv_rank_fails_because_you_are_a_buyer():
    assert check_iv_rank(78).status == FAIL


# --- spread ---------------------------------------------------------------


def test_spread_unsupplied_is_unknown():
    assert check_spread(None, None).status == UNKNOWN
    assert check_spread(9.30, None).status == UNKNOWN


def test_tight_spread_passes():
    c = check_spread(9.35, 9.45)  # 0.10 on a 9.40 mid = 1.1%
    assert c.status == PASS
    assert "tight" in c.detail


def test_merely_acceptable_spread_passes_but_says_so():
    c = check_spread(9.30, 9.50)  # 0.20 on a 9.40 mid = 2.1%
    assert c.status == PASS
    assert "acceptable" in c.detail


def test_wide_spread_fails():
    c = check_spread(2.00, 2.20)  # 0.20 on a 2.10 mid = 9.5%
    assert c.status == FAIL
    assert "9.5%" in c.detail


def test_the_cheap_weekly_is_penalised_by_relative_spread():
    """An identical $0.15 spread is 1.6% of a $9.38 contract and 7.2% of a $2.08 one."""
    assert check_spread(9.30, 9.45).status == PASS
    assert check_spread(2.00, 2.15).status == FAIL


def test_inverted_or_nonsensical_quote_fails():
    assert check_spread(5.00, 4.00).status == FAIL
    assert check_spread(0.0, 0.0).status == FAIL


# --- earnings -------------------------------------------------------------


def test_earnings_unsupplied_is_unknown():
    assert check_earnings(None, dte=38, today=TODAY).status == UNKNOWN


def test_earnings_can_be_declared_clear():
    c = check_earnings(None, dte=38, today=TODAY, declared_none=True)
    assert c.status == PASS
    assert "declared clear" in c.detail


def test_earnings_before_the_exit_fails():
    # 38 DTE means the 21-DTE exit is ~17 days out, i.e. 2026-08-25.
    c = check_earnings(dt.date(2026, 8, 20), dte=38, today=TODAY)
    assert c.status == FAIL
    assert "IV crush" in c.detail


def test_earnings_after_the_exit_passes():
    c = check_earnings(dt.date(2026, 9, 30), dte=38, today=TODAY)
    assert c.status == PASS


def test_earnings_exactly_on_the_exit_date_fails():
    c = check_earnings(dt.date(2026, 8, 25), dte=38, today=TODAY)
    assert c.status == FAIL


# --- target vs expected move ---------------------------------------------


def test_target_unsupplied_is_unknown():
    assert check_target(None, 232.0, 0.28, 38).status == UNKNOWN


def test_target_inside_the_expected_move_passes():
    c = check_target(236.0, spot=232.0, vol=0.28, dte=38, kind="call")
    assert c.status == PASS
    assert "sigma" in c.detail


def test_target_beyond_the_expected_move_fails():
    c = check_target(280.0, spot=232.0, vol=0.28, dte=38, kind="call")
    assert c.status == FAIL
    assert "1.5 limit" in c.detail


def test_target_between_one_and_the_limit_passes_but_is_flagged():
    """The band the 1.5 sigma gate opened up: admitted, and labelled stretching."""
    c = check_target(249.0, spot=232.0, vol=0.28, dte=38, kind="call")
    assert 1.0 < float(c.detail.split()[1]) <= 1.5
    assert c.status == PASS
    assert "stretching" in c.detail


def test_target_just_past_the_limit_fails():
    c = check_target(255.0, spot=232.0, vol=0.28, dte=38, kind="call")
    assert float(c.detail.split()[1]) > 1.5
    assert c.status == FAIL


def test_target_limit_is_configurable():
    stretch = dict(target=249.0, spot=232.0, vol=0.28, dte=38, kind="call")
    assert check_target(**stretch, limit=1.5).status == PASS
    assert check_target(**stretch, limit=1.0).status == FAIL


def test_target_on_the_wrong_side_of_spot_fails():
    assert check_target(220.0, 232.0, 0.28, 38, kind="call").status == FAIL
    assert check_target(240.0, 232.0, 0.28, 38, kind="put").status == FAIL


def test_put_target_below_spot_is_judged_normally():
    assert check_target(228.0, 232.0, 0.28, 38, kind="put").status == PASS


# --- the whole card -------------------------------------------------------


BASE = dict(
    spot=232.0,
    strike=230.0,
    dte=38.0,
    vol=0.28,
    premium=7.60,
    contracts=1,
    account=20_000.0,
    kind="call",
    hurdle=0.05,
)


def test_a_fully_answered_good_trade_clears():
    checks = run_all(
        **BASE,
        iv_rank=22,
        bid=7.55,
        ask=7.65,
        declared_no_earnings=True,
        target=238.0,
        today=TODAY,
    )
    assert verdict(checks) == PASS
    assert all(c.ok for c in checks)


def test_an_unanswered_card_is_incomplete_not_passing():
    checks = run_all(**BASE)
    assert verdict(checks) == UNKNOWN
    unknown = {c.name for c in checks if c.status == UNKNOWN}
    assert unknown == {
        "IV rank",
        "bid/ask spread",
        "earnings",
        "target within expected move",
    }


def test_a_failure_outranks_an_unknown():
    checks = run_all(**BASE, iv_rank=90)
    assert verdict(checks) == FAIL


def test_run_all_returns_every_gate():
    assert len(run_all(**BASE)) == 8


def test_the_typical_bad_trade_fails_several_gates():
    checks = run_all(
        spot=340.0,
        strike=360.0,
        dte=5.0,
        vol=0.55,
        premium=2.10,
        contracts=4,
        account=20_000.0,
        kind="call",
        hurdle=0.26,
        iv_rank=72,
        bid=2.00,
        ask=2.20,
        declared_no_earnings=True,
        target=375.0,
        today=TODAY,
    )
    failed = {c.name for c in checks if c.status == FAIL}
    assert {"days to expiry", "delta", "decay hurdle", "IV rank"} <= failed
    assert verdict(checks) == FAIL

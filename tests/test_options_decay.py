import pytest

from pwb_toolbox.options import (
    black_scholes,
    breakeven_spot,
    decay_schedule,
    drop_dead,
    hurdle_ratio,
    recovery,
)


def test_hurdle_rises_as_expiry_approaches():
    ratios = [hurdle_ratio(100.0, 100.0, dte, 0.30) for dte in (60, 45, 30, 14, 7, 3)]
    assert ratios == sorted(ratios)


def test_hurdle_is_worse_for_short_dated_contracts():
    """The core argument for buying more time than the thesis needs."""
    long_dated = hurdle_ratio(100.0, 100.0, 45.0, 0.30)
    weekly = hurdle_ratio(100.0, 100.0, 5.0, 0.30)
    assert weekly > 2 * long_dated


def test_breakeven_spot_round_trips():
    target = 4.0
    s = breakeven_spot(target, strike=100.0, days_to_expiry=20.0, vol=0.30)
    assert s is not None
    assert black_scholes(s, 100.0, 20.0, 0.30).price == pytest.approx(target, abs=1e-6)


def test_breakeven_spot_round_trips_for_puts():
    target = 3.5
    s = breakeven_spot(target, strike=100.0, days_to_expiry=20.0, vol=0.30, kind="put")
    assert s is not None
    price = black_scholes(s, 100.0, 20.0, 0.30, kind="put").price
    assert price == pytest.approx(target, abs=1e-6)


def test_breakeven_spot_unreachable_for_put_above_discounted_strike():
    # A put can never be worth more than the discounted strike.
    assert breakeven_spot(200.0, 100.0, 20.0, 0.30, kind="put") is None


def test_recovery_at_entry_shows_no_loss():
    entry = black_scholes(100.0, 100.0, 45.0, 0.30).price
    r = recovery(entry, 100.0, 100.0, 45.0, 0.30, horizon_days=1.0)
    assert r.loss_per_share <= 0
    assert "at or above entry" in r.verdict


def test_recovery_grows_as_position_moves_against_you():
    entry = black_scholes(100.0, 100.0, 45.0, 0.30).price
    sigmas = [
        recovery(entry, spot, 100.0, 40.0, 0.30, horizon_days=19.0).sigma_required
        for spot in (99.0, 97.0, 94.0, 90.0)
    ]
    assert sigmas == sorted(sigmas)


def test_deep_loss_late_in_life_is_flagged_for_exit():
    entry = black_scholes(100.0, 105.0, 45.0, 0.30).price
    r = recovery(entry, 88.0, 105.0, 10.0, 0.30, horizon_days=9.0)
    assert r.sigma_required > 1.0
    assert r.verdict.startswith("EXIT")


def test_recovery_reports_the_current_premium():
    r = recovery(5.0, 97.0, 100.0, 30.0, 0.30)
    expected = black_scholes(97.0, 100.0, 30.0, 0.30).price
    assert r.current_premium == pytest.approx(expected, abs=1e-12)
    assert r.loss_per_share == pytest.approx(5.0 - expected, abs=1e-12)


def test_drop_dead_returns_a_date_inside_the_contract_life():
    entry = black_scholes(100.0, 105.0, 45.0, 0.30).price
    dd = drop_dead(entry, 96.0, 105.0, 45.0, 0.30)
    assert dd.days_to_expiry is not None
    assert 21.0 <= dd.days_to_expiry <= 45.0
    assert dd.sessions_from_now >= 0


def test_drop_dead_short_circuits_inside_the_exit_window():
    dd = drop_dead(5.0, 100.0, 100.0, 14.0, 0.30)
    assert dd.sessions_from_now == 0.0
    assert "21-DTE exit" in dd.reason


def test_drop_dead_arrives_sooner_for_a_worse_position():
    entry = black_scholes(100.0, 105.0, 60.0, 0.30).price
    near = drop_dead(entry, 99.0, 105.0, 60.0, 0.30).sessions_from_now
    far = drop_dead(entry, 92.0, 105.0, 60.0, 0.30).sessions_from_now
    assert far <= near


def test_decay_schedule_is_monotonic_and_bounded():
    rows = decay_schedule(100.0, 100.0, 45.0, 0.30)
    dtes = [r[0] for r in rows]
    extrinsics = [r[1] for r in rows]
    assert dtes == sorted(dtes, reverse=True)
    assert extrinsics == sorted(extrinsics, reverse=True)
    assert all(0 <= r[2] <= 100.0 for r in rows)


def test_decay_schedule_skips_checkpoints_beyond_expiry():
    rows = decay_schedule(100.0, 100.0, 20.0, 0.30)
    assert all(r[0] <= 20.0 for r in rows)


def test_half_of_extrinsic_is_gone_well_before_half_the_time():
    """Decay is front-loaded against the holder: 21 of 45 days costs >21% of value."""
    rows = dict((r[0], r[2]) for r in decay_schedule(100.0, 100.0, 45.0, 0.30))
    assert rows[21] < 100.0 * (21.0 / 45.0) ** 0.5 + 1.0
    assert rows[7] < 50.0

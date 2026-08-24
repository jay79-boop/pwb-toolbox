"""prop_sim — pricing an eval against the arithmetic, not the pitch.

The contract:

    - with no complications the Monte Carlo agrees with gambler's ruin,
      P(pass) = D/(T+D) -- the closed form anchors the whole engine;
    - position size does NOT change pass odds for the pure symmetric walk,
      and DOES through the rules: a time limit rewards size, a consistency
      rule taxes it, a trailing drawdown is strictly worse than fixed;
    - EV falls as fees rise, deterministically for a given seed;
    - real exported trades (night-lab shape) convert to the same R the rest
      of the repo computes, shorts included.

Everything seeded, stdlib, no network.
"""

import pytest

from tools.prop_sim import (
    Rules,
    coin_flip,
    empirical,
    price,
    r_from_records,
    run_eval,
)
import random


def bare_rules(**over):
    """No time limit, no consistency, fixed drawdown: pure gambler's ruin."""
    base = dict(
        profit_target=2000.0,
        max_drawdown=1000.0,
        drawdown_mode="fixed",
        eval_fee=100.0,
        activation_fee=0.0,
        min_days=0,
        max_days=None,
        trades_per_day=1,
        consistency_pct=None,
    )
    base.update(over)
    return Rules(**base)


def pass_rate(rules, risk=1000.0, sims=3000, seed=11, draw=None):
    rng = random.Random(seed)
    draw = draw or coin_flip()
    return (
        sum(run_eval(rng, draw, risk, rules).get("passed") for _ in range(sims)) / sims
    )


# ---------------------------------------------------------------------------
# The closed form anchors the engine
# ---------------------------------------------------------------------------


def test_monte_carlo_agrees_with_gamblers_ruin():
    # T=2 steps, D=1 step of +-1000: P = D/(T+D) = 1/3.
    got = pass_rate(bare_rules(), risk=1000.0)
    assert got == pytest.approx(1 / 3, abs=0.03)


def test_size_does_not_move_the_pure_walk():
    # The stream's "sizing optimizes odds" is FALSE for the bare walk: with
    # integer step ratios the ruin probability is size-invariant.
    big = pass_rate(bare_rules(), risk=1000.0)
    small = pass_rate(bare_rules(), risk=100.0, sims=2000)
    assert big == pytest.approx(small, abs=0.04)


# ---------------------------------------------------------------------------
# ...and the rules are where sizing starts to matter
# ---------------------------------------------------------------------------


def test_a_time_limit_rewards_size():
    limited = dict(max_days=20, trades_per_day=3)
    assert pass_rate(bare_rules(**limited), risk=1000.0) > pass_rate(
        bare_rules(**limited), risk=100.0
    )


def test_a_consistency_rule_taxes_size():
    # One giant day cannot carry the pass; big steps concentrate profit.
    ruled = dict(consistency_pct=0.5, max_days=40, trades_per_day=3)
    assert pass_rate(bare_rules(**ruled), risk=200.0) > pass_rate(
        bare_rules(**ruled), risk=2000.0
    )


def test_trailing_drawdown_is_strictly_worse_than_fixed():
    fixed = pass_rate(bare_rules(), risk=500.0)
    trailing = pass_rate(bare_rules(drawdown_mode="eod_trailing"), risk=500.0)
    assert trailing < fixed


def test_intraday_trailing_is_no_kinder_than_eod():
    eod = pass_rate(bare_rules(drawdown_mode="eod_trailing"), risk=500.0)
    intraday = pass_rate(bare_rules(drawdown_mode="intraday_trailing"), risk=500.0)
    assert intraday <= eod + 0.02


def test_a_daily_loss_limit_fails_the_day_it_bites():
    rng = random.Random(1)
    always_lose = lambda _rng: -1.0
    got = run_eval(
        rng,
        always_lose,
        400.0,
        bare_rules(daily_loss_limit=1000.0, trades_per_day=5),
    )
    assert got["passed"] is False and got["why"] == "daily loss limit"


def test_min_days_delays_an_instant_target():
    rng = random.Random(1)
    always_win = lambda _rng: 1.0
    got = run_eval(rng, always_win, 3000.0, bare_rules(min_days=3, trades_per_day=1))
    assert got["passed"] and got["days"] >= 3


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_ev_falls_as_the_fee_rises():
    cheap = price(coin_flip(), 500.0, bare_rules(eval_fee=100.0), sims=800, seed=3)
    dear = price(coin_flip(), 500.0, bare_rules(eval_fee=600.0), sims=800, seed=3)
    assert dear["ev_per_attempt"] == pytest.approx(
        cheap["ev_per_attempt"] - 500.0, abs=0.01
    )


def test_pricing_is_deterministic_for_a_seed():
    a = price(coin_flip(), 300.0, bare_rules(), sims=500, seed=9)
    b = price(coin_flip(), 300.0, bare_rules(), sims=500, seed=9)
    assert a == b


def test_a_strategy_with_real_edge_prices_better_than_the_coin():
    edge = coin_flip(win_p=0.55)
    coin = coin_flip(win_p=0.5)
    rules = bare_rules(max_days=40, trades_per_day=3)
    assert (
        price(edge, 400.0, rules, sims=800, seed=5)["ev_per_attempt"]
        > price(coin, 400.0, rules, sims=800, seed=5)["ev_per_attempt"]
    )


# ---------------------------------------------------------------------------
# Real trades in
# ---------------------------------------------------------------------------


def test_records_convert_to_the_repo_standard_r():
    records = [
        {"entry": 100.0, "stop": 90.0, "exit": 120.0, "direction": "long"},
        {"entry": 100.0, "stop": 110.0, "exit": 80.0, "direction": "short"},
        {"entry": 100.0, "stop": 100.0, "exit": 105.0},  # zero risk: dropped
        {"entry": "junk"},
    ]
    assert r_from_records(records) == pytest.approx([2.0, 2.0])


def test_empirical_sampler_draws_from_the_history():
    draw = empirical([1.5, -1.0])
    rng = random.Random(2)
    seen = {draw(rng) for _ in range(50)}
    assert seen == {1.5, -1.0}


def test_an_empty_history_refuses_loudly():
    with pytest.raises(SystemExit):
        empirical([])

"""calibration_audit — does the option math happen at the rate it claims?

The contract:

    - on data the model is literally correct about (lognormal, constant
      volatility) nothing is convicted: an audit that cries wolf on its own
      generating process is worthless;
    - on fat-tailed data it convicts the far barriers, and in the right
      direction — a mixture of ordinary and violent days makes distant
      levels reachable more often than a normal allows;
    - when volatility moves in regimes, the static-volatility benchmark
      misses rows the trailing estimate gets right, which is what says the
      estimate is worth keeping;
    - the barrier probabilities are the closed forms, not approximations of
      them, and touch is always at least finish;
    - touch is judged on intraday highs and lows when the data carries them,
      and the closes-only reading is a floor rather than a measurement;
    - windows never overlap, so no bar is counted twice as evidence;
    - the binomial test is exact, checked against a hand-computable case.

All data is synthetic with known structure; no network anywhere.
"""

import math
import random

import pytest

from pwb_toolbox.options.probability import finish_probability, touch_probability
from tools.calibration_audit import (
    Day,
    audit,
    binom_two_sided_p,
    collect,
    read_series,
    realized_vol,
    reliability,
    render,
    windows,
)

from datetime import date, timedelta


def gbm(
    n: int = 6000,
    vol: float = 0.20,
    drift: float = 0.0,
    seed: int = 4,
    wild_frac: float = 0.0,
    wild_mult: float = 4.0,
    regime: int = 0,
    regime_mult: float = 3.0,
) -> list[Day]:
    """Daily bars from a lognormal walk, with optional fat tails or regimes.

    `wild_frac` makes that fraction of days draw from a much wider normal —
    the standard fat-tail mixture, same total variance, heavier ends.
    `regime` alternates calm and violent blocks of that length, which is
    what a trailing volatility estimate can track and a static one cannot.

    Highs and lows are drawn around the close so the touch reading has
    something intraday to work with; the walk itself is close-to-close.
    """
    rng = random.Random(seed)
    base = vol / math.sqrt(252)
    price = 100.0
    day = date(2000, 1, 3)
    out = []
    for i in range(n):
        daily = base
        if regime and (i // regime) % 2:
            daily = base * regime_mult
        if wild_frac and rng.random() < wild_frac:
            daily *= wild_mult
        price *= math.exp(rng.gauss(drift / 252, daily))
        wick = abs(rng.gauss(0, daily / 2))
        out.append(Day(day, price * (1 + wick), price * (1 - wick), price))
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# The closed forms
# ---------------------------------------------------------------------------


def test_touch_is_twice_finish_for_a_driftless_walk():
    # The reflection principle, which is the whole reason a stop at a "20%
    # level" gets hit far more often than the chain implies.
    spot, target, vol, days = 100.0, 110.0, 0.25, 30.0
    finish = finish_probability(spot, target, vol, days, rate=0.0)
    touch = touch_probability(spot, target, vol, days)
    assert touch > finish
    # Not exactly 2x: finish carries the -sigma^2/2 drift term that touch
    # drops, which pushes an up-barrier's finish odds down and the ratio
    # above two. The identity itself is the driftless one.
    assert touch / finish == pytest.approx(2.13, abs=0.02)
    assert touch == pytest.approx(
        2 * finish_probability(spot, target, vol, days, rate=0.5 * vol * vol), rel=1e-9
    )


def test_touch_is_capped_and_certain_at_the_money():
    assert touch_probability(100.0, 100.0, 0.25, 30.0) == 1.0
    hair = touch_probability(100.0, 100.0001, 3.0, 400.0)
    assert hair <= 1.0 and hair == pytest.approx(1.0, abs=1e-5)


def test_probabilities_refuse_impossible_inputs():
    for bad in ((0.0, 110.0, 0.2, 30.0), (100.0, 110.0, 0.0, 30.0)):
        with pytest.raises(ValueError):
            touch_probability(*bad)
        with pytest.raises(ValueError):
            finish_probability(*bad)


# ---------------------------------------------------------------------------
# The machinery
# ---------------------------------------------------------------------------


def test_windows_never_share_a_bar():
    starts = windows(200, horizon=21, lookback=20)
    assert starts[0] == 20
    assert all(b - a == 21 for a, b in zip(starts, starts[1:]))
    assert starts[-1] + 21 < 200


def test_realized_vol_is_zero_on_a_straight_line_and_scales_up():
    flat = [100.0 * 1.001**i for i in range(30)]
    assert realized_vol(flat) == pytest.approx(0.0, abs=1e-12)
    noisy = gbm(n=800, vol=0.30, seed=9)
    assert realized_vol([d.close for d in noisy]) == pytest.approx(0.30, abs=0.03)


def test_the_binomial_test_is_exact():
    # n=10, k=8, p=0.5: both tails at or below the observed likelihood are
    # {0,1,2} and {8,9,10}, which is 112/1024 by hand.
    assert binom_two_sided_p(10, 8, 0.5) == pytest.approx(112 / 1024)
    assert binom_two_sided_p(10, 5, 0.5) == pytest.approx(1.0)
    assert binom_two_sided_p(0, 0, 0.3) == 1.0


def test_intraday_touches_are_seen_and_closes_alone_are_a_floor():
    # A day that spikes 6% and closes flat: the barrier was reached, and a
    # closes-only file cannot know that.
    spike = [
        Day(date(2020, 1, 1) + timedelta(days=i), 100.0, 100.0, 100.0)
        for i in range(60)
    ]
    spike[40] = Day(spike[40].day, 106.0, 100.0, 100.0)
    with_wicks = collect(spike, horizon=10, lookback=20, mode="static")
    assert with_wicks == []  # a flat series has no volatility to scale by

    walk = gbm(n=1500, seed=12)
    flattened = [Day(d.day, d.close, d.close, d.close) for d in walk]
    rich = collect(walk, horizon=21, lookback=20, mode="traded")
    poor = collect(flattened, horizon=21, lookback=20, mode="traded")
    assert sum(r["touched"] for r in rich) > sum(r["touched"] for r in poor)
    assert sum(r["finished"] for r in rich) == sum(r["finished"] for r in poor)


def test_reliability_rows_cover_both_directions_and_both_questions():
    rows = reliability(collect(gbm(n=2000), horizon=21, lookback=20, mode="traded"))
    assert {r["kind"] for r in rows} == {"finish", "touch"}
    assert {r["side"] for r in rows} == {"up", "down"}
    assert len(rows) == 16  # 2 questions x 2 sides x 4 barriers
    for r in rows:
        assert r["n"] > 0 and 0.0 <= r["predicted"] <= 1.0


# ---------------------------------------------------------------------------
# The two findings the audit exists to be able to make
# ---------------------------------------------------------------------------


def test_a_correct_model_is_not_convicted():
    # The generating process IS lognormal with constant volatility, so the
    # audit must come back with nothing. An audit that convicts here would
    # convict anything.
    result = audit({"GBM": gbm(n=6000, seed=4)}, horizon=21, lookback=60, mode="traded")
    assert result["windows"] > 90
    assert not [r for r in result["rows"] if r["miscalibrated"]]
    # And not by being blind: every row lands close, rather than missing
    # widely and escaping on a weak test.
    assert max(abs(r["diff"]) for r in result["rows"]) < 0.06


def test_fat_tails_convict_the_far_barriers_in_the_right_direction():
    # The classic mixture: most days ordinary, one in twenty violent. Same
    # kind of volatility number, heavier ends — so distant levels are
    # reached more often than a normal tail allows.
    wild = gbm(n=6000, vol=0.16, seed=6, wild_frac=0.05, wild_mult=5.0)
    result = audit({"WILD": wild}, horizon=5, lookback=60, mode="traded")
    far = [r for r in result["rows"] if r["k"] == 2.0]
    assert all(r["diff"] > 0 for r in far), "fat tails must over-hit the 2s rows"
    assert [r for r in far if r["miscalibrated"]], "and enough to be convicted"


def test_the_static_benchmark_is_beaten_when_volatility_moves():
    # Calm and violent blocks: a trailing estimate follows them, one number
    # for the whole record cannot. This is what prices the estimate.
    swinging = gbm(n=6000, vol=0.10, seed=8, regime=120, regime_mult=3.0)
    traded = audit({"S": swinging}, horizon=21, lookback=60, mode="traded")
    static = audit({"S": swinging}, horizon=21, lookback=60, mode="static")
    missed = lambda res: sum(1 for r in res["rows"] if r["miscalibrated"])  # noqa: E731
    assert missed(static) > missed(traded)


def test_the_window_s_own_volatility_is_not_on_offer():
    # Self-normalization would report a fat-tailed series as thin-tailed,
    # so the mode simply does not exist.
    with pytest.raises(ValueError):
        collect(gbm(n=200), horizon=5, lookback=20, mode="hindsight")


def test_a_bad_mode_is_refused():
    with pytest.raises(ValueError):
        collect(gbm(n=200), horizon=5, lookback=20, mode="guess")


# ---------------------------------------------------------------------------
# Files and output
# ---------------------------------------------------------------------------


def test_reads_ohlc_and_falls_back_to_the_close(tmp_path):
    full = tmp_path / "full.csv"
    full.write_text("timestamp,open,high,low,close\n2020-01-02,10,12,9,11\n")
    thin = tmp_path / "thin.csv"
    thin.write_text("timestamp,close\n2020-01-02,11\n2020-01-03,x\n")
    assert read_series(full)[0] == Day(date(2020, 1, 2), 12.0, 9.0, 11.0)
    assert read_series(thin) == [Day(date(2020, 1, 2), 11.0, 11.0, 11.0)]


def test_the_report_names_its_terms_and_its_caveats():
    result = audit({"GBM": gbm(n=2000)}, horizon=21, lookback=20, mode="traded")
    text = render(result)
    assert "FINISH" in text and "TOUCH" in text
    assert "trailing 20d volatility" in text
    assert "optimistic" in text  # the correlation caveat is never dropped
    flat = audit(
        {"F": [Day(d.day, d.close, d.close, d.close) for d in gbm(n=2000)]},
        horizon=21,
        lookback=20,
        mode="traded",
    )
    assert "CLOSES ONLY" in render(flat)

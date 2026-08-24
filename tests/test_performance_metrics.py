"""Tests for the pure statistical functions in `pwb_toolbox.performance.metrics`.

This module backs every Sharpe/Sortino/Calmar/drawdown number the toolbox
reports, and had zero test coverage. Where a closed-form expected value is
easy to construct (compounding series, symmetric returns, perfectly
correlated series) we assert exact numbers; for functions where hand-deriving
an exact result is error-prone, we assert well-understood relationships on
synthetic data instead (e.g. "the wilder series has the lower Sharpe").
"""

import math
import random

import pandas as pd
import pytest

from pwb_toolbox.performance.metrics import (
    _invert_matrix,
    _ols,
    acf,
    annualized_volatility,
    cagr,
    calmar_ratio,
    capm_alpha_beta,
    cumulative_excess_return,
    fama_french_3factor,
    fama_french_5factor,
    fama_french_regression,
    information_ratio,
    kurtosis,
    max_drawdown,
    omega_ratio,
    pacf,
    parametric_expected_shortfall,
    parametric_var,
    returns_table,
    rolling_cumulative_return,
    sharpe_ratio,
    skewness,
    sortino_ratio,
    tail_ratio,
    total_return,
    ulcer_index,
    ulcer_performance_index,
    variance_ratio,
)


def _compound(rets, start=100.0):
    """Build a price path from a list of period returns."""
    prices = [start]
    for r in rets:
        prices.append(prices[-1] * (1 + r))
    return prices


# --- total_return / cagr / annualized_volatility ----------------------------


def test_total_return_basic():
    assert math.isclose(total_return([100, 110, 121]), 0.21)


def test_total_return_empty_is_zero():
    assert total_return([]) == 0.0


def test_cagr_exact_compounding():
    # 121 = 100 * 1.1^2, so a 2-period CAGR (periods_per_year=1) is exactly 10%.
    assert math.isclose(cagr([100, 110, 121], periods_per_year=1), 0.10, rel_tol=1e-9)


def test_annualized_volatility_zero_for_smooth_compounding():
    # Constant per-period return -> zero variance of returns.
    prices = _compound([0.1, 0.1, 0.1, 0.1])
    assert annualized_volatility(prices, periods_per_year=1) == 0.0


def test_annualized_volatility_scales_with_dispersion():
    calm = _compound([0.01, -0.01, 0.01, -0.01] * 10)
    wild = _compound([0.05, -0.05, 0.05, -0.05] * 10)
    assert annualized_volatility(wild) > annualized_volatility(calm)


# --- max_drawdown / ulcer_index / calmar_ratio ------------------------------


def test_max_drawdown_depth_on_known_path():
    depth, _duration = max_drawdown([100, 50, 100])
    assert math.isclose(depth, -0.5)


def test_max_drawdown_zero_for_monotonic_series():
    # Note: `duration` is 1, not 0, here. `peak` starts equal to `p[0]` and the
    # loop only advances `peak` on a strict `price > peak`, so the very first
    # bar always falls into the "underwater" branch once. This is existing,
    # documented-by-this-test behavior of `max_drawdown`, not a new bug.
    depth, duration = max_drawdown([100, 110, 120, 130])
    assert depth == 0.0
    assert duration == 1


def test_ulcer_index_zero_for_monotonic_series():
    assert ulcer_index([100, 110, 120, 130]) == 0.0


def test_ulcer_index_positive_when_underwater():
    assert ulcer_index([100, 90, 95, 100]) > 0.0


def test_calmar_ratio_zero_when_no_drawdown():
    # No drawdown -> calmar_ratio divides by zero-guarded mdd -> 0.0 by design.
    assert calmar_ratio([100, 110, 120, 130]) == 0.0


# --- sharpe_ratio / sortino_ratio -------------------------------------------


def test_sharpe_ratio_zero_variance_edge_case():
    prices = _compound([0.01, 0.01, 0.01, 0.01])
    assert sharpe_ratio(prices) == 0.0


def test_sharpe_ratio_prefers_calmer_series_at_equal_mean_return():
    rng = random.Random(0)
    calm_rets = [0.001 + rng.gauss(0, 0.002) for _ in range(250)]
    wild_rets = [0.001 + rng.gauss(0, 0.02) for _ in range(250)]
    calm = _compound(calm_rets)
    wild = _compound(wild_rets)
    assert sharpe_ratio(calm) > sharpe_ratio(wild)


def test_sortino_ratio_ignores_upside_volatility():
    # All-upside noise (returns >= 0) has zero downside deviation -> undefined
    # downside risk. The function must not raise and must return a finite value.
    prices = _compound([0.01, 0.03, 0.0, 0.02, 0.01])
    result = sortino_ratio(prices)
    assert math.isfinite(result)


# --- capm_alpha_beta ---------------------------------------------------------


def test_capm_alpha_beta_recovers_known_linear_relationship():
    rng = random.Random(1)
    bench_rets = [rng.gauss(0.0005, 0.01) for _ in range(300)]
    # Strategy is exactly 2x the benchmark's return, no noise, no alpha.
    strat_rets = [2 * r for r in bench_rets]
    bench_prices = _compound(bench_rets)
    strat_prices = _compound(strat_rets)
    alpha, beta = capm_alpha_beta(strat_prices, bench_prices)
    assert math.isclose(beta, 2.0, rel_tol=1e-6)
    assert math.isclose(alpha, 0.0, abs_tol=1e-9)


# --- skewness / kurtosis -----------------------------------------------------


def test_skewness_zero_for_symmetric_returns():
    rets = [0.05, -0.05, 0.05, -0.05, 0.05, -0.05]
    prices = _compound(rets)
    assert math.isclose(skewness(prices), 0.0, abs_tol=1e-9)


def test_kurtosis_finite_and_nonnegative_for_typical_series():
    rng = random.Random(2)
    rets = [rng.gauss(0, 0.01) for _ in range(200)]
    prices = _compound(rets)
    result = kurtosis(prices)
    assert math.isfinite(result)
    assert result >= 0


# --- a plain list must work wherever the signature says Sequence[float] ------
#
# These three read the caller's index to label their output. The obvious
# `getattr(prices, "index", range(n))` is wrong: on a list, `index` is a
# *method*, so the default never applies and the result is an attempt to
# iterate a bound method. `cumulative_excess_return` is declared
# `Sequence[float]`, so a list is squarely in contract and used to raise
# TypeError; the other two are declared for Series but should not blow up
# either. See `_index_of`.


def test_cumulative_excess_return_accepts_a_plain_list():
    result = cumulative_excess_return([100, 110, 121], [100, 105, 110.25])
    assert list(result.index) == [0, 1, 2]
    assert result.iloc[0] == 0.0


def test_rolling_cumulative_return_accepts_a_plain_list():
    result = rolling_cumulative_return([100, 110, 121, 133.1], 2)
    assert list(result.index) == [0, 1, 2, 3]


def test_returns_table_accepts_a_plain_list_of_dates():
    # Without a DatetimeIndex there are no years to group by, so the frame is
    # empty -- but building it must not raise.
    assert returns_table([]).empty


# --- returns_table -----------------------------------------------------------


def test_returns_table_computes_month_and_year_returns():
    index = pd.to_datetime(["2024-01-02", "2024-01-31", "2024-02-01", "2024-02-29"])
    prices = pd.Series([100.0, 110.0, 110.0, 121.0], index=index)
    table = returns_table(prices)

    # Each month is measured first-to-last *within* that month.
    assert math.isclose(table.loc[2024, "Jan"], 0.10)
    assert math.isclose(table.loc[2024, "Feb"], 0.10)
    # The year spans the first January price to the last February one.
    assert math.isclose(table.loc[2024, "Year"], 0.21)


def test_returns_table_leaves_months_without_data_as_none():
    index = pd.to_datetime(["2024-03-01", "2024-03-28"])
    table = returns_table(pd.Series([100.0, 110.0], index=index))
    assert table.loc[2024, "Jan"] is None
    assert math.isclose(table.loc[2024, "Mar"], 0.10)


# --- rolling_cumulative_return -----------------------------------------------


def test_rolling_cumulative_return_is_none_until_the_window_is_full():
    prices = pd.Series([100.0, 110.0, 121.0, 133.1])
    result = rolling_cumulative_return(prices, 2)
    # The first `window` entries have no bar `window` back to compare against.
    assert result.iloc[0] is None or math.isnan(result.iloc[0])
    assert result.iloc[1] is None or math.isnan(result.iloc[1])
    assert math.isclose(result.iloc[2], 0.21)
    assert math.isclose(result.iloc[3], 0.21)


def test_rolling_cumulative_return_preserves_the_input_index():
    index = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    result = rolling_cumulative_return(pd.Series([100.0, 110.0, 121.0], index=index), 1)
    assert list(result.index) == list(index)


# --- ulcer_performance_index -------------------------------------------------


def test_ulcer_performance_index_zero_when_never_underwater():
    # No drawdown -> ulcer index 0 -> guarded to 0.0 rather than dividing.
    assert ulcer_performance_index([100, 110, 120, 130]) == 0.0


def test_ulcer_performance_index_is_cagr_over_ulcer_index():
    prices = [100, 90, 95, 120]
    expected = cagr(prices, 252) / ulcer_index(prices)
    assert math.isclose(ulcer_performance_index(prices), expected)


def test_ulcer_performance_index_falls_as_the_risk_free_rate_rises():
    prices = [100, 90, 95, 120]
    assert ulcer_performance_index(prices, risk_free_rate=0.0) > (
        ulcer_performance_index(prices, risk_free_rate=0.5)
    )


# --- parametric_var / parametric_expected_shortfall --------------------------


def test_parametric_var_on_a_constant_return_series_is_minus_the_mean():
    # Zero dispersion -> sigma 0 -> VaR collapses to -mu. A steadily *gaining*
    # series therefore reports a negative VaR, i.e. no loss at that level.
    prices = _compound([0.10, 0.10])
    assert math.isclose(parametric_var(prices), -0.10)


def test_parametric_var_grows_with_dispersion():
    calm = _compound([0.01, -0.01] * 20)
    wild = _compound([0.05, -0.05] * 20)
    assert parametric_var(wild) > parametric_var(calm)


def test_parametric_var_grows_as_the_level_gets_stricter():
    prices = _compound([0.01, -0.02, 0.03, -0.01] * 10)
    # A 1% tail is further out than a 5% tail, so the loss quoted is larger.
    assert parametric_var(prices, level=0.01) > parametric_var(prices, level=0.05)


def test_expected_shortfall_is_at_least_var_at_the_same_level():
    # ES averages the tail beyond the VaR cut-off, so it can never be smaller.
    prices = _compound([0.01, -0.02, 0.03, -0.01] * 10)
    assert parametric_expected_shortfall(prices) >= parametric_var(prices)


# --- tail_ratio --------------------------------------------------------------


def test_tail_ratio_is_zero_for_a_series_too_short_to_have_tails():
    assert tail_ratio([100, 110]) == 0.0


def test_tail_ratio_near_one_for_a_symmetric_return_distribution():
    prices = _compound([0.01 * (i - 10) for i in range(21)])
    assert math.isclose(tail_ratio(prices), 1.0, rel_tol=1e-6)


# --- omega_ratio -------------------------------------------------------------


def test_omega_ratio_is_one_for_returns_symmetric_about_the_threshold():
    prices = _compound([0.1, -0.1, 0.1, -0.1])
    assert math.isclose(omega_ratio(prices), 1.0, rel_tol=1e-9)


def test_omega_ratio_zero_when_nothing_falls_below_the_threshold():
    # No losses -> the denominator is zero -> guarded to 0.0 by design.
    assert omega_ratio(_compound([0.01, 0.02, 0.03])) == 0.0


def test_omega_ratio_falls_as_the_threshold_rises():
    prices = _compound([0.02, -0.01, 0.03, -0.01] * 5)
    assert omega_ratio(prices, threshold=0.0) > omega_ratio(prices, threshold=2.52)


# --- information_ratio -------------------------------------------------------


def test_information_ratio_zero_when_strategy_tracks_benchmark_exactly():
    # Zero active return every period -> zero tracking error -> guarded to 0.0.
    prices = _compound([0.01, -0.02, 0.03])
    assert information_ratio(prices, prices) == 0.0


def test_information_ratio_positive_when_strategy_consistently_outperforms():
    bench = _compound([0.01] * 50)
    strat = _compound([0.02] * 50)
    # A constant active return has zero variance, which the function guards to
    # 0.0; add one different period so the tracking error is defined.
    strat = _compound([0.02] * 49 + [0.03])
    assert information_ratio(strat, bench) > 0


def test_information_ratio_zero_for_series_too_short_to_compare():
    assert information_ratio([100], [100]) == 0.0


# --- _invert_matrix / _ols ---------------------------------------------------


def test_invert_matrix_returns_the_identity_for_the_identity():
    assert _invert_matrix([[1, 0], [0, 1]]) == [[1.0, 0.0], [0.0, 1.0]]


def test_invert_matrix_inverts_a_known_matrix():
    inverse = _invert_matrix([[4, 7], [2, 6]])
    # 1/10 * [[6, -7], [-2, 4]]
    assert math.isclose(inverse[0][0], 0.6)
    assert math.isclose(inverse[0][1], -0.7)
    assert math.isclose(inverse[1][0], -0.2)
    assert math.isclose(inverse[1][1], 0.4)


def test_invert_matrix_returns_none_for_a_singular_matrix():
    # The second row is twice the first, so there is no inverse.
    assert _invert_matrix([[1, 2], [2, 4]]) is None


def test_invert_matrix_recovers_from_a_zero_pivot_by_swapping_rows():
    # A zero in the leading position is not singular -- it just needs a swap.
    inverse = _invert_matrix([[0, 1], [1, 0]])
    assert inverse == [[0.0, 1.0], [1.0, 0.0]]


def test_ols_recovers_exact_coefficients_of_a_noiseless_fit():
    # y = 3 + 2*x, fitted with an intercept column.
    xs = [1.0, 2.0, 3.0, 4.0]
    y = [3 + 2 * x for x in xs]
    X = [[1.0, x] for x in xs]
    intercept, slope = _ols(y, X)
    assert math.isclose(intercept, 3.0, abs_tol=1e-9)
    assert math.isclose(slope, 2.0, abs_tol=1e-9)


def test_ols_returns_zeros_when_the_design_matrix_is_singular():
    # A duplicated column makes X'X non-invertible; the guard returns zeros
    # rather than raising.
    X = [[1.0, 1.0], [1.0, 1.0], [1.0, 1.0]]
    assert _ols([1.0, 2.0, 3.0], X) == [0.0, 0.0]


# --- fama_french -------------------------------------------------------------


def _factor_frame(n, rng):
    mkt = [rng.gauss(0.0005, 0.01) for _ in range(n)]
    return (
        pd.DataFrame(
            {
                "Mkt-RF": mkt,
                "SMB": [rng.gauss(0, 0.005) for _ in range(n)],
                "HML": [rng.gauss(0, 0.005) for _ in range(n)],
                "RMW": [rng.gauss(0, 0.005) for _ in range(n)],
                "CMA": [rng.gauss(0, 0.005) for _ in range(n)],
                "RF": [0.0] * n,
            }
        ),
        mkt,
    )


def test_fama_french_regression_is_indexed_by_alpha_then_the_factors():
    rng = random.Random(3)
    factors, _mkt = _factor_frame(60, rng)
    prices = _compound([rng.gauss(0.001, 0.01) for _ in range(59)])
    result = fama_french_regression(prices, factors, ["Mkt-RF", "SMB"])
    assert list(result.index) == ["alpha", "Mkt-RF", "SMB"]


def test_fama_french_regression_returns_zeros_for_a_series_too_short():
    factors = pd.DataFrame({"Mkt-RF": [0.01], "RF": [0.0]})
    result = fama_french_regression([100.0], factors, ["Mkt-RF"])
    assert list(result.index) == ["alpha", "Mkt-RF"]
    assert list(result) == [0.0, 0.0]


def test_fama_french_3factor_selects_the_three_canonical_columns():
    rng = random.Random(4)
    factors, _mkt = _factor_frame(40, rng)
    prices = _compound([rng.gauss(0.001, 0.01) for _ in range(39)])
    result = fama_french_3factor(prices, factors)
    assert list(result.index) == ["alpha", "Mkt-RF", "SMB", "HML"]


def test_fama_french_5factor_selects_the_five_canonical_columns():
    rng = random.Random(5)
    factors, _mkt = _factor_frame(40, rng)
    prices = _compound([rng.gauss(0.001, 0.01) for _ in range(39)])
    result = fama_french_5factor(prices, factors)
    assert list(result.index) == ["alpha", "Mkt-RF", "SMB", "HML", "RMW", "CMA"]


def test_fama_french_skips_factor_columns_the_frame_does_not_carry():
    rng = random.Random(6)
    factors = pd.DataFrame(
        {"Mkt-RF": [rng.gauss(0, 0.01) for _ in range(20)], "RF": [0.0] * 20}
    )
    prices = _compound([rng.gauss(0.001, 0.01) for _ in range(19)])
    result = fama_french_3factor(prices, factors)
    assert list(result.index) == ["alpha", "Mkt-RF"]


# --- cumulative_excess_return ------------------------------------------------


def test_cumulative_excess_return_starts_at_zero():
    result = cumulative_excess_return([100, 110, 121], [100, 105, 110.25])
    assert result.iloc[0] == 0.0


def test_cumulative_excess_return_is_zero_throughout_against_itself():
    prices = _compound([0.01, -0.02, 0.03])
    result = cumulative_excess_return(prices, prices)
    assert all(math.isclose(v, 0.0, abs_tol=1e-12) for v in result)


def test_cumulative_excess_return_compounds_the_active_return():
    # Strategy +10% a period, benchmark +5%: the active return is +5% a period,
    # compounded, so two periods give 1.05^2 - 1.
    strat = _compound([0.10, 0.10])
    bench = _compound([0.05, 0.05])
    result = cumulative_excess_return(strat, bench)
    assert math.isclose(result.iloc[-1], 1.05**2 - 1, rel_tol=1e-9)


def test_cumulative_excess_return_truncates_to_the_shorter_series():
    result = cumulative_excess_return([100, 110, 121, 133], [100, 105])
    assert len(result) == 2


# --- variance_ratio ----------------------------------------------------------


def test_variance_ratio_zero_for_a_constant_return_series():
    # Zero variance in returns -> guarded to 0.0 rather than dividing.
    assert variance_ratio(_compound([0.1, 0.1, 0.1, 0.1])) == 0.0


def test_variance_ratio_zero_when_the_series_is_shorter_than_the_lag():
    assert variance_ratio([100, 110], lag=5) == 0.0


def test_variance_ratio_near_one_for_an_uncorrelated_random_walk():
    # Independent returns aggregate linearly in variance, so the ratio is ~1.
    rng = random.Random(7)
    prices = _compound([rng.gauss(0, 0.01) for _ in range(2000)])
    assert math.isclose(variance_ratio(prices, lag=2), 1.0, abs_tol=0.1)


def test_variance_ratio_below_one_for_a_mean_reverting_series():
    # Perfectly alternating returns cancel when aggregated, so the two-period
    # variance is far below twice the one-period variance.
    prices = _compound([0.02, -0.02] * 50)
    assert variance_ratio(prices, lag=2) < 1.0


# --- acf / pacf --------------------------------------------------------------


def test_acf_of_a_perfectly_alternating_series_is_minus_one_at_lag_one():
    prices = _compound([0.05, -0.05] * 20)
    lag1, lag2 = acf(prices, [1, 2])
    assert math.isclose(lag1, -1.0, rel_tol=1e-6)
    # Two steps apart the returns are identical again.
    assert math.isclose(lag2, 1.0, rel_tol=1e-6)


def test_acf_returns_zero_for_out_of_range_lags():
    prices = _compound([0.01, -0.01, 0.02])
    # Lag 0 and any lag at or past the number of returns are not defined.
    assert acf(prices, [0, 99]) == [0.0, 0.0]


def test_acf_is_zero_everywhere_for_a_constant_return_series():
    assert acf(_compound([0.1, 0.1, 0.1]), [1, 2]) == [0.0, 0.0]


def test_acf_returns_one_entry_per_requested_lag():
    rng = random.Random(8)
    prices = _compound([rng.gauss(0, 0.01) for _ in range(100)])
    assert len(acf(prices, [1, 2, 3, 5, 8])) == 5


def test_pacf_approximates_acf_at_lag_one():
    # The first partial autocorrelation has nothing to partial out, so it is
    # the plain autocorrelation -- but only in the limit. The two estimators
    # normalise differently on a finite sample (`acf` divides the covariance by
    # `n - lag` and the variance by `n`, while `pacf` regresses on the
    # truncated sample), so they agree to a few percent rather than exactly.
    rng = random.Random(9)
    prices = _compound([rng.gauss(0, 0.01) for _ in range(200)])
    assert math.isclose(pacf(prices, [1])[0], acf(prices, [1])[0], rel_tol=0.02)


def test_pacf_returns_zero_for_out_of_range_lags():
    prices = _compound([0.01, -0.01, 0.02])
    assert pacf(prices, [0, 99]) == [0.0, 0.0]


def test_pacf_returns_one_entry_per_requested_lag():
    rng = random.Random(10)
    prices = _compound([rng.gauss(0, 0.01) for _ in range(100)])
    assert len(pacf(prices, [1, 2, 3])) == 3

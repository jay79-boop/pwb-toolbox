"""Tests for StrategyComparator and related functions.

These tests use synthetic NAV data rather than running actual strategies,
so they exercise the comparison logic without depending on Backtrader or live data.
"""

import math
from datetime import datetime, timedelta

import pandas as pd
import pytest

from pwb_toolbox.backtesting.comparator import (
    StrategyComparison,
    StrategyComparator,
    _calculate_correlation,
    _calculate_metrics,
    _calculate_returns,
    _create_portfolio_nav,
)


def _synthetic_nav(base=100.0, returns=None, days=252):
    """Build a synthetic NAV series from daily returns or a fixed number of days.

    If returns is provided, use them; otherwise generate a flat +10% annual return.
    """
    if returns is None:
        returns = [0.0001] * days  # ~2.5% annual, near-flat

    dates = [datetime(2025, 1, 1) + timedelta(days=i) for i in range(len(returns) + 1)]
    prices = [base]
    for r in returns:
        prices.append(prices[-1] * (1 + r))

    return pd.Series(prices, index=pd.DatetimeIndex(dates), dtype=float)


class TestCalculateReturns:
    def test_returns_from_flat_nav(self):
        nav = _synthetic_nav(base=100.0, returns=[0.0] * 10)
        returns = _calculate_returns(nav)
        assert len(returns) == 10
        assert (returns == 0.0).all()

    def test_returns_from_uptrend(self):
        nav = _synthetic_nav(base=100.0, returns=[0.01] * 5)
        returns = _calculate_returns(nav)
        assert len(returns) == 5
        assert (returns > 0).all()
        assert math.isclose(returns.iloc[0], 0.01, rel_tol=1e-9)

    def test_returns_drop_first_na(self):
        """pct_change() produces NaN on the first row, which dropna() removes."""
        nav = _synthetic_nav(base=100.0, returns=[0.01] * 3)
        returns = _calculate_returns(nav)
        assert len(returns) == 3  # One fewer than the 4-bar nav
        assert not returns.isna().any()


class TestCalculateMetrics:
    def test_flat_strategy_has_zero_return_and_volatility(self):
        nav = _synthetic_nav(base=100.0, returns=[0.0] * 50)
        metrics = _calculate_metrics(nav)

        assert math.isclose(metrics["final_nav"], 100.0, rel_tol=1e-9)
        assert math.isclose(metrics["total_return"], 0.0, rel_tol=1e-9)
        assert math.isclose(metrics["cagr"], 0.0, rel_tol=1e-9)
        assert math.isclose(metrics["volatility"], 0.0, rel_tol=1e-9)
        assert math.isclose(metrics["max_drawdown"], 0.0, rel_tol=1e-9)

    def test_uptrend_has_positive_return(self):
        # Constant returns produce zero volatility; vary them to get non-zero vol
        nav = _synthetic_nav(base=100.0, returns=[0.01, -0.005, 0.01, -0.005] * 12 + [0.01])
        metrics = _calculate_metrics(nav)

        assert metrics["final_nav"] > 100.0
        assert metrics["total_return"] > 0.0
        assert metrics["cagr"] > 0.0
        assert metrics["volatility"] > 0.0
        # Sharpe may still be nan if volatility is very small; just check return is positive
        assert metrics["total_return"] > 0.0

    def test_win_rate_calculation(self):
        """Win rate is the fraction of days with positive returns."""
        nav = _synthetic_nav(base=100.0, returns=[0.01, -0.01, 0.01, -0.01, 0.01])
        metrics = _calculate_metrics(nav)

        # 3 up days out of 5 = 60% win rate
        assert math.isclose(metrics["win_rate"], 0.6, rel_tol=1e-9)

    def test_metrics_dict_has_all_required_fields(self):
        nav = _synthetic_nav()
        metrics = _calculate_metrics(nav)

        required_fields = {
            "final_nav", "total_return", "cagr", "volatility",
            "max_drawdown", "sharpe_ratio", "sortino_ratio", "win_rate"
        }
        assert required_fields.issubset(metrics.keys())


class TestCalculateCorrelation:
    def test_identical_strategies_have_correlation_one(self):
        # Build two NAVs with identical base returns (but distinct price paths)
        nav_a = _synthetic_nav(base=100.0, returns=[0.01, -0.005, 0.01] * 17)
        nav_b = _synthetic_nav(base=100.0, returns=[0.01, -0.005, 0.01] * 17)
        navs = {"A": nav_a, "B": nav_b}

        corr = _calculate_correlation(navs)

        # Same return sequence -> correlation should be very close to 1
        assert corr.loc["A", "B"] > 0.99

    def test_flat_vs_uptrend_correlation(self):
        nav_flat = _synthetic_nav(base=100.0, returns=[0.0] * 50)
        nav_up = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        navs = {"flat": nav_flat, "up": nav_up}

        corr = _calculate_correlation(navs)

        # Flat has zero variance, so correlation is NaN; but the call should not error
        assert corr.shape == (2, 2)

    def test_opposite_strategies_have_negative_correlation(self):
        nav_up = _synthetic_nav(base=100.0, returns=[0.01, -0.01] * 25)
        nav_down = _synthetic_nav(base=100.0, returns=[-0.01, 0.01] * 25)
        navs = {"up": nav_up, "down": nav_down}

        corr = _calculate_correlation(navs)

        # Returns are anticorrelated
        assert corr.loc["up", "down"] < 0.0


class TestCreatePortfolioNav:
    def test_equal_weight_two_strategies(self):
        nav_a = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        nav_b = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        navs = {"A": nav_a, "B": nav_b}

        portfolio = _create_portfolio_nav(navs, weights={"A": 0.5, "B": 0.5})

        # Portfolio should track similar to either strategy (both identical)
        assert portfolio.iloc[-1] > portfolio.iloc[0]
        assert len(portfolio) == len(nav_a)

    def test_unequal_weight_pulls_toward_better_strategy(self):
        nav_weak = _synthetic_nav(base=100.0, returns=[0.001] * 50)
        nav_strong = _synthetic_nav(base=100.0, returns=[0.01] * 50)

        # Equal weight: average performance
        portfolio_equal = _create_portfolio_nav(
            {"weak": nav_weak, "strong": nav_strong},
            weights={"weak": 0.5, "strong": 0.5}
        )

        # Heavy toward strong: better performance
        portfolio_heavy = _create_portfolio_nav(
            {"weak": nav_weak, "strong": nav_strong},
            weights={"weak": 0.1, "strong": 0.9}
        )

        assert portfolio_heavy.iloc[-1] > portfolio_equal.iloc[-1]

    def test_default_weights_are_equal(self):
        nav_a = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        nav_b = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        navs = {"A": nav_a, "B": nav_b}

        portfolio = _create_portfolio_nav(navs)  # No weights specified

        assert portfolio.iloc[-1] > portfolio.iloc[0]


class TestStrategyComparison:
    def test_summary_returns_dataframe(self):
        metrics_a = {"sharpe_ratio": 1.0, "max_drawdown": -0.15}
        metrics_b = {"sharpe_ratio": 0.8, "max_drawdown": -0.20}
        individual_metrics = {"A": metrics_a, "B": metrics_b}

        result = StrategyComparison(
            navs={},
            individual_metrics=individual_metrics,
            portfolio_metrics={},
            correlation_matrix=pd.DataFrame(),
            portfolio_nav=pd.Series(),
        )

        summary = result.summary()

        assert isinstance(summary, pd.DataFrame)
        assert summary.shape == (2, 2)
        assert list(summary.index) == ["A", "B"]

    def test_repr_includes_strategy_names_and_metrics(self):
        nav_a = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        metrics_a = _calculate_metrics(nav_a)
        portfolio_nav = _synthetic_nav(base=100.0, returns=[0.01] * 50)
        portfolio_metrics = _calculate_metrics(portfolio_nav)

        result = StrategyComparison(
            navs={"StrategyA": nav_a},
            individual_metrics={"StrategyA": metrics_a},
            portfolio_metrics=portfolio_metrics,
            correlation_matrix=pd.DataFrame({"StrategyA": [1.0]}),
            portfolio_nav=portfolio_nav,
        )

        repr_str = repr(result)

        assert "StrategyA" in repr_str
        assert "Sharpe" in repr_str
        assert "Return" in repr_str


class TestStrategyComparator:
    def test_add_and_run_single_strategy_mock(self):
        """Test the comparator with a mock strategy that returns synthetic NAV."""

        class MockStrategy:
            """A strategy that just logs synthetic NAV data."""
            def __init__(self):
                self.log_data = [
                    {"date": (datetime(2025, 1, 1) + timedelta(days=i)).isoformat(), "value": 100.0 + i}
                    for i in range(50)
                ]

        # We can't easily test the full run() without mocking pwb_bt.run_strategy,
        # so we verify the comparator structure instead
        comparator = StrategyComparator()

        assert len(comparator.strategies) == 0

        comparator.add_strategy("mock", MockStrategy)

        assert len(comparator.strategies) == 1
        assert "mock" in comparator.strategies
        assert comparator.strategies["mock"]["strategy_cls"] == MockStrategy

    def test_add_strategy_with_indicator_and_kwargs(self):
        class DummyStrategy:
            pass

        class DummyIndicator:
            pass

        comparator = StrategyComparator()
        comparator.add_strategy(
            "test",
            DummyStrategy,
            indicator_cls=DummyIndicator,
            indicator_kwargs={"period": 20},
            strategy_kwargs={"debug": True},
        )

        spec = comparator.strategies["test"]

        assert spec["indicator_cls"] == DummyIndicator
        assert spec["indicator_kwargs"] == {"period": 20}
        assert spec["strategy_kwargs"] == {"debug": True}

    def test_kwargs_default_to_empty_dicts(self):
        class DummyStrategy:
            pass

        comparator = StrategyComparator()
        comparator.add_strategy("test", DummyStrategy)

        spec = comparator.strategies["test"]

        assert spec["indicator_kwargs"] == {}
        assert spec["strategy_kwargs"] == {}

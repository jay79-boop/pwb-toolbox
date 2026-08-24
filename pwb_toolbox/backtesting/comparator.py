"""Strategy comparison harness for portfolio analysis and correlation detection."""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
import pwb_toolbox.backtesting as pwb_bt
import pwb_toolbox.performance as pwb_perf


class StrategyComparison:
    """Results container for strategy comparison."""

    def __init__(
        self,
        navs: Dict[str, pd.Series],
        individual_metrics: Dict[str, Dict[str, float]],
        portfolio_metrics: Dict[str, float],
        correlation_matrix: pd.DataFrame,
        portfolio_nav: pd.Series,
    ):
        self.navs = navs
        self.individual_metrics = individual_metrics
        self.portfolio_metrics = portfolio_metrics
        self.correlation_matrix = correlation_matrix
        self.portfolio_nav = portfolio_nav

    def summary(self) -> pd.DataFrame:
        """Return a summary table of individual strategy metrics."""
        return pd.DataFrame(self.individual_metrics).T

    def __repr__(self):
        summary = self.summary()
        return (
            f"StrategyComparison(\n"
            f"Strategies: {list(self.navs.keys())}\n"
            f"Portfolio Return: {self.portfolio_metrics['total_return']:.2%}\n"
            f"Portfolio Sharpe: {self.portfolio_metrics['sharpe_ratio']:.3f}\n"
            f"Avg Correlation: {self.correlation_matrix.values[np.triu_indices_from(self.correlation_matrix.values, k=1)].mean():.3f}\n"
            f")\n{summary.to_string()}"
        )


def _strategy_to_nav(strategy) -> pd.Series:
    """Extract NAV series from a Backtrader strategy result."""
    nav = pd.Series(
        [row["value"] for row in strategy.log_data],
        index=pd.to_datetime([row["date"] for row in strategy.log_data]),
        dtype=float,
    ).sort_index()
    return nav


def _calculate_returns(nav: pd.Series) -> pd.Series:
    """Calculate daily returns from a NAV series."""
    return nav.pct_change().dropna()


def _calculate_metrics(nav: pd.Series) -> Dict[str, float]:
    """Calculate performance metrics for a single strategy."""
    mdd, _ = pwb_perf.max_drawdown(nav)
    returns = _calculate_returns(nav)

    metrics = {
        "final_nav": float(nav.iloc[-1]),
        "total_return": float(pwb_perf.total_return(nav)),
        "cagr": float(pwb_perf.cagr(nav)),
        "volatility": float(pwb_perf.annualized_volatility(nav)),
        "max_drawdown": float(mdd),
        "sharpe_ratio": float(pwb_perf.sharpe_ratio(nav)),
        "sortino_ratio": float(pwb_perf.sortino_ratio(nav)),
        "win_rate": float((returns > 0).sum() / len(returns)),
    }
    return metrics


def _calculate_correlation(navs: Dict[str, pd.Series]) -> pd.DataFrame:
    """Calculate pairwise correlation between strategy returns."""
    nav_df = pd.concat(navs.values(), axis=1)
    nav_df.columns = navs.keys()
    returns_df = nav_df.pct_change().dropna()
    return returns_df.corr()


def _create_portfolio_nav(
    navs: Dict[str, pd.Series], weights: Dict[str, float] = None
) -> pd.Series:
    """Create a portfolio NAV by combining strategies with equal or specified weights."""
    if weights is None:
        weights = {name: 1.0 / len(navs) for name in navs.keys()}

    nav_df = pd.concat(navs.values(), axis=1)
    nav_df.columns = navs.keys()
    nav_df = nav_df.dropna()

    weight_series = pd.Series(weights)
    weight_series /= weight_series.sum()

    initial_nav = 100000  # Arbitrary starting capital
    positions = initial_nav * weight_series / nav_df.iloc[0]
    cash = initial_nav - (positions * nav_df.iloc[0]).sum()

    dates = nav_df.index
    daily_navs = []

    for i, (date, prices) in enumerate(nav_df.iterrows()):
        if i > 0 and date.year != dates[i - 1].year:
            portfolio_nav = (positions * prices).sum() + cash
            positions = portfolio_nav * weight_series / prices
            cash = portfolio_nav - (positions * prices).sum()

        portfolio_nav = (positions * prices).sum() + cash
        daily_navs.append(portfolio_nav)

    portfolio_series = pd.Series(daily_navs, index=dates, name="Portfolio")
    return portfolio_series


class StrategyComparator:
    """Run and compare multiple strategies on identical data."""

    def __init__(self):
        self.strategies = {}

    def add_strategy(
        self,
        name: str,
        strategy_cls,
        indicator_cls=None,
        indicator_kwargs=None,
        strategy_kwargs=None,
    ):
        """Register a strategy to be compared.

        Parameters
        ----------
        name : str
            Human-readable name for the strategy.
        strategy_cls : type
            Backtrader strategy class.
        indicator_cls : type, optional
            Indicator class used by the strategy.
        indicator_kwargs : dict, optional
            Keyword arguments for the indicator.
        strategy_kwargs : dict, optional
            Keyword arguments for the strategy.
        """
        self.strategies[name] = {
            "strategy_cls": strategy_cls,
            "indicator_cls": indicator_cls,
            "indicator_kwargs": indicator_kwargs or {},
            "strategy_kwargs": strategy_kwargs or {},
        }

    def run(
        self,
        symbols: List[str],
        start_date: str,
        cash: float = 100000,
        cerebro_kwargs=None,
        broker_kwargs=None,
        weights: Dict[str, float] = None,
    ) -> StrategyComparison:
        """Run all registered strategies on identical data.

        Parameters
        ----------
        symbols : list
            List of symbols to backtest.
        start_date : str
            Start date for backtest (YYYY-MM-DD).
        cash : float, optional
            Initial cash for each strategy (default 100k).
        cerebro_kwargs : dict, optional
            Keyword arguments for Cerebro.
        broker_kwargs : dict, optional
            Keyword arguments for broker (commission, slippage, etc.).
        weights : dict, optional
            Portfolio weights for each strategy. Defaults to equal weight.

        Returns
        -------
        StrategyComparison
            Object containing individual and portfolio metrics, correlation matrix.
        """
        navs = {}
        print(f"Running {len(self.strategies)} strategies for comparison...")

        for name, spec in self.strategies.items():
            print(f"  {name}...", end=" ")
            strategy = pwb_bt.run_strategy(
                indicator_cls=spec["indicator_cls"],
                indicator_kwargs=spec["indicator_kwargs"],
                strategy_cls=spec["strategy_cls"],
                strategy_kwargs=spec["strategy_kwargs"],
                symbols=symbols,
                start_date=start_date,
                cash=cash,
                cerebro_kwargs=cerebro_kwargs,
                broker_kwargs=broker_kwargs,
            )
            navs[name] = _strategy_to_nav(strategy)
            print(f"✓")

        # Calculate individual metrics
        print("Calculating individual metrics...")
        individual_metrics = {}
        for name, nav in navs.items():
            individual_metrics[name] = _calculate_metrics(nav)

        # Calculate portfolio metrics
        print("Calculating portfolio metrics...")
        portfolio_nav = _create_portfolio_nav(navs, weights)
        portfolio_metrics = _calculate_metrics(portfolio_nav)

        # Calculate correlation
        print("Calculating correlation matrix...")
        correlation_matrix = _calculate_correlation(navs)

        return StrategyComparison(
            navs=navs,
            individual_metrics=individual_metrics,
            portfolio_metrics=portfolio_metrics,
            correlation_matrix=correlation_matrix,
            portfolio_nav=portfolio_nav,
        )

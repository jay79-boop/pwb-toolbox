"""Configuration dataclasses for backtesting and optimization."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyConfig:
    """Configuration for running a single backtest."""

    indicator_cls: type
    indicator_kwargs: dict
    strategy_cls: type
    strategy_kwargs: dict = field(default_factory=dict)
    symbols: list = field(default_factory=list)
    start_date: str = None
    cash: float = 100000.0
    cerebro_kwargs: dict = field(default_factory=dict)
    broker_kwargs: dict = field(default_factory=dict)


@dataclass
class GAOptimizationConfig:
    """Configuration for genetic algorithm optimization of strategy parameters."""

    n_weights: int
    bias_bounds: tuple = (-10, 10)
    weight_bounds: tuple = (-10, 10)
    pop_size: int = 64
    n_generations: int = 40
    cx_prob: float = 0.6
    mut_prob: float = 0.3
    seed: Optional[int] = None

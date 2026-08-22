"""Configuration dataclasses for execution and order placement."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class OptimalQuoteConfig:
    """Configuration for optimal limit order pricing."""

    symbol: str
    quantity: float
    time_in_seconds: float
    mu: float = 0.0
    sigma: float = 0.3
    A: float = 0.1
    k: float = 0.3
    gamma: Optional[float] = None
    b: float = 3
    tick_size: float = 0.01
    average_trading_size: float = 100.0
    is_plot: bool = False


@dataclass
class OptimalLimitOrderFormulaConfig:
    """Configuration for the optimal limit order formula calculation."""

    q_max: int
    t_max: float
    mu: float
    sigma: float
    A: float
    k: float
    gamma: float
    b: float
    is_plot: bool = False

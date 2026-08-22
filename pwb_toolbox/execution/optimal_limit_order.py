import math

import matplotlib.pyplot as plt
import numpy as np
from scipy.integrate import odeint

from .config import OptimalLimitOrderFormulaConfig, OptimalQuoteConfig


def optimal_limit_order_formula(config: OptimalLimitOrderFormulaConfig):
    """Calculate optimal limit order offset from mid-price.

    Args:
        config: OptimalLimitOrderFormulaConfig with market and risk parameters.

    Returns:
        Optimal price offset in ticks.
    """
    alpha = config.k / 2 * config.gamma * np.power(config.sigma, 2)
    beta = config.k * config.mu
    eta = config.A * np.power(
        1 + config.gamma / config.k, -(1 + config.k / config.gamma)
    )
    w_0 = 1

    def w_T(q):
        return np.exp(-config.k * q * config.b)

    def linear_ode(w_q, w_q_1, q):
        return (alpha * np.power(q, 2) - beta * q) * w_q - eta * w_q_1

    def linear_ode_system(y, t):
        w = [w_0, *y]
        dydt = [linear_ode(w[q], w[q - 1], q) for q in range(1, config.q_max + 1)]
        return dydt

    w_T = [w_T(q) for q in range(1, config.q_max + 1)]
    t = np.linspace(0, -config.t_max, 100)

    w = odeint(linear_ode_system, w_T, t, args=())

    delta = {}
    for q in range(1, config.q_max + 1):
        if q == 1:
            delta[q] = 1 / config.k * np.log(
                w[:, q - 1] / w_0
            ) + 1 / config.gamma * np.log(1 + config.gamma / config.k)
        else:
            delta[q] = 1 / config.k * np.log(
                w[:, q - 1] / w[:, q - 2]
            ) + 1 / config.gamma * np.log(1 + config.gamma / config.k)

    if config.is_plot:
        for q in range(1, config.q_max + 1):
            plt.plot(t, delta[q], "b", label=f"delta_{q}(t)")
        plt.legend(loc="best")
        plt.xlabel("t")
        plt.ylabel("Ask price - Limit order price")
        plt.grid()
        plt.show()

    return delta[config.q_max][-1]


def get_optimal_quote(config: OptimalQuoteConfig):
    """Solve for the optimal limit-order price offset from the mid-price.

    Args:
        config: OptimalQuoteConfig with order and market parameters.

    Returns:
        Optimal price offset in currency (scaled by tick size).

    Note:
        Symbol is accepted for caller bookkeeping/logging but does not affect
        the calculation. Per-instrument calibration should customize the market
        parameters (mu, sigma, A, k, gamma, b) from that symbol's recent data.
    """
    gamma = config.gamma
    if gamma is None:
        gamma = 5e-4 / config.tick_size

    formula_config = OptimalLimitOrderFormulaConfig(
        q_max=math.ceil(config.quantity / config.average_trading_size),
        t_max=config.time_in_seconds,
        mu=config.mu,
        sigma=config.sigma,
        A=config.A,
        k=config.k,
        gamma=gamma,
        b=config.b,
        is_plot=config.is_plot,
    )

    quote = optimal_limit_order_formula(formula_config)
    quote = quote * config.tick_size
    if not math.isfinite(quote):
        return 0.0
    return quote


if __name__ == "__main__":
    config = OptimalQuoteConfig(
        symbol="demo",
        quantity=500,
        time_in_seconds=600,
    )
    quote = get_optimal_quote(config)
    buy_sign = "-" if np.sign(-1 * quote) < 0 else "+"
    sell_sign = "-" if np.sign(quote) < 0 else "+"
    print(f"buy@mid {buy_sign} {np.abs(quote)} USD")
    print(f"sell@mid {sell_sign} {np.abs(quote)} USD")

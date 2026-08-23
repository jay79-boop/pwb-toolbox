from functools import partial
from multiprocessing import Pool
import os
import random

from deap import base, creator, tools, algorithms
import numpy as np
import pandas as pd

from .backtest_engine import run_strategy
from .config import StrategyConfig, GAOptimizationConfig
from ..datasets import get_pricing
from ..performance.metrics import calmar_ratio


def _evaluate(
    individual,
    indicator_cls,
    strategy_cls,
    strategy_kwargs,
    symbols,
    start_date,
    cash,
    cerebro_kwargs,
    broker_kwargs,
    n_weights,
):
    """Return -Calmar ratio (GA minimises) for one candidate."""
    bias = individual[0]  # scalar bias
    weights = individual[1 : 1 + n_weights]  # list of length n

    # Build the indicator kwargs expected by your strategy
    indicator_kwargs = {
        "bias": bias,
        "weights": weights,
    }

    config = StrategyConfig(
        indicator_cls=indicator_cls,
        indicator_kwargs=indicator_kwargs,
        strategy_cls=strategy_cls,
        strategy_kwargs=strategy_kwargs,
        symbols=symbols,
        start_date=start_date,
        cash=cash,
        cerebro_kwargs=cerebro_kwargs,
        broker_kwargs=broker_kwargs,
    )
    strategy = run_strategy(config)

    # Get strategy NAV
    nav_df = pd.DataFrame(strategy.log_data)
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_df.set_index("date", inplace=True)

    calmar = calmar_ratio(nav_df["value"])

    return (-calmar,)  # GA minimizes


def optimize_strategy_ga(
    strategy_config: StrategyConfig,
    optimization_config: GAOptimizationConfig,
):
    """Optimize strategy indicator parameters using genetic algorithm.

    Args:
        strategy_config: StrategyConfig with strategy, data, and backtrader settings.
        optimization_config: GAOptimizationConfig with GA hyperparameters.

    Returns:
        Dict with best bias, weights, Calmar ratio, and convergence logbook.
    """
    if optimization_config.seed is not None:
        random.seed(optimization_config.seed)
        np.random.seed(optimization_config.seed)

    genome_len = 1 + optimization_config.n_weights  # bias + weights

    # Fitness (single objective, we minimise negative Calmar)
    creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
    creator.create("Individual", list, fitness=creator.FitnessMin)

    toolbox = base.Toolbox()

    # Gene initialisers ------------------------------------------------
    bias_low, bias_high = optimization_config.bias_bounds
    w_low, w_high = optimization_config.weight_bounds

    toolbox.register("bias_gene", random.uniform, bias_low, bias_high)
    toolbox.register("weight_gene", random.uniform, w_low, w_high)

    # Individual & population ------------------------------------------
    toolbox.register(
        "individual",
        tools.initCycle,
        creator.Individual,
        (toolbox.bias_gene,) + (toolbox.weight_gene,) * optimization_config.n_weights,
        n=1,
    )
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)

    # Evaluation wrapped with fixed args -------------------------------
    toolbox.register(
        "evaluate",
        partial(
            _evaluate,
            indicator_cls=strategy_config.indicator_cls,
            strategy_cls=strategy_config.strategy_cls,
            strategy_kwargs=strategy_config.strategy_kwargs,
            symbols=strategy_config.symbols,
            start_date=strategy_config.start_date,
            cash=strategy_config.cash,
            cerebro_kwargs=strategy_config.cerebro_kwargs,
            broker_kwargs=strategy_config.broker_kwargs,
            n_weights=optimization_config.n_weights,
        ),
    )

    # Parallel evaluation -----------------------------
    total_cores = os.cpu_count()
    n_workers = max(1, total_cores // 2)
    pool = Pool(processes=n_workers)
    toolbox.register("map", pool.map)

    # Operators --------------------------------------------------------
    toolbox.register("mate", tools.cxBlend, alpha=0.4)
    toolbox.register("mutate", tools.mutGaussian, mu=0, sigma=2, indpb=0.2)
    toolbox.register("select", tools.selTournament, tournsize=3)

    # GA loop ----------------------------------------------------------
    pop = toolbox.population(optimization_config.pop_size)

    # statistics (optional)
    stats = tools.Statistics(lambda ind: -ind.fitness.values[0])  # Calmar
    stats.register("avg", np.mean)
    stats.register("max", np.max)

    pop, logbook = algorithms.eaSimple(
        pop,
        toolbox,
        cxpb=optimization_config.cx_prob,
        mutpb=optimization_config.mut_prob,
        ngen=optimization_config.n_generations,
        stats=stats,
        verbose=True,
    )

    # Close the pool to free resources
    pool.close()
    pool.join()

    # Best individual --------------------------------------------------
    best_ind = tools.selBest(pop, k=1)[0]
    best_bias = best_ind[0]
    best_weights = best_ind[1:]

    best_calmar = -best_ind.fitness.values[0]

    return {
        "bias": best_bias,
        "weights": best_weights,
        "calmar": best_calmar,
        "logbook": logbook,  # so you can inspect convergence
    }

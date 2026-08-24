"""Probability that the underlying finishes beyond a level, or ever touches it.

These are the two numbers a ladder is built on: "what are the odds this rung
prints" and "what are the odds price merely visits it". Both come out of the
same lognormal assumption the greeks use, and both are worth being blunt
about — they are the *model's* odds, not a forecast, and the model is a
convenient fiction with thin tails and constant volatility.

Which is exactly why ``tools/calibration_audit.py`` exists: it takes these
functions, runs them over years of real daily bars, and reports how often the
level was actually reached when the model said 30%. Read that before sizing
anything off a number from here.

``static/option-lab.js`` carries the same two functions for the journal, and
``tests/test_option_lab.py`` requires the two implementations to agree to
1e-9, so the browser and the backtests cannot end up quoting different odds
on one contract.
"""

from __future__ import annotations

import math

from scipy.stats import norm

from .greeks import DAYS_PER_YEAR


def finish_probability(
    spot: float,
    target: float,
    vol: float,
    days: float,
    rate: float = 0.045,
) -> float:
    """Probability the underlying ends at or beyond ``target`` at expiry.

    Under the risk-neutral measure, which is to say: the market's own pricing
    of that outcome, already reflected in the option's price. It embeds no
    view and no drift you believe in. Useful for ranking the rungs of a
    ladder against each other, misleading if read as "my odds".

    Args:
        spot: Underlying price now.
        target: The level to finish beyond. Above spot reads as "at or
            above", below spot as "at or below".
        vol: Volatility as a decimal (0.30 for 30%).
        days: Calendar days to expiry.
        rate: Risk-free rate as a decimal. Pass 0.0 to ask the driftless
            question, which is what the calibration audit does so that a real
            drift shows up as a finding instead of hiding inside the model.

    Raises:
        ValueError: If spot, target, vol or days is non-positive.
    """
    if spot <= 0 or target <= 0 or vol <= 0 or days <= 0:
        raise ValueError("spot, target, vol and days must be positive")
    years = days / DAYS_PER_YEAR
    d = (math.log(spot / target) + (rate - 0.5 * vol * vol) * years) / (
        vol * math.sqrt(years)
    )
    return float(norm.cdf(d)) if target >= spot else float(1.0 - norm.cdf(d))


def touch_probability(spot: float, target: float, vol: float, days: float) -> float:
    """Probability the underlying touches ``target`` at any point before expiry.

    For driftless Brownian motion the reflection principle gives
    P(max > b) = 2 P(end > b): a level you would only *finish* beyond one time
    in five is *touched* closer to two times in five. That factor of two is
    the whole reason a stop placed at a "20% chance" level gets hit far more
    often than the option chain suggests.

    The drift term is dropped rather than approximated — keeping it would
    imply a precision this identity does not have — and the result is capped
    at 1.

    Raises:
        ValueError: If spot, target, vol or days is non-positive.
    """
    if spot <= 0 or target <= 0 or vol <= 0 or days <= 0:
        raise ValueError("spot, target, vol and days must be positive")
    if target == spot:
        return 1.0
    years = days / DAYS_PER_YEAR
    d = abs(math.log(target / spot)) / (vol * math.sqrt(years))
    return float(min(1.0, 2.0 * norm.cdf(-d)))

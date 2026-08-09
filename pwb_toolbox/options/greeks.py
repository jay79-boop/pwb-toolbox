"""Black-Scholes pricing and greeks for European equity options.

Theta is returned per calendar day rather than annualized, since that is the
number a trader reads off a broker screen. Vega is per one percentage point
of implied volatility, for the same reason.

American early exercise is not modeled. For long calls on non-dividend payers
early exercise is never optimal, so the European price is exact; for puts and
for dividend payers these values are an approximation that understates the
option slightly.

One consequence is worth knowing before it surprises you: a deep in-the-money
European put prices *below* intrinsic value, giving it negative extrinsic value
and positive theta. That is correct here — you cannot exercise early to collect
the strike, so you carry the discount on it, and that discount unwinds in your
favour as expiry approaches. Real American puts carry an early-exercise premium
instead. It does not affect the 0.60-0.70 delta range this module is aimed at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

DAYS_PER_YEAR = 365.0
TRADING_DAYS = 252.0


@dataclass(frozen=True)
class Greeks:
    """Price and risk sensitivities for a single option contract, per share."""

    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 percentage point of IV
    intrinsic: float
    extrinsic: float


def _d1_d2(
    spot: float, strike: float, years: float, rate: float, vol: float
) -> tuple[float, float]:
    sigma_sqrt_t = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / sigma_sqrt_t
    return d1, d1 - sigma_sqrt_t


def black_scholes(
    spot: float,
    strike: float,
    days_to_expiry: float,
    vol: float,
    rate: float = 0.045,
    kind: str = "call",
) -> Greeks:
    """Price a European option and return its greeks.

    Args:
        spot: Underlying price.
        strike: Option strike.
        days_to_expiry: Calendar days remaining. Must be positive.
        vol: Implied volatility as a decimal (0.30 for 30%).
        rate: Risk-free rate as a decimal.
        kind: Either ``"call"`` or ``"put"``.

    Raises:
        ValueError: If ``kind`` is unrecognized or an input is non-positive.
    """
    kind = kind.lower()
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")
    if days_to_expiry <= 0:
        raise ValueError("days_to_expiry must be positive")
    if spot <= 0 or strike <= 0 or vol <= 0:
        raise ValueError("spot, strike and vol must be positive")

    years = days_to_expiry / DAYS_PER_YEAR
    d1, d2 = _d1_d2(spot, strike, years, rate, vol)
    discount = math.exp(-rate * years)
    pdf_d1 = norm.pdf(d1)

    # Shared across both kinds: the volatility term of theta, and gamma/vega.
    decay_term = -spot * pdf_d1 * vol / (2.0 * math.sqrt(years))
    gamma = pdf_d1 / (spot * vol * math.sqrt(years))
    vega = spot * pdf_d1 * math.sqrt(years) / 100.0

    if kind == "call":
        price = spot * norm.cdf(d1) - strike * discount * norm.cdf(d2)
        delta = norm.cdf(d1)
        theta_annual = decay_term - rate * strike * discount * norm.cdf(d2)
        intrinsic = max(spot - strike, 0.0)
    else:
        price = strike * discount * norm.cdf(-d2) - spot * norm.cdf(-d1)
        delta = norm.cdf(d1) - 1.0
        theta_annual = decay_term + rate * strike * discount * norm.cdf(-d2)
        intrinsic = max(strike - spot, 0.0)

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta_annual / DAYS_PER_YEAR,
        vega=vega,
        intrinsic=intrinsic,
        extrinsic=price - intrinsic,
    )


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    days_to_expiry: float,
    rate: float = 0.045,
    kind: str = "call",
    bracket: tuple[float, float] = (1e-4, 5.0),
) -> float | None:
    """Back out the volatility that reprices ``price``.

    The premium you actually paid is the observable fact; a volatility typed in
    from elsewhere is not. Solving backwards keeps every greek consistent with
    the contract in front of you. Returns ``None`` when the price sits outside
    what the model can produce at any volatility — usually a stale or crossed
    quote, or an American put trading under its intrinsic value.
    """
    lo, hi = bracket

    def price_at(v: float) -> float:
        return black_scholes(spot, strike, days_to_expiry, v, rate, kind).price

    if not (price_at(lo) <= price <= price_at(hi)):
        return None

    for _ in range(200):
        mid = 0.5 * (lo + hi)
        p = price_at(mid)
        if abs(p - price) < 1e-10:
            return mid
        if p < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def expected_move(spot: float, vol: float, days: float) -> float:
    """One standard deviation of underlying movement over ``days`` sessions.

    Uses trading days rather than calendar days, since price only moves when
    the market is open.
    """
    if days <= 0:
        return 0.0
    return spot * vol * math.sqrt(days / TRADING_DAYS)

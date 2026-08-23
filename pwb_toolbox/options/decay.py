"""Hold-time analytics for long single-leg option positions.

The question this module answers is "should I still be in this trade?", framed
so the answer is arithmetic rather than judgement.

Two measures do the work:

``hurdle_ratio``
    Checked at entry. How far the underlying must travel each day just to
    offset decay, expressed as a fraction of a normal day's move. It scales
    roughly with the inverse square root of time remaining, which is why
    short-dated contracts rent time so expensively.

``recovery``
    Checked daily on a losing position. How far the underlying must travel to
    return the position to its entry premium, expressed in standard deviations
    of the time actually left. Under ~1 sigma the trade is still live. Past
    ~2 sigma it is a lottery ticket carrying daily rent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .greeks import black_scholes, expected_move

# Required move beyond this many sigma means the recovery is no longer a
# trade thesis, it is a hope. Used as the default drop-dead threshold.
DEFAULT_SIGMA_LIMIT = 1.0
_UNREACHABLE = float("inf")


@dataclass(frozen=True)
class Recovery:
    """What it would take to get a losing position back to entry."""

    current_premium: float
    loss_per_share: float
    breakeven_spot: float | None
    required_move: float
    sigma_required: float
    verdict: str


@dataclass(frozen=True)
class DropDead:
    """The day a losing position stops being worth holding."""

    days_to_expiry: float | None
    sessions_from_now: float | None
    reason: str


def hurdle_ratio(
    spot: float,
    strike: float,
    days_to_expiry: float,
    vol: float,
    rate: float = 0.045,
    kind: str = "call",
) -> float:
    """Daily breakeven move as a fraction of a normal daily move.

    A value of 0.10 means the underlying must deliver a tenth of a typical
    day's range, in your direction, every day, simply to offset decay.
    """
    g = black_scholes(spot, strike, days_to_expiry, vol, rate, kind)
    if g.delta == 0:
        return _UNREACHABLE
    breakeven_drift = abs(g.theta) / abs(g.delta)
    daily_sigma = expected_move(spot, vol, 1.0)
    if daily_sigma == 0:
        return _UNREACHABLE
    return breakeven_drift / daily_sigma


def shot_clock(hours_to_expiry: float, loss_tolerance: float = 0.10) -> float:
    """Hours a near-the-money option can sit on a flat underlying before
    decay has eaten ``loss_tolerance`` of its value.

    Near the money, remaining option value scales with the square root of
    time left (V proportional to sqrt(T)), so on a flat underlying the value
    after holding for t of T remaining hours is sqrt((T - t) / T) of what you
    paid. Inverting: the tolerated loss x is spent at t = T * (1 - (1 - x)^2).

    This is the number that turns "0DTE decays fast" into a decision: bought
    at 10:00 with 5.5 trading hours left and a 10% decay budget, the clock
    reads about an hour — if the move hasn't started by then, the thesis is
    late and the rent is compounding. The approximation is for near-the-money
    contracts; far OTM near expiry decays faster still, as the probability of
    reaching the strike collapses on top of this.
    """
    if hours_to_expiry <= 0:
        raise ValueError("hours_to_expiry must be positive")
    if not 0 < loss_tolerance < 1:
        raise ValueError("loss_tolerance must be between 0 and 1")
    return hours_to_expiry * (1.0 - (1.0 - loss_tolerance) ** 2)


def breakeven_spot(
    target_premium: float,
    strike: float,
    days_to_expiry: float,
    vol: float,
    rate: float = 0.045,
    kind: str = "call",
    bracket: tuple[float, float] = (0.01, 100.0),
) -> float | None:
    """Underlying price at which the option is worth ``target_premium``.

    Solved numerically rather than from delta, so the answer accounts for both
    the curvature of the option and the decay still to be paid between now and
    ``days_to_expiry``. Returns ``None`` when the target is unreachable at that
    horizon — which for a put means the premium exceeds the discounted strike.
    """
    lo, hi = bracket[0] * strike, bracket[1] * strike

    def price_at(s: float) -> float:
        return black_scholes(s, strike, days_to_expiry, vol, rate, kind).price

    # Calls rise with spot, puts fall; orient the search accordingly.
    increasing = kind.lower() == "call"
    lo_price, hi_price = price_at(lo), price_at(hi)
    best, worst = (hi_price, lo_price) if increasing else (lo_price, hi_price)
    if target_premium > best or target_premium < worst:
        return None

    for _ in range(200):
        mid = 0.5 * (lo + hi)
        p = price_at(mid)
        if abs(p - target_premium) < 1e-8:
            return mid
        if (p < target_premium) == increasing:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def recovery(
    entry_premium: float,
    spot: float,
    strike: float,
    days_to_expiry: float,
    vol: float,
    rate: float = 0.045,
    kind: str = "call",
    horizon_days: float | None = None,
) -> Recovery:
    """Measure how far a losing position is from breakeven, in sigma.

    Args:
        entry_premium: Premium paid per share.
        horizon_days: Calendar days you would actually hold for. Defaults to
            the full remaining life, but should be set to your planned exit
            (the 21-DTE rule) so the sigma budget reflects reality.
    """
    now = black_scholes(spot, strike, days_to_expiry, vol, rate, kind)
    horizon = days_to_expiry if horizon_days is None else horizon_days
    horizon = max(min(horizon, days_to_expiry), 1e-9)
    remaining = max(days_to_expiry - horizon, 1e-9)

    target_spot = breakeven_spot(entry_premium, strike, remaining, vol, rate, kind)
    loss = entry_premium - now.price

    if target_spot is None:
        move, sigma = _UNREACHABLE, _UNREACHABLE
    else:
        move = target_spot - spot
        # Sigma is scaled by trading sessions, so convert the calendar horizon.
        sessions = horizon * 252.0 / 365.0
        band = expected_move(spot, vol, sessions)
        sigma = abs(move) / band if band > 0 else _UNREACHABLE

    return Recovery(
        current_premium=now.price,
        loss_per_share=loss,
        breakeven_spot=target_spot,
        required_move=move,
        sigma_required=sigma,
        verdict=_verdict(sigma, loss),
    )


def _verdict(sigma: float, loss: float) -> str:
    if loss <= 0:
        return "position is at or above entry"
    if sigma == _UNREACHABLE:
        return "EXIT - breakeven is unreachable at this horizon"
    if sigma < 0.5:
        return "live - recovery is well within a normal move"
    if sigma < DEFAULT_SIGMA_LIMIT:
        return "live - recovery plausible but shrinking"
    if sigma < 2.0:
        return "EXIT - recovery needs an outsized move"
    return "EXIT - recovery is a tail event, you are paying rent on a lottery ticket"


def drop_dead(
    entry_premium: float,
    spot: float,
    strike: float,
    days_to_expiry: float,
    vol: float,
    rate: float = 0.045,
    kind: str = "call",
    sigma_limit: float = DEFAULT_SIGMA_LIMIT,
    floor_dte: float = 21.0,
) -> DropDead:
    """The day this position stops being worth holding, assuming spot is flat.

    Walks forward one session at a time holding the underlying still, and
    reports the first day on which recovering to entry would require more than
    ``sigma_limit`` standard deviations of the time then remaining. This is the
    date to write on the trade card at entry, before there is any money on the
    line to argue with.
    """
    if days_to_expiry <= floor_dte:
        return DropDead(
            days_to_expiry, 0.0, f"already inside the {floor_dte:g}-DTE exit"
        )

    step = 365.0 / 252.0  # one trading session in calendar days
    dte = days_to_expiry
    sessions = 0.0

    while dte > floor_dte:
        r = recovery(
            entry_premium,
            spot,
            strike,
            dte,
            vol,
            rate,
            kind,
            horizon_days=dte - floor_dte,
        )
        if r.sigma_required > sigma_limit:
            return DropDead(dte, sessions, f"recovery exceeds {sigma_limit:g} sigma")
        dte -= step
        sessions += 1.0

    return DropDead(
        floor_dte,
        sessions,
        f"reaches the {floor_dte:g}-DTE exit while still recoverable",
    )


def decay_schedule(
    spot: float,
    strike: float,
    days_to_expiry: float,
    vol: float,
    rate: float = 0.045,
    kind: str = "call",
    checkpoints: tuple[float, ...] = (45, 30, 21, 14, 7, 3, 1),
) -> list[tuple[float, float, float]]:
    """Extrinsic value remaining at each checkpoint, holding spot flat.

    Returns ``(dte, extrinsic, pct_of_today)`` triples for every checkpoint at
    or below the current days to expiry.
    """
    today = black_scholes(spot, strike, days_to_expiry, vol, rate, kind)
    rows = []
    for dte in checkpoints:
        if dte > days_to_expiry:
            continue
        g = black_scholes(spot, strike, dte, vol, rate, kind)
        pct = g.extrinsic / today.extrinsic * 100.0 if today.extrinsic > 0 else 0.0
        rows.append((dte, g.extrinsic, pct))
    return rows

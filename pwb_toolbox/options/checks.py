"""Pre-trade gates for long single-leg option positions.

Each gate returns PASS, FAIL, or UNKNOWN. UNKNOWN is not a free pass — a card
with an unanswered gate does not clear, because "I did not check whether this
stock reports earnings next week" and "this stock does not report earnings next
week" are different statements, and only one of them is a reason to trade.

The gates split into two families. Position gates (size, contract selection)
come from the plan's own rules. Market gates (implied volatility, spread,
earnings, target reach) come from data the price chart does not carry, and are
the ones a momentum-indicator process is structurally blind to.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .greeks import black_scholes, expected_move

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

MAX_PREMIUM_PCT = 4.0
MIN_DTE, MAX_DTE = 30.0, 45.0
MIN_DELTA, MAX_DELTA = 0.55, 0.75
MAX_HURDLE = 0.25
MAX_IV_RANK = 50.0
ELEVATED_IV_RANK = 30.0
MAX_SPREAD_PCT = 5.0
GOOD_SPREAD_PCT = 2.0
EXIT_DTE = 21.0


@dataclass(frozen=True)
class Check:
    """One gate's result."""

    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == PASS


def check_size(cost: float, account: float, limit: float = MAX_PREMIUM_PCT) -> Check:
    pct = cost / account * 100
    if pct <= limit:
        return Check("size", PASS, f"{pct:.1f}% of account (limit {limit:g}%)")
    return Check("size", FAIL, f"{pct:.1f}% of account, over the {limit:g}% limit")


def check_dte(dte: float) -> Check:
    if MIN_DTE <= dte <= MAX_DTE:
        return Check("days to expiry", PASS, f"{dte:g} DTE")
    if dte < MIN_DTE:
        return Check(
            "days to expiry",
            FAIL,
            f"{dte:g} DTE is short — the rule is {MIN_DTE:g}-{MAX_DTE:g}",
        )
    return Check(
        "days to expiry",
        FAIL,
        f"{dte:g} DTE is longer than needed; you are paying for time you exit before",
    )


def check_delta(delta: float) -> Check:
    d = abs(delta)
    if MIN_DELTA <= d <= MAX_DELTA:
        return Check("delta", PASS, f"{delta:.2f}")
    if d < MIN_DELTA:
        return Check(
            "delta",
            FAIL,
            f"{delta:.2f} is too far out of the money — lottery territory",
        )
    return Check(
        "delta", FAIL, f"{delta:.2f} is deep in the money; buy the stock instead"
    )


def check_hurdle(hurdle: float, limit: float = MAX_HURDLE) -> Check:
    if hurdle <= limit:
        return Check("decay hurdle", PASS, f"{hurdle:.2f}x a normal daily move")
    return Check(
        "decay hurdle",
        FAIL,
        f"{hurdle:.2f}x a normal daily move — the stock must run every day to stand still",
    )


def check_iv_rank(iv_rank: float | None) -> Check:
    """You are buying premium, so you want implied volatility low, not high."""
    if iv_rank is None:
        return Check(
            "IV rank",
            UNKNOWN,
            "not supplied — read it off the option chain before committing",
        )
    if iv_rank > MAX_IV_RANK:
        return Check(
            "IV rank",
            FAIL,
            f"{iv_rank:.0f} — you are buying premium at above-median prices",
        )
    if iv_rank > ELEVATED_IV_RANK:
        return Check("IV rank", PASS, f"{iv_rank:.0f} — acceptable but not cheap")
    return Check("IV rank", PASS, f"{iv_rank:.0f} — premium is cheap")


def check_spread(bid: float | None, ask: float | None) -> Check:
    """Round-trip friction, the one cost that is certain before the trade opens."""
    if bid is None or ask is None:
        return Check("bid/ask spread", UNKNOWN, "not supplied — quote the chain")
    if ask <= 0 or bid < 0 or ask < bid:
        return Check("bid/ask spread", FAIL, f"nonsensical quote {bid} / {ask}")
    mid = (bid + ask) / 2
    if mid == 0:
        return Check("bid/ask spread", FAIL, "zero mid price")
    pct = (ask - bid) / mid * 100
    if pct > MAX_SPREAD_PCT:
        return Check(
            "bid/ask spread",
            FAIL,
            f"{pct:.1f}% of mid — friction eats the edge before direction matters",
        )
    quality = "tight" if pct <= GOOD_SPREAD_PCT else "acceptable"
    return Check("bid/ask spread", PASS, f"{pct:.1f}% of mid ({quality})")


def check_earnings(
    earnings: dt.date | None,
    dte: float,
    today: dt.date | None = None,
    declared_none: bool = False,
) -> Check:
    """Holding a long option through an earnings print invites a volatility crush.

    Being right on direction and still losing money is the characteristic way
    this trade fails, which is why it is a gate rather than a note.
    """
    if earnings is None and not declared_none:
        return Check(
            "earnings",
            UNKNOWN,
            "not supplied — check the calendar, or pass --no-earnings to declare it clear",
        )
    today = today or dt.date.today()
    exit_date = today + dt.timedelta(days=round(max(dte - EXIT_DTE, 0)))
    if earnings is None:
        return Check("earnings", PASS, f"declared clear through {exit_date}")
    if today <= earnings <= exit_date:
        return Check(
            "earnings",
            FAIL,
            f"reports {earnings}, before your {exit_date} exit — IV crush risk",
        )
    return Check("earnings", PASS, f"reports {earnings}, after your {exit_date} exit")


def check_target(
    target: float | None,
    spot: float,
    vol: float,
    dte: float,
    kind: str = "call",
) -> Check:
    """Is the move you need one the options market thinks is likely?

    The expected move is the market's own one-sigma forecast for the horizon.
    A target beyond it is a bet the market prices as unlikely — which can still
    be the right bet, but should be a decision rather than an accident.
    """
    if target is None:
        return Check("target within expected move", UNKNOWN, "no price target supplied")
    horizon = max(dte - EXIT_DTE, 1.0)
    band = expected_move(spot, vol, horizon * 252.0 / 365.0)
    if band <= 0:
        return Check("target within expected move", FAIL, "degenerate expected move")

    move = target - spot
    wrong_way = (kind.lower() == "call" and move <= 0) or (
        kind.lower() == "put" and move >= 0
    )
    if wrong_way:
        return Check(
            "target within expected move",
            FAIL,
            f"target {target:g} is the wrong side of spot {spot:g} for a {kind}",
        )

    sigma = abs(move) / band
    if sigma > 1.0:
        return Check(
            "target within expected move",
            FAIL,
            f"needs {sigma:.2f} sigma ({move:+.2f}) by your exit; expected move is "
            f"+/-{band:.2f}",
        )
    return Check(
        "target within expected move",
        PASS,
        f"needs {sigma:.2f} sigma ({move:+.2f}); expected move is +/-{band:.2f}",
    )


def run_all(
    *,
    spot: float,
    strike: float,
    dte: float,
    vol: float,
    premium: float,
    contracts: int,
    account: float,
    kind: str = "call",
    rate: float = 0.045,
    hurdle: float,
    iv_rank: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    earnings: dt.date | None = None,
    declared_no_earnings: bool = False,
    target: float | None = None,
    today: dt.date | None = None,
) -> list[Check]:
    """Run every gate, position gates first."""
    g = black_scholes(spot, strike, dte, vol, rate, kind)
    return [
        check_size(premium * 100 * contracts, account),
        check_dte(dte),
        check_delta(g.delta),
        check_hurdle(hurdle),
        check_iv_rank(iv_rank),
        check_spread(bid, ask),
        check_earnings(earnings, dte, today, declared_no_earnings),
        check_target(target, spot, vol, dte, kind),
    ]


def verdict(checks: list[Check]) -> str:
    """A card clears only when every gate passes. Unknown is not permission."""
    if any(c.status == FAIL for c in checks):
        return FAIL
    if any(c.status == UNKNOWN for c in checks):
        return UNKNOWN
    return PASS

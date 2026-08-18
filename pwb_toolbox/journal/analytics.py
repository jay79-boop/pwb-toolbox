"""Diagnostics over a set of round-trip option trades.

These answer the questions a trade log exists to answer, and which memory
answers badly: which exits actually fire, whether the time-of-day belief holds
up, whether short-dated contracts underperform, and how often a loss was once a
profit.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .schwab import RoundTrip

DTE_BUCKETS = ((0, 7, "0-7"), (7, 21, "8-21"), (21, 45, "22-45"), (45, 10_000, "45+"))
HOUR_LABELS = {
    9: "09:30-10:00 open",
    10: "10:00-11:00",
    11: "11:00-12:00",
    12: "12:00-14:00 midday",
    13: "12:00-14:00 midday",
    14: "14:00-15:00",
    15: "15:00-16:00 close",
}


@dataclass(frozen=True)
class Bucket:
    """Aggregate performance for a slice of the log."""

    label: str
    trades: int
    wins: int
    total_pnl: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.trades if self.trades else 0.0


def _bucketize(groups: dict[str, list[RoundTrip]]) -> list[Bucket]:
    return [
        Bucket(
            label=label,
            trades=len(trips),
            wins=sum(1 for t in trips if t.won),
            total_pnl=sum(t.pnl for t in trips),
        )
        for label, trips in groups.items()
    ]


def exit_census(trips: list[RoundTrip]) -> Counter:
    """How each position actually ended. The direct measure of the habit."""
    return Counter(t.exit_reason for t in trips)


def by_entry_hour(trips: list[RoundTrip]) -> list[Bucket]:
    """Performance by time of day. Empty when the export carries no times."""
    groups: dict[str, list[RoundTrip]] = defaultdict(list)
    for t in trips:
        if t.open_time is None:
            continue
        groups[HOUR_LABELS.get(t.open_time.hour, "outside RTH")].append(t)
    return sorted(_bucketize(groups), key=lambda b: b.label)


def by_dte_bucket(trips: list[RoundTrip]) -> list[Bucket]:
    """Performance by days to expiry at entry. Tests the weeklies question."""
    groups: dict[str, list[RoundTrip]] = defaultdict(list)
    for t in trips:
        dte = t.dte_at_entry
        for low, high, label in DTE_BUCKETS:
            if low <= dte < high:
                groups[label].append(t)
                break
    order = [label for _, _, label in DTE_BUCKETS]
    return sorted(_bucketize(groups), key=lambda b: order.index(b.label))


def hold_time_summary(trips: list[RoundTrip]) -> dict[str, float]:
    """Hold times split by outcome, to see whether losers are held longer."""
    winners = [t.hold_days for t in trips if t.won]
    losers = [t.hold_days for t in trips if not t.won]

    def mean(xs: list[int]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "avg_hold_all": mean([t.hold_days for t in trips]),
        "avg_hold_winners": mean(winners),
        "avg_hold_losers": mean(losers),
        "max_hold": max((t.hold_days for t in trips), default=0),
    }


def wash_sale_candidates(trips: list[RoundTrip], window_days: int = 30) -> list[tuple]:
    """Losing closes followed by a new open in the same underlying inside the window.

    A screen, not a tax determination — the IRS test turns on "substantially
    identical" securities, which for options depends on strike and expiry in
    ways this does not attempt to resolve. Treat hits as questions for your
    accountant.
    """
    hits = []
    losses = sorted(
        (t for t in trips if not t.won), key=lambda t: (t.underlying, t.close_date)
    )
    opens = sorted(trips, key=lambda t: (t.underlying, t.open_date))

    for loss in losses:
        for candidate in opens:
            if candidate.underlying != loss.underlying:
                continue
            gap = (candidate.open_date - loss.close_date).days
            if 0 <= gap <= window_days and candidate is not loss:
                hits.append(
                    (loss.underlying, loss.close_date, candidate.open_date, loss.pnl)
                )
                break
    return hits


def summary(trips: list[RoundTrip]) -> dict[str, float]:
    """Headline numbers for the whole log."""
    if not trips:
        return {"trades": 0}
    wins = [t for t in trips if t.won]
    losses = [t for t in trips if not t.won]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    return {
        "trades": len(trips),
        "win_rate": len(wins) / len(trips) * 100,
        "total_pnl": sum(t.pnl for t in trips),
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
        "expired_worthless": sum(
            1 for t in trips if t.exit_reason == "expired" and not t.won
        ),
    }

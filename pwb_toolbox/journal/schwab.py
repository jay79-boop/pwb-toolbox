"""Parse a Schwab transaction export into round-trip option trades.

Schwab's web export (Accounts > History > Transactions > Export) produces rows
like::

    "Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
    "08/07/2026","Buy to Open","AAPL 09/18/2026 230.00 C",...,"1","$9.40","$0.65","-$940.65"

Two deliberate design choices:

Parsing fails loudly. A row this module cannot interpret raises rather than
being skipped, because a silently dropped fill produces a trade log that looks
complete and is wrong — the worst possible outcome for a document you are going
to draw conclusions from.

Time of day is optional. The web export usually carries only a date, while the
thinkorswim Account Statement export carries execution times. Anything keyed on
time degrades to "unknown" rather than guessing.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field

# "AAPL 09/18/2026 230.00 C"  /  "SPY 12/19/2026 500 P"
OPTION_SYMBOL = re.compile(
    r"^(?P<underlying>[A-Z.]{1,6})\s+"
    r"(?P<expiry>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<strike>[\d.]+)\s+"
    r"(?P<kind>[CP])$"
)

OPENING = {"buy to open", "sell to open"}
CLOSING = {"sell to close", "buy to close"}
EXPIRY = {"expired", "assigned", "exercised"}


class ParseError(ValueError):
    """A transaction row could not be interpreted."""


@dataclass
class Fill:
    """One option execution."""

    date: dt.date
    time: dt.time | None
    action: str
    underlying: str
    expiry: dt.date
    strike: float
    kind: str  # "call" or "put"
    quantity: int
    price: float
    fees: float

    @property
    def contract(self) -> tuple[str, dt.date, float, str]:
        return (self.underlying, self.expiry, self.strike, self.kind)


@dataclass
class RoundTrip:
    """A position opened and later closed or expired."""

    underlying: str
    expiry: dt.date
    strike: float
    kind: str
    quantity: int
    open_date: dt.date
    open_time: dt.time | None
    open_price: float
    close_date: dt.date
    close_price: float
    exit_reason: str
    fees: float = 0.0
    _pnl: float | None = field(default=None, repr=False)

    @property
    def dte_at_entry(self) -> int:
        return (self.expiry - self.open_date).days

    @property
    def hold_days(self) -> int:
        return (self.close_date - self.open_date).days

    @property
    def pnl(self) -> float:
        if self._pnl is not None:
            return self._pnl
        gross = (self.close_price - self.open_price) * 100 * self.quantity
        return gross - self.fees

    @property
    def won(self) -> bool:
        return self.pnl > 0


def _money(raw: str) -> float:
    """Parse a Schwab currency cell. Blank means zero."""
    s = raw.strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        value = float(s)
    except ValueError as exc:
        raise ParseError(f"could not read a money value from {raw!r}") from exc
    return -value if negative else value


def _date_and_time(raw: str) -> tuple[dt.date, dt.time | None]:
    """Parse a Schwab date cell, tolerating 'as of' rows and optional times."""
    s = raw.strip()
    # "08/07/2026 as of 08/06/2026" — the settlement note; take the first date.
    if " as of " in s:
        s = s.split(" as of ")[0].strip()
    for fmt, has_time in (
        ("%m/%d/%Y %H:%M:%S", True),
        ("%m/%d/%Y %H:%M", True),
        ("%m/%d/%Y", False),
    ):
        try:
            parsed = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        return parsed.date(), (parsed.time() if has_time else None)
    raise ParseError(f"could not read a date from {raw!r}")


def parse_fills(text: str) -> list[Fill]:
    """Read option executions from the text of a Schwab transaction export.

    Non-option rows (dividends, transfers, equity trades) are ignored. Option
    rows that cannot be parsed raise ``ParseError``.
    """
    # Schwab sometimes prefixes the file with a title line before the header.
    lines = text.lstrip().splitlines()
    while lines and not lines[0].lstrip('"').startswith("Date"):
        lines.pop(0)
    if not lines:
        raise ParseError("no header row found — expected a column named 'Date'")

    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    fills: list[Fill] = []

    for lineno, row in enumerate(reader, start=2):
        symbol = (row.get("Symbol") or "").strip()
        match = OPTION_SYMBOL.match(symbol)
        if not match:
            continue  # not an option row

        action = (row.get("Action") or "").strip().lower()
        if action not in OPENING | CLOSING | EXPIRY:
            raise ParseError(f"line {lineno}: unrecognized action {action!r}")

        try:
            date, time = _date_and_time(row.get("Date") or "")
            expiry, _ = _date_and_time(match.group("expiry"))
            quantity = abs(int(float((row.get("Quantity") or "0").strip() or 0)))
            price = abs(_money(row.get("Price") or ""))
            fees = abs(_money(row.get("Fees & Comm") or ""))
        except ParseError as exc:
            raise ParseError(f"line {lineno}: {exc}") from exc

        if quantity == 0:
            raise ParseError(f"line {lineno}: zero quantity on an option row")

        fills.append(
            Fill(
                date=date,
                time=time,
                action=action,
                underlying=match.group("underlying"),
                expiry=expiry,
                strike=float(match.group("strike")),
                kind="call" if match.group("kind") == "C" else "put",
                quantity=quantity,
                price=price,
                fees=fees,
            )
        )
    return fills


def pair_round_trips(fills: list[Fill]) -> tuple[list[RoundTrip], list[Fill]]:
    """Match opening fills to closing fills, FIFO within each contract.

    Returns the completed round trips and any fills left unmatched — open
    positions, or closes whose opening fill predates the export window.
    """
    by_contract: dict[tuple, list[Fill]] = {}
    for f in sorted(fills, key=lambda f: (f.date, f.time or dt.time.min)):
        by_contract.setdefault(f.contract, []).append(f)

    trips: list[RoundTrip] = []
    unmatched: list[Fill] = []

    for contract, group in by_contract.items():
        open_queue: list[Fill] = []
        for f in group:
            if f.action in OPENING:
                open_queue.append(f)
                continue

            remaining = f.quantity
            while remaining > 0 and open_queue:
                opener = open_queue[0]
                matched = min(remaining, opener.quantity)
                expired = f.action in EXPIRY
                trips.append(
                    RoundTrip(
                        underlying=opener.underlying,
                        expiry=opener.expiry,
                        strike=opener.strike,
                        kind=opener.kind,
                        quantity=matched,
                        open_date=opener.date,
                        open_time=opener.time,
                        open_price=opener.price,
                        close_date=f.date,
                        close_price=0.0 if expired else f.price,
                        exit_reason="expired" if expired else "closed",
                        fees=opener.fees + f.fees,
                    )
                )
                remaining -= matched
                opener.quantity -= matched
                if opener.quantity == 0:
                    open_queue.pop(0)

            if remaining > 0:
                unmatched.append(f)

        unmatched.extend(open_queue)

    trips.sort(key=lambda t: (t.open_date, t.underlying))
    return trips, unmatched


def load(text: str) -> tuple[list[RoundTrip], list[Fill]]:
    """Parse an export and pair it into round trips in one step."""
    return pair_round_trips(parse_fills(text))

"""Turn the way a trade plan names an option into something a broker accepts.

`docs/spec-desk.md` says every plan names its instrument as a human would --
``"NVDA 02OCT26 190C"`` -- because a person has to read it, approve it, and
type it into a platform. Brokers want the four fields separately, and the OCC
wants a fixed-width 21-character symbol. This module is the translation, and it
is deliberately free of any broker SDK so it can be tested without one.

Three shapes go in::

    NVDA 02OCT26 190C        the plan format from docs/spec-desk.md
    NVDA 02OCT26 190 C       the same, loosely spaced
    NVDA  261002C00190000    the OCC 21-character symbol

One shape comes out: an :class:`OptionContract` with the four fields a broker
needs. ``"shares"`` -- what the ``momentum-stock`` lane records -- is not an
option and is rejected as such rather than mangled into one.

The strike is kept as a float because that is what every downstream consumer
(greeks, the ladder, the labs) already uses, but OCC round-tripping goes
through integer thousandths so a strike like 190.005 cannot silently drift.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

__all__ = ["OptionContract", "ParseError", "parse_option_instrument"]


class ParseError(ValueError):
    """Raised when a string is not an option instrument this module knows."""


_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

#: ``NVDA 02OCT26 190C`` and its loosely spaced variants.
_PLAN_FORMAT = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9.]{0,5})\s+"
    r"(?P<day>\d{1,2})(?P<month>[A-Z]{3})(?P<year>\d{2})\s+"
    r"\$?(?P<strike>\d+(?:\.\d+)?)\s*"
    r"(?P<right>[CP])$",
    re.IGNORECASE,
)

#: The OCC's fixed-width symbol: 6-char root, YYMMDD, C/P, strike in thousandths.
_OCC_FORMAT = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9.]{0,5})\s*"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<right>[CP])"
    r"(?P<strike>\d{8})$",
    re.IGNORECASE,
)

#: Two-digit years are unambiguous for listed options: nothing trades in 1926.
_CENTURY = 2000


@dataclass(frozen=True)
class OptionContract:
    """One listed option, in the four fields every broker asks for."""

    underlying: str
    expiry: dt.date
    strike: float
    right: str  # "C" or "P"

    def __post_init__(self) -> None:
        if self.right not in ("C", "P"):
            raise ParseError(f"right must be 'C' or 'P', got {self.right!r}")
        if self.strike <= 0:
            raise ParseError(f"strike must be positive, got {self.strike!r}")

    @property
    def occ_symbol(self) -> str:
        """The OCC 21-character symbol, which is what most APIs key on."""

        thousandths = int(round(self.strike * 1000))
        return (
            f"{self.underlying:<6}"
            f"{self.expiry:%y%m%d}"
            f"{self.right}"
            f"{thousandths:08d}"
        )

    @property
    def ib_expiry(self) -> str:
        """IB wants ``lastTradeDateOrContractMonth`` as ``YYYYMMDD``."""

        return f"{self.expiry:%Y%m%d}"

    def dte(self, today: dt.date | None = None) -> int:
        """Calendar days to expiry — the number every shot clock starts from."""

        return (self.expiry - (today or dt.date.today())).days

    def describe(self) -> str:
        """Back to the plan format, so a round trip is readable."""

        strike = f"{self.strike:g}"
        return (
            f"{self.underlying} "
            f"{self.expiry:%d%b%y}".upper() + f" {strike}{self.right}"
        )


def _year(two_digit: str) -> int:
    return _CENTURY + int(two_digit)


def parse_option_instrument(text: str) -> OptionContract:
    """Parse a plan-format or OCC option symbol into an :class:`OptionContract`.

    Raises :class:`ParseError` on anything else, including the literal
    ``"shares"`` the ``momentum-stock`` lane records — a share position has no
    strike or expiry, and quietly inventing one would be worse than failing.
    """

    if text is None:
        raise ParseError("no instrument given")
    cleaned = " ".join(str(text).strip().split())
    if not cleaned:
        raise ParseError("no instrument given")

    match = _PLAN_FORMAT.match(cleaned)
    if match:
        month = _MONTHS.get(match.group("month").upper())
        if month is None:
            raise ParseError(f"unknown month in {text!r}")
        try:
            expiry = dt.date(_year(match.group("year")), month, int(match.group("day")))
        except ValueError as exc:
            raise ParseError(f"impossible expiry date in {text!r}: {exc}") from exc
        return OptionContract(
            underlying=match.group("root").upper(),
            expiry=expiry,
            strike=float(match.group("strike")),
            right=match.group("right").upper(),
        )

    match = _OCC_FORMAT.match(cleaned)
    if match:
        try:
            expiry = dt.date(
                _year(match.group("yy")),
                int(match.group("mm")),
                int(match.group("dd")),
            )
        except ValueError as exc:
            raise ParseError(f"impossible expiry date in {text!r}: {exc}") from exc
        return OptionContract(
            underlying=match.group("root").upper(),
            expiry=expiry,
            strike=int(match.group("strike")) / 1000.0,
            right=match.group("right").upper(),
        )

    raise ParseError(
        f"{text!r} is not an option instrument. Expected the plan format "
        "'NVDA 02OCT26 190C' or an OCC symbol 'NVDA  261002C00190000'."
    )

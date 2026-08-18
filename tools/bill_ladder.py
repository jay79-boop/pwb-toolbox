#!/usr/bin/env python
"""Does rolling short T-bills beat just buying the longer one?

The intuition that sends people to the 4-week bill is that rolling it thirteen
times a year compounds, while a single 13-week bill pays simple interest and
only compounds four times. That is true, and it is worth about one basis point.
The yield you gave up to get it is usually worth twenty.

This settles the question with the actual curve instead of the intuition. It
pulls Treasury's daily bill rates, converts every maturity to the same
effective-annual basis so they are directly comparable, and reports what the
short bill would have to pay for the roll to break even. If today's short rate
is below that number, rolling is a bet that rates rise — priced, not free.

Three commands:

    curve     The bill curve as it stands, every maturity on one basis, with
              the break-even for rolling 4-week paper out to each of them.

    compare   Roll one maturity against holding another over a horizon. The
              dollars, the effective annual yields, and the rate the roll needs
              to average from here to tie.

    savings   What a bill is worth after tax against a savings account or CD.
              Bill interest is exempt from state and local income tax, which is
              the part that actually moves the decision in a high-tax state.

Every command takes explicit --rate overrides, so all of it works with no
network. Examples::

    python tools/bill_ladder.py curve
    python tools/bill_ladder.py compare --roll-rate 3.65 --hold-rate 3.86
    python tools/bill_ladder.py compare --roll-weeks 4 --hold-weeks 26 --live
    python tools/bill_ladder.py savings --bill-rate 3.68 --savings-apy 3.90 \\
        --federal 24 --state 9.3

Rates are entered as percentages: ``--roll-rate 3.65`` means 3.65%.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass

import click

# Treasury quotes bill coupon-equivalent yields on an actual/365 simple-interest
# basis, which is what makes the conversions below exact rather than approximate.
DAY_COUNT = 365.0

BILL_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_bill_rates&field_tdr_date_value={year}&page&_format=csv"
)

_COUPON_EQUIVALENT = re.compile(r"(\d+)\s*WEEKS?\s+COUPON\s+EQUIVALENT", re.I)

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")


# --------------------------------------------------------------------------
# The math
# --------------------------------------------------------------------------


def growth(rate: float, days: int) -> float:
    """Growth factor of one dollar held ``days`` at a coupon-equivalent ``rate``."""
    return 1.0 + rate * days / DAY_COUNT


def effective_annual(rate: float, days: int) -> float:
    """Annualize ``rate`` over a ``days`` holding period, compounding the rolls.

    This is the only basis on which two maturities are comparable. A 4-week bill
    and a 13-week bill quoted at the same number do not pay the same amount over
    a year, because the short one compounds more often.
    """
    return growth(rate, days) ** (DAY_COUNT / days) - 1.0


def roll_growth(rate: float, term_days: int, horizon_days: int) -> float:
    """Growth from rolling a ``term_days`` bill at a constant ``rate``.

    A horizon that is not a whole number of terms leaves a stub, which is held
    at the same rate rather than silently dropped — otherwise a 91-day horizon
    would credit the 4-week roll with only 84 days of interest and the
    comparison would flatter the longer bill.
    """
    whole, stub = divmod(horizon_days, term_days)
    factor = growth(rate, term_days) ** whole
    if stub:
        factor *= growth(rate, stub)
    return factor


def _solve(target: float, factor, lo: float = -0.5, hi: float = 1.0) -> float:
    """Bisect for the rate whose growth ``factor`` hits ``target``."""
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if factor(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def breakeven_roll_rate(
    hold_rate: float, hold_days: int, roll_term_days: int, horizon_days: int
) -> float:
    """Constant short rate at which rolling ties holding, over the same horizon.

    Compare today's short rate against this. Below it, the roll only wins if
    rates rise; above it, the roll wins even if they never move.
    """
    target = roll_growth(hold_rate, hold_days, horizon_days)
    return _solve(target, lambda r: roll_growth(r, roll_term_days, horizon_days))


def forward_breakeven_rate(
    hold_rate: float,
    hold_days: int,
    roll_term_days: int,
    horizon_days: int,
    first_rate: float,
) -> float | None:
    """What the *remaining* rolls must average, given the first leg is locked.

    The first roll is bought at today's auction, so it cannot help. Only the
    rolls after it can make up the gap, which is why this number is always
    further from today's rate than the flat break-even — and is the honest
    statement of the bet being taken.
    """
    remaining = horizon_days - roll_term_days
    if remaining <= 0:
        return None
    target = roll_growth(hold_rate, hold_days, horizon_days)
    locked = growth(first_rate, roll_term_days)
    return _solve(target / locked, lambda r: roll_growth(r, roll_term_days, remaining))


@dataclass(frozen=True)
class Comparison:
    """One roll-versus-hold verdict, in every unit that matters."""

    horizon_days: int
    principal: float
    roll_term_days: int
    roll_rate: float
    hold_days: int
    hold_rate: float
    roll_growth: float
    hold_growth: float
    breakeven: float
    forward_breakeven: float | None

    @property
    def roll_interest(self) -> float:
        return self.principal * (self.roll_growth - 1.0)

    @property
    def hold_interest(self) -> float:
        return self.principal * (self.hold_growth - 1.0)

    @property
    def edge(self) -> float:
        """Dollars the roll gains (positive) or gives up (negative)."""
        return self.roll_interest - self.hold_interest

    @property
    def roll_annual(self) -> float:
        return effective_annual(self.roll_rate, self.roll_term_days)

    @property
    def hold_annual(self) -> float:
        return effective_annual(self.hold_rate, self.hold_days)

    @property
    def compounding_gain(self) -> float:
        """What rolling more often is worth, holding the rate fixed.

        The whole case for the short bill, isolated: the difference between its
        quoted rate and what that rate becomes once compounded at its own
        frequency. Compare it against the yield given up.
        """
        return self.roll_annual - self.roll_rate


def compare(
    roll_rate: float,
    hold_rate: float,
    roll_term_days: int,
    hold_days: int,
    horizon_days: int | None = None,
    principal: float = 100_000.0,
) -> Comparison:
    """Roll a short bill against holding a longer one over a common horizon."""
    horizon = hold_days if horizon_days is None else horizon_days
    return Comparison(
        horizon_days=horizon,
        principal=principal,
        roll_term_days=roll_term_days,
        roll_rate=roll_rate,
        hold_days=hold_days,
        hold_rate=hold_rate,
        roll_growth=roll_growth(roll_rate, roll_term_days, horizon),
        hold_growth=roll_growth(hold_rate, hold_days, horizon),
        breakeven=breakeven_roll_rate(hold_rate, hold_days, roll_term_days, horizon),
        forward_breakeven=forward_breakeven_rate(
            hold_rate, hold_days, roll_term_days, horizon, roll_rate
        ),
    )


# --------------------------------------------------------------------------
# Tax
# --------------------------------------------------------------------------


def after_tax_rate(
    rate: float, federal: float = 0.0, state: float = 0.0, treasury: bool = False
) -> float:
    """Rate kept after tax. Treasury interest is exempt from state and local tax."""
    return rate * (1.0 - (federal if treasury else federal + state))


def taxable_equivalent_yield(
    rate: float, federal: float = 0.0, state: float = 0.0
) -> float:
    """Pre-tax yield a fully taxable account needs to match this Treasury.

    The number to quote at a savings account: a bank rate below this one is
    losing to the bill even though it looks higher on the screen.
    """
    keep = 1.0 - (federal + state)
    if keep <= 0.0:
        raise ValueError("combined marginal rate must be below 100%")
    return rate * (1.0 - federal) / keep


# --------------------------------------------------------------------------
# The curve
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BillCurve:
    """Coupon-equivalent bill yields for one business day, keyed by weeks."""

    date: dt.date
    rates: dict[int, float]

    def days(self, weeks: int) -> int:
        return weeks * 7

    def rate(self, weeks: int) -> float:
        if weeks not in self.rates:
            raise KeyError(
                f"no {weeks}-week rate on {self.date:%Y-%m-%d}; "
                f"have {sorted(self.rates)}"
            )
        return self.rates[weeks]


def _parse_date(raw: str) -> dt.date | None:
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_bill_csv(text: str) -> BillCurve:
    """Read Treasury's daily bill CSV and return its most recent row.

    Only the coupon-equivalent columns are read. The bank-discount rate sitting
    beside each one is quoted against face value on a 360-day year and is not
    comparable to anything else here; using it by mistake understates the bill
    by roughly a tenth of a point.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty Treasury CSV")

    header = rows[0]
    columns: dict[int, int] = {}
    for index, name in enumerate(header):
        match = _COUPON_EQUIVALENT.search(name)
        if match:
            columns[int(match.group(1))] = index
    if not columns:
        raise ValueError(f"no coupon-equivalent columns in header: {header}")

    best: BillCurve | None = None
    for row in rows[1:]:
        if not row:
            continue
        date = _parse_date(row[0])
        if date is None:
            continue
        rates: dict[int, float] = {}
        for weeks, index in columns.items():
            if index >= len(row):
                continue
            cell = row[index].strip()
            if not cell:
                continue
            try:
                rates[weeks] = float(cell) / 100.0
            except ValueError:
                continue
        if rates and (best is None or date > best.date):
            best = BillCurve(date=date, rates=rates)

    if best is None:
        raise ValueError("Treasury CSV had no usable rows")
    return best


def fetch_curve(session=None, year: int | None = None, url: str | None = None):
    """Fetch today's bill curve. ``session`` is injectable so tests stay offline."""
    if session is None:
        import requests

        session = requests.Session()
    if url is None:
        year = year or dt.date.today().year
        url = BILL_URL.format(year=year)
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return parse_bill_csv(response.text)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _pct(value: float) -> float:
    """CLI rates are entered as percentages; the math works in decimals."""
    return value / 100.0


def _rule(char: str = "-", width: int = 66) -> str:
    return char * width


def _verdict(edge: float) -> tuple[str, str]:
    if edge > 0.0:
        return "ROLL WINS", "green"
    if edge < 0.0:
        return "HOLD WINS", "red"
    return "TIE", "yellow"


def _resolve_curve(live: bool, year: int | None):
    try:
        return fetch_curve(year=year)
    except Exception as exc:  # network, proxy, Treasury outage, schema change
        message = f"could not fetch the Treasury bill curve: {exc}"
        if live:
            raise click.ClickException(message)
        raise click.ClickException(
            message + "\nPass the rates yourself instead, e.g. --roll-rate 3.65 "
            "--hold-rate 3.86"
        )


@click.group()
def cli() -> None:
    """Roll-versus-hold arithmetic for the Treasury bill curve."""


@cli.command()
@click.option("--year", type=int, help="Calendar year to pull. Default: this one.")
@click.option(
    "--roll-weeks",
    default=4,
    type=int,
    help="Maturity assumed to be rolled when computing break-evens.",
)
@click.option("--principal", default=100_000.0, type=float)
def curve(year: int | None, roll_weeks: int, principal: float) -> None:
    """Show the current bill curve on one comparable basis."""
    bills = _resolve_curve(live=True, year=year)

    click.echo(_rule("="))
    click.echo(f"  TREASURY BILL CURVE   {bills.date:%Y-%m-%d}")
    click.echo(_rule("="))
    click.echo("\n  Coupon equivalent, and the same rate compounded to a year:\n")
    click.echo(f"    {'MATURITY':<12}{'QUOTED':>10}{'EFF. ANNUAL':>14}")
    for weeks in sorted(bills.rates):
        rate = bills.rates[weeks]
        annual = effective_annual(rate, bills.days(weeks))
        click.echo(f"    {f'{weeks} weeks':<12}{rate:>9.2%}{annual:>14.2%}")

    if roll_weeks not in bills.rates:
        return

    roll_rate = bills.rate(roll_weeks)
    roll_days = bills.days(roll_weeks)
    longer = [w for w in sorted(bills.rates) if w > roll_weeks]
    if not longer:
        return

    click.echo(f"\n  ROLLING {roll_weeks}-WEEK PAPER AT {roll_rate:.2%} INSTEAD:\n")
    click.echo(
        f"    {'AGAINST':<12}{'BREAK-EVEN':>12}{'GAP':>10}"
        f"{'EDGE':>12}{'':>3}{'VERDICT'}"
    )
    for weeks in longer:
        result = compare(
            roll_rate=roll_rate,
            hold_rate=bills.rate(weeks),
            roll_term_days=roll_days,
            hold_days=bills.days(weeks),
            principal=principal,
        )
        label, colour = _verdict(result.edge)
        gap = roll_rate - result.breakeven
        click.echo(
            f"    {f'{weeks} weeks':<12}{result.breakeven:>12.2%}"
            f"{gap:>+10.2%}{result.edge:>+12,.0f}   "
            + click.style(label, fg=colour, bold=True)
        )
    click.echo(
        f"\n  Break-even is the flat {roll_weeks}-week rate that ties. Today's rate"
    )
    click.echo("  below it means the roll needs rates to rise to win.")
    click.echo(
        f"  Edge is dollars on {principal:,.0f} over each maturity's own term.\n"
    )


@cli.command()
@click.option("--roll-weeks", default=4, type=int, help="Maturity you would roll.")
@click.option("--hold-weeks", default=13, type=int, help="Maturity you would hold.")
@click.option("--roll-rate", type=float, help="Short rate, percent. Omit to fetch.")
@click.option("--hold-rate", type=float, help="Long rate, percent. Omit to fetch.")
@click.option(
    "--horizon-weeks",
    type=int,
    help="Comparison window. Default: the held maturity's own term.",
)
@click.option("--principal", default=100_000.0, type=float)
@click.option("--live", is_flag=True, help="Force a fetch even if rates were given.")
@click.option("--year", type=int)
def compare_cmd(
    roll_weeks: int,
    hold_weeks: int,
    roll_rate: float | None,
    hold_rate: float | None,
    horizon_weeks: int | None,
    principal: float,
    live: bool,
    year: int | None,
) -> None:
    """Roll one maturity against holding another."""
    if live or roll_rate is None or hold_rate is None:
        bills = _resolve_curve(live=live, year=year)
        source = f"Treasury, {bills.date:%Y-%m-%d}"
        if live or roll_rate is None:
            roll_rate = bills.rate(roll_weeks) * 100.0
        if live or hold_rate is None:
            hold_rate = bills.rate(hold_weeks) * 100.0
    else:
        source = "your input"

    result = compare(
        roll_rate=_pct(roll_rate),
        hold_rate=_pct(hold_rate),
        roll_term_days=roll_weeks * 7,
        hold_days=hold_weeks * 7,
        horizon_days=horizon_weeks * 7 if horizon_weeks else None,
        principal=principal,
    )
    rolls = result.horizon_days / result.roll_term_days

    click.echo(_rule("="))
    click.echo(
        f"  ROLL {roll_weeks}w  vs  HOLD {hold_weeks}w"
        f"   over {result.horizon_days} days   ({source})"
    )
    click.echo(_rule("="))

    roll_label = f"Roll {roll_weeks}w"
    hold_label = f"Hold {hold_weeks}w"

    click.echo("\n  QUOTED, AND COMPOUNDED AT EACH MATURITY'S OWN FREQUENCY")
    click.echo(
        f"    {roll_label:<10}{result.roll_rate:>8.2%}"
        f"   ->{result.roll_annual:>9.3%} effective annual"
    )
    click.echo(
        f"    {hold_label:<10}{result.hold_rate:>8.2%}"
        f"   ->{result.hold_annual:>9.3%} effective annual"
    )

    click.echo(f"\n  OVER {result.horizon_days} DAYS ON {principal:,.0f}")
    click.echo(
        f"    {roll_label + f' x {rolls:.2f}':<18}{result.roll_interest:>12,.2f}"
    )
    click.echo(f"    {hold_label:<18}{result.hold_interest:>12,.2f}")
    label, colour = _verdict(result.edge)
    click.echo(
        f"    {'Difference':<18}{result.edge:>+12,.2f}   "
        + click.style(label, fg=colour, bold=True)
    )

    click.echo("\n  WHAT COMPOUNDING IS ACTUALLY WORTH")
    click.echo(
        f"    Rolling {roll_weeks}w lifts {result.roll_rate:.2%} to "
        f"{result.roll_annual:.3%} — a gain of {result.compounding_gain:.3%}."
    )
    click.echo(
        f"    You gave up {result.hold_rate - result.roll_rate:.2%} of yield "
        f"to collect it."
    )

    click.echo("\n  BREAK-EVEN")
    click.echo(
        f"    {f'Flat {roll_weeks}w rate that ties':<34}{result.breakeven:>8.3%}  "
        f"({result.roll_rate - result.breakeven:+.3%} vs today)"
    )
    if result.forward_breakeven is not None:
        click.echo(
            f"    {'Remaining rolls must average':<34}"
            f"{result.forward_breakeven:>8.3%}  "
            f"({result.forward_breakeven - result.roll_rate:+.3%} from here)"
        )
    click.echo()


cli.add_command(compare_cmd, name="compare")


@cli.command()
@click.option("--bill-rate", type=float, help="Bill coupon equivalent, percent.")
@click.option("--bill-weeks", default=13, type=int)
@click.option(
    "--savings-apy", required=True, type=float, help="Bank APY, percent, as advertised."
)
@click.option("--federal", default=0.0, type=float, help="Federal marginal rate, %.")
@click.option(
    "--state", default=0.0, type=float, help="State + local marginal rate, %."
)
@click.option("--principal", default=100_000.0, type=float)
@click.option("--year", type=int)
def savings(
    bill_rate: float | None,
    bill_weeks: int,
    savings_apy: float,
    federal: float,
    state: float,
    principal: float,
    year: int | None,
) -> None:
    """Compare a bill against a savings account or CD, after tax."""
    if bill_rate is None:
        bills = _resolve_curve(live=False, year=year)
        bill_rate = bills.rate(bill_weeks) * 100.0
        source = f"Treasury, {bills.date:%Y-%m-%d}"
    else:
        source = "your input"

    bill = _pct(bill_rate)
    bank = _pct(savings_apy)
    fed = _pct(federal)
    st = _pct(state)

    # The bank quotes APY, already compounded; the bill does not. Put the bill on
    # the bank's basis so the two numbers mean the same thing before tax touches
    # either of them.
    bill_apy = effective_annual(bill, bill_weeks * 7)
    bill_net = after_tax_rate(bill_apy, fed, st, treasury=True)
    bank_net = after_tax_rate(bank, fed, st, treasury=False)
    tey = taxable_equivalent_yield(bill_apy, fed, st)

    click.echo(_rule("="))
    click.echo(f"  {bill_weeks}-WEEK BILL vs SAVINGS   ({source})")
    click.echo(_rule("="))
    click.echo(
        f"\n  Marginal rates: {federal:g}% federal, {state:g}% state and local\n"
    )
    click.echo(
        f"    {'':<22}{'PRE-TAX':>10}{'AFTER TAX':>12}{'ON ' + f'{principal:,.0f}':>14}"
    )
    click.echo(
        f"    {f'{bill_weeks}-week bill':<22}{bill_apy:>10.2%}"
        f"{bill_net:>12.2%}{principal * bill_net:>14,.0f}"
    )
    click.echo(
        f"    {'Savings / CD':<22}{bank:>10.2%}"
        f"{bank_net:>12.2%}{principal * bank_net:>14,.0f}"
    )
    edge = principal * (bill_net - bank_net)
    label, colour = ("BILL WINS", "green") if edge > 0 else ("BANK WINS", "red")
    click.echo(
        f"    {'Difference':<22}{'':>10}{bill_net - bank_net:>+12.2%}{edge:>+14,.0f}"
        f"   " + click.style(label, fg=colour, bold=True)
    )
    click.echo(
        f"\n  The bill's {bill_apy:.2%} is worth {tey:.2%} in a taxable account."
    )
    click.echo(f"  A bank paying less than {tey:.2%} is losing to it.\n")
    if state == 0.0:
        click.echo(
            "  You passed no state rate, so the exemption scored nothing here.\n"
        )


if __name__ == "__main__":
    cli()

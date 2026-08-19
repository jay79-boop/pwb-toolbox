"""Watch the Profit & Exit Planner and say when something needs a decision.

The workbook is a document you have to remember to open. This is the part that
reaches you: it reads the Watch tab, applies three rules, and writes a message
that carries the decision rather than a nudge to go and look one up.

    python tools/planner_watch.py --url "<published CSV URL>"
    python tools/planner_watch.py --csv watch.csv --state watch-state.json

The three rules, chosen deliberately and kept few:

* a plan's next rung is within reach, or has been passed
* a holding moved more than a set percentage since the last run
* a holding has grown past the weight limit

And two guards that are not rules. A holding whose price is not live is skipped
entirely: a rung alert computed from a price typed months ago is a false alarm,
and false alarms are how alerting dies. A holding with a live price but no
quantity is set aside too — its weight is zero, so every rule passes over it in
silence that looks exactly like nothing being wrong.

Both are counted in the message. An untouched workbook and a portfolio with
nothing to decide are otherwise the same three words, and the first is the more
likely of the two on any given day.

Movement needs yesterday's price, which a spreadsheet cannot remember. The
state file holds the last price seen per holding and nothing else.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_NEAR = 0.05
DEFAULT_MOVE = 0.10

# A price is only trusted when the sheet says the feed produced it. Anything
# else is a number someone typed, however recently.
LIVE = "live"

# The register ships with one filled-in row to show the shape. It is a teaching
# aid, not a position, and alerting on it is noise on day one — when it is also
# the only row with a quantity and therefore 100% of the portfolio.
EXAMPLE = "example"


def _number(raw: str | None) -> float | None:
    """Read a cell the way a person wrote it: $1,234.50, 12%, (44), or blank."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"—", "-", "#N/A", "#REF!", "#VALUE!", "#DIV/0!"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    percent = text.endswith("%")
    for junk in "$,%":
        text = text.replace(junk, "")
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if percent:
        value /= 100
    return -value if negative else value


@dataclass
class Plan:
    plan: str
    holding: str
    ticker: str
    feed: str
    units: float | None
    avg_cost: float | None
    price: float | None
    weight: float | None
    next_gain: float | None
    target: float | None
    away: float | None
    units_to_sell: float | None
    net_cash: float | None

    @property
    def live(self) -> bool:
        return self.feed.strip().lower().startswith(LIVE)


@dataclass
class Holding:
    holding: str
    ticker: str
    asset_class: str
    status: str
    feed: str
    units: float | None
    avg_cost: float | None
    price: float | None
    market_value: float | None
    weight: float | None
    unrealised: float | None

    @property
    def live(self) -> bool:
        return self.feed.strip().lower().startswith(LIVE)

    @property
    def active(self) -> bool:
        return self.status.strip().lower() == "active"


@dataclass
class Report:
    alerts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)
    no_plans: bool = False
    no_rows: bool = False
    prices: dict[str, float] = field(default_factory=dict)

    def text(self) -> str:
        if not self.alerts:
            body = "Nothing needs a decision."
        else:
            body = "\n".join(f"- {line}" for line in self.alerts)
        if self.skipped:
            names = ", ".join(sorted(self.skipped))
            body += (
                f"\n\n{len(self.skipped)} skipped, no live price: {names}. "
                "Nothing can be said about these until the price updates."
            )
        # A live price on a row you hold nothing of measures nothing. Left
        # unsaid, an untouched workbook is byte-identical to a calm portfolio.
        if self.unfilled:
            names = ", ".join(sorted(self.unfilled))
            body += (
                f"\n\n{len(self.unfilled)} with a live price but no quantity: "
                f"{names}. Units and average cost are still blank, so weight "
                "and market value are zero and no rule can fire on them."
            )
        if self.no_plans:
            body += (
                "\n\nNo plan rows to watch. Rung alerts need a plan with a "
                "target price; until one exists that rule is silent whatever "
                "prices do."
            )
        # Nothing parsed at all. Every other line here is derived from rows, so
        # they are all empty too and the report would otherwise be three words
        # of reassurance about a tab it never managed to read.
        if self.no_rows:
            # Kept to ASCII: this prints to a Windows console, where the em
            # dashes used elsewhere in this file come out as replacement
            # characters.
            body += (
                "\n\nNo plan or holding rows found on this tab. Neither header "
                "row ('Plan, Holding' or 'Holding, Ticker') is present, so "
                "nothing was read and nothing could be checked. The usual "
                "cause is --gid pointing at a plan tab rather than the Watch "
                "tab."
            )
        return body


def _money(value: float) -> str:
    return f"${value:,.2f}" if abs(value) < 1000 else f"${value:,.0f}"


def _units(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"


def parse(rows: list[list[str]]) -> tuple[list[Plan], list[Holding]]:
    """Pull the two blocks out of the Watch tab.

    Found by their header row rather than by position, so inserting a note at
    the top of the tab does not silently shift every field by one.
    """
    plans: list[Plan] = []
    holdings: list[Holding] = []
    section = None
    for row in rows:
        cells = [c.strip() for c in row] + [""] * 16
        first = cells[0].lower()
        if first == "plan" and cells[1].lower() == "holding":
            section = "plans"
            continue
        if first == "holding" and cells[1].lower() == "ticker":
            section = "holdings"
            continue
        if not cells[0] or not cells[1]:
            continue
        if section == "plans":
            plans.append(
                Plan(
                    plan=cells[0],
                    holding=cells[1],
                    ticker=cells[2],
                    feed=cells[3],
                    units=_number(cells[4]),
                    avg_cost=_number(cells[5]),
                    price=_number(cells[6]),
                    weight=_number(cells[7]),
                    next_gain=_number(cells[8]),
                    target=_number(cells[9]),
                    away=_number(cells[10]),
                    units_to_sell=_number(cells[11]),
                    net_cash=_number(cells[12]),
                )
            )
        elif section == "holdings":
            holdings.append(
                Holding(
                    holding=cells[0],
                    ticker=cells[1],
                    asset_class=cells[2],
                    status=cells[3],
                    feed=cells[4],
                    units=_number(cells[5]),
                    avg_cost=_number(cells[6]),
                    price=_number(cells[7]),
                    market_value=_number(cells[8]),
                    weight=_number(cells[9]),
                    unrealised=_number(cells[10]),
                )
            )
    return plans, holdings


def check(
    plans: list[Plan],
    holdings: list[Holding],
    previous: dict[str, float],
    *,
    near: float = DEFAULT_NEAR,
    move: float = DEFAULT_MOVE,
    max_weight: float | None = None,
) -> Report:
    report = Report()
    skipped: set[str] = set()
    unfilled: set[str] = set()
    plans_defined = 0

    for plan in plans:
        if plan.holding.strip().lower().startswith(EXAMPLE):
            continue
        # Counted before the live check: the question this answers is whether a
        # plan exists at all, not whether it can be evaluated right now.
        if plan.target is not None:
            plans_defined += 1
        if not plan.live:
            skipped.add(plan.holding)
            continue
        if plan.away is None or plan.target is None or plan.price is None:
            continue
        if plan.away <= 0:
            headline = (
                f"{plan.holding} is past its "
                f"{plan.next_gain:+.0%} rung — {_money(plan.target)}, "
                f"trading at {_money(plan.price)}."
            )
        elif plan.away <= near:
            headline = (
                f"{plan.holding} is {plan.away:.1%} from its "
                f"{plan.next_gain:+.0%} rung at {_money(plan.target)} "
                f"(now {_money(plan.price)})."
            )
        else:
            continue
        # The decision, not a pointer to where the decision is written down.
        if plan.units_to_sell and plan.net_cash is not None:
            left = (plan.units or 0) - plan.units_to_sell
            headline += (
                f" Plan says sell {_units(plan.units_to_sell)} for "
                f"{_money(plan.net_cash)} net, leaving {_units(left)}."
            )
        report.alerts.append(headline)

    for holding in holdings:
        if holding.holding.strip().lower().startswith(EXAMPLE):
            continue
        if not holding.active:
            continue
        if not holding.live or holding.price is None:
            if holding.units:
                skipped.add(holding.holding)
            continue
        # A live price is not the same as a position. With no units the weight
        # is 0.0 — falsy, so the limit check below short-circuits — and market
        # value is 0 too. Every rule passes over the row without a word, which
        # is the one silence this tool must never produce unexplained.
        if not holding.units:
            unfilled.add(holding.holding)
            continue
        report.prices[holding.holding] = holding.price
        was = previous.get(holding.holding)
        if was:
            change = holding.price / was - 1
            if abs(change) >= move:
                report.alerts.append(
                    f"{holding.holding} moved {change:+.1%} since the last check, "
                    f"{_money(was)} to {_money(holding.price)}."
                )
        if max_weight and holding.weight and holding.weight > max_weight:
            report.alerts.append(
                f"{holding.holding} is {holding.weight:.0%} of the portfolio, "
                f"past your {max_weight:.0%} limit."
            )

    report.skipped = sorted(skipped)
    report.unfilled = sorted(unfilled)
    # Only worth saying when there is something to plan for; a tab that parsed
    # nothing is covered by no_rows below, which is the more useful complaint.
    report.no_plans = not plans_defined and bool(report.prices or unfilled)
    report.no_rows = not plans and not holdings
    return report


SHEET_ID = re.compile(r"/spreadsheets/d/(?:e/)?([A-Za-z0-9_-]{20,})")
GID = re.compile(r"[#?&]gid=(\d+)")


class Unreadable(Exception):
    """The sheet could not be fetched, with the reason a person can act on."""


def csv_url(raw: str, gid: str | None = None) -> str:
    """Turn whatever was in the address bar into something that returns CSV.

    The link copied from a browser is an editor page, and asking urllib for it
    gets HTML or a 401. Both of the URLs Google hands out — the /edit one and
    the Publish-to-web one — carry the file id, so the export form can be built
    from either.
    """
    raw = raw.strip()
    if "output=csv" in raw or "format=csv" in raw:
        return raw
    match = SHEET_ID.search(raw)
    if not match:
        if re.fullmatch(r"[A-Za-z0-9_-]{20,}", raw):
            match = None
            file_id = raw
        else:
            raise Unreadable(
                "That does not look like a Google Sheets link. Expected "
                "something starting https://docs.google.com/spreadsheets/d/"
            )
    else:
        file_id = match.group(1)
    tab = gid or (GID.search(raw).group(1) if GID.search(raw) else None)
    if tab is None:
        raise Unreadable(
            "That link does not say which tab to read. Open the Watch tab in "
            "the browser and copy the address again — it ends with #gid=NUMBER "
            "— or pass the number with --gid."
        )
    return (
        f"https://docs.google.com/spreadsheets/d/{file_id}"
        f"/export?format=csv&gid={tab}"
    )


def read_csv(
    *, url: str | None, path: str | None, gid: str | None = None
) -> list[list[str]]:
    if path:
        text = pathlib.Path(path).read_text(encoding="utf-8-sig")
    else:
        target = csv_url(url, gid)
        try:
            with urllib.request.urlopen(target, timeout=30) as response:  # noqa: S310
                text = response.read().decode("utf-8-sig")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise Unreadable(
                    "Google refused to serve the sheet without a login "
                    f"(HTTP {error.code}). The watcher signs in as nobody, so "
                    "the sheet has to be readable by anyone with the link: in "
                    "the sheet, Share > General access > Anyone with the link "
                    "> Viewer. Nothing becomes editable, and only whoever has "
                    "the link can read it."
                ) from error
            if error.code == 404:
                raise Unreadable(
                    "No such sheet or tab (HTTP 404). Check the --gid matches "
                    "the Watch tab."
                ) from error
            raise Unreadable(
                f"Could not fetch the sheet (HTTP {error.code})."
            ) from error
        except urllib.error.URLError as error:
            raise Unreadable(f"Could not reach Google: {error.reason}") from error
    # A login page is HTML, and HTML parsed as CSV is a single nonsense row
    # rather than an error, which would otherwise read as "no holdings".
    if text.lstrip()[:1] == "<":
        raise Unreadable(
            "Google returned a web page instead of data, which means it wants "
            "a login. Share the sheet with Anyone with the link > Viewer."
        )
    return list(csv.reader(io.StringIO(text)))


def load_state(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    file = pathlib.Path(path)
    if not file.exists():
        return {}
    try:
        return {k: float(v) for k, v in json.loads(file.read_text()).items()}
    except (ValueError, TypeError):
        return {}


def save_state(path: str | None, prices: dict[str, float]) -> None:
    if path and prices:
        pathlib.Path(path).write_text(json.dumps(prices, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="published CSV URL for the Watch tab")
    source.add_argument("--csv", help="a local CSV, for testing")
    parser.add_argument("--gid", help="the Watch tab's gid, if the URL omits it")
    parser.add_argument("--state", help="where to remember prices between runs")
    parser.add_argument(
        "--near",
        type=float,
        default=DEFAULT_NEAR,
        help="how close to a rung counts as worth saying (default 5%%)",
    )
    parser.add_argument(
        "--move",
        type=float,
        default=DEFAULT_MOVE,
        help="a move this big since the last run is worth saying (default 10%%)",
    )
    parser.add_argument(
        "--max-weight",
        type=float,
        default=0.20,
        help="flag a holding above this share of the portfolio (default 20%%)",
    )
    parser.add_argument(
        "--quiet-when-nothing",
        action="store_true",
        help="print nothing at all when there is nothing to say",
    )
    args = parser.parse_args()

    try:
        rows = read_csv(url=args.url, path=args.csv, gid=args.gid)
    except Unreadable as problem:
        # A traceback tells you where the code gave up. This tells you what to
        # go and change.
        raise SystemExit(f"Cannot read the sheet.\n\n{problem}")
    plans, holdings = parse(rows)
    report = check(
        plans,
        holdings,
        load_state(args.state),
        near=args.near,
        move=args.move,
        max_weight=args.max_weight,
    )
    save_state(args.state, report.prices)
    if report.alerts or not args.quiet_when_nothing:
        print(report.text())


if __name__ == "__main__":
    main()

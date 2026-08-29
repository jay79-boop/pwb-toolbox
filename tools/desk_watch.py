"""Did the spicy desk actually report?

The desk's product is the record. On 2026-08-25 the morning scan produced a
full report in its log and never wrote the file, so the wrapper logged
``SKIPPED vault append`` and the day left no trace. On 08-26 and 08-27 the run
produced no output at all. Three consecutive scans, no record, and nothing
said so -- the gap was found by hand four days later.

A silent failure looks exactly like a quiet market. This tool tells them apart:
it walks the trading calendar and names every session that should have a report
and does not.

Design notes:

- The calendar is derived from rules, not a table, so it never needs annual
  upkeep. It mirrors ``Scripts/nyse-holidays.ps1`` on the owner's machine --
  the same closures, the same weekend-observed shifting, Good Friday by Gauss's
  Easter algorithm.
- A report that exists but carries no content is *not* treated as a report. A
  file the wrapper created and never filled is the exact failure this exists to
  catch, and calling it present would hide it.
- An unparseable filename is reported as unparseable. It is never guessed into
  a date -- refuse rather than repair.
- Nothing here touches the network, a broker or a clock it was not given.

Usage::

    python tools/desk_watch.py check                  # last 10 sessions
    python tools/desk_watch.py check --since 2026-08-20
    python tools/desk_watch.py calendar --year 2026   # what it thinks is closed

``check`` exits 1 when a session is missing or empty, so a wrapper can react to
it rather than having to read the output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
from dataclasses import dataclass, field

DEFAULT_REPORTS = pathlib.Path("spec_desk/reports")

# A report shorter than this is a stub, not a report. The real ones run to
# several thousand characters; the threshold only has to separate "the wrapper
# touched the file" from "the agent wrote something".
MIN_REPORT_CHARS = 200

_DATE_FILE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


# --------------------------------------------------------------------------
# Calendar. Pure: date rules in, dates out.
# --------------------------------------------------------------------------


def easter(year: int) -> dt.date:
    """Gauss's algorithm. Good Friday is two days before this."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The nth `weekday` of a month. Monday is 0, matching date.weekday()."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        nxt = dt.date(year + 1, 1, 1)
    else:
        nxt = dt.date(year, month + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: dt.date) -> dt.date:
    """A closure falling on a weekend is observed on the adjacent weekday."""
    if day.weekday() == 5:  # Saturday -> preceding Friday
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:  # Sunday -> following Monday
        return day + dt.timedelta(days=1)
    return day


def nyse_holidays(year: int) -> dict[dt.date, str]:
    """Full NYSE closures for `year`, by rule. Half-days are not closures.

    New Year's Day carries the exchange's one exception to weekend observance:
    every other holiday landing on a Saturday moves to the preceding Friday,
    but a Saturday New Year's Day does not -- the market simply trades that
    December 31. Applying the general rule here would invent a closure (the
    next is 2028-12-31) and this tool would then read a perfectly good missing
    report as a holiday, which is the exact confusion it exists to remove.
    """
    days = {
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        easter(year) - dt.timedelta(days=2): "Good Friday",
        _last_weekday(year, 5, 0): "Memorial Day",
        _observed(dt.date(year, 6, 19)): "Juneteenth",
        _observed(dt.date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        _observed(dt.date(year, 12, 25)): "Christmas Day",
    }
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() != 5:
        days[_observed(new_year)] = "New Year's Day"
    return days


def is_trading_day(day: dt.date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in nyse_holidays(day.year)


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """Every session from `start` to `end`, both inclusive."""
    if end < start:
        return []
    out = []
    day = start
    while day <= end:
        if is_trading_day(day):
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def last_sessions(end: dt.date, count: int) -> list[dt.date]:
    """The `count` sessions ending at or before `end`, oldest first."""
    if count <= 0:
        return []
    out: list[dt.date] = []
    day = end
    # A run of closures never approaches this bound; it stops a bad date
    # argument from spinning forever.
    for _ in range(count * 10 + 400):
        if is_trading_day(day):
            out.append(day)
            if len(out) == count:
                break
        day -= dt.timedelta(days=1)
    return sorted(out)


# --------------------------------------------------------------------------
# Audit. Pure: what is on disk in, a verdict out.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    """One report file the scanner found."""

    day: dt.date
    chars: int

    @property
    def is_empty(self) -> bool:
        return self.chars < MIN_REPORT_CHARS


@dataclass
class Audit:
    sessions: list[dt.date] = field(default_factory=list)
    present: list[dt.date] = field(default_factory=list)
    missing: list[dt.date] = field(default_factory=list)
    empty: list[dt.date] = field(default_factory=list)
    unparseable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.empty

    @property
    def worst_run(self) -> int:
        """Longest unbroken run of sessions that left no usable record."""
        bad = set(self.missing) | set(self.empty)
        run = best = 0
        for day in self.sessions:
            run = run + 1 if day in bad else 0
            best = max(best, run)
        return best


def audit(sessions: list[dt.date], reports: list[Report], unparseable=()) -> Audit:
    by_day = {r.day: r for r in reports}
    result = Audit(sessions=list(sessions), unparseable=sorted(unparseable))
    for day in sessions:
        report = by_day.get(day)
        if report is None:
            result.missing.append(day)
        elif report.is_empty:
            result.empty.append(day)
        else:
            result.present.append(day)
    return result


# --------------------------------------------------------------------------
# Edge. Everything that touches the filesystem.
# --------------------------------------------------------------------------


def scan_reports(directory: pathlib.Path) -> tuple[list[Report], list[str]]:
    """Read the report directory. Returns (reports, unparseable filenames)."""
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        return [], []
    reports: list[Report] = []
    bad: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name == "README.md":
            continue
        match = _DATE_FILE.match(path.name)
        if not match:
            bad.append(path.name)
            continue
        try:
            day = dt.date(*(int(g) for g in match.groups()))
        except ValueError:
            # A well-shaped name that is not a real date -- say so, never guess.
            bad.append(path.name)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            bad.append(path.name)
            continue
        reports.append(Report(day=day, chars=len(text.strip())))
    return reports, bad


def render(result: Audit, directory: pathlib.Path) -> str:
    lines: list[str] = []
    span = ""
    if result.sessions:
        span = f" {result.sessions[0].isoformat()}..{result.sessions[-1].isoformat()}"
    lines.append(f"Desk report trail{span}  ({directory})")
    lines.append("")

    if not result.sessions:
        lines.append("  No trading sessions in that window -- nothing to check.")
        return "\n".join(lines)

    for day in result.sessions:
        if day in result.missing:
            mark, note = "MISSING", "no report written"
        elif day in result.empty:
            mark, note = "EMPTY  ", "file exists but carries no report"
        else:
            mark, note = "ok     ", ""
        lines.append(f"  {mark}  {day.isoformat()} {day:%a}  {note}".rstrip())

    lines.append("")
    lines.append(
        f"  {len(result.present)} of {len(result.sessions)} sessions on record"
        f" -- {len(result.missing)} missing, {len(result.empty)} empty"
    )
    if result.worst_run >= 2:
        lines.append(
            f"  Longest silent run: {result.worst_run} consecutive sessions."
            " The desk was not reporting and nothing said so."
        )
    if result.unparseable:
        lines.append(
            "  Ignored, filename is not a date: " + ", ".join(result.unparseable)
        )
    if result.ok:
        lines.append("  Every session on record.")
    return "\n".join(lines)


def cmd_check(args) -> int:
    directory = pathlib.Path(args.dir)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    if args.since:
        start = dt.date.fromisoformat(args.since)
        sessions = trading_days(start, today)
    else:
        sessions = last_sessions(today, args.last)

    reports, bad = scan_reports(directory)
    result = audit(sessions, reports, bad)
    print(render(result, directory))
    return 0 if result.ok else 1


def cmd_calendar(args) -> int:
    year = args.year or dt.date.today().year
    holidays = nyse_holidays(year)
    print(f"NYSE full closures {year} (derived from rules, not a table)")
    for day, name in sorted(holidays.items()):
        print(f"  {day.isoformat()} {day:%a}  {name}")
    print(f"\n  {len(trading_days(dt.date(year,1,1), dt.date(year,12,31)))} sessions")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="desk_watch",
        description="Name every trading session the spicy desk failed to report.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="which sessions left no report")
    p.add_argument("--dir", default=str(DEFAULT_REPORTS), help="report directory")
    p.add_argument("--last", type=int, default=10, help="how many sessions back")
    p.add_argument("--since", help="audit from this date instead (YYYY-MM-DD)")
    p.add_argument("--today", help="treat this as today (YYYY-MM-DD)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("calendar", help="the closures it derives for a year")
    p.add_argument("--year", type=int)
    p.set_defaults(func=cmd_calendar)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

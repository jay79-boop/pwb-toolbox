#!/usr/bin/env python3
"""The desk bridge: machine-local facts, redacted by schema, carried in git.

The desk's feeds are on the owner's Windows machine -- the paper book, the trade
journal, the report folder, the broker. A cloud session shares nothing with that
machine but GitHub, so until now ``tools/awareness.py`` listed the whole desk
under *not visible from here*, which is the state a blind spot is supposed to be
reported in rather than left in.

This module is the bridge, and it is the pattern the repository already trusts:
``tools/desk_agent/runs.jsonl`` is committed on purpose and pushed by the
launcher precisely so a cloud session can read facts it cannot reach. The desk
gets the same treatment.

**Two halves, and the split is the whole design.**

*The emitter* runs on the machine. It reads the real paths, derives a fixed set
of counts, ages and flags, and writes ``signals/desk.json``. Whoever pushes that
file -- the desk agent launcher already pushes after every run -- carries the
desk into GitHub's copy.

*The signal* is what a cloud session reads. It is deliberately not the desk.

**Redaction is enforced by schema, not by discipline.** This fork is public and
``spec_desk/*``, ``night_lab/*`` and the journal are gitignored for that reason.
So every field here is an integer, a float, a boolean, an ISO date, or a value
from a closed vocabulary -- and ``validate`` rejects anything else before a byte
is written. There is no free-text field to forget to scrub, which is a stronger
guarantee than remembering to leave the ticker out. ``tests/test_desk_signal.py``
plants tickers, prices and balances in every input and asserts none survive.

**Missing is not zero.** Every numeric field is ``None`` when the input could not
be read, and that reads as *unanswerable* downstream, never as *nothing wrong*.
Same refusal as ``planner_watch`` skipping a holding with no live price.

**A bridge that stops being written looks exactly like a calm desk**, which is
the failure the awareness layer exists to catch, so staleness is derived from the
trading calendar rather than a number picked out of the air: if a full trading
session has elapsed since the signal was written, the bridge -- not the desk --
is what stopped.

Usage::

    python tools/desk_signal.py emit          # on the machine; writes signals/desk.json
    python tools/desk_signal.py emit --dry-run
    python tools/desk_signal.py show          # what the committed signal says, and its age
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from dataclasses import dataclass, asdict, fields

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools import desk_watch  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SIGNAL_DIR = REPO_ROOT / "signals"
DEFAULT_SIGNAL = SIGNAL_DIR / "desk.json"

# The owner's machine. Spelled out rather than assumed: two checkouts exist and
# the OneDrive one is canonical, so the desk paths hang off the OneDrive root.
DEFAULT_JOURNAL_DIR = pathlib.Path.home() / "OneDrive" / "trade-journal"
DEFAULT_REPORTS = REPO_ROOT / "spec_desk" / "reports"

# How many trading sessions back the report audit looks. Two working weeks: long
# enough that a run of silence is visible, short enough that a fixed gap from a
# month ago stops being re-reported forever.
SESSIONS_AUDITED = 10

# The broker is tri-state on purpose. `unknown` is not a synonym for
# `disconnected` -- one says the desk is unplugged, the other says nobody looked,
# and reporting the second as the first is how a layer starts lying quietly.
BROKER_STATES = ("connected", "disconnected", "unknown")

# Every field, and the only shapes its value may take. This table *is* the
# redaction: a value that is not an int, float, bool, None, ISO date or a member
# of a named vocabulary cannot be written, so there is no path by which a ticker,
# a price, a balance or a filename reaches a public repository.
SCHEMA: dict[str, str] = {
    "taken": "stamp",
    "sessions_checked": "int",
    "sessions_missing": "int",
    "sessions_empty": "int",
    "worst_missing_run": "int",
    "last_report_day": "date",
    "paper_book_age_hours": "float",
    "paper_book_last_bar": "date",
    "paper_book_closed": "int",
    "paper_book_open": "int",
    "journal_age_hours": "float",
    "journal_export_present": "bool",
    "broker": "broker",
}


class Unpublishable(ValueError):
    """A value that would leak. Raised before anything is written."""


@dataclass(frozen=True)
class DeskSignal:
    """One redacted reading of the desk, true at one moment.

    ``None`` means *could not be read*. It never means zero, and nothing
    downstream is allowed to treat it as zero.
    """

    taken: str = ""
    sessions_checked: int | None = None
    sessions_missing: int | None = None
    sessions_empty: int | None = None
    worst_missing_run: int | None = None
    last_report_day: str = ""
    paper_book_age_hours: float | None = None
    paper_book_last_bar: str = ""
    paper_book_closed: int | None = None
    paper_book_open: int | None = None
    journal_age_hours: float | None = None
    journal_export_present: bool | None = None
    broker: str = "unknown"

    def as_dict(self) -> dict:
        payload = asdict(self)
        validate(payload)
        return payload

    @staticmethod
    def from_dict(raw: dict) -> "DeskSignal":
        known = {f.name for f in fields(DeskSignal)}
        return DeskSignal(**{k: v for k, v in raw.items() if k in known})


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_stamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate(payload: dict) -> dict:
    """Reject anything the schema does not allow, before it can be written.

    This is the guard that makes the signal safe to commit to a public fork. It
    refuses rather than scrubs: a value that does not fit is a bug in the
    emitter, and silently dropping it would hide that bug behind a file that
    looks fine.
    """
    unknown = sorted(set(payload) - set(SCHEMA))
    if unknown:
        raise Unpublishable(f"field(s) not in the schema: {', '.join(unknown)}")
    for key, kind in SCHEMA.items():
        value = payload.get(key)
        if kind == "stamp":
            if not _is_stamp(value):
                raise Unpublishable(f"{key} must be an ISO timestamp, got {value!r}")
        elif kind == "date":
            if value not in ("", None) and not _is_date(value):
                raise Unpublishable(
                    f"{key} must be an ISO date or empty, got {value!r}"
                )
        elif kind == "broker":
            if value not in BROKER_STATES:
                raise Unpublishable(
                    f"{key} must be one of {BROKER_STATES}, got {value!r}"
                )
        elif kind == "int":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise Unpublishable(f"{key} must be an int or null, got {value!r}")
        elif kind == "bool":
            if value is not None and not isinstance(value, bool):
                raise Unpublishable(f"{key} must be a bool or null, got {value!r}")
        elif kind == "float":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise Unpublishable(f"{key} must be a number or null, got {value!r}")
    return payload


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------


def sessions_missed(signal_day: dt.date, today: dt.date) -> int:
    """Trading sessions that fully elapsed since the signal was written.

    Days strictly between the two, so a session still open is never counted --
    "today has not finished" is not evidence that anything stopped. Weekends and
    NYSE holidays fall out of the calendar for free, which is why this is a rule
    and not a threshold in hours.
    """
    if signal_day >= today:
        return 0
    span = desk_watch.trading_days(signal_day + dt.timedelta(days=1), today)
    return len([d for d in span if d < today])


def reads_nothing(signal: DeskSignal) -> bool:
    """True when the emitter wrote a signal and every desk fact in it is missing.

    This is the failure the whole layer exists to catch, one level further in. A
    signal written on a machine where the paths are wrong -- a second checkout, a
    OneDrive folder that has not synced, a job registered without the credential
    that reaches the disk -- is perfectly fresh and completely empty, and reading
    a fresh stamp as a healthy desk is exactly the mistake. An emitter that read
    nothing is a broken emitter, not a quiet desk.
    """
    return (
        signal.sessions_checked is None
        and signal.paper_book_closed is None
        and signal.paper_book_open is None
        and not signal.paper_book_last_bar
        and signal.journal_age_hours is None
        and signal.journal_export_present is None
        and signal.broker == "unknown"
    )


def signal_age(signal: DeskSignal, now: dt.datetime) -> tuple[float | None, int]:
    """``(hours_old, sessions_missed)``. ``None`` when the stamp is unreadable."""
    try:
        taken = dt.datetime.fromisoformat(signal.taken.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None, 0
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=dt.timezone.utc)
    hours = (now - taken).total_seconds() / 3600.0
    return hours, sessions_missed(taken.date(), now.date())


def derive(
    audit: desk_watch.Audit | None,
    paper_book: dict | None,
    journal_age_hours: float | None,
    journal_export_present: bool | None,
    broker: str,
    now: dt.datetime,
) -> DeskSignal:
    """Everything the emitter read, reduced to publishable shape.

    Pure: the caller does the reading. Nothing here opens a file, so the whole
    reduction is testable against planted inputs with no filesystem at all.
    """
    if broker not in BROKER_STATES:
        broker = "unknown"

    last_report = ""
    checked = missing = empty = worst = None
    if audit is not None:
        checked = len(audit.sessions)
        missing = len(audit.missing)
        empty = len(audit.empty)
        worst = audit.worst_run
        if audit.present:
            last_report = max(audit.present).isoformat()

    closed = open_count = None
    last_bar = ""
    if isinstance(paper_book, dict):
        closed = _count(paper_book, ("closed", "closed_positions", "closed_trades"))
        open_count = _count(paper_book, ("open", "open_positions", "positions"))
        raw_bar = paper_book.get("last_bar")
        if _is_date(raw_bar):
            last_bar = str(raw_bar)

    signal = DeskSignal(
        taken=now.isoformat(),
        sessions_checked=checked,
        sessions_missing=missing,
        sessions_empty=empty,
        worst_missing_run=worst,
        last_report_day=last_report,
        paper_book_age_hours=_hours(paper_book, now),
        paper_book_last_bar=last_bar,
        paper_book_closed=closed,
        paper_book_open=open_count,
        journal_age_hours=journal_age_hours,
        journal_export_present=journal_export_present,
        broker=broker,
    )
    validate(asdict(signal))
    return signal


def _count(book: dict, keys: tuple[str, ...]) -> int | None:
    """Length of whichever list the paper book actually uses. Never a guess."""
    for key in keys:
        value = book.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _hours(book: dict | None, now: dt.datetime) -> float | None:
    if not isinstance(book, dict):
        return None
    for key in ("updated", "written", "last_written", "timestamp"):
        raw = book.get(key)
        if not isinstance(raw, str):
            continue
        try:
            stamp = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        return round((now - stamp).total_seconds() / 3600.0, 2)
    return None


# ---------------------------------------------------------------------------
# The dirty edge
# ---------------------------------------------------------------------------


def read_paper_book(path: pathlib.Path) -> dict | None:
    """The paper book, if it parses. Unreadable and malformed both give None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _age_hours(path: pathlib.Path, now: dt.datetime) -> float | None:
    try:
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    except OSError:
        return None
    return round((now - stamp).total_seconds() / 3600.0, 2)


def read_journal(journal_dir: pathlib.Path, now: dt.datetime):
    """``(age_hours, export_present)`` for the trade journal.

    The journal itself is never opened -- only its mtime and whether an exported
    register sits beside it. It is a personal document that this repository
    deliberately does not hold, and the signal has no business reading inside it.
    """
    page = journal_dir / "trade-journal.html"
    age = _age_hours(page, now) if page.exists() else None
    export = None
    if journal_dir.is_dir():
        export = any(
            p.suffix.lower() == ".json" and "journal" in p.name.lower()
            for p in journal_dir.iterdir()
            if p.is_file()
        )
    return age, export


def read_broker(heartbeat: pathlib.Path, now: dt.datetime) -> str:
    """Broker state from a heartbeat file the local IB side writes.

    Returns ``unknown`` when there is no heartbeat, which is the honest answer
    and not a synonym for disconnected. This is the one desk fact that could not
    be verified from a cloud session while it was built, so it is wired to refuse
    rather than to assume: no heartbeat, no claim.
    """
    age = _age_hours(heartbeat, now)
    if age is None:
        return "unknown"
    # A heartbeat older than a session is a file somebody left behind, not a
    # live connection. Same calendar rule as the signal's own staleness.
    try:
        stamp = dt.datetime.fromtimestamp(heartbeat.stat().st_mtime, dt.timezone.utc)
    except OSError:
        return "unknown"
    return "disconnected" if sessions_missed(stamp.date(), now.date()) else "connected"


def emit(
    now: dt.datetime,
    reports_dir: pathlib.Path = DEFAULT_REPORTS,
    journal_dir: pathlib.Path = DEFAULT_JOURNAL_DIR,
    paper_book: pathlib.Path | None = None,
    broker_heartbeat: pathlib.Path | None = None,
) -> DeskSignal:
    """Read the machine, reduce it, return the signal. Writes nothing."""
    reports, unparseable = desk_watch.scan_reports(reports_dir)
    audit = None
    if reports_dir.is_dir():
        sessions = desk_watch.last_sessions(now.date(), SESSIONS_AUDITED)
        audit = desk_watch.audit(sessions, reports, unparseable)

    book_path = paper_book or (journal_dir / "paper-book.json")
    book = read_paper_book(book_path)
    journal_age, export = read_journal(journal_dir, now)
    heartbeat = broker_heartbeat or (journal_dir / "ib-heartbeat.txt")
    return derive(audit, book, journal_age, export, read_broker(heartbeat, now), now)


def write_signal(signal: DeskSignal, path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(signal.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def load_signal(path: pathlib.Path) -> DeskSignal | None:
    """The committed signal, or None when there is not one to read."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        validate({k: v for k, v in raw.items() if k in SCHEMA})
    except Unpublishable:
        return None
    return DeskSignal.from_dict(raw)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")

    emit_cmd = sub.add_parser("emit", help="read this machine and write the signal")
    emit_cmd.add_argument("--out", default=str(DEFAULT_SIGNAL))
    emit_cmd.add_argument("--reports", default=str(DEFAULT_REPORTS))
    emit_cmd.add_argument("--journal-dir", default=str(DEFAULT_JOURNAL_DIR))
    emit_cmd.add_argument("--paper-book", default="")
    emit_cmd.add_argument("--broker-heartbeat", default="")
    emit_cmd.add_argument("--dry-run", action="store_true", help="print, do not write")

    show = sub.add_parser("show", help="what the committed signal says, and its age")
    show.add_argument("--signal", default=str(DEFAULT_SIGNAL))

    args = parser.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)

    if args.command == "show" or args.command is None:
        path = pathlib.Path(getattr(args, "signal", DEFAULT_SIGNAL))
        signal = load_signal(path)
        if signal is None:
            print(f"No readable desk signal at {path}.")
            print("The desk is not visible from here. Run `emit` on the machine.")
            return 1
        hours, missed = signal_age(signal, now)
        print(json.dumps(signal.as_dict(), indent=2, sort_keys=True))
        age = "unknown age" if hours is None else f"{hours:.1f}h old"
        print(f"\nSignal is {age}; {missed} trading session(s) elapsed since.")
        return 1 if missed else 0

    signal = emit(
        now,
        reports_dir=pathlib.Path(args.reports),
        journal_dir=pathlib.Path(args.journal_dir),
        paper_book=pathlib.Path(args.paper_book) if args.paper_book else None,
        broker_heartbeat=(
            pathlib.Path(args.broker_heartbeat) if args.broker_heartbeat else None
        ),
    )
    if args.dry_run:
        print(json.dumps(signal.as_dict(), indent=2, sort_keys=True))
        return 0
    out = write_signal(signal, pathlib.Path(args.out))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

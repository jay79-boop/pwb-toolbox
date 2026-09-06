"""Tests for tools/desk_signal.py and the desk adapter in tools/awareness.py.

The lab standard's matched pairs apply, and one pair here is doing unusual work.
The bridge carries machine-local desk facts into a **public** repository, so the
convict/acquit pair that matters most is not about a signal firing -- it is about
a ticker, a price or a balance reaching git. ``test_nothing_a_paper_book_holds
_survives_into_the_signal`` plants all three and the schema must drop them.

The second thing pinned here is that *missing is not zero*. A desk fact that
could not be read must never fire a rule built for a fact that was read and came
back empty, because that is how a layer starts reporting a blind spot as a
finding.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from tools import awareness as aw
from tools import desk_signal as ds
from tools import desk_watch

# Wednesday. The Monday before is 08-31, the Friday before that is 08-28.
NOW = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def signal(**kw) -> ds.DeskSignal:
    base = dict(taken=NOW.isoformat(), broker="unknown")
    base.update(kw)
    return ds.DeskSignal(**base)


def audit_of(missing=(), present=(), empty=()):
    sessions = sorted({*missing, *present, *empty})
    result = desk_watch.Audit(sessions=sessions)
    result.missing = list(missing)
    result.present = list(present)
    result.empty = list(empty)
    return result


# ---------------------------------------------------------------------------
# Redaction: the reason this file can be committed at all
# ---------------------------------------------------------------------------


def test_nothing_a_paper_book_holds_survives_into_the_signal():
    """Plant a ticker, a price and a balance. None may reach the emitted JSON."""
    book = {
        "updated": (NOW - dt.timedelta(hours=3)).isoformat(),
        "last_bar": "2026-09-01",
        "closed": [
            {"symbol": "NVDA", "entry": 178.42, "exit": 191.10, "r": 1.8},
            {"symbol": "BTC-USD", "entry": 61200.0, "exit": 59110.0, "r": -1.0},
        ],
        "open": [{"symbol": "TSLA", "entry": 244.01}],
        "account_balance": 51234.99,
        "broker_account": "U1234567",
    }
    emitted = json.dumps(ds.derive(None, book, None, None, "unknown", NOW).as_dict())
    for leak in ("NVDA", "BTC-USD", "TSLA", "178.42", "61200", "51234", "U1234567"):
        assert leak not in emitted, f"{leak} reached the signal"
    # And the publishable reduction is still there: two closed, one open.
    assert json.loads(emitted)["paper_book_closed"] == 2
    assert json.loads(emitted)["paper_book_open"] == 1


def test_a_field_outside_the_schema_is_refused_rather_than_scrubbed():
    with pytest.raises(ds.Unpublishable):
        ds.validate({"taken": NOW.isoformat(), "ticker": "NVDA", "broker": "unknown"})


def test_free_text_in_a_schema_field_is_refused():
    payload = signal().as_dict()
    payload["broker"] = "connected to IBKR account U1234567"
    with pytest.raises(ds.Unpublishable):
        ds.validate(payload)


def test_every_emitted_value_is_a_number_a_bool_a_date_or_a_known_word():
    payload = ds.derive(
        audit_of(present=[dt.date(2026, 9, 1)]),
        {"updated": NOW.isoformat(), "last_bar": "2026-09-01", "closed": []},
        336.0,
        False,
        "connected",
        NOW,
    ).as_dict()
    for key, value in payload.items():
        assert value is None or isinstance(
            value, (int, float, bool, str)
        ), f"{key} is {type(value)}"
        if isinstance(value, str):
            assert (
                ds._is_stamp(value)
                or ds._is_date(value)
                or value in ds.BROKER_STATES
                or value == ""
            ), f"{key} carries free text: {value!r}"


# ---------------------------------------------------------------------------
# Staleness comes from the calendar: convict and acquit
# ---------------------------------------------------------------------------


def test_a_weekend_is_not_a_missed_session():
    """Friday's signal read on Monday. Nothing stopped; nothing may be claimed."""
    assert ds.sessions_missed(dt.date(2026, 8, 28), dt.date(2026, 8, 31)) == 0


def test_a_skipped_trading_day_is_a_missed_session():
    """Friday's signal read on Tuesday. Monday elapsed and nobody wrote."""
    assert ds.sessions_missed(dt.date(2026, 8, 28), dt.date(2026, 9, 1)) == 1


def test_today_is_never_counted_because_it_has_not_finished():
    assert ds.sessions_missed(dt.date(2026, 9, 1), dt.date(2026, 9, 2)) == 0


def test_a_stale_bridge_is_convicted_as_the_thing_that_stopped():
    """Friday's signal read on Wednesday, carrying a fact so it is stale and not empty."""
    stale = signal(
        taken=dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.timezone.utc).isoformat(),
        sessions_checked=10,
    )
    bridge = [o for o in aw.observe_desk(stale, NOW) if o.entity == "desk:bridge"][0]
    assert bridge.severity == "act" and bridge.trigger == "stopped"
    assert "not been written" in bridge.summary


def test_a_current_bridge_is_acquitted():
    bridge = [
        o
        for o in aw.observe_desk(signal(sessions_checked=10), NOW)
        if o.entity == "desk:bridge"
    ][0]
    assert bridge.severity == "info" and not bridge.trigger


def test_no_signal_at_all_produces_no_observation_rather_than_a_calm_one():
    """The absence is a blind spot for `collect` to name, never a clean bill."""
    assert aw.observe_desk(None, NOW) == []


# ---------------------------------------------------------------------------
# Reports, the paper book, and the journal gap
# ---------------------------------------------------------------------------


def test_sessions_with_no_report_are_convicted():
    obs = aw.observe_desk(
        signal(sessions_checked=10, sessions_missing=3, worst_missing_run=2), NOW
    )
    reports = [o for o in obs if o.entity == "desk:reports"][0]
    assert reports.severity == "act" and reports.trigger == "stopped"


def test_a_complete_run_of_reports_is_acquitted():
    obs = aw.observe_desk(
        signal(sessions_checked=10, sessions_missing=0, sessions_empty=0), NOW
    )
    reports = [o for o in obs if o.entity == "desk:reports"][0]
    assert reports.severity == "info" and not reports.trigger


def test_closed_trades_with_no_export_route_are_a_blocking_decision():
    """The 2026-09-01 finding: a live register nothing is pointed at."""
    obs = aw.observe_desk(
        signal(paper_book_closed=18, journal_export_present=False), NOW
    )
    gap = [o for o in obs if o.entity == "desk:journal-gap"][0]
    assert gap.trigger == "blocking" and gap.severity == "act"


def test_closed_trades_with_an_export_route_are_not_a_blocker():
    obs = aw.observe_desk(
        signal(paper_book_closed=18, journal_export_present=True), NOW
    )
    assert not [o for o in obs if o.entity == "desk:journal-gap"]


def test_an_unreadable_export_check_is_not_treated_as_a_missing_export():
    """None is 'could not be read'. It must not fire the rule that False fires."""
    obs = aw.observe_desk(
        signal(paper_book_closed=18, journal_export_present=None), NOW
    )
    assert not [o for o in obs if o.entity == "desk:journal-gap"]


def test_a_paper_book_behind_the_calendar_is_flagged():
    obs = aw.observe_desk(signal(paper_book_last_bar="2026-08-28"), NOW)
    behind = [o for o in obs if o.entity == "desk:paper-book"][0]
    assert behind.severity == "watch"
    assert dict(behind.metrics)["sessions_behind"] == 2  # 08-31 and 09-01


def test_a_paper_book_up_to_date_is_not_flagged():
    obs = aw.observe_desk(signal(paper_book_last_bar="2026-09-01"), NOW)
    assert not [o for o in obs if o.entity == "desk:paper-book"]


def test_the_journal_age_is_reported_and_never_judged():
    """No elapsed-time rule can tell a quiet fortnight from a broken one."""
    obs = aw.observe_desk(signal(journal_age_hours=336.0), NOW)
    journal = [o for o in obs if o.entity == "desk:journal"][0]
    assert journal.severity == "info" and not journal.trigger


# ---------------------------------------------------------------------------
# The broker: the one fact that could not be verified while it was built
# ---------------------------------------------------------------------------


def test_an_unknown_broker_says_nothing_at_all():
    assert not [
        o
        for o in aw.observe_desk(signal(broker="unknown"), NOW)
        if o.entity == "desk:broker"
    ]


def test_a_stale_broker_heartbeat_is_convicted():
    obs = aw.observe_desk(signal(broker="disconnected"), NOW)
    broker = [o for o in obs if o.entity == "desk:broker"][0]
    assert broker.severity == "act" and broker.trigger == "stopped"


def test_a_live_broker_heartbeat_is_acquitted():
    obs = aw.observe_desk(signal(broker="connected"), NOW)
    broker = [o for o in obs if o.entity == "desk:broker"][0]
    assert broker.severity == "info" and not broker.trigger


def test_a_heartbeat_that_does_not_exist_reads_unknown_not_disconnected(tmp_path):
    assert ds.read_broker(tmp_path / "nope.txt", NOW) == "unknown"


# ---------------------------------------------------------------------------
# The edge
# ---------------------------------------------------------------------------


def test_an_unparseable_paper_book_is_dropped_rather_than_repaired(tmp_path):
    bad = tmp_path / "paper-book.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ds.read_paper_book(bad) is None


def test_a_signal_round_trips_through_disk(tmp_path):
    original = signal(sessions_checked=10, sessions_missing=1, broker="connected")
    path = ds.write_signal(original, tmp_path / "desk.json")
    assert ds.load_signal(path) == original


def test_a_signal_carrying_an_illegal_value_is_refused_on_read(tmp_path):
    path = tmp_path / "desk.json"
    path.write_text(
        json.dumps({"taken": NOW.isoformat(), "broker": "U1234567"}), encoding="utf-8"
    )
    assert ds.load_signal(path) is None


def test_emit_reads_a_real_tree_without_touching_the_network(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-09-01.md").write_text("x" * 400, encoding="utf-8")
    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "paper-book.json").write_text(
        json.dumps({"last_bar": "2026-09-01", "closed": [1, 2], "open": []}),
        encoding="utf-8",
    )
    out = ds.emit(NOW, reports_dir=reports, journal_dir=journal)
    assert out.paper_book_closed == 2
    assert out.paper_book_last_bar == "2026-09-01"
    assert out.broker == "unknown"
    assert out.sessions_checked == ds.SESSIONS_AUDITED
    ds.validate(out.as_dict())


# ---------------------------------------------------------------------------
# The fresh-and-empty case: found by running the emitter, not by reading it
# ---------------------------------------------------------------------------


def test_a_signal_that_read_nothing_is_convicted_not_called_current():
    """A perfectly fresh signal with every field empty is a broken emitter.

    Found by running `emit` in a checkout with no desk in it: the bridge reported
    "the desk signal is current" and nothing else, which is the exact reading
    this layer exists to prevent -- an emitter pointed at the wrong machine looks
    identical to a desk with nothing wrong.
    """
    bridge = [o for o in aw.observe_desk(signal(), NOW) if o.entity == "desk:bridge"][0]
    assert bridge.severity == "act" and bridge.trigger == "stopped"
    assert "read nothing" in bridge.summary


def test_one_real_fact_is_enough_to_acquit_the_emitter():
    """The acquit half. A desk with a quiet week still reads *something*."""
    quiet = signal(sessions_checked=10, sessions_missing=0)
    bridge = [o for o in aw.observe_desk(quiet, NOW) if o.entity == "desk:bridge"][0]
    assert bridge.severity == "info" and not bridge.trigger
    assert not ds.reads_nothing(quiet)


def test_a_stale_and_empty_signal_is_reported_as_empty_first():
    """Both are true; the emitter is the one worth fixing, so it is the one said."""
    old = signal(taken=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc).isoformat())
    bridge = [o for o in aw.observe_desk(old, NOW) if o.entity == "desk:bridge"][0]
    assert "read nothing" in bridge.summary

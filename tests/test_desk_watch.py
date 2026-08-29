"""Tests for tools/desk_watch.py.

The lab standard's matched pairs apply here as much as to a scoring lab: a
watchdog that fires on every quiet day is exactly as useless as one that never
fires. Each convict test below has an acquit twin.

The real failure this tool was built from is pinned in
``test_convicts_the_real_august_gap``: 2026-08-25 wrote no file, 08-26 and
08-27 produced no output at all, and nothing said so for four days.
"""

from __future__ import annotations

import datetime as dt

from tools import desk_watch as dw

# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


def test_easter_matches_known_dates():
    # Independently checkable; Good Friday hangs off these.
    assert dw.easter(2026) == dt.date(2026, 4, 5)
    assert dw.easter(2027) == dt.date(2027, 3, 28)
    assert dw.easter(2024) == dt.date(2024, 3, 31)


def test_2026_closures_match_the_real_nyse_calendar():
    got = dw.nyse_holidays(2026)
    assert set(got) == {
        dt.date(2026, 1, 1),  # New Year's Day, a Thursday
        dt.date(2026, 1, 19),  # MLK, 3rd Monday
        dt.date(2026, 2, 16),  # Washington's Birthday, 3rd Monday
        dt.date(2026, 4, 3),  # Good Friday
        dt.date(2026, 5, 25),  # Memorial, last Monday
        dt.date(2026, 6, 19),  # Juneteenth, a Friday
        dt.date(2026, 7, 3),  # July 4 is a Saturday -> observed Friday
        dt.date(2026, 9, 7),  # Labor, 1st Monday
        dt.date(2026, 11, 26),  # Thanksgiving, 4th Thursday
        dt.date(2026, 12, 25),  # Christmas, a Friday
    }


def test_weekend_observance_shifts_both_ways():
    # July 4 2021 fell on a Sunday -> observed Monday the 5th.
    assert dt.date(2021, 7, 5) in dw.nyse_holidays(2021)
    # Christmas 2027 falls on a Saturday -> observed Friday the 24th.
    assert dt.date(2027, 12, 24) in dw.nyse_holidays(2027)


def test_a_saturday_new_year_is_the_one_holiday_that_does_not_shift():
    """The exchange's own exception. Every other Saturday holiday moves to the
    preceding Friday; New Year's does not, and the market trades Dec 31."""
    assert dt.date(2022, 1, 1).weekday() == 5  # Saturday
    assert dt.date(2021, 12, 31) not in dw.nyse_holidays(2022)
    assert dw.is_trading_day(dt.date(2021, 12, 31))
    # 2028-01-01 is also a Saturday -- the next occurrence.
    assert dt.date(2027, 12, 31) not in dw.nyse_holidays(2028)
    # A Sunday New Year's still shifts forward to the Monday.
    assert dt.date(2023, 1, 2) in dw.nyse_holidays(2023)
    # And a weekday one simply stands.
    assert dt.date(2027, 1, 1) in dw.nyse_holidays(2027)


def test_trading_days_skips_weekends_and_closures():
    # Thanksgiving week 2026: Thu 26th closed, Fri 27th open (half day, still
    # a session), weekend excluded.
    days = dw.trading_days(dt.date(2026, 11, 23), dt.date(2026, 11, 29))
    assert days == [
        dt.date(2026, 11, 23),
        dt.date(2026, 11, 24),
        dt.date(2026, 11, 25),
        dt.date(2026, 11, 27),
    ]


def test_last_sessions_counts_backwards_over_a_holiday():
    # Inclusive of `end` when `end` is itself a session. Walking back from Fri
    # 2026-07-10 crosses the observed July 3 closure and the weekend.
    got = dw.last_sessions(dt.date(2026, 7, 10), 6)
    assert got == [
        dt.date(2026, 7, 2),
        dt.date(2026, 7, 6),
        dt.date(2026, 7, 7),
        dt.date(2026, 7, 8),
        dt.date(2026, 7, 9),
        dt.date(2026, 7, 10),
    ]


def test_last_sessions_from_a_closed_day_returns_only_open_ones():
    # Asked from a Sunday, it must not invent a session.
    got = dw.last_sessions(dt.date(2026, 8, 30), 3)
    assert got == [
        dt.date(2026, 8, 26),
        dt.date(2026, 8, 27),
        dt.date(2026, 8, 28),
    ]


def test_last_sessions_is_empty_for_a_nonpositive_count():
    assert dw.last_sessions(dt.date(2026, 8, 28), 0) == []


# --------------------------------------------------------------------------
# Audit -- convict and acquit
# --------------------------------------------------------------------------


def _full(day: dt.date) -> dw.Report:
    return dw.Report(day=day, chars=dw.MIN_REPORT_CHARS + 1)


def test_convicts_a_planted_gap():
    sessions = dw.trading_days(dt.date(2026, 8, 24), dt.date(2026, 8, 28))
    reports = [_full(d) for d in sessions if d != dt.date(2026, 8, 26)]
    result = dw.audit(sessions, reports)
    assert result.missing == [dt.date(2026, 8, 26)]
    assert not result.ok


def test_acquits_a_complete_trail():
    """The twin: nothing is wrong, so it must stay silent."""
    sessions = dw.trading_days(dt.date(2026, 8, 24), dt.date(2026, 8, 28))
    result = dw.audit(sessions, [_full(d) for d in sessions])
    assert result.missing == []
    assert result.empty == []
    assert result.ok
    assert result.worst_run == 0


def test_acquits_a_holiday_with_no_report():
    """A closed market is not a failure to report -- the trap for a naive
    'one file per weekday' check, which would convict every holiday."""
    sessions = dw.trading_days(dt.date(2026, 6, 17), dt.date(2026, 6, 23))
    assert dt.date(2026, 6, 19) not in sessions  # Juneteenth
    result = dw.audit(sessions, [_full(d) for d in sessions])
    assert result.ok


def test_acquits_weekends():
    sessions = dw.trading_days(dt.date(2026, 8, 28), dt.date(2026, 8, 31))
    assert sessions == [dt.date(2026, 8, 28), dt.date(2026, 8, 31)]
    assert dw.audit(sessions, [_full(d) for d in sessions]).ok


def test_convicts_a_file_that_exists_but_is_empty():
    """The wrapper touching a file must not read as a report."""
    day = dt.date(2026, 8, 28)
    result = dw.audit([day], [dw.Report(day=day, chars=12)])
    assert result.empty == [day]
    assert result.missing == []
    assert not result.ok


def test_convicts_the_real_august_gap():
    """08-25 wrote no file; 08-26 and 08-27 produced no output at all."""
    sessions = dw.trading_days(dt.date(2026, 8, 21), dt.date(2026, 8, 28))
    present = [dt.date(2026, 8, 21), dt.date(2026, 8, 24), dt.date(2026, 8, 28)]
    result = dw.audit(sessions, [_full(d) for d in present])
    assert result.missing == [
        dt.date(2026, 8, 25),
        dt.date(2026, 8, 26),
        dt.date(2026, 8, 27),
    ]
    assert result.worst_run == 3


def test_worst_run_counts_consecutive_sessions_not_calendar_days():
    """Fri and the following Mon are consecutive sessions, weekend between."""
    sessions = dw.trading_days(dt.date(2026, 8, 26), dt.date(2026, 8, 31))
    present = [dt.date(2026, 8, 26)]
    result = dw.audit(sessions, [_full(d) for d in present])
    assert result.missing == [
        dt.date(2026, 8, 27),
        dt.date(2026, 8, 28),
        dt.date(2026, 8, 31),
    ]
    assert result.worst_run == 3


def test_an_empty_window_is_not_a_failure():
    result = dw.audit([], [])
    assert result.ok
    assert result.worst_run == 0


# --------------------------------------------------------------------------
# Edge -- refuse rather than repair
# --------------------------------------------------------------------------


def test_scan_reads_dates_and_sizes(tmp_path):
    (tmp_path / "2026-08-28.md").write_text("x" * 500, encoding="utf-8")
    (tmp_path / "2026-08-27.md").write_text("  ", encoding="utf-8")
    reports, bad = dw.scan_reports(tmp_path)
    by_day = {r.day: r for r in reports}
    assert by_day[dt.date(2026, 8, 28)].chars == 500
    assert by_day[dt.date(2026, 8, 27)].is_empty
    assert bad == []


def test_scan_refuses_to_guess_an_unparseable_name(tmp_path):
    (tmp_path / "2026-08-28.md").write_text("x" * 500, encoding="utf-8")
    (tmp_path / "notes.md").write_text("x" * 500, encoding="utf-8")
    (tmp_path / "2026-13-45.md").write_text("x" * 500, encoding="utf-8")
    reports, bad = dw.scan_reports(tmp_path)
    assert [r.day for r in reports] == [dt.date(2026, 8, 28)]
    assert sorted(bad) == ["2026-13-45.md", "notes.md"]


def test_scan_ignores_the_readme_that_keeps_the_dir_in_git(tmp_path):
    (tmp_path / "README.md").write_text("why this dir exists", encoding="utf-8")
    reports, bad = dw.scan_reports(tmp_path)
    assert reports == [] and bad == []


def test_scan_of_a_missing_directory_is_empty_not_an_error(tmp_path):
    reports, bad = dw.scan_reports(tmp_path / "nope")
    assert reports == [] and bad == []


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_check_exits_nonzero_on_a_gap_and_zero_when_clean(tmp_path, capsys):
    for day in ("2026-08-26", "2026-08-27"):
        (tmp_path / f"{day}.md").write_text("x" * 500, encoding="utf-8")

    rc = dw.main(
        ["check", "--dir", str(tmp_path), "--today", "2026-08-28", "--last", "3"]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISSING" in out and "2026-08-28" in out

    (tmp_path / "2026-08-28.md").write_text("x" * 500, encoding="utf-8")
    rc = dw.main(
        ["check", "--dir", str(tmp_path), "--today", "2026-08-28", "--last", "3"]
    )
    assert rc == 0
    assert "Every session on record." in capsys.readouterr().out


def test_check_since_reports_the_silent_run(tmp_path, capsys):
    (tmp_path / "2026-08-24.md").write_text("x" * 500, encoding="utf-8")
    rc = dw.main(
        [
            "check",
            "--dir",
            str(tmp_path),
            "--since",
            "2026-08-24",
            "--today",
            "2026-08-27",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "Longest silent run: 3" in out


def test_calendar_command_runs(capsys):
    assert dw.main(["calendar", "--year", "2026"]) == 0
    assert "Good Friday" in capsys.readouterr().out


def test_check_on_an_empty_window_is_clean(tmp_path, capsys):
    rc = dw.main(
        [
            "check",
            "--dir",
            str(tmp_path),
            "--since",
            "2026-12-26",
            "--today",
            "2026-12-27",
        ]
    )
    assert rc == 0
    assert "nothing to check" in capsys.readouterr().out

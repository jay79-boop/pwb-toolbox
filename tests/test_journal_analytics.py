import datetime as dt

import pytest

from pwb_toolbox.journal import (
    RoundTrip,
    by_dte_bucket,
    by_entry_hour,
    exit_census,
    hold_time_summary,
    summary,
    wash_sale_candidates,
)


def trip(
    underlying="AAPL",
    open_day=1,
    close_day=10,
    open_price=9.0,
    close_price=12.0,
    exit_reason="closed",
    dte=40,
    hour=None,
    qty=1,
):
    open_date = dt.date(2026, 8, open_day)
    return RoundTrip(
        underlying=underlying,
        expiry=open_date + dt.timedelta(days=dte),
        strike=230.0,
        kind="call",
        quantity=qty,
        open_date=open_date,
        open_time=dt.time(hour, 30) if hour else None,
        open_price=open_price,
        close_date=dt.date(2026, 8, close_day),
        close_price=close_price,
        exit_reason=exit_reason,
    )


def test_exit_census_counts_how_positions_ended():
    trips = [
        trip(),
        trip(exit_reason="expired", close_price=0.0),
        trip(exit_reason="expired", close_price=0.0),
    ]
    assert exit_census(trips) == {"closed": 1, "expired": 2}


def test_summary_headline_numbers():
    trips = [
        trip(open_price=9.0, close_price=12.0),  # +300
        trip(open_price=9.0, close_price=6.0),  # -300
        trip(open_price=2.0, close_price=0.0, exit_reason="expired"),  # -200
    ]
    s = summary(trips)
    assert s["trades"] == 3
    assert s["win_rate"] == pytest.approx(100 / 3)
    assert s["total_pnl"] == pytest.approx(-200.0)
    assert s["expired_worthless"] == 1
    assert s["profit_factor"] == pytest.approx(300 / 500)


def test_summary_of_an_empty_log():
    assert summary([]) == {"trades": 0}


def test_dte_buckets_separate_weeklies_from_monthlies():
    trips = [
        trip(dte=5, open_price=2.0, close_price=0.0, exit_reason="expired"),
        trip(dte=6, open_price=2.0, close_price=0.0, exit_reason="expired"),
        trip(dte=38, open_price=9.0, close_price=12.0),
    ]
    buckets = {b.label: b for b in by_dte_bucket(trips)}
    assert buckets["0-7"].trades == 2
    assert buckets["0-7"].win_rate == 0.0
    assert buckets["22-45"].win_rate == 100.0


def test_entry_hour_is_empty_without_execution_times():
    assert by_entry_hour([trip(), trip()]) == []


def test_entry_hour_groups_by_session_window():
    trips = [trip(hour=9), trip(hour=15), trip(hour=15, close_price=6.0)]
    buckets = {b.label: b for b in by_entry_hour(trips)}
    assert buckets["09:30-10:00 open"].trades == 1
    assert buckets["15:00-16:00 close"].trades == 2
    assert buckets["15:00-16:00 close"].win_rate == 50.0


def test_hold_time_splits_winners_from_losers():
    trips = [
        trip(open_day=1, close_day=5, close_price=12.0),  # win, 4 days
        trip(open_day=1, close_day=21, close_price=4.0),  # loss, 20 days
    ]
    h = hold_time_summary(trips)
    assert h["avg_hold_winners"] == 4
    assert h["avg_hold_losers"] == 20
    assert h["max_hold"] == 20


def test_bucket_handles_zero_trades():
    from pwb_toolbox.journal import Bucket

    b = Bucket(label="empty", trades=0, wins=0, total_pnl=0.0)
    assert b.win_rate == 0.0
    assert b.avg_pnl == 0.0


def test_wash_sale_flags_a_reentry_inside_the_window():
    trips = [
        trip(open_day=1, close_day=5, close_price=4.0),  # loss closed Aug 5
        trip(open_day=9, close_day=20, close_price=12.0),  # reopened Aug 9
    ]
    hits = wash_sale_candidates(trips)
    assert len(hits) == 1
    assert hits[0][0] == "AAPL"
    assert hits[0][2] == dt.date(2026, 8, 9)


def test_wash_sale_ignores_reentry_outside_the_window():
    trips = [
        trip(open_day=1, close_day=2, close_price=4.0),
        trip(open_day=1, close_day=20, close_price=12.0),
    ]
    assert wash_sale_candidates(trips, window_days=5) == []


def test_wash_sale_does_not_cross_underlyings():
    trips = [
        trip(underlying="AAPL", open_day=1, close_day=5, close_price=4.0),
        trip(underlying="TSLA", open_day=6, close_day=20, close_price=12.0),
    ]
    assert wash_sale_candidates(trips) == []

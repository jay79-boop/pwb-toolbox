import datetime as dt

import pytest

from pwb_toolbox.journal import ParseError, load, pair_round_trips, parse_fills

HEADER = (
    '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"'
)


def row(date, action, symbol, qty, price, fees="$0.65"):
    return f'"{date}","{action}","{symbol}","desc","{qty}","{price}","{fees}","$0.00"'


def export(*rows):
    return "\n".join([HEADER, *rows])


AAPL = "AAPL 09/18/2026 230.00 C"
TSLA = "TSLA 08/21/2026 360.00 C"


def test_parses_a_simple_option_fill():
    fills = parse_fills(export(row("08/07/2026", "Buy to Open", AAPL, 1, "$9.40")))
    assert len(fills) == 1
    f = fills[0]
    assert f.underlying == "AAPL"
    assert f.expiry == dt.date(2026, 9, 18)
    assert f.strike == 230.0
    assert f.kind == "call"
    assert f.quantity == 1
    assert f.price == 9.40
    assert f.time is None


def test_parses_put_and_execution_time():
    fills = parse_fills(
        export(
            row("08/07/2026 10:32", "Buy to Open", "SPY 12/19/2026 500 P", 2, "$4.10")
        )
    )
    assert fills[0].kind == "put"
    assert fills[0].time == dt.time(10, 32)
    assert fills[0].quantity == 2


def test_ignores_non_option_rows():
    fills = parse_fills(
        export(
            row("08/07/2026", "Buy", "AAPL", 10, "$232.00"),
            '"08/01/2026","Cash Dividend","MSFT","desc","","","","$12.00"',
            row("08/07/2026", "Buy to Open", AAPL, 1, "$9.40"),
        )
    )
    assert len(fills) == 1


def test_tolerates_as_of_dates_and_a_title_line():
    text = "Transactions for account XXXX\n" + export(
        row("08/07/2026 as of 08/06/2026", "Buy to Open", AAPL, 1, "$9.40")
    )
    assert parse_fills(text)[0].date == dt.date(2026, 8, 7)


def test_unknown_action_raises_rather_than_skipping():
    with pytest.raises(ParseError, match="unrecognized action"):
        parse_fills(export(row("08/07/2026", "Journaled Shares", AAPL, 1, "$9.40")))


def test_bad_money_value_raises():
    with pytest.raises(ParseError, match="money value"):
        parse_fills(export(row("08/07/2026", "Buy to Open", AAPL, 1, "N/A")))


def test_missing_header_raises():
    with pytest.raises(ParseError, match="no header row"):
        parse_fills("just some text\nwith no columns")


def test_zero_quantity_raises():
    with pytest.raises(ParseError, match="zero quantity"):
        parse_fills(export(row("08/07/2026", "Buy to Open", AAPL, 0, "$9.40")))


def test_round_trip_pairs_open_and_close():
    trips, unmatched = load(
        export(
            row("08/07/2026", "Buy to Open", AAPL, 1, "$9.40"),
            row("08/20/2026", "Sell to Close", AAPL, 1, "$12.10"),
        )
    )
    assert not unmatched
    (t,) = trips
    assert t.exit_reason == "closed"
    assert t.hold_days == 13
    assert t.dte_at_entry == 42
    assert t.pnl == pytest.approx((12.10 - 9.40) * 100 - 1.30)
    assert t.won


def test_expiry_closes_at_zero_and_is_labelled():
    trips, _ = load(
        export(
            row("08/07/2026", "Buy to Open", TSLA, 4, "$2.10"),
            row("08/21/2026", "Expired", TSLA, 4, "$0.00", fees="$0.00"),
        )
    )
    (t,) = trips
    assert t.exit_reason == "expired"
    assert t.close_price == 0.0
    assert t.pnl == pytest.approx(-2.10 * 100 * 4 - 0.65)
    assert not t.won


def test_partial_close_splits_into_two_round_trips():
    """The scale-out ladder produces exactly this shape."""
    trips, unmatched = load(
        export(
            row("08/07/2026", "Buy to Open", AAPL, 2, "$9.40"),
            row("08/12/2026", "Sell to Close", AAPL, 1, "$14.10"),
            row("08/25/2026", "Sell to Close", AAPL, 1, "$9.40"),
        )
    )
    assert not unmatched
    assert len(trips) == 2
    assert [t.quantity for t in trips] == [1, 1]
    assert trips[0].close_date == dt.date(2026, 8, 12)
    assert trips[1].close_date == dt.date(2026, 8, 25)


def test_still_open_position_is_reported_unmatched():
    trips, unmatched = load(export(row("08/07/2026", "Buy to Open", AAPL, 1, "$9.40")))
    assert trips == []
    assert len(unmatched) == 1


def test_close_without_a_matching_open_is_reported_unmatched():
    trips, unmatched = load(
        export(row("08/20/2026", "Sell to Close", AAPL, 1, "$12.10"))
    )
    assert trips == []
    assert len(unmatched) == 1


def test_different_strikes_do_not_cross_match():
    other = "AAPL 09/18/2026 240.00 C"
    trips, unmatched = load(
        export(
            row("08/07/2026", "Buy to Open", AAPL, 1, "$9.40"),
            row("08/20/2026", "Sell to Close", other, 1, "$3.10"),
        )
    )
    assert trips == []
    assert len(unmatched) == 2


def test_fills_are_paired_fifo_by_date():
    trips, _ = load(
        export(
            row("08/03/2026", "Buy to Open", AAPL, 1, "$8.00"),
            row("08/07/2026", "Buy to Open", AAPL, 1, "$9.40"),
            row("08/20/2026", "Sell to Close", AAPL, 1, "$12.10"),
        )
    )
    assert len(trips) == 1
    assert trips[0].open_price == 8.00  # the earlier lot closed first

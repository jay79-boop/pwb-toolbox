"""Parsing the way a trade plan names an option.

`docs/spec-desk.md` fixes the plan format as something a person reads and
approves; brokers want fields. These tests pin the translation both ways, and
pin the refusals — because the failure that matters here is not a crash, it is
a share position quietly becoming an option with an invented strike.
"""

import datetime as dt

import pytest

from pwb_toolbox.execution.option_contract import (
    OptionContract,
    ParseError,
    parse_option_instrument,
)

# --- the plan format ------------------------------------------------------


def test_plan_format_from_the_spec_desk_doc():
    """The exact string used as the worked example in docs/spec-desk.md."""

    c = parse_option_instrument("NVDA 02OCT26 190C")
    assert c.underlying == "NVDA"
    assert c.expiry == dt.date(2026, 10, 2)
    assert c.strike == pytest.approx(190.0)
    assert c.right == "C"


@pytest.mark.parametrize(
    "text",
    [
        "NVDA 02OCT26 190C",
        "nvda 02oct26 190c",
        "  NVDA   02OCT26   190 C  ",
        "NVDA 02OCT26 $190C",
        "NVDA 2OCT26 190C",
    ],
)
def test_plan_format_tolerates_how_people_actually_type_it(text):
    c = parse_option_instrument(text)
    assert (c.underlying, c.expiry, c.strike, c.right) == (
        "NVDA",
        dt.date(2026, 10, 2),
        190.0,
        "C",
    )


def test_puts_parse_and_keep_their_right():
    c = parse_option_instrument("SPX 19DEC26 6000P")
    assert c.right == "P"
    assert c.strike == pytest.approx(6000.0)


def test_fractional_strikes_survive():
    c = parse_option_instrument("XSP 30SEP26 668.5P")
    assert c.strike == pytest.approx(668.5)
    assert c.occ_symbol == "XSP   260930P00668500"


# --- the OCC format -------------------------------------------------------


def test_occ_symbol_parses():
    c = parse_option_instrument("NVDA  261002C00190000")
    assert (c.underlying, c.expiry, c.strike, c.right) == (
        "NVDA",
        dt.date(2026, 10, 2),
        190.0,
        "C",
    )


def test_occ_symbol_is_fixed_width_and_padded():
    c = parse_option_instrument("SPX 19DEC26 6000P")
    assert c.occ_symbol == "SPX   261219P06000000"
    assert len(c.occ_symbol) == 21


def test_plan_and_occ_round_trip_through_each_other():
    original = "NVDA 02OCT26 190.5C"
    once = parse_option_instrument(original)
    twice = parse_option_instrument(once.occ_symbol)
    assert once == twice
    assert parse_option_instrument(once.describe()) == once


# --- broker-facing fields -------------------------------------------------


def test_ib_expiry_is_yyyymmdd():
    assert parse_option_instrument("NVDA 02OCT26 190C").ib_expiry == "20261002"


def test_dte_counts_calendar_days_from_a_given_day():
    c = parse_option_instrument("NVDA 02OCT26 190C")
    assert c.dte(dt.date(2026, 9, 25)) == 7
    assert c.dte(dt.date(2026, 10, 2)) == 0


# --- the refusals ---------------------------------------------------------


def test_shares_is_not_an_option():
    """The momentum-stock lane records 'shares'. Inventing a strike is worse
    than failing, so this must raise rather than guess."""

    with pytest.raises(ParseError):
        parse_option_instrument("shares")


@pytest.mark.parametrize(
    "text", ["", "   ", None, "NVDA", "NVDA 02OCT26", "not a thing"]
)
def test_unparseable_instruments_raise(text):
    with pytest.raises(ParseError):
        parse_option_instrument(text)


def test_impossible_expiry_raises_rather_than_rolling_over():
    with pytest.raises(ParseError, match="impossible expiry"):
        parse_option_instrument("NVDA 31FEB26 190C")


def test_unknown_month_raises():
    with pytest.raises(ParseError):
        parse_option_instrument("NVDA 02XXX26 190C")


def test_negative_or_zero_strike_is_rejected_at_construction():
    with pytest.raises(ParseError):
        OptionContract("NVDA", dt.date(2026, 10, 2), 0.0, "C")


def test_bad_right_is_rejected_at_construction():
    with pytest.raises(ParseError):
        OptionContract("NVDA", dt.date(2026, 10, 2), 190.0, "X")

"""Roll-versus-hold arithmetic, and the CSV it reads the curve from.

The tool exists to answer one question — does rolling 4-week bills beat holding
the 13-week — and the answer turns entirely on two quantities being kept apart:
what compounding more often is worth, and what the yield curve charges for it.
The first is a few basis points and the second is usually an order of magnitude
larger, so a bug that conflated them would still print a plausible number. The
headline case is pinned to the cent for that reason.

Nothing here touches the network. ``fetch_curve`` takes an injectable session,
which is what the Treasury test hands it.
"""

import datetime as dt
import os
import sys

import pytest
from click.testing import CliRunner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from tools.bill_ladder import (  # noqa: E402
    DAY_COUNT,
    after_tax_rate,
    breakeven_roll_rate,
    cli,
    compare,
    effective_annual,
    fetch_curve,
    forward_breakeven_rate,
    growth,
    parse_bill_csv,
    roll_growth,
    taxable_equivalent_yield,
)

# --------------------------------------------------------------------------
# Basis conversions
# --------------------------------------------------------------------------


def test_growth_is_simple_interest_on_actual_365():
    # Treasury quotes bill coupon equivalents this way; compounding them inside
    # their own term would overstate every maturity on the curve.
    assert growth(0.0365, 28) == pytest.approx(1.0 + 0.0365 * 28 / 365.0)
    assert growth(0.0386, 91) == pytest.approx(1.00962356, abs=1e-8)


def test_effective_annual_compounds_at_the_maturity_own_frequency():
    assert effective_annual(0.0365, 28) == pytest.approx(0.0371214, abs=1e-6)
    assert effective_annual(0.0386, 91) == pytest.approx(0.0391629, abs=1e-6)


def test_effective_annual_is_a_no_op_at_one_year():
    assert effective_annual(0.04, int(DAY_COUNT)) == pytest.approx(0.04)


def test_roll_growth_holds_the_stub_rather_than_dropping_it():
    # 91 days is three 28-day bills and a 7-day remainder. Crediting only the
    # whole terms would hand the long bill a full week of free interest.
    whole_only = growth(0.0365, 28) ** 3
    rolled = roll_growth(0.0365, 28, 91)
    assert rolled > whole_only
    assert rolled == pytest.approx(whole_only * growth(0.0365, 7))


def test_roll_growth_with_no_stub_matches_plain_compounding():
    assert roll_growth(0.04, 28, 84) == pytest.approx(growth(0.04, 28) ** 3)


# --------------------------------------------------------------------------
# The headline comparison
# --------------------------------------------------------------------------


def test_rolling_four_week_loses_to_the_thirteen_week_at_august_2026_rates():
    # 4-week at 3.65% against 13-week at 3.86%, the curve on 2026-08-14.
    # Pinned to the cent: this is the case the tool was written to answer.
    result = compare(
        roll_rate=0.0365,
        hold_rate=0.0386,
        roll_term_days=28,
        hold_days=91,
        principal=100_000.0,
    )
    assert result.roll_interest == pytest.approx(912.94, abs=0.01)
    assert result.hold_interest == pytest.approx(962.36, abs=0.01)
    assert result.edge == pytest.approx(-49.41, abs=0.01)
    assert result.roll_annual == pytest.approx(0.0371214, abs=1e-6)
    assert result.hold_annual == pytest.approx(0.0391629, abs=1e-6)


def test_compounding_gain_is_dwarfed_by_the_yield_given_up():
    # The entire case for the short bill, against its entire cost. Roughly six
    # basis points bought for twenty-one.
    result = compare(0.0365, 0.0386, 28, 91)
    yield_given_up = result.hold_rate - result.roll_rate
    assert result.compounding_gain == pytest.approx(0.00062, abs=1e-5)
    assert yield_given_up == pytest.approx(0.0021, abs=1e-6)
    assert result.compounding_gain < yield_given_up / 3.0


def test_extra_compounding_frequency_is_worth_about_one_basis_point():
    # Restating the same fact as a tolerance: rolling weekly instead of
    # quarterly excuses about a basis point of yield, not twenty.
    breakeven = breakeven_roll_rate(0.0386, 91, 28, 91)
    assert breakeven < 0.0386
    assert 0.0386 - breakeven == pytest.approx(0.00013, abs=5e-5)


def test_the_roll_wins_when_the_short_rate_is_high_enough():
    # An inverted front end flips the verdict, which is the point of computing
    # it rather than asserting that longer is always better.
    result = compare(0.0420, 0.0386, 28, 91)
    assert result.edge > 0.0
    assert result.roll_rate > result.breakeven


def test_a_flat_curve_still_favours_the_roll_slightly():
    result = compare(0.0386, 0.0386, 28, 91)
    assert result.edge > 0.0
    assert result.edge < 5.0  # on 100k over a quarter — the compounding crumb


# --------------------------------------------------------------------------
# Break-evens
# --------------------------------------------------------------------------


def test_rolling_at_the_breakeven_rate_ties():
    breakeven = breakeven_roll_rate(0.0386, 91, 28, 91)
    result = compare(breakeven, 0.0386, 28, 91)
    assert result.edge == pytest.approx(0.0, abs=1e-6)


def test_forward_breakeven_assumes_the_first_leg_is_already_locked():
    # The first roll is bought at today's auction and cannot help, so the rolls
    # after it have to make up the whole gap — a larger move than the flat
    # break-even implies, and the honest statement of the bet.
    flat = breakeven_roll_rate(0.0386, 91, 28, 91)
    forward = forward_breakeven_rate(0.0386, 91, 28, 91, first_rate=0.0365)
    assert forward > flat > 0.0365


def test_rolling_at_the_forward_breakeven_ties():
    forward = forward_breakeven_rate(0.0386, 91, 28, 91, first_rate=0.0365)
    achieved = growth(0.0365, 28) * roll_growth(forward, 28, 63)
    assert achieved == pytest.approx(growth(0.0386, 91), abs=1e-9)


def test_forward_breakeven_is_undefined_when_one_roll_covers_the_horizon():
    assert forward_breakeven_rate(0.0386, 28, 28, 28, first_rate=0.0365) is None


# --------------------------------------------------------------------------
# Tax
# --------------------------------------------------------------------------


def test_treasury_interest_escapes_state_tax():
    assert after_tax_rate(0.04, federal=0.24, state=0.093, treasury=True) == (
        pytest.approx(0.04 * 0.76)
    )
    assert after_tax_rate(0.04, federal=0.24, state=0.093, treasury=False) == (
        pytest.approx(0.04 * (1.0 - 0.333))
    )


def test_a_bank_paying_the_taxable_equivalent_yield_exactly_ties():
    tey = taxable_equivalent_yield(0.0392, federal=0.24, state=0.093)
    assert after_tax_rate(tey, 0.24, 0.093, treasury=False) == pytest.approx(
        after_tax_rate(0.0392, 0.24, 0.093, treasury=True)
    )


def test_the_exemption_is_worth_nothing_in_a_state_with_no_income_tax():
    assert taxable_equivalent_yield(0.04, federal=0.24, state=0.0) == (
        pytest.approx(0.04)
    )


def test_taxable_equivalent_yield_rejects_an_impossible_tax_rate():
    with pytest.raises(ValueError):
        taxable_equivalent_yield(0.04, federal=0.7, state=0.4)


# --------------------------------------------------------------------------
# Treasury's CSV
# --------------------------------------------------------------------------


TREASURY_CSV = """\
Date,"4 WEEKS BANK DISCOUNT","4 WEEKS COUPON EQUIVALENT","8 WEEKS BANK DISCOUNT",\
"8 WEEKS COUPON EQUIVALENT","13 WEEKS BANK DISCOUNT","13 WEEKS COUPON EQUIVALENT",\
"26 WEEKS BANK DISCOUNT","26 WEEKS COUPON EQUIVALENT","52 WEEKS BANK DISCOUNT",\
"52 WEEKS COUPON EQUIVALENT"
08/13/2026,3.58,3.64,3.66,3.73,3.79,3.87,3.85,3.97,3.88,4.05
08/14/2026,3.59,3.65,3.67,3.74,3.78,3.86,3.84,3.96,3.87,4.04
08/12/2026,3.57,3.63,3.65,3.72,3.77,3.85,,,3.86,4.03
"""


def test_parse_bill_csv_takes_the_most_recent_row_not_the_first():
    curve = parse_bill_csv(TREASURY_CSV)
    assert curve.date == dt.date(2026, 8, 14)
    assert curve.rate(4) == pytest.approx(0.0365)
    assert curve.rate(13) == pytest.approx(0.0386)
    assert curve.rate(52) == pytest.approx(0.0404)


def test_parse_bill_csv_ignores_the_bank_discount_columns():
    # The discount rate sits beside every coupon equivalent, is quoted on a
    # 360-day year against face, and reads about six basis points lower. Picking
    # it up by column position instead of by name would understate the curve.
    curve = parse_bill_csv(TREASURY_CSV)
    assert set(curve.rates) == {4, 8, 13, 26, 52}
    assert 0.0359 not in curve.rates.values()


def test_parse_bill_csv_skips_blank_cells():
    rows = TREASURY_CSV.splitlines()
    only_partial = "\n".join([rows[0], rows[3]])  # the row with empty 26-week cells
    curve = parse_bill_csv(only_partial)
    assert 26 not in curve.rates
    assert curve.rate(13) == pytest.approx(0.0385)


def test_parse_bill_csv_accepts_an_iso_date_column():
    text = TREASURY_CSV.replace("08/14/2026", "2026-08-14")
    assert parse_bill_csv(text).date == dt.date(2026, 8, 14)


def test_curve_rate_names_what_it_has_when_a_maturity_is_missing():
    curve = parse_bill_csv(TREASURY_CSV)
    with pytest.raises(KeyError, match="17"):
        curve.rate(17)


def test_parse_bill_csv_rejects_a_header_it_does_not_recognise():
    with pytest.raises(ValueError, match="coupon-equivalent"):
        parse_bill_csv("Date,Something Else\n08/14/2026,3.65\n")


def test_parse_bill_csv_rejects_a_body_with_no_usable_rows():
    header = TREASURY_CSV.splitlines()[0]
    with pytest.raises(ValueError, match="no usable rows"):
        parse_bill_csv(header + "\nN/A,,,,,,,,,,\n")


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeSession:
    """Stands in for ``requests.Session``; records what it was asked for."""

    def __init__(self, text):
        self._text = text
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        return _FakeResponse(self._text)


def test_fetch_curve_uses_the_injected_session_and_the_requested_year():
    session = _FakeSession(TREASURY_CSV)
    curve = fetch_curve(session=session, year=2026)
    assert curve.date == dt.date(2026, 8, 14)
    url, timeout = session.calls[0]
    assert "2026" in url
    assert "daily_treasury_bill_rates" in url
    assert timeout is not None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_compare_command_reports_the_verdict_and_the_breakeven():
    result = CliRunner().invoke(
        cli, ["compare", "--roll-rate", "3.65", "--hold-rate", "3.86"]
    )
    assert result.exit_code == 0, result.output
    assert "HOLD WINS" in result.output
    assert "-49.41" in result.output
    assert "3.847%" in result.output


def test_compare_command_honours_a_longer_horizon():
    result = CliRunner().invoke(
        cli,
        [
            "compare",
            "--roll-rate",
            "3.65",
            "--hold-rate",
            "3.86",
            "--horizon-weeks",
            "52",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "over 364 days" in result.output


def test_savings_command_prices_the_state_tax_exemption():
    result = CliRunner().invoke(
        cli,
        [
            "savings",
            "--bill-rate",
            "3.86",
            "--savings-apy",
            "3.90",
            "--federal",
            "24",
            "--state",
            "9.3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "BILL WINS" in result.output
    assert "4.46%" in result.output


def test_savings_command_says_so_when_no_state_rate_was_given():
    result = CliRunner().invoke(
        cli, ["savings", "--bill-rate", "3.86", "--savings-apy", "3.90"]
    )
    assert result.exit_code == 0, result.output
    assert "exemption scored nothing" in result.output

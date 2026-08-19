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

import tools.bill_ladder as bill_ladder  # noqa: E402
from tools.bill_ladder import (  # noqa: E402
    DAY_COUNT,
    build_ladder,
    after_tax_rate,
    breakeven_roll_rate,
    cli,
    compare,
    effective_annual,
    fetch_curve,
    forward_breakeven_rate,
    growth,
    nearest_maturity,
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
    # 4-week at 3.65% against 13-week at 3.86%. Not a curve Treasury ever
    # published — 3.86% is the 3-month constant-maturity rate of 2026-08-14,
    # which runs a few basis points above the bill's own coupon equivalent.
    # Kept as the pin anyway: the arithmetic is what is under test, and a wider
    # spread than the real curve's exercises it further from the tie. The
    # curve as actually published is pinned further down.
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


# The shape Treasury actually publishes, which the fixture above does not have:
# seven maturities including the 6-week and the 17-week. Coupon equivalents are
# the real curve of 2026-08-17; the discount column beside them is the discount
# rate those yields imply, d = 360*CE / (365 + CE*days), rather than invented
# numbers. The 52-week has no coupon-equivalent conversion of that form and is
# quoted directly.
LIVE_CSV = """\
Date,"4 WEEKS BANK DISCOUNT","4 WEEKS COUPON EQUIVALENT","6 WEEKS BANK DISCOUNT",\
"6 WEEKS COUPON EQUIVALENT","8 WEEKS BANK DISCOUNT","8 WEEKS COUPON EQUIVALENT",\
"13 WEEKS BANK DISCOUNT","13 WEEKS COUPON EQUIVALENT","17 WEEKS BANK DISCOUNT",\
"17 WEEKS COUPON EQUIVALENT","26 WEEKS BANK DISCOUNT","26 WEEKS COUPON EQUIVALENT",\
"52 WEEKS BANK DISCOUNT","52 WEEKS COUPON EQUIVALENT"
08/17/2026,3.64,3.70,3.63,3.70,3.68,3.75,3.72,3.81,3.75,3.85,3.79,3.92,3.83,3.99
"""


def test_parse_bill_csv_reads_the_full_published_maturity_set():
    # The 6-week and 17-week columns are easy to omit from a hand-built fixture
    # and are present in the real file; a parser keyed to a fixed list of
    # maturities would drop them silently.
    curve = parse_bill_csv(LIVE_CSV)
    assert sorted(curve.rates) == [4, 6, 8, 13, 17, 26, 52]
    assert curve.rate(6) == pytest.approx(0.0370)
    assert curve.rate(17) == pytest.approx(0.0385)


def test_the_curve_of_2026_08_17_favours_holding_at_every_maturity_but_one():
    # The 6-week is quoted at the same 3.70% as the 4-week, so rolling the
    # shorter one wins on compounding alone — the single case on this curve
    # where staying short is not paid for.
    curve = parse_bill_csv(LIVE_CSV)
    short = curve.rate(4)
    edges = {
        weeks: compare(short, curve.rate(weeks), 28, curve.days(weeks)).edge
        for weeks in sorted(curve.rates)
        if weeks > 4
    }
    assert edges[6] > 0.0
    assert all(edge < 0.0 for weeks, edge in edges.items() if weeks > 6)
    # Staying in 4-week paper for a year instead of buying the 52-week bill
    # costs this much per 100k, if the curve never moves.
    assert edges[52] == pytest.approx(-225.71, abs=0.01)


# --------------------------------------------------------------------------
# Ladders
# --------------------------------------------------------------------------


def _live_available():
    curve = parse_bill_csv(LIVE_CSV)
    return {curve.days(w): r for w, r in curve.rates.items()}


def test_a_ladder_splits_the_capital_evenly_and_ends_at_the_full_maturity():
    plan = build_ladder(weeks=13, rungs=4, principal=100_000.0, target_rate=0.0381)
    assert len(plan.rungs) == 4
    assert all(rung.principal == pytest.approx(25_000.0) for rung in plan.rungs)
    assert plan.rungs[-1].seed_days == 91
    assert plan.ideal_spacing_days == pytest.approx(22.75)


def test_the_cadence_always_sums_to_the_maturity():
    # The invariant that makes a ladder a ladder: however the seeds land, the
    # gaps between maturities must tile exactly one full term, because every
    # rung rolls forward by that term and the pattern repeats. A seeding bug
    # that lost or double-counted a rung would break this before it broke
    # anything a reader would notice.
    for rungs in (1, 2, 3, 4, 7):
        for weeks in (4, 13, 26, 52):
            plan = build_ladder(weeks, rungs, 100_000.0, 0.0381)
            assert sum(plan.cadence_days) == weeks * 7


def test_the_cadence_still_tiles_when_seeded_from_real_maturities():
    plan = build_ladder(13, 4, 100_000.0, 0.0381, available=_live_available())
    assert [rung.seed_days for rung in plan.rungs] == [28, 42, 56, 91]
    assert plan.cadence_days == (14, 14, 35, 28)
    assert sum(plan.cadence_days) == 91


def test_a_one_rung_ladder_is_just_holding_the_bill():
    plan = build_ladder(13, 1, 100_000.0, 0.0381, available=_live_available())
    assert plan.cadence_days == (91,)
    assert plan.liquidity_edge_days() == 0
    assert plan.build_drag == pytest.approx(0.0)


def test_seeds_come_from_purchasable_maturities_not_arithmetic_ones():
    # 22.75-day spacing wants a 23-day bill, which does not exist.
    ideal = build_ladder(13, 4, 100_000.0, 0.0381)
    real = build_ladder(13, 4, 100_000.0, 0.0381, available=_live_available())
    assert ideal.rungs[0].seed_days == 23
    assert real.rungs[0].seed_days == 28
    assert real.rungs[0].seed_rate == pytest.approx(0.0370)


def test_nearest_maturity_avoids_one_already_taken():
    available = [28, 42, 56, 91]
    assert nearest_maturity(available, 30) == 28
    assert nearest_maturity(available, 30, taken=[28]) == 42


def test_nearest_maturity_reuses_only_once_nothing_is_left():
    assert nearest_maturity([28], 30, taken=[28]) == 28


def test_a_curve_too_short_to_seed_every_rung_is_flagged_not_hidden():
    # Four rungs of 4-week paper want bills at 7, 14, 21 and 28 days, and the
    # curve's shortest is 28. Rungs that come due on the same day roll the same
    # distance forward and stay married, so the ladder is really one rung.
    plan = build_ladder(4, 4, 100_000.0, 0.0370, available={28: 0.0370})
    assert not plan.distinct_seeds
    assert build_ladder(13, 4, 100_000.0, 0.0381, available=_live_available())


def test_steady_state_yields_exactly_the_maturity_it_is_built_from():
    # A ladder buys liquidity, not yield. If this ever showed a premium the
    # arithmetic would be inventing money.
    plan = build_ladder(13, 4, 100_000.0, 0.0381, available=_live_available())
    assert plan.steady_annual == pytest.approx(effective_annual(0.0381, 91))
    assert plan.steady_income == pytest.approx(100_000.0 * plan.steady_annual)


def test_the_last_rung_costs_nothing_to_place():
    plan = build_ladder(13, 4, 100_000.0, 0.0381, available=_live_available())
    assert plan.rungs[-1].seed_days == 91
    assert plan.rungs[-1].seed_rate == pytest.approx(0.0381)
    assert plan.rungs[-1].drag == pytest.approx(0.0)


def test_build_drag_is_the_sum_of_the_rungs_and_positive_on_a_rising_curve():
    plan = build_ladder(13, 4, 100_000.0, 0.0381, available=_live_available())
    assert plan.build_drag == pytest.approx(sum(r.drag for r in plan.rungs))
    assert plan.build_drag == pytest.approx(7.58, abs=0.01)


def test_build_drag_is_unknown_rather_than_guessed_without_a_curve():
    plan = build_ladder(13, 4, 100_000.0, 0.0381)
    assert plan.build_drag is None
    assert all(rung.drag is None for rung in plan.rungs)


def test_a_flat_curve_costs_nothing_to_ladder():
    flat = {28: 0.0381, 42: 0.0381, 56: 0.0381, 91: 0.0381}
    plan = build_ladder(13, 4, 100_000.0, 0.0381, available=flat)
    assert plan.build_drag == pytest.approx(0.0)


def test_build_ladder_rejects_a_structure_that_is_not_one():
    with pytest.raises(ValueError, match="at least one rung"):
        build_ladder(13, 0, 100_000.0, 0.0381)
    with pytest.raises(ValueError, match="at least a week"):
        build_ladder(0, 4, 100_000.0, 0.0381)


def test_ladder_command_reports_the_real_seeds_and_the_build_cost(monkeypatch):
    monkeypatch.setattr(
        bill_ladder, "fetch_curve", lambda **kw: parse_bill_csv(LIVE_CSV)
    )
    result = CliRunner().invoke(cli, ["ladder"])
    assert result.exit_code == 0, result.output
    assert "28d" in result.output
    assert "14d, 14d, 35d, 28d" in result.output
    assert "7.58" in result.output


def test_ladder_command_does_not_pass_ideal_maturities_off_as_buyable():
    # Offline the seeds are arithmetic, and saying otherwise would send someone
    # looking for a 23-day bill.
    result = CliRunner().invoke(cli, ["ladder", "--rate", "3.81"])
    assert result.exit_code == 0, result.output
    assert "not purchasable bills" in result.output
    assert "Unknown without the curve" in result.output


def test_ladder_command_does_not_claim_liquidity_it_does_not_have(monkeypatch):
    # The 4-rung 13-week ladder's worst gap is 35 days against the 28-day bill
    # it is compared with, so it is slower in the worst case, not faster.
    monkeypatch.setattr(
        bill_ladder, "fetch_curve", lambda **kw: parse_bill_csv(LIVE_CSV)
    )
    result = CliRunner().invoke(cli, ["ladder"])
    assert "longest gap is 35d" in result.output
    # Two 26-week rungs land exactly on the 91-day bill's own cadence.
    even = CliRunner().invoke(cli, ["ladder", "--weeks", "26", "--rungs", "2"])
    assert "at least as often" in even.output

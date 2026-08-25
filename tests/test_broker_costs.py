"""Tests for the broker cost model.

The interesting behaviour is all at the edges: the per-leg commission cap that
only bites above a size, the platform fee that only matters annually, and the
sunk-cost subtraction that produced a wrong ranking the first time it was
written. Each of those has a test that fails if it regresses.
"""

import pytest

from tools.broker_costs import (
    Broker,
    OptionRate,
    default_brokers,
    main,
)


def _by_name(name):
    for broker in default_brokers():
        if broker.name == name:
            return broker
    raise AssertionError(f"no broker named {name!r}")


# ---------------------------------------------------------------------------
# Per-leg commission cap
# ---------------------------------------------------------------------------


def test_cap_does_not_bite_below_threshold():
    """tastytrade's $10/leg cap is irrelevant at small size."""

    rate = OptionRate(1.00, 0.00, per_leg_cap=10.0)
    assert rate.leg_cost(5, closing=False) == pytest.approx(5.0)


def test_cap_bites_at_and_above_threshold():
    rate = OptionRate(1.00, 0.00, per_leg_cap=10.0)
    assert rate.leg_cost(10, closing=False) == pytest.approx(10.0)
    assert rate.leg_cost(250, closing=False) == pytest.approx(10.0)


def test_uncapped_rate_scales_linearly():
    rate = OptionRate(0.65, 0.65)
    assert rate.leg_cost(250, closing=False) == pytest.approx(162.5)


def test_closing_side_can_be_free():
    rate = OptionRate(1.00, 0.00, per_leg_cap=10.0)
    assert rate.leg_cost(4, closing=True) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# The sunk-cost invariant — this is the bug that shipped once
# ---------------------------------------------------------------------------


def test_sunk_cost_cannot_exceed_the_platform_fee():
    """A cost cannot be sunk out of a total it was never added to.

    The first version of this model marked IB's data fee already-paid without
    ever charging it, so IB came out $120/yr cheaper than a broker with
    identical commissions and no platform fee at all.
    """

    with pytest.raises(ValueError, match="never included it"):
        Broker(
            name="wrong",
            equity_option=OptionRate(0.65, 0.65),
            index_option=OptionRate(0.65, 0.65),
            platform_monthly=0.0,
            already_paid_monthly=10.0,
        )


def test_marginal_never_undercuts_commission_for_a_fully_sunk_fee():
    """With the whole platform fee sunk, marginal cost is exactly commission."""

    ib = _by_name("IBKR (fixed)")
    cost = ib.annual_cost(legs=4, contracts=1, cycles=52, close_rate=0.7, index=True)
    assert cost["marginal"] == pytest.approx(cost["commission"])
    assert cost["total"] == pytest.approx(cost["commission"] + cost["platform"])


def test_identical_commissions_tie_on_the_marginal_basis():
    """IBKR and Schwab charge the same $0.65; the sunk data fee must not split them."""

    kwargs = dict(legs=4, contracts=1, cycles=52, close_rate=0.7, index=True)
    ib = _by_name("IBKR (fixed)").annual_cost(**kwargs)
    schwab = _by_name("Schwab / tos").annual_cost(**kwargs)
    assert ib["marginal"] == pytest.approx(schwab["marginal"])


# ---------------------------------------------------------------------------
# Annual arithmetic
# ---------------------------------------------------------------------------


def test_platform_fee_lands_in_the_annual_total():
    tradier = _by_name("Tradier Pro")
    cost = tradier.annual_cost(
        legs=4, contracts=1, cycles=52, close_rate=0.7, index=True
    )
    assert cost["platform"] == pytest.approx(120.0)
    # 52 opens x 4 legs x $0.35, plus 70% of those closed at the same rate.
    assert cost["open"] == pytest.approx(52 * 4 * 0.35)
    assert cost["close"] == pytest.approx(52 * 0.7 * 4 * 0.35)


def test_close_rate_zero_charges_nothing_to_close():
    schwab = _by_name("Schwab / tos")
    cost = schwab.annual_cost(
        legs=4, contracts=1, cycles=52, close_rate=0.0, index=True
    )
    assert cost["close"] == pytest.approx(0.0)
    assert cost["commission"] == pytest.approx(cost["open"])


def test_exchange_fee_scales_with_contracts_traded():
    schwab = _by_name("Schwab / tos")
    free = schwab.annual_cost(
        legs=4, contracts=2, cycles=10, close_rate=1.0, index=True, exchange_fee=0.0
    )
    charged = schwab.annual_cost(
        legs=4, contracts=2, cycles=10, close_rate=1.0, index=True, exchange_fee=0.07
    )
    # 10 opens + 10 closes, 4 legs, 2 contracts = 160 contracts.
    assert charged["contracts"] == pytest.approx(160)
    assert charged["total"] - free["total"] == pytest.approx(160 * 0.07)


# ---------------------------------------------------------------------------
# The finding the tool exists to produce
# ---------------------------------------------------------------------------


def test_tradier_platform_fee_loses_at_one_lot_and_wins_at_five():
    """The $120/yr fee is the whole story, and it flips with size.

    This is the result that says broker choice is not a cost question at this
    desk's size — if it ever stops being true, this test breaks and the note in
    docs/brokers.md needs rewriting.
    """

    kwargs = dict(legs=4, cycles=52, close_rate=0.7, index=True)
    tradier = _by_name("Tradier Pro")
    schwab = _by_name("Schwab / tos")

    at_one = tradier.annual_cost(contracts=1, **kwargs)["total"]
    schwab_one = schwab.annual_cost(contracts=1, **kwargs)["total"]
    assert at_one > schwab_one

    at_five = tradier.annual_cost(contracts=5, **kwargs)["total"]
    schwab_five = schwab.annual_cost(contracts=5, **kwargs)["total"]
    assert at_five < schwab_five


def test_tastytrade_cap_makes_it_flat_above_ten_lots():
    """Above the cap, size is free — which is why it wins at 25 lots."""

    tasty = _by_name("tastytrade")
    kwargs = dict(legs=4, cycles=52, close_rate=0.7, index=True)
    assert tasty.annual_cost(contracts=10, **kwargs)["total"] == pytest.approx(
        tasty.annual_cost(contracts=50, **kwargs)["total"]
    )


def test_one_lot_spread_across_brokers_stays_small():
    """The headline finding: at 1 lot the entire spread is a rounding error.

    If this ever exceeds a few hundred dollars a year, cost becomes a real
    input to broker choice and docs/brokers.md's argument has to be revisited.
    """

    kwargs = dict(legs=4, contracts=1, cycles=52, close_rate=0.7, index=True)
    totals = [b.annual_cost(**kwargs)["total"] for b in default_brokers()]
    assert max(totals) - min(totals) < 300.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["brokers"],
        ["trade", "--legs", "4", "--contracts", "1", "--index"],
        ["condor", "--sizes", "1", "5"],
        ["condor", "--marginal", "--sizes", "1"],
        ["spread", "--sizes", "1", "10"],
    ],
)
def test_cli_commands_run(argv, capsys):
    assert main(argv) == 0
    assert capsys.readouterr().out.strip()

"""Placing an option order through IB.

Four of the seven things this desk trades are options, and until
``place_option_order`` existed none of them could reach a broker except by
hand — ``place_orders`` builds share contracts only. These tests pin the two
things that make the new path safe to use: the live-account brake still
applies to it, and a limit order with no quote refuses rather than turning
itself into a market order into an illiquid chain.
"""

import datetime as dt
from unittest.mock import MagicMock

import pytest

from pwb_toolbox.execution.ib_connector import (
    PAPER_PORTS,
    IBConnector,
    LiveOrderBlocked,
)
from pwb_toolbox.execution.option_contract import ParseError

LIVE_PORT = 4001
PAPER_PORT = 4002
ENV = "PWB_ALLOW_LIVE_ORDERS"

PLAN = "NVDA 02OCT26 190C"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def _connector(
    port=PAPER_PORT, allow_live_orders=False, bid=4.10, ask=4.30, last=None, close=None
):
    ibc = IBConnector.__new__(IBConnector)
    ibc.port = port
    ibc.allow_live_orders = allow_live_orders
    ibc.ib = MagicMock()

    ticker = MagicMock()
    ticker.bid, ticker.ask, ticker.last, ticker.close = bid, ask, last, close
    ibc.ib.reqMktData.return_value = ticker

    trade = MagicMock()
    trade.order.orderId = 77
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.filled = 0
    trade.orderStatus.avgFillPrice = 0.0
    entry = MagicMock()
    entry.time.isoformat.return_value = "2026-08-24T14:30:00+00:00"
    trade.log = [entry]
    ibc.ib.placeOrder.return_value = trade
    return ibc


def _placed_order(ibc):
    return ibc.ib.placeOrder.call_args[0][1]


def _placed_contract(ibc):
    return ibc.ib.placeOrder.call_args[0][0]


# --- the brake still guards this path -------------------------------------


def test_live_port_blocks_option_orders_without_both_unlocks():
    """The whole point of the brake is that a new order path cannot dodge it."""

    ibc = _connector(port=LIVE_PORT)
    with pytest.raises(LiveOrderBlocked):
        ibc.place_option_order(PLAN, 1)
    ibc.ib.placeOrder.assert_not_called()


def test_live_port_still_blocks_with_only_the_env_unlock(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    ibc = _connector(port=LIVE_PORT, allow_live_orders=False)
    with pytest.raises(LiveOrderBlocked):
        ibc.place_option_order(PLAN, 1)


def test_live_port_still_blocks_with_only_the_code_unlock():
    ibc = _connector(port=LIVE_PORT, allow_live_orders=True)
    with pytest.raises(LiveOrderBlocked):
        ibc.place_option_order(PLAN, 1)


def test_live_port_allows_the_order_with_both_unlocks(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    ibc = _connector(port=LIVE_PORT, allow_live_orders=True)
    record = ibc.place_option_order(PLAN, 1)
    assert record.status == "Submitted"


@pytest.mark.parametrize("port", sorted(PAPER_PORTS))
def test_paper_ports_need_no_unlocks(port):
    ibc = _connector(port=port)
    record = ibc.place_option_order(PLAN, 1)
    assert record.order_id == 77


# --- the contract that actually gets built --------------------------------


def test_contract_carries_the_parsed_fields():
    ibc = _connector()
    ibc.place_option_order(PLAN, 1)
    contract = _placed_contract(ibc)
    assert contract.symbol == "NVDA"
    assert contract.lastTradeDateOrContractMonth == "20261002"
    assert contract.strike == pytest.approx(190.0)
    assert contract.right == "C"


def test_record_is_keyed_on_the_occ_symbol():
    """A plan string is not a stable key; the OCC symbol is."""

    ibc = _connector()
    record = ibc.place_option_order(PLAN, 1)
    assert record.symbol == "NVDA  261002C00190000".strip()


def test_an_occ_symbol_is_accepted_as_readily_as_a_plan_string():
    ibc = _connector()
    record = ibc.place_option_order("NVDA  261002C00190000", 1)
    assert record.symbol.startswith("NVDA")


def test_shares_are_refused_rather_than_mangled():
    ibc = _connector()
    with pytest.raises(ParseError):
        ibc.place_option_order("shares", 1)
    ibc.ib.placeOrder.assert_not_called()


# --- direction and size ---------------------------------------------------


def test_positive_quantity_buys_and_negative_sells():
    ibc = _connector()
    assert ibc.place_option_order(PLAN, 2).action == "BUY"
    assert ibc.place_option_order(PLAN, -2).action == "SELL"


def test_quantity_is_the_absolute_contract_count():
    ibc = _connector()
    record = ibc.place_option_order(PLAN, -3)
    assert record.quantity == 3
    assert record.direction == "short"


def test_zero_quantity_is_an_error_not_a_silent_skip():
    """place_orders skips zeroes because it sweeps a position vector. A single
    named order asking for nothing is a caller bug and should say so."""

    ibc = _connector()
    with pytest.raises(ValueError, match="zero-quantity"):
        ibc.place_option_order(PLAN, 0)


# --- pricing --------------------------------------------------------------


def test_explicit_limit_price_wins_over_any_quote():
    ibc = _connector(bid=4.10, ask=4.30)
    record = ibc.place_option_order(PLAN, 1, limit_price=4.00)
    assert record.price == pytest.approx(4.00)
    assert _placed_order(ibc).lmtPrice == pytest.approx(4.00)
    ibc.ib.reqMktData.assert_not_called()


def test_limit_price_defaults_to_the_midpoint():
    """Options are wide; the midpoint is a better default than the last trade."""

    ibc = _connector(bid=4.10, ask=4.30, last=3.00)
    record = ibc.place_option_order(PLAN, 1)
    assert record.price == pytest.approx(4.20)


def test_last_is_used_only_when_a_side_is_missing():
    ibc = _connector(bid=None, ask=None, last=3.55)
    assert ibc.place_option_order(PLAN, 1).price == pytest.approx(3.55)


def test_close_is_the_final_fallback():
    ibc = _connector(bid=0, ask=0, last=None, close=2.25)
    assert ibc.place_option_order(PLAN, 1).price == pytest.approx(2.25)


def test_no_quote_refuses_rather_than_downgrading_to_market():
    """The share path downgrades to a market order. For options that is how a
    spread eats a position, so this path refuses instead."""

    ibc = _connector(bid=None, ask=None, last=None, close=None)
    with pytest.raises(ValueError, match="no quote available"):
        ibc.place_option_order(PLAN, 1)
    ibc.ib.placeOrder.assert_not_called()


def test_market_order_is_available_when_explicitly_asked_for():
    ibc = _connector(bid=None, ask=None, last=None, close=None)
    record = ibc.place_option_order(PLAN, 1, order_type="MKT")
    assert record.price is None
    assert _placed_order(ibc).orderType == "MKT"

"""The live-order brake on :class:`IBConnector`.

The design constraint these tests exist to pin: the brake must stop an
irreversible order against a funded account, and must be *invisible* to
everything else. A guardrail that blocks paper automation or the test suite
gets switched off, and a switched-off guardrail protects nothing.
"""

from unittest.mock import MagicMock

import pytest

from pwb_toolbox.execution.ib_connector import (
    PAPER_PORTS,
    IBConnector,
    LiveOrderBlocked,
    _env_allows_live_orders,
)

LIVE_PORT = 4001  # IB Gateway's funded-account port
ENV = "PWB_ALLOW_LIVE_ORDERS"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """No test inherits an unlock from the developer's own shell."""

    monkeypatch.delenv(ENV, raising=False)


def _connector(port, allow_live_orders=False):
    ibc = IBConnector.__new__(IBConnector)
    ibc.port = port
    ibc.allow_live_orders = allow_live_orders
    ibc.ib = MagicMock()
    return ibc


# --- paper is never gated -------------------------------------------------


@pytest.mark.parametrize("port", sorted(PAPER_PORTS))
def test_paper_ports_are_never_blocked(port):
    """Paper trading needs no unlocks at all — this is the whole point."""

    _connector(port)._assert_orders_allowed()


def test_default_port_is_a_paper_port():
    """The constructor default must not be a funded account.

    If this ever changes, every caller that relied on the default silently
    starts needing unlocks.
    """

    assert IBConnector().port in PAPER_PORTS


# --- live needs both keys -------------------------------------------------


def test_live_blocked_with_neither_unlock():
    with pytest.raises(LiveOrderBlocked):
        _connector(LIVE_PORT)._assert_orders_allowed()


def test_live_blocked_with_only_the_argument():
    with pytest.raises(LiveOrderBlocked):
        _connector(LIVE_PORT, allow_live_orders=True)._assert_orders_allowed()


def test_live_blocked_with_only_the_env_var(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(LiveOrderBlocked):
        _connector(LIVE_PORT)._assert_orders_allowed()


def test_live_allowed_with_both_unlocks(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    _connector(LIVE_PORT, allow_live_orders=True)._assert_orders_allowed()


# --- fail closed, not open ------------------------------------------------


def test_unknown_port_is_treated_as_live():
    """An unrecognised port must fail safe rather than be assumed harmless."""

    with pytest.raises(LiveOrderBlocked):
        _connector(9999)._assert_orders_allowed()


def test_instance_without_init_fails_closed():
    """Bypassing __init__ must raise LiveOrderBlocked, not AttributeError.

    The calibration tests build connectors this way, so the guard reads its
    attributes defensively — but defensive must mean blocked, not permitted.
    """

    bare = IBConnector.__new__(IBConnector)
    with pytest.raises(LiveOrderBlocked):
        bare._assert_orders_allowed()


def test_message_names_both_missing_remedies():
    """The error has to say how to proceed, or it just gets worked around."""

    with pytest.raises(LiveOrderBlocked) as excinfo:
        _connector(LIVE_PORT)._assert_orders_allowed()
    message = str(excinfo.value)
    assert "allow_live_orders=True" in message
    assert ENV in message


# --- the broker is never reached ------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.place_orders({"AAPL": 10}),
        lambda c: c.execute_orders({"AAPL": 10}, time_in_seconds=60),
    ],
)
def test_order_entry_points_block_before_touching_the_broker(call):
    """Both entry points must refuse *before* any ib_insync call is made."""

    ibc = _connector(LIVE_PORT)
    with pytest.raises(LiveOrderBlocked):
        call(ibc)
    ibc.ib.assert_not_called()
    assert not ibc.ib.method_calls


# --- env parsing ----------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_truthy_env_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert _env_allows_live_orders()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_falsy_env_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert not _env_allows_live_orders()

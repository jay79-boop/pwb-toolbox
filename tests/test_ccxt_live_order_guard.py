"""The live-order brake on :class:`CCXTConnector`.

The same design constraint the IB brake is held to: it must stop an
irreversible order against a funded exchange account, and must be *invisible*
to everything that cannot move real money. A guardrail that blocks sandbox
automation or the test suite gets switched off, and a switched-off guardrail
protects nothing.

This connector shipped without any brake at all while :class:`IBConnector` had
the full two-key pattern, so these tests exist to pin the asymmetry closed.
"""

from unittest.mock import MagicMock

import pytest

from pwb_toolbox.execution._live_guard import LIVE_ORDER_ENV
from pwb_toolbox.execution.ccxt_connector import CCXTConnector, LiveOrderBlocked

ENV = LIVE_ORDER_ENV


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """No test inherits an unlock from the developer's own shell."""

    monkeypatch.delenv(ENV, raising=False)


def _exchange(sandbox=False):
    ex = MagicMock()
    ex.isSandboxModeEnabled = sandbox
    ex.fetch_ticker.return_value = {"last": 100.0}
    ex.create_order.return_value = {
        "id": "1",
        "status": "closed",
        "filled": 1.0,
        "average": 100.0,
    }
    return ex


def _connector(sandbox=False, allow_live_orders=False, connected=True):
    cc = CCXTConnector("binance", allow_live_orders=allow_live_orders, sandbox=sandbox)
    if connected:
        cc.exchange = _exchange(sandbox)
    return cc


# --- sandbox is never gated ----------------------------------------------


def test_sandbox_is_never_blocked():
    """Testnet trading needs no unlocks at all - this is the whole point."""

    _connector(sandbox=True)._assert_orders_allowed()


def test_sandbox_orders_go_through_with_no_unlocks():
    assert _connector(sandbox=True).place_orders({"BTC/USDT": 0.01})


def test_default_is_not_sandbox_and_not_unlocked():
    """The constructor defaults must describe a connector that cannot trade.

    If either default flips, every caller that relied on it silently gains the
    ability to place a funded-account order.
    """

    cc = CCXTConnector("binance")
    assert cc.sandbox is False
    assert cc.allow_live_orders is False


# --- live needs both keys -------------------------------------------------


def test_live_blocked_with_no_unlocks():
    with pytest.raises(LiveOrderBlocked):
        _connector()._assert_orders_allowed()


def test_live_blocked_with_only_the_code_key():
    with pytest.raises(LiveOrderBlocked):
        _connector(allow_live_orders=True)._assert_orders_allowed()


def test_live_blocked_with_only_the_env_key(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(LiveOrderBlocked):
        _connector()._assert_orders_allowed()


def test_live_allowed_with_both_keys(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    _connector(allow_live_orders=True)._assert_orders_allowed()


def test_place_orders_is_the_choke_point():
    """The brake must sit on the call that reaches the exchange, not beside it."""

    with pytest.raises(LiveOrderBlocked):
        _connector().place_orders({"BTC/USDT": 0.01})


def test_execute_orders_inherits_the_brake():
    """``execute_orders`` delegates to ``place_orders`` and must not bypass it."""

    with pytest.raises(LiveOrderBlocked):
        _connector().execute_orders({"BTC/USDT": 0.01}, time_in_seconds=1)


def test_no_order_reaches_the_exchange_when_blocked():
    cc = _connector()
    with pytest.raises(LiveOrderBlocked):
        cc.place_orders({"BTC/USDT": 0.01})
    cc.exchange.create_order.assert_not_called()


# --- fail closed ----------------------------------------------------------


def test_asking_for_sandbox_is_not_the_same_as_being_in_sandbox():
    """A connector that requested sandbox but did not get it is still live.

    Sandbox is read off the connected exchange, never off the constructor flag,
    so a ``set_sandbox_mode`` that silently did nothing cannot unlock trading.
    """

    cc = CCXTConnector("binance", sandbox=True)
    cc.exchange = _exchange(sandbox=False)
    with pytest.raises(LiveOrderBlocked):
        cc._assert_orders_allowed()


def test_unconnected_connector_fails_closed():
    """No exchange object means nothing has proven itself to be sandbox."""

    cc = CCXTConnector("binance")
    cc.exchange = None
    with pytest.raises(LiveOrderBlocked):
        cc._assert_orders_allowed()


def test_instance_without_init_fails_closed():
    """Bypassing __init__ must raise LiveOrderBlocked, not AttributeError."""

    bare = CCXTConnector.__new__(CCXTConnector)
    with pytest.raises(LiveOrderBlocked):
        bare._assert_orders_allowed()


# --- the message must be actionable ---------------------------------------


def test_error_names_both_remedies():
    """An error that does not say how to proceed just gets worked around."""

    with pytest.raises(LiveOrderBlocked) as excinfo:
        _connector()._assert_orders_allowed()
    message = str(excinfo.value)
    assert "allow_live_orders=True" in message
    assert ENV in message
    assert "sandbox=True" in message


def test_error_names_only_the_missing_remedy(monkeypatch):
    """With one key already supplied, the message asks only for the other."""

    monkeypatch.setenv(ENV, "1")
    with pytest.raises(LiveOrderBlocked) as excinfo:
        _connector()._assert_orders_allowed()
    message = str(excinfo.value)
    assert "allow_live_orders=True" in message
    assert f"set {ENV}=1" not in message


# --- the factory must not hand out both keys ------------------------------


def test_factory_defaults_to_locked(monkeypatch):
    """``create_connector`` must not produce a connector that can trade live."""

    monkeypatch.setattr("ccxt.binance", MagicMock(), raising=False)
    from pwb_toolbox.execution.broker_factory import create_connector

    cc = create_connector({"broker": "ccxt", "exchange": "binance"})
    assert cc.allow_live_orders is False


def test_factory_reads_the_code_key_from_config_only(monkeypatch):
    """The first key comes from the config mapping and nowhere else.

    The second key is already an environment variable. If the factory also read
    the first one from the environment, a single exported variable would satisfy
    both and the two-key design would collapse to one.
    """

    monkeypatch.setattr("ccxt.binance", MagicMock(), raising=False)
    monkeypatch.setenv(ENV, "1")
    monkeypatch.setenv("PWB_ALLOW_LIVE_ORDERS", "1")
    from pwb_toolbox.execution.broker_factory import create_connector

    cc = create_connector({"broker": "ccxt", "exchange": "binance"})
    assert cc.allow_live_orders is False, "env alone must never unlock the code key"

    cc = create_connector(
        {"broker": "ccxt", "exchange": "binance", "allow_live_orders": True}
    )
    assert cc.allow_live_orders is True


def test_factory_passes_sandbox_through(monkeypatch):
    monkeypatch.setattr("ccxt.binance", MagicMock(), raising=False)
    from pwb_toolbox.execution.broker_factory import create_connector

    cc = create_connector({"broker": "ccxt", "exchange": "binance", "sandbox": True})
    assert cc.sandbox is True


def test_factory_locks_ib_too(monkeypatch):
    """The same rule on the IB branch, so the two brokers cannot drift."""

    from pwb_toolbox.execution.broker_factory import create_connector

    monkeypatch.setenv(ENV, "1")
    ibc = create_connector({"broker": "ib"})
    assert ibc.allow_live_orders is False

    ibc = create_connector({"broker": "ib", "allow_live_orders": True})
    assert ibc.allow_live_orders is True

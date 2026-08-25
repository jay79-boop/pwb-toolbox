"""Utility functions to interact with cryptocurrency exchanges via ``ccxt``.

This module mirrors the interface exposed by :class:`IBConnector` but uses
`ccxt`_ to communicate with crypto exchanges.  Only a small subset of
functionality is implemented which is sufficient for placing basic market or
limit orders and retrieving account information.

Example
-------
    >>> from pwb_toolbox.execution import create_connector
    >>> cc = create_connector({"broker": "ccxt", "exchange": "binance",
    ...                        "api_key": "...", "api_secret": "...",
    ...                        "sandbox": True})
    >>> cc.connect()
    >>> nav = cc.get_account_nav()
    >>> positions = cc.get_positions()
    >>> cc.place_orders({"BTC/USDT": 0.01})   # sandbox: no unlocks needed
    >>> cc.disconnect()

Trading a **funded** exchange account additionally requires both unlocks
described in :mod:`pwb_toolbox.execution._live_guard` -- ``allow_live_orders=True``
in the calling code and ``PWB_ALLOW_LIVE_ORDERS`` in the environment. Sandbox
mode needs neither, so tests and paper automation never notice the brake.

The connector provides ``connect``/``disconnect`` helpers, account information
methods and simple order placement utilities.  Orders are submitted using
:func:`ccxt.Exchange.create_order` while price snapshots are obtained via
:func:`ccxt.Exchange.fetch_ticker`.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Dict, List, Optional

import pandas as pd
import ccxt

from ._live_guard import LiveOrderBlocked, missing_unlocks


@dataclass
class TradeRecord:
    """Container for information about a single trade.

    The structure mirrors the ``TradeRecord`` used by :class:`IBConnector` so
    that calling code can operate on both connectors interchangeably.
    """

    timestamp: str
    ib_timestamp: Optional[str]
    symbol: str
    action: str
    quantity: float
    price: Optional[float]
    order_id: str
    status: str
    filled: float
    avg_fill_price: Optional[float]
    entry: Optional[float]
    exit: Optional[float]
    ret: Optional[float]
    direction: str
    order_type: str

    def as_dict(self) -> Dict[str, Optional[float]]:
        """Return the record as a plain dictionary."""

        return {
            "timestamp": self.timestamp,
            "ib_timestamp": self.ib_timestamp,
            "symbol": self.symbol,
            "action": self.action,
            "quantity": self.quantity,
            "price": self.price,
            "order_id": self.order_id,
            "status": self.status,
            "filled": self.filled,
            "avg_fill_price": self.avg_fill_price,
            "entry": self.entry,
            "exit": self.exit,
            "return": self.ret,
            "direction": self.direction,
            "order_type": self.order_type,
        }


class CCXTConnector:
    """Minimal wrapper around :mod:`ccxt` exchanges.

    Parameters
    ----------
    exchange : str
        Name of the exchange as expected by ``ccxt`` (e.g. ``"binance"``).
    api_key, api_secret : str, optional
        Credentials used to authenticate with the exchange.
    params : dict, optional
        Additional parameters passed to the exchange constructor.
    sandbox : bool, optional
        Put the exchange in ``ccxt`` sandbox/testnet mode on :meth:`connect`.
        Sandbox orders cannot move real money, so they bypass the live-order
        brake entirely.
    allow_live_orders : bool, optional
        First of the two keys required to trade a funded account. The second is
        the ``PWB_ALLOW_LIVE_ORDERS`` environment variable. Defaults to
        ``False`` so that anything which merely constructs a connector -- a
        stray import, an unattended scheduled run -- cannot place a live order.
    """

    def __init__(
        self,
        exchange: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        params: Optional[Dict[str, object]] = None,
        sandbox: bool = False,
        allow_live_orders: bool = False,
    ) -> None:
        self.exchange_name = exchange
        self.api_key = api_key
        self.api_secret = api_secret
        self.params = params or {}
        self.sandbox = sandbox
        self.allow_live_orders = allow_live_orders
        self.exchange: Optional[ccxt.Exchange] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """Instantiate the ``ccxt`` exchange using the provided credentials."""

        exchange_class = getattr(ccxt, self.exchange_name)
        config = {"apiKey": self.api_key, "secret": self.api_secret}
        config.update(self.params)
        exchange = exchange_class(config)
        if self.sandbox:
            # Raises ccxt.NotSupported when the exchange has no testnet, which
            # is the right outcome: failing here is far better than silently
            # leaving a "sandbox" connector pointed at the live venue.
            exchange.set_sandbox_mode(True)
        self.exchange = exchange

    def disconnect(self) -> None:
        """Clear the exchange instance."""

        if self.exchange is not None:
            # Some exchanges implement ``close`` for websockets; ignore errors
            close = getattr(self.exchange, "close", None)
            if callable(close):
                try:  # pragma: no cover - network failure
                    close()
                except Exception:
                    pass
        self.exchange = None

    # ------------------------------------------------------------------
    # Live-order safety
    # ------------------------------------------------------------------
    def _assert_orders_allowed(self) -> None:
        """Block funded-account orders unless both unlocks are present.

        Sandbox mode returns immediately, so this is invisible to anything that
        cannot move real money. Sandbox is read from the connected exchange
        (``isSandboxModeEnabled``) rather than from the constructor flag, so a
        connector that merely *asked* for sandbox and did not get it is still
        treated as live. Attributes are read defensively: an instance built
        without ``__init__`` fails closed rather than raising ``AttributeError``.
        """

        exchange = getattr(self, "exchange", None)
        if exchange is not None and getattr(exchange, "isSandboxModeEnabled", False):
            return

        missing = missing_unlocks(
            getattr(self, "allow_live_orders", False),
            "pass allow_live_orders=True when constructing CCXTConnector",
        )
        if not missing:
            return

        raise LiveOrderBlocked(
            f"Refusing to send orders on {getattr(self, 'exchange_name', '?')!r}, "
            "which is not in sandbox mode. To trade a funded account, "
            + " and ".join(missing)
            + ". To trade the testnet instead, construct the connector with "
            "sandbox=True — an exchange that is not in sandbox mode is treated "
            "as live on purpose."
        )

    # ------------------------------------------------------------------
    # Account information helpers
    # ------------------------------------------------------------------
    def _ensure_connection(self) -> ccxt.Exchange:
        if self.exchange is None:
            raise ConnectionError("Exchange not connected")
        return self.exchange

    def get_account_nav(self) -> float:
        """Return the total account value from ``fetch_balance``.

        The return value is the sum of the ``total`` balances across all
        currencies.  It is only a rough approximation of the real NAV but is
        sufficient for simple monitoring purposes.
        """

        ex = self._ensure_connection()
        balance = ex.fetch_balance()
        totals = balance.get("total", {})
        if isinstance(totals, dict):
            return float(sum(v for v in totals.values() if isinstance(v, (int, float))))
        try:
            return float(totals)
        except (TypeError, ValueError):
            return 0.0

    def get_positions(self) -> Dict[str, float]:
        """Return current positions keyed by symbol."""

        ex = self._ensure_connection()
        positions: Dict[str, float] = {}
        try:
            raw_positions = ex.fetch_positions()
        except Exception as exc:  # pragma: no cover - network failure
            logging.error("Error fetching positions: %s", exc)
            return positions

        for pos in raw_positions:
            symbol = pos.get("symbol")
            size = (
                pos.get("contracts")
                or pos.get("positionAmt")
                or pos.get("size")
                or pos.get("contractSize")
            )
            if symbol and size is not None:
                try:
                    positions[symbol] = float(size)
                except (TypeError, ValueError):
                    continue
        return positions

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def place_orders(
        self, orders: Dict[str, float], order_type: str = "LMT"
    ) -> List[TradeRecord]:
        """Place a collection of orders using ``create_order``.

        Parameters
        ----------
        orders : dict
            Mapping of symbol to desired signed quantity.  Positive quantities
            represent buy orders and negative quantities sell orders.
        order_type : {"LMT", "MKT"}
            Preferred order type.  When ``"LMT"`` is requested a snapshot quote
            is fetched and used as the limit price; if unavailable the order is
            downgraded to a market order.
        """

        ex = self._ensure_connection()
        self._assert_orders_allowed()
        trade_records: List[TradeRecord] = []
        for symbol, qty in orders.items():
            side = "buy" if qty > 0 else "sell"
            amount = abs(qty)
            if amount == 0:
                continue

            price: Optional[float] = None
            ccxt_order_type = "limit"
            if order_type.upper() == "MKT":
                ccxt_order_type = "market"
            else:
                try:
                    ticker = ex.fetch_ticker(symbol)
                    price = ticker.get("last") or ticker.get("close")
                    if price is None:
                        ccxt_order_type = "market"
                except Exception as exc:  # pragma: no cover - network failure
                    logging.error("Error fetching ticker for %s: %s", symbol, exc)
                    ccxt_order_type = "market"

            if ccxt_order_type == "market":
                order = ex.create_order(symbol, ccxt_order_type, side, amount)
            else:
                order = ex.create_order(symbol, ccxt_order_type, side, amount, price)

            trade_records.append(
                TradeRecord(
                    timestamp=pd.Timestamp.utcnow().isoformat(),
                    ib_timestamp=order.get("datetime"),
                    symbol=symbol,
                    action=side.upper(),
                    quantity=amount,
                    price=price if ccxt_order_type == "limit" else None,
                    order_id=str(order.get("id")),
                    status=order.get("status", ""),
                    filled=float(order.get("filled", 0) or 0),
                    avg_fill_price=order.get("average"),
                    entry=None,
                    exit=None,
                    ret=None,
                    direction="long" if side == "buy" else "short",
                    order_type="LMT" if ccxt_order_type == "limit" else "MKT",
                )
            )
        return trade_records

    def execute_orders(
        self,
        orders: Dict[str, float],
        time_in_seconds: int,
        time_step: int = 60,
    ) -> List[TradeRecord]:
        """Execute ``orders``; currently a thin wrapper over :meth:`place_orders`.

        The ``time_in_seconds`` and ``time_step`` parameters are accepted for
        API compatibility with :class:`IBConnector` but are not used in the
        current implementation.
        """

        return self.place_orders(orders, order_type="LMT")

"""Utility functions to interact with Interactive Brokers via ``ib_insync``.

This module centralizes all logic that is specific to the Interactive Brokers
API so that scripts such as ``run_live.py`` can focus on computing target
positions.  The implementation is a thin wrapper around the :mod:`ib_insync`
package which provides convenient synchronous access to IB.

Example
-------
    >>> from pwb_toolbox.execution import create_connector
    >>> ibc = create_connector({"broker": "ib"})
    >>> ibc.connect()
    >>> nav = ibc.get_account_nav()
    >>> positions = ibc.get_positions()
    >>> ibc.disconnect()

The :class:`IBConnector` class exposes methods for obtaining account
information, retrieving current positions and submitting orders.  The order
placement logic mirrors the one previously implemented in ``run_live.py`` and
supports both market and limit orders.  For limit orders a snapshot quote is
requested and the last trade price (or closing price as a fallback) is used as
the limit price.  If no price is available the order is converted into a market
order.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import os
import statistics
import time
from typing import Dict, List, Optional, Sequence

import pandas as pd
from ib_insync import IB, LimitOrder, MarketOrder, Option, Stock

from .optimal_limit_order import get_optimal_quote
from .config import OptimalQuoteConfig
from .option_contract import OptionContract, parse_option_instrument

# Regular NYSE/Nasdaq session length; used to convert a daily volatility
# estimate into the ticks-per-sqrt-second units `get_optimal_quote` expects.
_SECONDS_PER_TRADING_SESSION = 6.5 * 3600

# Interactive Brokers listens on a different port for paper and live accounts.
# 4002 is the Gateway's paper port (this module's default) and 7497 is TWS's.
# Orders on those ports cannot move real money, so they are never gated —
# backtests, paper automation and the test suite run completely untouched.
PAPER_PORTS = frozenset({4002, 7497})

_LIVE_ORDER_ENV = "PWB_ALLOW_LIVE_ORDERS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class LiveOrderBlocked(RuntimeError):
    """Raised when a live-account order is attempted without both unlocks.

    Placing an order against a funded account is irreversible in a way nothing
    else in this package is, so it takes two independent keys that are awkward
    to supply by accident: an explicit ``allow_live_orders=True`` in the code
    *and* ``PWB_ALLOW_LIVE_ORDERS`` set in the environment. A stray import, an
    unattended scheduled run, or a config file someone flipped cannot satisfy
    both.
    """


def _env_allows_live_orders() -> bool:
    """True when ``PWB_ALLOW_LIVE_ORDERS`` is set to a truthy value."""

    return os.environ.get(_LIVE_ORDER_ENV, "").strip().lower() in _TRUTHY


def _sigma_from_closes(
    closes: Sequence[float],
    tick_size: float,
    seconds_per_session: float = _SECONDS_PER_TRADING_SESSION,
) -> Optional[float]:
    """Convert a series of daily closes into a per-instrument `sigma`.

    `sigma` is the volatility parameter (in ticks per sqrt-second) expected by
    :func:`optimal_limit_order.get_optimal_quote`. Returns ``None`` if there is
    not enough data to produce a meaningful estimate.
    """
    prices = [c for c in closes if c and c > 0]
    if len(prices) < 5 or tick_size <= 0:
        return None
    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    daily_vol = statistics.pstdev(log_returns)
    if not daily_vol:
        return None
    last_price = prices[-1]
    return (daily_vol * last_price / tick_size) / math.sqrt(seconds_per_session)


@dataclass
class TradeRecord:
    """Container for information about a single trade."""

    timestamp: str
    ib_timestamp: Optional[str]
    symbol: str
    action: str
    quantity: int
    price: Optional[float]
    order_id: int
    status: str
    filled: float
    avg_fill_price: float
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


class IBConnector:
    """Small wrapper around :class:`ib_insync.IB`.

    Parameters
    ----------
    host, port, client_id : optional
        Connection parameters passed to :meth:`ib_insync.IB.connect`.
    market_data_type : int, optional
        Market data type requested through :meth:`ib_insync.IB.reqMarketDataType`.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        market_data_type: int = 4,
        allow_live_orders: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.market_data_type = market_data_type
        self.allow_live_orders = allow_live_orders
        self.ib = IB()

    # ------------------------------------------------------------------
    # Live-order safety
    # ------------------------------------------------------------------
    def _assert_orders_allowed(self) -> None:
        """Block live-account orders unless both unlocks are present.

        Paper ports return immediately, so this is invisible to anything that
        cannot move real money. Attributes are read defensively: an instance
        built without ``__init__`` (as the tests do) fails closed rather than
        raising ``AttributeError``.
        """

        port = getattr(self, "port", None)
        if port in PAPER_PORTS:
            return

        missing = []
        if not getattr(self, "allow_live_orders", False):
            missing.append("pass allow_live_orders=True when constructing IBConnector")
        if not _env_allows_live_orders():
            missing.append(f"set {_LIVE_ORDER_ENV}=1 in the environment")
        if not missing:
            return

        raise LiveOrderBlocked(
            f"Refusing to send orders on port {port!r}, which is not a known "
            f"paper port ({sorted(PAPER_PORTS)}). To trade a funded account, "
            + " and ".join(missing)
            + ". If this port is actually a paper account, supply both unlocks "
            "as well — an unrecognised port is treated as live on purpose."
        )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """Connect to the IB gateway and set the market data type."""

        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=30)
        self.ib.reqMarketDataType(self.market_data_type)

    def disconnect(self) -> None:
        """Disconnect from IB."""

        self.ib.disconnect()

    # ------------------------------------------------------------------
    # Account information helpers
    # ------------------------------------------------------------------
    def get_account_nav(self) -> float:
        """Return the net liquidation value of the account."""

        account_nav_value = 0.0
        for item in self.ib.accountSummary():
            if item.tag == "NetLiquidation":
                account_nav_value = float(item.value)
                break
        return account_nav_value

    def get_positions(self) -> Dict[str, float]:
        """Return current IB positions keyed by symbol."""

        return {p.contract.symbol: p.position for p in self.ib.positions()}

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def _ensure_connection(self) -> None:
        """Ensure that the IB client is connected.

        Attempts to reconnect using the stored parameters when disconnected.
        Raises ``ConnectionError`` if reconnection fails.
        """

        if not self.ib.isConnected():
            try:
                self.connect()
            except Exception as exc:  # pragma: no cover - network failure
                raise ConnectionError("Unable to reconnect to IB") from exc

    def _place_order_with_reconnect(self, contract, order):
        """Place an order, reconnecting once on ``ConnectionError``."""

        self._ensure_connection()
        try:
            return self.ib.placeOrder(contract, order)
        except ConnectionError as exc:
            logging.warning("Connection error while placing order: %s", exc)
            self._ensure_connection()
            return self.ib.placeOrder(contract, order)

    # ------------------------------------------------------------------
    # Per-instrument calibration for optimal limit-order pricing
    # ------------------------------------------------------------------
    def _get_tick_size(self, contract) -> Optional[float]:
        """Return the contract's minimum price increment, or ``None`` if
        it can't be looked up (e.g. contract not qualified)."""

        try:
            details = self.ib.reqContractDetails(contract)
        except Exception as exc:  # pragma: no cover - network failure
            logging.warning(
                "Could not fetch contract details for %s: %s", contract.symbol, exc
            )
            return None
        if details and details[0].minTick and details[0].minTick > 0:
            return details[0].minTick
        return None

    def _get_sigma(
        self, contract, tick_size: float, lookback_days: int = 30
    ) -> Optional[float]:
        """Estimate this contract's volatility from recent daily bars."""

        try:
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=f"{lookback_days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
            )
        except Exception as exc:  # pragma: no cover - network failure
            logging.warning(
                "Could not fetch historical data for %s volatility estimate: %s",
                contract.symbol,
                exc,
            )
            return None
        closes = [bar.close for bar in bars]
        return _sigma_from_closes(closes, tick_size)

    def _get_quote_calibration(self, contract) -> Dict[str, float]:
        """Best-effort per-instrument calibration for `get_optimal_quote`.

        Falls back to that function's own generic defaults for anything that
        can't be estimated (e.g. no market data permission, illiquid or newly
        listed contract) rather than failing the order.
        """

        kwargs: Dict[str, float] = {}
        tick_size = self._get_tick_size(contract)
        if tick_size:
            kwargs["tick_size"] = tick_size
            sigma = self._get_sigma(contract, tick_size)
            if sigma:
                kwargs["sigma"] = sigma
        return kwargs

    def place_orders(
        self, orders: Dict[str, float], order_type: str = "LMT"
    ) -> List[TradeRecord]:
        """Place a collection of orders.

        Parameters
        ----------
        orders : dict
            Mapping of symbol to desired signed quantity.  Positive quantities
            represent buy orders and negative quantities sell orders.
        order_type : {"LMT", "MKT"}
            Preferred order type.  When ``"LMT"`` is requested a snapshot quote
            is fetched and used as the limit price; if unavailable the order is
            downgraded to a market order.

        Returns
        -------
        list of :class:`TradeRecord`
            Trade information for each successfully submitted order.
        """

        self._assert_orders_allowed()

        trade_records: List[TradeRecord] = []
        for symbol, qty in orders.items():
            contract = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(contract)

            action = "BUY" if qty > 0 else "SELL"
            quantity = abs(int(qty))
            if quantity == 0:
                continue

            price: Optional[float] = None
            if order_type.upper() == "MKT":
                order = MarketOrder(action, quantity)
            else:
                try:
                    ticker = self.ib.reqMktData(contract, "", snapshot=True)
                    self.ib.sleep(1)
                    price = ticker.last if pd.notna(ticker.last) else ticker.close
                    if price is None or pd.isna(price):
                        order = MarketOrder(action, quantity)
                    else:
                        order = LimitOrder(action, quantity, price)
                except Exception as exc:  # pragma: no cover - network failure
                    logging.error("Error fetching market data for %s: %s", symbol, exc)
                    order = MarketOrder(action, quantity)

            trade = self._place_order_with_reconnect(contract, order)
            self.ib.sleep(1)
            ib_timestamp = trade.log[-1].time.isoformat() if trade.log else None

            trade_records.append(
                TradeRecord(
                    timestamp=pd.Timestamp.utcnow().isoformat(),
                    ib_timestamp=ib_timestamp,
                    symbol=symbol,
                    action=action,
                    quantity=quantity,
                    price=price,
                    order_id=trade.order.orderId,
                    status=trade.orderStatus.status,
                    filled=trade.orderStatus.filled,
                    avg_fill_price=trade.orderStatus.avgFillPrice,
                    entry=None,
                    exit=None,
                    ret=None,
                    direction="long" if action == "BUY" else "short",
                    order_type=order.orderType,
                )
            )
        return trade_records

    def place_option_order(
        self,
        instrument: str,
        qty: int,
        order_type: str = "LMT",
        limit_price: Optional[float] = None,
        exchange: str = "SMART",
        currency: str = "USD",
        multiplier: str = "100",
    ) -> TradeRecord:
        """Place a single option order named the way a trade plan names it.

        ``place_orders`` builds :class:`~ib_insync.Stock` contracts because it
        serves the systematic side, where a strategy emits a target position
        per ticker. The speculative desk does not work that way: it names one
        contract -- ``"NVDA 02OCT26 190C"`` -- and trades it. Four of the seven
        things this desk trades are options, and until this existed none of
        them could reach a broker except by hand.

        Parameters
        ----------
        instrument : str
            Plan format (``"NVDA 02OCT26 190C"``) or an OCC symbol.
        qty : int
            Signed contract count. Positive buys, negative sells.
        order_type : {"LMT", "MKT"}
            ``"LMT"`` without ``limit_price`` fetches a snapshot quote and uses
            the midpoint, falling back to last, then close. If none is
            available the order is **not** silently downgraded to a market
            order -- an option with no quote is usually one that should not be
            traded, and a market order into an illiquid chain is how a spread
            eats a position.
        limit_price : float, optional
            Explicit limit. The plan usually has one, and it should win over
            anything fetched.

        Raises
        ------
        LiveOrderBlocked
            On a funded-account port without both unlocks -- the same brake
            that guards ``place_orders``, deliberately not bypassed here.
        ParseError
            If ``instrument`` is not an option symbol this package understands.
        ValueError
            If ``qty`` is zero, or a limit order has no usable price.
        """

        self._assert_orders_allowed()

        contract_spec: OptionContract = parse_option_instrument(instrument)
        quantity = abs(int(qty))
        if quantity == 0:
            raise ValueError(
                f"refusing to place a zero-quantity order for {instrument!r}"
            )
        action = "BUY" if qty > 0 else "SELL"

        contract = Option(
            contract_spec.underlying,
            contract_spec.ib_expiry,
            contract_spec.strike,
            contract_spec.right,
            exchange,
            currency=currency,
            multiplier=multiplier,
        )
        self.ib.qualifyContracts(contract)

        price: Optional[float] = limit_price
        if order_type.upper() == "MKT":
            order = MarketOrder(action, quantity)
            price = None
        else:
            if price is None:
                price = self._option_quote(contract)
            if price is None:
                raise ValueError(
                    f"no quote available for {contract_spec.describe()}; supply "
                    "limit_price explicitly or pass order_type='MKT' if a "
                    "market order into this chain is genuinely intended"
                )
            order = LimitOrder(action, quantity, price)

        trade = self._place_order_with_reconnect(contract, order)
        self.ib.sleep(1)
        ib_timestamp = trade.log[-1].time.isoformat() if trade.log else None

        return TradeRecord(
            timestamp=pd.Timestamp.utcnow().isoformat(),
            ib_timestamp=ib_timestamp,
            symbol=contract_spec.occ_symbol.strip(),
            action=action,
            quantity=quantity,
            price=price,
            order_id=trade.order.orderId,
            status=trade.orderStatus.status,
            filled=trade.orderStatus.filled,
            avg_fill_price=trade.orderStatus.avgFillPrice,
            entry=None,
            exit=None,
            ret=None,
            direction="long" if action == "BUY" else "short",
            order_type=order.orderType,
        )

    def _option_quote(self, contract) -> Optional[float]:
        """Midpoint if both sides are quoted, else last, else close, else None.

        Options are wide enough that the last trade can sit a long way from
        where the contract is actually offered, so the midpoint is preferred
        over the last price here -- the reverse of the share path's ordering,
        and deliberately so.
        """

        try:
            ticker = self.ib.reqMktData(contract, "", snapshot=True)
            self.ib.sleep(1)
        except Exception as exc:  # pragma: no cover - network failure
            logging.error("Error fetching option quote for %s: %s", contract, exc)
            return None

        bid, ask = getattr(ticker, "bid", None), getattr(ticker, "ask", None)
        if (
            bid is not None
            and ask is not None
            and pd.notna(bid)
            and pd.notna(ask)
            and bid > 0
            and ask > 0
        ):
            return round((bid + ask) / 2.0, 2)

        for candidate in (
            getattr(ticker, "last", None),
            getattr(ticker, "close", None),
        ):
            if candidate is not None and pd.notna(candidate) and candidate > 0:
                return round(float(candidate), 2)
        return None

    def execute_orders(
        self,
        orders: Dict[str, float],
        time_in_seconds: int,
        time_step: int = 60,
    ) -> List[TradeRecord]:
        """Execute ``orders`` using the optimal limit order strategy.

        The execution algorithm follows the strategy implemented in
        :func:`optimal_limit_order.get_optimal_quote`.  For each symbol a
        sequence of limit orders is submitted.  Orders are refreshed every
        ``time_step`` seconds with the remaining quantity and the time left to
        trade.  Any residual quantity after ``time_in_seconds`` is sent as a
        market order.

        Parameters
        ----------
        orders
            Mapping of symbol to desired signed quantity.  Positive quantities
            represent buy orders and negative quantities sell orders.
        time_in_seconds
            Maximum time allowed to execute the orders.
        time_step
            Refresh interval for the limit orders in seconds.

        Returns
        -------
        list of :class:`TradeRecord`
            Trade information for each submitted order (including refreshes and
            the final market order if necessary).
        """

        self._assert_orders_allowed()

        trade_records: List[TradeRecord] = []
        start_time = time.time()
        deadline = start_time + time_in_seconds

        # Prepare contracts and remaining quantities for all symbols
        order_info: Dict[str, Dict[str, object]] = {}
        for symbol, qty in orders.items():
            remaining_qty = abs(int(qty))
            if remaining_qty <= 0:
                continue
            contract = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            action = "BUY" if qty > 0 else "SELL"
            order_info[symbol] = {
                "contract": contract,
                "action": action,
                "remaining_qty": remaining_qty,
                # Estimated once per symbol per call; sigma/tick_size don't
                # meaningfully change over the life of a single execution run.
                "calibration": self._get_quote_calibration(contract),
            }

        while time.time() < deadline and any(
            info["remaining_qty"] > 0 for info in order_info.values()
        ):
            placed_orders: Dict[str, tuple] = {}
            for symbol, info in order_info.items():
                remaining_qty = int(info["remaining_qty"])
                if remaining_qty <= 0:
                    continue

                contract = info["contract"]
                action = str(info["action"])
                remaining_time = max(int(deadline - time.time()), 0)

                # Obtain a snapshot to compute the mid price
                try:
                    ticker = self.ib.reqMktData(contract, "", snapshot=True)
                    self.ib.sleep(1)
                    mid_price = None
                    if (
                        ticker.bid is not None
                        and ticker.ask is not None
                        and pd.notna(ticker.bid)
                        and pd.notna(ticker.ask)
                    ):
                        mid_price = (ticker.bid + ticker.ask) / 2
                    if mid_price is None or pd.isna(mid_price):
                        mid_price = (
                            ticker.last if pd.notna(ticker.last) else ticker.close
                        )
                except Exception:  # pragma: no cover - network failure
                    mid_price = None

                if mid_price is None or pd.isna(mid_price):
                    order = MarketOrder(action, remaining_qty)
                    price: Optional[float] = None
                else:
                    config = OptimalQuoteConfig(
                        symbol=symbol,
                        quantity=remaining_qty,
                        time_in_seconds=remaining_time,
                        **info["calibration"],
                    )
                    quote = get_optimal_quote(config)
                    price = mid_price - quote if action == "BUY" else mid_price + quote
                    if not (math.isfinite(quote) and math.isfinite(price)):
                        order = MarketOrder(action, remaining_qty)
                        price = None
                    else:
                        order = LimitOrder(action, remaining_qty, price)

                trade = self._place_order_with_reconnect(contract, order)
                placed_orders[symbol] = (trade, price, order)

            # Allow orders to work for the specified time step
            self.ib.sleep(time_step)

            for symbol, (trade, price, order) in placed_orders.items():
                info = order_info[symbol]
                action = str(info["action"])
                filled = int(trade.orderStatus.filled)
                info["remaining_qty"] = max(int(info["remaining_qty"]) - filled, 0)
                ib_timestamp = trade.log[-1].time.isoformat() if trade.log else None

                trade_records.append(
                    TradeRecord(
                        timestamp=pd.Timestamp.utcnow().isoformat(),
                        ib_timestamp=ib_timestamp,
                        symbol=symbol,
                        action=action,
                        quantity=filled if filled else int(info["remaining_qty"]),
                        price=price,
                        order_id=trade.order.orderId,
                        status=trade.orderStatus.status,
                        filled=trade.orderStatus.filled,
                        avg_fill_price=trade.orderStatus.avgFillPrice,
                        entry=None,
                        exit=None,
                        ret=None,
                        direction="long" if action == "BUY" else "short",
                        order_type=order.orderType,
                    )
                )

                if info["remaining_qty"] > 0 and trade.orderStatus.status not in {
                    "Filled",
                    "Cancelled",
                }:
                    try:
                        self.ib.cancelOrder(order)
                    except Exception:  # pragma: no cover - network failure
                        pass

        # Send market orders for any remaining quantities
        for symbol, info in order_info.items():
            remaining_qty = int(info["remaining_qty"])
            if remaining_qty <= 0:
                continue
            contract = info["contract"]
            action = str(info["action"])
            order = MarketOrder(action, remaining_qty)
            trade = self._place_order_with_reconnect(contract, order)
            self.ib.sleep(1)
            ib_timestamp = trade.log[-1].time.isoformat() if trade.log else None
            trade_records.append(
                TradeRecord(
                    timestamp=pd.Timestamp.utcnow().isoformat(),
                    ib_timestamp=ib_timestamp,
                    symbol=symbol,
                    action=action,
                    quantity=remaining_qty,
                    price=None,
                    order_id=trade.order.orderId,
                    status=trade.orderStatus.status,
                    filled=trade.orderStatus.filled,
                    avg_fill_price=trade.orderStatus.avgFillPrice,
                    entry=None,
                    exit=None,
                    ret=None,
                    direction="long" if action == "BUY" else "short",
                    order_type=order.orderType,
                )
            )

        return trade_records

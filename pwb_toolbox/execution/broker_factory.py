"""Factory for execution connectors.

Call :func:`create_connector` with a configuration dictionary or rely on
``PWB_*`` environment variables.  Supported configuration keys:

General
-------
``broker`` (``PWB_BROKER``): ``"ib"`` or ``"ccxt"``

Interactive Brokers
-------------------
``host`` (``PWB_IB_HOST``)
``port`` (``PWB_IB_PORT``)
``client_id`` (``PWB_IB_CLIENT_ID``)
``market_data_type`` (``PWB_IB_MARKET_DATA_TYPE``)

CCXT Exchanges
--------------
``exchange`` (``PWB_CCXT_EXCHANGE``)
``api_key`` (``PWB_CCXT_API_KEY``)
``api_secret`` (``PWB_CCXT_API_SECRET``)
``params`` (no env var)

Example
-------
    >>> from pwb_toolbox.execution import create_connector
    >>> conn = create_connector({"broker": "ccxt", "exchange": "binance"})
    >>> conn.connect()
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Union

from .ib_connector import IBConnector
from .ccxt_connector import CCXTConnector

Connector = Union[IBConnector, CCXTConnector]


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _live_orders_requested(cfg: Dict[str, object]) -> bool:
    """Read the *first* live-order key, from the config mapping only.

    Deliberately never consulted from the environment. The second key already
    is an environment variable, and a factory that read this one from the
    environment too would let a single exported variable satisfy both — which
    is exactly the accident the two-key design exists to prevent.
    """

    return bool(cfg.get("allow_live_orders", False))


def create_connector(config: Optional[Dict[str, Any]] = None) -> Connector:
    """Instantiate a connector based on a config mapping or environment.

    Parameters
    ----------
    config : dict, optional
        Configuration values overriding environment variables.

    Returns
    -------
    :class:`IBConnector` or :class:`CCXTConnector`
    """

    cfg = dict(config or {})
    broker = (cfg.get("broker") or os.getenv("PWB_BROKER", "ib")).lower()

    if broker == "ib":
        host = cfg.get("host", os.getenv("PWB_IB_HOST", "127.0.0.1"))
        port = int(cfg.get("port", os.getenv("PWB_IB_PORT", 7497)))
        client_id = int(cfg.get("client_id", os.getenv("PWB_IB_CLIENT_ID", 1)))
        market_data_type = int(
            cfg.get("market_data_type", os.getenv("PWB_IB_MARKET_DATA_TYPE", 4))
        )
        return IBConnector(
            host=host,
            port=port,
            client_id=client_id,
            market_data_type=market_data_type,
            allow_live_orders=_live_orders_requested(cfg),
        )

    if broker == "ccxt":
        exchange = cfg.get("exchange", os.getenv("PWB_CCXT_EXCHANGE"))
        if not exchange:
            raise ValueError("CCXT connector requires 'exchange' setting")
        api_key = cfg.get("api_key", os.getenv("PWB_CCXT_API_KEY"))
        api_secret = cfg.get("api_secret", os.getenv("PWB_CCXT_API_SECRET"))
        params = cfg.get("params")
        sandbox = bool(
            cfg.get(
                "sandbox", os.getenv("PWB_CCXT_SANDBOX", "").strip().lower() in _TRUTHY
            )
        )
        return CCXTConnector(
            exchange,
            api_key=api_key,
            api_secret=api_secret,
            params=params,
            sandbox=sandbox,
            allow_live_orders=_live_orders_requested(cfg),
        )

    raise ValueError(f"Unknown broker '{broker}'")

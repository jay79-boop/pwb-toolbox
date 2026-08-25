"""The live-order brake shared by every broker connector.

Placing an order against a funded account is irreversible in a way nothing else
in this package is, so it takes **two independent keys** that are awkward to
supply by accident:

* an explicit ``allow_live_orders=True`` in the calling code, and
* ``PWB_ALLOW_LIVE_ORDERS`` set in the environment.

A stray import, an unattended scheduled run, or a config file someone flipped
satisfies neither alone.

These primitives live here rather than in one connector so the connectors cannot
drift apart on the question of what "live" means. ``ib_connector`` re-exports
them for backwards compatibility, and each connector supplies its own notion of
what counts as a non-funded account (a paper port for IB, sandbox mode for
CCXT) plus its own error text.
"""

from __future__ import annotations

import os
from typing import List

# The second key. Deliberately shared across connectors: it is the "yes, this
# machine may trade real money" switch, not a per-broker one. The first key is
# per-connector, so unlocking IB never silently unlocks CCXT.
LIVE_ORDER_ENV = "PWB_ALLOW_LIVE_ORDERS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class LiveOrderBlocked(RuntimeError):
    """Raised when a live-account order is attempted without both unlocks.

    The message always names *both* remedies. An error that does not say how to
    proceed just gets worked around, and a guardrail that gets worked around
    protects nothing.
    """


def env_allows_live_orders() -> bool:
    """True when ``PWB_ALLOW_LIVE_ORDERS`` is set to a truthy value."""

    return os.environ.get(LIVE_ORDER_ENV, "").strip().lower() in _TRUTHY


def missing_unlocks(allow_live_orders: bool, code_remedy: str) -> List[str]:
    """Return the remedies still needed, in the order a caller should apply them.

    An empty list means both keys are present. ``code_remedy`` is the
    connector-specific phrasing for the first key, e.g. "pass
    ``allow_live_orders=True`` when constructing CCXTConnector".
    """

    missing: List[str] = []
    if not allow_live_orders:
        missing.append(code_remedy)
    if not env_allows_live_orders():
        missing.append(f"set {LIVE_ORDER_ENV}=1 in the environment")
    return missing

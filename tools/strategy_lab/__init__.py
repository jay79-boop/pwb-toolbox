"""Strategy Lab — a live dashboard for strategy test runs.

`store` holds validated run records, `server` serves them next to the dashboard,
and `record` builds records from the things in this repo that produce trades.
"""

from .record import build, from_reversal_sim, post
from .store import SCHEMA, RunStore, ValidationError, validate

__all__ = [
    "SCHEMA",
    "RunStore",
    "ValidationError",
    "build",
    "from_reversal_sim",
    "post",
    "validate",
]

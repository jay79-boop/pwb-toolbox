"""Building run records, and posting them at a running lab.

Anything that can write JSON can feed the dashboard; this module exists so the
things already in this repo do not each reinvent the shape.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .store import SCHEMA, validate

# Currency per index point, for the runs where a dollar figure is wanted at all.
POINT_VALUES = {
    "NQ": 20.0,
    "MNQ": 2.0,
    "ES": 50.0,
    "MES": 5.0,
    "RTY": 50.0,
    "YM": 5.0,
    "MYM": 0.5,
}


def point_value_for(symbol: str | None) -> float | None:
    """Best guess at a contract's point value from its ticker.

    Longest root first, so MNQ is not read as NQ — the difference is a factor of
    ten in every dollar figure on the page.
    """
    if not symbol:
        return None
    upper = symbol.upper()
    for root in sorted(POINT_VALUES, key=len, reverse=True):
        if re.search(rf"(^|[^A-Z]){root}([^A-Z]|$)", upper):
            return POINT_VALUES[root]
    return None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "run"


def content_digest(record: Mapping[str, Any]) -> str:
    """Six hex characters standing for what this run actually is.

    Two runs posted inside the same second — a parameter sweep, or a session
    firing several configurations at once — used to land on the same id, and the
    later one silently replaced the earlier. Folding the content in means a
    different configuration is a different run, while re-posting an identical one
    lands on the same id and overwrites itself, which is what re-running a
    backtest should do.
    """
    material = json.dumps(
        {
            "strategy": record.get("strategy"),
            "symbol": record.get("symbol"),
            "timeframe": record.get("timeframe"),
            "session": record.get("session"),
            "params": record.get("params"),
            "period": record.get("period"),
            "funnel": record.get("funnel"),
            "trades": record.get("trades"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:6]


def make_id(strategy: str, digest: str = "") -> str:
    """A run's id: what it is, not when it was posted.

    Keying on content rather than a timestamp means re-running the same backtest
    updates its own entry instead of adding a near-identical twin, while any
    change — a parameter, a bar, a trade — is a genuinely new run. Ordering comes
    from `recorded_at`, which is what actually carries the time.
    """
    tail = f"-{digest}" if digest else ""
    return slug(strategy)[: 128 - len(tail)] + tail


def build(
    strategy: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    run_id: str | None = None,
    source: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    session: str | None = None,
    period: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    funnel: Mapping[str, int] | None = None,
    point_value: float | None = None,
    notes: str | None = None,
) -> dict:
    """Assemble and validate a run record. Raises ValidationError if it is wrong."""
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "id": "",  # filled in below, once the content it stands for is assembled
        "strategy": strategy,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trades": list(trades),
    }
    optional = {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "session": session,
        "period": dict(period) if period else None,
        "params": dict(params) if params else None,
        "funnel": dict(funnel) if funnel else None,
        "point_value": (
            point_value if point_value is not None else point_value_for(symbol)
        ),
        "notes": notes,
    }
    record.update({k: v for k, v in optional.items() if v is not None})
    record["id"] = run_id or make_id(strategy, content_digest(record))
    return validate(record)


def from_reversal_sim(results: Iterable[Any], config: Any, **kwargs) -> dict:
    """Turn `tools.reversal_15m_sim` day results into a run record.

    The funnel is the part worth carrying across: it is what distinguishes a run
    that found no setups from a chart that had no 09:15 bar to look at.
    """
    results = list(results)
    trades = []
    for r in results:
        t = r.trade
        if t is None:
            continue
        trades.append(
            {
                "day": t.day.isoformat(),
                "direction": t.direction,
                "setup_ts": t.setup_ts.isoformat(timespec="minutes"),
                "entry_ts": t.entry_ts.isoformat(timespec="minutes"),
                "entry": round(t.entry, 6),
                "exit_ts": t.exit_ts.isoformat(timespec="minutes"),
                "exit": round(t.exit, 6),
                "target": round(t.target, 6),
                "stop": round(t.stop, 6),
                "reason": t.reason,
                "r": round(t.r_multiple, 6),
                "points": round(t.points, 6),
            }
        )

    days = [r.day for r in results]
    kwargs.setdefault(
        "period",
        (
            {"start": min(days).isoformat(), "end": max(days).isoformat()}
            if days
            else None
        ),
    )
    kwargs.setdefault(
        "params",
        {
            "candle1": config.candle1.strftime("%H:%M"),
            "flatten": config.flatten.strftime("%H:%M"),
            "sma_length": config.sma_length,
            "use_sma": config.use_sma,
            "skip_friday": config.skip_friday,
            "reward_risk": config.reward_risk,
        },
    )
    kwargs.setdefault(
        "funnel",
        {
            "days": len(results),
            "days_with_candle_1": sum(1 for r in results if r.candle1),
            "days_committed": sum(1 for r in results if r.committed_direction),
            "trades": len(trades),
        },
    )
    kwargs.setdefault("source", "tools/reversal_15m_sim.py")
    return build(kwargs.pop("strategy", "15-Minute Reversal"), trades, **kwargs)


def post(
    record: Mapping[str, Any],
    url: str = "http://127.0.0.1:8771/api/runs",
    timeout: float = 10.0,
) -> dict:
    """Send a record to a running lab. Returns the server's reply."""
    body = json.dumps(record).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The lab's rejection message is the useful part; without it the caller
        # only learns "400" and has to guess which field was wrong.
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"lab rejected the run ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"no lab answering at {url} — start one with `python -m tools.strategy_lab` ({exc.reason})"
        ) from exc

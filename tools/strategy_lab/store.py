"""The run store: validated strategy-run records on disk, newest first.

A *run record* is the contract between anything that tests a strategy and the
dashboard that displays it. Any session, script or notebook that can write JSON
can feed the lab; nothing needs to import this package.

Deliberately one JSON file per run in one directory. That makes a run diffable,
greppable, individually deletable, and safe to write from two processes at once —
none of which is true of a single appended file, which is what a store like this
usually decays into.

Standard library only, so the server that reads it has nothing to install.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "pwb.strategy-run/1"

# A run's id becomes a filename, so it is restricted rather than escaped.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

MAX_TRADES = 100_000


class ValidationError(ValueError):
    """A record was rejected. The message is safe to show a caller."""


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ValidationError(message)


def _clean_text(value: Any, field: str, limit: int = 400) -> str:
    _require(isinstance(value, str), f"{field} must be a string")
    # Control characters would corrupt a terminal and survive into the page as
    # invisible junk; strip the category rather than blacklisting characters.
    text = "".join(c for c in value if unicodedata.category(c)[0] != "C").strip()
    _require(text != "", f"{field} must not be empty")
    _require(len(text) <= limit, f"{field} must be at most {limit} characters")
    return text


def _number(value: Any, field: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{field} must be a number",
    )
    value = float(value)
    # NaN and infinity survive json.loads but poison every downstream average.
    _require(
        value == value and value not in (float("inf"), float("-inf")),
        f"{field} must be finite",
    )
    return value


def validate_trade(raw: Any, index: int) -> dict:
    where = f"trades[{index}]"
    _require(isinstance(raw, dict), f"{where} must be an object")

    trade = {
        "day": _clean_text(raw.get("day"), f"{where}.day", 32),
        "direction": raw.get("direction"),
        "r": _number(raw.get("r"), f"{where}.r"),
    }
    _require(trade["direction"] in (1, -1), f"{where}.direction must be 1 or -1")

    for key in ("entry", "exit", "target", "stop", "points"):
        if raw.get(key) is not None:
            trade[key] = _number(raw[key], f"{where}.{key}")
    for key in ("setup_ts", "entry_ts", "exit_ts", "reason"):
        if raw.get(key) is not None:
            trade[key] = _clean_text(raw[key], f"{where}.{key}", 64)
    return trade


def validate(raw: Any) -> dict:
    """Return a normalized run record, or raise ValidationError.

    Unknown top-level keys are dropped rather than rejected: a future producer
    that adds a field should not be refused by an older lab.
    """
    _require(isinstance(raw, dict), "a run record must be a JSON object")
    _require(raw.get("schema") == SCHEMA, f"schema must be {SCHEMA!r}")

    run_id = _clean_text(raw.get("id"), "id", 128)
    _require(
        bool(ID_RE.match(run_id)), "id must be letters, digits, dot, dash or underscore"
    )

    trades_raw = raw.get("trades")
    _require(isinstance(trades_raw, list), "trades must be a list")
    _require(len(trades_raw) <= MAX_TRADES, f"at most {MAX_TRADES} trades")

    record: dict[str, Any] = {
        "schema": SCHEMA,
        "id": run_id,
        "strategy": _clean_text(raw.get("strategy"), "strategy", 200),
        "trades": [validate_trade(t, i) for i, t in enumerate(trades_raw)],
    }

    for key in ("recorded_at", "source", "symbol", "timeframe", "session", "notes"):
        if raw.get(key) is not None:
            record[key] = _clean_text(raw[key], key, 2000 if key == "notes" else 200)

    if raw.get("point_value") is not None:
        record["point_value"] = _number(raw["point_value"], "point_value")

    period = raw.get("period")
    if isinstance(period, dict):
        record["period"] = {
            k: _clean_text(period[k], f"period.{k}", 32)
            for k in ("start", "end")
            if period.get(k) is not None
        }

    funnel = raw.get("funnel")
    if isinstance(funnel, dict):
        record["funnel"] = {
            str(k)[:40]: int(_number(v, f"funnel.{k}"))
            for k, v in funnel.items()
            if isinstance(v, (int, float))
        }

    params = raw.get("params")
    if isinstance(params, dict):
        # Params are shown as a caption, so they are flattened to scalars here
        # rather than letting an arbitrary nested object reach the page.
        record["params"] = {
            str(k)[:40]: v
            for k, v in params.items()
            if isinstance(v, (str, int, float, bool)) and not isinstance(k, bool)
        }

    return record


@dataclass
class RunStore:
    """A directory of run records."""

    directory: Path

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not ID_RE.match(run_id):
            raise ValidationError("bad run id")
        return self.directory / f"{run_id}.json"

    def save(self, raw: Any) -> dict:
        record = validate(raw)
        path = self._path(record["id"])
        # Write beside the target and rename, so a reader never sees half a file.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        return record

    def get(self, run_id: str) -> dict | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        try:
            return validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValidationError, json.JSONDecodeError):
            return None

    def delete(self, run_id: str) -> bool:
        path = self._path(run_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.json"))

    def all(self) -> list[dict]:
        """Every valid record, most recently recorded first.

        Sorted by `recorded_at` rather than by id, because an id says what a run
        is, not when it happened. Unreadable files are skipped: a corrupt record
        must not take the dashboard down with it — the run that matters is
        usually the one just written, not the one that rotted.
        """
        out = []
        for run_id in self.ids():
            record = self.get(run_id)
            if record is not None:
                out.append(record)
        out.sort(key=lambda r: (r.get("recorded_at", ""), r["id"]), reverse=True)
        return out

    def index(self) -> list[dict]:
        """Light listing for the run picker: no trade rows."""
        return [
            {k: v for k, v in r.items() if k != "trades"}
            | {"trade_count": len(r["trades"])}
            for r in self.all()
        ]


def load_many(paths: Iterable[Path]) -> list[dict]:
    return [validate(json.loads(Path(p).read_text(encoding="utf-8"))) for p in paths]

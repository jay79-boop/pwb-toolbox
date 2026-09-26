#!/usr/bin/env python
"""Finviz Research Dashboard -- a local web dashboard over finviz_scan.

Serves a single-page dashboard and a small JSON API from the Python standard
library only: zero new dependencies, no build step. Every number on the page is
derived at request time from finviz_scan's research verbs (screener, lookup,
watchlist signals) or from Finviz/Yahoo directly -- never from a written-down
snapshot, so the page cannot go stale the way the old dashboard did.

Everything that talks to Finviz or Yahoo lives in finviz_scan (imported below)
or in the two small fetchers defined here (`_fetch_map`, `_fetch_dated_bars`).
A short in-memory TTL cache keeps free-tier Finviz rate-limit friendly:
fundamentals 15 min, screener/map 10 min, news 5 min.

Run it:
    python tools/finviz_dash.py                 # serves on :8721, opens browser
    python tools/finviz_dash.py --no-browser    # for headless testing / curl
    python tools/finviz_dash.py --port 9000     # pick your own port

Use `tools/start_finviz_dash.ps1` (or its Desktop shortcut) for the no-brainer
launch.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

# Anchored to this file (repo root), not the CWD -- finviz_scan must import as
# a sibling module regardless of how the server is launched.
REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import finviz_scan  # noqa: E402  (all research verbs; it never runs CLI code on import)
import pandas as pd  # noqa: E402  (tiny glue frame for watchlist_signals)

DEFAULT_PORT = 8721
UI_DIR = REPO_ROOT / "tools" / "finviz_dash_ui"
UI_ROOT = UI_DIR.resolve()
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Cache TTLs, in seconds.
TTL_SCREENER = 10 * 60
TTL_MAP = 10 * 60

# One lock around every network fetch: the server is multi-threaded and free
# Finviz rate-limits hard, so serializing plenty is what keeps it alive.
_NETWORK_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


class _TTLCache:
    """Thread-safe in-memory cache with per-key TTLs.

    `get(key)` returns (value, stale); stale is False for a fresh value and
    True for one that is past its TTL but still being served as a fallback --
    a rate-limited fetch must not blank the page.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float):
        with self._lock:
            item = self._data.get(key)
        if item is None:
            return None
        value, stored = item
        return value, (time.monotonic() - stored) > ttl

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic())


CACHE = _TTLCache()


def _cached_fetch(key: str, ttl: float, fetch):
    """Return (payload, stale). Fetch fresh on a miss or expired key; on a
    fetch failure, serve whatever is cached (flagged stale) or re-raise."""
    hit = CACHE.get(key, ttl)
    if hit is not None:
        value, stale = hit
        if not stale:
            return value, False
        try:
            with _NETWORK_LOCK:
                fresh = fetch()
        except Exception as exc:  # rate limited / offline -- keep last good
            if isinstance(value, dict):
                value = dict(value)
                value["stale"] = True
                value["stale_reason"] = str(exc)
            return value, True
        CACHE.set(key, fresh)
        return fresh, False
    with _NETWORK_LOCK:
        fresh = fetch()
    CACHE.set(key, fresh)
    return fresh, False


# ---------------------------------------------------------------------------
# Small local fetchers (besides finviz_scan, the only network touchpoints)
# ---------------------------------------------------------------------------


def _et_str() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%a %b %d %H:%M ET")


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _market_status() -> dict:
    """'pre' / 'open' / 'after' / 'closed', from Eastern time."""
    now = datetime.now(ZoneInfo("America/New_York"))
    wd = now.weekday()
    t = now.time()
    if wd >= 5:
        status, label = "closed", "Market closed (weekend)"
    elif t < dtime(9, 30):
        status, label = "pre", "Pre-market"
    elif t < dtime(16, 0):
        status, label = "open", "Market open"
    else:
        status, label = "after", "After hours"
    return {
        "status": status,
        "label": label,
        "et": _et_str(),
    }


def _fetch_map() -> dict:
    """S&P 500 heat map, rebuilt from the screener. Finviz retired the plain
    text map.asc feed (404 since Sept 2026), so the map is derived from the
    same live source the screener preset buttons use: one request for the
    whole index, cached 10 min. Same output shape as the old feed, per tile:
    ticker, name, industry, price, daily change %, short market cap.
    """
    frame = finviz_scan.fetch_screener(
        {"Index": "S&P 500"}, limit=500, order="Ticker", ascend=True
    )
    sectors: dict[str, dict] = {}
    skipped = 0
    for _, row in frame.iterrows():
        ticker = str(row.get("Ticker") or "").strip().upper()
        if not ticker:
            skipped += 1
            continue
        change_txt = str(row.get("Change %") or "0%")
        change_txt = (
            change_txt.replace("+", "").replace("%", "").replace("−", "-").strip()
        )
        try:
            change_f = float(change_txt)
        except ValueError:
            change_f = 0.0
        if change_f != change_f:  # NaN change -- still show the tile, flat
            change_f = 0.0
        try:
            price_f = float(row.get("Price"))
        except (TypeError, ValueError):
            price_f = math.nan
        tile = {
            "t": ticker,
            "n": str(row.get("Company") or ticker),
            "i": str(row.get("Industry") or ""),
            "p": price_f,
            "c": round(change_f, 2),
            "mc": finviz_scan.human_number(row.get("Market Cap")),
        }
        bucket = sectors.setdefault(
            str(row.get("Sector") or "Uncategorized"),
            {
                "sector": str(row.get("Sector") or "Uncategorized"),
                "tiles": [],
                "changes": [],
            },
        )
        bucket["tiles"].append(tile)
        bucket["changes"].append(change_f)

    ordered = []
    for key, bucket in sectors.items():
        tiles = bucket["tiles"]
        avg = (
            sum(bucket["changes"]) / len(bucket["changes"])
            if bucket["changes"]
            else 0.0
        )
        ordered.append(
            {
                "sector": key,
                "avg_change": round(avg, 2),
                "count": len(tiles),
                "tiles": tiles,
            }
        )
    ordered.sort(key=lambda s: s["count"], reverse=True)
    return {"sectors": ordered, "skipped": skipped, "fetched_at": _et_str()}


def _fetch_dated_bars(symbol: str, days: int):
    """Daily OHLCV with dates for the dashboard, unlike finviz_scan's
    `fetch_bars`, which intentionally drops the DatetimeIndex for its math.
    Returns a list of bar dicts {t, o, h, l, c, v}, oldest first."""
    import yfinance as yf

    raw = yf.download(
        symbol,
        period=f"{days}d",
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    raw = raw.reset_index()  # DatetimeIndex -> 'Date' column
    # yfinance returns a MultiIndex ('Close', 'AAPL') for a single symbol;
    # flatten to the price part so row.get("close") works again.
    if hasattr(raw.columns, "nlevels") and raw.columns.nlevels > 1:
        raw.columns = [str(c[0]).lower() for c in raw.columns]
    else:
        raw.columns = [str(c).lower() for c in raw.columns]
    out = []
    for _, row in raw.iterrows():
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue  # no close for the day -- skip
        if close != close or close == 0:  # NaN / zero -- skip the day
            continue
        try:
            o = float(row.get("open"))
            h = float(row.get("high"))
            l = float(row.get("low"))
        except (TypeError, ValueError):
            continue  # sparse row (e.g. halted day with OHLC missing) -- skip
        if o != o or h != h or l != l:  # NaN OHLC -- same skip
            continue
        out.append(
            {
                "t": str(row.get("date", row.get("Date", "")))[:10],
                "o": o,
                "h": h,
                "l": l,
                "c": close,
                "v": int(row.get("volume", 0) or 0),
            }
        )
    return out


# ---------------------------------------------------------------------------
# API payload builders (derive at read time; no stored numbers)
# ---------------------------------------------------------------------------


def _signal_flags(sig: dict) -> tuple[str, list[str]]:
    """The CLI's rule: a symbol with 2+ same-direction signals is flagged for
    a look. Returns (direction, human labels for the chips)."""
    c = sig.get("confluence", {})
    bull, bear = c.get("bullish_count", 0), c.get("bearish_count", 0)
    labels = []
    if sig.get("ema_cross") == "bullish":
        labels.append("EMA20/50 up-cross")
    if sig.get("ema_cross") == "bearish":
        labels.append("EMA20/50 down-cross")
    if sig.get("ema80_react") == "bounce":
        labels.append("80-EMA bounce")
    if sig.get("ema80_react") == "reject":
        labels.append("80-EMA reject")
    if sig.get("rsi_signal") == "oversold":
        labels.append("RSI oversold")
    if sig.get("rsi_signal") == "overbought":
        labels.append("RSI overbought")
    if sig.get("ma_cross") == "golden":
        labels.append("Golden cross")
    if sig.get("ma_cross") == "death":
        labels.append("Death cross")
    if sig.get("near_52w") == "high":
        labels.append("Near 52w high")
    if sig.get("near_52w") == "low":
        labels.append("Near 52w low")
    if bull > bear and max(bull, bear) >= 2:
        direction = "bullish"
    elif bear > bull and max(bull, bear) >= 2:
        direction = "bearish"
    else:
        direction = "mixed"
    return direction, labels


def _watchlist_payload() -> dict:
    symbols = finviz_scan.load_watchlist(Path(finviz_scan.DEFAULT_WATCHLIST_FILE))
    base = {
        "fetched_at": _et_str(),
        "market": _market_status(),
        "flagged": [],
        "items": [],
        "watchlist_path": str(Path(finviz_scan.DEFAULT_WATCHLIST_FILE)),
    }
    if not symbols:
        return {**base, "empty": True}
    bars = finviz_scan.fetch_bars(symbols, days=finviz_scan.WATCHLIST_HISTORY_DAYS)
    items = []
    flagged = set()
    for sym in symbols:
        entry = {"ticker": sym, "signals": None, "spark": None, "error": None}
        df = bars.get(sym)
        if df is None:
            entry["error"] = "No price data (check the spelling, or it may be delisted)"
            items.append(entry)
            continue
        try:
            sig = finviz_scan.watchlist_signals(df)
        except ValueError as exc:
            entry["error"] = str(exc)
            items.append(entry)
            continue
        sig["confluence"] = finviz_scan.classify_confluence(sig)
        closes = df["close"].astype(float).tolist()
        last, prev = closes[-1], closes[-2]
        change = last - prev
        pct = (change / prev * 100) if prev else 0.0
        c = sig["confluence"]
        if max(c.get("bullish_count", 0), c.get("bearish_count", 0)) >= 2:
            flagged.add(sym)
        direction, labels = _signal_flags(sig)
        entry.update(
            {
                "price": round(last, 2),
                "change": round(change, 2),
                "change_pct": round(pct, 2),
                "spark": [round(x, 2) for x in closes[-60:]],
                "signals": sig,
                "direction": direction,
                "signal_labels": labels,
            }
        )
        items.append(entry)
    return {
        **base,
        "flag_reason": "Two or more signals line up the same way today",
        "flagged": sorted(flagged),
        "items": items,
    }


def _map_payload() -> dict:
    return _cached_fetch("map", TTL_MAP, _fetch_map)[0]


def _presets_payload() -> list:
    return [
        {
            "name": name,
            "filters": filters,
            "labels": "  ".join(f"{k} = {v}" for k, v in filters.items()),
        }
        for name, filters in sorted(finviz_scan.PRESETS.items())
    ]


# Big enough that no preset is silently cut (large_cap_uptrend alone is
# several hundred names); rows arrive largest-company-first, so if a hand-typed
# filter ever does hit the cap, what is dropped is the smallest names, never
# the back half of the alphabet.
SCREENER_MAX_FETCH = 1000


def _screener_filters(preset: str | None, custom: list[str]) -> dict:
    """Preset filters overlaid with hand-typed "Name=Value" ones -- the same
    rule as the CLI's `screener --preset X --filter ...`."""
    if preset and preset not in finviz_scan.PRESETS:
        raise ValueError(f"Unknown preset '{preset}'.")
    filters = dict(finviz_scan.PRESETS[preset]) if preset else {}
    for raw in custom:
        name, value = finviz_scan.parse_filter_kv(raw)
        filters[name] = value
    return filters


def _screener_payload(
    preset: str | None,
    custom: list[str] | None = None,
    max_fetch: int = SCREENER_MAX_FETCH,
) -> dict:
    custom = [c for c in (custom or []) if c.strip()]
    if not preset and not custom:
        return {"preset": None, "presets": _presets_payload(), "count": 0, "rows": []}
    filters = _screener_filters(preset, custom)

    def fetch():
        df = finviz_scan.fetch_screener(
            filters, order="Market Cap.", limit=max_fetch, ascend=False
        )
        fetched = (
            0 if df is None else len(df)
        )  # finvizfinance returns None on 0 matches
        if preset in finviz_scan.STOCK_ONLY_PRESETS:
            df = finviz_scan.exclude_etfs(df)
        rows = [] if df is None else df.to_dict(orient="records")
        return {"rows": rows, "fetched": fetched}

    key = "screener:" + json.dumps(filters, sort_keys=True) + f":{preset}"
    result, _stale = _cached_fetch(key, TTL_SCREENER, fetch)
    return {
        "preset": preset,
        "custom": custom,
        "filters": filters,
        "count": len(result["rows"]),
        "truncated": result["fetched"] >= max_fetch,
        "rows": result["rows"],
        "fetched_at": _et_str(),
    }


def _screener_export(preset: str | None, custom: list[str] | None = None) -> dict:
    payload = _screener_payload(preset, custom)
    if payload["count"] == 0:
        raise ValueError("No rows to save for this screen.")
    out_dir = finviz_scan.default_desktop_dir() / "finviz-research"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"screener_{preset or 'custom'}_{_stamp()}.csv"
    finviz_scan._save_csv(pd.DataFrame(payload["rows"]), str(path))
    return {"ok": True, "path": str(path), "count": payload["count"]}


def _ticker_payload(sym: str) -> dict:
    sym = sym.upper().strip()
    if not sym:
        raise ValueError("No ticker given.")
    result = finviz_scan.fetch_lookup(sym)  # fundamentals + news + insiders

    fundament = dict(result.get("fundament") or {})
    ordered = {f: fundament[f] for f in finviz_scan.FUNDAMENT_FIELDS if f in fundament}

    news = result.get("news")
    news_rows = (
        news.to_dict(orient="records") if news is not None and not news.empty else []
    )
    insiders = result.get("insiders")
    insider_rows = (
        insiders.to_dict(orient="records")
        if insiders is not None and not insiders.empty
        else []
    )

    # Real bars + signals for the chart; same math and source as `check`.
    bars = _fetch_dated_bars(sym, 400)
    signals = None
    signals_error = None
    if bars:
        frame = pd.DataFrame(bars)[["o", "h", "l", "c", "v"]]
        frame.columns = ["open", "high", "low", "close", "volume"]
        try:
            sig = finviz_scan.watchlist_signals(frame)
            sig["confluence"] = finviz_scan.classify_confluence(sig)
            signals = sig
        except Exception as exc:  # too little history, etc.
            signals_error = str(exc)

    return {
        "ticker": sym,
        "fetched_at": _et_str(),
        "market": _market_status(),
        "fundament": ordered,
        "news": news_rows,
        "news_error": result.get("news_error"),
        "insiders": insider_rows,
        "insiders_error": result.get("insiders_error"),
        "signals": signals,
        "signals_error": signals_error,
        "bars": bars[-260:],  # ~a year for the big chart
        "spark": [b["c"] for b in bars[-60:]] if bars else [],
    }


def _watchlist_add(ticker: str) -> dict:
    path = Path(finviz_scan.DEFAULT_WATCHLIST_FILE)
    with _NETWORK_LOCK:
        symbols = finviz_scan.load_watchlist(path)
        symbols = sorted(set(symbols) | {ticker.upper()})
        finviz_scan.save_watchlist(path, symbols)
    return {"ok": True, "count": len(symbols), "ticker": ticker.upper()}


def _watchlist_remove(ticker: str) -> dict:
    path = Path(finviz_scan.DEFAULT_WATCHLIST_FILE)
    with _NETWORK_LOCK:
        symbols = finviz_scan.load_watchlist(path)
        symbols = sorted(set(symbols) - {ticker.upper()})
        finviz_scan.save_watchlist(path, symbols)
    return {"ok": True, "count": len(symbols), "ticker": ticker.upper()}


def _open_text_menu() -> dict:
    """Open the original numbered terminal menu in its own console window, so
    the browser user can fall back to the no-graphics tool without leaving
    the dashboard running."""
    launcher = REPO_ROOT / "tools" / "start_finviz.ps1"
    if not launcher.exists():
        raise ValueError("start_finviz.ps1 not found next to the dashboard.")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
        ],
        **kwargs,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def _json_safe(value):
    """NaN/inf -> None, recursively, so the page gets null (shown as a dash)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "FinvizDash/1.0"

    def log_message(self, _fmt, *_args):  # keep the console window quiet
        pass

    # -- helpers -------------------------------------------------------

    def _send_json(self, payload, status=200) -> None:
        # allow_nan=False after _json_safe: Python writes NaN by default, which
        # Python reads back fine but a browser's JSON.parse rejects outright --
        # a single blank P/E used to fail the whole screener response.
        body = json.dumps(_json_safe(payload), default=str, allow_nan=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status=500) -> None:
        self._log_error(message)
        self._send_json({"ok": False, "error": message}, status=status)

    def _log_error(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}\n"
        try:
            out = finviz_scan.default_desktop_dir() / "finviz-research" / "error.log"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass  # never let logging break a request
        print(line, end="", file=sys.stderr)

    def _serve_static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        # Serve only files under the UI folder -- no path traversal.
        if "/" in name or "\\" in name or "\x00" in name:
            self._send_error("Bad path.", 404)
            return
        candidate = (UI_DIR / name).resolve()
        if not (candidate == UI_ROOT or UI_ROOT in candidate.parents):
            self._send_error("Bad path.", 404)
            return
        if not candidate.is_file():
            self._send_error("Not found.", 404)
            return
        body = candidate.read_bytes()
        ctype = _CONTENT_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    # -- routes --------------------------------------------------------

    def _route_api(self, parsed) -> None:
        parts = [p for p in parsed.path.split("/") if p]
        if not parts or parts[0] != "api":
            self._send_error("Not found.", 404)
            return
        action = parts[1] if len(parts) > 1 else ""
        query = self._query()
        try:
            if action == "watchlist":
                self._send_json(_watchlist_payload())
            elif action == "presets":
                self._send_json({"presets": _presets_payload()})
            elif action == "screener":
                preset = (query.get("preset") or [None])[0]
                self._send_json(_screener_payload(preset, query.get("f")))
            elif action == "screener-export":
                preset = (query.get("preset") or [None])[0]
                if not preset and not query.get("f"):
                    raise ValueError(
                        "A preset or a filter is required for screener-export."
                    )
                self._send_json(_screener_export(preset, query.get("f")))
            elif action == "map":
                self._send_json(_map_payload())
            elif action == "ticker" and len(parts) >= 3:
                self._send_json(_ticker_payload(parts[2]))
            elif action == "watchlist-add":
                t = (query.get("t") or [None])[0]
                self._send_json(
                    _watchlist_add(t)
                    if t
                    else {"ok": False, "error": "No ticker given."}
                )
            elif action == "watchlist-remove":
                t = (query.get("t") or [None])[0]
                self._send_json(
                    _watchlist_remove(t)
                    if t
                    else {"ok": False, "error": "No ticker given."}
                )
            elif action == "text-menu":
                self._send_json(_open_text_menu())
            else:
                self._send_error(
                    "Unknown API action. Try /api/watchlist, /api/map, /api/presets, "
                    "/api/screener, /api/screener-export, /api/watchlist-add, "
                    "/api/watchlist-remove, /api/ticker/SYM, /api/text-menu.",
                    404,
                )
        except Exception as exc:
            self._send_error(f"{type(exc).__name__}: {exc}")

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._route_api(parsed)
        else:
            self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._route_api(parsed)
        else:
            self._send_error("Not found.", 404)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        prog="finviz_dash",
        description="Local web dashboard over Finviz research (finviz_scan).",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FINVIZ_DASH_PORT", DEFAULT_PORT)),
    )
    ap.add_argument(
        "--no-browser", action="store_true", help="do not open a browser tab"
    )
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Finviz Research Dashboard -> {url}")
    print(
        "Press Ctrl+C to stop. Errors land in finviz-research/error.log on your Desktop."
    )
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

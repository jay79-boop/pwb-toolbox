#!/usr/bin/env python
"""Seasonality that has to earn its place: calendar patterns tested, not assumed.

Sector rotation folklore says energy runs in spring, September is weak, the
market sells in May. Some of that is real, most of it is memory dressed as
measurement — scan 15 tickers by 12 months and ~9 "patterns" appear by pure
luck. This tool batches any universe of tickers, measures every ticker-month
cell on decades of daily closes, and then makes each finding survive three
gates before it is allowed to call itself a pattern:

    permutation   the cell's mean return against 2,000 within-year shuffles
                  of the same ticker's own monthly history — "how often does
                  luck alone look this good?"
    split-half    the effect must point the same way in the first half of the
                  years and the second half, independently. A pattern that
                  died in 2010 is history, not seasonality.
    FDR           Benjamini-Hochberg across every cell scanned, because the
                  scan is a fishing expedition and must be priced as one.

Named almanac claims (sell in May, September weakness, the January effect)
are treated differently, and better: they were stated before we looked, so
each faces only its own one-sided test, no FDR — and gets a public verdict,
HELD or FAILED, on this universe. The failed ones are the discipline: they
are the list of things to stop believing.

Tiers, so nothing is hidden and nothing is oversold:

    CONVICTED   passed all three gates with >= MIN_YEARS of history
    CANDIDATE   raw p < .05 but failed a gate — watch, do not trade
    NOISE       everything else, shown faintly so the eye calibrates

Outputs: a self-contained visual report (heatmap, average-year paths, the
now-window screener, folklore verdicts) at season/season-report.html; a
sectioned TradingView watchlist at season/tradingview-watchlist.txt; and
season/season.json for other tools (the pre-trade pack, the night lab) to
read. `context SYMBOL` answers "where does today sit in this ticker's year?"

All statistics are pure functions over dicts of daily closes and are tested
on synthetic data with planted effects (tests/test_season_scan.py). Only
`fetch` touches the network, and only on the owner's machine — the cloud
proxy blocks Yahoo.

Examples::

    python tools/season_scan.py fetch                  # default universe, ~max history
    python tools/season_scan.py report                 # compute + render everything
    python tools/season_scan.py watchlist              # TradingView import file
    python tools/season_scan.py context XLE            # today's seasonal position
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "season"
DATA_DIR_NAME = "data"
REPORT_NAME = "season-report.html"
JSON_NAME = "season.json"
WATCHLIST_NAME = "tradingview-watchlist.txt"
UNIVERSE_NAME = "universe.txt"

# Sectors are the rotation itself; indexes are the baseline every sector
# pattern is judged against; crypto is included because the owner asked and
# excluded from conviction because ten years of history cannot clear the
# year-count gate — the report says so rather than quietly passing it.
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC"]
INDEXES = ["SPY", "QQQ", "IWM", "DIA"]
CRYPTO = ["BTC-USD", "ETH-USD"]
DEFAULT_UNIVERSE = SECTORS + INDEXES + CRYPTO

MIN_YEARS = 15  # below this a cell can be CANDIDATE at best
PERMUTATIONS = 2000
FDR_Q = 0.10
ENTER_LEAVE_DAYS = 14  # "entering/leaving" horizon for the now-window screener

CONVICTED, CANDIDATE, NOISE = "convicted", "candidate", "noise"

MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# ---------------------------------------------------------------------------
# The almanac on trial. Each claim is pre-registered — stated by the folklore
# before we looked at the data — so it faces its own one-sided test and no
# FDR. `months` is the claimed window, `direction` the claimed sign.
# ---------------------------------------------------------------------------

FOLKLORE = [
    {
        "name": "Best six months (Nov-Apr strong)",
        "symbol": "SPY",
        "months": [11, 12, 1, 2, 3, 4],
        "direction": "up",
        "source": "Stock Trader's Almanac (Hirsch), the Halloween indicator",
    },
    {
        "name": "Sell in May (May-Oct weak)",
        "symbol": "SPY",
        "months": [5, 6, 7, 8, 9, 10],
        "direction": "down",
        "source": "Bouman & Jacobsen, AER 2002",
    },
    {
        "name": "September weakness",
        "symbol": "SPY",
        "months": [9],
        "direction": "down",
        "source": "the most persistent single-month effect in the US record",
    },
    {
        "name": "Santa / December strength",
        "symbol": "SPY",
        "months": [12],
        "direction": "up",
        "source": "Stock Trader's Almanac",
    },
    {
        "name": "January effect (small caps)",
        "symbol": "IWM",
        "months": [1],
        "direction": "up",
        "source": "Keim 1983; widely reported as faded since",
    },
    {
        "name": "Energy spring run (Feb-Apr)",
        "symbol": "XLE",
        "months": [2, 3, 4],
        "direction": "up",
        "source": "Stock Trader's Almanac sector seasonality",
    },
]


# ---------------------------------------------------------------------------
# Return arithmetic — everything downstream reads these dicts
# ---------------------------------------------------------------------------


def monthly_log_returns(closes: dict[date, float]) -> dict[tuple[int, int], float]:
    """Sum of daily log returns per (year, month), in chronological order."""
    days = sorted(closes)
    out: dict[tuple[int, int], float] = {}
    for prev, day in zip(days, days[1:]):
        if closes[prev] <= 0 or closes[day] <= 0:
            continue
        r = math.log(closes[day] / closes[prev])
        key = (day.year, day.month)
        out[key] = out.get(key, 0.0) + r
    return out


def month_series(monthly: dict[tuple[int, int], float], month: int) -> list[float]:
    return [monthly[k] for k in sorted(monthly) if k[1] == month]


def perm_pvalue(
    monthly: dict[tuple[int, int], float],
    months: list[int],
    seed: int = 7,
    permutations: int = PERMUTATIONS,
) -> tuple[float, float]:
    """Observed mean of the window's months, and its two-sided permutation p.

    The null shuffles the months WITHIN each year: 2008 stays a terrible
    year with its damage spread across its own twelve months, volatility
    regimes survive, and only the calendar alignment is destroyed. "Could a
    randomly relabeled calendar look this seasonal?"

    (A circular shift of the whole monthly series -- the obvious first
    answer -- is wrong twice over for this statistic: shifts by multiples
    of 12 land the calendar back on itself, and every other shift maps all
    of a month's slots onto one single other month, collapsing the null to
    eleven distinct values. A merely top-ranked month then reads as
    p=0.0005. The within-year shuffle has none of that.)
    """
    by_year: dict[int, list[float]] = {}
    for (year, _month), value in sorted(monthly.items()):
        by_year.setdefault(year, []).append(value)
    year_labels = {
        year: [m for (y, m) in sorted(monthly) if y == year] for year in by_year
    }

    def window_mean(assignment: dict[int, list[float]]) -> float:
        picked = [
            v
            for year, values in assignment.items()
            for v, m in zip(values, year_labels[year])
            if m in months
        ]
        return sum(picked) / len(picked) if picked else 0.0

    n_in = sum(1 for k in monthly if k[1] in months)
    if n_in == 0 or n_in == len(monthly):
        return 0.0, 1.0

    observed = window_mean(by_year)
    rng = random.Random(seed)
    hits = 0
    shuffled = {year: list(vals) for year, vals in by_year.items()}
    for _ in range(permutations):
        for vals in shuffled.values():
            rng.shuffle(vals)
        if abs(window_mean(shuffled)) >= abs(observed):
            hits += 1
    return observed, (hits + 1) / (permutations + 1)


def split_half(monthly: dict[tuple[int, int], float], months: list[int]) -> dict:
    """The window's mean in the older half of the years vs the newer half.

    Agreement means the same sign in both halves independently — the cheap,
    honest test for a pattern that stopped working a decade ago.
    """
    years = sorted({k[0] for k in monthly})
    if len(years) < 4:
        return {"first": 0.0, "second": 0.0, "agree": False}
    cut = years[len(years) // 2]

    def half_mean(pred) -> float:
        vals = [v for (y, m), v in monthly.items() if m in months and pred(y)]
        return sum(vals) / len(vals) if vals else 0.0

    first = half_mean(lambda y: y < cut)
    second = half_mean(lambda y: y >= cut)
    return {
        "first": first,
        "second": second,
        "agree": first != 0.0 and second != 0.0 and (first > 0) == (second > 0),
    }


def bh_fdr(pvalues: list[float], q: float = FDR_Q) -> list[bool]:
    """Benjamini-Hochberg: which of these p-values survive at FDR q?

    The grid is a fishing expedition — 15 tickers by 12 months is 180 casts —
    and this is what pricing it as one looks like.
    """
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    passing = [False] * n
    threshold_rank = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= q * rank / n:
            threshold_rank = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= threshold_rank:
            passing[idx] = True
    return passing


# ---------------------------------------------------------------------------
# Cells and tiers
# ---------------------------------------------------------------------------


def cell_stats(closes: dict[date, float], symbol: str) -> list[dict]:
    """Every month's raw evidence for one ticker. Tiers are assigned later,
    after FDR has seen the whole grid."""
    monthly = monthly_log_returns(closes)
    out = []
    for month in range(1, 13):
        series = month_series(monthly, month)
        if not series:
            continue
        mean, p = perm_pvalue(monthly, [month])
        halves = split_half(monthly, [month])
        out.append(
            {
                "symbol": symbol,
                "month": month,
                "mean": mean,
                "mean_pct": round(100 * (math.exp(mean) - 1), 2),
                "n_years": len(series),
                "hit_rate": round(sum(1 for r in series if r > 0) / len(series), 2),
                "p": round(p, 4),
                "half_first_pct": round(100 * (math.exp(halves["first"]) - 1), 2),
                "half_second_pct": round(100 * (math.exp(halves["second"]) - 1), 2),
                "halves_agree": halves["agree"],
            }
        )
    return out


def assign_tiers(
    cells: list[dict], q: float = FDR_Q, min_years: int = MIN_YEARS
) -> None:
    """Tier every cell in place, with FDR run across the whole scanned grid."""
    fdr_pass = bh_fdr([c["p"] for c in cells], q)
    for cell, passed in zip(cells, fdr_pass):
        cell["fdr_pass"] = passed
        enough_history = cell["n_years"] >= min_years
        if passed and cell["halves_agree"] and enough_history:
            cell["tier"] = CONVICTED
        elif cell["p"] < 0.05:
            cell["tier"] = CANDIDATE
            if not enough_history:
                cell["tier_note"] = f"only {cell['n_years']}y of history"
            elif not cell["halves_agree"]:
                cell["tier_note"] = "halves disagree — may have died"
            else:
                cell["tier_note"] = "did not survive FDR across the grid"
        else:
            cell["tier"] = NOISE


def judge_folklore(all_closes: dict[str, dict[date, float]]) -> list[dict]:
    """Each pre-registered claim, tried on its own terms: one-sided, no FDR."""
    verdicts = []
    for claim in FOLKLORE:
        closes = all_closes.get(claim["symbol"])
        out = dict(claim)
        if not closes:
            out["verdict"] = "NO DATA"
            verdicts.append(out)
            continue
        monthly = monthly_log_returns(closes)
        mean, p_two = perm_pvalue(monthly, claim["months"])
        expected_sign = 1 if claim["direction"] == "up" else -1
        right_way = (mean > 0) == (expected_sign > 0)
        # One-sided: half the two-sided p when the effect points the claimed
        # way; if it points the wrong way the claim has simply failed.
        p_one = p_two / 2 if right_way else 1 - p_two / 2
        halves = split_half(monthly, claim["months"])
        years = len({k[0] for k in monthly})
        out.update(
            mean_pct=round(100 * (math.exp(mean) - 1), 2),
            p=round(p_one, 4),
            n_years=years,
            halves_agree=halves["agree"],
        )
        if years < 10:
            out["verdict"] = "INSUFFICIENT"
        elif right_way and p_one < 0.05 and halves["agree"]:
            out["verdict"] = "HELD"
        else:
            out["verdict"] = "FAILED"
        verdicts.append(out)
    return verdicts


# ---------------------------------------------------------------------------
# The now-window screener — what is actionable this week
# ---------------------------------------------------------------------------


def _month_window(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return start, end


def now_windows(
    cells: list[dict], today: date, horizon: int = ENTER_LEAVE_DAYS
) -> dict:
    """CONVICTED cells sorted into in / entering / leaving relative to today."""
    out = {"in": [], "entering": [], "leaving": []}
    for cell in cells:
        if cell.get("tier") != CONVICTED:
            continue
        month = cell["month"]
        year = today.year
        start, end = _month_window(year, month)
        if start > today + timedelta(days=horizon):
            start, end = _month_window(year - 1, month)
        entry = {
            "symbol": cell["symbol"],
            "month": MONTH_NAMES[month - 1],
            "direction": "bullish" if cell["mean"] > 0 else "bearish",
            "mean_pct": cell["mean_pct"],
            "hit_rate": cell["hit_rate"],
        }
        if start <= today <= end:
            days_left = (end - today).days
            entry["days_left"] = days_left
            (out["leaving"] if days_left <= horizon else out["in"]).append(entry)
        else:
            nstart, _ = _month_window(year + (month < today.month), month)
            days_until = (nstart - today).days
            if 0 < days_until <= horizon:
                entry["days_until"] = days_until
                out["entering"].append(entry)
    for bucket in out.values():
        bucket.sort(key=lambda e: -abs(e["mean_pct"]))
    return out


# ---------------------------------------------------------------------------
# The average year, drawn — the runs and dips as a path
# ---------------------------------------------------------------------------


def average_year_path(closes: dict[date, float], buckets: int = 73) -> dict:
    """Mean cumulative log return across the year, older half vs newer half.

    73 five-day buckets of the calendar year keep the path smooth enough to
    read and honest enough to show where the runs and the dips actually sit.
    Two overlaid halves make regime death visible in the picture itself.
    """
    days = sorted(closes)
    if len(days) < 300:
        return {"old": [], "new": []}
    years = sorted({d.year for d in days})
    cut = years[len(years) // 2]
    sums = {"old": [0.0] * buckets, "new": [0.0] * buckets}
    counts = {"old": [0] * buckets, "new": [0] * buckets}
    for prev, day in zip(days, days[1:]):
        if closes[prev] <= 0 or closes[day] <= 0 or day.year != prev.year:
            continue
        r = math.log(closes[day] / closes[prev])
        b = min(buckets - 1, (day.timetuple().tm_yday - 1) * buckets // 366)
        half = "old" if day.year < cut else "new"
        sums[half][b] += r
        counts[half][b] += 1
    out = {}
    for half in ("old", "new"):
        n_years = max(1, len([y for y in years if (y < cut) == (half == "old")]))
        path, cum = [], 0.0
        for b in range(buckets):
            cum += sums[half][b] / n_years
            path.append(round(100 * (math.exp(cum) - 1), 2))
        out[half] = path
    return out


# ---------------------------------------------------------------------------
# IO — CSVs of daily closes in, artifacts out
# ---------------------------------------------------------------------------


def season_dir(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else DEFAULT_DIR


def read_closes(path: Path) -> dict[date, float]:
    closes: dict[date, float] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            raw = row.get("timestamp") or row.get("date")
            try:
                closes[datetime.fromisoformat(str(raw)).date()] = float(row["close"])
            except (TypeError, ValueError):
                continue
    return closes


def load_universe(base: Path, extra: list[str] | None) -> list[str]:
    """Default universe, plus season/universe.txt (one ticker a line, # for
    comments) — which is where the owner's own watchlist names live."""
    symbols = list(DEFAULT_UNIVERSE)
    user_file = base / UNIVERSE_NAME
    if user_file.exists():
        for line in user_file.read_text(encoding="utf-8").splitlines():
            line = line.strip().upper()
            if line and not line.startswith("#") and line not in symbols:
                symbols.append(line)
    for sym in extra or []:
        sym = sym.strip().upper()
        if sym and sym not in symbols:
            symbols.append(sym)
    return symbols


def load_all_closes(base: Path, symbols: list[str]) -> dict[str, dict[date, float]]:
    out = {}
    data_dir = base / DATA_DIR_NAME
    for sym in symbols:
        path = data_dir / f"{sym}.csv"
        if path.exists():
            closes = read_closes(path)
            if closes:
                out[sym] = closes
    return out


def compute(all_closes: dict[str, dict[date, float]], today: date) -> dict:
    """The whole scan: cells, tiers, folklore, now-windows, paths."""
    cells: list[dict] = []
    for sym in sorted(all_closes):
        cells.extend(cell_stats(all_closes[sym], sym))
    assign_tiers(cells)
    return {
        "generated": today.isoformat(),
        "symbols": sorted(all_closes),
        "cells": cells,
        "folklore": judge_folklore(all_closes),
        "now": now_windows(cells, today),
        "paths": {
            sym: average_year_path(all_closes[sym]) for sym in sorted(all_closes)
        },
        "gates": {
            "permutations": PERMUTATIONS,
            "fdr_q": FDR_Q,
            "min_years": MIN_YEARS,
        },
    }


# ---------------------------------------------------------------------------
# TradingView watchlist — sectioned, import-ready
# ---------------------------------------------------------------------------


def render_watchlist(scan: dict) -> str:
    """###Section headers are TradingView's own import format. Sections are
    ordered by how actionable they are today; every symbol appears once, in
    its most actionable section."""
    now = scan["now"]
    placed: set[str] = set()
    lines: list[str] = []

    def section(title: str, entries: list[str]) -> None:
        fresh = [s for s in entries if s not in placed]
        if not fresh:
            return
        lines.append(f"###{title}")
        lines.extend(fresh)
        placed.update(fresh)

    section(
        "IN SEASON NOW", [e["symbol"] for e in now["in"] if e["direction"] == "bullish"]
    )
    section(
        "ENTERING SOON",
        [e["symbol"] for e in now["entering"] if e["direction"] == "bullish"],
    )
    section("LEAVING - TIGHTEN UP", [e["symbol"] for e in now["leaving"]])
    section(
        "SEASONALLY WEAK NOW",
        [e["symbol"] for e in now["in"] if e["direction"] == "bearish"],
    )
    convicted_syms = sorted(
        {c["symbol"] for c in scan["cells"] if c["tier"] == CONVICTED}
    )
    section("HAS A PROVEN SEASON (REST OF YEAR)", convicted_syms)
    section("SCANNED - NOTHING PROVEN", [s for s in scan["symbols"]])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# fetch — the only network code, and it only runs on the owner's machine
# ---------------------------------------------------------------------------


def fetch_all(symbols: list[str], base: Path) -> int:
    try:
        import yfinance as yf
    except ImportError:
        print("fetch needs yfinance:  python -m pip install yfinance")
        return 1
    data_dir = base / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for sym in symbols:
        frame = yf.download(
            sym, period="max", interval="1d", progress=False, auto_adjust=True
        )
        if frame is None or frame.empty:
            print(f"  {sym}: nothing returned")
            failures += 1
            continue
        if hasattr(frame.columns, "levels"):
            frame.columns = frame.columns.get_level_values(0)
        path = data_dir / f"{sym}.csv"
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["timestamp", "close"])
            for ts, row in frame.iterrows():
                writer.writerow([ts.strftime("%Y-%m-%d"), float(row["Close"])])
        print(f"  {sym}: {len(frame)} daily closes")
    print(f"{len(symbols) - failures}/{len(symbols)} fetched into {data_dir}")
    return 0 if failures < len(symbols) else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_fetch(args):
    base = season_dir(args.dir)
    return fetch_all(load_universe(base, args.symbols), base)


def cmd_report(args):
    base = season_dir(args.dir)
    symbols = load_universe(base, args.symbols)
    all_closes = load_all_closes(base, symbols)
    if not all_closes:
        print(
            f"No data under {base / DATA_DIR_NAME}. Run `season_scan.py fetch` first."
        )
        return 1
    scan = compute(all_closes, date.today())
    (base / JSON_NAME).write_text(
        json.dumps(scan, ensure_ascii=False), encoding="utf-8"
    )
    report = base / REPORT_NAME
    report.write_text(render_report(scan), encoding="utf-8")
    watchlist = base / WATCHLIST_NAME
    watchlist.write_text(render_watchlist(scan), encoding="utf-8")
    convicted = sum(1 for c in scan["cells"] if c["tier"] == CONVICTED)
    held = sum(1 for f in scan["folklore"] if f["verdict"] == "HELD")
    print(
        f"{len(scan['cells'])} cells over {len(all_closes)} tickers: "
        f"{convicted} convicted; folklore {held} held / "
        f"{sum(1 for f in scan['folklore'] if f['verdict'] == 'FAILED')} failed."
    )
    print(f"Report:    {report}")
    print(f"Watchlist: {watchlist}")
    return 0


def cmd_watchlist(args):
    base = season_dir(args.dir)
    scan_path = base / JSON_NAME
    if not scan_path.exists():
        print(f"No scan at {scan_path}. Run `season_scan.py report` first.")
        return 1
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    out = base / WATCHLIST_NAME
    out.write_text(render_watchlist(scan), encoding="utf-8")
    print(f"Watchlist: {out}")
    return 0


def cmd_context(args):
    base = season_dir(args.dir)
    scan_path = base / JSON_NAME
    if not scan_path.exists():
        print(f"No scan at {scan_path}. Run `season_scan.py report` first.")
        return 1
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    sym = args.symbol.upper()
    today = date.today()
    cells = [c for c in scan["cells"] if c["symbol"] == sym]
    if not cells:
        print(f"{sym} is not in the scan.")
        return 1
    current = next((c for c in cells if c["month"] == today.month), None)
    if current is None:
        print(f"{sym}: no data for {MONTH_NAMES[today.month - 1]}.")
        return 0
    tier = current["tier"]
    line = (
        f"{sym} in {MONTH_NAMES[today.month - 1]}: {current['mean_pct']:+}% avg "
        f"over {current['n_years']}y, hit rate {current['hit_rate']:.0%}, "
        f"p={current['p']}"
    )
    if tier == CONVICTED:
        side = "strong" if current["mean"] > 0 else "weak"
        print(f"{line} — CONVICTED {side} month.")
    elif tier == CANDIDATE:
        print(f"{line} — candidate only ({current.get('tier_note', 'unproven')}).")
    else:
        print(f"{line} — noise; the calendar says nothing here.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def shared(p):
        p.add_argument("--dir", help=f"season directory (default {DEFAULT_DIR})")
        p.add_argument("--symbols", nargs="*", help="extra tickers beyond the universe")

    p = sub.add_parser("fetch", help="download daily closes for the universe")
    shared(p)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("report", help="compute the scan; write report, json, watchlist")
    shared(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser(
        "watchlist", help="rewrite the TradingView watchlist from the scan"
    )
    shared(p)
    p.set_defaults(func=cmd_watchlist)

    p = sub.add_parser("context", help="where today sits in one ticker's seasonal year")
    p.add_argument("symbol")
    p.add_argument("--dir", help=f"season directory (default {DEFAULT_DIR})")
    p.set_defaults(func=cmd_context)

    args = ap.parse_args(argv)
    return args.func(args)


# ---------------------------------------------------------------------------
# The report — one self-contained file, charts first, no server, no build
# ---------------------------------------------------------------------------

# Diverging scale: teal for strength, red for weakness, gray at nothing —
# the repo's validated gain/loss pair as the poles (protan dE 12, checked
# with the dataviz validator). Near-zero cells deliberately read as gray;
# conviction is never carried by tint alone but by printed values, bold
# text and an outline, so the page survives colorblindness and grayscale.
_REPORT_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Season Scan</title>
<style>
  :root {
    --ink: #1c1917; --ink-2: #57534e; --ink-3: #a8a29e;
    --bg: #fafaf9; --card: #ffffff; --line: #e7e5e4;
    --up: #0d9488; --down: #ef4444; --mid: #f0efec;
  }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--bg); color: var(--ink);
         font: 15px/1.5 "Segoe UI", system-ui, sans-serif; padding: 24px; }
  h1 { font-size: 22px; } h2 { font-size: 16px; margin: 28px 0 10px; }
  .sub { color: var(--ink-2); margin: 4px 0 18px; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 10px; padding: 12px 14px; flex: 1 1 220px; }
  .card h3 { font-size: 13px; color: var(--ink-2); text-transform: uppercase;
             letter-spacing: .04em; margin-bottom: 8px; }
  .entry { display: flex; justify-content: space-between; gap: 8px;
           padding: 3px 0; border-bottom: 1px dashed var(--line); }
  .entry:last-child { border-bottom: 0; }
  .chip { font-weight: 600; }
  .chip.up::before { content: "\\25B2 "; color: var(--up); }
  .chip.down::before { content: "\\25BC "; color: var(--down); }
  .muted { color: var(--ink-3); }
  .scroll { overflow-x: auto; background: var(--card);
            border: 1px solid var(--line); border-radius: 10px; padding: 10px; }
  table { border-collapse: separate; border-spacing: 2px; }
  th { font-size: 12px; color: var(--ink-2); font-weight: 600; padding: 2px 6px;
       text-align: center; }
  th.sym { text-align: right; padding-right: 10px; }
  td.cell { min-width: 52px; height: 30px; text-align: center; font-size: 12px;
            border-radius: 4px; color: var(--ink); }
  td.cell.convicted { font-weight: 700; outline: 2px solid var(--ink); }
  td.cell.candidate { font-style: italic; }
  td.cell.noise { color: transparent; }
  td.cell.noise:hover { color: var(--ink-3); }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 13px;
            color: var(--ink-2); margin: 8px 2px; }
  .legend .swatch { display: inline-block; width: 12px; height: 12px;
                    border-radius: 3px; vertical-align: -1px; margin-right: 5px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
          gap: 12px; }
  .panel { background: var(--card); border: 1px solid var(--line);
           border-radius: 10px; padding: 10px 12px; }
  .panel h4 { font-size: 13px; margin-bottom: 4px; }
  svg { display: block; width: 100%; height: 90px; }
  .verdict { font-weight: 700; }
  .verdict.held { color: var(--up); } .verdict.failed { color: var(--down); }
  .folk td, .folk th { padding: 6px 10px; font-size: 13px; text-align: left; }
  .folk tr + tr td { border-top: 1px solid var(--line); }
  .tip { position: fixed; pointer-events: none; background: var(--ink);
         color: #fafaf9; font-size: 12px; padding: 4px 8px; border-radius: 6px;
         display: none; z-index: 9; }
  .foot { color: var(--ink-3); font-size: 12px; margin-top: 26px;
          max-width: 72ch; }
</style>
</head>
<body>
<h1>Season Scan</h1>
<p class="sub" id="sub"></p>

<h2>This week</h2>
<div class="cards" id="now"></div>

<h2>The year, ticker by month</h2>
<div class="legend">
  <span><span class="swatch" style="background:#0d9488"></span>strong month</span>
  <span><span class="swatch" style="background:#f0efec;border:1px solid #e7e5e4"></span>nothing</span>
  <span><span class="swatch" style="background:#ef4444"></span>weak month</span>
  <span><b>bold + outline</b> = convicted (all three gates)</span>
  <span><i>italic</i> = candidate (p&lt;.05, failed a gate)</span>
  <span class="muted">blank = noise &mdash; hover any cell for its full stats</span>
</div>
<div class="scroll"><table id="heat"></table></div>

<h2>The average year, drawn &mdash; runs and dips</h2>
<div class="legend">
  <span><span class="swatch" style="background:#0d9488"></span>newer half of the years</span>
  <span><span class="swatch" style="background:#a8a29e"></span>older half</span>
  <span class="muted">two lines that disagree = a pattern that died</span>
</div>
<div class="grid" id="paths"></div>

<h2>Folklore on trial</h2>
<div class="scroll"><table class="folk" id="folk"></table></div>

<h2>Candidates &mdash; watch, do not trade</h2>
<div class="scroll"><table class="folk" id="cand"></table></div>

<p class="foot" id="foot"></p>
<div class="tip" id="tip"></div>

<script>
const DATA = __DATA__;
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* Diverging tint: mean% clamped to +/-3, gray at zero. */
function mix(a, b, t) {
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const ch = (sh) => Math.round(((pa >> sh & 255) * (1 - t)) + ((pb >> sh & 255) * t));
  return "rgb(" + ch(16) + "," + ch(8) + "," + ch(0) + ")";
}
function tint(pct) {
  const t = Math.min(1, Math.abs(pct) / 3);
  return mix("#f0efec", pct >= 0 ? "#0d9488" : "#ef4444", t * 0.85);
}

/* Header line */
document.getElementById("sub").textContent =
  DATA.symbols.length + " tickers, generated " + DATA.generated +
  " - gates: " + DATA.gates.permutations + " permutations, both halves must agree, FDR q=" +
  DATA.gates.fdr_q + ", >=" + DATA.gates.min_years + "y for conviction";

/* Now panel */
(function () {
  const holder = document.getElementById("now");
  const spec = [["IN SEASON NOW", "in"], ["ENTERING SOON", "entering"], ["LEAVING", "leaving"]];
  for (const [title, key] of spec) {
    const card = el("div", "card");
    card.appendChild(el("h3", "", title));
    const entries = DATA.now[key];
    if (!entries.length) card.appendChild(el("div", "muted", "nothing convicted"));
    for (const e of entries) {
      const row = el("div", "entry");
      row.appendChild(el("span", "chip " + (e.direction === "bullish" ? "up" : "down"),
        e.symbol + " " + e.direction));
      const when = e.days_left !== undefined ? e.days_left + "d left"
                 : e.days_until !== undefined ? "in " + e.days_until + "d" : e.month;
      row.appendChild(el("span", "muted",
        e.month + " " + (e.mean_pct >= 0 ? "+" : "") + e.mean_pct + "% - " + when));
      card.appendChild(row);
    }
    holder.appendChild(card);
  }
})();

/* Heatmap - the table IS the chart, so it is also the accessible view */
(function () {
  const table = document.getElementById("heat");
  const head = el("tr");
  head.appendChild(el("th", "sym", ""));
  for (const m of MONTHS) head.appendChild(el("th", "", m));
  table.appendChild(head);
  const bySym = {};
  for (const c of DATA.cells) (bySym[c.symbol] = bySym[c.symbol] || {})[c.month] = c;
  for (const sym of DATA.symbols) {
    const row = el("tr");
    row.appendChild(el("th", "sym", sym));
    for (let m = 1; m <= 12; m++) {
      const c = (bySym[sym] || {})[m];
      const td = el("td", "cell " + (c ? c.tier : "noise"),
        c ? (c.mean_pct >= 0 ? "+" : "") + c.mean_pct.toFixed(1) : "");
      if (c) {
        td.style.background = tint(c.mean_pct);
        td.dataset.tip = sym + " " + MONTHS[m - 1] + ": " +
          (c.mean_pct >= 0 ? "+" : "") + c.mean_pct + "% avg over " + c.n_years +
          "y | hit " + Math.round(c.hit_rate * 100) + "% | p=" + c.p +
          " | halves " + c.half_first_pct + "% / " + c.half_second_pct + "%" +
          (c.tier_note ? " | " + c.tier_note : "");
      }
      row.appendChild(td);
    }
    table.appendChild(row);
  }
})();

/* Average-year small multiples */
(function () {
  const holder = document.getElementById("paths");
  for (const sym of DATA.symbols) {
    const path = DATA.paths[sym];
    if (!path || !path.new.length) continue;
    const panel = el("div", "panel");
    panel.appendChild(el("h4", "", sym));
    const all = path.old.concat(path.new);
    const lo = Math.min(0, ...all), hi = Math.max(0, ...all);
    const W = 240, H = 90, PAD = 4;
    const x = (i, n) => PAD + i * (W - 2 * PAD) / (n - 1);
    const y = (v) => H - PAD - (v - lo) * (H - 2 * PAD) / ((hi - lo) || 1);
    const line = (pts) => pts.map((v, i) =>
      (i ? "L" : "M") + x(i, pts.length).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.innerHTML =
      '<line x1="0" x2="' + W + '" y1="' + y(0) + '" y2="' + y(0) +
      '" stroke="#e7e5e4" stroke-width="1"/>' +
      '<path d="' + line(path.old) + '" fill="none" stroke="#a8a29e" stroke-width="2"/>' +
      '<path d="' + line(path.new) + '" fill="none" stroke="#0d9488" stroke-width="2"/>';
    svg.dataset.sym = sym;
    panel.appendChild(svg);
    holder.appendChild(panel);
  }
})();

/* Folklore + candidates */
(function () {
  const folk = document.getElementById("folk");
  folk.innerHTML = "<tr><th>Claim</th><th>Ticker</th><th>Measured</th><th>p</th><th>Verdict</th></tr>";
  for (const f of DATA.folklore) {
    const tr = el("tr");
    tr.appendChild(el("td", "", f.name));
    tr.appendChild(el("td", "", f.symbol));
    tr.appendChild(el("td", "", f.mean_pct !== undefined ?
      (f.mean_pct >= 0 ? "+" : "") + f.mean_pct + "%/mo over " + f.n_years + "y" : "-"));
    tr.appendChild(el("td", "", f.p !== undefined ? String(f.p) : "-"));
    const v = el("td");
    const mark = f.verdict === "HELD" ? "\\u2713 " : f.verdict === "FAILED" ? "\\u2717 " : "";
    v.appendChild(el("span", "verdict " + f.verdict.toLowerCase(), mark + f.verdict));
    tr.appendChild(v);
    folk.appendChild(tr);
  }
  const cand = document.getElementById("cand");
  cand.innerHTML = "<tr><th>Ticker</th><th>Month</th><th>Avg</th><th>p</th><th>Why not convicted</th></tr>";
  const rows = DATA.cells.filter((c) => c.tier === "candidate")
    .sort((a, b) => a.p - b.p);
  if (!rows.length) cand.innerHTML += '<tr><td colspan="5" class="muted">none this scan</td></tr>';
  for (const c of rows) {
    const tr = el("tr");
    tr.appendChild(el("td", "", c.symbol));
    tr.appendChild(el("td", "", MONTHS[c.month - 1]));
    tr.appendChild(el("td", "", (c.mean_pct >= 0 ? "+" : "") + c.mean_pct + "%"));
    tr.appendChild(el("td", "", String(c.p)));
    tr.appendChild(el("td", "muted", c.tier_note || ""));
    cand.appendChild(tr);
  }
})();

/* One shared tooltip for heatmap cells and path panels */
(function () {
  const tip = document.getElementById("tip");
  document.addEventListener("mousemove", (ev) => {
    const cell = ev.target.closest("[data-tip]");
    const svg = ev.target.closest("svg[data-sym]");
    let text = cell ? cell.dataset.tip : "";
    if (!text && svg) {
      const rect = svg.getBoundingClientRect();
      const p = DATA.paths[svg.dataset.sym];
      const i = Math.max(0, Math.min(p.new.length - 1,
        Math.round((ev.clientX - rect.left) / rect.width * (p.new.length - 1))));
      const month = MONTHS[Math.min(11, Math.floor(i / p.new.length * 12))];
      text = svg.dataset.sym + " ~" + month + ": newer " + p.new[i] +
        "% | older " + (p.old[i] !== undefined ? p.old[i] : "-") + "% cum";
    }
    if (!text) { tip.style.display = "none"; return; }
    tip.textContent = text;
    tip.style.display = "block";
    tip.style.left = Math.min(window.innerWidth - 320, ev.clientX + 14) + "px";
    tip.style.top = (ev.clientY + 14) + "px";
  });
})();

document.getElementById("foot").textContent =
  "Every number is arithmetic over real daily closes. A cell is convicted only if " +
  "its month beats " + DATA.gates.permutations + " within-year reshuffles of its own history, " +
  "points the same way in both halves of the years, and survives false-discovery " +
  "correction across the whole grid. Candidates failed a gate and are listed so you " +
  "know what almost fooled you. Past seasonality is a tendency, not a promise.";
</script>
</body>
</html>
"""


def render_report(scan: dict) -> str:
    return _REPORT_TEMPLATE.replace("__DATA__", json.dumps(scan, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())

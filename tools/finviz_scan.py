#!/usr/bin/env python
"""Finviz research from your own machine -- no AI tokens spent doing it.

Two things, both against Finviz's free public site (not Elite):

    screener   filter the whole market down to a ticker list + key stats
    lookup     one ticker's fundamentals, recent news, and insider trades

Fetching is done by the `finvizfinance` package (BeautifulSoup + requests
under the hood, MIT licensed, no relation to any AI provider) -- this file
is a thin, testable wrapper around it plus a no-brainer `menu` mode for
`tools/start_finviz.ps1` to launch. Nothing here calls an LLM; the only
network calls are the two `fetch_*` functions below, and only ever to
finviz.com.

Only `fetch_screener` and `fetch_lookup` touch the network, and only on
your own machine -- same as `season_scan.py`'s Yahoo calls and
`crypto_scan.py`'s yfinance calls, the cloud proxy that runs this repo's
CI blocks finviz.com too. Everything else (rendering, filter parsing,
the preset table) is pure and covered by tests/test_finviz_scan.py,
including a check that every PRESETS entry is a real finviz filter name
with a real option value, run against finvizfinance's own vendored
filter table -- offline, no live fetch required.

Examples::

    python tools/finviz_scan.py menu                        # no-brainer interactive mode
    python tools/finviz_scan.py list-presets
    python tools/finviz_scan.py screener --preset large_cap_uptrend --top 20
    python tools/finviz_scan.py screener --filter "Sector=Technology" --filter "P/E=Under 20"
    python tools/finviz_scan.py lookup AAPL
    python tools/finviz_scan.py list-filters
    python tools/finviz_scan.py filter-options "Market Cap."
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

DEFAULT_OUT_DIR = "finviz"

# Starting points, not trading advice -- edit or add to these freely. Each is
# a {finviz filter name: option value} dict, exactly what `set_filter` below
# takes. Names and values must match finviz's own labels exactly (spacing,
# punctuation and all); `list-filters` / `filter-options NAME` print the real
# ones so a new preset never has to be guessed. tests/test_finviz_scan.py
# checks every entry here against that same table.
PRESETS = {
    "large_cap_uptrend": {
        "Market Cap.": "+Large (over $10bln)",
        "200-Day Simple Moving Average": "Price above SMA200",
        "50-Day Simple Moving Average": "Price above SMA50",
    },
    "oversold_value": {
        "P/E": "Low (<15)",
        "RSI (14)": "Oversold (30)",
    },
    "unusual_volume": {
        "Relative Volume": "Over 2",
        "Average Volume": "Over 500K",
    },
}

# The columns worth printing from `ticker_fundament()`'s ~70-field dict, in
# the order a person actually reads them. Anything not in this list is still
# in the dict (and in --out json) -- this only trims the console view.
FUNDAMENT_FIELDS = [
    "Company",
    "Sector",
    "Industry",
    "Country",
    "Market Cap",
    "P/E",
    "Forward P/E",
    "PEG",
    "EPS (ttm)",
    "EPS next Y",
    "Insider Own",
    "Insider Trans",
    "Inst Own",
    "Short Float",
    "52W High",
    "52W Low",
    "RSI (14)",
    "Price",
    "Change",
    "Volume",
    "Avg Volume",
]

NEWS_COLUMNS = ["Date", "Title", "Link"]
INSIDER_COLUMNS = [
    "Insider Trading",
    "Relationship",
    "Date",
    "Transaction",
    "Cost",
    "#Shares",
    "Value ($)",
]


# ---------------------------------------------------------------------------
# Rendering + filter parsing -- pure functions, no network, fully tested
# ---------------------------------------------------------------------------


def parse_filter_kv(raw: str) -> tuple[str, str]:
    """Split one --filter "Name=Value" argument. Raises ValueError, not
    SystemExit, so the CLI layer decides how to report it and tests can
    assert on the message."""
    if "=" not in raw:
        raise ValueError(f'--filter expects "Name=Value", got: {raw!r}')
    name, value = raw.split("=", 1)
    name, value = name.strip(), value.strip()
    if not name or not value:
        raise ValueError(f'--filter expects "Name=Value", got: {raw!r}')
    return name, value


def render_screener(df: pd.DataFrame | None, top: int) -> str:
    if df is None or df.empty:
        return "No tickers matched these filters."
    shown = df.head(top)
    lines = [
        f"{len(df)} match(es), showing {len(shown)}:",
        "",
        shown.to_string(index=False),
    ]
    if len(df) > top:
        lines.append(
            f"\n... {len(df) - top} more not shown (--top to see more, --out to save all)"
        )
    return "\n".join(lines)


def render_lookup(
    ticker: str,
    fundament: dict | None,
    news: pd.DataFrame | None,
    news_error: str | None,
    insiders: pd.DataFrame | None,
    insiders_error: str | None,
    news_top: int = 8,
    insiders_top: int = 8,
) -> str:
    lines = [f"=== {ticker.upper()} ==="]
    if not fundament:
        lines.append("No fundamentals returned -- check the ticker spelling.")
    else:
        width = max((len(f) for f in FUNDAMENT_FIELDS if f in fundament), default=0)
        for field in FUNDAMENT_FIELDS:
            if field in fundament:
                lines.append(f"  {field:<{width}}  {fundament[field]}")

    lines += ["", "-- News --"]
    if news_error:
        lines.append(f"  could not load news: {news_error}")
    elif news is None or news.empty:
        lines.append("  (none)")
    else:
        cols = [c for c in NEWS_COLUMNS if c in news.columns]
        for _, row in news.head(news_top).iterrows():
            date = row.get("Date", "")
            title = row.get("Title", "")
            lines.append(f"  {date}  {title}")
        if not cols:
            lines.append(f"  (unexpected columns: {list(news.columns)})")

    lines += ["", "-- Insider trades (most recent) --"]
    if insiders_error:
        lines.append(f"  could not load insider trades: {insiders_error}")
    elif insiders is None or insiders.empty:
        lines.append("  (none)")
    else:
        cols = [c for c in INSIDER_COLUMNS if c in insiders.columns]
        table = insiders[cols] if cols else insiders
        lines.append(table.head(insiders_top).to_string(index=False))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Network plumbing -- the only part that touches finviz.com. Blocked from a
# cloud sandbox (same as yfinance/Yahoo elsewhere in this repo); run
# `screener`, `lookup` and `menu` on your own machine.
# ---------------------------------------------------------------------------


def fetch_screener(
    filters: dict,
    order: str = "Ticker",
    limit: int = 100,
    ascend: bool = True,
) -> pd.DataFrame:
    from finvizfinance.screener.overview import Overview

    ov = Overview()
    if filters:
        ov.set_filter(filters_dict=filters)
    return ov.screener_view(order=order, limit=limit, ascend=ascend, verbose=0)


def fetch_lookup(ticker: str) -> dict:
    """One ticker's fundamentals, news and insider trades. News/insider
    fetches are kept independent of each other and of the fundamentals call:
    Finviz shows insider trades only for some tickers and no news for others,
    and one section being unavailable should not blank out the rest."""
    from finvizfinance.quote import finvizfinance as FinvizQuote

    q = FinvizQuote(ticker)
    fundament = q.ticker_fundament()

    news, news_error = None, None
    try:
        news = q.ticker_news()
    except (
        Exception
    ) as exc:  # finvizfinance raises plain Exception/ValueError on layout misses
        news_error = str(exc)

    insiders, insiders_error = None, None
    try:
        insiders = q.ticker_inside_trader()
    except Exception as exc:
        insiders_error = str(exc)

    return {
        "fundament": fundament,
        "news": news,
        "news_error": news_error,
        "insiders": insiders,
        "insiders_error": insiders_error,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _save_csv(df: pd.DataFrame, out: str) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def cmd_screener(args):
    filters = dict(PRESETS.get(args.preset, {})) if args.preset else {}
    for raw in args.filter or []:
        name, value = parse_filter_kv(raw)
        filters[name] = value
    df = fetch_screener(
        filters, order=args.order, limit=args.limit, ascend=not args.desc
    )
    print(render_screener(df, args.top))
    if args.out and df is not None and not df.empty:
        _save_csv(df, args.out)
        print(f"\nsaved {len(df)} row(s) -> {args.out}")


def cmd_lookup(args):
    result = fetch_lookup(args.ticker)
    print(render_lookup(args.ticker, **result))
    if args.out:
        import json

        payload = dict(result)
        for key in ("news", "insiders"):
            df = payload.get(key)
            payload[key] = df.to_dict(orient="records") if df is not None else None
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nsaved -> {args.out}")


def cmd_list_filters(args):
    from finvizfinance.screener.util import get_filters

    for name in get_filters():
        print(name)


def cmd_filter_options(args):
    from finvizfinance.screener.util import get_filter_options

    for opt in get_filter_options(args.name):
        print(opt)


def cmd_list_presets(args):
    for name, filters in PRESETS.items():
        print(f"{name}:")
        for key, value in filters.items():
            print(f"  {key} = {value}")


def _prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or default


def run_menu() -> None:
    """No-brainer interactive mode: numbered choices, no flags to remember.
    This is what the desktop shortcut launches."""
    print("Finviz Research -- runs locally, no AI tokens spent.\n")
    while True:
        print("1) Screener with a ready-made preset")
        print("2) Screener with your own filters")
        print("3) Look up one ticker")
        print("4) List every filter name")
        print("5) List the option values for one filter")
        print("6) Quit")
        choice = _prompt("Choose", "6")

        if choice in ("6", "q", "quit", ""):
            print("Bye.")
            return

        try:
            if choice == "1":
                _menu_preset_screener()
            elif choice == "2":
                _menu_custom_screener()
            elif choice == "3":
                _menu_lookup()
            elif choice == "4":
                from finvizfinance.screener.util import get_filters

                for name in get_filters():
                    print(f"  {name}")
                print()
            elif choice == "5":
                name = _prompt("Filter name, exactly as listed under option 4")
                from finvizfinance.screener.util import get_filter_options

                for opt in get_filter_options(name):
                    print(f"  {opt}")
                print()
            else:
                print("Not a choice on the list -- try again.\n")
        except Exception as exc:
            print(f"\nSomething went wrong: {exc}")
            print("Paste this whole message back if you want it fixed.\n")


def _menu_preset_screener() -> None:
    names = list(PRESETS)
    for i, name in enumerate(names, 1):
        print(f"  {i}) {name}  ->  {PRESETS[name]}")
    pick = _prompt("Preset number")
    try:
        preset = names[int(pick) - 1]
    except (ValueError, IndexError):
        print("Not a valid preset number.\n")
        return
    top = int(_prompt("How many rows to show", "20") or 20)
    df = fetch_screener(PRESETS[preset], limit=max(top, 100))
    print("\n" + render_screener(df, top) + "\n")
    _menu_maybe_save_csv(df)


def _menu_custom_screener() -> None:
    print('Enter filters as "Name=Value", one per line. Blank line to finish.')
    print("Example: Sector=Technology   (option 4/5 list every valid Name and Value)")
    filters: dict[str, str] = {}
    while True:
        line = input("  filter> ").strip()
        if not line:
            break
        try:
            name, value = parse_filter_kv(line)
        except ValueError as exc:
            print(f"  {exc}")
            continue
        filters[name] = value
    if not filters:
        print("No filters entered -- that would list the entire market, skipping.\n")
        return
    top = int(_prompt("How many rows to show", "20") or 20)
    df = fetch_screener(filters, limit=max(top, 100))
    print("\n" + render_screener(df, top) + "\n")
    _menu_maybe_save_csv(df)


def _menu_maybe_save_csv(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    save = _prompt("Save the full results to a CSV file? (path, or blank to skip)")
    if save:
        _save_csv(df, save)
        print(f"saved -> {save}\n")
    else:
        print()


def _menu_lookup() -> None:
    ticker = _prompt("Ticker symbol, e.g. AAPL").upper()
    if not ticker:
        return
    result = fetch_lookup(ticker)
    print("\n" + render_lookup(ticker, **result) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="finviz_scan",
        description="Finviz research (screener + single-ticker lookup) from your own machine.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("menu", help="no-brainer interactive mode -- start here")
    p.set_defaults(func=lambda args: run_menu())

    p = sub.add_parser("screener", help="filter the market to a ticker list")
    p.add_argument(
        "--preset", choices=sorted(PRESETS), help="start from a named preset"
    )
    p.add_argument(
        "--filter",
        action="append",
        metavar="Name=Value",
        help='add/override one filter, e.g. --filter "Sector=Technology" (repeatable)',
    )
    p.add_argument(
        "--order", default="Ticker", help="finviz sort column (default: Ticker)"
    )
    p.add_argument(
        "--desc", action="store_true", help="sort descending instead of ascending"
    )
    p.add_argument(
        "--limit", type=int, default=100, help="max rows to fetch from finviz"
    )
    p.add_argument("--top", type=int, default=20, help="rows to print to the console")
    p.add_argument("--out", help="also save every fetched row to this CSV path")
    p.set_defaults(func=cmd_screener)

    p = sub.add_parser("lookup", help="one ticker: fundamentals, news, insider trades")
    p.add_argument("ticker")
    p.add_argument("--out", help="also save the full result as JSON to this path")
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("list-filters", help="print every valid screener filter name")
    p.set_defaults(func=cmd_list_filters)

    p = sub.add_parser("filter-options", help="print the valid values for one filter")
    p.add_argument("name", help='exact filter name, e.g. "Market Cap."')
    p.set_defaults(func=cmd_filter_options)

    p = sub.add_parser("list-presets", help="print the built-in screener presets")
    p.set_defaults(func=cmd_list_presets)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

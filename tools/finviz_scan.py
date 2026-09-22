#!/usr/bin/env python
"""Finviz research from your own machine -- no AI tokens spent doing it.

Four things:

    screener    filter the whole market down to a ticker list + key stats
                (Finviz's free public site, not Elite). A filter match is
                not a scored setup -- add --vet (or --add-flagged) to run
                the same timing signals `check` runs on the watchlist
                against these hits before trusting any of them, or say yes
                when `menu` asks the same thing.
    lookup      one ticker's fundamentals, recent news, and insider trades
                (also Finviz)
    watchlist   your own tracked-ticker list -- add/remove/view
    check       EMA/RSI/volume/52-week timing signals on that watchlist,
                against real daily price history (yfinance, not Finviz --
                see the comment above watchlist_signals() for what each
                signal means and where it's from). Read that comment before
                trusting any of it: nothing here finds "perfect timing",
                it flags candidates worth a closer look. `vet_candidates()`
                runs the identical pipeline against a screener's ticker
                list instead of the watchlist -- what --vet/--add-flagged
                and the menu's vetting step call.

`screener`/`lookup` fetching is done by the `finvizfinance` package
(BeautifulSoup + requests under the hood, MIT licensed, no relation to any
AI provider); `check` fetches bars the same way `crypto_scan.py` and
`season_scan.py` already do. This file is a thin, testable wrapper around
both, plus a no-brainer `menu` mode for `tools/start_finviz.ps1` to launch.
Nothing here calls an LLM -- the only network-touching functions are
`fetch_screener`, `fetch_lookup` and `fetch_bars`, and only ever to
finviz.com or Yahoo.

Only those three functions touch the network, and only on your own
machine -- the cloud proxy that runs this repo's CI blocks both finviz.com
and yfinance. Everything else (rendering, filter parsing, the preset
table, every EMA/RSI/confluence calculation) is pure and covered by
tests/test_finviz_scan.py and tests/test_finviz_watchlist.py, including a
check that every PRESETS entry is a real finviz filter name with a real
option value, run against finvizfinance's own vendored filter table --
offline, no live fetch required.

Research files (saved screener CSVs, ticker lookups, watchlist checks)
default to your Desktop (a `finviz-research` folder there), not this repo
-- see default_desktop_dir() below.

Examples::

    python tools/finviz_scan.py menu                        # no-brainer interactive mode
    python tools/finviz_scan.py list-presets
    python tools/finviz_scan.py screener --preset large_cap_uptrend --top 20
    python tools/finviz_scan.py screener --filter "Sector=Technology" --filter "P/E=Under 20"
    python tools/finviz_scan.py screener --preset large_cap_uptrend --add-flagged
    python tools/finviz_scan.py screener --preset breakout_squeeze --vet
    python tools/finviz_scan.py screener --preset breakout_momentum --vet
    python tools/finviz_scan.py screener --preset megacap_short_exhaustion --vet
    python tools/finviz_scan.py lookup AAPL
    python tools/finviz_scan.py list-filters
    python tools/finviz_scan.py filter-options "Market Cap."
    python tools/finviz_scan.py watchlist add AAPL MSFT NVDA
    python tools/finviz_scan.py watchlist list
    python tools/finviz_scan.py check
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd

# Anchored to this file, not the current directory: a relative default meant
# running the script from any other folder silently started a second, empty
# watchlist there.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST_FILE = str(REPO_ROOT / "finviz" / "watchlist.txt")

# Extra research sources for `lookup`, run after the Finviz sections. Empty by
# default. Each entry is (section title, callable taking a ticker and
# returning plain text). One failing never blanks the Finviz sections or the
# other add-ons -- see run_addons(). An AI-backed source (Perplexity and the
# like) spends paid tokens on every lookup, which this tool otherwise never
# does: keep such an add-on opt-in, read its key from the environment (.env,
# never committed), and say in its section title that it costs money.
LOOKUP_ADDONS: list[tuple[str, object]] = []

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
    # "About to explode upside", split into the two setups requested
    # 2026-09-22 -- see the note above squeeze_signal() for why they're kept
    # separate rather than merged into one preset.
    "breakout_squeeze": {
        # Finviz has no "tight range" filter, so this only narrows to liquid
        # names sitting near their highs; the actual contraction read is
        # squeeze_signal() on the vetted bars (--vet / the menu's vet step).
        "52-Week High/Low": "0-10% below High",
        "Average Volume": "Over 500K",
    },
    "breakout_momentum": {
        # Already trending, not coiling: confirmed uptrend + a strong recent
        # quarter + volume backing it up. Vets through the same EMA/RSI/MA
        # confluence as every other preset here.
        "200-Day Simple Moving Average": "Price above SMA200",
        "50-Day Simple Moving Average": "Price above SMA50",
        "Performance": "Quarter Up",
        "Relative Volume": "Over 1.5",
    },
    "megacap_short_exhaustion": {
        # "Big tech, liquid enough to short": mega-cap, extended well above
        # its own trend, RSI already overbought. Vetting then looks for the
        # bearish side of the same confluence (EMA80 reject, death cross
        # starting) on top of this filter, not a separate signal.
        "Market Cap.": "Mega ($200bln and more)",
        "200-Day Simple Moving Average": "Price above SMA200",
        "RSI (14)": "Overbought (70)",
        "Performance": "Month +10%",
    },
}

# Presets built to find *stocks*, not funds. Finviz's screener has no
# stocks-only toggle of its own -- confirmed 2026-09-22 live: AAXJ, ACWX,
# ACYN and AAPU (leveraged/international ETFs) all matched breakout_squeeze
# and breakout_momentum on price/volume alone, same as any real stock would.
# These three get ETF rows dropped client-side after fetching (see
# exclude_etfs() below); every other preset and any hand-typed --filter
# stays exactly what Finviz matched.
STOCK_ONLY_PRESETS = {
    "breakout_squeeze",
    "breakout_momentum",
    "megacap_short_exhaustion",
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


def human_number(value) -> str:
    """41390000000.0 -> '41.39B'. Console display only; saved files keep the
    raw number."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if x != x:
        return ""
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= size:
            return f"{x / size:.2f}{suffix}"
    return f"{x:g}"


def humanize_screener(df: pd.DataFrame) -> pd.DataFrame:
    """A display copy with Market Cap as 41.39B and Volume as 1,646,765
    instead of scientific notation."""
    out = df.copy()
    if "Market Cap" in out.columns:
        out["Market Cap"] = out["Market Cap"].map(human_number)
    if "Volume" in out.columns:
        out["Volume"] = out["Volume"].map(
            lambda v: f"{float(v):,.0f}" if pd.notna(v) else ""
        )
    return out


def render_screener(df: pd.DataFrame | None, top: int, limit: int | None = None) -> str:
    if df is None or df.empty:
        return "No tickers matched these filters."
    shown = humanize_screener(df.head(top))
    if limit is not None and len(df) >= limit:
        # Finviz stopped at the fetch limit, so the real match count is unknown.
        header = (
            f"First {len(df)} match(es) fetched -- that is the fetch limit, more "
            f"may match (--limit to fetch more). Showing {len(shown)}:"
        )
    else:
        header = f"{len(df)} match(es), showing {len(shown)}:"
    lines = [header, "", shown.to_string(index=False)]
    if len(df) > top:
        lines.append(
            f"\n... {len(df) - top} more not shown (--top to see more, --out to save all)"
        )
    return "\n".join(lines)


def screener_tickers(df: pd.DataFrame | None, limit: int | None = None) -> list[str]:
    """Ticker symbols from a screener result, in the order Finviz returned
    them, deduped, optionally capped. What `--vet`/`--add-flagged` and the
    menu's vetting step feed into `vet_candidates()` -- a screener match is
    only a filter hit, not a scored setup; vetting is what decides which of
    these are actually worth tracking today."""
    if df is None or df.empty or "Ticker" not in df.columns:
        return []
    out: list[str] = []
    for raw in df["Ticker"]:
        sym = str(raw).strip().upper()
        if sym and sym not in out:
            out.append(sym)
        if limit is not None and len(out) >= limit:
            break
    return out


def exclude_etfs(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Drop rows Finviz classifies as funds (Industry == 'Exchange Traded
    Fund') rather than operating companies. Used only for STOCK_ONLY_PRESETS
    -- the general screener path leaves whatever the caller's own filters
    matched untouched. None/empty/columnless input passes through as-is."""
    if df is None or df.empty or "Industry" not in df.columns:
        return df
    return df[df["Industry"] != "Exchange Traded Fund"].reset_index(drop=True)


def _one_line(text) -> str:
    """Finviz headlines arrive wrapped in newlines and indentation."""
    return " ".join(str(text).split())


def render_lookup(
    ticker: str,
    fundament: dict | None,
    news: pd.DataFrame | None,
    news_error: str | None,
    insiders: pd.DataFrame | None,
    insiders_error: str | None,
    news_top: int = 8,
    insiders_top: int = 8,
    addons: list[tuple[str, str | None, str | None]] | None = None,
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
            date = _one_line(row.get("Date", ""))
            title = _one_line(row.get("Title", ""))
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

    for title, text, error in addons or []:
        lines += ["", f"-- {title} --"]
        if error:
            lines.append(f"  could not load: {error}")
        elif not text:
            lines.append("  (none)")
        else:
            lines += [f"  {line}" for line in str(text).splitlines()]

    return "\n".join(lines)


def run_addons(ticker: str, addons) -> list[tuple[str, str | None, str | None]]:
    """(title, text, error) per add-on. Each is isolated: one raising is
    reported in its own section and the rest still run."""
    out = []
    for title, fetch in addons:
        try:
            out.append((title, fetch(ticker), None))
        except Exception as exc:
            out.append((title, None, str(exc) or type(exc).__name__))
    return out


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
        "addons": run_addons(ticker, LOOKUP_ADDONS),
    }


# ---------------------------------------------------------------------------
# Watchlist -- EMA/RSI/volume/52-week signals on real daily price history.
# A DIFFERENT data source from everything above: Finviz's free site has no
# historical-OHLCV endpoint, so this pulls daily bars via yfinance the same
# way crypto_scan.py and season_scan.py already do (fetch_bars below is a
# deliberate near-duplicate of crypto_scan.fetch_daily, not a cross-import --
# `python tools/finviz_scan.py check` runs this file as a script, and a
# script's own directory goes on sys.path, not the repo root, so `from
# tools.crypto_scan import ...` would break exactly there while still
# passing under pytest). Same rule as everywhere else in this repo: the
# cloud proxy blocks yfinance too, so `fetch_bars` only ever runs on your
# own machine.
#
# Read this before trusting any of it: no indicator or combination of
# indicators finds "perfect timing" -- that phrase describes something that
# does not exist. Every signal here is a documented, commonly-cited pattern,
# not a guarantee; it flags candidates worth a closer look, the same way
# crypto_scan's score_universe does, and is exactly as fallible on a bad
# day. Sourced from a 2026-09-15 web search (capital.com, altrady.com,
# Phillip Nova, and TradingView's own "GODMODE"-family scripts):
#
#     ema_cross     20-EMA crossing 50-EMA -- the most commonly cited
#                   short/medium trend-change signal.
#     ema80_react   price touching the 80-EMA and closing back on the same
#                   side ("bounce") or the opposite side it came from
#                   ("reject"). TradingView's EMA-confluence "GODMODE"
#                   scripts (55/99-period EMA pairs, etc.) use a moving
#                   average as support/resistance the same way -- but
#                   "godmode" names a family of different community
#                   scripts, not one standardized formula, so nothing here
#                   claims to reproduce a specific one.
#     rsi           Wilder's RSI(14); <30 oversold, >70 overbought.
#     ma_cross      50/200-day SMA golden/death cross -- ema_cross's
#                   long-horizon sibling, deliberately a different lookback.
#     near_52w      price within NEAR_52W_PCT of its 52-week high/low.
#                   Reported as context only -- "near a high" and "near a
#                   low" each read as bullish under one style (breakout,
#                   mean-reversion) and bearish under the other, so it is
#                   not folded into the confluence count below.
#     volume        today's volume vs its own 20-day average, excluding
#                   today -- same exclude-the-surge-itself logic as
#                   crypto_scan.coin_signals. Context only, not scored.
#     macd          MACD(12,26,9) histogram sign, cited repeatedly as the
#                   standard confirmation for an EMA cross. Context only.
#     squeeze       today's 20-period Bollinger Band width ranked against its
#                   own trailing SQUEEZE_LOOKBACK days -- true when it's in
#                   the tightest SQUEEZE_PERCENTILE of that window (Bollinger,
#                   *Bollinger on Bollinger Bands*, 2001; the same "coil
#                   before it moves" read behind Minervini's VCP, *Trade Like
#                   a Stock Market Wizard*, 2013). Practitioner lore, not
#                   peer-reviewed like the crypto momentum papers in
#                   docs/trading-wisdom.md, and it says nothing about which
#                   way the expansion breaks -- a squeeze near a 52-week LOW
#                   is not a bullish read. Context only, not folded into the
#                   confluence count below, same reasoning as near_52w.
#
# `classify_confluence` counts how many of the four *directional* signals
# (ema_cross, ema80_react, rsi extreme, ma_cross) agree on this bar. A
# higher count is not "more correct" -- it is "more of these specific,
# named things happened to line up today." Treat it as a same-day
# prioritization order for your own research, never as a signal to act on
# by itself.
# ---------------------------------------------------------------------------

WATCHLIST_HISTORY_DAYS = 300  # enough to seed EMA80 and cover a 52-week window
WATCHLIST_MIN_BARS = (
    100  # fewer than this and EMA80/52-week reads are too seed-biased to report
)
NEAR_52W_PCT = 0.05  # within 5% of the 52-week extreme counts as "near"
EMA80_TOUCH_PCT = 0.015  # within 1.5% of the 80-EMA counts as a touch
SCREENER_VET_LIMIT = 25  # each vetted ticker is its own yfinance fetch
BB_PERIOD = 20  # Bollinger's own default
BB_NUM_STD = 2.0  # Bollinger's own default
SQUEEZE_LOOKBACK = 120  # trailing window today's band width is ranked against
SQUEEZE_PERCENTILE = 0.20  # tightest 20% of that window counts as a squeeze


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI: average gain/loss smoothed with alpha = 1/period."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_width_pct(
    close: pd.Series, period: int = BB_PERIOD, num_std: float = BB_NUM_STD
) -> pd.Series:
    """Bollinger Band width as a fraction of the middle band: (upper -
    lower) / middle. Small = price coiling in a tight range."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return (2 * num_std * std) / mid


def squeeze_signal(
    close: pd.Series,
    lookback: int = SQUEEZE_LOOKBACK,
    percentile: float = SQUEEZE_PERCENTILE,
) -> dict:
    """Is today's Bollinger width unusually tight versus its own trailing
    history? `bb_width_rank` is the fraction of the lookback window with a
    *wider* band than today (0 = the tightest bar in the window). Returns
    squeeze=False with NaN width/rank -- never raises -- when there isn't
    enough history yet, same degrade-gracefully rule as near_52w. Also
    False when the whole lookback window never moved at all (width stuck
    at exactly 0 throughout): a flatlined ticker isn't "coiling", there's
    no real range there to contract, and ranking a constant series is
    undefined anyway."""
    width = bollinger_width_pct(close)
    tail = width.iloc[-lookback:]
    if len(tail) < lookback or tail.isna().any() or tail.max() <= 0:
        return {
            "bb_width_pct": float("nan"),
            "bb_width_rank": float("nan"),
            "squeeze": False,
        }
    current = float(tail.iloc[-1])
    rank = float((tail < current).mean())
    return {
        "bb_width_pct": current,
        "bb_width_rank": rank,
        "squeeze": rank <= percentile,
    }


def macd_histogram(close: pd.Series) -> pd.Series:
    macd_line = ema(close, 12) - ema(close, 26)
    signal_line = ema(macd_line, 9)
    return macd_line - signal_line


def _ema80_reaction(
    close: pd.Series, high: pd.Series, low: pd.Series, ema80: pd.Series
) -> str:
    last_close, last_high, last_low = (
        float(close.iloc[-1]),
        float(high.iloc[-1]),
        float(low.iloc[-1]),
    )
    last_ema80 = float(ema80.iloc[-1])
    touched = (
        last_low <= last_ema80 <= last_high
        or abs(last_low - last_ema80) / last_ema80 <= EMA80_TOUCH_PCT
        or abs(last_high - last_ema80) / last_ema80 <= EMA80_TOUCH_PCT
    )
    if not touched:
        return "none"
    prior_above = float(close.iloc[-2]) > float(ema80.iloc[-2])
    if prior_above and last_close > last_ema80:
        return "bounce"
    if not prior_above and last_close < last_ema80:
        return "reject"
    return "cross"  # touched from one side, closed the other -- a break, not a bounce/reject


def watchlist_signals(bars: pd.DataFrame) -> dict:
    """Signals for one ticker from daily bars (columns: open, high, low,
    close, volume; oldest first). Raises ValueError on too little history
    rather than reporting a seed-biased EMA80/52-week number as real."""
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume"].astype(float)
    n = len(close)
    if n < WATCHLIST_MIN_BARS:
        raise ValueError(f"need {WATCHLIST_MIN_BARS} bars, have {n}")

    last = float(close.iloc[-1])
    ema20, ema50, ema80 = ema(close, 20), ema(close, 50), ema(close, 80)
    rsi14 = rsi(close, 14)
    hist = macd_histogram(close)

    prev_diff = float(ema20.iloc[-2] - ema50.iloc[-2])
    last_diff = float(ema20.iloc[-1] - ema50.iloc[-1])
    ema_cross = "none"
    if prev_diff <= 0 < last_diff:
        ema_cross = "bullish"
    elif prev_diff >= 0 > last_diff:
        ema_cross = "bearish"

    rsi_last = float(rsi14.iloc[-1])
    rsi_signal = (
        "oversold" if rsi_last < 30 else "overbought" if rsi_last > 70 else "neutral"
    )

    sma50, sma200 = close.rolling(50).mean(), close.rolling(200).mean()
    ma_cross = "none"
    if n >= 201 and pd.notna(sma200.iloc[-2]):
        prev_ma_diff = float(sma50.iloc[-2] - sma200.iloc[-2])
        last_ma_diff = float(sma50.iloc[-1] - sma200.iloc[-1])
        if prev_ma_diff <= 0 < last_ma_diff:
            ma_cross = "golden"
        elif prev_ma_diff >= 0 > last_ma_diff:
            ma_cross = "death"

    window = min(n, 252)
    high_52w = float(high.iloc[-window:].max())
    low_52w = float(low.iloc[-window:].min())
    from_high = last / high_52w - 1  # <= 0
    from_low = last / low_52w - 1  # >= 0
    near_52w = "none"
    if abs(from_high) <= NEAR_52W_PCT:
        near_52w = "high"
    elif from_low <= NEAR_52W_PCT:
        near_52w = "low"

    vol_avg20 = float(volume.iloc[-21:-1].mean())
    volume_surge = (
        float(volume.iloc[-1]) / vol_avg20 - 1 if vol_avg20 > 0 else float("nan")
    )

    squeeze = squeeze_signal(close)

    return {
        "last": last,
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema80": float(ema80.iloc[-1]),
        "ema_cross": ema_cross,
        "ema80_react": _ema80_reaction(close, high, low, ema80),
        "rsi14": rsi_last,
        "rsi_signal": rsi_signal,
        "ma_cross": ma_cross,
        "near_52w": near_52w,
        "from_52w_high": from_high,
        "from_52w_low": from_low,
        "volume_surge": volume_surge,
        "bb_width_pct": squeeze["bb_width_pct"],
        "bb_width_rank": squeeze["bb_width_rank"],
        "squeeze": squeeze["squeeze"],
        "macd_hist": float(hist.iloc[-1]),
        "bars": n,
    }


def classify_confluence(signals: dict) -> dict:
    """How many of the four *directional* signals agree on this bar. See
    the module-level note above on why near_52w/volume/macd are context
    only and not counted here."""
    bullish = sum(
        1
        for key, value in (
            ("ema_cross", "bullish"),
            ("ema80_react", "bounce"),
            ("rsi_signal", "oversold"),
            ("ma_cross", "golden"),
        )
        if signals[key] == value
    )
    bearish = sum(
        1
        for key, value in (
            ("ema_cross", "bearish"),
            ("ema80_react", "reject"),
            ("rsi_signal", "overbought"),
            ("ma_cross", "death"),
        )
        if signals[key] == value
    )
    direction = (
        "bullish" if bullish > bearish else "bearish" if bearish > bullish else "mixed"
    )
    return {"bullish_count": bullish, "bearish_count": bearish, "direction": direction}


def load_watchlist(path: Path) -> list[str]:
    """One ticker per line; '#' starts a trailing comment; blank lines and
    duplicates are dropped. Missing file reads as an empty watchlist."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        symbol = line.split("#", 1)[0].strip().upper()
        if symbol:
            out.append(symbol)
    return sorted(set(out))


def save_watchlist(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = sorted({s.strip().upper() for s in symbols if s.strip()})
    path.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")


def fetch_bars(
    symbols: list[str], days: int = WATCHLIST_HISTORY_DAYS
) -> dict[str, pd.DataFrame]:
    """Daily bars per symbol via yfinance. Missing/empty symbols are
    dropped silently here and reported by the caller as skipped."""
    import yfinance as yf

    out = {}
    raw = yf.download(
        symbols,
        period=f"{days}d",
        interval="1d",
        group_by="ticker",
        progress=False,
        auto_adjust=False,
    )
    for sym in symbols:
        try:
            df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.rename(columns=str.lower)[
            ["open", "high", "low", "close", "volume"]
        ].dropna()
        if not df.empty:
            out[sym] = df.reset_index(drop=True)
    return out


def run_watchlist_check(
    bars_by_symbol: dict[str, pd.DataFrame],
) -> tuple[dict[str, dict], list[str]]:
    results: dict[str, dict] = {}
    skipped: list[str] = []
    for sym, bars in bars_by_symbol.items():
        try:
            signals = watchlist_signals(bars)
        except ValueError:
            skipped.append(sym)
            continue
        signals["confluence"] = classify_confluence(signals)
        results[sym] = signals
    return results, skipped


def _format_squeeze(s: dict) -> str:
    """Handles the hand-built dicts in older tests/callers that predate the
    squeeze fields -- reads as "n/a" rather than raising a KeyError."""
    rank = s.get("bb_width_rank")
    if rank is None or rank != rank:  # NaN or missing
        return "n/a (not enough history)"
    tag = "YES" if s.get("squeeze") else "no"
    return f"{tag} (width rank {rank:.0%} of trailing {SQUEEZE_LOOKBACK}d, tighter = lower)"


def render_watchlist_check(
    results: dict[str, dict],
    skipped: list[str],
    no_data: list[str] = (),
    heading: str = "watchlist check",
) -> tuple[str, list[str]]:
    """Returns (report text, symbols worth an actual look, not the whole
    watchlist) -- flagged on a 2+ signal confluence *or* a Bollinger squeeze
    (see squeeze_signal()), since a coiling range is its own setup and won't
    show up as EMA/RSI/MA confluence until after it's already broken.
    `heading` only changes the title line -- `vet_candidates()` passes a
    different one so a screener-hits report doesn't read as if it came from
    the watchlist."""
    flagged = [
        sym
        for sym, s in results.items()
        if max(s["confluence"]["bullish_count"], s["confluence"]["bearish_count"]) >= 2
        or s.get("squeeze", False)
    ]
    lines = [
        f"Finviz Research -- {heading}",
        "No indicator finds perfect timing. This flags candidates for your own",
        "look, nothing more -- see the comment above watchlist_signals() in",
        "tools/finviz_scan.py for what each signal is and where it's from.",
        "",
    ]
    if not results:
        lines.append(
            "Nothing to check -- empty watchlist, or no symbol had enough history."
        )
    ranked = sorted(
        results,
        key=lambda sym: -max(
            results[sym]["confluence"]["bullish_count"],
            results[sym]["confluence"]["bearish_count"],
        ),
    )
    for sym in ranked:
        s = results[sym]
        c = s["confluence"]
        marker = "  <-- FLAGGED" if sym in flagged else ""
        vol = (
            "n/a"
            if s["volume_surge"] != s["volume_surge"]
            else f"{s['volume_surge']:+.1%}"
        )
        lines += [
            f"{sym}{marker}",
            f"  last {s['last']:.2f}   confluence: {c['direction']} "
            f"({c['bullish_count']} bullish / {c['bearish_count']} bearish signal(s))",
            f"  EMA20/50 cross: {s['ema_cross']:<8}  EMA80 reaction: {s['ema80_react']:<6}  "
            f"RSI14: {s['rsi14']:.1f} ({s['rsi_signal']})",
            f"  50/200 SMA cross: {s['ma_cross']:<6}  52w: {s['near_52w']:<4} "
            f"({s['from_52w_high']:+.1%} from high, {s['from_52w_low']:+.1%} from low)",
            f"  volume vs 20d avg: {vol}   MACD(12,26,9) hist: {s['macd_hist']:+.3f}",
            f"  Bollinger squeeze: {_format_squeeze(s)}",
            "",
        ]
    if skipped:
        lines.append(
            f"skipped (need {WATCHLIST_MIN_BARS}+ bars of history): {', '.join(sorted(skipped))}"
        )
    if no_data:
        lines.append(
            f"no price data (check the spelling, or it may be delisted): "
            f"{', '.join(sorted(no_data))}"
        )
    return "\n".join(lines), flagged


def check_watchlist(symbols: list[str]) -> tuple[str, list[str]]:
    """Fetch bars and build the report -- the one path both `check` and the
    menu use. Returns (report text, flagged symbols)."""
    bars = fetch_bars(symbols)
    results, skipped = run_watchlist_check(bars)
    no_data = [s for s in symbols if s not in bars]
    return render_watchlist_check(results, sorted(set(skipped)), sorted(set(no_data)))


def vet_candidates(symbols: list[str]) -> tuple[str, list[str]]:
    """The other caller of the same signal pipeline as `check_watchlist` --
    for a screener's ticker list rather than the tracked watchlist. A
    screener match only means Finviz's filters matched it today; this is
    what actually vets whether it looks like a live setup, using the exact
    same EMA/RSI/MA/52-week confluence logic (see the comment above
    `watchlist_signals()`). Returns (report text, flagged symbols -- 2+
    signal confluence, the ones `--add-flagged`/the menu offer to save)."""
    bars = fetch_bars(symbols)
    results, skipped = run_watchlist_check(bars)
    no_data = [s for s in symbols if s not in bars]
    return render_watchlist_check(
        results,
        sorted(set(skipped)),
        sorted(set(no_data)),
        heading="candidate vetting",
    )


def _stamp() -> str:
    # Seconds included: two runs inside one minute used to overwrite each
    # other's saved report.
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def default_desktop_dir() -> Path:
    """Where to save research files. FINVIZ_DESKTOP is set by
    start_finviz.ps1 to [Environment]::GetFolderPath('Desktop'), which sees
    a OneDrive-redirected Desktop that Path.home()/"Desktop" cannot. Falls
    back to Path.home()/"Desktop" for a direct `python tools/finviz_scan.py`
    run outside the launcher."""
    env = os.environ.get("FINVIZ_DESKTOP")
    return Path(env) if env else Path.home() / "Desktop"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _save_csv(df: pd.DataFrame, out: str) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")


def _lookup_json(result: dict) -> str:
    import json

    payload = dict(result)
    for key in ("news", "insiders"):
        df = payload.get(key)
        payload[key] = df.to_dict(orient="records") if df is not None else None
    return json.dumps(payload, indent=2, default=str)


def cmd_screener(args):
    filters = dict(PRESETS.get(args.preset, {})) if args.preset else {}
    for raw in args.filter or []:
        name, value = parse_filter_kv(raw)
        filters[name] = value
    df = fetch_screener(
        filters, order=args.order, limit=args.limit, ascend=not args.desc
    )
    if args.preset in STOCK_ONLY_PRESETS:
        df = exclude_etfs(df)
    print(render_screener(df, args.top, limit=args.limit))
    if args.out and df is not None and not df.empty:
        _save_csv(df, args.out)
        print(f"\nsaved {len(df)} row(s) -> {args.out}")

    if not (args.vet or args.add_flagged):
        return
    symbols = screener_tickers(df, limit=args.vet_limit)
    if not symbols:
        print("\nNothing to vet -- no tickers matched.")
        return
    print(f"\nVetting {len(symbols)} ticker(s) against timing signals ...")
    report, flagged = vet_candidates(symbols)
    print("\n" + report)
    if not args.add_flagged:
        return
    path = Path(args.file)
    if not flagged:
        print("\nnothing flagged -- watchlist unchanged")
        return
    save_watchlist(path, load_watchlist(path) + flagged)
    print(
        f"\nadded {len(flagged)} flagged ticker(s) to the watchlist ({path}): "
        f"{', '.join(flagged)}"
    )


def cmd_lookup(args):
    result = fetch_lookup(args.ticker)
    print(render_lookup(args.ticker, **result))
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_lookup_json(result), encoding="utf-8")
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


def cmd_watchlist(args):
    path = Path(args.file)
    symbols = load_watchlist(path)
    if args.action == "add":
        symbols = sorted(set(symbols) | {s.upper() for s in args.tickers})
        save_watchlist(path, symbols)
    elif args.action == "remove":
        symbols = sorted(set(symbols) - {s.upper() for s in args.tickers})
        save_watchlist(path, symbols)
    print(f"watchlist ({len(symbols)}): {', '.join(symbols) if symbols else '(empty)'}")


def cmd_check(args):
    path = Path(args.file)
    symbols = load_watchlist(path)
    if not symbols:
        print(
            f"Watchlist is empty ({path}). Add tickers first: "
            f"finviz_scan.py watchlist add TICKER ..."
        )
        return
    report, flagged = check_watchlist(symbols)
    print(report)

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else default_desktop_dir() / "finviz-research"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"watchlist-check_{_stamp()}.txt"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nsaved -> {out_path}")

    if flagged and not args.quiet:
        print(f"\n{len(flagged)} flagged: {', '.join(flagged)}")
    # Exit code, not just text: tools/finviz_watchlist_alert.ps1 (the daily
    # scheduled-task wrapper) reads this to decide whether to pop a message,
    # rather than parsing stdout.
    sys.exit(2 if flagged else 0)


def _prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or default


def _prompt_int(message: str, default: int) -> int:
    while True:
        raw = _prompt(message, str(default))
        try:
            value = int(raw)
        except ValueError:
            print("  Type a whole number, like 20.")
            continue
        if value > 0:
            return value
        print("  Type a number above 0.")


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
        print("6) Manage your watchlist (add/remove/view tickers)")
        print("7) Check your watchlist for timing signals")
        print("8) Quit")
        choice = _prompt("Choose", "8")

        if choice in ("8", "q", "quit", ""):
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
            elif choice == "6":
                _menu_watchlist_manage()
            elif choice == "7":
                _menu_watchlist_check()
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
    top = _prompt_int("How many rows to show", 20)
    limit = max(top, 100)
    df = fetch_screener(PRESETS[preset], limit=limit)
    print("\n" + render_screener(df, top, limit=limit) + "\n")
    _menu_maybe_save_csv(df)
    _menu_maybe_vet_and_track(df)


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
    top = _prompt_int("How many rows to show", 20)
    limit = max(top, 100)
    df = fetch_screener(filters, limit=limit)
    print("\n" + render_screener(df, top, limit=limit) + "\n")
    _menu_maybe_save_csv(df)
    _menu_maybe_vet_and_track(df)


def _menu_maybe_save_csv(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    suggested = default_desktop_dir() / "finviz-research" / f"screener_{_stamp()}.csv"
    save = _prompt(
        "Save the full results to a CSV file? (Enter for Desktop, or type a path, or 'n' to skip)",
        str(suggested),
    )
    if save.lower() in ("n", "no", "skip"):
        print()
        return
    _save_csv(df, save)
    print(f"saved -> {save}\n")


def _menu_maybe_vet_and_track(df: pd.DataFrame) -> None:
    """The step the screener alone can't do: a filter match is not a scored
    setup. Offers to run the same EMA/RSI/MA/52-week vetting `check` runs on
    the watchlist against these screener hits instead, then offers to add
    whatever clears 2+ signal confluence -- not everything shown -- to the
    watchlist."""
    symbols = screener_tickers(df, limit=SCREENER_VET_LIMIT)
    if not symbols:
        return
    do_vet = _prompt(
        f"Vet these {len(symbols)} ticker(s) against timing signals "
        "(EMA/RSI/MA/52-week, same as 'check')? (y/n)",
        "y",
    )
    if do_vet.lower() not in ("y", "yes"):
        return
    print(f"\nChecking {len(symbols)} ticker(s) ...")
    report, flagged = vet_candidates(symbols)
    print("\n" + report)
    if not flagged:
        print("Nothing flagged -- none of these cleared 2+ signal confluence today.\n")
        return
    add = _prompt(
        f"Add the {len(flagged)} flagged ticker(s) to your watchlist "
        f"({', '.join(flagged)})? (y/n)",
        "y",
    )
    if add.lower() in ("y", "yes"):
        path = Path(DEFAULT_WATCHLIST_FILE)
        save_watchlist(path, load_watchlist(path) + flagged)
        print(f"added -> {path}\n")


def _menu_lookup() -> None:
    ticker = _prompt("Ticker symbol, e.g. AAPL").upper()
    if not ticker:
        return
    result = fetch_lookup(ticker)
    print("\n" + render_lookup(ticker, **result) + "\n")
    suggested = default_desktop_dir() / "finviz-research" / f"{ticker}_{_stamp()}.json"
    save = _prompt(
        "Save this to a file? (Enter for Desktop, or type a path, or 'n' to skip)",
        str(suggested),
    )
    if save.lower() in ("n", "no", "skip"):
        return
    out_path = Path(save)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_lookup_json(result), encoding="utf-8")
    print(f"saved -> {out_path}\n")


def _menu_watchlist_manage() -> None:
    path = Path(DEFAULT_WATCHLIST_FILE)
    while True:
        symbols = load_watchlist(path)
        print(
            f"\nWatchlist ({len(symbols)}): {', '.join(symbols) if symbols else '(empty)'}"
        )
        print("  a) add ticker(s)   r) remove ticker(s)   b) back to the main menu")
        pick = _prompt("Choose", "b")
        if pick in ("b", "back", ""):
            return
        if pick == "a":
            raw = _prompt("Ticker(s) to add, space-separated, e.g. AAPL MSFT")
            add = {s.upper() for s in raw.split() if s.strip()}
            if add:
                save_watchlist(path, symbols + list(add))
        elif pick == "r":
            raw = _prompt("Ticker(s) to remove, space-separated")
            remove = {s.upper() for s in raw.split() if s.strip()}
            save_watchlist(path, [s for s in symbols if s not in remove])
        else:
            print("Not a choice on the list.")


def _menu_watchlist_check() -> None:
    path = Path(DEFAULT_WATCHLIST_FILE)
    symbols = load_watchlist(path)
    if not symbols:
        print("Watchlist is empty -- add tickers first (option 6).\n")
        return
    print(f"Checking {len(symbols)} ticker(s): {', '.join(symbols)} ...")
    report, _flagged = check_watchlist(symbols)
    print("\n" + report)

    suggested = (
        default_desktop_dir() / "finviz-research" / f"watchlist-check_{_stamp()}.txt"
    )
    save = _prompt(
        "Save this report? (Enter for Desktop, or type a path, or 'n' to skip)",
        str(suggested),
    )
    if save.lower() in ("n", "no", "skip"):
        return
    out_path = Path(save)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"saved -> {out_path}\n")


def main(argv=None):
    # Piped output (the scheduled task's log) is cp1252 on Windows; a headline
    # with a curly quote would otherwise crash the whole run mid-print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

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
    p.add_argument(
        "--vet",
        action="store_true",
        help=(
            "after screening, run the same EMA/RSI/MA/52-week vetting as "
            "'check' on the matched tickers and report which look like a "
            "live setup, not just a filter match"
        ),
    )
    p.add_argument(
        "--add-flagged",
        action="store_true",
        help="like --vet, and also add the flagged tickers to the watchlist",
    )
    p.add_argument(
        "--vet-limit",
        type=int,
        default=SCREENER_VET_LIMIT,
        help="max tickers to vet -- each is its own yfinance fetch",
    )
    p.add_argument(
        "--file",
        default=DEFAULT_WATCHLIST_FILE,
        help="watchlist file --add-flagged writes to",
    )
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

    p = sub.add_parser("watchlist", help="add/remove/view the tracked-ticker list")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("tickers", nargs="*", help="ticker symbols (not needed for 'list')")
    p.add_argument(
        "--file", default=DEFAULT_WATCHLIST_FILE, help="watchlist file to use"
    )
    p.set_defaults(func=cmd_watchlist)

    p = sub.add_parser(
        "check",
        help="EMA/RSI/volume/52-week signals on the watchlist -- see module docstring",
    )
    p.add_argument(
        "--file", default=DEFAULT_WATCHLIST_FILE, help="watchlist file to read"
    )
    p.add_argument("--out-dir", help="where to save the report (default: your Desktop)")
    p.add_argument(
        "--quiet", action="store_true", help="skip the extra 'N flagged' summary line"
    )
    p.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

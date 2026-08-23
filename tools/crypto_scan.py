#!/usr/bin/env python
"""Scan liquid crypto pairs for momentum setups worth a paper trade.

This is the "trade crypto" command: it ranks a universe of liquid USD pairs
by signals with published evidence behind them and shows which are set up to
move — majority-upside and majority-downside candidates, plus the market
regime they'd be trading against. It is a screener, not a trader: every
candidate still goes through the pre-trade pack, the trade card's gates, and
the journal's locked thesis, and until the paper record says otherwise,
trades stay on paper.

The signals, and why these and not others:

    momentum    1-week and 4-week past return. Time-series momentum is the
                one crypto anomaly with peer-reviewed support (Liu &
                Tsyvinski, RFS 2021: 1-4 week lookbacks); longer lookbacks
                mean-revert.
    trend       Distance from the 50-day moving average — regime context for
                the momentum number, not an independent edge.
    volume      Recent volume vs. its own 30-day norm. Liu-Tsyvinski found
                volume-related predictability; a move on rising volume is
                more trustworthy than one on air.
    volatility  ATR% — not a direction signal at all. It is reported so
                position sizing and stop distance are set from the coin's
                actual daily range, and so a "mover" isn't just a coin that
                always moves 8% a day.
    btc regime  Alts are effectively one trade: correlation to BTC is high
                enough that an alt long against falling BTC is a bet against
                the whole market's direction. The scan says so out loud.

Scores are cross-sectional ranks (who looks strongest *relative to the rest
of the universe today*), so no threshold needs re-tuning as volatility
regimes change.

Everything scoring-related is pure math over a DataFrame and tested with
synthetic data (tests/test_crypto_scan.py); only `scan` with live tickers
touches the network.

Examples::

    python tools/crypto_scan.py scan                      # default universe, live
    python tools/crypto_scan.py scan --top 5
    python tools/crypto_scan.py scan --symbols BTC-USD ETH-USD SOL-USD
    python tools/crypto_scan.py scan --csv-dir bars/      # offline, cached CSVs
    python tools/crypto_scan.py fetch --out-dir bars/     # cache bars for offline use
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

# Liquid, boring, on yfinance. Deliberately no micro-caps: the scan looks for
# movers among coins that can actually be traded at the shown price.
DEFAULT_UNIVERSE = [
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "XRP-USD",
    "BNB-USD",
    "ADA-USD",
    "DOGE-USD",
    "AVAX-USD",
    "LINK-USD",
    "DOT-USD",
    "LTC-USD",
    "BCH-USD",
    "ATOM-USD",
    "NEAR-USD",
    "TRX-USD",
]

HISTORY_DAYS = 120  # enough for the 50d SMA plus a 30d volume norm
MIN_BARS = 60  # below this a coin is skipped, not guessed at


# ---------------------------------------------------------------------------
# Per-coin signals — pure functions of a bars DataFrame
# ---------------------------------------------------------------------------


def coin_signals(bars: pd.DataFrame) -> dict:
    """Signals for one coin from daily bars (columns: close, high, low,
    volume; oldest first). Returns NaNs where history is too short rather
    than inventing numbers."""
    close = bars["close"].astype(float)
    n = len(close)
    if n < MIN_BARS:
        raise ValueError(f"need {MIN_BARS} bars, have {n}")
    last = float(close.iloc[-1])

    ret_7d = last / float(close.iloc[-8]) - 1
    ret_28d = last / float(close.iloc[-29]) - 1
    sma50 = float(close.iloc[-50:].mean())
    trend = last / sma50 - 1

    vol = bars["volume"].astype(float)
    # Baseline excludes the last week so a genuine surge is measured against
    # normal turnover, not partly against itself.
    vol_norm = float(vol.iloc[-37:-7].mean())
    volume_surge = (
        float(vol.iloc[-7:].mean()) / vol_norm - 1 if vol_norm > 0 else float("nan")
    )

    high, low = bars["high"].astype(float), bars["low"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_pct = float(true_range.iloc[-14:].mean()) / last

    return {
        "last": last,
        "ret_7d": ret_7d,
        "ret_28d": ret_28d,
        "trend_vs_sma50": trend,
        "volume_surge": volume_surge,
        "atr_pct": atr_pct,
    }


def _rank_pct(values: pd.Series) -> pd.Series:
    """Rank to [0, 1], NaN-safe: NaN in, NaN out, others ranked among themselves."""
    return values.rank(pct=True)


def score_universe(signals: pd.DataFrame) -> pd.DataFrame:
    """Composite direction score per coin from a frame of coin_signals rows
    (index = symbol). Score is the average cross-sectional rank of the two
    momentum horizons, trend, and volume surge, mapped to [-1, +1]: positive
    means set up to the upside relative to the rest of the universe.

    Volatility is deliberately not in the score — a coin isn't a better long
    for being wilder. It rides along for sizing.
    """
    ranks = pd.concat(
        {
            "ret_7d": _rank_pct(signals["ret_7d"]),
            "ret_28d": _rank_pct(signals["ret_28d"]),
            "trend_vs_sma50": _rank_pct(signals["trend_vs_sma50"]),
            "volume_surge": _rank_pct(signals["volume_surge"]),
        },
        axis=1,
    )
    out = signals.copy()
    out["score"] = (ranks.mean(axis=1, skipna=True) - 0.5) * 2
    # A relative rank means nothing when the absolute move is dead flat, so
    # direction additionally requires the coin itself to have actually moved.
    out["setup"] = "neutral"
    out.loc[(out["score"] >= 0.25) & (out["ret_7d"] > 0), "setup"] = "upside"
    out.loc[(out["score"] <= -0.25) & (out["ret_7d"] < 0), "setup"] = "downside"
    return out.sort_values("score", ascending=False)


def btc_regime(signals: pd.DataFrame) -> str:
    """One line on the tide every alt trade swims in."""
    if "BTC-USD" not in signals.index:
        return "BTC not in scan universe; alt signals lack their usual anchor."
    r7 = signals.loc["BTC-USD", "ret_7d"]
    trend = signals.loc["BTC-USD", "trend_vs_sma50"]
    if r7 != r7 or trend != trend:
        return "BTC history incomplete; regime unknown."
    direction = "rising" if r7 > 0 else "falling"
    posture = "above" if trend > 0 else "below"
    line = f"BTC {direction} on the week ({r7:+.1%}) and {posture} its 50d average ({trend:+.1%})."
    if r7 < 0:
        line += " Alt longs here fight the whole market's direction — correlation makes alts one trade."
    return line


def render_scan(scored: pd.DataFrame, regime: str, top: int, skipped: list[str]) -> str:
    lines = [regime, ""]
    header = f"{'symbol':<10} {'score':>6} {'7d':>8} {'28d':>8} {'vs 50d':>8} {'volume':>8} {'ATR%/d':>7}  setup"
    lines.append(header)
    lines.append("-" * len(header))
    for sym, row in scored.head(top).iterrows():
        lines.append(_render_row(sym, row))
    tail = scored.tail(top)
    if len(scored) > top:
        lines.append("...")
        for sym, row in tail.iterrows():
            if sym not in scored.head(top).index:
                lines.append(_render_row(sym, row))
    if skipped:
        lines.append(f"\nskipped (insufficient history): {', '.join(skipped)}")
    lines += [
        "",
        "Ranks are relative to this universe today, not forecasts. Anything here",
        "is a candidate for a pre-trade pack and a paper trade against a locked",
        "thesis — position sized from ATR%, never from conviction.",
    ]
    return "\n".join(lines)


def _render_row(sym: str, row) -> str:
    def pct(v):
        return "   n/a" if v != v else f"{v:+7.1%}"

    return (
        f"{sym:<10} {row['score']:>+6.2f} {pct(row['ret_7d']):>8} {pct(row['ret_28d']):>8} "
        f"{pct(row['trend_vs_sma50']):>8} {pct(row['volume_surge']):>8} {row['atr_pct']:>6.1%}  {row['setup']}"
    )


# ---------------------------------------------------------------------------
# Data plumbing — the only part that touches the network
# ---------------------------------------------------------------------------


def fetch_daily(
    symbols: list[str], days: int = HISTORY_DAYS
) -> dict[str, pd.DataFrame]:
    """Daily bars per symbol via yfinance. Missing/empty symbols are dropped
    silently here and reported by the caller as skipped."""
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


def load_csv_dir(path: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Offline source: one <SYMBOL>.csv per coin, yfinance-shaped or canonical."""
    out = {}
    for sym in symbols:
        f = path / f"{sym}.csv"
        if f.exists():
            df = pd.read_csv(f)
            df.columns = [c.lower() for c in df.columns]
            out[sym] = df[
                [
                    c
                    for c in ("open", "high", "low", "close", "volume")
                    if c in df.columns
                ]
            ]
    return out


def run_scan(bars_by_symbol: dict[str, pd.DataFrame], top: int) -> str:
    rows, skipped = {}, []
    for sym, bars in bars_by_symbol.items():
        try:
            rows[sym] = coin_signals(bars)
        except ValueError:
            skipped.append(sym)
    if not rows:
        raise SystemExit("no symbol had enough history to scan")
    signals = pd.DataFrame(rows).T
    scored = score_universe(signals)
    return render_scan(scored, btc_regime(signals), top, skipped)


def cmd_scan(args):
    symbols = args.symbols or DEFAULT_UNIVERSE
    if args.csv_dir:
        bars = load_csv_dir(Path(args.csv_dir), symbols)
    else:
        bars = fetch_daily(symbols)
    print(run_scan(bars, args.top))


def cmd_fetch(args):
    symbols = args.symbols or DEFAULT_UNIVERSE
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bars = fetch_daily(symbols)
    for sym, df in bars.items():
        df.to_csv(out_dir / f"{sym}.csv", index=False)
    print(f"cached {len(bars)}/{len(symbols)} symbols -> {out_dir}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="crypto_scan",
        description="Rank liquid crypto pairs by evidence-backed momentum signals.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="rank the universe and show setups")
    p.add_argument("--symbols", nargs="+", help="override the default universe")
    p.add_argument("--top", type=int, default=8, help="rows to show from each end")
    p.add_argument("--csv-dir", help="read cached CSVs instead of fetching live")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("fetch", help="cache daily bars to CSVs for offline scans")
    p.add_argument("--symbols", nargs="+")
    p.add_argument("--out-dir", default="crypto_bars")
    p.set_defaults(func=cmd_fetch)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

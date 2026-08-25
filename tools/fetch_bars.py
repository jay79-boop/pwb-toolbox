"""Intraday OHLCV from yfinance, in the shape the bar labs already read.

``tools/backtest_lab.py`` and ``tools/vwap_lab.py`` read a CSV with a ``time``
column and OHLCV columns, and ``--vendor generic`` reads that column as UTC.
So this writes **naive UTC** stamps and nothing else: the vendor-timezone trap
that cost this repo an eight-year backtest cannot bite a file whose stamps
were never local in the first place.

``tools/season_scan.py fetch`` already covers daily bars. This covers
intraday, which is what VWAP actually needs -- a session VWAP over daily bars
is one point per day.

    python tools/fetch_bars.py SPY --interval 5m --period 60d --out spy.csv
    python tools/vwap_lab.py spy.csv --vendor generic --mintick 0.01

Use ``--mintick 0.01`` for a penny-quoted equity or ETF and ``0.25`` for ES.

Two caps worth knowing before reading a short run as a short history. Yahoo
limits intraday history hard and *silently* -- roughly 7 days of 1m bars and
60 days of 5m -- so asking for more returns the cap rather than an error, and
the row count and date range are printed for the run to be read against. And
Yahoo is a single vendor, so a file from here cannot clear the two-vendor
noise floor on its own; it is a real-volume feed to develop against, not
evidence that an edge survived data sourcing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def normalise(frame):
    """A yfinance frame -> the labs' naive-UTC OHLCV shape.

    Kept pure so the reshaping is tested without a network call, which the
    suite is not allowed to make. Current yfinance returns MultiIndex columns
    even for a single ticker and older versions return a flat index; both
    arrive here and leave identical.
    """
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).strip().lower() for c in out.columns]
    out = out.loc[:, ~out.columns.duplicated()]

    missing = [c for c in COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"feed is missing {', '.join(missing)}")
    out = out[COLUMNS]

    index = pd.DatetimeIndex(out.index)
    # Intraday bars arrive tz-aware in the exchange's own zone; daily ones
    # arrive naive. Convert the first and leave the second alone -- localising
    # a naive stamp to a guessed zone is the exact error this file avoids.
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    out.index = index

    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out.dropna(subset=COLUMNS)


def zero_volume_share(frame) -> float:
    """The fraction of bars carrying no volume -- the TWAP-degeneracy gauge."""
    if not len(frame):
        return 0.0
    return float((frame["volume"] <= 0).mean())


def fetch(symbol, interval="5m", period="60d"):
    """Download and reshape. The only function here that touches the network."""
    try:
        import yfinance as yf
    except ImportError:  # pragma: no cover - import guard
        raise SystemExit("fetch needs yfinance:  python -m pip install yfinance")

    frame = yf.download(
        symbol, interval=interval, period=period, progress=False, auto_adjust=False
    )
    if frame is None or not len(frame):
        raise SystemExit(
            f"no bars came back for {symbol} at {interval}/{period} -- check the "
            "symbol, and note that Yahoo serves intraday history only for "
            "recent windows"
        )
    return normalise(frame)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbol", help="a Yahoo ticker, e.g. SPY or ES=F")
    ap.add_argument(
        "--interval", default="5m", help="1m, 2m, 5m, 15m, 30m or 60m (default 5m)"
    )
    ap.add_argument(
        "--period", default="60d", help="Yahoo caps this per interval (default 60d)"
    )
    ap.add_argument("--out", default=None, help="CSV path (default <symbol>.csv)")
    args = ap.parse_args(argv)

    bars = fetch(args.symbol, args.interval, args.period)
    out = Path(args.out or f"{args.symbol}.csv")
    bars.reset_index(names="time").to_csv(out, index=False)

    share = zero_volume_share(bars)
    print(f"{len(bars)} bars -> {out}")
    print(f"  {bars.index[0]} .. {bars.index[-1]}  (naive UTC)")
    print(f"  zero-volume bars: {100 * share:.1f}%")
    if share > 0.5:
        print(
            "  WARNING: this feed would make VWAP a TWAP. Pick an instrument "
            "that reports real volume before reading any VWAP result off it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
the row count and date range are printed for the run to be read against. Its
crypto bars are worse than that: BTC-USD came back 50% zero-volume, which
makes a VWAP built on it a TWAP with corrupted bands.

``--exchange`` switches to ccxt and fixes both. An exchange reports the
volume it actually matched, and serves years rather than sixty days --
and since two exchanges quoting one pair are genuinely two vendors, this is
what makes ``noise_floor`` computable at all::

    python tools/fetch_bars.py BTC/USDT --exchange binance  --days 365 --out a.csv
    python tools/fetch_bars.py BTC/USDT --exchange coinbase --days 365 --out b.csv
    python tools/vwap_lab.py a.csv --vendor generic --second b.csv \
        --crypto --mintick 6.5

Read the mintick line each source prints rather than copying one: mintick is
the per-trade slippage in price units, so a tick borrowed across instruments
quoting orders of magnitude apart silently stops charging a real cost.
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


def normalise_ccxt(rows):
    """ccxt's ``[ms, open, high, low, close, volume]`` rows -> the labs' frame.

    ccxt stamps in UTC milliseconds, the one timestamp format that cannot be
    misread: there is no local zone to guess at and no DST to get wrong. The
    result is the same naive-UTC index the yfinance path produces, so a file
    from either source reads identically under ``--vendor generic``.
    """
    if not len(rows):
        raise ValueError("no rows to normalise")
    frame = pd.DataFrame(list(rows), columns=["ms", *COLUMNS])
    frame.index = pd.to_datetime(frame["ms"], unit="ms")
    frame = frame.drop(columns=["ms"])
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    return frame.dropna(subset=COLUMNS)


def _ccxt_client(exchange):  # pragma: no cover - import and construction guard
    try:
        import ccxt
    except ImportError:
        raise SystemExit("--exchange needs ccxt:  python -m pip install ccxt")
    if not hasattr(ccxt, exchange):
        raise SystemExit(f"ccxt has no exchange named {exchange!r}")
    return getattr(ccxt, exchange)({"enableRateLimit": True})


def fetch_ccxt(
    symbol,
    exchange="binance",
    timeframe="5m",
    days=365,
    limit=1000,
    client=None,
    progress=None,
):
    """Paginated OHLCV from one exchange, as the labs' naive-UTC frame.

    Exchanges serve a bounded window per call, so years of 5-minute bars mean
    walking forward a batch at a time. ``client`` is injectable precisely so
    that walk -- the loop, the dedup across overlapping batches, and the two
    ways it has to terminate -- is tested without a network call, the same way
    ``pwb_toolbox.scraping`` tests its HTTP.

    Termination is on *fresh rows*, not on a row count: an exchange that keeps
    answering with bars already collected would otherwise spin forever, and
    that is the failure a bad ``since`` actually produces.
    """
    ex = client if client is not None else _ccxt_client(exchange)
    step = ex.parse_timeframe(timeframe) * 1000
    now = ex.milliseconds()
    since = now - int(days * 86_400_000)

    rows, seen = [], set()
    while since < now:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not batch:
            break
        fresh = [r for r in batch if r[0] not in seen]
        if not fresh:
            break
        seen.update(r[0] for r in fresh)
        rows.extend(fresh)
        if progress is not None:
            progress(len(rows))
        since = batch[-1][0] + step

    if not rows:
        raise SystemExit(f"no bars came back for {symbol} on {exchange}")
    return normalise_ccxt(rows)


def short_history_warning(bars, days, exchange, tolerance=0.8):
    """Whether an exchange served materially less history than was asked for.

    Some exchanges cap OHLCV depth however far back ``since`` reaches --
    Kraken answers with roughly the last 720 intervals -- and they do it by
    responding *successfully* with the cap rather than by erroring. A run then
    quietly measures one month where three years were requested, which is the
    same shape as Yahoo's silent period cap: a wrong answer that looks like a
    right one. Returns ``None`` when the history is full enough to be read as
    asked for.
    """
    if len(bars) < 2:
        return None
    span = (bars.index[-1] - bars.index[0]).total_seconds() / 86_400.0
    if span >= days * tolerance:
        return None
    return (
        f"WARNING: asked {exchange} for {days:,.0f} days of history and got "
        f"{span:,.0f}. Some exchanges cap OHLCV depth however far back you "
        "ask, and answer with the cap rather than erroring. Read this as a "
        f"{span:,.0f}-day run, or fetch from an exchange that serves more."
    )


def mintick_for_bp(price, bp=1.0):
    """The ``--mintick`` that charges ``bp`` basis points of slippage.

    ``backtest_lab.backtest`` sets slippage to ``slip_ticks * mintick`` in
    price units, and ``slip_ticks`` defaults to 1.0 -- so mintick *is* the
    per-trade slippage, and its cost in basis points depends entirely on the
    instrument's quote. A tick copied from a futures example onto an
    instrument quoting three orders of magnitude higher charges almost
    nothing, and a frictionless run measures nothing tradeable.
    """
    return price * bp / 1e4


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
    ap.add_argument("symbol", help="a Yahoo ticker (SPY) or ccxt pair (BTC/USDT)")
    ap.add_argument(
        "--exchange",
        default=None,
        help="a ccxt exchange (binance, coinbase, kraken). Switches source off "
        "yfinance: real matched volume, years of history, and a second one of "
        "these is a genuine second vendor for the noise floor",
    )
    ap.add_argument(
        "--interval", default="5m", help="1m, 2m, 5m, 15m, 30m or 60m (default 5m)"
    )
    ap.add_argument(
        "--period", default="60d", help="yfinance only; Yahoo caps it per interval"
    )
    ap.add_argument(
        "--days", type=float, default=365.0, help="--exchange only (default 365)"
    )
    ap.add_argument("--out", default=None, help="CSV path (default <symbol>.csv)")
    args = ap.parse_args(argv)

    if args.exchange:
        seen = [0]

        def progress(n):
            if n - seen[0] >= 20_000:
                seen[0] = n
                print(f"  ...{n:,} bars", flush=True)

        bars = fetch_ccxt(
            args.symbol,
            exchange=args.exchange,
            timeframe=args.interval,
            days=args.days,
            progress=progress,
        )
    else:
        bars = fetch(args.symbol, args.interval, args.period)
    out = Path(args.out or f"{args.symbol.replace('/', '-')}.csv")
    bars.reset_index(names="time").to_csv(out, index=False)

    if args.exchange:
        stale = short_history_warning(bars, args.days, args.exchange)
        if stale:
            print(stale)

    share = zero_volume_share(bars)
    price = float(bars["close"].mean())
    print(f"{len(bars)} bars -> {out}")
    print(f"  {bars.index[0]} .. {bars.index[-1]}  (naive UTC)")
    print(f"  zero-volume bars: {100 * share:.1f}%")
    print(
        f"  mean price {price:,.2f} -> --mintick {mintick_for_bp(price):,.4g} "
        "charges ~1bp per trade"
    )
    if share > 0.5:
        print(
            "  WARNING: this feed would make VWAP a TWAP. Pick an instrument "
            "that reports real volume before reading any VWAP result off it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

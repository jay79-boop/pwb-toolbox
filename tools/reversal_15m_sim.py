"""Reference simulator for The Purpose Driven Trader's 15-Minute Reversal.

This is the executable spec for ``pine/purpose_driven_15m_reversal.pine``. The Pine
runs on TradingView and cannot be executed from a container, so this module exists to
prove the *state machine* is coherent — commitment, the skipping of counter-trend
failure swings, the body-extreme entry, the 2.4 reward-to-risk stop, one trade per day,
the Friday skip and the 15:55 flatten — against bars a test can construct by hand.

It does not prove the Pine compiles, and it is not a port anything imports: it is a
second reading of the same written rules, kept beside them so a rule can be changed in
one place and checked in the other.

Order emulation follows TradingView's broker emulator closely enough for the counts to
mean something:

* an entry stop placed at the close of the setup bar can only fill on a LATER bar;
* a stop order fills at its own price, or at the bar's open when the bar gaps past it;
* if a bar touches both the target and the protective stop, the stop is assumed first;
* the flatten is a market order, so it fills at the OPEN of the bar after the one that
  straddles the flatten time.

Run it over a CSV of 15-minute bars (``timestamp,open,high,low,close``, timestamps in
America/New_York or with an offset)::

    python tools/reversal_15m_sim.py bars.csv --rr 2.4 --sma-length 60
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from datetime import date, datetime, time
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

LONG = 1
SHORT = -1


@dataclass(frozen=True)
class Bar:
    """One 15-minute bar, stamped with the time it OPENED, in America/New_York."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def open_minute(self) -> int:
        return self.ts.hour * 60 + self.ts.minute

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)


@dataclass
class Trade:
    day: date
    direction: int
    setup_ts: datetime
    entry_ts: datetime
    entry: float
    exit_ts: datetime
    exit: float
    target: float
    stop: float
    reason: str

    @property
    def points(self) -> float:
        return (self.exit - self.entry) * self.direction

    @property
    def r_multiple(self) -> float:
        risk = abs(self.entry - self.stop)
        return self.points / risk if risk else 0.0


@dataclass
class DayResult:
    """What the strategy did with one session, including the days it did nothing."""

    day: date
    candle1: Bar | None = None
    skipped_reason: str | None = None
    committed_direction: int | None = None
    setup_ts: datetime | None = None
    entry: float | None = None
    target: float | None = None
    stop: float | None = None
    trade: Trade | None = None


@dataclass
class Config:
    candle1: time = time(9, 15)
    flatten: time = time(15, 55)
    sma_length: int = 60
    use_sma: bool = True
    skip_friday: bool = True
    reward_risk: float = 2.4
    bar_minutes: int = 15


def _minute(t: time) -> int:
    return t.hour * 60 + t.minute


def daily_sma(bars: Sequence[Bar], length: int) -> dict[date, float]:
    """Trend value available *during* each session.

    ``request.security(..., "D", ta.sma(close, n), lookahead=barmerge.lookahead_off)``
    cannot see the forming daily bar, so an intraday bar is compared against the SMA of
    the last CLOSED daily bar. Reproducing that here is the difference between an honest
    filter and one that peeks at its own day's close.
    """
    closes: dict[date, float] = {}
    for bar in bars:
        closes[bar.ts.date()] = bar.close

    days = sorted(closes)
    out: dict[date, float] = {}
    for i, day in enumerate(days):
        prior = [closes[d] for d in days[max(0, i - length) : i]]
        if len(prior) == length:
            out[day] = sum(prior) / length
    return out


def simulate(
    bars: Sequence[Bar],
    config: Config | None = None,
    sma: dict[date, float] | None = None,
) -> list[DayResult]:
    """Walk the bars one session at a time and report what each day did."""
    cfg = config or Config()
    if sma is None:
        sma = daily_sma(bars, cfg.sma_length) if cfg.use_sma else {}

    c1_min = _minute(cfg.candle1)
    flat_min = _minute(cfg.flatten)

    by_day: dict[date, list[Bar]] = {}
    for bar in bars:
        by_day.setdefault(bar.ts.date(), []).append(bar)

    results: list[DayResult] = []
    for day in sorted(by_day):
        results.append(_run_day(day, by_day[day], cfg, c1_min, flat_min, sma))
    return results


def _run_day(
    day: date,
    day_bars: list[Bar],
    cfg: Config,
    c1_min: int,
    flat_min: int,
    sma: dict[date, float],
) -> DayResult:
    result = DayResult(day=day)

    if cfg.skip_friday and day.weekday() == 4:
        result.skipped_reason = "friday"
        return result

    day_bars = sorted(day_bars, key=lambda b: b.ts)
    candle1 = next((b for b in day_bars if b.open_minute == c1_min), None)
    if candle1 is None:
        # No opening-range bar means no strategy. On a regular-hours chart this is every
        # single day, which is why an empty backtest points at the chart, not the rules.
        result.skipped_reason = "no candle 1"
        return result
    result.candle1 = candle1

    trend = sma.get(day) if cfg.use_sma else None
    if cfg.use_sma and trend is None:
        result.skipped_reason = "sma warming up"
        return result

    scan = [
        b
        for b in day_bars
        if c1_min < b.open_minute < flat_min
        and b.open_minute + cfg.bar_minutes <= flat_min
    ]

    setup: Bar | None = None
    direction = 0
    for bar in scan:
        long_fail = (
            bar.low < candle1.low
            and bar.close > candle1.low
            and bar.high < candle1.high
        )
        short_fail = (
            bar.high > candle1.high
            and bar.close < candle1.high
            and bar.low > candle1.low
        )
        # A failure swing that disagrees with the trend is skipped outright: it does not
        # use up the day, so a later aligned one can still commit it.
        if long_fail and (trend is None or bar.close > trend):
            setup, direction = bar, LONG
            break
        if short_fail and (trend is None or bar.close < trend):
            setup, direction = bar, SHORT
            break

    if setup is None:
        result.skipped_reason = "no aligned failure swing"
        return result

    if direction == LONG:
        entry = setup.body_high
        target = candle1.high
        stop = entry - (target - entry) / cfg.reward_risk
    else:
        entry = setup.body_low
        target = candle1.low
        stop = entry + (entry - target) / cfg.reward_risk

    result.committed_direction = direction
    result.setup_ts = setup.ts
    result.entry, result.target, result.stop = entry, target, stop

    result.trade = _fill(
        day, day_bars, setup, direction, entry, target, stop, flat_min, cfg.bar_minutes
    )
    return result


def _intrabar_path(bar: Bar) -> list[float]:
    """The price path TradingView's emulator assumes inside one bar.

    It has only OHLC to work with, so it guesses the order: when the open sits closer
    to the high, the bar is taken to have run open -> high -> low -> close, otherwise
    open -> low -> high -> close. Guessing matters here because the entry stop and the
    protective stop can both be inside the same 15-minute bar, and assuming the worst
    unconditionally invents losses on every bar that filled the entry on its way up.
    """
    if abs(bar.open - bar.high) <= abs(bar.open - bar.low):
        return [bar.open, bar.high, bar.low, bar.close]
    return [bar.open, bar.low, bar.high, bar.close]


def _fill(
    day: date,
    day_bars: list[Bar],
    setup: Bar,
    direction: int,
    entry: float,
    target: float,
    stop: float,
    flat_min: int,
    bar_minutes: int = 15,
) -> Trade | None:
    """Walk every bar after the setup along its assumed path, in order.

    The entry stop is live from the first of those bars; the bracket only becomes live
    at the point on the path where the entry actually filled, so a low that printed
    *before* the entry cannot retroactively stop the trade out.
    """
    after = [b for b in day_bars if b.ts > setup.ts]

    reached = (
        (lambda p, lvl: p >= lvl) if direction == LONG else (lambda p, lvl: p <= lvl)
    )
    fallen = (
        (lambda p, lvl: p <= lvl) if direction == LONG else (lambda p, lvl: p >= lvl)
    )

    entry_ts: datetime | None = None
    entry_px = entry

    def done(ts: datetime, px: float, reason: str) -> Trade:
        return Trade(
            day=day,
            direction=direction,
            setup_ts=setup.ts,
            entry_ts=entry_ts,
            entry=entry_px,
            exit_ts=ts,
            exit=px,
            target=target,
            stop=stop,
            reason=reason,
        )

    for i, bar in enumerate(after):
        if _straddles_flatten(bar, flat_min, bar_minutes):
            if entry_ts is None:
                return None  # 15:55: the unfilled order is cancelled, no trade today
            nxt = after[i + 1] if i + 1 < len(after) else None
            return done(
                nxt.ts if nxt else bar.ts,
                nxt.open if nxt else bar.close,
                "flatten",
            )

        path = _intrabar_path(bar)
        for j, price in enumerate(path):
            if entry_ts is None:
                if reached(price, entry):
                    # j == 0 means the bar opened past the stop: it fills at the open.
                    entry_px = path[0] if j == 0 else entry
                    entry_ts = bar.ts
                continue
            if fallen(price, stop):
                return done(bar.ts, path[0] if j == 0 else stop, "stop")
            if reached(price, target):
                return done(bar.ts, path[0] if j == 0 else target, "target")

    if entry_ts is None:
        return None
    last = after[-1]
    return done(last.ts, last.close, "session end")


def _straddles_flatten(bar: Bar, flat_min: int, bar_minutes: int = 15) -> bool:
    return bar.open_minute < flat_min <= bar.open_minute + bar_minutes


def summarize(results: Iterable[DayResult]) -> dict[str, float | int]:
    results = list(results)
    trades = [r.trade for r in results if r.trade]
    wins = [t for t in trades if t.points > 0]
    rs = [t.r_multiple for t in trades]
    return {
        "days": len(results),
        "days_with_candle_1": sum(1 for r in results if r.candle1),
        "days_committed": sum(1 for r in results if r.committed_direction),
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "total_r": round(sum(rs), 2),
        "avg_r": round(statistics.mean(rs), 3) if rs else 0.0,
        "points": round(sum(t.points for t in trades), 2),
    }


def trades_as_records(trades: list[Trade], symbol: str) -> list[dict]:
    """Closed-trade records in the shape the night lab's arithmetic reads.

    This is the bridge that lets the overnight stress lab chew on a backtest
    before a strategy ever risks paper money: entry/stop/exit/direction are
    the exact fields `night_lab.trade_r` computes R from, `lane` keeps the
    sim's trades distinguishable from the desk's in leak-mining, and `opened`
    carries the timestamp patterns (weekday, month) leaks are mined over.
    """
    records = []
    for i, t in enumerate(trades):
        records.append(
            {
                "id": f"sim-{t.day.isoformat()}-{i}",
                "lane": "sim-15m",
                "symbol": symbol,
                "direction": "long" if t.direction > 0 else "short",
                "status": "closed",
                "entry": t.entry,
                "stop": t.stop,
                "exit": t.exit,
                "opened": t.entry_ts.isoformat(),
                "closed": t.exit_ts.isoformat(),
                "reason": t.reason,
            }
        )
    return records


def fetch_bars(symbol: str, days: int, out_path: str) -> int:
    """Fetch 15-minute bars from Yahoo into the CSV shape `read_csv` reads.

    Import is deferred and failure is a message, not a traceback: this is the
    one network-touching path in the file, it runs only on the owner's
    machine (the cloud proxy blocks Yahoo), and the tests never call it.
    Yahoo serves at most 60 days of 15m bars, so `days` is clamped.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance is not installed: python -m pip install yfinance")
        return 1
    days = min(days, 59)
    frame = yf.download(
        symbol, period=f"{days}d", interval="15m", progress=False, auto_adjust=False
    )
    if frame is None or frame.empty:
        print(f"Yahoo returned no 15m bars for {symbol}.")
        return 1
    if hasattr(frame.columns, "levels"):  # yfinance MultiIndex when one ticker
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.tz_convert("America/New_York")
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "open", "high", "low", "close"])
        for ts, row in frame.iterrows():
            writer.writerow(
                [
                    ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                ]
            )
    print(f"Wrote {len(frame)} bars to {out_path}")
    return 0


def read_csv(path: str) -> list[Bar]:
    bars: list[Bar] = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            raw = row.get("timestamp") or row.get("time") or row.get("date")
            ts = datetime.fromisoformat(str(raw))
            ts = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=ET)
            bars.append(
                Bar(
                    ts,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                )
            )
    return sorted(bars, key=lambda b: b.ts)


def _parse_hm(text: str) -> time:
    hh, mm = text.replace(" ", "").split(":")
    return time(int(hh), int(mm))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", help="15-minute bars: timestamp,open,high,low,close")
    ap.add_argument("--candle1", default="09:15")
    ap.add_argument("--flatten", default="15:55")
    ap.add_argument("--sma-length", type=int, default=60)
    ap.add_argument("--no-sma", action="store_true", help="disable the trend filter")
    ap.add_argument("--trade-fridays", action="store_true")
    ap.add_argument("--rr", type=float, default=2.4, help="reward-to-risk")
    ap.add_argument(
        "--fetch",
        metavar="SYMBOL",
        help="fetch 15m bars for SYMBOL from Yahoo into CSV first (needs yfinance; "
        "use ES=F for the electronic session the default 09:15 candle needs)",
    )
    ap.add_argument(
        "--days", type=int, default=55, help="days to fetch (Yahoo caps 15m at 60)"
    )
    ap.add_argument("--symbol", default=None, help="symbol label on exported trades")
    ap.add_argument(
        "--trades-out",
        metavar="PATH",
        help="write closed trades as JSON for the night lab (see night_lab.py plan --sim)",
    )
    args = ap.parse_args(argv)

    if args.fetch:
        rc = fetch_bars(args.fetch, args.days, args.csv)
        if rc:
            return rc

    bars = read_csv(args.csv)
    cfg = Config(
        candle1=_parse_hm(args.candle1),
        flatten=_parse_hm(args.flatten),
        sma_length=args.sma_length,
        use_sma=not args.no_sma,
        skip_friday=not args.trade_fridays,
        reward_risk=args.rr,
    )
    results = simulate(bars, cfg)
    stats = summarize(results)

    if args.trades_out:
        trades = [r.trade for r in results if r.trade]
        symbol = args.symbol or args.fetch or "SIM"
        Path(args.trades_out).write_text(
            json.dumps({"trades": trades_as_records(trades, symbol)}, indent=2),
            encoding="utf-8",
        )
        print(f"{len(trades)} closed trade(s) -> {args.trades_out}")

    width = max(len(k) for k in stats)
    for key, value in stats.items():
        print(f"{key.replace('_', ' '):<{width}}  {value}")
    if stats["days_with_candle_1"] == 0:
        print(
            f"\nNo {args.candle1} ET bar in {stats['days']} days — these are "
            "regular-hours bars. The strategy needs the electronic session."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

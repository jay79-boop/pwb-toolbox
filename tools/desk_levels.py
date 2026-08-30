"""Market structure read from bars, so the desk jobs need no chart to read one.

`premarket` used to get its levels by driving TradingView Desktop, and
`journal` got its screenshots the same way. That is what forced the scheduled
tasks to keep `LogonType: Interactive` -- a task set to run whether the user is
logged on or not gets a logon session with no desktop, and an Electron app has
nowhere to render. See
`docs/decisions/2026-08-29-the-logon-type-is-not-the-bug.md`.

This computes the same four things off bar data instead:

* the prior trading day's range,
* the Asia / London / NY session highs and lows,
* every unmitigated fair value gap, and the order block that produced it,
* where price sits relative to all of them, in basis points.

**Everything a 07:00 gameplan needs is yesterday's completed data.** The prior
day's range and the overnight session levels are settled history by the time
the job fires; only "where price is right now" is live, and that is the least
load-bearing number on the page. So it is reported with the age of the bar it
came from, and never silently.

Two traps this file is built around, both from `docs/backtesting.md`:

**Timestamps.** The only input shape accepted is **naive UTC**, which is what
`tools/fetch_bars.py` writes. Session windows are then built in exchange-local
time through `zoneinfo`, per calendar day, so the tz database handles DST
rather than a flat offset. A flat offset is right in January and an hour out in
July, and it moves the whole session window for eight months of the year --
which is the failure that turned an eight-year backtest from +39 points into
+7. `tests/test_desk_levels.py` plants the same session high in January and in
July and requires both to be found.

**The day boundary is not midnight.** CME index futures run 18:00 ET to 17:00
ET with an hour's break, so "yesterday" for NQ is not a calendar date. A
premarket run of 2026-08-29 already carried this: the NQ daily bar and the
hourly bars disagreed on the Wednesday, and both were right -- the daily close
was the 16:00 settlement while the session traded on to 17:00. The boundary is
explicit here and configurable, and it is never inferred.

    python tools/desk_levels.py levels NQ=F --json out.json
    python tools/desk_levels.py levels NQ=F --csv bars.csv --markdown
    python tools/desk_levels.py chart NQ=F --csv bars.csv --out shot.png

`--csv` reads bars already on disk and makes the whole command offline. Without
it the bars are fetched, which is the only part of this file that touches the
network -- and no test does.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

OHLC = ["open", "high", "low", "close"]

# Exchange-local session windows, as (start, end) in the instrument's own zone.
# ICT killzone conventions, which is the vocabulary jobs/premarket.md is
# written in. `asia` deliberately crosses midnight and is handled as such.
SESSIONS = {
    "asia": (time(20, 0), time(0, 0)),
    "london": (time(2, 0), time(5, 0)),
    "ny_am": (time(9, 30), time(12, 0)),
    "ny_pm": (time(13, 30), time(16, 0)),
}

# CME equity index futures: the trading day runs from 18:00 ET the previous
# evening to 17:00 ET. Not a calendar date, and not inferable from the bars.
DEFAULT_TZ = "America/New_York"
DEFAULT_DAY_BOUNDARY = time(18, 0)

# A level computed from a bar this old is not a level about now. The premarket
# job fires ~90 minutes before the open against a feed that is itself delayed,
# so this is generous on purpose -- it exists to catch a feed that has stopped,
# not to police ordinary delay.
STALE_AFTER = timedelta(hours=6)

# How far into the future a bar may be stamped before it is a fault rather than
# a labelling convention. A bar named by its open time exists before it closes,
# so one interval of slack is ordinary; an hour is not.
FUTURE_BAR_TOLERANCE = timedelta(minutes=60)


# --------------------------------------------------------------- the shapes --


@dataclass
class Level:
    """One named price with its distance from where price is now."""

    name: str
    price: float
    bps_from_price: float | None = None
    side: str | None = None  # 'above' | 'below' | 'at'


@dataclass
class Gap:
    """A three-bar fair value gap."""

    direction: str  # 'bullish' | 'bearish'
    top: float
    bottom: float
    formed_at: str  # ISO, the third bar
    filled_fraction: float = 0.0
    order_block: dict | None = None
    bps_from_price: float | None = None

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class Structure:
    """Everything one instrument's bars say, ready to be written down."""

    symbol: str
    trading_day: str
    last_price: float | None
    last_bar_at: str | None
    bar_age_minutes: float | None
    stale: bool
    prior_day: dict = field(default_factory=dict)
    sessions: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)
    notes: list = field(default_factory=list)


# ------------------------------------------------------------- reading bars --


def read_bars(path):
    """A CSV in the shape `tools/fetch_bars.py` writes: naive UTC + OHLCV.

    Naive stamps are read as UTC and nothing else. An aware index is converted
    and then stripped. Localising a naive stamp to a *guessed* zone is the one
    thing this file will not do -- it is the error that cost this repo an
    eight-year backtest.
    """
    frame = pd.read_csv(path)
    stamp = next(
        (
            c
            for c in frame.columns
            if str(c).strip().lower() in ("time", "date", "datetime")
        ),
        frame.columns[0],
    )
    index = pd.DatetimeIndex(pd.to_datetime(frame[stamp], utc=False))
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    frame = frame.drop(columns=[stamp])
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    missing = [c for c in OHLC if c not in frame.columns]
    if missing:
        raise ValueError(f"{path}: feed is missing {', '.join(missing)}")
    frame.index = index
    return frame[~frame.index.duplicated(keep="first")].sort_index()


def fetch_bars(symbol, interval="15m", period="1mo"):
    """The one network call in this file. No test reaches it."""
    import yfinance as yf

    raw = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    if raw.empty:
        raise ValueError(f"{symbol}: the feed returned no bars for {period}/{interval}")
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    frame.index = index
    return frame[~frame.index.duplicated(keep="first")].sort_index()


# ------------------------------------------------- the day, and its sessions --


def _local(stamps, tz):
    """Naive-UTC stamps -> aware stamps in the exchange's own zone."""
    return pd.DatetimeIndex(stamps).tz_localize("UTC").tz_convert(ZoneInfo(tz))


def trading_days(index, tz=DEFAULT_TZ, boundary=DEFAULT_DAY_BOUNDARY):
    """Which trading day each bar belongs to.

    A bar at or after `boundary` in exchange-local time belongs to the NEXT
    calendar day's session -- which is what makes 18:00 ET Sunday the start of
    Monday's trade rather than the end of Sunday's.

    The conversion runs through `zoneinfo`, so a bar in July and a bar in
    January get the offset each actually had. That is the whole defence against
    the DST trap, and it is why this is not arithmetic on a fixed offset.
    """
    local = _local(index, tz)
    dates = pd.Series(local.date, index=index)
    rolls = pd.Series(local.time, index=index) >= boundary
    return pd.Series(
        [d + timedelta(days=1) if r else d for d, r in zip(dates, rolls)],
        index=index,
    )


def session_bounds(day, name, tz=DEFAULT_TZ):
    """The UTC half-open window `[start, end)` of one session on one day.

    Built per day in local time and then converted, never by adding a constant.
    A window that crosses midnight (Asia) starts on the previous evening.
    """
    start_t, end_t = SESSIONS[name]
    zone = ZoneInfo(tz)
    if start_t >= end_t:  # crosses midnight; Asia opens the evening before
        start_local = datetime.combine(day - timedelta(days=1), start_t, tzinfo=zone)
        end_local = datetime.combine(day, end_t, tzinfo=zone)
    else:
        start_local = datetime.combine(day, start_t, tzinfo=zone)
        end_local = datetime.combine(day, end_t, tzinfo=zone)
    to_utc = lambda d: d.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return to_utc(start_local), to_utc(end_local)


def session_levels(bars, day, tz=DEFAULT_TZ):
    """High/low/open/close per session for one day.

    A session with no bars is reported as `None`, never interpolated and never
    borrowed from a neighbour. An empty overnight is a real and frequent answer
    -- a holiday, a feed that only carries regular hours -- and a plausible
    number in its place is worse than an absent one.
    """
    out = {}
    for name in SESSIONS:
        start, end = session_bounds(day, name, tz)
        window = bars.loc[(bars.index >= start) & (bars.index < end)]
        if window.empty:
            out[name] = None
            continue
        out[name] = {
            "high": float(window["high"].max()),
            "low": float(window["low"].min()),
            "open": float(window["open"].iloc[0]),
            "close": float(window["close"].iloc[-1]),
            "bars": int(len(window)),
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
    return out


def day_range(bars, days, day):
    """One trading day's range, or `None` if that day has no bars."""
    window = bars.loc[days.values == day]
    if window.empty:
        return None
    high = float(window["high"].max())
    low = float(window["low"].min())
    return {
        "high": high,
        "low": low,
        "mid": (high + low) / 2.0,
        "open": float(window["open"].iloc[0]),
        "close": float(window["close"].iloc[-1]),
        "bars": int(len(window)),
    }


# --------------------------------------------------- gaps, and their origins --


def find_gaps(bars):
    """Every three-bar fair value gap, oldest first.

    Bullish: bar[i-1].high < bar[i+1].low, so the middle bar left a window
    price never traded through. Bearish is the mirror. Nothing is scored or
    thresholded here -- filtering is `unmitigated()`'s job, and keeping the two
    apart is what lets a test plant a gap and check it is found before any
    judgement is applied to it.
    """
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    stamps = bars.index
    gaps = []
    for i in range(1, len(bars) - 1):
        if highs[i - 1] < lows[i + 1]:
            gaps.append(
                Gap(
                    "bullish",
                    float(lows[i + 1]),
                    float(highs[i - 1]),
                    stamps[i + 1].isoformat(),
                )
            )
        elif lows[i - 1] > highs[i + 1]:
            gaps.append(
                Gap(
                    "bearish",
                    float(lows[i - 1]),
                    float(highs[i + 1]),
                    stamps[i + 1].isoformat(),
                )
            )
    return gaps


def fill_fraction(gap, bars):
    """How much of the gap later trade has eaten, in [0, 1].

    Measured only against bars AFTER the gap finished forming. The third bar is
    part of the gap's own construction; counting it would mitigate every gap at
    birth.
    """
    span = gap.top - gap.bottom
    if span <= 0:
        return 1.0
    later = bars.loc[bars.index > pd.Timestamp(gap.formed_at)]
    if later.empty:
        return 0.0
    if gap.direction == "bullish":
        deepest = float(later["low"].min())
        eaten = gap.top - max(deepest, gap.bottom)
    else:
        highest = float(later["high"].max())
        eaten = min(highest, gap.top) - gap.bottom
    return float(min(max(eaten / span, 0.0), 1.0))


def unmitigated(gaps, bars, threshold=1.0):
    """Gaps price has not fully traded back through.

    `threshold` is the fraction of the gap that must be eaten before it stops
    counting. The default of 1.0 means only a gap filled edge to edge is
    discarded, which is the loosest reading; pass 0.5 for the stricter
    convention that a gap is done once price reaches its midpoint.
    """
    kept = []
    for gap in gaps:
        gap.filled_fraction = fill_fraction(gap, bars)
        if gap.filled_fraction < threshold:
            kept.append(gap)
    return kept


def order_block(gap, bars):
    """The last opposing candle before the leg that opened this gap.

    Tying the order block to an FVG's own impulse leg rather than to a
    displacement threshold is deliberate: a threshold is a tunable, and a
    tunable in a level is a place for a number to come from nowhere. Here there
    is nothing to tune -- the gap defines the leg, the leg defines the candle.

    Returns `None` when no opposing candle precedes the leg, which is a real
    outcome at the start of a series rather than something to paper over.
    """
    stamps = bars.index
    where = stamps.get_indexer([pd.Timestamp(gap.formed_at)])
    if len(where) == 0 or where[0] < 0:
        return None
    impulse = where[0] - 1  # the middle bar of the three
    opens = bars["open"].to_numpy()
    closes = bars["close"].to_numpy()
    wants_down = gap.direction == "bullish"
    for i in range(impulse - 1, -1, -1):
        is_down = closes[i] < opens[i]
        if is_down == wants_down:
            row = bars.iloc[i]
            return {
                "at": stamps[i].isoformat(),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "body_high": float(max(row["open"], row["close"])),
                "body_low": float(min(row["open"], row["close"])),
            }
    return None


# --------------------------------------------------------------- assembling --


def bps(level, price):
    """Distance from price in basis points.

    Basis points, never raw points: `docs/backtesting.md` -- ten points on $70
    oil and ten on a 20,000 index are not the same trade, and a gameplan that
    ranks by raw distance ranks by whichever quote is largest.
    """
    if price is None or price == 0:
        return None
    return round((level - price) / price * 10_000.0, 1)


def structure(bars, symbol, tz=DEFAULT_TZ, boundary=DEFAULT_DAY_BOUNDARY, now=None):
    """Everything the bars say about one instrument.

    `now` is injected rather than read from the clock, so staleness is testable
    without waiting for time to pass.
    """
    if len(bars) < 3:
        raise ValueError(
            f"{symbol}: {len(bars)} bar(s) is not enough to read structure from"
        )

    days = trading_days(bars.index, tz, boundary)
    today = days.iloc[-1]
    earlier = [d for d in dict.fromkeys(days) if d < today]
    prior = earlier[-1] if earlier else None

    last_at = bars.index[-1]
    last_price = float(bars["close"].iloc[-1])
    now = now or datetime.utcnow()
    age = (now - last_at.to_pydatetime()).total_seconds() / 60.0

    notes = []
    if prior is None:
        notes.append(
            "No prior trading day in this window: the range below is today's "
            "so far, not yesterday's settled range."
        )

    gaps = unmitigated(find_gaps(bars), bars)
    for gap in gaps:
        gap.order_block = order_block(gap, bars)
        gap.bps_from_price = bps(gap.mid, last_price)
    # Nearest first: a setup at the door is not the same as one price must
    # travel a long way to reach, and the ranking is what says which is which.
    gaps.sort(
        key=lambda g: abs(g.bps_from_price if g.bps_from_price is not None else 1e9)
    )

    # A bar stamped in the future is a clock skew or a mislabelled feed, and
    # either one makes every level below suspect. A small negative is normal --
    # a bar labelled by its open time exists before it closes -- so only a real
    # excursion is called out, and it is called out rather than clamped away.
    if age < -FUTURE_BAR_TOLERANCE.total_seconds() / 60.0:
        notes.append(
            f"The last bar is stamped {abs(age):.0f} min in the FUTURE "
            f"({last_at.isoformat()}Z). Either this machine's clock is wrong or the "
            "feed's stamps are not UTC. Do not trade these levels until that is settled."
        )

    stale = age > STALE_AFTER.total_seconds() / 60.0
    if stale:
        # Say the word. A note about a stale feed that never calls it stale is
        # the same class of miss as a status code printed without its meaning.
        notes.append(
            f"STALE: the last bar is {age / 60:.1f}h old ({last_at.isoformat()}Z). "
            "Treat the live price as unusable; the settled levels still hold."
        )

    return Structure(
        symbol=symbol,
        trading_day=str(today),
        last_price=last_price,
        last_bar_at=last_at.isoformat(),
        bar_age_minutes=round(age, 1),
        stale=stale,
        prior_day=(
            day_range(bars, days, prior)
            if prior is not None
            else day_range(bars, days, today)
        ),
        sessions=session_levels(bars, today, tz),
        gaps=[asdict(g) for g in gaps],
        notes=notes,
    )


def named_levels(struct):
    """The flat list of levels a gameplan quotes, nearest first."""
    price = struct.last_price
    out = []
    if struct.prior_day:
        for key in ("high", "low", "mid"):
            out.append(Level(f"prior day {key}", struct.prior_day[key]))
    for name, session in struct.sessions.items():
        if session is None:
            continue
        out.append(Level(f"{name} high", session["high"]))
        out.append(Level(f"{name} low", session["low"]))
    for level in out:
        level.bps_from_price = bps(level.price, price)
        if level.bps_from_price is None:
            level.side = None
        elif level.bps_from_price > 0:
            level.side = "above"
        elif level.bps_from_price < 0:
            level.side = "below"
        else:
            level.side = "at"
    out.sort(
        key=lambda lv: abs(lv.bps_from_price if lv.bps_from_price is not None else 1e9)
    )
    return out


# ---------------------------------------------------------------- rendering --


def render(bars, struct, path, title=None, marks=None, bars_shown=120):
    """A candlestick PNG with the levels drawn on it.

    This is the journal's chart image. It is deliberately *not* a screenshot of
    the owner's chart -- it carries none of their drawings or indicator
    settings -- and it buys something a screenshot cannot: it is regenerated
    from the same bars to the same picture every time, so a thesis can be
    re-read years later against the data that framed it rather than against a
    PNG nobody can reproduce.

    `marks` is an optional {label: price} of thesis levels (entry, stop,
    target) drawn over the structure.
    """
    import matplotlib

    matplotlib.use("Agg")  # no display; this is the whole point of the file
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter, date2num

    window = bars.tail(bars_shown)
    if window.empty:
        raise ValueError("nothing to draw: the window is empty")

    ink, muted, grid = "#1B1F23", "#5B6570", "#E3E7EB"
    up, down = "#2E7D6B", "#C1553B"  # teal / terracotta: readable in deuteranopia
    level_c, mark_c = "#3A6EA5", "#7A3E9D"
    # Gap tints echo the candle colours so direction reads without a legend
    # lookup, and both stay distinguishable from the blue level lines and the
    # purple thesis marks in deuteranopia.
    gap_bull, gap_bear = "#2E7D6B", "#C1553B"

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=110)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")

    times = date2num(window.index.to_pydatetime())
    width = (times[1] - times[0]) * 0.7 if len(times) > 1 else 0.01
    for t, (_, row) in zip(times, window.iterrows()):
        rising = row["close"] >= row["open"]
        colour = up if rising else down
        ax.vlines(t, row["low"], row["high"], color=colour, linewidth=0.8, zorder=2)
        ax.add_patch(
            plt.Rectangle(
                (t - width / 2, min(row["open"], row["close"])),
                width,
                max(abs(row["close"] - row["open"]), 1e-9),
                facecolor=colour,
                edgecolor=colour,
                linewidth=0.5,
                zorder=3,
            )
        )

    left, right = times[0], times[-1]
    # Gap bands carry their direction in their COLOUR, not in a label. Labelling
    # each one stacked four "bear FVG" strings on top of each other and on top
    # of the thesis marks the moment two gaps sat close together, which is the
    # normal case rather than a corner one. One legend line says what the tints
    # mean; the markdown table carries the numbers.
    drawn = set()
    for gap in struct.gaps[:4]:
        tint = gap_bull if gap["direction"] == "bullish" else gap_bear
        ax.axhspan(gap["bottom"], gap["top"], color=tint, alpha=0.16, zorder=1)
        drawn.add(gap["direction"])
    if drawn:
        parts = []
        if "bullish" in drawn:
            parts.append("bullish")
        if "bearish" in drawn:
            parts.append("bearish")
        ax.text(
            0.995,
            1.005,
            "shaded: unmitigated FVG (" + " / ".join(parts) + ")",
            transform=ax.transAxes,
            color=muted,
            fontsize=8,
            va="bottom",
            ha="right",
        )

    for level in named_levels(struct)[:8]:
        ax.axhline(
            level.price,
            color=level_c,
            linewidth=0.8,
            linestyle="--",
            alpha=0.7,
            zorder=4,
        )
        ax.text(
            left,
            level.price,
            f"{level.name} {level.price:,.2f} ",
            color=level_c,
            fontsize=7,
            va="bottom",
            ha="left",
        )

    for label, price in (marks or {}).items():
        ax.axhline(price, color=mark_c, linewidth=1.4, zorder=5)
        ax.text(
            right,
            price,
            f" {label} {price:,.2f}",
            color=mark_c,
            fontsize=8,
            fontweight="bold",
            va="bottom",
            ha="right",
        )

    ax.set_title(
        title or f"{struct.symbol} - trading day {struct.trading_day}",
        color=ink,
        fontsize=12,
        loc="left",
        pad=12,
    )
    stamp = f"last bar {struct.last_bar_at}Z ({struct.bar_age_minutes:.0f} min old)"
    if struct.stale:
        stamp += "  --  STALE"
    ax.text(
        0.0, 1.005, stamp, transform=ax.transAxes, color=muted, fontsize=8, va="bottom"
    )
    # Room at the left for the level labels, which are drawn at the axis edge
    # and otherwise sit on top of the first candles.
    span = right - left if right > left else 1.0
    ax.set_xlim(left - span * 0.13, right + span * 0.02)
    ax.xaxis.set_major_formatter(DateFormatter("%m-%d %H:%M"))
    ax.grid(True, color=grid, linewidth=0.6, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(grid)
    ax.tick_params(colors=muted, labelsize=8)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ------------------------------------------------------------------ writing --


def to_markdown(struct):
    """The block a gameplan or a journal note pastes in."""
    lines = [f"### {struct.symbol} - trading day {struct.trading_day}", ""]
    age = struct.bar_age_minutes
    flag = "  **STALE**" if struct.stale else ""
    lines.append(
        f"Last bar `{struct.last_bar_at}Z`, {age:.0f} min old. "
        f"Last price **{struct.last_price:,.2f}**.{flag}"
    )
    lines.append("")
    if struct.prior_day:
        p = struct.prior_day
        lines += [
            f"**Prior day** {p['low']:,.2f} - {p['high']:,.2f} "
            f"(mid {p['mid']:,.2f}, close {p['close']:,.2f})",
            "",
        ]
    lines += ["| level | price | from price |", "| --- | ---: | ---: |"]
    for level in named_levels(struct)[:10]:
        lines.append(
            f"| {level.name} | {level.price:,.2f} | {level.bps_from_price:+.0f} bp |"
        )
    lines.append("")
    if struct.gaps:
        lines += [
            "| unmitigated FVG | zone | filled | from price | order block |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for gap in struct.gaps[:6]:
            block = gap["order_block"]
            ob = f"{block['low']:,.2f}-{block['high']:,.2f}" if block else "none"
            lines.append(
                f"| {gap['direction']} | {gap['bottom']:,.2f}-{gap['top']:,.2f} | "
                f"{gap['filled_fraction'] * 100:.0f}% | {gap['bps_from_price']:+.0f} bp | {ob} |"
            )
    else:
        lines.append("No unmitigated fair value gaps in this window.")
    for note in struct.notes:
        lines += ["", f"> {note}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- cli --


def _bars_for(args):
    if args.csv:
        return read_bars(args.csv)
    return fetch_bars(args.symbol, args.interval, args.period)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("symbol")
        p.add_argument(
            "--csv", help="read bars from a fetch_bars.py CSV instead of the network"
        )
        p.add_argument("--interval", default="15m")
        p.add_argument("--period", default="1mo")
        p.add_argument("--tz", default=DEFAULT_TZ)
        return p

    levels = common(sub.add_parser("levels", help="structure as JSON and/or markdown"))
    levels.add_argument("--json", dest="json_out")
    levels.add_argument("--markdown", action="store_true")

    chart = common(
        sub.add_parser("chart", help="render a candlestick PNG with the levels on it")
    )
    chart.add_argument("--out", required=True)
    chart.add_argument("--title")
    chart.add_argument("--bars", type=int, default=120)
    chart.add_argument(
        "--mark",
        action="append",
        default=[],
        metavar="LABEL=PRICE",
        help="draw a thesis level, e.g. --mark entry=29200 --mark stop=29050",
    )

    args = parser.parse_args(argv)
    bars = _bars_for(args)
    struct = structure(bars, args.symbol, tz=args.tz)

    if args.cmd == "levels":
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(asdict(struct), indent=2), encoding="utf-8"
            )
            print(f"wrote {args.json_out}")
        if args.markdown or not args.json_out:
            print(to_markdown(struct))
        return 0

    marks = {}
    for raw in args.mark:
        if "=" not in raw:
            parser.error(f"--mark wants LABEL=PRICE, got {raw!r}")
        label, price = raw.split("=", 1)
        marks[label] = float(price)
    out = render(
        bars, struct, args.out, title=args.title, marks=marks, bars_shown=args.bars
    )
    print(f"wrote {out}")
    if struct.stale:
        print("NOTE: the last bar is stale; the chart says so on its face.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

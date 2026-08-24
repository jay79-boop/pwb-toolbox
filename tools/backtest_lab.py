"""Run one strategy across several instruments and feeds, and say whether the
result clears its own noise floor.

A single-instrument backtest is the easiest way to fool yourself in this
repository. The ICT AM continuation strategy this was written for measured
+39 points over eight years of S&P minute bars -- and two independent facts,
each found only because the run was widened, destroyed that number:

  * the feed's timestamps were New York local time *with* DST, not the fixed
    EST the loader assumed, so the session window sat an hour late for eight
    months of every year (see ``to_utc``);
  * the same strategy on the same index from a second vendor disagreed by
    283bp over the same period, against a measured "edge" of 50bp (see
    ``noise_floor``).

So the two things this module exists to make cheap are running the same
strategy over many instruments, and running it over the same instrument from
two vendors. The second is the one that decides whether the first meant
anything.

Everything here works on frames you supply. Nothing fetches: the loaders take
paths, and the tests drive the whole pipeline on synthetic bars.

    from tools.backtest_lab import to_utc, to_bars, backtest, noise_floor

    frame = to_bars(read_histdata("SPXUSD-2015.csv"), minutes=5)
    result = backtest(frame, StrategyClass, mintick=0.25)
    print(result.bps)          # normalised: points mean different things
                               # on $70 oil and on a 20,000 index
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import backtrader as bt
import pandas as pd

#: Vendors stamp intraday bars in whatever clock they please, and none of them
#: say so in the file. These are the ones verified against each other here;
#: ``verify_timezone`` is how a new one gets added rather than assumed.
KNOWN_FEED_TIMEZONES = {
    # histdata's ASCII M1 exports are New York local time, DST included --
    # NOT the fixed EST their documentation is often read as promising.
    "histdata": "America/New_York",
    # oanda exports are already UTC.
    "oanda": "UTC",
}


def to_utc(index, tz):
    """Naive local stamps -> naive UTC, honouring daylight saving.

    A flat offset is the tempting shortcut and it is wrong for any feed
    stamped in a zone that observes DST: right in January, an hour out in
    July. Since the strategy converts back to an exchange timezone to test
    its session, that hour silently moves the whole trading window.

    The hour that repeats when the clocks go back is genuinely ambiguous and
    the minute that never happens when they go forward does not exist, so
    both are dropped rather than guessed at -- two bars a year, against a
    session filter that would otherwise be quietly wrong.
    """
    index = pd.DatetimeIndex(index)
    if tz in (None, "UTC"):
        return index
    localized = index.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    return localized.tz_convert("UTC").tz_localize(None)


def verify_timezone(a, b, candidates=range(-6 * 60, 6 * 60 + 1, 30)):
    """The offset in minutes that best aligns feed ``a`` to feed ``b``.

    Compares *returns*, not levels: two vendors quoting the same index differ
    by a basis that swamps a level comparison, but their minute-to-minute
    returns line up sharply and only at the right offset. Run it on a winter
    month and a summer month separately -- an offset that changes between
    them is DST, and the feed is stamped in local time.

    Both arguments are close-price series indexed by time.
    """
    ra, rb = a.pct_change(), b.pct_change()
    best = (None, float("-inf"))
    for minutes in candidates:
        shifted = ra.copy()
        shifted.index = shifted.index + pd.Timedelta(minutes=minutes)
        joined = pd.concat(
            [shifted.rename("a"), rb.rename("b")], axis=1, sort=True
        ).dropna()
        joined = joined[(joined != 0).all(axis=1)]
        if len(joined) < 100:
            continue
        correlation = joined["a"].corr(joined["b"])
        if correlation is not None and correlation > best[1]:
            best = (minutes, correlation)
    return best


def to_bars(frame, minutes=5):
    """Resample OHLCV to ``minutes``, labelling each bar by its own opening.

    ``label="left"`` matters: a bar stamped with the time it *closed* is a bar
    the session filter admits one interval late, which is the same class of
    error as getting the timezone wrong.
    """
    out = frame.resample(f"{minutes}min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return out.dropna(subset=["open"])


def read_histdata(path, tz="America/New_York"):
    """One of histdata's semicolon-separated ASCII M1 files, stamped in UTC."""
    frame = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["stamp", "open", "high", "low", "close", "volume"],
    )
    frame.index = to_utc(pd.to_datetime(frame["stamp"], format="%Y%m%d %H%M%S"), tz)
    frame = frame[frame.index.notna()].drop(columns=["stamp"])
    return frame.sort_index()


def read_generic(path, tz="UTC", time_column="time"):
    """A CSV with a timestamp column and OHLCV columns, stamped in UTC."""
    frame = pd.read_csv(path)
    frame.index = to_utc(pd.to_datetime(frame[time_column]), tz)
    frame = frame[frame.index.notna()].drop(columns=[time_column])
    return frame.sort_index()


@dataclass
class Result:
    """One backtest, with the normalisation needed to compare it to another."""

    trades: int
    wins: int
    net: float
    #: Mean close over the run, which is what ``bps`` is a fraction of.
    price: float
    #: The strategy instance the run produced, for callers that need more than
    #: the counts -- ``tools/vwap_lab.py`` reads its trade log for the night
    #: lab export. Never compared or serialised.
    strategy: object = None

    @property
    def bps(self):
        """Net as basis points of price.

        The only cross-instrument unit that means anything here. Ten points on
        WTI at 70 and ten points on JP225 at 20,000 are not the same trade,
        and summing raw points across instruments produces a number whose
        biggest component is whichever instrument has the largest quote.
        """
        return 1e4 * self.net / self.price if self.price else 0.0

    @property
    def win_rate(self):
        return 100.0 * self.wins / self.trades if self.trades else 0.0


class _Ledger(bt.Analyzer):
    """Closed-trade P&L, which Backtrader reports and then forgets."""

    def start(self):
        self.pnl = []

    def notify_trade(self, trade):
        if trade.isclosed:
            self.pnl.append(trade.pnlcomm)

    def get_analysis(self):
        return self.pnl


def backtest(
    frame,
    strategy,
    mintick=None,
    minutes=5,
    cash=100_000.0,
    slip_ticks=1.0,
    commission=0.02,
    extra_feeds=(),
    **params,
):
    """Run ``strategy`` over ``frame`` and return a :class:`Result`.

    ``slip_ticks`` and ``commission`` default to roughly what a liquid futures
    contract costs. They are not decoration: the strategy this module was
    built for is gross-positive and net-negative, so a run without costs
    measures nothing anyone can trade.

    ``extra_feeds`` are additional frames for a strategy declaring
    ``feed_spec`` -- a converted script reading ``request.security`` on a
    second instrument needs them in the order the spec lists.

    ``mintick`` is passed only to a strategy that declares it: the converter
    emits that param when, and only when, the script read
    ``syminfo.mintick``, so handing it to one that did not is an error rather
    than a no-op.
    """
    if mintick is not None and "mintick" in strategy.params._getkeys():
        params["mintick"] = mintick
    cerebro = bt.Cerebro()
    cerebro.adddata(
        bt.feeds.PandasData(
            dataname=frame, timeframe=bt.TimeFrame.Minutes, compression=minutes
        )
    )
    for feed, timeframe, compression in extra_feeds:
        cerebro.adddata(
            bt.feeds.PandasData(
                dataname=feed, timeframe=timeframe, compression=compression
            )
        )
    cerebro.addstrategy(strategy, **params)
    cerebro.broker.setcash(cash)
    if commission:
        cerebro.broker.setcommission(
            commission=commission, commtype=bt.CommInfoBase.COMM_FIXED
        )
    if slip_ticks:
        cerebro.broker.set_slippage_fixed(slip_ticks * mintick)
    cerebro.addanalyzer(_Ledger, _name="ledger")
    strat = cerebro.run()[0]
    pnl = strat.analyzers.ledger.get_analysis()
    return Result(
        trades=len(pnl),
        wins=sum(1 for x in pnl if x > 0),
        net=sum(pnl),
        price=float(frame["close"].mean()),
        strategy=strat,
    )


@dataclass
class NoiseFloor:
    """How far two vendors disagree about the same strategy and instrument."""

    correlation: float
    mean_gap: float
    gap_stdev: float
    edge: float

    @property
    def clears(self):
        """Whether the edge is larger than the disagreement measuring it.

        A strategy whose result is smaller than the spread between two feeds
        of the same instrument has not been shown to have an edge; it has been
        shown to be sensitive to data sourcing. This is deliberately a
        one-sided test -- clearing the floor is necessary, nowhere near
        sufficient.
        """
        return abs(self.edge) > abs(self.mean_gap) + self.gap_stdev

    def __str__(self):
        verdict = "clears" if self.clears else "INSIDE"
        return (
            f"edge {self.edge:+.0f}bp vs vendor gap "
            f"{self.mean_gap:+.0f}+/-{self.gap_stdev:.0f}bp "
            f"(corr {self.correlation:.2f}) -- {verdict} the noise floor"
        )


def noise_floor(one, other):
    """Compare per-period results for one strategy across two vendors.

    Both arguments map a period (a year, say) to a :class:`Result`. A high
    correlation with a large mean gap is the dangerous case and the one seen
    in practice: the feeds agree about the shape of every year and still
    disagree about the sign of the total.
    """
    keys = sorted(set(one) & set(other))
    if len(keys) < 2:
        raise ValueError("need at least two periods in both to compare")
    a = [one[k].bps for k in keys]
    b = [other[k].bps for k in keys]
    gaps = [x - y for x, y in zip(a, b)]
    correlation = (
        statistics.correlation(a, b)
        if len(keys) > 2 and statistics.pstdev(a) and statistics.pstdev(b)
        else float("nan")
    )
    return NoiseFloor(
        correlation=correlation,
        mean_gap=statistics.fmean(gaps),
        gap_stdev=statistics.stdev(gaps) if len(gaps) > 1 else 0.0,
        edge=sum(a),
    )


def summarise(results):
    """Fold ``{period: Result}`` into one row's worth of numbers."""
    values = list(results.values())
    trades = sum(r.trades for r in values)
    return {
        "trades": trades,
        "wins": sum(r.wins for r in values),
        "bps": sum(r.bps for r in values),
        "net": sum(r.net for r in values),
        "win_rate": 100.0 * sum(r.wins for r in values) / trades if trades else 0.0,
    }

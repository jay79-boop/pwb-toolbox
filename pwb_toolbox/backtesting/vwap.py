"""Session-anchored VWAP with deviation bands, and the strategies traded off it.

VWAP earns a place other indicator lines do not: it began as the execution
benchmark institutions are graded against (Berkowitz, Logue & Noser 1988), so
real order flow congregates around it. The peer-reviewed literature is almost
entirely about executing *at* VWAP; the evidence for trading *off* it is
practitioner-grade, and it points one way: mean reversion from the deviation
bands has measurable support, while the popular close-over-VWAP crossover
produced zero significant configurations in the largest published parameter
sweep. ``VwapStrategy`` therefore implements the fade and the pullback as the
candidates, and the crossover as a control expected to fail -- a harness that
cannot flunk the known-dead setup cannot be trusted about the live ones.

Everything here is built to run under ``tools.backtest_lab``: bars arrive as
naive-UTC datetimes, the strategy converts to an exchange timezone to test its
session, and costs are the lab's problem. ``tools/vwap_lab.py`` is the driver.

One data honesty note: VWAP needs volume, and several of the feeds in this
repository (histdata index CFDs among them) carry zero volume on every bar.
The indicator degrades to a time-weighted average price rather than dividing
by zero, and the lab reports the zero-volume share so a "VWAP" result on a
volumeless feed is read as the TWAP result it actually is.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import backtrader as bt

LONG = 1
SHORT = -1

SETUPS = ("fade", "pullback", "cross")


def parse_hm(text: str) -> int:
    """``"09:30"`` -> minutes after midnight."""
    hh, mm = text.replace(" ", "").split(":")
    return int(hh) * 60 + int(mm)


def session_key(dt_utc: datetime, tz: str, anchor_minutes: int):
    """Which session a naive-UTC bar stamp belongs to.

    Shifting by the anchor time before taking the date makes a session that
    crosses midnight hang together: with a 09:30 anchor, 09:30 today through
    09:25 tomorrow all share one key, so the VWAP resets at the session open
    rather than at a midnight nobody trades.
    """
    local = dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz))
    return (local - timedelta(minutes=anchor_minutes)).date()


def local_minutes(dt_utc: datetime, tz: str) -> int:
    local = dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz))
    return local.hour * 60 + local.minute


class SessionVwap(bt.Indicator):
    """Running VWAP of the typical price since the session anchor, with bands.

    The bands are volume-weighted standard deviations of the typical price
    around the VWAP -- the construction TradingView's VWAP bands use -- so a
    ``band_k`` of 2.0 is the +/-2 sigma envelope the published fade numbers
    are quoted at.

    ``anchor`` switches the indicator to anchored mode: accumulation starts
    at that naive-UTC timestamp and never resets, which is the swing-trading
    "anchored VWAP" (to an earnings date, a swing low). Bars before the
    anchor emit NaN so a strategy cannot trade a VWAP that does not exist yet.

    Bars with zero volume get zero weight; a session with *no* volume at all
    falls back to the unweighted mean and stdev (TWAP) instead of dividing by
    zero. The fallback is deliberate and visible -- see the module docstring.
    """

    lines = ("vwap", "upper", "lower")
    params = (
        ("band_k", 2.0),
        ("tz", "America/New_York"),
        ("anchor_time", "00:00"),
        ("anchor", None),
    )
    plotinfo = dict(subplot=False)

    def __init__(self):
        self._anchor_min = parse_hm(self.p.anchor_time)
        self._anchor_dt = None
        if self.p.anchor is not None:
            raw = self.p.anchor
            self._anchor_dt = (
                datetime.fromisoformat(raw) if isinstance(raw, str) else raw
            )
        self._session = None
        self._zero()

    def _zero(self):
        self._n = 0
        self._sum_v = 0.0
        self._sum_pv = 0.0
        self._sum_pv2 = 0.0
        self._sum_p = 0.0
        self._sum_p2 = 0.0

    def next(self):
        dt = self.data.datetime.datetime(0)
        if self._anchor_dt is not None:
            if dt < self._anchor_dt:
                self.lines.vwap[0] = float("nan")
                self.lines.upper[0] = float("nan")
                self.lines.lower[0] = float("nan")
                return
        else:
            key = session_key(dt, self.p.tz, self._anchor_min)
            if key != self._session:
                self._session = key
                self._zero()

        typical = (self.data.high[0] + self.data.low[0] + self.data.close[0]) / 3.0
        vol = max(float(self.data.volume[0]), 0.0)
        self._n += 1
        self._sum_v += vol
        self._sum_pv += typical * vol
        self._sum_pv2 += typical * typical * vol
        self._sum_p += typical
        self._sum_p2 += typical * typical

        if self._sum_v > 0:
            mean = self._sum_pv / self._sum_v
            var = self._sum_pv2 / self._sum_v - mean * mean
        else:
            mean = self._sum_p / self._n
            var = self._sum_p2 / self._n - mean * mean
        sigma = math.sqrt(max(var, 0.0))
        self.lines.vwap[0] = mean
        self.lines.upper[0] = mean + self.p.band_k * sigma
        self.lines.lower[0] = mean - self.p.band_k * sigma


class VwapStrategy(bt.Strategy):
    """One of three VWAP setups, each gated by the same optional confirms.

    Setups (``setup=``):

    * ``"fade"`` -- a close outside the +/-``band_k`` sigma band enters toward
      VWAP; target is a close back across VWAP, stop is ``stop_k`` sigma
      beyond the signal close. The variant with empirical support.
    * ``"pullback"`` -- with the previous close above VWAP, a bar that touches
      VWAP and closes back above it enters long (mirror short); target is the
      opposite band, stop ``stop_k`` sigma past VWAP. The continuation flavor.
    * ``"cross"`` -- close crosses VWAP, stop-and-reverse. The retail
      favorite, kept as a control: the published sweeps found it worthless,
      so a harness result that flatters it is a harness bug.

    Confirms, each off by default so every gate's cost is measurable:

    * ``rvol_min`` -- bar volume over its ``rvol_len`` SMA must reach this.
    * ``day_type_bp`` -- the first 30 session minutes classify the day
      (market intraday momentum, Gao/Han/Li/Zhou JFE 2018): past the
      threshold it is a trend day, which blocks counter-trend fades and
      restricts pullbacks to the trend direction.
    * ``ma_len`` -- entries must agree with the side of this close SMA.
    * ``rsi_len`` -- fades additionally require a stretched RSI. The wisdom
      doc calls oscillators folklore; this exists to measure that claim, not
      to dispute it.

    Exits are decided on closes and filled at the next open -- the honest
    reading for a strategy that will also live as a Pine script, where a
    market order fills exactly there. All entries wait out ``warmup_min``
    session minutes so the freshly reset bands have something in them.

    Sessions: ``rth_only=True`` trades ``session_start``..``session_end`` in
    ``tz`` and flattens at the end. For 24/7 markets pass ``rth_only=False``
    and ``tz="UTC"``: the VWAP then anchors at UTC midnight, which is the
    convention crypto session VWAP uses, arbitrary as any other -- a caveat,
    not a feature.
    """

    params = dict(
        setup="fade",
        band_k=2.0,
        stop_k=1.0,
        tz="America/New_York",
        session_start="09:30",
        session_end="15:55",
        rth_only=True,
        anchor=None,
        warmup_min=30,
        rvol_len=50,
        rvol_min=0.0,
        day_type_bp=0.0,
        ma_len=0,
        rsi_len=0,
    )

    def __init__(self):
        if self.p.setup not in SETUPS:
            raise ValueError(f"setup must be one of {SETUPS}, not {self.p.setup!r}")
        self._start_min = parse_hm(self.p.session_start)
        self._end_min = parse_hm(self.p.session_end)
        anchor_time = self.p.session_start if self.p.rth_only else "00:00"
        self.vwap = SessionVwap(
            self.data,
            band_k=self.p.band_k,
            tz=self.p.tz,
            anchor_time=anchor_time,
            anchor=self.p.anchor,
        )
        self.rvol_sma = (
            bt.ind.SMA(self.data.volume, period=self.p.rvol_len)
            if self.p.rvol_min > 0
            else None
        )
        self.ma = (
            bt.ind.SMA(self.data.close, period=self.p.ma_len) if self.p.ma_len else None
        )
        self.rsi = (
            bt.ind.RSI(self.data.close, period=self.p.rsi_len, safediv=True)
            if self.p.rsi_len
            else None
        )
        self._live_orders = []
        self._order_intent = {}
        self._session = None
        self._session_open = None
        self._session_start_dt = None
        self._day_type = None
        self._stop_level = None
        self._direction = 0
        self._exit_reason = None
        self._open_rec = None
        self.trade_log = []

    # --- bookkeeping ---------------------------------------------------------

    def _new_session(self):
        self._session_open = None
        self._session_start_dt = None
        self._day_type = None

    def notify_order(self, order):
        """Trade recording keyed to each order's declared intent.

        On a same-bar reverse both fills are executed before either
        notification arrives, so ``self.position`` is useless for telling an
        exit from an entry -- it already shows the new position during the old
        order's notification. ``_enter``/``_close`` therefore tag every order
        they issue, and this reads the tag, never the position.
        """
        if order.status in (order.Submitted, order.Accepted):
            return
        if order in self._live_orders:
            self._live_orders.remove(order)
        intent = self._order_intent.pop(order.ref, None)
        if order.status != order.Completed:
            return
        filled_dt = bt.num2date(order.executed.dt)
        if intent == "entry" and self._open_rec is None:
            self._open_rec = {
                "direction": "long" if order.isbuy() else "short",
                "entry": order.executed.price,
                "stop": self._stop_level,
                "opened": filled_dt.isoformat(),
            }
        elif intent == "exit" and self._open_rec is not None:
            rec = self._open_rec
            rec["exit"] = order.executed.price
            rec["closed"] = filled_dt.isoformat()
            rec["reason"] = self._exit_reason or "exit"
            self.trade_log.append(rec)
            self._open_rec = None

    def _close(self, reason):
        self._exit_reason = reason
        order = self.close()
        self._order_intent[order.ref] = "exit"
        self._live_orders.append(order)

    def _enter(self, direction, stop_level):
        self._direction = direction
        self._stop_level = stop_level
        order = self.buy() if direction == LONG else self.sell()
        self._order_intent[order.ref] = "entry"
        self._live_orders.append(order)

    # --- gates ---------------------------------------------------------------

    def _rvol_ok(self):
        if self.rvol_sma is None:
            return True
        avg = self.rvol_sma[0]
        return avg > 0 and self.data.volume[0] / avg >= self.p.rvol_min

    def _ma_ok(self, direction):
        if self.ma is None:
            return True
        return (
            self.data.close[0] > self.ma[0]
            if direction == LONG
            else self.data.close[0] < self.ma[0]
        )

    def _rsi_ok(self, direction):
        if self.rsi is None:
            return True
        return self.rsi[0] < 30 if direction == LONG else self.rsi[0] > 70

    def _day_type_allows(self, kind, direction):
        """Trend days block counter-trend fades and off-trend pullbacks."""
        if self._day_type in (None, "range"):
            return True
        trend = LONG if self._day_type == "trend_up" else SHORT
        if kind == "fade":
            return direction == trend
        return direction == trend

    # --- the walk ------------------------------------------------------------

    def next(self):
        dt = self.data.datetime.datetime(0)
        key = session_key(dt, self.p.tz, self._start_min if self.p.rth_only else 0)
        if key != self._session:
            self._session = key
            self._new_session()

        minutes = local_minutes(dt, self.p.tz)
        in_session = (
            self._start_min <= minutes < self._end_min if self.p.rth_only else True
        )

        if self.p.rth_only and not in_session:
            if self.position and not self._live_orders:
                self._close("flatten")
            return

        if self._session_open is None:
            self._session_open = float(self.data.open[0])
            self._session_start_dt = dt
        elapsed = (dt - self._session_start_dt).total_seconds() / 60.0

        if self.p.day_type_bp > 0 and self._day_type is None and elapsed >= 30:
            ret_bp = 1e4 * (float(self.data.close[-1]) / self._session_open - 1.0)
            if ret_bp >= self.p.day_type_bp:
                self._day_type = "trend_up"
            elif ret_bp <= -self.p.day_type_bp:
                self._day_type = "trend_down"
            else:
                self._day_type = "range"

        if self._live_orders:
            return

        vwap = self.vwap.vwap[0]
        upper = self.vwap.upper[0]
        lower = self.vwap.lower[0]
        if math.isnan(vwap):
            return
        sigma = (upper - vwap) / self.p.band_k if self.p.band_k else 0.0
        close = float(self.data.close[0])

        if self.position:
            self._manage(close, vwap, upper, lower)
            return

        if elapsed < self.p.warmup_min or sigma <= 0:
            return
        if self.p.day_type_bp > 0 and self._day_type is None:
            return  # the classifier has not spoken yet; do not front-run it

        if self.p.setup == "fade":
            self._try_fade(close, vwap, upper, lower, sigma)
        elif self.p.setup == "pullback":
            self._try_pullback(close, vwap, upper, lower, sigma)
        else:
            self._try_cross(close, vwap)

    def _manage(self, close, vwap, upper, lower):
        if self.p.setup == "cross":
            prev_vwap = self.vwap.vwap[-1]
            prev_close = float(self.data.close[-1])
            if math.isnan(prev_vwap):
                return
            if self._direction == LONG and close < vwap and prev_close >= prev_vwap:
                self._close("reverse")
                self._enter(SHORT, None)
            elif self._direction == SHORT and close > vwap and prev_close <= prev_vwap:
                self._close("reverse")
                self._enter(LONG, None)
            return

        if self.p.setup == "fade":
            target_hit = close >= vwap if self._direction == LONG else close <= vwap
        else:  # pullback rides to the far band
            target_hit = close >= upper if self._direction == LONG else close <= lower
        stopped = self._stop_level is not None and (
            close <= self._stop_level
            if self._direction == LONG
            else close >= self._stop_level
        )
        if stopped:
            self._close("stop")
        elif target_hit:
            self._close("target")

    def _try_fade(self, close, vwap, upper, lower, sigma):
        if close > upper:
            direction = SHORT
        elif close < lower:
            direction = LONG
        else:
            return
        if not self._day_type_allows("fade", direction):
            return
        if not (self._rvol_ok() and self._ma_ok(direction) and self._rsi_ok(direction)):
            return
        stop = (
            close - self.p.stop_k * sigma
            if direction == LONG
            else close + self.p.stop_k * sigma
        )
        self._enter(direction, stop)

    def _try_pullback(self, close, vwap, upper, lower, sigma):
        if len(self.data) < 2:
            return
        prev_close = float(self.data.close[-1])
        prev_vwap = self.vwap.vwap[-1]
        if math.isnan(prev_vwap):
            return
        low = float(self.data.low[0])
        high = float(self.data.high[0])
        if prev_close > prev_vwap and low <= vwap and close > vwap:
            direction = LONG
        elif prev_close < prev_vwap and high >= vwap and close < vwap:
            direction = SHORT
        else:
            return
        if not self._day_type_allows("pullback", direction):
            return
        if not (self._rvol_ok() and self._ma_ok(direction)):
            return
        stop = (
            vwap - self.p.stop_k * sigma
            if direction == LONG
            else vwap + self.p.stop_k * sigma
        )
        self._enter(direction, stop)

    def _try_cross(self, close, vwap):
        if len(self.data) < 2:
            return
        prev_vwap = self.vwap.vwap[-1]
        prev_close = float(self.data.close[-1])
        if math.isnan(prev_vwap):
            return
        if close > vwap and prev_close <= prev_vwap:
            self._enter(LONG, None)
        elif close < vwap and prev_close >= prev_vwap:
            self._enter(SHORT, None)

    def stop(self):
        # A record still open when the data ends is closed at the last price,
        # so an exported trade list never carries a half-written row.
        if self._open_rec is not None:
            rec = self._open_rec
            rec["exit"] = float(self.data.close[0])
            rec["closed"] = self.data.datetime.datetime(0).isoformat()
            rec["reason"] = "data end"
            self.trade_log.append(rec)
            self._open_rec = None

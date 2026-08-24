"""Generate a Backtrader strategy from a parsed PineScript program.

The structural problem
----------------------

Pine and Backtrader disagree about when a computation happens. In Pine every
expression is a series evaluated on every bar, so ``ta.sma(close, 20)`` can be
written anywhere. In Backtrader an indicator is a line object built once in
``__init__`` and then *indexed* per bar in ``next``.

So the translation is a hoist: every recognised ``ta.*`` call becomes a named
attribute constructed in ``__init__``, and every reference to it in a condition
becomes ``self.<name>[0]``. That is the whole trick, and it is why an
expression is lowered differently depending on which of the two contexts it
lands in -- see :meth:`_Generator._line_expr` and :meth:`_Generator._value_expr`.

What is deliberately not translated
-----------------------------------

Anything whose Backtrader equivalent would be a guess is reported rather than
emitted: ``varip``'s intrabar updates, arrays and matrices, loops, and
``strategy.exit`` offsets measured in ticks. Presentational calls (``plot``,
``bgcolor``, ``label.new``) are dropped, but reported separately as *ignored*
-- they change nothing about how the strategy trades.

``var`` is the exception that is *not* a guess: Pine initialises it once and
keeps it across bars, which is what an instance attribute already does, so it
becomes one. Only a literal initial value works -- ``var x = close`` means the
first bar's close and ``__init__`` runs before there is a first bar.

``request.security`` is the other. Pine reaches another timeframe -- or another
instrument -- inline;
Backtrader reaches it through a resampled feed added to the cerebro before the
strategy exists. So the call becomes a read from ``self.datas[n]`` and the
class records, in ``feed_spec``, the feeds the caller has to supply. The
timeframe stops being tunable at that point -- resampling happens before
``addstrategy`` -- which is why a timeframe taken from an input is resolved to
that input's default and reported.

A user-defined function is the third, and it is inlined at each call site
rather than emitted as a Python ``def``. That is not a shortcut: Pine gives
every call site its own independent series state, so two calls to the same
function do not share history the way two calls to a Python function share a
closure. Substituting the arguments into the body is what that rule *means*
here -- and it also dissolves the length problem, since ``ta.sma(src, len)``
gets a concrete length the moment the call site's argument replaces ``len``.
See :meth:`_Generator._inline_call`.

A body that keeps ``var`` state cannot fold into one expression -- the state
has to be *updated*, in order, once per bar -- so it becomes lines emitted in
front of whichever statement asked for the value, plus a name. Each call site
gets its own attributes, which is that same per-call-site rule again. See
:meth:`_Generator._inline_stateful`.

``strategy.exit`` with a ``stop`` or ``limit`` is a fourth. Both are absolute
prices, so they become a Backtrader stop and limit order linked one-cancels-
other. The subtlety is that Pine's exit is a *standing instruction* rather than
a submission: it is re-evaluated on every bar and moves its orders when the
levels change. Emitting a fresh order per call would stack them and fill
several times over, so the generated class maintains them instead -- see
``_EXIT_HELPER``.

``barstate.*`` is the fifth, and the only one that is a constant. It asks where
the script sits in the chart's history, and a bar-close backtest already knows:
``next()`` runs once per completed historical bar. So ``barstate.isconfirmed``
is ``True`` -- which is what TradingView's own backtest answers on a historical
bar, not a simplification of it -- and the repaint guards built on it correctly
become no-ops. ``islast`` and ``isfirst`` are positions in the feed rather than
constants, so they stay live.

``strategy.entry`` is the sixth, and the one that is easiest to get wrong by
doing the obvious thing. It is not ``self.buy()``: Pine's default
``pyramiding=0`` allows one entry per direction, so calling it again while
already long does nothing, where ``buy()`` adds every time. An entry against
the position is a reversal. See ``_ENTRY_HELPER``. With a ``limit`` or
``stop`` price it is a pending order rather than a market one: it is collected
under its id and submitted at the end of the bar as a Backtrader bracket, so a
``strategy.exit`` naming it through ``from_entry`` rides along as the
bracket's exit legs, re-issuing the id moves the order rather than stacking
another, and ``strategy.cancel`` withdraws it while it is still unfilled. See
``_PENDING_HELPER``.

Pine's trade counters are the seventh. Backtrader keeps no ledger of closed
trades, so one is built from ``notify_trade`` -- but only in strategies that
ask for it. See ``_TRADES_HELPER``.

Composed expressions as indicator sources are the eighth. Backtrader overloads
arithmetic on lines, so ``ta.ema(hlc3 - close, n)`` is a line fed to an
indicator -- and a value the script assigned as an ordinary number is lowered a
second time, as a line, when something asks for it as a source. See
:meth:`_Generator._line_expr` and :meth:`_Generator._promote`.

A conditional is the ninth. ``bt.If`` computes both of its branches every bar,
which defeats the guard a conditional usually is -- ``d != 0 ? x / d : 0``
would divide anyway, and Backtrader's line division raises where Pine answers
``na``. So a ternary becomes a ``PineExpr``: the expression as a Python
function of the current bar's values, where ``if``/``else`` is lazy again.
See ``_EXPR_INDICATOR`` and :meth:`_Generator._expr_line`.

``ta.hma``, ``ta.vwma`` and ``ta.alma`` are the fourteenth. Hull looks like it is
already in Backtrader and is not: the built-in truncates the final
``sqrt(period)`` where Pine rounds it, so the two disagree for 24 of the first
59 lengths. It is composed here from the weighted averages Pine says it is
made of. See :meth:`_Generator._hoist_moving_average`.

``ta.pivothigh`` and ``ta.pivotlow`` are the tenth. Backtrader has no pivot
indicator, so one is emitted alongside the strategy. The bar under test is
``right`` bars back, which is what keeps it causal: a pivot is reported only
once that many further bars have closed and confirmed it. See
``_PIVOT_INDICATOR``.

Naming an inlined intermediate is the thirteenth, and it is one absence
wearing three faces. Substitution has nowhere to put a body local, so copying
it into every read grows the tree multiplicatively, ``adx[1]`` has no previous
bar to read off an expression tree, and ``ta.ema(adx, n)`` cannot take one as
a source. A local past :data:`_MATERIALISE_NODE_LIMIT` is given a name in
``next()`` instead, which is one node, lands in ``_computed``, and lets
:meth:`_Generator._promote` build the same expression as a line the moment
anything asks for its history or hands it to an indicator. See
:meth:`_Generator._materialise`.

That splits the prelude in two. A ``var`` update has to run on every bar
wherever the call sits, so it is still refused under an ``if``. A named
intermediate is a value rather than a state machine, and computing it only on
the bars that read it is the same strategy.

An ``if`` that writes one name in every branch is the twelfth. It is how a
Pine function picks a value -- assign a default, overwrite it in whichever
branch applies, hand the name back -- and it is an assignment rather than an
expression, so it folds into the substitution wherever it sits rather than
only at the end of a body. A missing ``else`` binds the name to itself, which
substitution turns back into whatever it last held: Pine leaves a variable
alone when no branch runs. See :func:`_if_as_assignment`.

The clock is the eleventh. ``timeframe.period`` and ``time()`` ask what the
chart is and when this bar opened, and the feed already knows both, so neither
needs the caller to declare anything. ``time(res)`` floors the bar's stamp to
``res``, which is what makes Pine's ``ta.change(time("240")) != 0`` fire on the
first chart bar of each new four-hour bar. A session argument filters by the
bar's own clock: Pine consults the exchange calendar and timezone, the feed
carries neither, and the feed's own timestamps are the one clock a backtest
has -- the generated ``_pine_in_session`` states the assumption. A weekly or
monthly resolution is still refused: it does not floor by modulo -- the epoch
falls on a Thursday. See ``_TIME_HELPER`` and :meth:`_Generator._value_time`.

``syminfo.mintick`` asks how coarsely the instrument's price moves, and a
Backtrader feed does not know. It becomes a param -- ``mintick``, defaulting
to the 0.01 of a US equity -- so the tick size stays where it lives, with the
instrument, and the report says to set it per instrument.

A conversion with a non-empty ``unsupported`` list is not a working port. It is
a starting point plus a list of what you still have to write yourself.
"""

import collections
import datetime
import keyword
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .nodes import (
    Assign,
    Binary,
    Bool,
    Call,
    ExprStmt,
    FuncDef,
    If,
    Index,
    ListLit,
    Na,
    Name,
    Num,
    Str,
    Ternary,
    TupleAssign,
    Unary,
    Unsupported,
)
from .parser import PineSyntaxError, parse


@dataclass(frozen=True)
class IndicatorSpec:
    """How a Pine indicator maps onto a Backtrader one."""

    bt_name: str
    #: Pine's first positional argument is the source series.
    takes_source: bool = True
    #: Source to use when Pine's short form omits it (``ta.highest(20)``).
    default_source: str = "close"
    #: Pine passes a length that becomes Backtrader's ``period``.
    takes_period: bool = True


INDICATORS: dict[str, IndicatorSpec] = {
    "ta.sma": IndicatorSpec("SMA"),
    "ta.ema": IndicatorSpec("EMA"),
    "ta.wma": IndicatorSpec("WMA"),
    "ta.rma": IndicatorSpec("SmoothedMovingAverage"),
    "ta.rsi": IndicatorSpec("RSI"),
    "ta.stdev": IndicatorSpec("StandardDeviation"),
    "ta.highest": IndicatorSpec("Highest", default_source="high"),
    "ta.lowest": IndicatorSpec("Lowest", default_source="low"),
    "ta.atr": IndicatorSpec("ATR", takes_source=False),
    "ta.tr": IndicatorSpec("TrueRange", takes_source=False, takes_period=False),
}

#: Pine cross helpers, mapped to a CrossOver line plus the comparison that
#: recovers the direction Pine means.
PIVOTS: tuple[str, ...] = ("ta.pivothigh", "ta.pivotlow")

#: Pine's account-level risk rules. Both halt trading on a breach;
#: max_intraday_loss lets it resume the next day, max_drawdown does not.
RISK_RULES = ("strategy.risk.max_drawdown", "strategy.risk.max_intraday_loss")

#: Moving averages Backtrader either lacks or spells differently enough to
#: matter. Each is built from what Pine says it is made of.
COMPOSED_AVERAGES: tuple[str, ...] = ("ta.hma", "ta.vwma", "ta.alma")

#: Operators Backtrader overloads on line objects to give another line.
#: Comparisons are deliberately absent: a truth value is not a source, and
#: `bt.If` evaluates both branches, which would defeat the `d != 0 ? x / d : 0`
#: guard scripts write precisely to avoid dividing by zero.
_LINE_OPS: tuple[str, ...] = ("+", "-", "*", "/", "%")

#: Math that composes on lines rather than on numbers.
_LINE_MATH: dict[str, str] = {
    "math.abs": "abs",
    "math.max": "bt.Max",
    "math.min": "bt.Min",
}

CROSSES: dict[str, str] = {
    "ta.crossover": "> 0",
    "ta.crossunder": "< 0",
    "ta.cross": "!= 0",
}

#: Pine price series, as line names. The feed they are read from is decided at
#: lowering time -- inside a ``request.security`` they belong to a resampled
#: feed rather than the chart's own.
PRICE_SERIES: dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}

#: Pine's derived price series, which Backtrader has no line for. ``{d}`` is
#: the feed. Composed of whole lines, so this is itself a line and can be fed
#: to an indicator.
DERIVED_LINES: dict[str, str] = {
    "hl2": "({d}.high + {d}.low) / 2",
    "hlc3": "({d}.high + {d}.low + {d}.close) / 3",
    "ohlc4": "({d}.open + {d}.high + {d}.low + {d}.close) / 4",
}

#: The same, as per-bar reads for ``next()``, with ``{i}`` the bar offset.
#: Derived rather than written twice: two spellings of one definition drift,
#: and a mismatch between them would be a wrong number, not a failure.
DERIVED_SERIES: dict[str, str] = {
    name: re.sub(r"(\{d\}\.\w+)", r"\1[{i}]", expression)
    for name, expression in DERIVED_LINES.items()
}

#: Pine timeframe strings mapped to a Backtrader (timeframe, compression).
#: A bare number is minutes; a trailing S, D, W or M names the unit.
_TIMEFRAME_UNITS: dict[str, str] = {
    "S": "bt.TimeFrame.Seconds",
    "": "bt.TimeFrame.Minutes",
    "D": "bt.TimeFrame.Days",
    "W": "bt.TimeFrame.Weeks",
    "M": "bt.TimeFrame.Months",
}


def parse_timeframe(text: Any) -> Optional[tuple[str, int]]:
    """Turn a Pine timeframe string into ``(bt.TimeFrame.X, compression)``.

    ``"240"`` is 240 minutes, ``"D"`` and ``"1D"`` are one day. Returns None
    for anything unrecognised, including the empty string, which in Pine means
    the chart's own timeframe and so needs no second feed.
    """
    if not isinstance(text, str):
        return None
    token: str = text.strip().upper()
    match = re.fullmatch(r"(\d*)([SDWM]?)", token)
    if not match or token == "":
        return None
    count, unit = match.groups()
    if unit == "" and count == "":
        return None
    return _TIMEFRAME_UNITS[unit], int(count) if count else 1


#: Seconds in one bar of a Pine resolution, for flooring a timestamp to it.
#: Weeks and months are deliberately absent: a week is a fixed 604800 seconds
#: only if you accept the week starting on a Thursday, which is where the epoch
#: falls, and a month is not a fixed number of seconds at all.
_TIMEFRAME_SECONDS: dict[str, int] = {"S": 1, "": 60, "D": 86400}


def timeframe_seconds(text: Any) -> Optional[int]:
    """Seconds in one bar of ``text``, or None where Pine's floor is not a modulo.

    ``"240"`` is 14400 and ``"D"`` is 86400. Returns None for the empty string
    -- the chart's own timeframe, which needs no flooring -- and for weeks and
    months, which do not tile the epoch evenly.
    """
    if not isinstance(text, str):
        return None
    token: str = text.strip().upper()
    match = re.fullmatch(r"(\d*)([SDWM]?)", token)
    if not match or token == "":
        return None
    count, unit = match.groups()
    if unit == "" and count == "":
        return None
    per: Optional[int] = _TIMEFRAME_SECONDS.get(unit)
    if per is None:
        return None
    return per * (int(count) if count else 1)


#: Emitted into a strategy that reads ``timeframe.period`` or calls ``time()``.
#:
#: Both are questions about the clock rather than about price, and a running
#: backtest answers them from the feed it was handed. ``timeframe.period``
#: inverts :func:`parse_timeframe`: Pine writes minutes as a bare number and
#: everything else with a unit letter, and writes one of a unit as the bare
#: letter, so ``(Minutes, 240)`` is ``"240"`` and ``(Days, 1)`` is ``"D"``.
#:
#: ``time(res)`` is the opening time of the ``res`` bar containing this one,
#: which is a floor, and that floor is the whole point: it makes
#: ``ta.change(time("240")) != 0`` true on the first chart bar of each new
#: four-hour bar. The assumption the floor carries is stated in the emitted
#: docstring rather than left for the reader to discover.
_TIME_HELPER = (
    "    _PINE_UNITS = (",
    '        (bt.TimeFrame.Seconds, "S"),',
    '        (bt.TimeFrame.Minutes, ""),',
    '        (bt.TimeFrame.Days, "D"),',
    '        (bt.TimeFrame.Weeks, "W"),',
    '        (bt.TimeFrame.Months, "M"),',
    "    )",
    "",
    "    def _pine_timeframe(self):",
    '        """The primary feed\'s timeframe, spelled the way Pine spells it."""',
    "        for frame, unit in self._PINE_UNITS:",
    "            if self.data._timeframe == frame:",
    "                count = int(self.data._compression or 1)",
    '                if unit == "":',
    "                    return str(count)",
    '                return unit if count == 1 else "%d%s" % (count, unit)',
    '        return ""',
    "",
    "    _PINE_TF_SECONDS = {",
    "        bt.TimeFrame.Seconds: 1,",
    "        bt.TimeFrame.Minutes: 60,",
    "        bt.TimeFrame.Days: 86400,",
    "        bt.TimeFrame.Weeks: 604800,",
    "        bt.TimeFrame.Months: 2592000,",
    "    }",
    "",
    "    def _pine_tf_seconds(self, text=None):",
    '        """Seconds in one bar, as Pine\'s timeframe.in_seconds reports it.',
    "",
    "        With no argument it is the feed's own bar, read off the feed rather",
    "        than off a string -- which is why this can be called from __init__,",
    "        where the answer is already settled but no bar has arrived yet.",
    "        Pine's month is a flat 30 days, which is what keeps the answer a",
    "        number rather than a calendar question.",
    '        """',
    "        if text:",
    "            token = str(text).strip().upper()",
    "            digits = ''.join(c for c in token if c.isdigit())",
    "            unit = ''.join(c for c in token if c.isalpha())",
    "            per = {'S': 1, '': 60, 'D': 86400, 'W': 604800,",
    "                   'M': 2592000}.get(unit)",
    "            if per is None:",
    "                return float('nan')",
    "            return per * (int(digits) if digits else 1)",
    "        per = self._PINE_TF_SECONDS.get(self.data._timeframe)",
    "        if per is None:",
    "            return float('nan')",
    "        return per * int(self.data._compression or 1)",
    "",
    "    def _pine_time(self, seconds=None, ago=0, session=None, tz=None):",
    '        """Opening time in epoch milliseconds, as Pine\'s time() reports it.',
    "",
    "        With no resolution this is the bar's own stamp. With one, it is that",
    "        stamp floored to the resolution, so the value changes exactly when a",
    "        new higher-timeframe bar begins. With a session it is NaN -- Pine's",
    "        na -- on a bar whose clock falls outside the session.",
    "",
    "        The floor runs on a continuous clock from midnight UTC. That is exact",
    "        for a market trading around the clock, and for any whose sessions",
    "        divide the day evenly. It is off by the session open for one whose",
    "        four-hour bars start at 09:30.",
    '        """',
    "        if ago and len(self) <= ago:",
    "            # Backtrader reads the far end of the preallocated buffer when",
    "            # the history is not there yet, which is a bar from the future.",
    "            # Pine answers na there, and reading this bar instead makes the",
    "            # difference downstream zero, which is what na produces.",
    "            ago = 0",
    "        when = self.data.datetime.datetime(-ago)",
    "        if session is not None and not self._pine_in_session(when, session, tz):",
    "            return float('nan')",
    "        stamp = calendar.timegm(when.utctimetuple())",
    "        if seconds:",
    "            stamp -= stamp % seconds",
    "        return stamp * 1000",
    "",
)


#: Emitted into a strategy that hands ``time()`` a session argument. Split from
#: ``_TIME_HELPER`` so a strategy that only floors timestamps does not carry
#: session parsing it never calls; the check inside ``_pine_time`` is guarded
#: by ``session is not None``, so it only reaches this method when the
#: generator also emitted it.
_SESSION_HELPER = (
    "    def _pine_in_session(self, when, spec, tz=None):",
    '        """Whether the bar\'s clock falls inside a Pine session string.',
    "",
    "        The check runs on the feed's own timestamps: Pine consults the",
    "        exchange calendar and timezone, which the feed does not carry, and",
    "        the feed's clock is the one clock a backtest has -- so data stamped",
    "        in another timezone than the exchange filters at shifted hours. A",
    "        timezone argument reads that clock as UTC and converts it into",
    "        the named zone first, DST included, so data already stamped in",
    "        exchange time should pass no timezone at all. A day suffix",
    '        (":23456", Sunday=1) names the day the session ends on, which',
    "        for an overnight range is the day after the bar's own.",
    '        """',
    "        parsed = self._pine_sessions.get(spec)",
    "        if parsed is None:",
    '            text, _, suffix = spec.partition(":")',
    "            try:",
    "                ranges = []",
    '                for part in text.split(","):',
    '                    start, dash, end = part.partition("-")',
    "                    if not dash or len(start) != 4 or len(end) != 4:",
    "                        raise ValueError(part)",
    "                    ranges.append(",
    "                        (",
    "                            int(start[:2]) * 60 + int(start[2:]),",
    "                            int(end[:2]) * 60 + int(end[2:]),",
    "                        )",
    "                    )",
    "                days = frozenset(int(d) for d in suffix) if suffix else None",
    "            except ValueError:",
    "                raise ValueError(",
    '                    "session %r is not HHMM-HHMM[,...][:days]" % (spec,)',
    "                ) from None",
    "            parsed = (ranges, days)",
    "            self._pine_sessions[spec] = parsed",
    "        ranges, days = parsed",
    "        if tz is not None:",
    "            when = (",
    "                when.replace(tzinfo=datetime.timezone.utc)",
    "                .astimezone(zoneinfo.ZoneInfo(tz))",
    "                .replace(tzinfo=None)",
    "            )",
    "        minute = when.hour * 60 + when.minute",
    "        # Pine numbers days from Sunday=1; Python from Monday=0.",
    "        day = (when.weekday() + 1) % 7 + 1",
    "        for start, end in ranges:",
    "            if start < end:",
    "                hit, rolls = start <= minute < end, False",
    "            else:",
    "                # An overnight session wraps midnight and ends tomorrow.",
    "                hit, rolls = minute >= start or minute < end, minute >= start",
    "            if not hit:",
    "                continue",
    "            if days is None or (day % 7 + 1 if rolls else day) in days:",
    "                return True",
    "        return False",
    "",
)


#: The shapes Pine writes a timestamp in. Every one is a constant, so it is
#: folded to epoch milliseconds here rather than parsed again on every bar.
#: No offset means UTC, which is what Pine assumes when none is given.
_TIMESTAMP_FORMATS = (
    "%d %b %Y %H:%M %z",
    "%d %b %Y %H:%M:%S %z",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%Y-%m-%d %H:%M %z",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_timestamp(text):
    """Pine's ``timestamp("...")`` as epoch milliseconds, or ``None``."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    for shape in _TIMESTAMP_FORMATS:
        try:
            moment = datetime.datetime.strptime(cleaned, shape)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return int(moment.timestamp() * 1000)
    return None


#: Pine builtins that read straight across to a Backtrader expression.
#: ``strategy.position_size`` is signed and in units on both sides, so the
#: mapping is exact rather than approximate.
BUILTIN_VALUES = {
    "bar_index": "len(self)",
    "math.pi": "math.pi",
    "strategy.position_size": "self.position.size",
    "strategy.position_avg_price": "self.position.price",
    #: `barstate.*` describes where the script is in the chart's history. A
    #: bar-close backtest answers most of it outright: `next()` runs once per
    #: completed historical bar, so that bar is confirmed, it is new, it is
    #: history, and it is not realtime. Constants, not approximations -- see
    #: the module note below.
    "barstate.isconfirmed": "True",
    "barstate.isnew": "True",
    "barstate.ishistory": "True",
    "barstate.isrealtime": "False",
    "barstate.isfirst": "(len(self) == 1)",
    "barstate.islast": "(len(self) == self.data.buflen())",
    #: With no realtime bar to follow it, the last confirmed historical bar is
    #: the last bar.
    "barstate.islastconfirmedhistory": "(len(self) == self.data.buflen())",
}

#: Pine math calls that are Python builtins under another name.
_BUILTIN_MATH = {
    "math.abs": "abs",
    "math.max": "max",
    "math.min": "min",
    "math.round": "round",
}

#: Pine math calls that are `math` module functions of the same name. Only
#: scalar ones: `math.sum` is a rolling window over a series, which is an
#: indicator rather than a function, and is deliberately absent.
_MODULE_MATH = {
    name: f"math.{name.split('.', 1)[1]}"
    for name in (
        "math.sqrt",
        "math.log",
        "math.log10",
        "math.exp",
        "math.floor",
        "math.ceil",
        "math.sin",
        "math.cos",
        "math.tan",
        "math.asin",
        "math.acos",
        "math.atan",
    )
}

INPUT_FUNCS = {
    "input",
    "input.int",
    "input.float",
    "input.bool",
    "input.string",
    "input.source",
    "input.session",
    "input.timeframe",
    "input.symbol",
    "input.time",
}

#: Presentational calls: dropped from the output, reported as ignored.
PRESENTATIONAL = {
    "plot",
    "plotshape",
    "plotchar",
    "plotarrow",
    "plotcandle",
    "plotbar",
    "bgcolor",
    "barcolor",
    "hline",
    "fill",
    "alert",
    "alertcondition",
    "label.new",
    "line.new",
    "box.new",
    "table.new",
    "table.cell",
}

#: Namespaces holding nothing but drawing constants -- `color.green`,
#: `shape.triangleup`, `location.belowbar`. They reach the generator only when
#: a script names one before handing it to a plot, and a plot is dropped, so
#: none of them can change how a strategy trades.
PRESENTATIONAL_NAMESPACES = (
    "color.",
    "display.",
    "extend.",
    "font.",
    "format.",
    "hline.",
    "label.style_",
    "line.style_",
    "location.",
    "plot.style_",
    "position.",
    "scale.",
    "shape.",
    "size.",
    "text.",
    "xloc.",
    "yloc.",
)


def _presentational_constant(name: str) -> bool:
    """True for a drawing constant such as ``color.green`` or ``#00c853``."""
    return name.startswith("#") or name.startswith(PRESENTATIONAL_NAMESPACES)


_BINARY_OPS = {
    "and": "and",
    "or": "or",
    "==": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
    "%": "%",
}

#: Attribute names already meaningful on a ``bt.Strategy``.
_RESERVED = {
    "data",
    "datas",
    "broker",
    "p",
    "params",
    "position",
    "next",
    "buy",
    "sell",
    "close",
    "order",
    "env",
    "cerebro",
    "lines",
}


#: Emitted into a strategy that uses `strategy.exit` with a stop or a limit.
#:
#: Pine's `strategy.exit` is a standing instruction rather than a submission:
#: it is re-evaluated on every bar, and when the levels move it moves its
#: orders instead of adding more. Translating each call into a fresh
#: `self.sell(...)` would stack an order per bar and fill several times, which
#: is a wrong backtest rather than an error -- so the orders are maintained.
#: Emitted above the strategy class when the script uses `ta.pivothigh` or
#: `ta.pivotlow`. Backtrader has no equivalent, but the definition is exact.
#:
#: The bar under test is `right` bars back, and it is a pivot when it beats
#: every one of the `left` bars before it and every one of the `right` bars
#: after it. Both comparisons are strict, which is what Pine does: a flat top
#: -- two equal highs side by side -- is not a pivot high, and scripts that
#: want one write their own with `>=` on the left.
#:
#: Nothing here reads a bar later than the current one, which is the whole
#: point of the `right` offset: the pivot is reported only once `right` more
#: bars have closed and confirmed it.
#:
#: ``once`` is not an optimisation. Backtrader runs indicators vectorised by
#: default, and emulates a missing ``once`` by replaying ``next`` while
#: advancing the inputs by hand -- which reads a bar out of step when an input
#: is itself an indicator carrying a minimum period. Silently, and only on some
#: bars. Writing ``once`` means the emulation is never used, and the two
#: spellings share ``_found`` so the rule they apply cannot drift apart.
_PIVOT_INDICATOR = (
    "class PinePivot(bt.Indicator):",
    '    """Pine\'s ta.pivothigh / ta.pivotlow, as a Backtrader line.',
    "",
    "    Carries the pivot value on the bar that confirms it, NaN elsewhere,",
    "    which is what Pine returns and what `na()` then tests.",
    '    """',
    "",
    '    lines = ("pivot",)',
    '    params = (("left", 5), ("right", 5), ("high", True))',
    "",
    "    def __init__(self):",
    "        self.addminperiod(self.p.left + self.p.right + 1)",
    "",
    "    def _found(self, candidate, window):",
    '        """Strict on both sides: a flat top is not a pivot."""',
    "        if self.p.high:",
    "            return all(candidate > value for value in window)",
    "        return all(candidate < value for value in window)",
    "",
    "    def next(self):",
    "        left, right = self.p.left, self.p.right",
    "        source = self.data",
    "        window = [source[-right + step] for step in range(1, right + 1)]",
    "        window += [source[-right - step] for step in range(1, left + 1)]",
    "        candidate = source[-right]",
    "        self.lines.pivot[0] = (",
    "            candidate if self._found(candidate, window) else float('nan')",
    "        )",
    "",
    "    def once(self, start, end):",
    "        left, right = self.p.left, self.p.right",
    "        source, out = self.data.array, self.lines.pivot.array",
    "        for i in range(start, end):",
    "            window = [source[i - right + step] for step in range(1, right + 1)]",
    "            window += [source[i - right - step] for step in range(1, left + 1)]",
    "            candidate = source[i - right]",
    "            out[i] = (",
    "                candidate if self._found(candidate, window) else float('nan')",
    "            )",
    "",
    "",
)

#: Emitted when a script reads history at an offset only known per bar.
#:
#: `line[-step]` past the start of the series does not raise in Backtrader and
#: does not answer `na`: the whole series is preloaded, so Python's negative
#: indexing counts back from the *end* and hands over a bar from the future.
#: On the first bar of a thirty-bar feed, `close[5]` reads bar 26.
#:
#: A constant offset is handled by sitting out the bars that cannot answer it
#: -- see `_max_lookback`. A computed one cannot be counted at conversion time,
#: so it is checked where it is read.
_BACK_HELPER = (
    "    def _pine_back(self, line, back):",
    '        """`x[n]` with n known only per bar, `na` before the series starts."""',
    "        try:",
    "            step = int(back)",
    "        except (TypeError, ValueError):",
    "            return float('nan')",
    "        if step < 0 or step >= len(line):",
    "            return float('nan')",
    "        return line[-step]",
    "",
)

#: Emitted when the script uses ``ta.alma``. Backtrader has no equivalent, and
#: unlike Hull it is not a composition of moving averages either -- the weights
#: are a Gaussian over the window, so the window has to be walked.
#:
#: The weights depend only on the length, the offset and sigma, so they are
#: computed once rather than per bar.
_ALMA_INDICATOR = (
    "class PineAlma(bt.Indicator):",
    '    """Pine\'s ta.alma: a Gaussian-weighted moving average."""',
    "",
    '    lines = ("alma",)',
    '    params = (("period", 9), ("offset", 0.85), ("sigma", 6.0))',
    "",
    "    def __init__(self):",
    "        length = self.p.period",
    "        centre = self.p.offset * (length - 1)",
    "        spread = length / self.p.sigma",
    "        self._weights = [",
    "            math.exp(-((i - centre) ** 2) / (2 * spread * spread))",
    "            for i in range(length)",
    "        ]",
    "        self._norm = sum(self._weights)",
    "        self.addminperiod(length)",
    "",
    "    def _weighted(self, at):",
    '        """`at` reads the window ending on the bar it is given."""',
    "        length = self.p.period",
    "        total = sum(",
    "            at(length - 1 - i) * weight",
    "            for i, weight in enumerate(self._weights)",
    "        )",
    "        return total / self._norm",
    "",
    "    def next(self):",
    "        self.lines.alma[0] = self._weighted(lambda back: self.data[-back])",
    "",
    "    def once(self, start, end):",
    "        source, out = self.data.array, self.lines.alma.array",
    "        for i in range(start, end):",
    "            out[i] = self._weighted(lambda back, i=i: source[i - back])",
    "",
    "",
)

#: Emitted above the strategy class when an expression has to be evaluated a
#: bar at a time rather than composed from line operators.
#:
#: Backtrader's line arithmetic computes every operand on every bar, which is
#: wrong for a conditional. `d != 0 ? x / d : 0` is written precisely so the
#: division does not happen when `d` is zero, and `bt.If` divides anyway --
#: then Backtrader's line division raises where Pine would answer `na`. Here
#: the expression is an ordinary Python function of the current bar's values,
#: so `if`/`else` is lazy again and the guard the author wrote does its job.
#:
#: The inputs are lines, so Backtrader still works out the minimum period and
#: the evaluation order. Only the *structure* moves into the function.
_EXPR_INDICATOR = (
    "class PineExpr(bt.Indicator):",
    '    """A Pine expression evaluated per bar, so its branches stay lazy."""',
    "",
    '    lines = ("value",)',
    '    params = (("func", None),)',
    "",
    "    def next(self):",
    "        self.lines.value[0] = self.p.func(*[data[0] for data in self.datas])",
    "",
    "    def once(self, start, end):",
    "        # Written out rather than left to Backtrader's `next` emulation,",
    "        # which reads an indicator input a bar out of step.",
    "        arrays = [data.array for data in self.datas]",
    "        out = self.lines.value.array",
    "        for i in range(start, end):",
    "            out[i] = self.p.func(*[array[i] for array in arrays])",
    "",
    "",
)

#: Emitted into any strategy that uses `strategy.entry`.
#:
#: `self.buy()` is not what `strategy.entry` means, and the difference is not
#: small. Pine's default `pyramiding=0` allows **one** entry per direction, so
#: calling `strategy.entry` again while already long does nothing. Backtrader's
#: `buy()` has no such rule: it adds every time, and a condition that holds for
#: twenty bars running builds a twenty-unit position where Pine holds one.
#:
#: An entry against the position is a reversal in Pine -- close the old, open
#: the new -- which is two orders here rather than one resize, so the entry
#: size still comes from the strategy's own sizer.
_ENTRY_HELPER_HEAD = (
    "    def _pine_entry(self, long, size=None, tag=None, limit=None, stop=None):",
    '        """Open or reverse a position, as Pine\'s strategy.entry does.',
    "",
    "        A market entry acts now. One with a limit or stop price is a",
    "        pending order: it is collected under its tag and submitted at the",
    "        end of the bar by _pine_flush, so a strategy.exit issued later in",
    "        the same bar attaches its levels as the bracket's exit legs, and",
    "        re-issuing the tag moves the order rather than stacking another.",
    '        """',
    "        # `limit=na` is how a script spells 'no limit after all'.",
    "        if limit is not None and limit != limit:",
    "            limit = None",
    "        if stop is not None and stop != stop:",
    "            stop = None",
)

#: The three lines ``_pine_entry`` gains when the script carries a
#: ``strategy.risk`` rule -- and only then, because the flags they read are
#: initialised alongside the rule. A strategy with no risk rule would otherwise
#: carry a check on attributes that do not exist.
_ENTRY_HALT_GUARD = (
    "        if self._pine_halted or self._pine_day_halted:",
    "            # A risk rule stopped trading; Pine places no new orders.",
    "            return",
)

_ENTRY_HELPER_TAIL = (
    "        held = self.position.size",
    "        if held > 0 if long else held < 0:",
    "            # pyramiding is 0: Pine allows one entry per direction.",
    "            return",
    "        if limit is None and stop is None:",
    "            if held:",
    "                self.close()",
    "            if long:",
    "                self.buy(size=size)",
    "            else:",
    "                self.sell(size=size)",
    "            return",
    "        self._pine_pending[tag] = {",
    "            'long': long,",
    "            'size': size,",
    "            'limit': limit,",
    "            'stop': stop,",
    "            'exit': None,",
    "        }",
    "",
)

#: Emitted into a strategy whose ``strategy.entry`` carries a limit or stop
#: price, or that calls ``strategy.cancel`` or ``strategy.cancel_all``.
#:
#: A priced entry in Pine is a standing order: it works until it fills, until
#: the same id is re-issued at new levels -- which *moves* it -- or until
#: ``strategy.cancel`` withdraws it. The ``strategy.exit`` that names it via
#: ``from_entry`` is armed with it and only becomes live orders when the entry
#: fills. Backtrader's bracket orders say exactly that -- a parent plus exit
#: legs activated on the parent's fill, one cancelling the other -- so pending
#: entries are collected per tag while the bar's statements run and submitted
#: once, at the end of ``next()``, with whatever exit the bar attached.
_PENDING_HELPER = (
    "",
    "    def _pine_flush(self):",
    '        """Place the bar\'s pending priced entries, as Pine does at bar close.',
    "",
    "        An unchanged spec whose order is still working is left alone, and",
    "        a changed one cancels what it replaces before submitting.",
    "",
    "        Sizing assumes the position is flat or already this way round",
    "        when the order fills -- true of the retrace scripts that rest",
    "        priced entries, which guard on position_size == 0. A fill against",
    "        an open position nets rather than reversing the way Pine would.",
    '        """',
    "        for tag, spec in list(self._pine_pending.items()):",
    "            del self._pine_pending[tag]",
    "            state = self._pine_working.get(tag)",
    "            if state:",
    "                if state[0] == spec and state[1][0].alive():",
    "                    continue",
    "                for order in state[1]:",
    "                    self.cancel(order)",
    "            enter = self.buy if spec['long'] else self.sell",
    "            leave = self.sell if spec['long'] else self.buy",
    "            if spec['stop'] is not None and spec['limit'] is not None:",
    "                placement = {",
    "                    'exectype': bt.Order.StopLimit,",
    "                    'price': spec['stop'],",
    "                    'plimit': spec['limit'],",
    "                }",
    "            elif spec['stop'] is not None:",
    "                placement = {'exectype': bt.Order.Stop, 'price': spec['stop']}",
    "            else:",
    "                placement = {'exectype': bt.Order.Limit, 'price': spec['limit']}",
    "            exit_tag, stop, limit = spec['exit'] or (None, None, None)",
    "            if stop is None and limit is None:",
    "                entry = enter(size=spec['size'], **placement)",
    "                self._pine_working[tag] = (spec, [entry])",
    "                continue",
    "            parent = enter(size=spec['size'], transmit=False, **placement)",
    "            quantity = abs(parent.size)",
    "            legs = []",
    "            if stop is not None:",
    "                legs.append(",
    "                    leave(",
    "                        size=quantity,",
    "                        exectype=bt.Order.Stop,",
    "                        price=stop,",
    "                        parent=parent,",
    "                        transmit=limit is None,",
    "                    )",
    "                )",
    "            if limit is not None:",
    "                legs.append(",
    "                    leave(",
    "                        size=quantity,",
    "                        exectype=bt.Order.Limit,",
    "                        price=limit,",
    "                        parent=parent,",
    "                        transmit=True,",
    "                    )",
    "                )",
    "            self._pine_working[tag] = (spec, [parent] + legs)",
    "            # Register the legs as the exit tag's standing orders, so a",
    "            # later strategy.exit moves them instead of doubling them.",
    "            self._pine_exits[exit_tag] = ((stop, limit), legs)",
    "",
    "    def _pine_cancel(self, tag):",
    '        """Withdraw the unfilled entry with this tag, as strategy.cancel does.',
    "",
    "        An entry that has already filled is out of reach, exactly as it is",
    "        in Pine -- its exit legs keep protecting the open position.",
    '        """',
    "        self._pine_pending.pop(tag, None)",
    "        state = self._pine_working.get(tag)",
    "        if state and state[1][0].alive():",
    "            del self._pine_working[tag]",
    "            for order in state[1]:",
    "                self.cancel(order)",
    "",
    "    def _pine_cancel_all(self):",
    '        """Withdraw every unfilled order, as strategy.cancel_all does.',
    "",
    "        Pending entries not yet submitted, entry brackets resting on the",
    "        book, and the standing exits protecting an open position all go.",
    "        Pine cancels price-triggered exits too, so an open position is",
    "        left unprotected until something closes it -- which is why the",
    "        idiom pairs this call with strategy.close_all().",
    '        """',
    "        self._pine_pending.clear()",
    "        for tag in list(self._pine_working):",
    "            self._pine_cancel(tag)",
    "        for tag, (_, orders) in list(self._pine_exits.items()):",
    "            del self._pine_exits[tag]",
    "            for order in orders:",
    "                self.cancel(order)",
    "",
)

#: Emitted when a script declares one of Pine's account-level risk rules.
#:
#: Pine cancels pending orders, closes the position and refuses new entries
#: once the limit is passed -- for good in the case of `max_drawdown`, and
#: until the next trading day for `max_intraday_loss`. The rule is checked
#: where it is declared, which is where Pine evaluates it.
#:
#: A bar-close run can only act at a close, so the halt lands up to a bar
#: later than Pine's intrabar one. That errs toward showing more loss rather
#: than less, which is the direction to be wrong in.
_RISK_HELPER = (
    "",
    "    def _pine_risk(self, limit, percent, intraday):",
    '        """One of Pine\'s strategy.risk rules, checked once per bar."""',
    "        value = self.broker.getvalue()",
    "        if value > self._pine_peak:",
    "            self._pine_peak = value",
    "        day = self.data.datetime.date(0)",
    "        if day != self._pine_day:",
    "            self._pine_day = day",
    "            self._pine_day_open = value",
    "            self._pine_day_halted = False",
    "        if self._pine_halted or self._pine_day_halted:",
    "            return",
    "        reference = self._pine_day_open if intraday else self._pine_peak",
    "        if reference is None or limit is None:",
    "            return",
    "        threshold = reference * limit / 100.0 if percent else limit",
    "        if reference - value < threshold:",
    "            return",
    "        self._pine_halt()",
    "        if intraday:",
    "            self._pine_day_halted = True",
    "        else:",
    "            self._pine_halted = True",
    "",
    "    def _pine_halt(self):",
    '        """Withdraw what is pending and flatten, as Pine does on a breach."""',
    "        pending = getattr(self, '_pine_pending', None)",
    "        if pending is not None:",
    "            pending.clear()",
    "        # No argument: get_orders_open(safe=True) is documented to hand",
    "        # back clones to manipulate rather than the live orders, and only",
    "        # cancels anything because this backtrader's clone() returns self.",
    "        for order in list(self.broker.get_orders_open()):",
    "            if order.owner is self:",
    "                self.cancel(order)",
    "        if self.position:",
    "            self.close()",
    "",
)

#: Emitted into a strategy whose `switch` between indicators offers a mode this
#: converter cannot build as a line.
#:
#: Which mode is taken is settled when the feed is attached, not when the
#: script is converted, so one unbuildable branch does not make the strategy
#: unconvertible -- every other mode works, and Pine would never evaluate the
#: branch not chosen. The branch becomes this instead of a line: selecting that
#: mode fails by name, at construction, before a bar is priced.
_CHOICE_HELPER = (
    "",
    "    def _pine_no_mode(self, described):",
    '        """Raise for a mode the conversion could not build.',
    "",
    "        Reached only when the inputs select this branch. It fires from",
    "        __init__, so a run either has every indicator it needs or stops",
    "        before its first bar -- never part way through a backtest.",
    '        """',
    "        raise NotImplementedError(",
    '            "this strategy was converted from PineScript, and the mode "',
    '            "selected needs %s, which has no Backtrader line. Choose "',
    '            "another mode, or write this one by hand." % described',
    "        )",
    "",
)

#: Emitted into a strategy that reads any of Pine's trade counters.
#:
#: Backtrader keeps no ledger of closed trades -- ``notify_trade`` reports each
#: one as it happens and then forgets it, and a closed ``Trade`` has already
#: had its ``size`` zeroed. So the ledger is built here, from the two
#: notifications that carry what Pine's accessors ask for. The exit price comes
#: from the fill rather than from arithmetic on the P&L: deriving it would be
#: exact only for a single-entry, single-exit, commission-free trade.
#:
#: ``notify_trade`` runs before ``next()`` on the same bar, so a trade that
#: closed on this bar is already counted when the strategy body reads the
#: counter -- which is what Pine does too.
_TRADES_HELPER = (
    "    def notify_order(self, order):",
    "        if order.status == order.Completed:",
    "            self._pine_fill = order.executed.price",
    "",
    "    def notify_trade(self, trade):",
    "        if trade.justopened:",
    "            self._pine_open = [",
    "                {",
    "                    'entry_price': trade.price,",
    "                    'size': trade.size,",
    "                    'entry_bar': len(self),",
    "                }",
    "            ]",
    "        elif trade.isclosed:",
    "            record = (",
    "                self._pine_open[0]",
    "                if self._pine_open",
    "                else {",
    "                    'entry_price': trade.price,",
    "                    'size': 0,",
    "                    'entry_bar': trade.baropen,",
    "                }",
    "            )",
    "            record['exit_price'] = self._pine_fill",
    "            record['exit_bar'] = len(self)",
    "            record['profit'] = trade.pnlcomm",
    "            self._pine_closed.append(record)",
    "            self._pine_open = []",
    "            if trade.pnlcomm > 0:",
    "                self._pine_wins += 1",
    "            elif trade.pnlcomm < 0:",
    "                self._pine_losses += 1",
    "",
    "    def _pine_trade(self, trades, index, field):",
    '        """One field of one trade, or NaN where Pine would answer `na`."""',
    "        try:",
    "            position = int(index)",
    "        except (TypeError, ValueError):",
    "            return float('nan')",
    "        # Pine has no negative indexing: an out-of-range index is `na`,",
    "        # where Python would silently count from the end.",
    "        if position < 0 or position >= len(trades):",
    "            return float('nan')",
    "        value = trades[position].get(field)",
    "        return float('nan') if value is None else value",
    "",
)

#: What ``strategy.closedtrades`` and friends read, and where the previous
#: bar's value of each is kept. Pine updates them as trades close, so ``[1]``
#: is a real question: `strategy.losstrades > strategy.losstrades[1]` is how a
#: script asks "did a loss just book".
TRADE_COUNTERS = {
    "strategy.closedtrades": ("len(self._pine_closed)", 0),
    "strategy.wintrades": ("self._pine_wins", 1),
    "strategy.losstrades": ("self._pine_losses", 2),
    "strategy.opentrades": ("len(self._pine_open)", 3),
    "strategy.eventrades": (
        "(len(self._pine_closed) - self._pine_wins - self._pine_losses)",
        None,
    ),
}

#: Pine's per-trade accessors, as the ledger field each one reads.
TRADE_FIELDS = {
    "entry_price": "entry_price",
    "exit_price": "exit_price",
    "entry_bar_index": "entry_bar",
    "exit_bar_index": "exit_bar",
    "size": "size",
    "profit": "profit",
}

#: Accessors Backtrader keeps nothing to answer with. Named individually so
#: the report says which one, rather than "unknown identifier".
TRADE_FIELDS_UNTRACKED = {
    "max_runup": "the run-up inside a trade is not recorded",
    "max_drawdown": "the drawdown inside a trade is not recorded",
    "commission": "commission is folded into profit, not kept separately",
    "entry_comment": "orders carry no comment",
    "exit_comment": "orders carry no comment",
    "entry_id": "orders carry no Pine id",
    "exit_id": "orders carry no Pine id",
}

_EXIT_HELPER = (
    "    def _pine_exit(self, tag, from_entry=None, stop=None, limit=None):",
    '        """Keep one exit order set for the open position, as Pine does."""',
    "        # `var float sl = na` is the usual way to spell 'no level yet',",
    "        # and a stop submitted at NaN would never be comparable.",
    "        if stop is not None and stop != stop:",
    "            stop = None",
    "        if limit is not None and limit != limit:",
    "            limit = None",
    "",
    "        pending = self._pine_pending.get(from_entry)",
    "        if pending is not None:",
    "            # The entry this exit names is itself pending, so the levels",
    "            # ride along as the bracket's exit legs when _pine_flush",
    "            # submits it at the end of the bar.",
    "            pending['exit'] = (tag, stop, limit)",
    "            return",
    "        working = self._pine_working.get(from_entry)",
    "        if working is not None and working[1][0].alive():",
    "            # The entry order is on the book but unfilled. Moving its exit",
    "            # levels means resubmitting the bracket, which flush will do.",
    "            spec = dict(working[0])",
    "            spec['exit'] = (tag, stop, limit)",
    "            if spec != working[0]:",
    "                self._pine_pending[from_entry] = spec",
    "            return",
    "",
    "        state = self._pine_exits.get(tag)",
    "        size = self.position.size",
    "        if not size or (stop is None and limit is None):",
    "            if state:",
    "                for order in state[1]:",
    "                    self.cancel(order)",
    "                del self._pine_exits[tag]",
    "            return",
    "",
    "        levels = (stop, limit)",
    "        if state and state[0] == levels and any(o.alive() for o in state[1]):",
    "            return",
    "        if state:",
    "            for order in state[1]:",
    "                self.cancel(order)",
    "",
    "        # Exiting a long means selling; exiting a short means buying.",
    "        leave = self.sell if size > 0 else self.buy",
    "        quantity = abs(size)",
    "        orders = []",
    "        if stop is not None:",
    "            orders.append(",
    "                leave(size=quantity, exectype=bt.Order.Stop, price=stop)",
    "            )",
    "        if limit is not None:",
    "            # oco links the pair, so whichever fills cancels the other.",
    '            extra = {"oco": orders[0]} if orders else {}',
    "            orders.append(",
    "                leave(",
    "                    size=quantity,",
    "                    exectype=bt.Order.Limit,",
    "                    price=limit,",
    "                    **extra,",
    "                )",
    "            )",
    "        self._pine_exits[tag] = (levels, orders)",
)


#: How far a single inlined call may expand. Substitution duplicates a local
#: once per read, so a body that leans on its intermediates grows fast; past
#: this the output stops being something anyone would want to read, and the
#: honest answer is that the function wants real intermediate values.
_INLINE_NODE_LIMIT = 400

#: How big a body local may get, as a substituted expression, before it is
#: given a name in ``next()`` instead of being copied into every read of it.
#:
#: Substitution has nowhere to put an intermediate, and that one absence wears
#: three faces: copying a local everywhere grows the tree multiplicatively,
#: ``adx[1]`` cannot read history off an expression tree, and ``ta.ema(adx, n)``
#: cannot take one as a source. Naming the local answers all three at once --
#: the name is one node, it lands in ``_computed``, and ``_promote`` builds a
#: line from it on demand.
#:
#: Well under :data:`_INLINE_NODE_LIMIT`, because several named locals still
#: have to fit inside it, and comfortably above the arithmetic that makes up
#: an ordinary one-line body, which should stay substituted.
_MATERIALISE_NODE_LIMIT = 60


def _substitute(node: object, bindings: dict[str, object]) -> object:
    """Replace every bound name in ``node`` with the expression bound to it.

    Names that are *not* bound are left alone, which is what Pine means: a
    function body reads anything it did not declare from the one global scope,
    and after inlining that scope is the caller's.
    """
    if isinstance(node, Name):
        return bindings.get(node.id, node)
    if isinstance(node, Index):
        return Index(
            base=_substitute(node.base, bindings),
            offset=_substitute(node.offset, bindings),
        )
    if isinstance(node, Call):
        return Call(
            func=node.func,
            args=tuple(_substitute(a, bindings) for a in node.args),
            kwargs=tuple((k, _substitute(v, bindings)) for k, v in node.kwargs),
        )
    if isinstance(node, Unary):
        return Unary(op=node.op, operand=_substitute(node.operand, bindings))
    if isinstance(node, Binary):
        return Binary(
            op=node.op,
            left=_substitute(node.left, bindings),
            right=_substitute(node.right, bindings),
        )
    if isinstance(node, Ternary):
        return Ternary(
            cond=_substitute(node.cond, bindings),
            then=_substitute(node.then, bindings),
            other=_substitute(node.other, bindings),
        )
    if isinstance(node, ListLit):
        return ListLit(items=tuple(_substitute(i, bindings) for i in node.items))
    return node  # a literal, which has nothing to substitute into


def _node_count(node: object) -> int:
    """Size of an expression tree, used to bound how far inlining may expand."""
    total: int = 1
    for child in (
        getattr(node, "base", None),
        getattr(node, "offset", None),
        getattr(node, "operand", None),
        getattr(node, "left", None),
        getattr(node, "right", None),
        getattr(node, "cond", None),
        getattr(node, "then", None),
        getattr(node, "other", None),
    ):
        if child is not None:
            total += _node_count(child)
    for child in getattr(node, "args", ()) or ():
        total += _node_count(child)
    for _, child in getattr(node, "kwargs", ()) or ():
        total += _node_count(child)
    for child in getattr(node, "items", ()) or ():
        total += _node_count(child)
    return total


def _if_as_assignment(statement: If) -> Optional[tuple[str, object]]:
    """Read an ``if`` whose every branch assigns one name as a conditional.

    This is the shape a Pine function uses to pick a value over several
    branches and then hand it back:

        moment = 0
        if a and b
            moment := 1
        else if c
            moment := 2
        else
            moment := 4
        moment

    Every branch writes the same name, so the block is one assignment whose
    right-hand side is a chain of conditionals -- which is what
    :meth:`_Generator._inline_body` can carry forward in its substitution.

    A missing ``else`` binds the name to *itself*. Pine leaves a variable
    alone when no branch runs, and substitution turns that back into whatever
    the name last held, where :func:`_if_as_expression` would have to answer
    ``na`` because it has no name to fall back on.

    Returns ``(target, expression)``, or None when the branches disagree about
    what they are writing or carry more than one statement.
    """

    def branch(body: list) -> Optional[tuple[str, object]]:
        if (
            len(body) == 1
            and isinstance(body[0], Assign)
            and body[0].qualifier in ("", ":=")
        ):
            return body[0].target, body[0].value
        return None

    head: Optional[tuple[str, object]] = branch(statement.body)
    if head is None:
        return None
    target, value = head

    if not statement.orelse:
        other: object = Name(target)
    elif len(statement.orelse) == 1 and isinstance(statement.orelse[0], If):
        nested: Optional[tuple[str, object]] = _if_as_assignment(statement.orelse[0])
        if nested is None or nested[0] != target:
            return None
        other = nested[1]
    else:
        tail: Optional[tuple[str, object]] = branch(statement.orelse)
        if tail is None or tail[0] != target:
            return None
        other = tail[1]

    return target, Ternary(cond=statement.cond, then=value, other=other)


def _if_as_expression(statement: If) -> Optional[object]:
    """Read a trailing ``if`` block as the value its function returns.

    Pine hands back the last expression of whichever branch ran, so an ``if``
    in this position is a conditional expression written over several lines --
    the same shape :func:`~pwb_toolbox.converting.parser.Parser.parse_if_expression`
    already folds. Returns ``None`` when a branch carries more than one
    expression, which a conditional cannot hold.
    """

    def branch(body: list) -> Optional[object]:
        if len(body) == 1 and isinstance(body[0], ExprStmt):
            return body[0].value
        if len(body) == 1 and isinstance(body[0], Assign) and not body[0].qualifier:
            return body[0].value
        return None

    then: Optional[object] = branch(statement.body)
    if then is None:
        return None
    if not statement.orelse:
        # Pine yields `na` when a value-carrying `if` falls off the end.
        return Ternary(cond=statement.cond, then=then, other=Na())
    if len(statement.orelse) == 1 and isinstance(statement.orelse[0], If):
        other = _if_as_expression(statement.orelse[0])
    else:
        other = branch(statement.orelse)
    if other is None:
        return None
    return Ternary(cond=statement.cond, then=then, other=other)


class _InlineFailure(Exception):
    """A nested inline could not be done. The reason is already reported."""


class ConversionError(RuntimeError):
    """Raised when the source cannot be converted at all."""


@dataclass
class ConversionResult:
    code: str
    class_name: str
    params: list = field(default_factory=list)
    unsupported: list = field(default_factory=list)
    ignored: list = field(default_factory=list)
    #: Readings the conversion made deliberately that differ from the Pine
    #: source as written. Not gaps -- the translation is faithful on the bars
    #: a backtest runs -- but the reader should know one was made.
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing needing attention was left behind.

        Neither ignored presentational calls nor notes count. Dropping a
        ``plot`` does not change how the strategy trades, and a note records a
        reading that is faithful for a backtest rather than a gap in one.
        """
        return not self.unsupported


def _describe(node: Any) -> str:
    """A short label for an expression, for naming it in a message.

    Only ever read by a human: the mode of a switch this converter cannot
    build, so `f_jma(src, length, jmaPhase, jmaPower)` should come back as
    something recognisable in the original source rather than a node type.
    """
    if isinstance(node, Call):
        return f"{node.func}()"
    if isinstance(node, Name):
        # A branch reached through an inlined function arrives as the
        # generated name of its state slot -- `f_jma_value_2`. The trailing
        # counter is noise to a reader looking for this in their own source;
        # what is left still carries the function's name.
        return re.sub(r"_\d+$", "", node.id)
    if isinstance(node, (Num, Str, Bool)):
        return repr(_literal(node))
    return type(node).__name__.lower()


def _class_name(title: str, fallback: str = "ConvertedStrategy") -> str:
    parts: list[str] = re.findall(r"[A-Za-z0-9]+", title or "")
    name: str = "".join(part[:1].upper() + part[1:] for part in parts)
    if not name or name[0].isdigit():
        return fallback
    return name


def _slug(title: Any) -> str:
    """Turn an input's title into a Python identifier: 'Stop %' -> 'stop'."""
    if not isinstance(title, str):
        return ""
    parts: list[str] = re.findall(r"[A-Za-z0-9]+", title)
    name: str = "_".join(parts).lower()
    return f"p_{name}" if name[:1].isdigit() else name


def _safe(name: str) -> str:
    if keyword.iskeyword(name) or name in _RESERVED or name.startswith("_"):
        return f"pine_{name}"
    return name


def _literal(node: object) -> Any:
    if isinstance(node, Num):
        value = node.value
        return int(value) if value == int(value) else value
    if isinstance(node, Str):
        return node.value
    if isinstance(node, Bool):
        return node.value
    return None


class _Generator:
    def __init__(self, program: Any, class_name: Optional[str] = None) -> None:
        self.program: Any = program
        self.declaration: Optional[tuple[str, str]] = program.declaration
        title: str = self.declaration[1] if self.declaration else ""
        self.class_name: str = class_name or _class_name(title)
        self.params: list[tuple[str, Any]] = []  # (pine_name, default)
        self.param_names: set[str] = set()
        self.series: dict[str, str] = {}  # pine name -> attribute name in __init__
        self.scalars: set[str] = set()  # names computed as locals in next()
        self.init_lines: list[str] = []
        self.next_lines: list[str] = []
        self.unsupported: list[tuple[str, str]] = []
        self.ignored: list[str] = []
        self.notes: list[str] = []
        self._counter: int = 0
        self._hoisted: dict[str, str] = {}  # construction source -> attribute name
        self._inputs: dict[str, str] = {}  # input call signature -> param name
        self.state: dict[str, str] = (
            {}
        )  # pine `var` name -> attribute name on the strategy
        self.feeds: list[tuple[str, int]] = (
            []
        )  # (bt.TimeFrame expression, compression) per extra feed
        self._feed_index: dict[str, int] = {}  # timeframe text -> index into self.datas
        #: The feed expressions currently lower against. Swapped while the
        #: inner expression of a request.security is translated.
        self._feed: str = "self.data"
        self._uses_exit: bool = False
        self._uses_math: bool = False
        self._uses_trades: bool = False
        self._uses_entry: bool = False
        self._uses_pending: bool = False
        self._uses_time: bool = False
        self._uses_session: bool = False
        self._uses_pivot: bool = False
        self._uses_expr: bool = False
        self._uses_alma: bool = False
        #: The generated param `syminfo.mintick` reads from, once registered.
        self._mintick: Optional[str] = None
        #: The deepest `name[k]` history read emitted into ``next()``. Bars
        #: that do not have that much history yet are skipped outright: Pine
        #: answers na there, and no condition on na fires, where Backtrader's
        #: preloaded buffer wraps a read past the start around to the *end* of
        #: the feed -- bars from the future, silently.
        self._max_lookback: int = 0
        self._uses_back: bool = False
        self._uses_risk: bool = False
        self._uses_choice: bool = False
        #: Pine name -> the expression it was assigned, for top-level plain
        #: assignments only. Read by `_promote` when one is wanted as a line.
        self._computed: dict[str, object] = {}
        self._promoted: dict[str, str] = {}
        self._promoting: set[str] = set()
        self._promotable: set[str] = self._promotable_names()
        self.functions: dict[str, Any] = program.functions
        #: Names currently being inlined, so recursion is caught rather than
        #: followed. A stack catches mutual recursion as well as direct.
        self._inlining: list[str] = []
        #: Lines that must run in front of the statement being emitted -- the
        #: per-bar state updates of an inlined function that keeps `var`.
        self._prelude: list[str] = []
        #: Depth of `_const_choice` branch lowering. Non-zero means `_hoist`
        #: hands back constructions instead of registering them in `__init__`.
        self._inline_depth: int = 0
        #: State slot -> the Pine name it came from, for slots whose `[1]` may
        #: be read. Only function-body vars qualify; see `_state_history`.
        self._var_history: dict[str, str] = {}
        #: State slots already assigned during the body being inlined.
        self._var_written: set[str] = set()

    # --- helpers -------------------------------------------------------------

    def _fresh(self, stem: str) -> str:
        self._counter += 1
        return f"_{stem}_{self._counter}"

    def _local(self, stem: str) -> str:
        """A fresh name for a local in ``next()``.

        Unlike an attribute it carries no leading underscore, which `_safe`
        would otherwise have to escape -- `pine__f_jma_beta_6` reads far worse
        than `f_jma_beta_6` for no gain.
        """
        return self._fresh(stem)[1:]

    def _hoist(self, stem: str, construction: str) -> str:
        """Build ``construction`` in ``__init__`` once, returning its handle.

        Identical constructions are shared. Backtrader recomputes every
        indicator on every bar, so emitting the same CrossOver twice -- which
        ``ta.crossover``/``ta.crossunder`` on one pair otherwise does -- would
        double that work for no benefit.
        """
        if self._inline_depth:
            # Inside a branch of a construction-time choice. The caller is
            # assembling one expression that must build only the branch it
            # takes, so this construction goes into that expression rather
            # than into `__init__` on a line of its own -- where it would be
            # built whether or not its branch is chosen. See `_const_choice`.
            return construction
        attr: Optional[str] = self._hoisted.get(construction)
        if attr is None:
            attr = self._fresh(stem)
            self._hoisted[construction] = attr
            self.init_lines.append(f"self.{attr} = {construction}")
        return f"self.{attr}"

    def _line_read(self, construction: str, index: int = 0) -> str:
        """Read a lowered line expression at an index, inside ``next()``.

        A bare handle -- a feed line, a hoisted indicator -- indexes
        directly. A composed expression only *builds* a line during
        ``__init__``; the same arithmetic inside ``next()`` runs on this
        bar's floats, and subscripting the float it returns raises. So
        anything composed is hoisted first and read through its attribute.
        """
        if "(" in construction:
            construction = self._hoist("line", construction)
        return f"{construction}[{index}]"

    def _reject(self, message: str) -> None:
        if message not in self.unsupported:
            self.unsupported.append(message)

    def _mintick_param(self) -> str:
        """``syminfo.mintick`` as a param: the tick size is the instrument's.

        Pine reads it off the symbol. A Backtrader feed carries no tick size,
        so it becomes a tunable param with the 0.01 of a US equity as its
        default, and the report says to set it per instrument.
        """
        if self._mintick is None:
            name: str = "mintick"
            suffix: int = 2
            while name in self.param_names:
                name, suffix = f"mintick_{suffix}", suffix + 1
            self.params.append((name, 0.01))
            self.param_names.add(name)
            self._mintick = name
            self._ignore(
                f"syminfo.mintick became the {self._mintick!r} param (default "
                "0.01): the tick size is a property of the instrument, so set "
                "it when adding the strategy"
            )
        return f"self.p.{_safe(self._mintick)}"

    def _ignore(self, message: str) -> None:
        if message not in self.ignored:
            self.ignored.append(message)

    def _note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def _note_varip(self) -> None:
        """Record, once, that ``varip`` was read as ``var``.

        The two differ only on a realtime bar, where ``varip`` is not rolled
        back between ticks. A backtest has no realtime bars, and Pine's own
        documentation is explicit that the distinction cannot be reproduced on
        historical ones -- so on the bars a backtest actually runs, reading
        ``varip`` as ``var`` is what Pine itself does, not an approximation of
        it.

        Noted rather than silent, because the script was written by someone
        who wanted the intrabar behaviour somewhere, and a live run is where
        they would not get it.
        """
        self._note(
            "varip read as var: the two are identical on historical bars, "
            "which is all a backtest has. Pine cannot reproduce varip's "
            "intrabar behaviour on historical bars either, so nothing is "
            "lost here that Pine would have kept -- but a live run would "
            "differ, and this conversion does not carry that difference"
        )

    # --- user-defined functions ----------------------------------------------

    def _inline_call(self, call):
        """Substitute a call's arguments into its body, returning an expression.

        Pine's functions are not Python's. Each call site keeps its own series
        state, so two calls to one function share nothing -- which is exactly
        what substituting the arguments into a fresh copy of the body produces.
        Doing it on the AST rather than on generated source also means the rest
        of this module needs no special case: by the time anything else sees
        the result, it is an ordinary expression.

        Returns ``None`` when the body is outside the subset, having reported
        why.
        """
        func = self.functions[call.func]

        if call.func in self._inlining:
            self._reject(
                f"{call.func}(): a recursive function cannot be inlined, and "
                "Backtrader has nothing to recurse over"
            )
            return None

        bindings = self._bind_arguments(func, call)
        if bindings is None:
            return None

        self._inlining.append(call.func)
        try:
            if any(
                isinstance(s, Assign) and s.qualifier in ("var", "varip")
                for s in func.body
            ):
                return self._inline_stateful(call, func, bindings)
            result = self._inline_body(func, bindings)
            if result is None:
                return None
            if isinstance(result, ListLit):
                # `[lower, upper, atr]` at the end of a body returns several
                # values at once, which only a destructuring call site can
                # take -- and that is reported in its own right.
                self._reject(
                    f"{call.func}() returns a tuple, which needs tuple "
                    "destructuring at the call site"
                )
                return None
            # Resolve calls the body makes to other functions here, while this
            # one is still on the stack. Doing it later -- when the caller
            # lowers the result -- would let a recursive function recurse
            # forever, because the stack would already have been unwound.
            result = self._resolve_user_calls(result)
        except _InlineFailure:
            return None
        finally:
            self._inlining.pop()

        # Substitution copies a local everywhere it is read, so a body that
        # reads its own intermediates many times over grows multiplicatively.
        # The indicator calls inside are still built once -- `_hoist` shares
        # identical constructions -- but the arithmetic around them is not.
        if _node_count(result) > _INLINE_NODE_LIMIT:
            self._reject(
                f"{call.func}(): inlining this expands past "
                f"{_INLINE_NODE_LIMIT} nodes, which is a sign the body wants "
                "real intermediate values rather than substitution"
            )
            return None
        return result

    def _inline_stateful(self, call, func, bindings):
        """Inline a body that keeps ``var`` state across bars.

        A pure body folds into one expression. A body with ``var`` in it
        cannot: the state has to be *updated*, in order, once per bar. So this
        emits statements into the prelude -- the lines that go in front of
        whichever statement the call appears in -- and hands back a name.

        Each call site gets its own attributes, which is the same rule that
        makes inlining the right translation in the first place. Two calls to
        one filter are two filters, and they are here.
        """
        slug = _slug(func.name) or "fn"
        for position, statement in enumerate(func.body):
            last = position == len(func.body) - 1

            if isinstance(statement, Assign) and statement.qualifier == "varip":
                self._note_varip()
                statement = Assign(statement.target, statement.value, "var")

            if isinstance(statement, Assign) and statement.qualifier == "var":
                initial = self._state_initial(statement.value)
                if initial is None:
                    self._reject(
                        f"{func.name}(): `var {statement.target}` needs a literal "
                        "initial value, because __init__ runs before the first bar"
                    )
                    return None
                slot = self._fresh(f"{slug}_{_safe(statement.target.lstrip(chr(95)))}")
                # Key the slot by its own attribute name: unique per call site,
                # so a body local can never collide with one of the caller's.
                self.state[slot] = slot
                self._var_history[slot] = statement.target
                self.init_lines.append(f"self.{slot} = {initial}")
                bindings[statement.target] = Name(slot)
                if last:
                    return Name(slot)
                continue

            if isinstance(statement, Assign):
                bound = bindings.get(statement.target)
                held = isinstance(bound, Name) and bound.id in self.state
                value = self._value_expr(_substitute(statement.value, bindings))
                if held:
                    self._prelude.append(
                        (True, f"self.{self.state[bound.id]} = {value}")
                    )
                    # Only now: the right-hand side above still had to see the
                    # previous bar's value, which is what `e0[1]` means.
                    self._var_written.add(bound.id)
                    if last:
                        return bound
                    continue
                name = self._local(f"{slug}_{_safe(statement.target.lstrip(chr(95)))}")
                self._prelude.append((True, f"{_safe(name)} = {value}"))
                self.scalars.add(name)
                bindings[statement.target] = Name(name)
                if last:
                    return Name(name)
                continue

            if isinstance(statement, ExprStmt) and last:
                value = self._value_expr(_substitute(statement.value, bindings))
                name = self._local(f"{slug}_value")
                self._prelude.append((True, f"{_safe(name)} = {value}"))
                self.scalars.add(name)
                return Name(name)

            if isinstance(statement, ExprStmt) and (
                isinstance(statement.value, Call)
                and statement.value.func in PRESENTATIONAL
            ):
                self._ignore(f"{statement.value.func}() dropped: presentational only")
                continue

            if isinstance(statement, If) and last:
                folded = _if_as_expression(statement)
                if folded is None:
                    self._reject(
                        f"{func.name}(): the trailing `if` has a branch carrying "
                        "more than one expression"
                    )
                    return None
                value = self._value_expr(_substitute(folded, bindings))
                name = self._local(f"{slug}_value")
                self._prelude.append((True, f"{_safe(name)} = {value}"))
                self.scalars.add(name)
                return Name(name)

            if isinstance(statement, Unsupported):
                self._reject(
                    f"{func.name}(): its body uses {statement.kind}, which is "
                    "not supported"
                )
                return None

            self._reject(
                f"{func.name}(): its body uses "
                f"{type(statement).__name__.lower()}, which this does not model"
            )
            return None

        self._reject(f"{func.name}(): its body is empty")
        return None

    def _resolve_user_calls(self, node):
        """Inline every user-defined call inside ``node``, innermost first."""
        if isinstance(node, Call):
            resolved = Call(
                func=node.func,
                args=tuple(self._resolve_user_calls(a) for a in node.args),
                kwargs=tuple((k, self._resolve_user_calls(v)) for k, v in node.kwargs),
            )
            if resolved.func not in self.functions:
                return resolved
            inlined = self._inline_call(resolved)
            if inlined is None:
                raise _InlineFailure
            return inlined
        if isinstance(node, Index):
            return Index(
                base=self._resolve_user_calls(node.base),
                offset=self._resolve_user_calls(node.offset),
            )
        if isinstance(node, Unary):
            return Unary(op=node.op, operand=self._resolve_user_calls(node.operand))
        if isinstance(node, Binary):
            return Binary(
                op=node.op,
                left=self._resolve_user_calls(node.left),
                right=self._resolve_user_calls(node.right),
            )
        if isinstance(node, Ternary):
            return Ternary(
                cond=self._resolve_user_calls(node.cond),
                then=self._resolve_user_calls(node.then),
                other=self._resolve_user_calls(node.other),
            )
        if isinstance(node, ListLit):
            return ListLit(items=tuple(self._resolve_user_calls(i) for i in node.items))
        return node

    def _bind_arguments(self, func, call):
        """Map each parameter to the expression the call supplies for it."""
        supplied = dict(call.kwargs)
        unknown = supplied.keys() - {p.name for p in func.params}
        if unknown:
            self._reject(
                f"{call.func}(): no parameter named {sorted(unknown)[0]!r}",
            )
            return None
        if len(call.args) > len(func.params):
            self._reject(
                f"{call.func}() takes {len(func.params)} arguments but "
                f"{len(call.args)} were given"
            )
            return None

        bindings = {}
        for position, param in enumerate(func.params):
            if position < len(call.args):
                bindings[param.name] = call.args[position]
            elif param.name in supplied:
                bindings[param.name] = supplied[param.name]
            elif param.default is not None:
                bindings[param.name] = param.default
            else:
                self._reject(
                    f"{call.func}(): no argument for {param.name!r}, which has "
                    "no default"
                )
                return None
        return bindings

    def _materialise(self, func, target, node):
        """Give a body local a name in ``next()`` rather than copying it around.

        Returns the ``Name`` to bind in its place. Registering the node in
        ``_computed`` is the part that matters beyond size: it is what lets
        :meth:`_promote` build a line for the same expression later, so the
        local can carry history and feed an indicator -- neither of which a
        substituted expression tree can do.

        The prelude only reaches ``next()`` from a top-level statement, which
        :meth:`_emit_statement` already enforces for the stateful path and now
        covers this one too.
        """
        stem = f"{_slug(func.name) or 'fn'}_{_safe(target.lstrip(chr(95)))}"
        name = self._local(stem)
        value = self._value_expr(node)
        self._prelude.append((False, f"{_safe(name)} = {value}"))
        self.scalars.add(name)
        # Single, unconditional, never reassigned -- true by construction here,
        # which is exactly what `_promotable` checks for a top-level name.
        self._computed[name] = node
        return Name(name)

    def _inline_body(self, func, bindings):
        """Fold a body down to the single expression its call site stands for.

        Each assignment extends the substitution rather than emitting a line,
        so a local is read as whatever it was last assigned. That makes ``:=``
        work for free: rebinding leaves earlier reads holding the earlier
        value, which is what sequential assignment means.
        """
        for position, statement in enumerate(func.body):
            last = position == len(func.body) - 1

            if isinstance(statement, Assign):
                if statement.qualifier in ("var", "varip"):
                    self._reject(
                        f"{func.name}(): `{statement.qualifier} "
                        f"{statement.target}` keeps state per call site, which "
                        "inlining cannot give it"
                    )
                    return None
                value = _substitute(statement.value, bindings)
                if not last and _node_count(value) > _MATERIALISE_NODE_LIMIT:
                    value = self._materialise(func, statement.target, value)
                bindings[statement.target] = value
                if last:
                    return value
                continue

            if isinstance(statement, ExprStmt):
                if last:
                    return _substitute(statement.value, bindings)
                if (
                    isinstance(statement.value, Call)
                    and statement.value.func in PRESENTATIONAL
                ):
                    self._ignore(
                        f"{statement.value.func}() dropped: presentational only"
                    )
                    continue
                self._reject(
                    f"{func.name}(): a statement in the middle of the body has "
                    "no value to carry forward"
                )
                return None

            if isinstance(statement, If):
                # A block that writes one name is an assignment, wherever it
                # sits. Try that first: it is the only reading available to an
                # `if` in the middle of a body, and for a trailing one it also
                # keeps the name's previous value when no branch runs.
                assigned = _if_as_assignment(statement)
                if assigned is not None:
                    target, folded = assigned
                    if target not in bindings:
                        self._reject(
                            f"{func.name}(): `{target}` is assigned only inside "
                            "an `if`, so it has no value on the branch that "
                            "does not run"
                        )
                        return None
                    value = _substitute(folded, bindings)
                    if not last and _node_count(value) > _MATERIALISE_NODE_LIMIT:
                        value = self._materialise(func, target, value)
                    bindings[target] = value
                    if last:
                        return value
                    continue
                if not last:
                    self._reject(
                        f"{func.name}(): an `if` in the middle of the body has "
                        "to write one name in every branch to carry a value "
                        "forward"
                    )
                    return None
                folded = _if_as_expression(statement)
                if folded is None:
                    self._reject(
                        f"{func.name}(): the trailing `if` has a branch "
                        "carrying more than one expression, so it does not "
                        "read as the function's value"
                    )
                    return None
                return _substitute(folded, bindings)

            if isinstance(statement, Unsupported):
                self._reject(
                    f"{func.name}(): its body uses {statement.kind}, which is "
                    "not supported"
                )
                return None

            self._reject(
                f"{func.name}(): its body uses "
                f"{type(statement).__name__.lower()}, which inlining cannot "
                "reduce to a single expression"
            )
            return None

        self._reject(f"{func.name}(): its body is empty")
        return None

    # --- expression lowering -------------------------------------------------

    def _line_expr(self, node):
        """Lower an expression for ``__init__``, where values are line objects.

        Backtrader overloads arithmetic on lines, so a composed expression is
        a line too and can be fed straight to an indicator. That is what lets
        ``ta.ema(hlc3 - close, 10)`` work at all: the operators here mean the
        same thing they mean in :meth:`_value_expr`, evaluated per bar by
        Backtrader rather than by the generated ``next()``.

        Returns ``None`` when the expression cannot be expressed as a line.
        """
        if isinstance(node, Name):
            if node.id in PRICE_SERIES:
                return f"{self._feed}.{PRICE_SERIES[node.id]}"
            if node.id in DERIVED_LINES:
                # `hlc3` is arithmetic over three lines, which is a line.
                return "(" + DERIVED_LINES[node.id].format(d=self._feed) + ")"
            if node.id in self.series:
                return f"self.{self.series[node.id]}"
            if node.id in self.param_names:
                return f"self.p.{_safe(node.id)}"
            if node.id == "syminfo.mintick":
                # A constant for the run, like a param, so line arithmetic
                # accepts it wherever it accepts one.
                return self._mintick_param()
            return self._promote(node.id)
        if isinstance(node, Num):
            return repr(_literal(node))
        if isinstance(node, Index):
            offset = _literal(node.offset)
            if not isinstance(offset, int):
                return None
            base = self._line_expr(node.base)
            # `close(-1)` is the line delayed by a bar. `close[-1]` would be a
            # read, and __init__ runs before there is a bar to read.
            return None if base is None else f"{base}({-offset})"
        if isinstance(node, Unary):
            if node.op != "-":
                return None
            operand = self._line_expr(node.operand)
            return None if operand is None else f"(-{operand})"
        if isinstance(node, Binary):
            if node.op not in _LINE_OPS:
                # A comparison is not a source. `and`/`or` over lines are, but
                # only inside a `PineExpr`, where they stay short-circuiting.
                return None
            left = self._line_expr(node.left)
            right = self._line_expr(node.right)
            if left is None or right is None:
                return None
            return f"({left} {node.op} {right})"
        if isinstance(node, Ternary):
            chosen = self._const_choice(node)
            if chosen is not None:
                return chosen
            # Not `bt.If`: it computes both branches on every bar, which
            # defeats the guard a conditional usually exists to be.
            return self._expr_line(node)
        if isinstance(node, Call):
            if node.func in self.functions:
                inlined = self._inline_call(node)
                return None if inlined is None else self._line_expr(inlined)
            if node.func == "request.security":
                return self._line_security(node)
            if node.func in COMPOSED_AVERAGES:
                return self._hoist_moving_average(node)
            if node.func in _LINE_MATH or node.func in ("math.pow", "math.avg"):
                parts = [self._line_expr(arg) for arg in node.args]
                if not parts or any(part is None for part in parts):
                    return None
                if node.func == "math.pow":
                    if len(parts) != 2:
                        return None
                    # `**` rather than math.pow, matching what `_value_expr`
                    # emits: the two must not disagree about the same call.
                    return f"({parts[0]} ** {parts[1]})"
                if node.func == "math.avg":
                    return f"(({' + '.join(parts)}) / {len(parts)})"
                return f"{_LINE_MATH[node.func]}({', '.join(parts)})"
            return self._hoist_indicator(node)
        return None

    def _const_expr(self, node):
        """Lower an expression ``__init__`` can evaluate, or ``None``.

        The sibling of :meth:`_period_expr`, and for the same reason. A period
        has to be a number by the time the indicator is constructed; a *choice
        between* indicators has to be settled by then too. What qualifies is
        the same on both sides -- literals, params, the clock -- with
        comparisons and booleans allowed here, since this lowers a condition
        rather than a length.

        Anything reading price is refused. A series holds no value when
        ``__init__`` runs, and a condition that moves per bar is not a choice
        between indicators at all; that one belongs in a ``PineExpr``.
        """
        if isinstance(node, (Num, Str, Bool)):
            return repr(_literal(node))
        if isinstance(node, Na):
            return "float('nan')"
        if isinstance(node, Name):
            if node.id in self.param_names:
                return f"self.p.{_safe(node.id)}"
            if node.id == "timeframe.period":
                self._uses_time = True
                return "self._pine_timeframe()"
            if node.id == "syminfo.mintick":
                return self._mintick_param()
            inner = self._computed.get(node.id)
            if inner is None or node.id in self._promoting:
                return None
            self._promoting.add(node.id)
            try:
                return self._const_expr(inner)
            finally:
                self._promoting.discard(node.id)
        if isinstance(node, Unary):
            operand = self._const_expr(node.operand)
            if operand is None:
                return None
            return f"(not {operand})" if node.op == "not" else f"({node.op}{operand})"
        if isinstance(node, Binary):
            operator = _BINARY_OPS.get(node.op)
            if operator is None:
                return None
            left = self._const_expr(node.left)
            right = self._const_expr(node.right)
            if left is None or right is None:
                return None
            return f"({left} {operator} {right})"
        if isinstance(node, Ternary):
            parts = [
                self._const_expr(part) for part in (node.cond, node.then, node.other)
            ]
            if any(part is None for part in parts):
                return None
            cond, then, other = parts
            return f"({then} if {cond} else {other})"
        if isinstance(node, Call) and node.func == "timeframe.in_seconds":
            return self._tf_seconds(node)
        return None

    def _const_choice(self, node):
        """A ternary picking between lines on a condition ``__init__`` settles.

        ``switch mode`` over ``ta.sma`` / ``ta.rma`` / ``ta.hma`` is the
        commonest shape in the corpus, usually reached through a function and
        keyed off an input or the chart's timeframe. None of those move during
        a run, so the choice is made once, by a Python conditional expression
        in ``__init__``.

        That it is a conditional *expression* is the substance, not the style.
        Backtrader's minimum period is the maximum over every indicator the
        strategy holds -- read or not -- so a 5-period average built beside an
        unused 80-period one produces nothing until bar 80. Building the
        branch not taken would move the bar a converted strategy starts
        trading on, and nothing about the generated source would look wrong.
        Only the taken branch is built, which is what a conditional expression
        gives and what a selector over pre-built branches would not.
        """
        condition = self._const_expr(node.cond)
        if condition is None:
            return None
        if (
            self._const_expr(node.then) is not None
            or self._const_expr(node.other) is not None
        ):
            # A branch that is itself a construction-time value is a number,
            # not a line, and a number cannot be read at `[0]`. `mode == 'A' ?
            # 5 : 20` is a constant and belongs in the scalar path; so does
            # the mixed case, where which branch is a line depends on the
            # condition and only one of the two answers could be read.
            return None
        # Speculative: a branch that will not lower files its own rejection on
        # the way out, and the caller's fallback is the path that decides what
        # to report. Unwound the way `_shifted_read` unwinds its attempt.
        mark = len(self.unsupported)
        self._inline_depth += 1
        try:
            then = self._line_expr(node.then)
            other = self._line_expr(node.other)
        finally:
            self._inline_depth -= 1
        del self.unsupported[mark:]
        if then is None and other is None:
            # Neither side is a line; there is no choice here to settle.
            return None
        then = then if then is not None else self._unbuildable(node.then)
        other = other if other is not None else self._unbuildable(node.other)
        return self._hoist("choice", f"({then} if {condition} else {other})")

    def _unbuildable(self, node):
        """A branch that cannot be a line, as something that says so if taken.

        Which branch a construction-time choice takes is not known until the
        feed is attached, so a script offering one mode this converter cannot
        build is not thereby a script that cannot be converted -- every other
        mode works, and Pine would never evaluate the branch not chosen.

        The branch becomes a raise instead of a line. Selecting that mode
        fails at construction, by name, before a single bar is priced; every
        other selection is unaffected. The alternative -- refusing the whole
        strategy -- throws away the modes that do work over one that might
        never be asked for.
        """
        self._uses_choice = True
        self._note(
            f"one mode of a switch between indicators needs {_describe(node)}, "
            "which has no Backtrader line. Selecting that mode raises at "
            "construction; every other mode the script offers converts and runs"
        )
        return f"self._pine_no_mode({_describe(node)!r})"

    def _period_expr(self, node):
        """Lower a length, or a pivot's bar count, or return ``None``.

        This is not ``_line_expr`` and must not be: a period is read once,
        when the indicator is constructed, so it has to be a number by then.
        A line is not, and passing one produces a class that converts cleanly
        and then dies on the first bar -- which is worse than reporting it.
        """
        if isinstance(node, Num):
            return repr(_literal(node))
        if isinstance(node, Name):
            if node.id in self.param_names:
                return f"self.p.{_safe(node.id)}"
            inner = self._computed.get(node.id)
            if inner is None or node.id in self._promoting:
                return None
            self._promoting.add(node.id)
            try:
                return self._period_expr(inner)
            finally:
                self._promoting.discard(node.id)
        if isinstance(node, Unary) and node.op == "-":
            operand = self._period_expr(node.operand)
            return None if operand is None else f"(-{operand})"
        if isinstance(node, Binary) and node.op in _LINE_OPS:
            left = self._period_expr(node.left)
            right = self._period_expr(node.right)
            if left is None or right is None:
                return None
            return f"({left} {_BINARY_OPS[node.op]} {right})"
        if isinstance(node, Ternary):
            # `len = mode == 'Fast' ? 5 : 20` is a length chosen once, not per
            # bar, so it resolves the same way the indicator it feeds does.
            # Both branches stay periods; only the condition may be a string
            # comparison, which is what `_const_expr` is for.
            cond = self._const_expr(node.cond)
            then = self._period_expr(node.then)
            other = self._period_expr(node.other)
            if cond is None or then is None or other is None:
                return None
            return f"({then} if {cond} else {other})"
        if isinstance(node, Call) and node.func in ("int", "math.round"):
            inner = self._period_expr(node.args[0]) if node.args else None
            return None if inner is None else f"int({inner})"
        if isinstance(node, Call) and node.func in ("math.max", "math.min"):
            parts = [self._period_expr(a) for a in node.args]
            if not parts or any(part is None for part in parts):
                return None
            name = "max" if node.func == "math.max" else "min"
            return f"{name}({', '.join(parts)})"
        return None

    def _expr_line(self, node):
        """Build a ``PineExpr`` for something the line operators cannot hold.

        Only the *leaves* become inputs -- a price series, an indicator, a
        promoted line. Everything above them stays in the function, which is
        what keeps a conditional lazy: hoisting `x / d` into an input would
        compute it on every bar again, defeating the guard around it.
        """
        inputs = []
        body = self._expr_source(node, inputs)
        if body is None or not inputs:
            return None
        self._uses_expr = True
        names = ", ".join(f"a{index}" for index in range(len(inputs)))
        return self._hoist(
            "expr",
            f"PineExpr({', '.join(inputs)}, func=lambda {names}: {body})",
        )

    def _expr_source(self, node, inputs):
        """Render ``node`` as Python over the current bar, or ``None``."""

        def slot(lowered):
            if lowered in inputs:
                return f"a{inputs.index(lowered)}"
            inputs.append(lowered)
            return f"a{len(inputs) - 1}"

        if isinstance(node, Num):
            return repr(_literal(node))
        if isinstance(node, Str):
            return repr(node.value)
        if isinstance(node, Bool):
            return "True" if node.value else "False"
        if isinstance(node, Na):
            return "float('nan')"

        if isinstance(node, Name):
            if node.id in self.param_names:
                # A param is fixed for the run, so reading it through the
                # closure raises no ordering question.
                return f"self.p.{_safe(node.id)}"
            lowered = self._line_expr(node)
            return None if lowered is None else slot(lowered)

        if isinstance(node, Index):
            lowered = self._line_expr(node)
            return None if lowered is None else slot(lowered)

        if isinstance(node, Unary):
            operand = self._expr_source(node.operand, inputs)
            if operand is None:
                return None
            return f"(not {operand})" if node.op == "not" else f"({node.op}{operand})"

        if isinstance(node, Binary):
            operator = _BINARY_OPS.get(node.op)
            if operator is None:
                return None
            left = self._expr_source(node.left, inputs)
            right = self._expr_source(node.right, inputs)
            if left is None or right is None:
                return None
            return f"({left} {operator} {right})"

        if isinstance(node, Ternary):
            cond = self._expr_source(node.cond, inputs)
            then = self._expr_source(node.then, inputs)
            other = self._expr_source(node.other, inputs)
            if cond is None or then is None or other is None:
                return None
            return f"({then} if {cond} else {other})"

        if isinstance(node, Call):
            if node.func in self.functions:
                inlined = self._inline_call(node)
                return None if inlined is None else self._expr_source(inlined, inputs)
            if node.func == "na":
                if len(node.args) != 1:
                    return None
                inner = self._expr_source(node.args[0], inputs)
                return None if inner is None else f"({inner} != {inner})"
            if node.func == "nz":
                inner = self._expr_source(node.args[0], inputs) if node.args else "0"
                fallback = (
                    self._expr_source(node.args[1], inputs)
                    if len(node.args) > 1
                    else "0"
                )
                if inner is None or fallback is None:
                    return None
                return f"({inner} if {inner} == {inner} else {fallback})"
            if node.func in _BUILTIN_MATH:
                parts = [self._expr_source(a, inputs) for a in node.args]
                if not parts or any(part is None for part in parts):
                    return None
                return f"{_BUILTIN_MATH[node.func]}({', '.join(parts)})"
            lowered = self._line_expr(node)
            return None if lowered is None else slot(lowered)

        return None

    def _promote(self, name):
        """Build a line for a value that was computed as a scalar in ``next()``.

        ``_ci = (hlc3 - _esa) / (0.015 * _d)`` followed by ``ta.sma(_ci, n)``
        is the commonest shape in the corpus, and the assignment on its own
        gives a number per bar rather than a line to hand an indicator. The
        expression is lowered a second time, as a line, and cached.

        Only an unconditional single assignment qualifies. One made inside an
        ``if`` holds a value on some bars and not others, and one that is
        reassigned with ``:=`` is a different value later -- a line built from
        either would be right only by accident.
        """
        if name in self._promoted:
            return self._promoted[name]
        node = self._computed.get(name)
        if node is None or name in self._promoting:
            return None
        self._promoting.add(name)
        try:
            lowered = self._line_expr(node)
        finally:
            self._promoting.discard(name)
        if lowered is None:
            return None
        handle = self._hoist(f"line_{_safe(name)}", lowered)
        self._promoted[name] = handle
        return handle

    def _shifted_read(self, name, ago):
        """Read a computed scalar ``ago`` bars back by re-lowering its definition.

        The fallback for history that :meth:`_promote` cannot build a line
        for. ``inSess = not na(time(timeframe.period, sess))`` has no line
        behind it -- ``time()`` is a per-bar read of the feed's clock -- but
        the definition holds on every bar, so ``inSess[1]`` is the same
        expression with every bar-relative read shifted one bar further back.
        Only the pure per-bar subset qualifies (:meth:`_value_at` says what
        that is); anything stateful has moved since and is refused.

        Rejections filed by a failed attempt are unwound: the caller's own
        message is the one that says what actually blocked the read.
        """
        node = self._computed.get(name)
        if node is None or name in self._promoting:
            return None
        mark = len(self.unsupported)
        self._promoting.add(name)
        try:
            lowered = self._value_at(node, ago)
        finally:
            self._promoting.discard(name)
        if lowered is None or len(self.unsupported) != mark:
            del self.unsupported[mark:]
            return None
        return lowered

    def _value_at(self, node, ago):
        """Lower an expression as it read ``ago`` bars back, or None.

        Covers the reads whose past is still answerable at the current bar:
        prices and lines, constants and params, ``time()``, ``na``/``nz``
        and the pure math builtins, and other computed scalars through
        :meth:`_name_at`. A ``var``, a trade counter, or a call with its
        own memory holds only its current value, so those answer None.
        """
        if isinstance(node, (Num, Str, Bool, Na)):
            return self._value_expr(node)
        if isinstance(node, Name):
            return self._name_at(node.id, ago)
        if isinstance(node, Index):
            offset = _literal(node.offset)
            if not isinstance(offset, int) or offset < 0:
                return None
            if isinstance(node.base, Call) and node.base.func == "time":
                return self._value_time(node.base, ago=ago + offset)
            if isinstance(node.base, Name):
                return self._name_at(node.base.id, ago + offset)
            return None
        if isinstance(node, Unary):
            operand = self._value_at(node.operand, ago)
            if operand is None:
                return None
            return f"(not {operand})" if node.op == "not" else f"({node.op}{operand})"
        if isinstance(node, Binary):
            op = _BINARY_OPS.get(node.op)
            left = self._value_at(node.left, ago)
            right = self._value_at(node.right, ago)
            if op is None or left is None or right is None:
                return None
            return f"({left} {op} {right})"
        if isinstance(node, Ternary):
            parts = [
                self._value_at(part, ago) for part in (node.cond, node.then, node.other)
            ]
            if any(part is None for part in parts):
                return None
            cond, then, other = parts
            return f"({then} if {cond} else {other})"
        if isinstance(node, Call):
            if node.func == "time":
                return self._value_time(node, ago=ago)
            if node.func == "na" and len(node.args) == 1:
                inner = self._value_at(node.args[0], ago)
                return None if inner is None else f"({inner} != {inner})"
            if node.func == "nz" and node.args:
                inner = self._value_at(node.args[0], ago)
                fallback = (
                    self._value_at(node.args[1], ago) if len(node.args) > 1 else "0"
                )
                if inner is None or fallback is None:
                    return None
                return f"({inner} if {inner} == {inner} else {fallback})"
            if node.func in _BUILTIN_MATH or node.func in _MODULE_MATH:
                parts = [self._value_at(arg, ago) for arg in node.args]
                if not parts or any(part is None for part in parts):
                    return None
                if node.func in _MODULE_MATH:
                    self._uses_math = True
                    return f"{_MODULE_MATH[node.func]}({', '.join(parts)})"
                return f"{_BUILTIN_MATH[node.func]}({', '.join(parts)})"
            # An indicator call is a line, and a line reads at any offset.
            lowered = self._line_expr(node)
            if lowered is not None:
                self._max_lookback = max(self._max_lookback, ago)
                return self._line_read(lowered, -ago)
            return None
        return None

    def _name_at(self, name, ago):
        """One name read ``ago`` bars back, where that is derivable."""
        if name in PRICE_SERIES or name in DERIVED_SERIES or name in self.series:
            return self._value_name(name, -ago)
        if name in self.param_names:
            # A param holds one value for the whole run, so its history is
            # its present.
            return f"self.p.{_safe(name)}"
        if name in self._computed and name not in self._promoting:
            self._promoting.add(name)
            try:
                return self._value_at(self._computed[name], ago)
            finally:
                self._promoting.discard(name)
        return None

    def _hoist_indicator(self, call):
        """Build a Backtrader indicator in ``__init__`` and return its handle."""
        spec = INDICATORS.get(call.func)
        if spec is None:
            return None

        args = list(call.args)
        source_code = None
        if spec.takes_source:
            if len(args) >= 2 or (len(args) == 1 and not spec.takes_period):
                source_code = self._line_expr(args.pop(0))
            else:
                source_code = f"{self._feed}.{PRICE_SERIES[spec.default_source]}"
            if source_code is None:
                self._reject(
                    f"{call.func}: source argument is not a plain series or parameter"
                )
                return None
        else:
            source_code = self._feed

        pieces = [source_code]
        if spec.takes_period:
            period = None
            if args:
                period = self._period_expr(args.pop(0))
            for key, value in call.kwargs:
                if key in ("length", "period"):
                    period = self._period_expr(value)
            if period is None:
                self._reject(f"{call.func}: could not resolve its length argument")
                return None
            pieces.append(f"period={period}")

        return self._hoist(
            spec.bt_name.lower(),
            f"bt.indicators.{spec.bt_name}({', '.join(pieces)})",
        )

    def _value_expr(self, node):
        """Lower an expression for ``next()``, where values are numbers."""
        if isinstance(node, Num):
            return repr(_literal(node))
        if isinstance(node, Str):
            return repr(node.value)
        if isinstance(node, Bool):
            return "True" if node.value else "False"
        if isinstance(node, Na):
            # Not reported: NaN is a faithful rendering of `na`, and it is
            # plain to read in the output. Since every `var` initialised to
            # `na` would otherwise file a note, reporting it is pure noise.
            return "float('nan')"

        if isinstance(node, Name):
            return self._value_name(node.id, 0)

        if isinstance(node, Index):
            offset = _literal(node.offset)
            if (
                isinstance(node.base, Call)
                and node.base.func == "time"
                and isinstance(offset, int)
            ):
                # time() is read off the feed's clock rather than hoisted into
                # a line, so its history is the same read at an offset.
                return self._value_time(node.base, ago=offset)
            if not isinstance(node.base, Name) and isinstance(offset, int):
                # `ta.ema(close, 20)[1]` is the previous bar of a line that has
                # to exist for the current bar to be readable at all, so the
                # base is built the way any indicator source is and then read
                # at an offset. Backtrader spells that the same way Pine does.
                lowered = self._line_expr(node.base)
                if lowered is not None:
                    self._max_lookback = max(self._max_lookback, offset)
                    return self._line_read(lowered, -offset)
            if not isinstance(offset, int):
                # `close[rsPeriod]` -- the offset is only known per bar, so the
                # bars it reaches back over cannot be counted at conversion
                # time and `_max_lookback` has nothing to widen. The read is
                # guarded where it happens instead.
                lowered = self._line_expr(node.base)
                if lowered is None:
                    self._reject(
                        "history at a computed offset needs a series to read "
                        "back over"
                    )
                    return "None"
                if "(" in lowered:
                    lowered = self._hoist("line", lowered)
                self._uses_back = True
                return f"self._pine_back({lowered}, {self._value_expr(node.offset)})"
            if not isinstance(node.base, Name):
                self._reject("history access is only supported as name[constant]")
                return "None"
            return self._value_name(node.base.id, -offset)

        if isinstance(node, Unary):
            operand = self._value_expr(node.operand)
            return f"(not {operand})" if node.op == "not" else f"({node.op}{operand})"

        if isinstance(node, Binary):
            op = _BINARY_OPS.get(node.op)
            if op is None:
                self._reject(f"operator {node.op!r} is not supported")
                return "None"
            return (
                f"({self._value_expr(node.left)} {op} {self._value_expr(node.right)})"
            )

        if isinstance(node, Ternary):
            # A choice between indicators settled at construction time is read
            # from the one line that choice built, rather than lowered again
            # here. Lowering it again would build every branch a second time,
            # through a path `_const_choice` does not govern -- and an
            # indicator that is merely built still counts toward the bar the
            # strategy starts on.
            chosen = self._const_choice(node)
            if chosen is not None:
                return self._line_read(chosen)
            return (
                f"({self._value_expr(node.then)} if {self._value_expr(node.cond)} "
                f"else {self._value_expr(node.other)})"
            )

        if isinstance(node, ListLit):
            return "[" + ", ".join(self._value_expr(i) for i in node.items) + "]"

        if isinstance(node, Call):
            return self._value_call(node)

        self._reject(f"expression of type {type(node).__name__} is not supported")
        return "None"

    def _value_name(self, name, index):
        if name in PRICE_SERIES or name in DERIVED_SERIES or name in self.series:
            # Only line reads wrap; a var, a param or a trade counter is an
            # attribute, and holds what it holds on every bar.
            self._max_lookback = max(self._max_lookback, -index)
        if name in PRICE_SERIES:
            return f"{self._feed}.{PRICE_SERIES[name]}[{index}]"
        if name in DERIVED_SERIES:
            return DERIVED_SERIES[name].format(d=self._feed, i=index)
        if name in self.series:
            return f"self.{self.series[name]}[{index}]"
        if name in self.state:
            if index:
                return self._state_history(name, index)
            return f"self.{self.state[name]}"
        # A computed local shadows a param of the same name. `width =
        # input.float(2.0, "Width") / 100` names the param from the title and
        # the local from the assignment, and they collide; the local is the one
        # Pine means. Resolving to the param instead is silently wrong rather
        # than loudly wrong, which is the worst way to be wrong.
        if name in self.scalars:
            if index:
                # The scalar holds this bar's number and nothing else, but the
                # expression behind it is known, so the same value exists as a
                # line the moment anything asks for its history.
                handle = self._promote(name)
                if handle is not None:
                    self._max_lookback = max(self._max_lookback, -index)
                    return f"{handle}[{index}]"
                shifted = self._shifted_read(name, -index)
                if shifted is not None:
                    return shifted
                self._reject(
                    f"{name}: history of a computed value needs a Backtrader line"
                )
            return _safe(name)
        if name in self.param_names:
            if index:
                self._reject(f"{name}: a parameter has no bar history")
            return f"self.p.{_safe(name)}"
        if name == "time":
            # `time` on its own is what `time()` answers with no resolution.
            self._uses_time = True
            return f"self._pine_time(None, {-index})" if index else "self._pine_time()"

        if name in TRADE_COUNTERS:
            current, slot = TRADE_COUNTERS[name]
            self._uses_trades = True
            if not index:
                return current
            if index == -1 and slot is not None:
                return f"self._pine_prev[{slot}]"
            self._reject(
                f"{name}[{-index}]: only the previous bar's value is kept, "
                "not a full history"
            )
            return current

        if name == "timeframe.period":
            if index:
                self._reject("timeframe.period: the chart's timeframe has no history")
                return "None"
            self._uses_time = True
            return "self._pine_timeframe()"

        if name in BUILTIN_VALUES:
            if index:
                self._reject(f"{name}: history of this builtin is not supported")
            if BUILTIN_VALUES[name].startswith("math."):
                self._uses_math = True
            return BUILTIN_VALUES[name]
        if name == "syminfo.mintick":
            if index:
                self._reject("syminfo.mintick: a tick size has no bar history")
            return self._mintick_param()
        if _presentational_constant(name):
            # `col = up ? color.green : color.red` only ever feeds a plot, and
            # plots are dropped. Refusing the colour would report the strategy
            # as unconvertible over something that cannot affect a trade.
            self._ignore(f"{name} dropped: presentational only")
            return "None"
        self._reject(f"unknown identifier {name!r}")
        return "None"

    def _hoist_moving_average(self, call):
        """Build the moving averages Backtrader has no exact equivalent for.

        Hull *looks* like it has one, and using it would be wrong: Backtrader
        truncates the final `sqrt(period)` where Pine rounds it, so the two
        disagree for 24 of the first 59 lengths -- silently, by a little. It
        is composed here instead, out of the weighted averages Pine says it is
        made of.
        """
        source, period = self._source_and_period(call)
        if source is None or period is None:
            return None

        if call.func == "ta.hma":
            half = self._hoist(
                "wma", f"bt.indicators.WMA({source}, period=({period}) // 2)"
            )
            full = self._hoist("wma", f"bt.indicators.WMA({source}, period={period})")
            return self._hoist(
                "hma",
                f"bt.indicators.WMA(2.0 * {half} - {full}, "
                f"period=round(({period}) ** 0.5))",
            )

        if call.func == "ta.vwma":
            volume = f"{self._feed}.volume"
            traded = self._hoist(
                "sma", f"bt.indicators.SMA({source} * {volume}, period={period})"
            )
            shares = self._hoist("sma", f"bt.indicators.SMA({volume}, period={period})")
            # A window with no volume at all divides by zero, where Pine
            # answers `na`, so the division is guarded rather than composed.
            self._uses_expr = True
            return self._hoist(
                "vwma",
                f"PineExpr({shares}, {traded}, func=lambda a0, a1: "
                "a1 / a0 if a0 else float('nan'))",
            )

        offset, sigma = "0.85", "6.0"
        extra = list(call.args[2:])
        named = dict(call.kwargs)
        if extra:
            offset = self._period_expr(extra[0]) or offset
        if len(extra) > 1:
            sigma = self._period_expr(extra[1]) or sigma
        for key, alias in (("offset", "offset"), ("sigma", "sigma")):
            if key in named:
                resolved = self._period_expr(named[key])
                if resolved is None:
                    self._reject(f"ta.alma: {key} must be a constant or a parameter")
                    return None
                if alias == "offset":
                    offset = resolved
                else:
                    sigma = resolved
        self._uses_alma = True
        self._uses_math = True
        return self._hoist(
            "alma",
            f"PineAlma({source}, period={period}, offset={offset}, sigma={sigma})",
        )

    def _source_and_period(self, call):
        """The ``(source, length)`` pair these all take, lowered."""
        args = list(call.args)
        named = dict(call.kwargs)
        source_node = args.pop(0) if args else named.get("source")
        period_node = args.pop(0) if args else named.get("length", named.get("period"))
        if source_node is None or period_node is None:
            self._reject(f"{call.func} expects a source and a length")
            return None, None
        source = self._line_expr(source_node)
        if source is None:
            self._reject(
                f"{call.func}: source argument is not a plain series or parameter"
            )
            return None, None
        period = self._period_expr(period_node)
        if period is None:
            self._reject(f"{call.func}: could not resolve its length argument")
            return None, None
        return source, period

    def _hoist_pivot(self, call):
        """Build a ``PinePivot`` in ``__init__`` and return its handle.

        Pine takes ``(source, left, right)`` or, in the short form, just
        ``(left, right)`` over ``high`` for a pivot high and ``low`` for a
        pivot low.
        """
        is_high = call.func == "ta.pivothigh"

        # Keywords first, so the positionals that remain can be matched
        # against the slots they actually fill. `ta.pivothigh(high,
        # rightbars=2)` leaves one positional, and it is `leftbars`.
        nodes = {}
        for key, node in call.kwargs:
            if key in ("leftbars", "left"):
                nodes["left"] = node
            elif key in ("rightbars", "right"):
                nodes["right"] = node
            elif key == "source":
                nodes["source"] = node
        positional = list(call.args)
        missing = [slot for slot in ("left", "right") if slot not in nodes]
        if len(positional) == len(missing) + 1 and "source" not in nodes:
            nodes["source"] = positional.pop(0)
        if len(positional) != len(missing):
            self._reject(f"{call.func} expects (source, left, right) or (left, right)")
            return None
        nodes.update(zip(missing, positional))

        if "source" in nodes:
            source = self._line_expr(nodes["source"])
            if source is None:
                self._reject(
                    f"{call.func}: source argument is not a plain series or parameter"
                )
                return None
        else:
            # Pine's short form measures the high for a pivot high and the low
            # for a pivot low, not the close for both.
            source = f"{self._feed}.{'high' if is_high else 'low'}"

        bars = {slot: self._period_expr(nodes[slot]) for slot in ("left", "right")}
        if bars.get("left") is None or bars.get("right") is None:
            # Same boundary as an indicator's period: a Backtrader indicator
            # fixes its window when it is constructed.
            self._reject(f"{call.func}: could not resolve its left/right bar counts")
            return None

        self._uses_pivot = True
        return self._hoist(
            "pivot",
            f"PinePivot({source}, left={bars['left']}, right={bars['right']}, "
            f"high={is_high})",
        )

    def _value_trade_field(self, call):
        """Lower ``strategy.closedtrades.entry_price(i)`` and its siblings."""
        ledger, _, field = call.func.rpartition(".")
        if field in TRADE_FIELDS_UNTRACKED:
            self._reject(f"{call.func}(): {TRADE_FIELDS_UNTRACKED[field]}")
            return "None"
        if field not in TRADE_FIELDS:
            self._reject(f"call to {call.func}() is not supported")
            return "None"
        if len(call.args) != 1:
            self._reject(f"{call.func}() expects one argument, the trade index")
            return "None"
        self._uses_trades = True
        store = (
            "self._pine_closed"
            if ledger == "strategy.closedtrades"
            else "self._pine_open"
        )
        index = self._value_expr(call.args[0])
        return f"self._pine_trade({store}, {index}, {TRADE_FIELDS[field]!r})"

    def _state_history(self, name, index):
        """Read ``x[n]`` where ``x`` is a ``var``.

        A ``var`` becomes one attribute, so the only history it can answer is
        the previous bar -- and only until this bar overwrites it. Inside a
        function body the updates are emitted in source order, so an attribute
        read before its own assignment still holds the previous bar's value,
        which is exactly what ``nz(e0[1], src)`` is asking for. Read after the
        assignment the same attribute holds *this* bar's value, so that one is
        refused rather than quietly answered with the wrong number.
        """
        if index != -1 or name not in self._var_history:
            written = self._var_history.get(name, name)
            self._reject(f"{written}: a var holds one value, not a series with history")
            return f"self.{self.state[name]}"
        if name in self._var_written:
            self._reject(
                f"{self._var_history[name]}[1]: read after this bar's assignment, "
                "where a var no longer holds the previous bar's value"
            )
        return f"self.{self.state[name]}"

    def _value_call(self, call):
        if call.func in self.functions:
            inlined = self._inline_call(call)
            return "None" if inlined is None else self._value_expr(inlined)

        if call.func in INPUT_FUNCS:
            # `stop = input.float(5.0, "Stop %") / 100` -- an input does not
            # have to be the whole right-hand side to be a tunable param.
            return self._register_input(call)

        if call.func in CROSSES:
            if len(call.args) != 2:
                self._reject(f"{call.func} expects two arguments")
                return "None"
            left = self._line_expr(call.args[0])
            right = self._line_expr(call.args[1])
            if left is None or right is None:
                self._reject(f"{call.func}: arguments must be series or parameters")
                return "None"
            handle = self._hoist("cross", f"bt.indicators.CrossOver({left}, {right})")
            return f"({handle}[0] {CROSSES[call.func]})"

        if call.func in INDICATORS:
            handle = self._hoist_indicator(call)
            return f"{handle}[0]" if handle else "None"

        if call.func == "ta.change":
            source = call.args[0] if call.args else Name("close")
            length = _literal(call.args[1]) if len(call.args) > 1 else 1
            if not isinstance(length, int):
                self._reject("ta.change: length must be a constant")
                return "None"
            has_history = isinstance(source, Name) or (
                isinstance(source, Call) and source.func == "time"
            )
            if not has_history:
                # Re-evaluating the source would subtract a value from itself
                # and report no change on any bar. Saying so beats emitting a
                # condition that silently never fires.
                self._reject(
                    "ta.change: the source needs bar history, so it has to be a "
                    "name or a builtin that carries one"
                )
                return "None"
            now = self._value_expr(source)
            before = self._value_expr(Index(base=source, offset=Num(float(length))))
            return f"({now} - {before})"

        if call.func in PIVOTS:
            handle = self._hoist_pivot(call)
            return f"{handle}[0]" if handle else "None"

        if call.func in COMPOSED_AVERAGES:
            handle = self._hoist_moving_average(call)
            return f"{handle}[0]" if handle else "None"

        if call.func.startswith(("strategy.closedtrades.", "strategy.opentrades.")):
            return self._value_trade_field(call)

        if call.func in _BUILTIN_MATH:
            inner = ", ".join(self._value_expr(a) for a in call.args)
            return f"{_BUILTIN_MATH[call.func]}({inner})"

        if call.func in _MODULE_MATH:
            self._uses_math = True
            inner = ", ".join(self._value_expr(a) for a in call.args)
            return f"{_MODULE_MATH[call.func]}({inner})"

        if call.func == "math.pow":
            if len(call.args) != 2:
                self._reject("math.pow expects two arguments")
                return "None"
            base, exponent = (self._value_expr(a) for a in call.args)
            return f"({base} ** {exponent})"

        if call.func == "math.sign":
            # Python has no sign(). Pine's returns -1, 0 or 1, and the
            # difference of two comparisons is exactly that.
            if len(call.args) != 1:
                self._reject("math.sign expects one argument")
                return "None"
            inner = self._value_expr(call.args[0])
            return f"(({inner} > 0) - ({inner} < 0))"

        if call.func == "math.avg":
            if not call.args:
                self._reject("math.avg expects at least one argument")
                return "None"
            parts = [self._value_expr(a) for a in call.args]
            return f"(({' + '.join(parts)}) / {len(parts)})"

        if call.func == "timeframe.in_seconds":
            lowered = self._tf_seconds(call)
            if lowered is None:
                self._reject(
                    "timeframe.in_seconds: the timeframe must be the chart's "
                    "own, a literal string, or an input, because a feed is "
                    "not built from a value that only exists per bar"
                )
                return "None"
            return lowered

        if call.func == "time":
            return self._value_time(call)

        if call.func == "timestamp":
            folded = self._fold_timestamp(call)
            if folded is None:
                self._reject(
                    "timestamp: only a literal date string is understood, "
                    "because the value has to be known before the run"
                )
                return "None"
            return repr(folded)

        if call.func == "request.security":
            return self._value_security(call)

        if call.func == "na":
            # `x != x` is the NaN test, matching how nz() below already spells
            # it. Pairs with `var float x = na`, which is how most strategies
            # mark "no position yet".
            if len(call.args) != 1:
                self._reject("na() expects one argument")
                return "None"
            inner = self._value_expr(call.args[0])
            return f"({inner} != {inner})"

        if call.func == "nz":
            inner = self._value_expr(call.args[0]) if call.args else "0"
            fallback = self._value_expr(call.args[1]) if len(call.args) > 1 else "0"
            return f"({inner} if {inner} == {inner} else {fallback})"

        self._reject(f"call to {call.func}() is not supported")
        return "None"

    # --- statements ----------------------------------------------------------

    def _emit_exit(self, expr, pad):
        """Lower ``strategy.exit``, including its stop and limit levels."""
        offsets = {key for key, _ in expr.kwargs} & {
            "loss",
            "profit",
            "trail_price",
            "trail_points",
            "trail_offset",
        }
        if offsets:
            # These are distances rather than prices, measured in Pine's ticks,
            # which depend on the instrument's tick size rather than on
            # anything visible in the script.
            self._reject(
                "strategy.exit with "
                + ", ".join(sorted(offsets))
                + ": these are distances in ticks, and the tick size is a "
                "property of the instrument rather than of the script"
            )
            return

        levels = {key: value for key, value in expr.kwargs if key in ("stop", "limit")}
        if not levels:
            self.next_lines.append(f"{pad}self.close()")
            return

        tag = _literal(expr.args[0]) if expr.args else "exit"
        from_entry = _literal(expr.args[1]) if len(expr.args) > 1 else None
        for key, value in expr.kwargs:
            if key == "from_entry":
                from_entry = _literal(value)
        self._uses_exit = True
        arguments = [repr(str(tag))]
        if from_entry is not None:
            # Names the entry whose pending bracket these levels ride on.
            arguments.append(f"from_entry={str(from_entry)!r}")
        for key in ("stop", "limit"):
            if key in levels:
                arguments.append(f"{key}={self._value_expr(levels[key])}")
        self.next_lines.append(f"{pad}self._pine_exit({', '.join(arguments)})")

    def _value_time(self, call, ago=0):
        """Lower Pine's ``time()`` to a floored stamp read off the primary feed.

        ``time()`` is this bar's opening time and ``time(res)`` is the opening
        time of the ``res`` bar containing it. ``ago`` reads a bar back, which
        is what ``time(res)[1]`` and ``ta.change(time(res))`` need.

        A session argument becomes a per-bar check against the feed's own
        clock -- the generated ``_pine_in_session`` states what that assumes.
        A timezone argument shifts that clock out of UTC into the named zone
        first, except ``syminfo.timezone``, which is dropped: the exchange's
        own zone is exactly what the bare check already assumes of the feed.
        """
        if len(call.args) > 3:
            self._reject("time() takes a resolution, a session and a timezone")
            return "None"
        self._uses_time = True
        session = ""
        if len(call.args) >= 2:
            self._uses_session = True
            session = f", session={self._value_expr(call.args[1])}"
        if len(call.args) == 3:
            zone = call.args[2]
            if not (isinstance(zone, Name) and zone.id == "syminfo.timezone"):
                session += f", tz={self._value_expr(zone)}"
        if not call.args:
            return f"self._pine_time(None, {ago})" if ago else "self._pine_time()"
        resolution = call.args[0]
        if isinstance(resolution, Name) and resolution.id == "timeframe.period":
            # The chart's own timeframe: the bar's stamp needs no flooring.
            seconds = None
        else:
            text = _literal(resolution)
            if not isinstance(text, str):
                self._reject("time(): the resolution must be a literal string")
                return "None"
            seconds = timeframe_seconds(text)
            if seconds is None:
                self._reject(
                    f"time(): resolution {text!r} does not floor to a fixed "
                    "number of seconds"
                )
                return "None"
        if session:
            return f"self._pine_time({seconds}, {ago}{session})"
        if ago:
            return f"self._pine_time({seconds}, {ago})"
        if seconds is None:
            return "self._pine_time()"
        return f"self._pine_time({seconds})"

    def _security_feed(self, call):
        """The Backtrader feed a ``request.security`` reads from, or ``None``.

        Pine reaches another timeframe inline; Backtrader reaches it through a
        second data feed set up on the cerebro before the strategy exists. So
        the call becomes a read from ``self.datas[n]``, and the feeds the
        caller has to supply are recorded on the class.

        Split out from the lowering because the same call has to be answerable
        both ways: as a number for ``next()``, and as a line for anything that
        wants to build an indicator on top of it.
        """
        if len(call.args) < 3:
            self._reject("request.security needs a symbol, timeframe and expression")
            return None

        symbol, timeframe, expression = call.args[0], call.args[1], call.args[2]

        if isinstance(symbol, Name) and symbol.id == "syminfo.tickerid":
            ticker = None  # the chart's own instrument
        else:
            ticker, from_input = self._constant_text(symbol)
            if ticker is None:
                self._reject(
                    "request.security: the symbol must be a literal string or "
                    "an input default, because the feed is loaded before the "
                    "strategy runs"
                )
                return None
            if from_input:
                # Same reasoning as a timeframe: the feed is chosen before the
                # strategy is instantiated, so the param cannot move it.
                self._ignore(
                    f"{from_input}: symbol fixed to {ticker!r} at conversion "
                    "time; change feed_spec, not the param"
                )

        # Pine takes `lookahead` fourth-and-fifth positionally as well as by
        # name, and only the keyword form was being checked. A script written
        # `request.security(sym, "240", x, barmerge.gaps_off,
        # barmerge.lookahead_on)` converted clean and read the higher-timeframe
        # bar before it closed -- a backtest that cannot lose, on data the
        # strategy could not have had. Any `lookahead_on` in the call is the
        # answer, wherever it sits.
        settings = [value for _, value in call.kwargs] + list(call.args[3:])
        for value in settings:
            if isinstance(value, Name) and value.id.endswith("lookahead_on"):
                # Backtrader has no equivalent, and inventing one would mean
                # feeding the strategy data it could not have had.
                self._reject(
                    "request.security with barmerge.lookahead_on reads a bar "
                    "before it closes; there is no Backtrader equivalent"
                )
                return None

        own_timeframe = (
            isinstance(timeframe, Name) and timeframe.id == "timeframe.period"
        )
        if own_timeframe and ticker is None:
            # The chart's own instrument at its own timeframe. Pine still
            # routes it through request.security; Backtrader needs no second
            # feed for it.
            feed = self._feed
        else:
            resolved, from_param = (
                (None, None) if own_timeframe else self._constant_text(timeframe)
            )
            if resolved is None and not own_timeframe:
                self._reject(
                    "request.security: the timeframe must be a literal string or "
                    "an input default, because the feed is built before the "
                    "strategy runs"
                )
                return None
            if own_timeframe:
                # A second instrument on the chart's own timeframe: a feed to
                # add, with nothing to resample.
                spec = (None, None)
            else:
                spec = parse_timeframe(resolved)
                if spec is None:
                    self._reject(
                        f"request.security: timeframe {resolved!r} is not recognised"
                    )
                    return None
                if from_param:
                    self._ignore(
                        f"{from_param}: timeframe fixed to {resolved!r} at "
                        "conversion time; change feed_spec, not the param"
                    )
            key = (ticker, resolved)
            index = self._feed_index.get(key)
            if index is None:
                self.feeds.append((ticker,) + spec)
                index = len(self.feeds)  # datas[0] is the chart itself
                self._feed_index[key] = index
            feed = f"self.datas[{index}]"

        return feed

    def _line_security(self, call):
        """``request.security`` as a line, for building indicators on."""
        feed = self._security_feed(call)
        if feed is None:
            return None
        previous, self._feed = self._feed, feed
        try:
            return self._line_expr(call.args[2])
        finally:
            self._feed = previous

    def _value_security(self, call):
        """``request.security`` as this bar's number."""
        feed = self._security_feed(call)
        if feed is None:
            return "None"
        previous, self._feed = self._feed, feed
        try:
            line = self._line_expr(call.args[2])
            if line is not None:
                return self._line_read(line)
            return self._value_expr(call.args[2])
        finally:
            self._feed = previous

    def _constant_text(self, node):
        """Recover a string, and the param it came from if it came from one.

        Neither half of a feed can stay tunable. `cerebro.resampledata` and
        `adddata` both run before the strategy is instantiated, so a timeframe
        or a symbol changed at `addstrategy` time would not move the feed
        underneath it. Resolving the input's default and baking it in is the
        honest reading; the param stays, because the script may read it
        elsewhere, and the conversion says it is no longer wired to the feed.
        """
        literal = _literal(node)
        if isinstance(literal, str):
            return literal, None
        if isinstance(node, Name):
            for name, default in self.params:
                if name == node.id and isinstance(default, str):
                    return default, name
        return None, None

    #: The timeframe half of the same question, kept under its old name.
    _timeframe_text = _constant_text

    def _tf_seconds(self, call):
        """``timeframe.in_seconds(...)`` as an expression, or ``None``.

        Unlike a feed's timeframe this one stays live: the helper takes the
        text, so an input the caller overrides is still read. Only the bare
        form has to be, since it asks the feed rather than a string.

        Answered from `__init__` as readily as from `next()`, which is the
        whole point of it -- the commonest use in the corpus picks a moving
        average by the chart's timeframe, and that choice has to be made
        when the indicator is built.
        """
        self._uses_time = True
        args = list(call.args)
        node = args[0] if args else None
        if node is None or (isinstance(node, Name) and node.id == "timeframe.period"):
            return "self._pine_tf_seconds()"
        text, param = self._constant_text(node)
        if param is not None:
            return f"self._pine_tf_seconds(self.p.{_safe(param)})"
        if text is not None:
            return f"self._pine_tf_seconds({text!r})"
        return None

    def _fold_timestamp(self, call):
        """``timestamp("01 Jan 2020 00:00 +0000")`` as epoch milliseconds."""
        if not isinstance(call, Call) or call.func != "timestamp":
            return None
        text = _literal(call.args[0]) if call.args else None
        return parse_timestamp(text)

    def _input_default(self, call):
        for arg in call.args:
            folded = self._fold_timestamp(arg)
            if folded is not None:
                return folded
        default = None
        for arg in call.args:
            literal = _literal(arg)
            if literal is not None and not isinstance(literal, str):
                default = literal
                break
        if default is None and call.args:
            default = _literal(call.args[0])
        for key, value in call.kwargs:
            if key == "defval":
                default = _literal(value)
        if call.func == "input.bool" and isinstance(default, (int, float)):
            default = bool(default)
        return default

    def _input_title(self, call):
        """The input's display title, used to name a param that has no variable."""
        for key, value in call.kwargs:
            if key == "title":
                return _literal(value)
        for arg in call.args[1:]:
            literal = _literal(arg)
            if isinstance(literal, str):
                return literal
        return None

    def _register_input(self, call, prefer=None):
        """Turn an ``input.*`` call into a Backtrader param and return its handle.

        Called both for `n = input.int(14)`, where the variable names the param,
        and for an input buried in an expression such as
        `stop = input.float(5.0, "Stop %") / 100`, where it does not and the
        title has to supply the name instead.
        """
        default = self._input_default(call)
        key = (call.func, repr(default), self._input_title(call))
        if prefer is None and key in self._inputs:
            return f"self.p.{_safe(self._inputs[key])}"

        # `params` and `param_names` hold the Pine name; `_safe` is applied
        # where it is written out, so applying it here too would double it.
        name = prefer or _slug(self._input_title(call)) or "param"
        if prefer is None:
            candidate, suffix = name, 2
            while candidate in self.param_names:
                candidate, suffix = f"{name}_{suffix}", suffix + 1
            name = candidate

        self.params.append((name, default))
        self.param_names.add(name)
        self._inputs[key] = name
        return f"self.p.{_safe(name)}"

    def _collect_input(self, statement):
        self._register_input(statement.value, prefer=statement.target)

    def _emit_statement(self, statement, indent):
        """Emit one statement, with any inlined state updates in front of it.

        A function that keeps ``var`` state produces lines as well as a value,
        and they have to run before the statement that asked for the value.
        They are collected while the statement is lowered and spliced in at
        the point it started.
        """
        outer, self._prelude = self._prelude, []
        mark = len(self.next_lines)
        try:
            self._emit_one(statement, indent)
        finally:
            prelude, self._prelude = self._prelude, outer
        if not prelude:
            return
        if indent and any(per_bar for per_bar, _ in prelude):
            # Pine updates a function's state on every bar wherever the call
            # sits. Emitting these under an `if` would update it only on the
            # bars the condition held, which is a different strategy.
            #
            # A named intermediate carries no such requirement: it is a value,
            # not a state machine, and computing it only on the bars that read
            # it is the same strategy.
            self._reject(
                "a function that keeps state across bars can only be called "
                "from a top-level statement, since Pine updates it every bar"
            )
            return
        pad = "    " * indent
        self.next_lines[mark:mark] = [f"{pad}{line}" for _, line in prelude]

    def _emit_one(self, statement, indent):
        pad = "    " * indent

        if isinstance(statement, Unsupported):
            self._reject(f"{statement.kind} block is not supported")
            return

        if isinstance(statement, TupleAssign):
            self._reject(
                "tuple destructuring (e.g. [macd, signal, hist] = ta.macd(...)) "
                "is not supported"
            )
            return

        if isinstance(statement, Assign):
            self._emit_assign(statement, indent, pad)
            return

        if isinstance(statement, If):
            cond = self._value_expr(statement.cond)
            self.next_lines.append(f"{pad}if {cond}:")
            body = list(statement.body)
            emitted = len(self.next_lines)
            for inner in body:
                self._emit_statement(inner, indent + 1)
            if len(self.next_lines) == emitted:
                self.next_lines.append(f"{pad}    pass")
            if statement.orelse:
                self.next_lines.append(f"{pad}else:")
                emitted = len(self.next_lines)
                for inner in statement.orelse:
                    self._emit_statement(inner, indent + 1)
                if len(self.next_lines) == emitted:
                    self.next_lines.append(f"{pad}    pass")
            return

        if isinstance(statement, ExprStmt):
            self._emit_expr_statement(statement.value, pad)
            return

        self._reject(f"statement of type {type(statement).__name__} is not supported")

    def _emit_assign(self, statement, indent, pad):
        if statement.qualifier == "varip":
            self._note_varip()
            self._declare_state(statement)
            return

        if statement.qualifier == "var":
            self._declare_state(statement)
            return

        # Resolve a user-defined call before the checks below, so a function
        # that just wraps an indicator -- `smooth(src, n) => ta.sma(src, n)` --
        # still becomes a line object rather than a scalar read of one.
        if isinstance(statement.value, Call) and statement.value.func in self.functions:
            inlined = self._inline_call(statement.value)
            if inlined is None:
                return
            statement = Assign(
                target=statement.target,
                value=inlined,
                qualifier=statement.qualifier,
            )

        if isinstance(statement.value, Call) and statement.value.func in INPUT_FUNCS:
            if indent:
                self._reject(
                    f"{statement.target}: inputs must be declared at top level"
                )
                return
            self._collect_input(statement)
            return

        # A bare indicator call becomes a line object built once in __init__.
        if isinstance(statement.value, Call) and statement.value.func in INDICATORS:
            handle = self._hoist_indicator(statement.value)
            if handle is not None:
                self.series[statement.target] = handle[len("self.") :]
            return

        if statement.qualifier == ":=" and statement.target in self.state:
            # Writing to persistent state. Unlike a local, this has to survive
            # the bar, so it assigns the attribute rather than a name in next().
            value = self._value_expr(statement.value)
            attribute = self.state[statement.target]
            self.next_lines.append(f"{pad}self.{attribute} = {value}")
            return

        if statement.qualifier == ":=" and statement.target not in self.scalars:
            self._reject(
                f"{statement.target}: reassignment of a value that was not "
                "defined in this scope"
            )
            return

        value = self._value_expr(statement.value)
        self.scalars.add(statement.target)
        if statement.target in self._promotable:
            # Kept so `ta.sma(thisName, n)` later on can build the same
            # expression as a line; see `_promote`.
            self._computed[statement.target] = statement.value
        self.next_lines.append(f"{pad}{_safe(statement.target)} = {value}")

    def _declare_state(self, statement):
        """Turn `var x = 0` into an attribute assigned once in ``__init__``.

        Pine initialises a ``var`` on the first bar and keeps it from then on,
        which is exactly what an instance attribute does. Only a literal
        initial value is accepted: ``var float x = close`` means the first
        bar's close, and ``__init__`` runs before there is a first bar.
        """
        initial = self._state_initial(statement.value)
        if initial is None:
            if isinstance(statement.value, Call):
                # Lower it purely for the reason it reports. "array.new_float()
                # is not supported" tells the caller what to do about
                # `var array<float> buf = array.new_float(n)`; "the initial
                # value must be a literal" sends them after the wrong thing.
                self._value_expr(statement.value)
                return
            self._reject(
                f"var {statement.target}: only a literal initial value is "
                "supported, because __init__ runs before the first bar"
            )
            return
        attribute = _safe(statement.target)
        self.state[statement.target] = attribute
        self.init_lines.append(f"self.{attribute} = {initial}")

    def _state_initial(self, node):
        """Render a ``var`` initialiser, or None when it is not a literal."""
        if isinstance(node, Na):
            return "float('nan')"
        if isinstance(node, Unary) and node.op == "-":
            inner = _literal(node.operand)
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return repr(-inner)
            return None
        literal = _literal(node)
        return None if literal is None else repr(literal)

    def _emit_expr_statement(self, expr, pad):
        if not isinstance(expr, Call):
            self._reject("a bare expression statement has no Backtrader equivalent")
            return

        if expr.func in PRESENTATIONAL:
            self._ignore(f"{expr.func}() dropped: presentational only")
            return

        if expr.func == "strategy.entry":
            self._emit_entry(expr, pad)
            return

        if expr.func in ("strategy.close", "strategy.close_all"):
            self.next_lines.append(f"{pad}self.close()")
            return

        if expr.func == "strategy.exit":
            self._emit_exit(expr, pad)
            return

        if expr.func == "strategy.cancel":
            tag = expr.args[0] if expr.args else None
            for key, value in expr.kwargs:
                if key == "id":
                    tag = value
            literal = _literal(tag) if tag is not None else None
            if literal is None:
                self._reject(
                    "strategy.cancel: the id must be a literal string, which "
                    "is what names the pending order it withdraws"
                )
                return
            self._uses_pending = True
            self.next_lines.append(f"{pad}self._pine_cancel({str(literal)!r})")
            return

        if expr.func in RISK_RULES:
            self._emit_risk(expr, pad)
            return

        if expr.func == "strategy.cancel_all":
            self._uses_pending = True
            self.next_lines.append(f"{pad}self._pine_cancel_all()")
            return

        self._reject(f"call to {expr.func}() is not supported")

    def _emit_risk(self, call, pad):
        """Lower one of Pine's account-level risk rules.

        The limit is an ordinary per-bar expression -- scripts routinely write
        `useMaxDD ? maxDD : 100`, which turns the rule off by making it
        unreachable -- so it is evaluated where the call sits rather than
        folded to a constant.
        """
        if not call.args:
            self._reject(f"{call.func} needs a limit")
            return
        limit = self._value_expr(call.args[0])
        percent = True
        for value in list(call.args[1:]) + [v for _, v in call.kwargs]:
            if isinstance(value, Name) and value.id == "strategy.cash":
                percent = False
        intraday = call.func == "strategy.risk.max_intraday_loss"
        self._uses_risk = True
        self.next_lines.append(f"{pad}self._pine_risk({limit}, {percent}, {intraday})")

    def _emit_entry(self, call, pad):
        direction = None
        for arg in call.args:
            if isinstance(arg, Name) and arg.id in ("strategy.long", "strategy.short"):
                direction = arg.id
        for key, value in call.kwargs:
            if key == "direction" and isinstance(value, Name):
                direction = value.id
        if direction is None:
            self._reject("strategy.entry: could not determine long or short")
            return

        size = None
        prices = []
        for key, value in call.kwargs:
            if key in ("qty", "size"):
                size = self._value_expr(value)
            if key in ("limit", "stop"):
                prices.append((key, self._value_expr(value)))
        long = "True" if direction == "strategy.long" else "False"
        arguments = f"{long}, size={size}" if size else long
        if prices:
            # A priced entry is a standing order, so it needs the id Pine
            # gave it: that is what re-issuing moves and strategy.cancel
            # withdraws.
            tag = _literal(call.args[0]) if call.args else "entry"
            arguments += f", tag={str(tag)!r}"
            for key, value in prices:
                arguments += f", {key}={value}"
            self._uses_pending = True
        self._uses_entry = True
        self.next_lines.append(f"{pad}self._pine_entry({arguments})")

    # --- assembly ------------------------------------------------------------

    def _promotable_names(self):
        """Names whose one top-level assignment can stand for them everywhere.

        A name assigned twice, or reassigned with ``:=``, or assigned inside a
        block, means different things on different bars. A single line object
        cannot say that, so those are left as the per-bar scalars they are.
        """
        assigned = collections.Counter()
        barred = set()

        def scan(statements, nested):
            for statement in statements:
                if isinstance(statement, If):
                    # Anything written under a condition is conditional.
                    scan(statement.body, True)
                    scan(statement.orelse, True)
                    continue
                if isinstance(statement, TupleAssign):
                    barred.update(statement.targets)
                    continue
                if not isinstance(statement, Assign):
                    continue
                if nested or statement.qualifier:
                    barred.add(statement.target)
                else:
                    assigned[statement.target] += 1

        scan(self.program.body, False)
        return {
            name
            for name, count in assigned.items()
            if count == 1 and name not in barred
        }

    def _check_declaration(self):
        """Report declaration settings the translation cannot honour.

        Backtrader nets a position per feed: a second `strategy.entry` adds to
        the one position rather than opening a second trade beside it. Pine
        does the same at `pyramiding=0`, its default, and that is what every
        entry and every trade counter here assumes. Above zero the two models
        genuinely differ, and `strategy.opentrades` is where it would show.
        """
        call = self.program.declaration_call
        if call is None:
            return
        for key, value in call.kwargs:
            if key != "pyramiding":
                continue
            literal = _literal(value)
            if literal == 0:
                return
            self._reject(
                "pyramiding: Backtrader nets one position per feed, so a "
                "second entry adds to the first rather than opening a "
                "trade beside it"
            )

    def generate(self):
        if self.declaration is None:
            self._reject(
                "no strategy() or indicator() declaration found; "
                "this does not look like a complete script"
            )
        elif self.declaration[0] == "indicator":
            self._ignore(
                "script declares indicator(), not strategy(); "
                "the generated class computes its lines but places no orders"
            )
        self._check_declaration()

        for statement in self.program.body:
            self._emit_statement(statement, indent=0)

        return self._render()

    def _render(self):
        out = ["import backtrader as bt"]
        if self._uses_time:
            out.append("import calendar")
        if self._uses_session:
            # _pine_in_session's timezone branch: datetime names UTC and
            # zoneinfo names the zone the session is checked in.
            out.append("import datetime")
        if self._uses_math:
            out.append("import math")
        if self._uses_session:
            out.append("import zoneinfo")
        out += ["", ""]
        if self._uses_pivot:
            out.extend(_PIVOT_INDICATOR)
        if self._uses_alma:
            out.extend(_ALMA_INDICATOR)
        if self._uses_expr:
            out.extend(_EXPR_INDICATOR)
        out.append(f"class {self.class_name}(bt.Strategy):")

        title = self.declaration[1] if self.declaration else ""
        out.append('    """Converted from PineScript by pwb_toolbox.converting.')
        if title:
            out.append("")
            out.append(f"    Original title: {title}")
        if self.unsupported:
            out.append("")
            out.append("    Not translated -- these still need writing by hand:")
            for item in self.unsupported:
                out.append(f"      - {item}")
        if self.ignored:
            out.append("")
            out.append("    Dropped as presentational:")
            for item in self.ignored:
                out.append(f"      - {item}")
        if self.notes:
            out.append("")
            out.append("    Read differently from the Pine source, on purpose:")
            for item in self.notes:
                out.append(f"      - {item}")
        out.append('    """')
        out.append("")

        if self.params:
            out.append("    params = (")
            for name, default in self.params:
                out.append(f"        ({_safe(name)!r}, {default!r}),")
            out.append("    )")
            out.append("")

        if self.feeds:
            out.append("    #: Feeds this strategy needs beyond the chart itself,")
            out.append("    #: in the order they become self.datas[1:], as")
            out.append("    #: (symbol, timeframe, compression). A symbol of None")
            out.append("    #: means the chart's own instrument, so the entry is a")
            out.append("    #: cerebro.resampledata(data, timeframe=...,")
            out.append("    #: compression=...); a named symbol means that")
            out.append("    #: instrument's own data, added with cerebro.adddata()")
            out.append("    #: and resampled too when a timeframe is given. All of")
            out.append("    #: them before cerebro.addstrategy().")
            out.append("    feed_spec = (")
            for ticker, timeframe, compression in self.feeds:
                symbol = "None" if ticker is None else repr(ticker)
                if timeframe is None:
                    out.append(f"        ({symbol}, None, None),")
                else:
                    out.append(f"        ({symbol}, {timeframe}, {compression}),")
            out.append("    )")
            out.append("")

        out.append("    def __init__(self):")
        if self._uses_exit or self._uses_pending:
            out.append("        self._pine_exits = {}")
            out.append("        self._pine_pending = {}")
            out.append("        self._pine_working = {}")
        if self._uses_session:
            out.append("        self._pine_sessions = {}")
        if self._uses_risk:
            out.append("        self._pine_peak = float('-inf')")
            out.append("        self._pine_day = None")
            out.append("        self._pine_day_open = None")
            out.append("        self._pine_halted = False")
            out.append("        self._pine_day_halted = False")
        if self._uses_trades:
            out.append("        self._pine_closed = []")
            out.append("        self._pine_open = []")
            out.append("        self._pine_wins = 0")
            out.append("        self._pine_losses = 0")
            out.append("        self._pine_fill = None")
            out.append("        self._pine_prev = (0, 0, 0, 0)")
        if self.feeds:
            # Without this the first read of self.datas[1] raises IndexError
            # somewhere in next(), which says nothing about what is missing.
            needed = len(self.feeds) + 1
            out.append(f"        if len(self.datas) < {needed}:")
            out.append(
                f'            raise ValueError("{self.class_name} needs '
                f'{needed} data feeds; see feed_spec")'
            )
        if self.init_lines:
            out.extend(f"        {line}" for line in self.init_lines)
        elif not (
            self._uses_exit
            or self._uses_trades
            or self._uses_pending
            or self._uses_session
        ):
            out.append("        pass")
        out.append("")

        if self._uses_time:
            out.extend(_TIME_HELPER)
        if self._uses_session:
            out.extend(_SESSION_HELPER)
        if self._uses_entry:
            out.extend(_ENTRY_HELPER_HEAD)
            if self._uses_risk:
                out.extend(_ENTRY_HALT_GUARD)
            out.extend(_ENTRY_HELPER_TAIL)
        if self._uses_exit:
            out.extend(_EXIT_HELPER)
        if self._uses_pending:
            out.extend(_PENDING_HELPER)
        if self._uses_risk:
            out.extend(_RISK_HELPER)
        if self._uses_choice:
            out.extend(_CHOICE_HELPER)
        if self._uses_trades:
            out.extend(_TRADES_HELPER)
        if self._uses_back:
            out.extend(_BACK_HELPER)

        out.append("    def next(self):")
        body = []
        if self._max_lookback:
            body.append(f"if len(self) <= {self._max_lookback}:")
            body.append("    # Pine answers na for history that does not exist")
            body.append("    # yet, and no condition on na fires. Backtrader")
            body.append("    # would answer with the far end of the preloaded")
            body.append("    # buffer -- bars from the future -- so the bars")
            body.append("    # missing the history are sat out instead.")
            body.append("    return")
        body += list(self.next_lines)
        if self._uses_pending:
            # Pine places the bar's orders when the script finishes running
            # on it, which for the pending entries is this flush.
            body.append("self._pine_flush()")
        if self._uses_trades:
            # Snapshotting at the *end* of the bar is what makes `[1]` mean
            # the previous bar: a trade closing on bar N is notified before
            # next() runs, so the counter has already moved by the time the
            # body reads it, and only this holds what it was.
            body.append(
                "self._pine_prev = (len(self._pine_closed), self._pine_wins, "
                "self._pine_losses, len(self._pine_open))"
            )
        if body:
            out.extend(f"        {line}" for line in body)
        else:
            out.append("        pass")

        return "\n".join(out) + "\n"


def _unparsable(message: str, class_name: str | None) -> ConversionResult:
    """Build the result for source that could not be parsed at all.

    Still emits a class, so callers writing one file per script get a file
    that says what went wrong rather than a traceback. It inherits from
    ``bt.Strategy`` and does nothing, and ``ok`` is False.
    """
    name = class_name or "UnconvertedStrategy"
    code = (
        "import backtrader as bt\n"
        "\n"
        "\n"
        f"class {name}(bt.Strategy):\n"
        '    """PineScript that pwb_toolbox.converting could not parse.\n'
        "\n"
        f"    {message}\n"
        "\n"
        "    This class is a placeholder -- it trades nothing.\n"
        '    """\n'
        "\n"
        "    def next(self):\n"
        "        pass\n"
    )
    return ConversionResult(
        code=code,
        class_name=name,
        unsupported=[f"could not parse: {message}"],
    )


def convert(source: str, class_name: str | None = None) -> ConversionResult:
    """Convert PineScript source into a Backtrader strategy.

    The result always carries generated code; check ``result.ok`` (or read
    ``result.unsupported``) before trusting it to be a faithful port.

    Source this converter cannot even parse is reported the same way as source
    it can parse but not translate. Raising here would break the promise above
    and, worse, would kill a loop over a corpus on its first odd script -- the
    very thing this module exists to survive.
    """
    try:
        program = parse(source)
    except PineSyntaxError as error:
        return _unparsable(str(error), class_name)
    generator = _Generator(program, class_name=class_name)
    code = generator.generate()
    return ConversionResult(
        code=code,
        class_name=generator.class_name,
        params=generator.params,
        unsupported=generator.unsupported,
        ignored=generator.ignored,
        notes=generator.notes,
    )

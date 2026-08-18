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

``request.security`` is the other. Pine reaches another timeframe inline;
Backtrader reaches it through a resampled feed added to the cerebro before the
strategy exists. So the call becomes a read from ``self.datas[n]`` and the
class records, in ``resample_spec``, the feeds the caller has to supply. The
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
the position is a reversal. See ``_ENTRY_HELPER``.

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
first chart bar of each new four-hour bar. Two things about it are refused
rather than guessed: a session argument, which needs an exchange calendar the
feed does not carry, and a weekly or monthly resolution, which does not floor
by modulo -- the epoch falls on a Thursday. See ``_TIME_HELPER`` and
:meth:`_Generator._value_time`.

A conversion with a non-empty ``unsupported`` list is not a working port. It is
a starting point plus a list of what you still have to write yourself.
"""

import collections
import keyword
import re
from dataclasses import dataclass, field
from typing import Any

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


INDICATORS = {
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
PIVOTS = ("ta.pivothigh", "ta.pivotlow")

#: Operators Backtrader overloads on line objects to give another line.
#: Comparisons are deliberately absent: a truth value is not a source, and
#: `bt.If` evaluates both branches, which would defeat the `d != 0 ? x / d : 0`
#: guard scripts write precisely to avoid dividing by zero.
_LINE_OPS = ("+", "-", "*", "/", "%")

#: Math that composes on lines rather than on numbers.
_LINE_MATH = {"math.abs": "abs", "math.max": "bt.Max", "math.min": "bt.Min"}

CROSSES = {
    "ta.crossover": "> 0",
    "ta.crossunder": "< 0",
    "ta.cross": "!= 0",
}

#: Pine price series, as line names. The feed they are read from is decided at
#: lowering time -- inside a ``request.security`` they belong to a resampled
#: feed rather than the chart's own.
PRICE_SERIES = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}

#: Pine's derived price series, which Backtrader has no line for. ``{d}`` is
#: the feed. Composed of whole lines, so this is itself a line and can be fed
#: to an indicator.
DERIVED_LINES = {
    "hl2": "({d}.high + {d}.low) / 2",
    "hlc3": "({d}.high + {d}.low + {d}.close) / 3",
    "ohlc4": "({d}.open + {d}.high + {d}.low + {d}.close) / 4",
}

#: The same, as per-bar reads for ``next()``, with ``{i}`` the bar offset.
#: Derived rather than written twice: two spellings of one definition drift,
#: and a mismatch between them would be a wrong number, not a failure.
DERIVED_SERIES = {
    name: re.sub(r"(\{d\}\.\w+)", r"\1[{i}]", expression)
    for name, expression in DERIVED_LINES.items()
}

#: Pine timeframe strings mapped to a Backtrader (timeframe, compression).
#: A bare number is minutes; a trailing S, D, W or M names the unit.
_TIMEFRAME_UNITS = {
    "S": "bt.TimeFrame.Seconds",
    "": "bt.TimeFrame.Minutes",
    "D": "bt.TimeFrame.Days",
    "W": "bt.TimeFrame.Weeks",
    "M": "bt.TimeFrame.Months",
}


def parse_timeframe(text):
    """Turn a Pine timeframe string into ``(bt.TimeFrame.X, compression)``.

    ``"240"`` is 240 minutes, ``"D"`` and ``"1D"`` are one day. Returns None
    for anything unrecognised, including the empty string, which in Pine means
    the chart's own timeframe and so needs no second feed.
    """
    if not isinstance(text, str):
        return None
    token = text.strip().upper()
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
_TIMEFRAME_SECONDS = {"S": 1, "": 60, "D": 86400}


def timeframe_seconds(text):
    """Seconds in one bar of ``text``, or None where Pine's floor is not a modulo.

    ``"240"`` is 14400 and ``"D"`` is 86400. Returns None for the empty string
    -- the chart's own timeframe, which needs no flooring -- and for weeks and
    months, which do not tile the epoch evenly.
    """
    if not isinstance(text, str):
        return None
    token = text.strip().upper()
    match = re.fullmatch(r"(\d*)([SDWM]?)", token)
    if not match or token == "":
        return None
    count, unit = match.groups()
    if unit == "" and count == "":
        return None
    per = _TIMEFRAME_SECONDS.get(unit)
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
    "    def _pine_time(self, seconds=None, ago=0):",
    '        """Opening time in epoch milliseconds, as Pine\'s time() reports it.',
    "",
    "        With no resolution this is the bar's own stamp. With one, it is that",
    "        stamp floored to the resolution, so the value changes exactly when a",
    "        new higher-timeframe bar begins.",
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
    "        stamp = calendar.timegm(when.utctimetuple())",
    "        if seconds:",
    "            stamp -= stamp % seconds",
    "        return stamp * 1000",
    "",
)


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
    "input.timeframe",
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
_ENTRY_HELPER = (
    "    def _pine_entry(self, long, size=None):",
    '        """Open or reverse a position, as Pine\'s strategy.entry does."""',
    "        held = self.position.size",
    "        if held > 0 if long else held < 0:",
    "            # pyramiding is 0: Pine allows one entry per direction.",
    "            return",
    "        if held:",
    "            self.close()",
    "        if long:",
    "            self.buy(size=size)",
    "        else:",
    "            self.sell(size=size)",
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
    "    def _pine_exit(self, tag, stop=None, limit=None):",
    '        """Keep one exit order set for the open position, as Pine does."""',
    "        # `var float sl = na` is the usual way to spell 'no level yet',",
    "        # and a stop submitted at NaN would never be comparable.",
    "        if stop is not None and stop != stop:",
    "            stop = None",
    "        if limit is not None and limit != limit:",
    "            limit = None",
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


def _substitute(node, bindings):
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


def _node_count(node):
    """Size of an expression tree, used to bound how far inlining may expand."""
    total = 1
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


def _if_as_assignment(statement):
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

    def branch(body):
        if (
            len(body) == 1
            and isinstance(body[0], Assign)
            and body[0].qualifier in ("", ":=")
        ):
            return body[0].target, body[0].value
        return None

    head = branch(statement.body)
    if head is None:
        return None
    target, value = head

    if not statement.orelse:
        other = Name(target)
    elif len(statement.orelse) == 1 and isinstance(statement.orelse[0], If):
        nested = _if_as_assignment(statement.orelse[0])
        if nested is None or nested[0] != target:
            return None
        other = nested[1]
    else:
        tail = branch(statement.orelse)
        if tail is None or tail[0] != target:
            return None
        other = tail[1]

    return target, Ternary(cond=statement.cond, then=value, other=other)


def _if_as_expression(statement):
    """Read a trailing ``if`` block as the value its function returns.

    Pine hands back the last expression of whichever branch ran, so an ``if``
    in this position is a conditional expression written over several lines --
    the same shape :func:`~pwb_toolbox.converting.parser.Parser.parse_if_expression`
    already folds. Returns ``None`` when a branch carries more than one
    expression, which a conditional cannot hold.
    """

    def branch(body):
        if len(body) == 1 and isinstance(body[0], ExprStmt):
            return body[0].value
        if len(body) == 1 and isinstance(body[0], Assign) and not body[0].qualifier:
            return body[0].value
        return None

    then = branch(statement.body)
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

    @property
    def ok(self) -> bool:
        """True when nothing needing attention was left behind.

        Ignored presentational calls do not count -- dropping a ``plot`` does
        not change how the strategy trades.
        """
        return not self.unsupported


def _class_name(title: str, fallback: str = "ConvertedStrategy") -> str:
    parts = re.findall(r"[A-Za-z0-9]+", title or "")
    name = "".join(part[:1].upper() + part[1:] for part in parts)
    if not name or name[0].isdigit():
        return fallback
    return name


def _slug(title) -> str:
    """Turn an input's title into a Python identifier: 'Stop %' -> 'stop'."""
    if not isinstance(title, str):
        return ""
    parts = re.findall(r"[A-Za-z0-9]+", title)
    name = "_".join(parts).lower()
    return f"p_{name}" if name[:1].isdigit() else name


def _safe(name: str) -> str:
    if keyword.iskeyword(name) or name in _RESERVED or name.startswith("_"):
        return f"pine_{name}"
    return name


def _literal(node) -> Any:
    if isinstance(node, Num):
        value = node.value
        return int(value) if value == int(value) else value
    if isinstance(node, Str):
        return node.value
    if isinstance(node, Bool):
        return node.value
    return None


class _Generator:
    def __init__(self, program, class_name=None):
        self.program = program
        self.declaration = program.declaration
        title = self.declaration[1] if self.declaration else ""
        self.class_name = class_name or _class_name(title)
        self.params = []  # (pine_name, default)
        self.param_names = set()
        self.series = {}  # pine name -> attribute name in __init__
        self.scalars = set()  # names computed as locals in next()
        self.init_lines = []
        self.next_lines = []
        self.unsupported = []
        self.ignored = []
        self._counter = 0
        self._hoisted = {}  # construction source -> attribute name
        self._inputs = {}  # input call signature -> param name
        self.state = {}  # pine `var` name -> attribute name on the strategy
        self.feeds = []  # (bt.TimeFrame expression, compression) per extra feed
        self._feed_index = {}  # timeframe text -> index into self.datas
        #: The feed expressions currently lower against. Swapped while the
        #: inner expression of a request.security is translated.
        self._feed = "self.data"
        self._uses_exit = False
        self._uses_math = False
        self._uses_trades = False
        self._uses_entry = False
        self._uses_time = False
        self._uses_pivot = False
        self._uses_expr = False
        #: Pine name -> the expression it was assigned, for top-level plain
        #: assignments only. Read by `_promote` when one is wanted as a line.
        self._computed = {}
        self._promoted = {}
        self._promoting = set()
        self._promotable = self._promotable_names()
        self.functions = program.functions
        #: Names currently being inlined, so recursion is caught rather than
        #: followed. A stack catches mutual recursion as well as direct.
        self._inlining = []
        #: Lines that must run in front of the statement being emitted -- the
        #: per-bar state updates of an inlined function that keeps `var`.
        self._prelude = []
        #: State slot -> the Pine name it came from, for slots whose `[1]` may
        #: be read. Only function-body vars qualify; see `_state_history`.
        self._var_history = {}
        #: State slots already assigned during the body being inlined.
        self._var_written = set()

    # --- helpers -------------------------------------------------------------

    def _fresh(self, stem):
        self._counter += 1
        return f"_{stem}_{self._counter}"

    def _local(self, stem):
        """A fresh name for a local in ``next()``.

        Unlike an attribute it carries no leading underscore, which `_safe`
        would otherwise have to escape -- `pine__f_jma_beta_6` reads far worse
        than `f_jma_beta_6` for no gain.
        """
        return self._fresh(stem)[1:]

    def _hoist(self, stem, construction):
        """Build ``construction`` in ``__init__`` once, returning its handle.

        Identical constructions are shared. Backtrader recomputes every
        indicator on every bar, so emitting the same CrossOver twice -- which
        ``ta.crossover``/``ta.crossunder`` on one pair otherwise does -- would
        double that work for no benefit.
        """
        attr = self._hoisted.get(construction)
        if attr is None:
            attr = self._fresh(stem)
            self._hoisted[construction] = attr
            self.init_lines.append(f"self.{attr} = {construction}")
        return f"self.{attr}"

    def _reject(self, message):
        if message not in self.unsupported:
            self.unsupported.append(message)

    def _ignore(self, message):
        if message not in self.ignored:
            self.ignored.append(message)

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
                self._reject(
                    f"{func.name}(): `varip {statement.target}` updates intrabar, "
                    "which a bar-close Backtrader run has no equivalent for"
                )
                return None

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
            # Not `bt.If`: it computes both branches on every bar, which
            # defeats the guard a conditional usually exists to be.
            return self._expr_line(node)
        if isinstance(node, Call):
            if node.func in self.functions:
                inlined = self._inline_call(node)
                return None if inlined is None else self._line_expr(inlined)
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
                    return f"{lowered}[{-offset}]"
            if not isinstance(node.base, Name) or not isinstance(offset, int):
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
                    return f"{handle}[{index}]"
                self._reject(
                    f"{name}: history of a computed value needs a Backtrader line"
                )
            return _safe(name)
        if name in self.param_names:
            if index:
                self._reject(f"{name}: a parameter has no bar history")
            return f"self.p.{_safe(name)}"
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
        if _presentational_constant(name):
            # `col = up ? color.green : color.red` only ever feeds a plot, and
            # plots are dropped. Refusing the colour would report the strategy
            # as unconvertible over something that cannot affect a trade.
            self._ignore(f"{name} dropped: presentational only")
            return "None"
        self._reject(f"unknown identifier {name!r}")
        return "None"

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

        if call.func == "time":
            return self._value_time(call)

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
        self._uses_exit = True
        arguments = [repr(str(tag))]
        for key in ("stop", "limit"):
            if key in levels:
                arguments.append(f"{key}={self._value_expr(levels[key])}")
        self.next_lines.append(f"{pad}self._pine_exit({', '.join(arguments)})")

    def _value_time(self, call, ago=0):
        """Lower Pine's ``time()`` to a floored stamp read off the primary feed.

        ``time()`` is this bar's opening time and ``time(res)`` is the opening
        time of the ``res`` bar containing it. ``ago`` reads a bar back, which
        is what ``time(res)[1]`` and ``ta.change(time(res))`` need.

        A session argument is refused rather than approximated: honouring it
        needs the exchange calendar, and the feed does not carry one.
        """
        if len(call.args) > 1:
            self._reject(
                "time() with a session argument needs the exchange calendar, "
                "which the data feed does not carry"
            )
            return "None"
        self._uses_time = True
        if not call.args:
            return f"self._pine_time(None, {ago})" if ago else "self._pine_time()"
        resolution = call.args[0]
        text = _literal(resolution)
        if not isinstance(text, str):
            self._reject("time(): the resolution must be a literal string")
            return "None"
        seconds = timeframe_seconds(text)
        if seconds is None:
            self._reject(
                f"time(): resolution {text!r} does not floor to a fixed number "
                "of seconds"
            )
            return "None"
        if ago:
            return f"self._pine_time({seconds}, {ago})"
        return f"self._pine_time({seconds})"

    def _value_security(self, call):
        """Lower ``request.security`` onto a resampled Backtrader feed.

        Pine reaches another timeframe inline; Backtrader reaches it through a
        second data feed set up on the cerebro before the strategy exists. So
        the call becomes a read from ``self.datas[n]``, and the feeds the
        caller has to supply are recorded on the class.
        """
        if len(call.args) < 3:
            self._reject("request.security needs a symbol, timeframe and expression")
            return "None"

        symbol, timeframe, expression = call.args[0], call.args[1], call.args[2]

        if not (isinstance(symbol, Name) and symbol.id == "syminfo.tickerid"):
            self._reject(
                "request.security on a symbol other than syminfo.tickerid needs "
                "a second instrument, which is a data-loading decision"
            )
            return "None"

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
                return "None"

        if isinstance(timeframe, Name) and timeframe.id == "timeframe.period":
            # The chart's own timeframe. Pine still routes it through
            # request.security; Backtrader needs no second feed for it.
            feed = self._feed
        else:
            resolved, from_param = self._timeframe_text(timeframe)
            if resolved is None:
                self._reject(
                    "request.security: the timeframe must be a literal string or "
                    "an input default, because the feed is built before the "
                    "strategy runs"
                )
                return "None"
            spec = parse_timeframe(resolved)
            if spec is None:
                self._reject(
                    f"request.security: timeframe {resolved!r} is not recognised"
                )
                return "None"
            if from_param:
                # The param stays, because the script may read it elsewhere,
                # but it cannot move the feed: resampledata runs before the
                # strategy is instantiated. Say so rather than leave a knob
                # that looks live and is not.
                self._ignore(
                    f"{from_param}: timeframe fixed to {resolved!r} at conversion "
                    "time; change resample_spec, not the param"
                )
            index = self._feed_index.get(resolved)
            if index is None:
                self.feeds.append(spec)
                index = len(self.feeds)  # datas[0] is the chart itself
                self._feed_index[resolved] = index
            feed = f"self.datas[{index}]"

        previous, self._feed = self._feed, feed
        try:
            line = self._line_expr(expression)
            if line is not None:
                return f"{line}[0]"
            return self._value_expr(expression)
        finally:
            self._feed = previous

    def _timeframe_text(self, node):
        """Recover the timeframe string and, if it came from one, the param.

        A timeframe cannot stay tunable: `cerebro.resampledata` runs before the
        strategy is instantiated, so a param changed at `addstrategy` time would
        not move the feed underneath it. Resolving the input's default and
        baking it in is the honest reading.
        """
        literal = _literal(node)
        if isinstance(literal, str):
            return literal, None
        if isinstance(node, Name):
            for name, default in self.params:
                if name == node.id and isinstance(default, str):
                    return default, name
        return None, None

    def _input_default(self, call):
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
            # `var` survives the bar; `varip` also survives *within* a bar, and
            # updates on every tick. A bar-close backtest has no ticks, so the
            # two are only equivalent by accident. Say so rather than guess.
            self._reject(
                f"varip {statement.target}: varip updates intrabar, which a "
                "bar-close Backtrader run has no equivalent for"
            )
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

        self._reject(f"call to {expr.func}() is not supported")

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
        for key, value in call.kwargs:
            if key in ("qty", "size"):
                size = self._value_expr(value)
        long = "True" if direction == "strategy.long" else "False"
        arguments = f"{long}, size={size}" if size else long
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
        if self._uses_math:
            out.append("import math")
        out += ["", ""]
        if self._uses_pivot:
            out.extend(_PIVOT_INDICATOR)
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
            out.append("    #: in the order they become self.datas[1:]. Add each")
            out.append("    #: with cerebro.resampledata(data, timeframe=..., ")
            out.append("    #: compression=...) before cerebro.addstrategy().")
            out.append("    resample_spec = (")
            for timeframe, compression in self.feeds:
                out.append(f"        ({timeframe}, {compression}),")
            out.append("    )")
            out.append("")

        out.append("    def __init__(self):")
        if self._uses_exit:
            out.append("        self._pine_exits = {}")
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
                f'{needed} data feeds; see resample_spec")'
            )
        if self.init_lines:
            out.extend(f"        {line}" for line in self.init_lines)
        elif not (self._uses_exit or self._uses_trades):
            out.append("        pass")
        out.append("")

        if self._uses_time:
            out.extend(_TIME_HELPER)
        if self._uses_entry:
            out.extend(_ENTRY_HELPER)
        if self._uses_exit:
            out.extend(_EXIT_HELPER)
        if self._uses_trades:
            out.extend(_TRADES_HELPER)

        out.append("    def next(self):")
        body = list(self.next_lines)
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
    )

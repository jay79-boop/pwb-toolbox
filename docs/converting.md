# Converting PineScript to Backtrader

`pwb_toolbox.converting` turns a PineScript strategy into a Backtrader one.

```python
from pwb_toolbox.converting import convert

result = convert(pine_source)
if result.ok:
    print(result.code)
else:
    print("still needs work:", result.unsupported)
```

Paired with the scraping module, that is a pipeline from a published script to
a runnable strategy:

```python
from pwb_toolbox.scraping import ScriptStore
from pwb_toolbox.converting import convert

for record in ScriptStore("script-corpus").records():
    if record.language == "pinescript":
        result = convert(record.code, class_name=None)
        print(record.title, "->", "ok" if result.ok else result.unsupported)
```

## Read this first

This is **not** a complete transpiler, and one cannot be written in a weekend.
Pine is a real language with multi-timeframe requests, persistent variables,
arrays, matrices, maps, user-defined types and libraries. Its execution model —
every expression is a series evaluated on every bar — does not line up with
Backtrader's, where an indicator is a line object built once and indexed per
bar.

What this does cover is the shape most published strategies actually take: a
declaration, some inputs, a handful of `ta.*` indicators, conditions, and entry
and exit calls. Everything else is **reported, not guessed**.

A result with a non-empty `unsupported` list is a starting point plus a to-do
list. It is not a working port, and `result.ok` tells you which you have.

## The core translation

Pine lets you write `ta.sma(close, 20)` anywhere because it is a series.
Backtrader needs that indicator constructed once in `__init__` and then indexed
in `next`. So the converter **hoists**:

```pinescript
maFast = ta.sma(close, fast)
if ta.crossover(maFast, maSlow)
    strategy.entry("long", strategy.long)
```

becomes

```python
def __init__(self):
    self._sma_1 = bt.indicators.SMA(self.data.close, period=self.p.fast)
    self._sma_2 = bt.indicators.SMA(self.data.close, period=self.p.slow)
    self._cross_3 = bt.indicators.CrossOver(self._sma_1, self._sma_2)

def next(self):
    if self._cross_3[0] > 0:
        self.buy()
```

Identical constructions are shared. `ta.crossover(a, b)` and
`ta.crossunder(a, b)` on the same pair produce **one** `CrossOver`, compared in
two directions — Backtrader recomputes every indicator on every bar, so a
duplicate is pure waste.

## What is translated

| Pine | Backtrader |
| --- | --- |
| `strategy("T")` / `indicator("T")` | class name and docstring |
| `input.int/float/bool/string(...)` | entries in `params` |
| `float x = ...`, `series int n = ...` | the type annotation is dropped |
| `array<float> b = ...`, `Zone z = ...` | likewise, generics and user types included |
| `float[] xs = ...` | the older array spelling, read the same way |
| `ta.sma/ema/wma/rma/rsi/stdev/highest/lowest/atr/tr` | `bt.indicators.*` |
| `ta.hma(src, len)` | composed from weighted averages — see below |
| `ta.vwma(src, len)` | `sma(src × volume) / sma(volume)`, guarded |
| `ta.alma(src, len, offset, sigma)` | a `PineAlma` line, emitted with the file |
| `ta.crossover/crossunder/cross` | `CrossOver` plus a direction test |
| `ta.pivothigh/pivotlow(src, left, right)` | a `PinePivot` line, emitted with the file |
| the short form, `ta.pivothigh(left, right)` | the same over `high` / `low` |
| `ta.change(src, n)` | `src[0] - src[-n]` |
| `close`, `open`, `high`, `low`, `volume` | `self.data.<line>[0]` |
| `hl2`, `hlc3`, `ohlc4` | the arithmetic spelled out, as a line or a read |
| `close[3]` | `self.data.close[-3]` |
| `and` / `or` / `not`, comparisons, arithmetic | the Python equivalents |
| `cond ? a : b` | `a if cond else b`, or a `PineExpr` line where one is needed |
| `switch mode` over `ta.sma` / `ta.rma` / ... | one indicator, chosen in `__init__` — see below |
| a length written as `mode == 'Fast' ? 5 : 20` | resolved the same way, since a period is read once |
| `switch` with or without a subject | the chain of conditionals it means |
| `if` / `else if` / `else` | the same, inside `next()` |
| an `if` read for its value | the conditional expression it means |
| `f(a, b) => ...` and its call sites | the body, inlined where it is called |
| a parameter with a type, a default, or both | the type dropped, the default kept |
| an expression split over several lines | joined before parsing |
| `strategy.entry(..., strategy.long/short, qty=)` | one entry per direction, reversing — see below |
| `strategy.entry(..., limit=, stop=)` | a resting bracket parent, submitted at bar close — see below |
| `strategy.exit(..., from_entry, ...)` on a pending entry | the bracket's exit legs, live when the entry fills |
| `strategy.cancel(id)` | withdraws the unfilled entry with that id |
| `strategy.cancel_all()` | withdraws every unfilled order, standing exits included |
| `time(res, session)`, `input.session` | na outside the session, checked on the feed's clock — see below |
| `time`, `time[n]` | the bar's own opening stamp, in epoch milliseconds |
| `timeframe.in_seconds()` | seconds in one bar of the feed, answerable from `__init__` |
| `timeframe.in_seconds("240")`, or an `input.timeframe` | the same for a written timeframe, the input still live |
| `timestamp("01 Jan 2020 00:00 +0000")` | the number it means, folded at conversion time |
| `input.time(...)` | an integer param, so the window moves without an edit |
| `time(res, session, tz)` | the same, with the feed's clock read as UTC and converted into `tz` |
| history of a computed value, e.g. `inSess[1]` | a promoted line, or the definition re-read a bar back — see below |
| `syminfo.mintick` | a `mintick` param, default 0.01 — see below |
| `strategy.closedtrades`, `opentrades`, `wintrades`, `losstrades`, `eventrades` | counters kept from `notify_trade` |
| any of those with `[1]` | the previous bar's value |
| `strategy.closedtrades.entry_price/exit_price/profit/size(i)` | a ledger built as trades close |
| `.entry_bar_index(i)`, `.exit_bar_index(i)` | likewise, on the same bar numbering as `bar_index` |
| `strategy.close`, `strategy.close_all`, bare `strategy.exit` | `self.close()` |
| `strategy.risk.max_drawdown(x)` | a per-bar equity check that flattens and stops — see below |
| `strategy.risk.max_intraday_loss(x)` | the same, lifting when the day turns |
| `strategy.percent_of_equity`, `strategy.cash` | which of the two the limit is measured in |
| `strategy.exit(..., stop=, limit=)` | an OCO stop/limit pair, maintained |
| `strategy.position_avg_price` | `self.position.price` |
| `strategy.position_size` | `self.position.size` |
| `bar_index` | `len(self)` |
| `barstate.isconfirmed/isnew/ishistory` | `True` — see below |
| `barstate.isrealtime` | `False` |
| `barstate.isfirst`, `barstate.islast` | a position in the feed, so still live |
| `var x = <literal>` | an attribute set once in `__init__` |
| `varip x = <literal>` | the same — the two are identical on historical bars |
| `x := value` | assignment, writing through to the attribute for a `var` |
| `x += y`, and `-=` `*=` `/=` `%=` | desugared to `x := x + y` |
| `na(x)` | the NaN test `x != x` |
| `request.security(syminfo.tickerid, tf, expr)` | a read from a resampled `self.datas[n]` |
| `request.security("SPY", tf, expr)`, `input.symbol` | a second instrument, recorded in `feed_spec` |
| `close[n]` where `n` is a param or computed | a guarded read back — see below |
| `math.abs/max/min/round`, `nz` | the Python equivalents |
| `math.pow`, `math.sign`, `math.avg` | the arithmetic spelled out |
| `math.sqrt/log/log10/exp/floor/ceil`, the trig set, `math.pi` | the `math` module, imported only when used |
| `var` inside a function body | an attribute per call site, updated each bar |

Pine inputs become real Backtrader params, so they stay tunable:

```python
cerebro.addstrategy(DualMACross, fast=3, slow=40)
```

An input does not have to be the whole right-hand side. The percentage idiom
works too, and the param is named from the input's title when there is no
variable to take the name from:

```pinescript
stop = input.float(5.0, "Stop Percent") / 100
```

```python
params = (('stop_percent', 5),)
...
stop = (self.p.stop_percent / 100)
```

Where a title-derived name collides with a variable, the computed local wins
every later reference — that is what the Pine source means by the name.

## What is refused

Reported in `result.unsupported`, never approximated:

- `request.security(..., lookahead=barmerge.lookahead_on)` — reads a bar before it closes
- `var x = <expression>` — only a literal initial value works; see below
- arrays, matrices, maps and `type` blocks — all reported, so the rest of the script is still diagnosed
- a user-defined function that recurses, returns a tuple, expands past the inlining limit, or keeps state and is called from inside an `if` — see below
- `for` / `while` loops
- `timestamp(tz, year, month, day, hour, minute)` — the numeric form; only the literal date string is read
- a `switch` used as a statement rather than for its value — that is a side-effecting block
- an `if` read for its value whose branch carries more than one expression — see below
- tuple destructuring, e.g. `[macd, signal, hist] = ta.macd(...)`
- `strategy.exit` carrying `loss`, `profit` or `trail_*` — distances in ticks, and tick size belongs to the instrument, not the script
- `pyramiding` above 0 — Backtrader nets one position per feed; see below
- `strategy.closedtrades.max_runup/max_drawdown/commission/entry_id/exit_id/exit_comment` — Backtrader records none of them
- more than one bar of history on a trade counter — only the previous bar is kept
- a computed value used as an indicator source when it is reassigned or conditional — see "Computed values as indicator sources"
- an indicator length, or a pivot's bar counts, that is only known per bar — a period is read once, when the indicator is built
- any identifier or call the converter does not know

Reported separately in `result.ignored`, because dropping them changes nothing
about how the strategy trades: `plot`, `plotshape`, `bgcolor`, `hline`, `fill`,
`alertcondition`, `label.new`, `line.new`, and friends — along with the drawing
constants they consume, such as `color.green` and `shape.triangleup`. A colour
cannot change a trade, so refusing one would fail a conversion over nothing.

A third list, `result.notes`, records a reading the conversion made
deliberately that differs from the source as written. Not a gap — the
translation is faithful on the bars a backtest runs — but the reader should
know one was made. `varip` read as `var` is one; a switch mode that raises
rather than building is the other.

All three lists are also written into the generated class's docstring, so a
converted file explains itself without needing the original result object.

## Stops and targets

`strategy.exit` with a `stop` or a `limit` becomes a Backtrader stop order and
limit order, linked one-cancels-other so whichever fills cancels the sibling:

```pinescript
if strategy.position_size > 0
    strategy.exit("Long Exit", "Long", stop=sl, limit=tp)
```

Both are absolute prices in Pine, which is what makes them translatable — no
tick size or entry price has to be inferred.

The subtlety is that **Pine's exit is a standing instruction, not a
submission.** It is re-evaluated on every bar, and when the levels change it
*moves* its orders rather than adding more. Translating each call into a fresh
`self.sell(...)` would leave a position held ten bars carrying twenty live exit
orders, filling several times over — a wrong backtest rather than an error.

So the generated class maintains them in a `_pine_exit` helper: same levels and
the orders still live, do nothing; levels moved, cancel and resubmit; position
closed, cancel and forget. The test suite counts orders against trades and
asserts the exact ratio, because that is the only way this failure shows up.

Direction is decided at runtime from the position, so exiting a long sells and
exiting a short buys. A level that is `na` submits nothing — `var float sl = na`
is how a script spells "no level yet", and a stop at NaN would never compare.

`loss`, `profit` and the `trail_*` family are refused. They are distances
measured in ticks, and tick size is a property of the instrument rather than
anything the script states.

## Priced entries

`strategy.entry` with a `limit` or `stop` price is a standing order too: it
rests until it fills, until the same id is re-issued — which *moves* it — or
until `strategy.cancel` withdraws it. And the `strategy.exit` that names it
through `from_entry` is issued on the same bar, while the position is still
size zero, so translating that exit against the position would place nothing
and leave the eventual fill unprotected.

The generated class collects priced entries per id while the bar's statements
run and submits them once, at the end of `next()`, as a Backtrader bracket:
the entry as parent, the exit's stop and limit as legs that only go live when
the parent fills, each cancelling the other.

```pinescript
strategy.entry("L", strategy.long, limit=entryP)
strategy.exit("Lx", "L", stop=stopP, limit=tgtP)
// ...bars later, if it never filled...
strategy.cancel("L")
```

Re-issuing `"L"` at new levels cancels and replaces the resting bracket; an
unchanged one is left alone; `strategy.cancel("L")` withdraws it while the
entry is unfilled and, exactly as in Pine, touches nothing once it has filled —
the legs keep protecting the open position. A `strategy.exit` re-issued after
the fill moves those legs rather than stacking a second pair beside them.

Only a literal id works for `strategy.cancel`, because the id is what names
the pending order; a computed one is reported.

`strategy.cancel_all()` needs no id: it withdraws every unfilled order at
once — pending entries, resting brackets, *and* the standing exits protecting
an open position, exactly as Pine's does. That last part means a position can
be left unprotected, which is why the force-flat idiom pairs it with
`strategy.close_all()`.

## Sessions and tick size

`time(res, session)` answers na on bars outside the session, which is how
`not na(time(timeframe.period, sess))` gates entries to market hours. The
check runs on the feed's own timestamps — Pine consults the exchange calendar
and timezone, the feed carries neither, and the feed's clock is the one clock
a backtest has — so data stamped in another timezone than the exchange filters
at shifted hours. Ranges (`"0930-1600"`, comma-separated, overnight allowed)
and a day suffix (`":23456"`, Sunday=1, naming the day the session ends on)
are understood; `input.session` becomes an ordinary string param, so the
window stays tunable from `addstrategy`.

A third argument names the timezone the session is meant in —
`time(timeframe.period, "0930-1130", "America/New_York")` is how a script
pins its window to New York regardless of the chart. The generated check then
reads the feed's clock as UTC and converts it into that zone, DST included,
so data already stamped in exchange time should not pass a timezone at all.
`syminfo.timezone` as the argument is dropped rather than converted: the
exchange's own zone is exactly what the bare check already assumes of the
feed's clock.

`syminfo.mintick` becomes a param named `mintick`, defaulting to the 0.01 of a
US equity. A Backtrader feed does not know its instrument's tick size, so the
knob is handed to the caller and the report says to set it per instrument.

## The date window

Nine of the seventeen strategies in the corpus open the same way: two dates, a
comparison against `time`, and every order behind the result.

```pinescript
dFrom   = input.time(timestamp("01 Jan 2020 00:00 +0000"), "From")
dTo     = input.time(timestamp("31 Dec 2030 23:59 +0000"), "To")
inRange = time >= dFrom and time <= dTo
```

A timestamp is a constant, so it is folded to epoch milliseconds once, here,
rather than parsed again on every bar:

```python
params = (('dFrom', 1577836800000), ('dTo', 1924991940000))
...
inRange = ((self._pine_time() >= self.p.dFrom) and (self._pine_time() <= self.p.dTo))
```

`input.time` becomes an ordinary integer param because the window is the input
most likely to be moved — walking a strategy forward is exactly that edit — and
a folded constant in the body could not be moved without regenerating the file.

Bare `time` is the bar's own opening stamp on the feed's clock, read as UTC,
which is the same convention `time(res, session)` above already uses. A
timestamp written without an offset is read as UTC too, which is what Pine
assumes when none is given.

The numeric spelling — `timestamp("GMT+0", 2025, 2, 1, 0, 0)` — is refused
rather than guessed at. It appears once in the corpus, in an indicator, and
resolving an arbitrary timezone name would mean guessing at a calendar the
feed does not carry.

### Seconds in a bar

`timeframe.in_seconds()` reads the feed rather than a string, and that is the
point rather than an implementation detail: the answer is settled before the
first bar, so it can be asked from `__init__`. Scripts use it to pick an
indicator by the chart's timeframe —

```pinescript
mode = timeframe.in_seconds() == 3600 ? smooth1H : timeframe.in_seconds() == 14400 ? smooth4H : 'EMA'
```

— and that choice has to be made where the indicator is built, not per bar.

The written forms take the text instead, so an `input.timeframe` stays live:
unlike the timeframe of a `request.security` feed, which is baked in because
the feed is built before the strategy exists, this one is only read. Pine's
month is a flat 30 days and its week 7, which is what keeps the answer a
number rather than a calendar question. A timeframe that is only known per
bar is reported.

## Reading history before it exists

`high[2]` on the first bar of a run reads history that is not there. Pine
answers `na`, and no condition on na fires. Backtrader preloads the feed into
flat arrays, and the same read wraps around to the *end* of the array — bars
from the future, silently. So the generated `next()` opens by sitting out the
bars that lack the deepest history the script reads; the regression test pins
a tape where only the wraparound could trigger a trade, and asserts it never
does.

## A second timeframe

Pine reaches another timeframe inline. Backtrader reaches it through a second
data feed, resampled onto the cerebro *before* the strategy exists. So
`request.security` becomes a read from `self.datas[n]`, and the generated class
records what it needs:

```pinescript
htfTF = input.timeframe("W", "Higher timeframe")
htfMa = request.security(syminfo.tickerid, htfTF, ta.ema(close, 4))
```

```python
feed_spec = (
    (None, bt.TimeFrame.Weeks, 1),
)

def __init__(self):
    if len(self.datas) < 2:
        raise ValueError("HTFTrend needs 2 data feeds; see feed_spec")
    self._ema_1 = bt.indicators.EMA(self.datas[1].close, period=4)
```

Each entry is `(symbol, timeframe, compression)`, in the order the feeds become
`self.datas[1:]`. A symbol of `None` means the chart's own instrument, so the
entry is a resample; a named symbol means that instrument's own data. Wiring it
up is then mechanical:

```python
cerebro.adddata(data)
for symbol, timeframe, compression in HTFTrend.feed_spec:
    feed = data if symbol is None else load(symbol)
    if timeframe is None:
        cerebro.adddata(feed)
    else:
        cerebro.resampledata(feed, timeframe=timeframe, compression=compression)
cerebro.addstrategy(HTFTrend)
```

Run it with the feeds missing and it raises in `__init__` naming what is
absent, rather than an `IndexError` from somewhere inside `next()`.

Calls sharing a timeframe share a feed. `timeframe.period` is the chart's own
timeframe, so it adds no feed at all.

**The timeframe stops being tunable.** `resampledata` runs before
`addstrategy`, so a param changed at that point cannot move the feed underneath
the strategy. A timeframe read from an input is therefore resolved to that
input's default and baked in — and reported in `result.ignored`, because a knob
that looks live and is not would be a silently wrong backtest. Change
`feed_spec`, not the param.

### A second instrument

The same machinery carries a different symbol. A relative-strength script names
one and compares against it:

```pinescript
peerSymbol = input.symbol("SPY", "Comparative Symbol")
peer = request.security(peerSymbol, timeframe.period, close)
```

```python
feed_spec = (
    ('SPY', None, None),
)
...
peer = self.datas[1].close[0]
```

A timeframe of `None` means that instrument at the chart's own timeframe — data
to add, with nothing to resample. `('SPY', bt.TimeFrame.Days, 1)` is SPY *and*
a resample, and counts as one feed. Reads sharing a symbol and a timeframe
share a feed; a symbol and a timeframe that differ are separate ones.

**The symbol stops being tunable for exactly the reason the timeframe does.**
The feed is loaded before the strategy is instantiated, so a param changed at
`addstrategy` time cannot swap the instrument underneath it. An `input.symbol`
is resolved to its default and baked in, the param stays because the script may
read it elsewhere, and the conversion says so in `result.ignored`.

Naming a symbol is not a way around the lookahead refusal below; that check
runs on every `request.security` regardless of which instrument it names.

### Reading back a variable number of bars

`baseClose[rsPeriod]`, where `rsPeriod` is an input, cannot be counted at
conversion time — so the bars it reaches over cannot be sat out in advance the
way a constant offset's are.

That matters more than it sounds, because of how Backtrader answers a read past
the start of a series. It does not raise, and it does not answer `na`: the
series is preloaded, so Python's negative indexing counts back from the **end**
and hands over a bar from the future. On the first bar of a thirty-bar feed,
`close[5]` reads bar 26.

```python
def _pine_back(self, line, back):
    """`x[n]` with n known only per bar, `na` before the series starts."""
    try:
        step = int(back)
    except (TypeError, ValueError):
        return float('nan')
    if step < 0 or step >= len(line):
        return float('nan')
    return line[-step]
```

The test for this walks a ramp where every bar's price is distinct, and asserts
both halves: the early bars answer `na`, and no bar ever reads a price from
later in the feed.

### On lookahead

Pine's default is `barmerge.lookahead_off`: the higher timeframe must not leak
data the current bar could not have seen. Backtrader's resampled feed behaves
the same way, and the test suite asserts it directly — a probe walks weekly and
monthly resamples and fails if the higher-timeframe bar is ever dated after the
chart bar being processed.

That check is the reason this feature can be trusted. A lookahead bug does not
fail loudly; it produces a beautiful, entirely fake backtest.

`lookahead=barmerge.lookahead_on` is refused for the same reason. It genuinely
reads a bar before it closes, and there is no way to offer that without feeding
the strategy data it could not have had.

## State that survives the bar

`var` is how a strategy remembers something between bars — the price it got
filled at, a stop level, a counter. Pine initialises a `var` once and keeps it;
a Backtrader instance attribute already behaves that way, so that is what it
becomes.

```pinescript
var float entryPrice = na
var int trades = 0
if na(entryPrice) and close > ma
    strategy.entry("long", strategy.long)
    entryPrice := close
    trades := trades + 1
```

```python
def __init__(self):
    self.entryPrice = float('nan')
    self.trades = 0

def next(self):
    if (self.entryPrice != self.entryPrice) and (self.data.close[0] > self._sma_1[0]):
        self.buy()
        self.entryPrice = self.data.close[0]
        self.trades = (self.trades + 1)
```

`na(x)` lowers to the NaN test `x != x`, which is what pairs with
`var float x = na` — the usual way a script spells "no position yet".

A `var` named after something a `bt.Strategy` already owns is renamed, exactly
as params are: `var position = 0` becomes `self.pine_position`, so it cannot
quietly overwrite Backtrader's own `position`.

Two limits, both reported rather than guessed at:

- **The initial value must be a literal.** `var float x = close` means the
  *first bar's* close, and `__init__` runs before there is a first bar. Numbers,
  strings, booleans and `na` all work; anything reading a series does not.
- **A `var` has no history.** `entryPrice[1]` needs a real line object. One
  attribute holds one value.

The test suite runs a converted `var` strategy through a real `cerebro` and
checks the Pine counter matches the broker's own trade count. Compiling proves
nothing here — a local assigned in `next()` compiles too, and would silently
count to one and stay there.

## Expressions split across lines

A long condition is routinely written over several lines, and both shapes turn
up in published scripts:

```pinescript
entryOk = (not useGateA or sweepLong) and
          (not useGateB or upperThird) and
          (not useGateC or bodyOk)

trailLevel = enableTrail and armed
             ? maxClose - trailDistance
             : na
```

Pine's own rule is that a continuation is indented by something that is *not* a
multiple of four, which collides with the indentation that opens a block. The
operator is read instead, which is unambiguous in both directions: **no
statement ends with a binary operator, and none begins with one.** So a line
ending in `and`, `+`, `?`, `:` or `=` continues onto the next, and a line
starting with one continues the line above.

`[` is deliberately not a continuation opener — a line starting with it is
tuple destructuring, `[macd, signal, hist] = ta.macd(...)`, which is a statement
of its own.

`-` and `+` need one more rule, because they are the two operators that are
also *prefixes*. A line opening with one either continues the line above or is
a fresh expression with a sign:

```pinescript
sign(v) =>
    if v > 0
        1
    else if v < 0
        -1        // the branch value, not `v < 0 - 1`
```

Only the second reading is possible after a line that opens a block, and
whether a line opens one is decidable from its own text: it starts with `if`,
`else`, `for`, `while` or `switch`, assigns an `if` or a `switch`, or ends with
`=>`. So a signed line under a block opener is that block's body. Read the
other way — which is what happened before this rule existed — an `if` branch of
`-1` silently became arithmetic, and the strategy traded on it.

Where the breaks fall never reaches the output: the tests assert that a split
condition and the same condition on one line generate character-identical code.

## Three moving averages, and one that looks like it is already there

`ta.vwma` and `ta.alma` have no Backtrader equivalent. `ta.hma` appears to —
`bt.indicators.HullMovingAverage` implements the same published formula — and
using it would be wrong.

Hull ends with a weighted average over `sqrt(length)` bars. Pine **rounds**
that; Backtrader **truncates** it. For `length = 13` Pine averages over 4 bars
and Backtrader over 3, and the two disagree for **24 of the first 59 lengths**
— by a little, on every bar, with nothing to show that anything is wrong.

So it is composed here out of what Pine says it is made of:

```python
self._wma_1 = bt.indicators.WMA(self.data.close, period=(self.p.n) // 2)
self._wma_2 = bt.indicators.WMA(self.data.close, period=self.p.n)
self._hma_3 = bt.indicators.WMA(2.0 * self._wma_1 - self._wma_2, period=round((self.p.n) ** 0.5))
```

There is a test asserting the generated code contains no `HullMovingAverage`
and that its value *differs* from Backtrader's, so the difference cannot be
tidied away later by someone who spots the built-in.

`ta.vwma` is `sma(src × volume, len) / sma(volume, len)`. The division goes
through a `PineExpr` rather than a line operation, so a window with no volume
at all answers `na` — as Pine does — instead of raising.

`ta.alma` is the one that needs a real indicator: its weights are a Gaussian
over the window rather than a composition of averages, so the window has to be
walked. The weights depend only on the length, the offset and sigma, so they
are computed once rather than per bar. Offset and sigma default to `0.85` and
`6`, matching TradingView.

All three are checked against their definitions written out longhand, to twelve
significant figures.

## Pivots, and the offset that makes them honest

Backtrader has no pivot indicator, so one is emitted into the generated file
when the script asks for it:

```pinescript
ph = ta.pivothigh(high, leftBars, rightBars)
plFound = not na(ta.pivotlow(low, leftBars, rightBars))
```

```python
self._pivot_1 = PinePivot(self.data.high, left=self.p.leftBars,
                          right=self.p.rightBars, high=True)
```

The bar under test is `right` bars back. It is a pivot when it beats every one
of the `left` bars before it and every one of the `right` bars after it, and
the line carries its value on the bar that confirms it and `NaN` everywhere
else — which is what Pine returns, and what `na()` then tests.

**Both comparisons are strict.** A flat top — two equal highs side by side —
is not a pivot high in Pine, and scripts that want one write their own with
`>=` on the left. The test suite checks this against a brute-force definition
written out longhand, on integer prices where ties are common enough that a
`>=` would show up immediately.

**The `right` offset is what makes it causal**, and that is not incidental: a
pivot is reported only once `right` further bars have closed and confirmed it.
Nothing reads a bar that has not happened. There is a test that grows the feed
and asserts no reading already taken changes — the same check the lookahead
section above applies to `request.security`.

The window is fixed when the indicator is constructed, so `left` and `right`
have to be numbers or params — the same boundary as any indicator's length. A
computed source is refused for the same reason `ta.ema(computed, n)` is; see
"A known simplification".

## `strategy.entry` is not `self.buy()`

This is the correction most likely to change a number you already have.

Pine's default is `pyramiding=0`, which allows **one entry per direction**.
Calling `strategy.entry` again while already long does nothing at all.
Backtrader's `buy()` has no such rule — it adds, every time. So the ordinary
Pine idiom

```pinescript
if close > ma
    strategy.entry("l", strategy.long)
```

which in Pine opens one position and holds it, used to emit a `self.buy()` on
**every bar the condition held**. On the test feed that built a 21-unit
position where Pine holds 1. Everything downstream — position value, P&L,
drawdown — was wrong by that factor, and nothing about the generated source
looked wrong.

An entry is now one call:

```python
def _pine_entry(self, long, size=None):
    held = self.position.size
    if held > 0 if long else held < 0:
        return                      # pyramiding is 0: Pine allows one
    if held:
        self.close()                # an entry against the position reverses
    if long:
        self.buy(size=size)
    else:
        self.sell(size=size)
```

Reversal is two orders rather than one resize, so the new position's size still
comes from the strategy's own sizer rather than being computed from the old
one.

`pyramiding` above 0 is refused. Above zero Pine really can hold several trades
in one direction, and Backtrader nets them into one position per feed — the two
models diverge, and `strategy.opentrades` is exactly where you would notice.

## Risk rules, and what a halt means

`strategy.risk.max_drawdown` and `strategy.risk.max_intraday_loss` are not
filters on a condition — they are a rule about the account, checked by Pine
itself, that ends trading when equity has fallen far enough.

```pinescript
strategy.risk.max_drawdown(20, strategy.percent_of_equity)
strategy.risk.max_intraday_loss(5, strategy.percent_of_equity)
```

Both become a call at the top of `next()`, before any of the bar's own
statements run:

```python
self._pine_risk(20, True, False)
self._pine_risk(5, True, True)
```

`_pine_risk` tracks peak equity for the drawdown rule and the day's opening
equity for the intraday one, and on a breach does three things: clears whatever
entries are waiting to be placed, cancels every order of its own still
working on the book, and closes the position. Then it sets a flag, and
`_pine_entry` refuses while that flag is up — because flattening alone is only
half of it. Pine places no new orders after a breach, and a script whose entry
condition is still true would otherwise walk straight back in on the next bar.

The two flags differ in how long they last. `max_drawdown` stops the run for
good. `max_intraday_loss` stops the *day*: the flag clears at the first bar of
the next one, along with a fresh opening-equity reference. That difference is
the whole reason Pine has two rules rather than one.

`strategy.percent_of_equity` and `strategy.cash` are not interchangeable, and
reading the wrong one is silent — the number in the source is the same either
way, and only the tape says which was meant. Percent is Pine's default when
neither is named.

Two divergences worth knowing:

- **The check lands on a close.** Pine evaluates the rule intrabar; a bar-close
  run can only act at a close, so the halt arrives up to a bar later, and the
  close it flattens at is the next bar's open. That errs toward showing *more*
  loss than Pine would, not less, which is the direction to be wrong in.
- **The intraday reference is a bar, not the session open.** The day's opening
  equity is taken at the first bar the feed carries for that date. On a feed
  that starts mid-session, that is not where the session started.

## The trade counters, and a ledger to answer them from

Backtrader keeps no record of closed trades: `notify_trade` reports each one as
it happens and then forgets it, and a closed `Trade` has already had its `size`
zeroed. So the ledger is built from the notifications, and only in strategies
that ask for it.

```pinescript
newLoss  = strategy.losstrades > strategy.losstrades[1]
barsIn   = bar_index - strategy.opentrades.entry_bar_index(0)
lastPnl  = strategy.closedtrades.profit(strategy.closedtrades - 1)
```

Three details are load-bearing:

- **`notify_trade` runs before `next()` on the same bar.** A trade that closed
  on this bar is already counted when the body reads the counter — which is
  what Pine does too.
- **`[1]` needs a snapshot taken at the end of the bar**, for exactly that
  reason: by the time the body runs, the counter has already moved. That is
  what makes `strategy.losstrades > strategy.losstrades[1]` mean "did a loss
  just book" rather than nothing at all. Only one bar back is kept; `[2]` is
  refused.
- **The exit price comes from the fill**, not from arithmetic on the P&L.
  Deriving it as `entry + pnl/size` is exact only for a single-entry,
  single-exit, commission-free trade, and silently wrong otherwise.

An out-of-range trade index answers `na`, as Pine does, rather than raising or
— worse — silently counting from the end the way Python's negative indexing
would.

The counters are checked against `TradeAnalyzer` in the test suite: it counts
the same trades from the same notifications by a different route, so agreement
between them is real evidence rather than a restatement.

## `barstate`, and the repaint guard that stops mattering

`barstate.*` asks where the script is in the chart's history, and a bar-close
backtest already knows: `next()` runs once per completed historical bar. So
most of it is a constant.

The one that matters is `barstate.isconfirmed`, which shows up all over
published strategies as a repaint guard:

```pinescript
longSignal  = rawLongSignal and cooldownOk and barstate.isconfirmed
_canEntry   = not _inCooldown and (not confirmClose or barstate.isconfirmed)
```

On a live chart that guard is doing real work — it stops the script acting on a
bar that is still forming and may yet change. On a **historical** bar there is
nothing to guard against, because the script calculates once, on the close.
TradingView's own backtest answers `true` there. So do we, and the guard
correctly collapses to nothing:

```python
signal = ((self.data.close[0] > self._sma_1[0]) and True)
```

That is a translation, not an approximation. The same reasoning fixes
`isnew` (the single calculation is also the first), `ishistory` (every bar is)
and `isrealtime` (no bar is).

`isfirst` and `islast` are different: they are positions in the feed, not
properties of how it is being replayed, so they stay live — `len(self) == 1`
and `len(self) == self.data.buflen()`. Both are parenthesised, because `==`
binds looser than arithmetic and a bare comparison dropped into a larger
expression would quietly mean something else.

The caveat worth stating: if a strategy is *designed* to behave differently
live than in backtest, this collapses that difference. Everything in this
module targets backtesting, so that is the intended reading — but it is the one
place where a converted script is deliberately simpler than the original.

## Functions, and why they are inlined

A user-defined function becomes no Python function at all. Each call site gets
its own copy of the body, with the arguments substituted in:

```pinescript
z(src, len) =>
    m = ta.sma(src, len)
    s = ta.stdev(src, len)
    (src - m) / s

fast = z(close, 20)
slow = z(close, 50)
```

```python
self._sma_1 = bt.indicators.SMA(self.data.close, period=20)
self._standarddeviation_2 = bt.indicators.StandardDeviation(self.data.close, period=20)
self._sma_3 = bt.indicators.SMA(self.data.close, period=50)
self._standarddeviation_4 = bt.indicators.StandardDeviation(self.data.close, period=50)
...
fast = ((self.data.close[0] - self._sma_1[0]) / self._standarddeviation_2[0])
slow = ((self.data.close[0] - self._sma_3[0]) / self._standarddeviation_4[0])
```

That is not a shortcut taken to avoid emitting a `def`. **Pine gives every call
site its own independent series state** — two calls to one function do not
share history the way two calls to a Python function share a closure. Copying
the body per call site is what that rule means here, and a shared `def` would
be the wrong translation.

Inlining also dissolves a problem that has no other answer. A Backtrader
indicator fixes its period when it is constructed, so `ta.sma(src, len)` cannot
be built while `len` is still a parameter. After substitution there is no
parameter: `len` is whatever the call site passed, and the indicator is built
with it. The two `z` calls above become four indicators, correctly, because
they are four different periods.

A body is read as a sequence of assignments ending in one expression, and each
assignment extends the substitution rather than emitting a line. `:=` therefore
works for free — rebinding a local leaves earlier reads holding the earlier
value, which is what sequential assignment means. The last statement carries the
value, whether it is an expression, an assignment, a trailing `if`, or a
trailing `switch`.

### State that survives the bar, per call site

A body may keep `var` state. This is what the JMA, Kalman and supersmoother
filters in every published corpus are made of, and it is why they could not be
converted before:

```pinescript
f_jma(src, len, phase, power) =>
    var float jma = na
    var float e0  = na
    ...
    e0  := (1 - _alpha) * src + _alpha * nz(e0[1], src)
    e1  := (src - e0) * (1 - _beta) + _beta * nz(e1[1], 0)
    jma := nz(jma[1], src) + e2
    jma
```

A pure body folds into one expression. A body with `var` in it cannot — the
state has to be *updated*, in order, once per bar. So it becomes lines in front
of whichever statement asked for the value:

```python
def __init__(self):
    self._f_jma_jma_1 = float('nan')
    self._f_jma_e0_2 = float('nan')

def next(self):
    self._f_jma_e0_2 = ((1 - alpha) * self.data.close[0] + alpha
                        * (self._f_jma_e0_2 if self._f_jma_e0_2 == self._f_jma_e0_2
                           else self.data.close[0]))
    ...
    smooth = f_jma_value_8
```

Each call site gets its own attributes — the same rule that makes inlining the
right translation at all. Two calls to one filter are two filters, and they are
here.

**`e0[1]` and bare `e0` both work, and they are different things.** One
attribute holds one value; which value depends on when it is read. Before this
bar's assignment it still holds the previous bar's — exactly what
`nz(e0[1], src)` is asking for. After the assignment it holds this bar's, which
is what Pine's bare `e0` on the next line means. Emitting the updates in source
order gets both right for free.

Reading `e0[1]` *after* `e0 :=` is the case where that stops working, and it is
refused rather than answered with this bar's number. So is `e0[2]`: one
attribute cannot reach two bars back.

The one placement rule: **a stateful function has to be called from a top-level
statement.** Pine updates the state on every bar wherever the call sits;
emitting the updates under an `if` would update them only on the bars the
condition held, which is a different strategy. That is reported, not emitted.

### What it refuses

- **`varip` in the body** — refused for the same reason `varip` is refused anywhere: it updates on every tick, and a bar-close run has none.
- **Recursion, direct or mutual.** There is nothing to recurse over, and a stack catches `g → h → g` as well as `f → f`.
- **A tuple return**, `[lower, upper, atr]`. It needs a destructuring call site, which is refused in its own right.
- **A body the grammar cannot read at all** — Pine allows several declarations on one line, and a `switch` case whose value is an indented block. Those are skipped and reported by name, exactly as every body was before any of them could be read. A function outside the subset must not fail the whole file.
- **Runaway expansion.** Substitution copies a local once per read, so nesting multiplies. Past 400 nodes the conversion stops and says so: an expression that wide is not what the author wrote, and the body wants real intermediate values instead. The check runs innermost-first, so it stops at the level that crosses the line rather than after building everything above it.

## The two jobs of `if`

Pine spells one keyword two ways. Read for its effect, `if` is a block that runs
statements. Read for its value, the same keyword is a conditional expression
whose arms happen to sit on their own lines:

```pinescript
float agreement = if priceSide != 0.0 and priceSide == maDirection
    1.0
else if priceSide == 0.0
    0.25
else
    0.0
```

Which job it is doing is decided by where it appears: on the right of an `=` or
`:=` it is an expression, anywhere else it is a block. So the block form is
untouched, and the expression form folds into exactly the nesting a ternary or a
`switch` already produces:

```python
agreement = (1.0 if (...) else (0.25 if (...) else 0.0))
```

An `else` is optional. Pine yields `na` when a value-carrying `if` falls off the
end without one, and so does the conversion — `float('nan')`, which `na(x)`
tests the same way it tests any other.

A branch has to be a single expression, because that is all a conditional
expression can hold. One that carries a block of statements is reported —
*"an if used for its value needs one expression per branch"* — rather than
having its first or last line guessed at.

This was the last construct in the corpus that no amount of the rest could get
past: with it, all 17 strategies under `tools/pine_sweep.py --strategies-only`
parse.

## Source it cannot even parse

`convert` does not raise on malformed or unrecognised syntax. It returns a
result like any other, with the parse error in `unsupported` and a placeholder
strategy as `code`:

```python
result = convert(broken_source)
result.ok            # False
result.unsupported   # ["could not parse: expected 'NEWLINE' but found 'x' on line 32"]
```

This matters for the corpus loop at the top of this page. Raising would kill
that loop on its first odd script and tell you nothing about the rest — which is
exactly what the module exists to avoid.

## Computed values as indicator sources

Backtrader overloads arithmetic on line objects, so a composition of lines is
itself a line and can be handed straight to an indicator. That is what makes
the commonest shape in published scripts work:

```pinescript
esa = ta.ema(hlc3, chLen)
d   = ta.ema(math.abs(hlc3 - esa), chLen)
ci  = (hlc3 - esa) / (0.015 * d)
wt1 = ta.ema(ci, avgLen)
```

```python
self._ema_1 = bt.indicators.EMA(((self.data.high + self.data.low + self.data.close) / 3), period=self.p.chLen)
self._ema_2 = bt.indicators.EMA(abs((((self.data.high + self.data.low + self.data.close) / 3) - self._ema_1)), period=self.p.chLen)
self._line_ci_3 = ((((self.data.high + self.data.low + self.data.close) / 3) - self._ema_1) / (0.015 * self._ema_2))
self._ema_4 = bt.indicators.EMA(self._line_ci_3, period=self.p.avgLen)
```

`ci` is assigned as an ordinary value, and it is still computed per bar in
`next()` as one — but when something asks for it as a *source*, the same
expression is lowered a second time as a line. Both spellings compute the same
number; there is a test asserting they agree to twelve significant figures,
because a divergence between them would mean a strategy saw different values
depending on whether anything happened to want a line.

Note `close[1]` becomes `close(-1)`, the line delayed by a bar, and not
`close[-1]`, which would be a *read* — and `__init__` runs before there is a
bar to read.

### When a value will not become a line

Promotion is refused wherever one line would be a lie about what the script
means, and each of these is a separate rule:

- **assigned more than once** — two assignments are two different values
- **assigned inside an `if`** — it holds a value on some bars and not others, where a line is computed on every one
- **reassigned with `:=`, or a `var`** — the same objection, spread over time

### History without a line

`inSess = not na(time(timeframe.period, sess))` cannot be promoted — `time()`
is a per-bar read of the feed's clock, not a line — yet `inSess and not
inSess[1]` is Pine's standard "first bar of the session". So history of a
computed value has a fallback: since the definition holds on every bar, the
previous bar's value is the same expression with every bar-relative read
shifted one bar further back — `time()[1]` reads the previous bar's stamp,
`close` becomes `close[1]`, and so on, recursively through other computed
values. Only the pure per-bar subset shifts (prices and lines, constants and
params, `time()`, `na`/`nz`, the math builtins); a definition reading a
`var` or a trade counter holds only its current value, so that history stays
refused rather than answered with today's state.

### A conditional needs a function, not a line operation

`bt.If` is a line operation, so it computes **both** of its branches on every
bar. That is fine for arithmetic and wrong for a guard:

```pinescript
ci = d != 0 ? (hlc3 - esa) / (0.015 * d) : 0.0
```

The conditional is there so the division does not happen when `d` is zero, and
Backtrader's line division raises `ZeroDivisionError` rather than yielding
`na`. Lowered with `bt.If`, the guard stops working and the run dies.

So a ternary becomes a `PineExpr` instead — the expression as an ordinary
Python function of the current bar's values, where `if`/`else` is lazy again:

```python
self._expr_3 = PineExpr(
    self._ema_2, ((self.data.high + self.data.low + self.data.close) / 3), self._ema_1,
    func=lambda a0, a1, a2: (((a1 - a2) / (0.015 * a0)) if (a0 != 0) else 0),
)
```

Only the **leaves** become inputs — a price series, an indicator, a promoted
line. Everything above them stays in the function, which is the point: hoisting
`x / d` into an input would compute it every bar again. Because the inputs are
lines, Backtrader still works out the minimum period and the evaluation order.

### Why both custom indicators write `once`

Backtrader runs indicators vectorised by default (`runonce=True`) and stepwise
on request. An indicator that defines only `next` gets its vectorised pass
*emulated*: Backtrader replays `next` while advancing the inputs by hand.

That emulation reads an input a bar out of step when the input is itself an
indicator carrying a minimum period. Not an error — just different numbers, on
some bars, in the mode nearly everyone runs. `PinePivot` over `ta.sma(close,
10)` missed three pivots out of a hundred bars that way.

So both `PineExpr` and `PinePivot` write `once` explicitly, and the test suite
runs a strategy using both under `runonce=True` and `runonce=False` and asserts
every bar agrees.

## `varip`, and why it is `var` here

`varip` differs from `var` only on a realtime bar, where it is not rolled back
between ticks. A backtest has no realtime bars, and Pine's own documentation
is explicit on both halves of what follows: *"varip will behave similar to var
on historical bars"*, and *"because varip only affects the behavior of your
code in the realtime bar, varip behavior cannot be simulated on historical
bars"*.

So on the bars a backtest actually runs, reading `varip` as `var` is what Pine
itself does, rather than an approximation of it — and the behaviour being
protected cannot be reproduced by anything, in Pine or here.

This was refused for a long time, and the refusal bought nothing while costing
a cascade: the name never entered scope, so every later read and every
reassignment reported separately. Three corpus strategies spent roughly
seventy per cent of their gap lists on it.

| script | gaps before | after |
| --- | --- | --- |
| `vein_reversal_labeler` | 58 | 16 |
| `wavetrend_v4` | 80 | 18 |
| `wavetrend_base` | 73 | 15 |

It is recorded in `result.notes`, not passed over in silence: the script was
written by someone who wanted intrabar behaviour somewhere, and a live run is
where they would not get it.

## Choosing between indicators before the first bar

The commonest shape in the corpus after the date window is a `switch` that
picks a moving average, usually reached through a function:

```pinescript
f_smooth(src, length, mode) =>
    switch mode
        'SMA' => ta.sma(src, length)
        'RMA' => ta.rma(src, length)
        'HMA' => ta.hma(src, length)
        =>       ta.ema(src, length)
wt1 = f_smooth(close, avgLen, mode)
```

`mode` is an input, or the chart's timeframe. Neither moves during a run, so
the choice is made once, in `__init__`:

```python
self._choice_1 = (bt.indicators.SMA(self.data.close, period=self.p.avgLen)
                  if (self.p.mode == 'SMA')
                  else (...))
wt1 = self._choice_1[0]
```

That it is a conditional **expression** is the substance, not the style. A
Python conditional expression evaluates only the branch it takes, so only the
chosen average is built — and that matters more than it looks:

> Backtrader's minimum period is the maximum over every indicator the strategy
> *holds*, read or not. A 5-period average built beside an unused 80-period one
> produces nothing until bar 80.

So building all four branches and selecting between them would move the bar a
converted strategy starts trading on, by an amount that depends on which
averages the script happens to offer. Nothing in the generated source would
look wrong. The test for this runs the same strategy twice, on the fast mode
and the slow one, and asserts the first bar is 5 and 80 respectively.

Two things deliberately do not take this path:

- **A conditional between numbers.** `mode == 'A' ? 5 : 20` is a number, not a
  line, and a number cannot be read at `[0]`. It stays a scalar — as does the
  mixed case, where which branch is a line depends on the condition.
- **A per-bar guard.** `d != 0 ? x / d : 0` has a condition that moves with the
  bars, so it cannot be settled in `__init__` at all. It stays a `PineExpr`,
  lazy, for the reason given under "A conditional needs a function, not a line
  operation".

A length chosen the same way resolves the same way: `ta.highest(high, mode ==
'Fast' ? 5 : 20)` reads its period once, when the indicator is built, which is
exactly when the condition can be answered.

### A mode that cannot be built

Some branch will not be a line at all — a stateful user function such as a
JMA, which is an iterative per-bar recursion rather than an expression. That
does not make the strategy unconvertible. Which branch is taken is settled
when the feed is attached, and Pine would never evaluate the branch not
chosen, so the modes that *do* build are worth having.

The branch becomes a raise instead of a line:

```python
self._choice_1 = (bt.indicators.SMA(...) if (self.p.mode == 'SMA')
                  else (bt.indicators.EMA(...) if (self.p.mode == 'EMA')
                        else self._pine_no_mode('f_jma_value')))
```

Selecting that mode fails by name, from `__init__`, before a single bar is
priced — a backtest that stops part way through is worse than one that never
starts, because it produces numbers. Every other selection runs untouched.

This is the one place `result.ok` does not mean "every path works". It means
every path the inputs can reach *as converted* either works or stops the run
loudly, which is why the reading is recorded in `result.notes` rather than
passed over.

## A known simplification

The same boundary decides what can be an indicator's length. A Backtrader
indicator fixes its period when it is constructed, so the length has to be a
number or a param:

```pinescript
len = switch mode          // computed per bar
    "Fast" => 5
    => 20
ma = ta.sma(close, len)    // reported, not guessed
```

That is refused with *"could not resolve its length argument"*. Real scripts
mostly use a `switch` for a multiplier or a condition, where it works fine; a
switched *length* needs the branch written into the Backtrader params instead.

## Verification

Unlike the scraping collectors, this module can be checked end to end locally,
and is. The test suite compiles generated strategies and runs them through a
real `cerebro` on synthetic bars, asserting that they execute, place orders, and
respond to parameter overrides. "It converted" is never taken to mean "it works".

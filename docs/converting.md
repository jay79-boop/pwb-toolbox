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
| `ta.crossover/crossunder/cross` | `CrossOver` plus a direction test |
| `ta.change(src, n)` | `src[0] - src[-n]` |
| `close`, `open`, `high`, `low`, `volume` | `self.data.<line>[0]` |
| `hl2`, `hlc3`, `ohlc4` | the arithmetic spelled out |
| `close[3]` | `self.data.close[-3]` |
| `and` / `or` / `not`, comparisons, arithmetic | the Python equivalents |
| `cond ? a : b` | `a if cond else b` |
| `switch` with or without a subject | the chain of conditionals it means |
| `if` / `else if` / `else` | the same, inside `next()` |
| an `if` read for its value | the conditional expression it means |
| `f(a, b) => ...` and its call sites | the body, inlined where it is called |
| a parameter with a type, a default, or both | the type dropped, the default kept |
| an expression split over several lines | joined before parsing |
| `strategy.entry(..., strategy.long/short, qty=)` | one entry per direction, reversing — see below |
| `strategy.closedtrades`, `opentrades`, `wintrades`, `losstrades`, `eventrades` | counters kept from `notify_trade` |
| any of those with `[1]` | the previous bar's value |
| `strategy.closedtrades.entry_price/exit_price/profit/size(i)` | a ledger built as trades close |
| `.entry_bar_index(i)`, `.exit_bar_index(i)` | likewise, on the same bar numbering as `bar_index` |
| `strategy.close`, `strategy.close_all`, bare `strategy.exit` | `self.close()` |
| `strategy.exit(..., stop=, limit=)` | an OCO stop/limit pair, maintained |
| `strategy.position_avg_price` | `self.position.price` |
| `strategy.position_size` | `self.position.size` |
| `bar_index` | `len(self)` |
| `barstate.isconfirmed/isnew/ishistory` | `True` — see below |
| `barstate.isrealtime` | `False` |
| `barstate.isfirst`, `barstate.islast` | a position in the feed, so still live |
| `var x = <literal>` | an attribute set once in `__init__` |
| `x := value` | assignment, writing through to the attribute for a `var` |
| `x += y`, and `-=` `*=` `/=` `%=` | desugared to `x := x + y` |
| `na(x)` | the NaN test `x != x` |
| `request.security(syminfo.tickerid, tf, expr)` | a read from a resampled `self.datas[n]` |
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

- `request.security` on a symbol other than `syminfo.tickerid` — a second instrument is a data-loading decision
- `request.security(..., lookahead=barmerge.lookahead_on)` — reads a bar before it closes
- `varip` — updates on every tick, and a bar-close run has no ticks
- `var x = <expression>` — only a literal initial value works; see below
- arrays, matrices, maps and `type` blocks — all reported, so the rest of the script is still diagnosed
- a user-defined function that recurses, returns a tuple, expands past the inlining limit, or keeps state and is called from inside an `if` — see below
- `for` / `while` loops
- a `switch` used as a statement rather than for its value — that is a side-effecting block
- an `if` read for its value whose branch carries more than one expression — see below
- tuple destructuring, e.g. `[macd, signal, hist] = ta.macd(...)`
- `strategy.exit` carrying `loss`, `profit` or `trail_*` — distances in ticks, and tick size belongs to the instrument, not the script
- `pyramiding` above 0 — Backtrader nets one position per feed; see below
- `strategy.closedtrades.max_runup/max_drawdown/commission/entry_id/exit_id/exit_comment` — Backtrader records none of them
- more than one bar of history on a trade counter — only the previous bar is kept
- any identifier or call the converter does not know

Reported separately in `result.ignored`, because dropping them changes nothing
about how the strategy trades: `plot`, `plotshape`, `bgcolor`, `hline`, `fill`,
`alertcondition`, `label.new`, `line.new`, and friends — along with the drawing
constants they consume, such as `color.green` and `shape.triangleup`. A colour
cannot change a trade, so refusing one would fail a conversion over nothing.

Both lists are also written into the generated class's docstring, so a
converted file explains its own gaps without needing the original result object.

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
resample_spec = (
    (bt.TimeFrame.Weeks, 1),
)

def __init__(self):
    if len(self.datas) < 2:
        raise ValueError("HTFTrend needs 2 data feeds; see resample_spec")
    self._ema_1 = bt.indicators.EMA(self.datas[1].close, period=4)
```

Wiring it up is then mechanical:

```python
cerebro.adddata(data)
for timeframe, compression in HTFTrend.resample_spec:
    cerebro.resampledata(data, timeframe=timeframe, compression=compression)
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
`resample_spec`, not the param.

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

## A known simplification

Only a bare `ta.*` call on the right-hand side of an assignment is hoisted into
`__init__`. Compound expressions such as `spread = maFast - maSlow` are computed
in `next()` from indexed values instead of becoming a Backtrader line.

That is correct for evaluating conditions, which is what strategies do with
them, and it avoids a class of bugs where a partially-lowered expression looks
like a line object but is not one. The cost: `spread[1]` — history of a computed
value — is refused rather than supported, because it would need a real line
object to be meaningful.

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

# Pine strategies

TradingView strategies, kept here as source so they are reviewable and diffable rather
than living only in a Pine Editor tab. Nothing under `pwb_toolbox/` imports them.

## `purpose_driven_15m_reversal.pine`

The Purpose Driven Trader's 15-Minute Reversal. Written for **`CME_MINI:NQ1!`, 15-minute
timeframe, extended (electronic) hours ON**.

### The chart has to be right or the strategy does nothing

The whole setup is anchored to the **09:15 America/New_York bar**, which only exists on a
chart showing the electronic session. On a regular-hours chart that bar is absent, and
the strategy is written to take **zero** trades rather than silently anchor to 09:30 and
trade something else. It draws a red label on the chart saying so.

Over roughly ten months of NQ 15m data with extended hours on, expect **~60–70 trades**.
Materially fewer is the chart, not the rules.

### Rules

| | |
| --- | --- |
| Candle 1 | the 09:15 15-minute bar; its high and low are the day's opening range |
| Scan | from 09:30, the day's **first** failure swing that agrees with the trend filter |
| Trend filter | 60-period SMA taken from the **daily** timeframe |
| Long | bar dips below Candle 1's low, **closes** back above it, high stays under Candle 1's high, price above the SMA |
| Short | the mirror |
| Confirmation | the **close** confirms — never the wick, never intrabar |
| Commitment | swings against the SMA are skipped and do not use up the day; the first aligned one commits the day, and there is no second setup even if the entry never fills |
| Entry | stop order at the setup bar's **body** extreme (long = higher of open/close), fillable only on a later bar |
| Target | Candle 1's opposite extreme |
| Stop | entry-to-target distance ÷ 2.4, on the other side |
| Size | one contract, one trade per day, no adding |
| Calendar | Monday–Thursday; Fridays skipped |
| Flat by | 15:55 — cancel anything unfilled, close anything open |

Inputs cover Candle-1 time, SMA length, SMA timeframe, SMA filter on/off, skip-Fridays,
flatten time and reward:risk.

### Two things worth knowing before reading the backtest

- **The flatten fills at 16:00, not 15:55.** Pine fills a market order at the open of the
  bar *after* the one that generated it, so the flatten is signalled on the 15:45 bar
  and fills at 16:00. `process_orders_on_close = true` would move it to 15:45's close,
  but it would also let the entry stop fill on the setup bar itself — which the rules
  forbid — so it stays off.
- **Defaults include costs**: $2.50 per contract per side and 1 tick of slippage. They
  do not change the trade count; set them to zero in the strategy properties if you want
  the gross figures.

## `vwap_strategy.pine`

The three VWAP setups from `pwb_toolbox/backtesting/vwap.py`, as one TradingView
strategy: **fade** (a close outside the ±kσ band enters toward VWAP — the setup
with empirical support), **pullback** (touch of VWAP from the prior close's side,
ride toward the far band), and **cross** (close crosses VWAP, stop-and-reverse —
the retail favorite, kept as a control because the published sweeps found it
worthless; distrust any run where it wins).

Two things to know before reading it:

- **VWAP is accumulated by hand from the session input (default `0930-1555`),
  not taken from `ta.vwap`.** `ta.vwap` resets at the instrument's session
  boundary — 18:00 ET on CME futures — while the fade numbers this implements
  are quoted off a 09:30 anchor. Hand accumulation keeps this file and the
  Python strategy computing the same line, which is the whole point of having
  both.
- **The Python side is the executable reading.** Pine cannot run in a
  container, so `tests/test_vwap.py` pins every rule (band math, session
  reset, the gates, the flatten) against hand-built bars through a real
  cerebro, and `tools/vwap_lab.py` is where the setups face costs and the
  two-vendor noise floor. Change a rule in one file, change it in the other.

Inputs cover the setup selector, band and stop widths in σ, the session, warmup
minutes, and the four confirms (relative volume, first-30-minute day-type, MA
side, RSI control — each off by default so every gate's cost is measurable).
Defaults include costs: $2.50 per contract per side and 1 tick of slippage.
The anchored-VWAP (swing) variant is Python-only for now: `vwap_lab --anchor`.

### Verifying a change

`tools/reversal_15m_sim.py` is an executable second reading of the same rules, and
`tests/test_reversal_15m_sim.py` pins each clause against hand-built bars. It cannot
compile Pine, so it will not catch a syntax error — it catches the rule being wrong,
which is the expensive kind. Change a rule in one place, check it in the other:

```bash
pytest tests/test_reversal_15m_sim.py -v
python tools/reversal_15m_sim.py bars.csv --rr 2.4      # bars.csv: timestamp,open,high,low,close
```

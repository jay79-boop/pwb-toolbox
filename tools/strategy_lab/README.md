# Strategy Lab

A live dashboard for strategy test runs. Post a backtest at it and the page
updates itself: stat tiles, equity curve, drawdown, R-multiple distribution,
session funnel, exit routes, weekday and hour breakdowns, and every trade in a
sortable table.

Standard library only — nothing to install, no build step.

```bash
python -m tools.strategy_lab                 # http://localhost:8771
python tools/reversal_15m_sim.py bars.csv --symbol CME_MINI:NQ1! --post
```

The page picks the run up within a few seconds. Leave it open in a tab while you
work.

## Why it exists

`pwb_toolbox/performance/` already computes the metrics, but it renders them
through matplotlib into files — fine for a report, useless for watching a run
land. This is the screen: one place that every strategy test in this repository
reports into, so results accumulate and compare instead of scrolling past in a
terminal.

## Connecting a session to it

The contract is a **run record** — one JSON object. Anything that can write JSON
can feed the lab; nothing has to import this package.

```json
{
  "schema": "pwb.strategy-run/1",
  "id": "15-minute-reversal-13a58e",
  "strategy": "15-Minute Reversal",
  "symbol": "CME_MINI:NQ1!",
  "timeframe": "15m",
  "session": "extended",
  "period": { "start": "2025-10-06", "end": "2026-04-23" },
  "params": { "reward_risk": 2.4, "sma_length": 60 },
  "point_value": 20.0,
  "funnel": { "days": 144, "days_with_candle_1": 116, "days_committed": 39, "trades": 38 },
  "trades": [
    {
      "day": "2026-01-13", "direction": 1,
      "entry_ts": "2026-01-13T11:30", "entry": 20430.27,
      "exit_ts": "2026-01-13T12:30", "exit": 20468.67,
      "target": 20468.67, "stop": 20411.07,
      "reason": "target", "r": 2.0, "points": 38.4
    }
  ]
}
```

Only `schema`, `id`, `strategy` and `trades` are required, and a trade needs only
`day`, `direction` and `r`. Everything else enriches the page — `point_value`
turns R into dollars, `funnel` draws the conversion chart, `params` captions the
run.

From Python:

```python
from tools.strategy_lab import build, post
post(build("My Strategy", trades, symbol="CME_MINI:NQ1!", params={"rr": 2.4}))
```

Or `curl -X POST http://localhost:8771/api/runs -d @run.json -H 'Content-Type: application/json'`.

**An id is content-keyed, not time-keyed.** Re-running the same backtest updates
its own entry; changing a parameter, a bar or a trade makes a new one. That keeps
a parameter sweep from burying the run you care about, and it is why posting the
same thing twice does not produce twins. (It also fixed a real bug: with a
second-resolution timestamp for an id, two runs posted in the same second landed
on the same file and the first vanished.)

## Reading it away from the machine

```bash
python -m tools.strategy_lab --export lab.html    # standalone snapshot, opens anywhere
python -m tools.strategy_lab --host 0.0.0.0       # reachable from a phone on the network
```

The export bakes the runs into the page and leaves out the tag that makes it poll,
so the identical file works over `file://` or published as an Artifact.

## What the page tells you that a metric table does not

- **The session funnel** — sessions in range → had Candle 1 → committed → filled.
  A strategy that took no trades is explained here rather than left ambiguous: no
  Candle 1 anywhere means the chart was wrong, not the rules.
- **Trades that lost more than 1R** are called out by name. A stop-out should cost
  about 1R; beyond that the trade escaped its stop, which is a risk-control bug
  and not a bad market.
- **R against dollars.** Fixed contracts with a moving stop means each trade risks
  a different amount, so a run can be R-positive and dollar-negative. The page
  says so when the two disagree in sign.
- **Small samples are labelled.** Under about 30 trades the win rate and profit
  factor are noise, and the page says that instead of presenting them as a verdict.

## Layout

| File | |
|---|---|
| `store.py` | validated records on disk, one JSON file per run |
| `server.py` | the HTTP shell: dashboard at `/`, runs at `/api/runs` |
| `record.py` | building records, and posting them |
| `../../static/strategy-lab.html` | the dashboard |
| `../../static/strategy-lab-stats.js` | the arithmetic, inlined into the page |

The stats module is a separate file that the page inlines verbatim, the same
arrangement `option-lab.js` has with the trade journal: edit the module, run its
tests, never patch the inlined copy. `tests/test_strategy_lab.py` requires those
numbers to agree with `pwb_toolbox.performance.trade_stats`, so the dashboard and
the package cannot drift into disagreeing about the same trades.

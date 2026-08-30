# desk_levels: market structure from bars

`tools/desk_levels.py` reads session levels, the prior trading day's range,
unmitigated fair value gaps and their order blocks off **bar data**, and
renders a candlestick image headless. It exists so the desk agent's `premarket`
and `journal` jobs stop needing TradingView Desktop — which is what forced
their scheduled tasks to keep a desktop-bound logon type. The reasoning is in
`docs/decisions/2026-08-29-the-jobs-stopped-needing-a-desktop-so-the-tasks-stopped-needing-one.md`.

```bash
python tools/desk_levels.py levels NQ=F --markdown
python tools/desk_levels.py levels NQ=F --json structure.json
python tools/desk_levels.py levels NQ=F --csv bars.csv --markdown   # offline
python tools/desk_levels.py chart NQ=F --out shot.png --mark entry=29200 --mark stop=29050
```

`--csv` reads a file in the shape `tools/fetch_bars.py` writes and makes the
whole command offline. Without it, bars are fetched from yfinance — the only
network call in the file, and one no test reaches.

## What it measures

| output | definition |
| --- | --- |
| prior day range | high/low/mid/open/close of the previous **trading** day |
| session levels | high/low/open/close for Asia, London, NY am, NY pm |
| fair value gaps | three-bar: `bar[i-1].high < bar[i+1].low`, and the mirror |
| order block | the last opposing candle before the leg that opened the gap |
| distance | every level's offset from last price, in **basis points** |

Sessions use ICT killzone windows in exchange-local time: Asia 20:00–00:00,
London 02:00–05:00, NY am 09:30–12:00, NY pm 13:30–16:00.

## The two traps it is built around

Both from `docs/backtesting.md`, and both produced confidently wrong answers in
this repo before.

**Timestamps.** The only input shape accepted is **naive UTC**, which is what
`fetch_bars.py` writes. Session windows are built in exchange-local time
through `zoneinfo`, per calendar day, so the tz database handles DST rather
than a flat offset. A flat offset is right in January and an hour out in July,
which moves the whole session window for eight months of the year — the failure
that turned an eight-year backtest from +39 points into +7.

`tests/test_desk_levels.py` plants the same 02:15 spike in January and in July
and requires both to be found as the London high. Replacing the `zoneinfo`
conversion with a constant five-hour offset passes January and fails July; that
was run, and it is what the test's docstring records. A second test asserts the
two months genuinely resolve to different UTC hours, so the pair cannot pass
vacuously.

**The day boundary is not midnight.** CME index futures run 18:00 ET to 17:00
ET, so "yesterday" for NQ is not a calendar date. The boundary is explicit and
configurable and is never inferred. A premarket run on 2026-08-29 hit the
consequence of getting this wrong: the NQ daily bar and the hourly bars
disagreed on the Wednesday and both were right, because the daily close was the
16:00 settlement while the session traded on to 17:00.

## What it refuses to do

Following the house rule that a lab refuses rather than repairs:

- **A session with no bars is `None`.** Never interpolated, never borrowed from
  a neighbouring session. An empty overnight is a real answer.
- **Fewer than three bars raises.** There is no structure to read.
- **A stale last bar is flagged**, in the JSON, in the markdown and on the face
  of the rendered chart. Past six hours the output says `STALE` and the note
  says the live price is unusable while the settled levels still hold.
- **A bar stamped in the future is named as a fault.** That is clock skew or a
  feed whose stamps are not UTC, and it makes every level suspect. Found by
  smoke-testing the CLI, which printed `-212 min old` and said nothing.
- **A naive timestamp is never localised to a guessed zone.** It is read as
  UTC, full stop.

## What it will not claim

**It does not audit its feed.** One vendor, and probably delayed.
`docs/backtesting.md` records two feeds of the same index disagreeing by 284bp
over eight years while correlating 0.93 year over year — the dangerous case,
because the feeds look like they agree. Nothing here runs `noise_floor()`. A
level a trade is actually placed against wants a second source first.

**It does not rank setups by quality**, only by distance. "At the door" and
"far away" is a fact about price; "good" is not, and this tool has no basis for
the second. The gameplan's judgement is the agent's.

**A rendered chart is not the owner's chart.** It carries none of their
drawings or indicator settings. What it buys instead is reproducibility: the
same bars produce the same picture every time, so a thesis can be re-read years
later against the data that framed it rather than against a PNG nobody can
regenerate.

## Which parts of the lab standard apply

`.claude/skills/lab-standard` lists eight. Stated honestly rather than claimed
wholesale:

- **Pure core, dirty edge** — yes. Every function takes a DataFrame; the fetch
  and the render are at the boundary, and no test touches the network.
- **Convict and acquit** — yes, matched pairs throughout: a planted gap must be
  found and featureless bars must yield none; a planted spike must be the
  session high in both DST offsets; a stale bar must be flagged and a fresh one
  must not.
- **No network, no broker, no key** — yes, enforced by a test that fails if
  `yfinance` or `matplotlib` reaches module scope.
- **Refuse rather than repair** — yes, listed above.
- **Price the fishing expedition** — **does not apply.** This reports
  structure; it makes no statistical claim, so there is no family of tests to
  correct across. Saying otherwise would be dressing.
- **Give it something to beat** — **partially.** There is no benchmark for a
  level. The nearest equivalent is that a gap is only reported while
  unmitigated, checked against subsequent price, and distance is a number
  rather than a judgement.
- **The plumbing** — this file, plus entries in `CLAUDE.md` and
  `docs/layout.md`. No data directory: nothing is cached.
- **Report the honest negative** — yes. "No unmitigated fair value gaps in this
  window" is printed as a result, not smoothed over.

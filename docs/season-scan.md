# The season scan

Calendar rotation, measured instead of remembered. `tools/season_scan.py`
batches any universe of tickers — the 11 sector ETFs, the index baselines,
crypto, your own names — and asks of every ticker-month cell: is this a
pattern, or is it memory?

## Three gates, because a grid is a fishing expedition

Scan 15 tickers by 12 months and ~9 "patterns" appear by pure luck. So a
cell is **CONVICTED** only when it clears all three:

1. **Permutation** — its mean beats within-year reshuffles of the
   ticker's own monthly history (2,000 minimum, scaled up with the size of
   the grid: BH admits the single best of n cells only at p <= q/n, and the
   test's floor of 1/(B+1) must sit below that bar or a lone true pattern
   is blocked by resolution — the first full-universe run hit exactly
   this). The shuffle stays inside each year, so
   2008 remains a terrible year with its damage spread across its own
   months; only the calendar alignment is destroyed. (A circular shift of
   the whole series — the obvious null — is wrong twice over: shifts by
   multiples of 12 land the calendar back on itself, and every other shift
   maps all of a month's slots onto one single other month, collapsing the
   null to eleven values. The scan's first draft had exactly this bug.)
2. **Split-half** — same sign in the older half of the years and the newer
   half, independently. A pattern that died in 2010 is history.
3. **FDR** — Benjamini–Hochberg across every cell scanned, q=0.10.

Cells at raw p<0.05 that fail a gate are **CANDIDATES**, listed with the
reason — they are what almost fooled you. Everything else is noise and is
shown faintly so the eye calibrates.

**Folklore is judged differently, and better.** Sell-in-May, September
weakness, the January effect, the energy spring run: these were stated
before we looked, so each faces its own one-sided test with no FDR — and
gets a public verdict, HELD or FAILED, on this data. The failed list is
the discipline: the things to stop believing.

Crypto is included because it was asked for and effectively cannot convict:
ten years cannot clear the 15-year gate, and the report says so rather than
quietly waving it through.

## What comes out

`report` writes three artifacts to `season/`:

- **`season-report.html`** — self-contained, opens from `file://`: the
  now-window screener (in season / entering / leaving, this week), the
  ticker-by-month heatmap (the table *is* the chart, so it is also the
  accessible view; conviction is carried by bold + outline and printed
  values, never by tint alone), the average-year paths with older and newer
  halves overlaid so a dead pattern is visible as two lines that disagree,
  folklore verdicts, and the candidates table.
- **`tradingview-watchlist.txt`** — TradingView's `###section` import
  format, most actionable section first, every symbol placed once. Import:
  TradingView → Watchlist → ⋯ → Import list. TradingView cannot auto-sync
  a file; re-import after a refresh. (True auto-push would ride the
  `docs/tradingview-mcp.md` setup from a local session — a later project.)
- **`season.json`** — the scan for other tools. `context SYMBOL` reads it
  and answers "where does today sit in this ticker's year?" — the line the
  pre-trade pack and the desk agent quote when a position is opened against
  its own season.

`fetch` pulls max-history daily closes (owner's machine; the cloud proxy
blocks Yahoo). Add your own names in `season/universe.txt`, one per line.

## Commands

```bash
python tools/season_scan.py fetch                # universe daily closes
python tools/season_scan.py report               # scan + all three artifacts
python tools/season_scan.py watchlist            # rewrite the watchlist only
python tools/season_scan.py context XLE          # today's seasonal position
```

Everything statistical is pure and tested on synthetic data with planted
effects — including a planted *dead* pattern that must not convict and pure
noise whose false-positive rate is checked (`tests/test_season_scan.py`).
Past seasonality is a tendency, not a promise; nothing here places trades,
and every candidate still owes the pre-trade pack everything it owes.

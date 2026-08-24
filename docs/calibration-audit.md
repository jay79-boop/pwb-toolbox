# The calibration audit

*"When Black-Scholes said 30% touch, how often did it actually touch?"*

Every option decision here leans on two model numbers: the chance price
**finishes** beyond a level, and the chance it ever **touches** one. Both
come out of a lognormal with constant volatility, which is a convenient
fiction — thin tails, one volatility, continuous trading. `tools/calibration_audit.py`
measures the fiction against years of real daily bars and reports where it
misses.

Run it before sizing anything off a probability from the ladder:

```bash
python tools/calibration_audit.py                    # season/data, 21-day horizon
python tools/calibration_audit.py --symbols SPY --horizon 5
python tools/calibration_audit.py --json audit.json
```

It reads the same daily bars `season_scan.py fetch` already writes into
`season/data`. Nothing here touches the network.

## How a row is built

For each non-overlapping window, the barrier is placed **k standard
deviations away** (k·σ·√T) rather than at a fixed percentage. That one
choice does most of the work:

- the model's probability is then the *same* for every window in a row, so
  each row is an exact binomial test rather than an approximation of one;
- the rows form a reliability curve by construction — 0.5σ is a ~62% touch,
  2.0σ a ~5% one.

Windows never overlap, because overlapping ones multiply the apparent sample
without adding information, which is how a calibration study convicts itself
of significance it never had. Benjamini-Hochberg runs across the sixteen
rows.

## Two volatility modes, and one that is deliberately missing

- **`traded`** sets the barrier from trailing realized volatility — the
  number you could actually have used at the time.
- **`static`** uses one volatility for the whole record. It knows the
  future, so it is a benchmark rather than something tradeable: if `static`
  misses rows that `traded` gets right, keeping a live volatility estimate
  is earning its keep.
- **The window's own realized volatility is not on offer.** Dividing a move
  by the volatility that move helped produce is self-normalization, and it
  shrinks tails mechanically — it would report a fat-tailed series as
  thin-tailed. The mode raises rather than misleads.

Probabilities are computed at **rate 0**, the same driftless assumption
`touch_probability` makes, so a real equity drift shows up as a *finding*
(up barriers over-hitting while down barriers under-hit) instead of being
absorbed into the model.

## Two things to expect before you see them

**Near touch barriers under-hit, and it is not a discovery.** The reflection
principle assumes price is watched continuously; a daily bar is not
continuous. A half-sigma barrier that a path crosses and recrosses inside
one session is touched less often in daily data than P(touch) = 2·P(finish)
predicts. Distant barriers, which take a real move to reach, are barely
affected — so the far rows are where a genuine fat tail shows up.

**Touch is judged on intraday highs and lows** when the file carries them,
and on closes when it does not. Closes alone can only understate touching,
and the report says which reading it used.

## The caveat that never gets dropped

Windows do not overlap, but the four barrier distances share a window and
tickers scanned together share a market. The p-values therefore run
optimistic against cross-sectional correlation. Run a single ticker for the
clean version, and treat one marginal row among sixteen as nothing.

## What is tested

`tests/test_calibration_audit.py` runs the audit on synthetic data with
known structure:

- lognormal with constant volatility → **nothing convicted**, and every row
  lands close rather than escaping on a weak test. An audit that convicts
  its own generating process is worthless;
- a fat-tail mixture → the 2σ rows over-hit and are convicted;
- regime-switching volatility → the static benchmark misses rows the
  trailing estimate gets right;
- the binomial test against a hand-computable case, and the closes-only
  touch reading against the same series with wicks.

The two probability functions live in `pwb_toolbox/options/probability.py`
and are pinned against their `static/option-lab.js` counterparts to 1e-9
(`tests/test_option_lab.py`), so the journal, the ladders and this audit
cannot end up quoting different odds on the same contract.

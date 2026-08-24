---
name: lab-standard
description: The construction standard behind this repo's measurement labs (`backtest_lab`, `season_scan`, `calibration_audit`, `night_lab`, `crypto_scan`, `prop_sim`, `spec_desk`, `kronos_lab`, `spend_watch`) — pure core, convict-and-acquit tests on planted synthetic data, no network in tests, a `docs/` protocol, a gitignored data dir. Load before adding or extending any tool under `tools/` that measures, scores, scans, screens or backtests.
---

# Building a lab to the house standard

A dozen tools here were built to this shape. It is written down because it was
re-derived from memory each time, and the ones that missed a piece needed a
follow-up pull request to add it.

The standard exists to answer one question: **would this tool tell me I was
wrong?** A lab that can only confirm is not a lab.

## Before you build one: check it does not exist

`docs/layout.md` is the full inventory — every tool here, what it decides and
what it refuses to claim. A dozen of these labs overlap at the edges, and the
cheaper move is usually a flag on an existing one. Read it first.

## The eight things

### 1. Pure core, dirty edge

The arithmetic is plain functions over plain data. Network, files, clocks and
API keys live at the boundary and are passed in. This is what makes the next
item possible at all, and it is why `tests/test_season_scan.py` and
`tests/test_spec_desk.py` can test real behaviour with no fixtures.

### 2. Convict *and* acquit, on planted synthetic data

Every lab that makes a judgement gets a matched pair of tests: one where you
planted the effect and the tool must find it, one where there is nothing and
the tool must stay silent. The second is the one that catches a lab that
convicts everything.

The real pairs, copy the pattern:

```
test_a_planted_month_shows_a_tiny_pvalue      / test_white_noise_shows_no_conviction_worthy_pvalue
test_a_real_persistent_effect_is_convicted    / test_noise_is_never_convicted
test_split_half_agrees_on_a_persistent_effect / test_split_half_catches_a_dead_pattern
```

`tests/test_calibration_audit.py` does the same against a model rather than a
series: synthetic data the model is *right* about (must convict nothing) and
data it is *wrong* about (must convict the far barriers).

### 3. No network, no broker, no key — in any test

House rule, not a preference. `ib_insync` runs against a mocked `IB`; scraping
runs against a fake session with injectable `sleep`/`monotonic`; dataset tests
never touch `PWB_API_KEY` or a Hugging Face login. If your lab needs live data,
the fetch is a separate command from the scoring, and only the scoring is
tested.

### 4. Refuse rather than repair

When an input is missing, stale or unparseable, drop it and say so — never
patch it into something plausible. This is the most repeated design decision
across these tools:

- `planner_watch` skips a holding with no live price instead of alerting on a
  number somebody typed months ago.
- `night_lab` drops any model output it cannot check rather than fixing it up.
- `crypto_scan` rejects thin history instead of scoring it.
- `spend_watch` refuses to derive a burn rate from a single snapshot, because
  session metadata reports lifetime totals — a rate needs a `--baseline`.

### 5. Price the fishing expedition

If the lab scans a grid, it is a fishing expedition and must be charged as one:
a multiple-testing correction across the whole family (`season_scan` runs
Benjamini-Hochberg across a 15x12 grid), an exact test rather than an
approximation where one exists (exact binomial in `calibration_audit`,
permutation reshuffles in `season_scan`), and a hold-out or split-half
agreement check. Pre-register named claims so they face only their own test.

### 6. Give it something to beat

A number with no benchmark is not a result. `kronos_lab` scores against
persistence; `calibration_audit` scores trailing volatility against a static
benchmark; the VWAP family keeps a crossover setup *as a control expected to
fail*. Keep the control even when it fails — especially then.

### 7. The plumbing, all of it

- `docs/<lab-name>.md` — the protocol: what it measures, what it refuses to
  claim, the honesty caveats.
- Data directory gitignored, README kept: `season/*` then `!season/README.md`.
  **This fork is public** — personal ledgers, universes and trade records never
  land in git.
- A `## Layout` entry in `CLAUDE.md`, one paragraph, saying what the tool
  decides and what it will not do.
- A line in `## Commands` if there is an invocation worth remembering.
- `black pwb_toolbox/ tools/ tests/` — that exact scope, never bare `black .`.

### 8. Report the honest negative

If the lab's verdict is that the thing does not work, that is the result. Ship
it as the result. Several of these tools exist specifically because a number
that looked good did not survive its own check.

## Before believing anything it prints

If the lab touches backtest results or vendor price feeds, the `backtest-trust`
skill is the check that comes next — two failure modes there produced
confidently wrong answers in this repo and neither announces itself.

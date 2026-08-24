---
name: pine-converter
description: Close the next gap in the Pine→Backtrader converter (`pwb_toolbox.converting`) by measurement rather than guess — build a corpus, rank the failures by scripts blocked with `tools/pine_sweep.py`, fix the top sole-blocker, re-sweep to prove the number moved. Load when a Pine script will not convert, when asked what is blocking the converter, or before adding support for any Pine builtin.
---

# Closing a converter gap

Roughly a quarter of this repository's merged pull requests are one turn of
this loop. It is written down because the loop is what makes the work
cumulative — each fix is chosen by how many scripts it unblocks, not by which
gap happened to be in front of you.

**The number is the deliverable.** A fix that does not move the sweep's clean
percentage has not been shown to be worth anything.

## The rule that comes first

**Do not use the user as a test harness.** Every round trip that asks them to
paste a command, report output and wait costs them far more than it costs us,
and nearly everything it ever found was reachable from the container. Run the
sweep here.

Container network facts, so you do not rediscover them: `raw.githubusercontent.com`
and plain `git clone` both work. `api.github.com` and `codeload.github.com`
return **403** (proxy policy), so `GitHubSource` cannot be exercised live —
clone instead.

## 1. Build the corpus

```bash
mkdir -p /tmp/corpus && cd /tmp/corpus
for r in kohld/tradingview-scripts Tim1l/PineCryptoStrategies \
         casoon/pine-scripts LouisLetcher/quant-pine mihakralj/pinescript; do
  git clone --depth 1 -q "https://github.com/$r.git" &
done; wait
```

## 2. Sweep it — always `--strategies-only`

```bash
python -m tools.pine_sweep /tmp/corpus --strategies-only
```

Without that flag the number is useless: `mihakralj/pinescript` is an indicator
library of 410 files and outnumbers the actual strategies in the corpus twenty
to one, dragging the headline percentage somewhere meaningless. This converter
targets `strategy(...)` scripts.

The sweep prints `N strategies | M convert clean (P%) | C crash`, then the
blocking reasons ranked by how many scripts each sits in front of.

## 3. Read the ranking correctly

Two things in that output decide what to work on:

- **Crashes come first, always.** `convert` is contracted never to raise. A
  non-zero crash count is a bug in the converter, not a fact about the corpus,
  and it is a contract violation regardless of how few scripts it touches.
- **Sole blockers next.** The sweep separates them for a reason: a sole blocker
  is the only kind of gap where a fix converts something *today*. Everything
  below that line is a frontier with another gap waiting behind it, so
  "unblocks 40 scripts" on a non-sole entry may unblock zero.

`--by-script` lists each blocked script with the gaps visible on it, closest
first — use it when you want to see how much work a specific script is from
converting, rather than which single fix pays best.

## 4. Fix it, then prove it

Re-run the identical sweep and quote both percentages. If clean did not move,
either the gap was not sole or the fix is incomplete; say which, do not bury it.

## 5. Test the way this repo tests conversions

`pwb_toolbox.converting` emits Backtrader source, so parsing is not passing. The
end-to-end section of `tests/test_converting.py` compiles the generated code and
runs it through a real `cerebro` on synthetic bars. **A conversion that parses
but does not execute or trade is a failure.** Add your case there.

Then `black pwb_toolbox/ tools/ tests/` — that exact scope, never bare `black .`.

## Where the rest lives

- `docs/converting.md` — what the converter supports and how it is structured.
- `tools/pine_sweep.py` — the sweep itself; read it before assuming what a flag does.
- `pine/` — TradingView strategies kept as reviewable source. Nothing under
  `pwb_toolbox/` imports them.

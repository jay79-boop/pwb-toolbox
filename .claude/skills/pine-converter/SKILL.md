---
name: pine-converter
description: Close the next gap in the Pine→Backtrader converter (`pwb_toolbox.converting`) by measurement rather than guess — sweep the corpus, read the ranking correctly, fix the top sole-blocker, re-sweep to prove the number moved, and test through a real cerebro. Load when a Pine script will not convert, when asked what is blocking the converter, or before adding support for any Pine builtin.
---

# Closing a converter gap

Roughly a quarter of this repository's merged pull requests are one turn of
this loop. It is written down because the loop is what makes the work
cumulative — each fix is chosen by how many scripts it unblocks, not by which
gap happened to be in front of you.

**The number is the deliverable.** A fix that does not move the sweep's clean
percentage has not been shown to be worth anything.

## 1. Sweep

`docs/converter-corpus.md` has the corpus clone commands, the container's proxy
limits, and why `--strategies-only` is not optional. Run what it says; do not
paste a command to the user and wait.

The sweep prints `N strategies | M convert clean (P%) | C crash`, then the
blocking reasons ranked by how many scripts each sits in front of.

## 2. Read the ranking correctly

This is the part the doc does not cover, and it is where the loop is won or
lost. Two things in that output decide what to work on:

- **Crashes come first, always.** `convert` is contracted never to raise, so a
  crash is a contract violation regardless of how few scripts it touches. Clear
  them before looking at anything else.
- **Then sole blockers.** The sweep separates them for a reason: a sole blocker
  is the only kind of gap where a fix converts something *today*. Everything
  below that line is a frontier with another gap waiting behind it — so
  "in front of 40 scripts" on a non-sole entry may unblock zero of them.

`--by-script` lists each blocked script with the gaps visible on it, closest
first. Use it to see how far one specific script is from converting, rather
than which single fix pays best.

## 3. Fix it, then prove it

Re-run the identical sweep and quote both percentages. If clean did not move,
either the gap was not sole or the fix is incomplete — say which. Do not report
a fix without the number that justifies it.

## 4. Test the way this repo tests conversions

`pwb_toolbox.converting` emits Backtrader source, so **parsing is not passing**.
The end-to-end section of `tests/test_converting.py` compiles the generated code
and runs it through a real `cerebro` on synthetic bars. A conversion that parses
but does not execute or trade is a failure, not a pass. Add your case there.

Then `black pwb_toolbox/ tools/ tests/` — that exact scope, never bare `black .`.

## Where the rest lives

- `docs/converter-corpus.md` — the corpus, the commands, the proxy limits.
- `docs/converting.md` — what the converter supports and how it is structured.
- `tools/pine_sweep.py` — the sweep itself; read it before assuming a flag's behaviour.

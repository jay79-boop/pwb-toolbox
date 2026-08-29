# Job: journal capture

**Runs:** weekday afternoons, after the close.
**Goal:** the journal fills itself, instead of being the chore that gets
skipped on exactly the days worth recording.

## Where the journal lives

`C:\Users\Gexio\OneDrive\trade-journal\trade-journal.html`, on the owner's
machine and **not in this repository** — it is a personal document and this
fork is public. Never commit it, never paste its contents into a commit
message, a PR, or a log record.

This job is therefore local-only. A cloud run cannot reach that file and must
log `skipped` with blocker `journal-not-reachable-from-cloud` rather than
pretending otherwise.

**It reads bar data, not a chart.** Its scheduled task is registered to run
whether anyone is signed in or not, so there may be no desktop — **do not call
`tv_launch` or any TradingView tool.**

## Do

1. Read the day's closed trades from the journal's own register.
2. For each, render the chart at the timeframe the thesis was framed on:

   ```
   python tools/desk_levels.py chart NQ=F --out shot.png \
       --mark entry=29200 --mark stop=29050 --mark target=29500
   ```

   The image carries the same bars, the session levels, the unmitigated gaps,
   and the thesis levels you pass in.
3. Fill the close-out against the **locked thesis** — that is the whole value
   of the journal. Whether the trade made money is secondary to whether it did
   what it said it would.
4. Where the outcome contradicts the thesis, say so plainly in the note. A
   journal that rationalises is a journal that teaches you nothing.

## The image is not the owner's chart, and says so

It carries none of their drawings and none of their indicator settings. That
was chosen deliberately on 2026-08-29, with the trade stated rather than
hidden: **what it buys is that the same bars produce the same picture every
time.** A screenshot cannot be regenerated — it records what one screen looked
like on one afternoon, and once the layout changes there is no way back to it.
A rendered chart can be redrawn years later from the data that framed the
thesis, which is what the journal is actually for.

So: **do not describe a rendered image as a screenshot of their chart**, in the
journal or in a run record. If a note needs their markup, that is a thing to
raise for them to do by hand, not to approximate.

## Do not

- Do not edit the inlined copies of `option-lab.js` or `journal-shots.js`
  inside the HTML. If one needs changing, change the module in this repo, run
  its tests, and re-inline the whole file. Patching the inlined copy makes the
  tested version and the running version different code.
- Do not write a thesis the owner did not write. Capture, do not author.

## Honest outcomes

No trades today → `ok`, no actions, "no trades to record". Not `skipped`: the
job ran and correctly found nothing, and the distinction matters for the review.

The feed returned nothing, or the bars were too stale to render an honest
picture → `failed`, with the blocker. A chart drawn from stale bars, with no
note saying so, is worse than no chart.

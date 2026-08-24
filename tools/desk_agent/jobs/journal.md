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

## Do

1. Read the day's closed trades from the chart and the journal's own register.
2. For each, capture the chart at the timeframe the thesis was framed on.
3. Fill the close-out against the **locked thesis** — that is the whole value
   of the journal. Whether the trade made money is secondary to whether it did
   what it said it would.
4. Where the outcome contradicts the thesis, say so plainly in the note. A
   journal that rationalises is a journal that teaches you nothing.

## Do not

- Do not edit the inlined copies of `option-lab.js` or `journal-shots.js`
  inside the HTML. If one needs changing, change the module in this repo, run
  its tests, and re-inline the whole file. Patching the inlined copy makes the
  tested version and the running version different code.
- Do not write a thesis the owner did not write. Capture, do not author.

## Honest outcomes

No trades today → `ok`, no actions, "no trades to record". Not `skipped`: the
job ran and correctly found nothing, and the distinction matters for the review.

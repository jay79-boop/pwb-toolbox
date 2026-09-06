# The cost of a turn is set before the turn begins

*Decided 2026-09-03.*

`2026-09-03-the-usage-panel-cannot-see-the-sessions-that-drained-the-window.md`
established where the window went: 19 cloud sessions, 354M cache reads, and one
25-hour session carrying 133M of them. This entry is about the number that could
have said so *first*, and the check that now reads it.

## What one turn cost, measured

`Amplitude Analytics installation` was sampled three times while it was still
live:

| Sample (UTC) | Meter | Cache reads | Output |
| --- | --- | --- | --- |
| 07:15:14 | $148.04 | 127,206,268 | 446,210 |
| 07:16:12 | $164.92 | 131,213,653 | 454,570 |
| 07:20:54 | $166.39 | 132,521,389 | 476,874 |

**One turn, 58 seconds apart, cost $16.88-equivalent and 4.01M cache reads to
produce 8,360 tokens of output.** Between the second and third samples, with
only a background agent running and nobody typing, it drew a further $1.47 in
282 seconds.

Its `context_usage.used_tokens` was 737,498 of a 1,000,000 cap. That is the
whole explanation: the price of a turn is fixed by the conversation behind it
*before* the turn does any work, so an old session gets steadily more expensive
at doing less. Figures are an API-equivalent meter, not a charge; every session
reported `isUsingOverage: false`.

Two snapshots are what makes those rates legitimate. One snapshot reports a
*lifetime* total and can never yield a rate — the error `tools/spend_watch.py`
was written to refuse.

## Age dominates effort level

The 2026-08-24 rules say effort level is a window decision. It is, but it is the
second-order term, and on this incident it pointed the wrong way. Across the
sixteen cloud sessions created 1–3 September, the top session ran at `high`, not
`max`, and on its own outspent all nine `max`-effort sessions combined —
**$148.04 against $99.77** at the 07:15Z sample.

So `max` on a short session is cheap and `high` on a day-old one is not. Rank
sessions by age and context, not by the effort dial.

## The lever that was missing

`spend_watch` read `usage.cache_read_tokens`. That is a lifetime total, and
therefore lagging: a session trips the check only after the spending has already
happened. It is the right field for "what did this cost" and the wrong one for
"what should I stop".

`external_metadata.context_usage.used_tokens` is the forward-looking twin — what
the **next** turn will re-read. It rises long before the lifetime total looks
alarming, and it rides along in the same bulk `list_sessions` response, so
reading it costs no extra call.

`find_heavy_context` reads it: `medium` past half the context cap, `high` past
70%. Both checks are kept, because a session can trip either without the other —
a finished session that spent a lot is fat but not heavy; a young session near
its cap is heavy but not yet fat. Two tests pin exactly that pair.

On the real 50-session snapshot the lifetime check raises eleven `medium`
findings and the new one raises a single `high`, naming the right session.

## What this does not change

The 2026-08-24 mechanism — Routines re-arming themselves into persistent
sessions — was **not** involved here. Every live Routine was read on 2026-09-03:
none re-arms itself, and the ones that mention re-arming do so to forbid it.
That fix held, and this was a different failure with the same symptom.

## Rules that follow

1. **A session is a consumable, not a workspace.** Close it when its thread is
   done.
2. **Past half its context cap, finish the thread and start fresh.** Not when it
   feels slow — the field says it outright, and `spend_watch audit` now flags it.
3. **Never leave a session connected overnight with a background agent in it.**
   It bills for existing.
4. **Read the meter from one snapshot, the rate from two.**

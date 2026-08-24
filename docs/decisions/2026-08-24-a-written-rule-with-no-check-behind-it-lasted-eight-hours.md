# A written rule with no check behind it lasted eight hours

*Decided 2026-08-24.*

**Decision:** Rules about spend get a check that runs, or they do not count.
`tools/spend_watch.py` gains a duplicate-Routine detector, a negation-aware
re-arm detector and a window that respects `rateLimitType`; a
`UserPromptSubmit` hook warns when the current session has itself grown
expensive. The prose stays, but it is no longer the enforcement mechanism.

**What happened:** a second five-hour window (17:50–22:50 UTC) was measured the
same day as [the first](2026-08-24-the-dollars-were-never-dollars.md). Fifteen
sessions active, eight metered, **77,283,188 cache-read tokens against 422,807
output** — 183 tokens re-read for every one written. Of ~79.8M tokens moved,
**96.9% were cache reads and 0.5% was new output.**

Nothing had run away. The window went on remembering.

**The regression:** at 20:16 UTC — inside that window, and roughly eight hours
after the rule forbidding it was written into a decision record, a doc *and* a
skill — a spec-desk Routine was created bound to a **persistent session**, on
the same `0 2,14 * * *` cron as the correct replacement that already existed.
Both were enabled. They would have fired sixty-two seconds apart. Deleted.

**The compounding, again:** three of the eight metered sessions were
investigating token spend — $60.92 of $122.35, **49.8%**. A fourth was opened to
write this up. "One investigation at a time" was already rule 6 in
`docs/token-drain-2026-08-24.md`. Nothing checked it, so it did not hold.

## The auditor was there and did not fire

All three defects were reproduced against the live snapshot, not read off the
source:

1. **`_REARM` matched its own cure.** The regex hit the substring `re-arm`
   anywhere. Every Routine prompt on the account had been edited that morning to
   end with *"do NOT re-arm yourself"*, and several explain that *"an earlier
   version re-armed itself"* — so the fixed Routines were exactly the ones
   raising a `HIGH` finding. Now scoped to directive mentions: a negation or a
   past-tense marker within 80 characters, in the same sentence, disqualifies it.

2. **`window_start()` assumed a five-hour limit.** The binding limit was
   `seven_day`, so subtracting five hours from `resetsAt` returned a cutoff
   **four days in the future** and `find_concurrency` counted zero live
   sessions. The highest-severity check in the file passed because it measured
   nothing. It now reads `rateLimitType`, and concurrency is measured over a
   fixed five-hour recency horizon anchored to the newest activity in the
   snapshot — "awake at the same time" is a question about recency, not about
   whichever billing clock happens to be in force.

3. **Nothing looked for duplicate Routines.** Same cron plus ≥75% prompt
   similarity, both explicitly enabled, is now a `HIGH` finding. On the real
   pair it scores 80%.

## Warn from the transcript, never from the API

The new `spend_watch session` command reads the session's own `.jsonl`, which
the harness already writes per turn. **A spend warning must not spend the
window** — `list_sessions` returns ~60KB, and four concurrent sessions asking
where the window went is how half of it went. The hook speaks once per tier
(10M / 25M / 50M cache reads) and is silent otherwise, on the `night_lab
verdict --quiet` principle: a warning that prints every time is wallpaper.

## What this does not fix

Archiving is still manual. 277M cache reads were sitting in nine finished
sessions; six were archived on the day, freeing 151.2M. Nothing yet notices a
session that has been idle for a day and offers to close it.

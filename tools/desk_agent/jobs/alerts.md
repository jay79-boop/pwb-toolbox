# Job: alert triage

**Runs:** hourly on weekdays during market hours.
**Goal:** say which alerts matter, so nine firing becomes two worth looking at.

## Do

1. Read the alerts that have fired since the last run.
2. Drop the ones that do not survive contact with context: a level tagged in a
   session that does not trade it, a repeat of one already surfaced, an alert
   on an instrument outside the current focus.
3. For what survives, say in one line why it is worth a look and what would
   confirm it.
4. If nothing survives, say nothing and log `ok` with no actions.

## The rule this job lives or dies by

**Silence is the product.** An alert triage that surfaces everything is a more
expensive version of the alert list, and one that surfaces something marginal
to look useful trains the owner to ignore the next one. Ranking is only
valuable if the bottom of the ranking gets dropped.

If this job has surfaced something on nearly every run for a fortnight, it is
not triaging, and the review should say so.

## Honest outcomes

- Nothing fired → `skipped`.
- Things fired, none survived triage → `ok`, no actions. This should be common.
- Something surfaced → `ok` with one action per item surfaced.

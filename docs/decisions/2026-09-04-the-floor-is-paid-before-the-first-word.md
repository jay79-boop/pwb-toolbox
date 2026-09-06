# The floor is paid before the first word

*Decided 2026-09-04.*

`2026-09-03-the-cost-of-a-turn-is-set-before-the-turn-begins.md` established the
**slope**: a session's price is fixed by the conversation behind it, so an old
session gets steadily more expensive at doing less. This entry is about the
**intercept** — what a session costs on turn one, with nothing behind it — and
the largest piece of it that this repository actually controls.

## What was measured

The owner opened a session with "I haven't even done anything and almost half of
my token spent already." Read from that session's own transcript:

| Turn | Cache read | Cache write | Output |
| --- | --- | --- | --- |
| 1 | 36,662 | 47,238 | 1,599 |
| 2 | 37,885 | 92,988 | 3,610 |
| 3 | 130,873 | 5,328 | 407 |

**The first request was ~84,000 tokens before the owner's question was read.**
Nothing had run. That is the system prompt, the tool surface, the skills roster
and `CLAUDE.md`, re-sent on every turn.

The complaint was accurate and it was not about a runaway process.

## `CLAUDE.md` was the largest piece we own

32,772 bytes — roughly 8,200 tokens — paid in every session of every sitting.
It had grown to hold the reasoning behind each rule, the incident that produced
it, and the worked example. All of that is worth keeping. None of it is worth
re-sending to a session that will never hit the situation.

Cut to 11,157 bytes (~2,800 tokens): **rules and traps, one line each**, with
reasoning moved to `docs/` and read on demand. The pre-cut text is preserved
whole at `docs/claude-md-archive-2026-09-04.md`. No rule was dropped.

## The automation was never the drain, and was disabled anyway

Eight scheduled Routines existed; two had been failing daily for days. All of
them together fire a handful of short-lived sessions per day — rounding error
against a single interactive session at 133M cache reads. Seven were disabled
at the owner's instruction. **This was not a fix for the complaint** and is
recorded so a later session does not mistake it for one. A job that fails daily
and is never read is worth less than nothing, which is reason enough on its own.

The eighth (`Monthly Account Credit Check Reminder`, created via `http_api`)
refuses agent edits and can only be disabled from the Routines UI.

## Correcting the recommendation given to the owner in the same session

He was offered "stop defaulting to Opus at max effort" as a lever and took it.
Per yesterday's entry that is **the second-order term, and on the 2026-09-02
incident it pointed the wrong way**: the top spender ran at `high`, and alone
outspent all nine `max`-effort sessions combined. The first-order term is
session age and accumulated context. Ranking by the effort dial is the error
that entry was written to prevent, and this one repeated it.

**The lever that matters is: start a new session for a new question.** The dial
is worth changing only after that.

## Two consequences, both deliberate

- **The `## Commands` block moved to `docs/layout.md`**, already the canonical
  inventory. `tools/front_door.py` scanned it out of `CLAUDE.md`, and its acquit
  guard caught the cut immediately — 5 commands where it demanded 20. That guard
  did exactly its job. `commands_source()` is now exposed so the test asserts
  every blurb is quotable from the file the scan actually read, rather than from
  a filename typed twice.
- **`.claude/hooks/session-orient.sh` is unregistered** from `SessionStart`. It
  made every session do git and GitHub research before its first word, whether
  or not a catch-up was wanted. The script is kept, not deleted — re-add it to
  `.claude/settings.json` to bring it back.

## Left on the table, and larger than this commit

The connected MCP connectors contribute roughly 300 of ~400 tool definitions to
every session's fixed cost. Disconnecting the unused ones is a claude.ai
settings change, is worth more than everything above, and cannot be done from a
cloud session.

## The rule that follows

**Anything added to `CLAUDE.md` is paid for in every session, forever.** Before
adding a paragraph, ask whether a session that never hits this situation still
needs to carry it. If not, it belongs in `docs/` behind a one-line pointer. The
same test applies to the skills roster and the connector list.

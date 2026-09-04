# Work order: find what actually changed in the drain window

**Delete this file once the answer is recorded in `docs/decisions/`.**

Written 2026-09-03 from inside a session that had hit its own hard stop, so only
`git log` was reachable. The owner's hypothesis is that the drain began with
installs made 2026-08-31..09-03, not with anything built before that. It is a
good hypothesis and it is not yet tested. Do not dismiss it, and do not act on
the MCP-disconnect recommendation until this is settled -- that recommendation
reasoned from the absolute per-turn cost, not from the change.

## What only three things can do

Per-turn cost is raised by exactly three things: **CLAUDE.md**, the **skills
list** (descriptions only -- a SKILL.md body loads on invocation), and **MCP tool
schemas**. Everything else in this repo -- tools, tests, docs -- costs nothing
until something reads it. Any explanation that does not route through one of
those three, or through the *number of sessions*, is not an explanation.

## Established from git

| Change | When | Per-turn effect |
| --- | --- | --- |
| CLAUDE.md +116 lines net | 08-29 → 09-02, 14 commits | real but modest, order +1.2K tokens/turn |
| `aiq-research` skill (426-line SKILL.md) | 09-02 `af113b0` | description only, small |
| `cuopt-...-formulation` skill (277-line) | 09-02 `849048c` | description only, small |
| **Eleven PRs merged in one day** | 09-02, #185–#197 | eleven sessions, each paying ~84K startup from turn one |
| No `.mcp.json` change | in the last 16 days | MCP surface did NOT grow in the window |

That last row matters: **the MCP servers were already there before the drain
started.** They are expensive, but they are not what changed. The owner is right.

## The suspect git cannot see, and it is the one with history

**Routines and scheduled jobs do not appear in a git log.** The 2026-08-24 drain
was caused by self-re-arming Routines bound to persistent sessions -- a wake that
finds nothing changed still pays to re-read the entire accumulated conversation
first. `docs/token-drain-2026-08-24.md` has the forensics; the `steward` skill
exists to forbid the pattern.

Two commits on 09-02 are in exactly that territory and are the top lead:

- `5634cef` "Let the desk agent run its own first step, and give it the repo venv"
- `1585e4c` "Push the desk agent's run log after every job, and check that it landed"

If the desk agent's schedule, scope or per-run cost changed on 09-02, each run is
a session, and the run log is the audit trail.

## What to actually run — in this order, stopping when it is answered

1. `python -m tools.desk_agent.runlog summary --last 40` — how many runs since
   08-31, and did the rate or duration jump on 09-02?
2. `list_triggers` (claude-code-remote MCP) — any Routine created in the window?
   Any with a `persistent_session_id`? Any whose prompt re-arms itself? That
   combination is the known killer.
3. `list_sessions` — per-session `cache_read_tokens` and `updated_at` for
   08-30..09-03. Cluster by day. **This is the measurement that settles
   install-vs-plan-change, and it is the only one that can.**
4. Feed 2 and 3 into `python tools/spend_watch.py audit <snapshot.json>` — the
   auditor already detects self-re-arming Routines, persistent-session binds,
   duplicate Routines, fat sessions and concurrency. It was built for this.

## The confound that must be stated in the answer

The owner moved off the ~5x plan back to Claude Pro on **2026-09-02** — the same
day as eleven merges and the desk-agent changes. A 5x smaller window and a
possible rise in consumption landed together, so a per-session token count is the
only way to separate them: **if per-session tokens are flat across 08-30..09-03,
the plan change explains everything and no install is at fault.** If they climbed
on 09-02, find which sessions and what they were doing.

Say which of the two it is, with the numbers. Do not report both as equally
likely to avoid committing.

## Done looks like

A decision record in `docs/decisions/` naming the cause with per-session figures,
linked from `docs/decisions/README.md`, and a one-line answer to the owner: *what
changed, how much of the drain it explains, and whether disconnecting MCP servers
is worth doing after all.* Then delete this file.

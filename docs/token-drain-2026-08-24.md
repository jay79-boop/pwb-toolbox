# The 2026-08-24 token drain: what actually burned the window

A five-hour window was exhausted between roughly 00:20 and 05:20 UTC on
2026-08-24. This is what did it, measured from `list_sessions` and
`list_triggers` rather than guessed, and the rule that follows.

## It was not one runaway thing

Seventeen sessions were active in that window. Twelve were cloud sessions on
Opus, nine of those at `max` effort. Nothing had gone rogue — every one of them
was doing roughly what it was asked to do. The window died of arithmetic.

| Session | Effort | Last activity (UTC) | Metered (lifetime) | Cache reads |
| --- | --- | --- | --- | --- |
| Ollama trade stress testing | max | 01:59 | $290.64 | 68.3M |
| Kronos financial analysis | max | 03:07 | $154.28 | 14.9M |
| Skill extraction and clarification | max | 02:23 | $88.70 | 56.7M |
| Puzzle app registration review | — | 03:09 | $43.06 | 13.2M |
| VWAP trading strategy | high | 01:45 | $20.43 | 6.4M |
| Multi-agent workflow architecture | high | 01:57 | $18.44 | 22.5M |
| Session sidebar cleanup | high | 03:09 | $6.85 | 1.7M |
| Claude usage analysis | max | 03:09 | $5.12 | 2.0M |
| Fleet lead A | — | 02:06 | $5.07 | 3.0M |
| Fleet lead B | — | 01:33 | $4.29 | 2.8M |
| Merge similar artifacts | max | 03:08 | $4.11 | 1.2M |
| Master blueprint review | — | 03:09 | $1.90 | 8.7M |

**These figures are not money, and no charge appears anywhere.** Confirmed with
the owner on the day: nothing was billed. Every session in the window reports
`isUsingOverage: false`, so the account never crossed into paid overage. The
`cost_usd` field is an API-equivalent valuation — what the same traffic would
have cost at pay-as-you-go rates — and on a subscription it is a **meter, not an
invoice**. Read the column as "how much of the five-hour window this consumed",
denominated in dollars only because that is the unit the field happens to use.

What was actually spent was the window, and the window is the scarce thing: it
resets on a clock rather than on a balance, so nothing buys it back early.

**And the column is lifetime, not in-window.** The three expensive sessions at
the top had been accumulating for a day or more; only part of each belongs here.
What *is* fully attributable is the five sessions created inside the window —
VWAP, both fleet leads, usage analysis, merge artifacts — which together metered
**$39.02-equivalent in about two hours**, before counting a single token from the
three big ones that were also working throughout.

## The mechanism: self-re-arming check-ins bound to persistent sessions

Three Routines were live at once, each ending its own prompt with an
instruction to schedule the next one:

| Routine | Fires into | Re-arm instruction |
| --- | --- | --- |
| Re-check PR #111 | VWAP session | "re-arm this check-in about an hour out" |
| Spec desk: XRP/DOGE stop-target watch | Kronos session ($154) | "re-arm another check-in ~3 hours out" |
| Re-check PR #78 | desk-agent session | daily |

Firing into a **persistent** session means the entire accumulated conversation
is re-read as input on every wake. The Kronos session carries 14.9M cache reads
and 508,918 raw input tokens; the Ollama one carries 68.3M cache reads. A
check-in that looks around, finds nothing changed, and goes back to sleep still
pays to re-read all of that first. The work is free; the remembering is not.

The fleet registry had already measured this and not generalised it: a
heartbeat wake that "does nothing" meters $3.29–$4.29. Fleet lead A at $5.07 and
lead B at $4.29 sit in the table above confirming the figure a second time.

One of the three had already been diagnosed and replaced at 02:07 UTC by a
fixed `0 2,14 * * *` cron that spawns a **fresh session per fire**, carrying
this note: *"The previous version re-armed itself every ~3 hours into a
long-lived session and became a significant token expense; that pattern was
deliberately removed."* The correct fix, correctly reasoned — but the old
Routine was never deleted, and it sat there alongside its own replacement.

## The compounding move: investigating the drain with more max-effort sessions

Four sessions were opened to investigate this problem — usage analysis (01:56),
merge artifacts (02:22), token-drain investigation (03:04), mysterious charges
(03:23). Three ran Opus at `max` effort. Two of them were the same
investigation running twice in parallel, and one was still running on the
*next* window hours later, re-deriving what the other had already found.

Diagnosis is not free. A question about the window, asked four times
concurrently at max effort, consumes the very thing it is asking about.

## Rules that follow

1. **A scheduled check-in gets a fresh session, never a persistent one**, unless
   it genuinely needs the prior conversation. Persistence turns a cheap poll
   into a full context re-read at every firing, and the cost grows with the
   session's age rather than staying flat.
2. **A Routine must not schedule its own successor.** Use a cron. A self-re-arming
   prompt has no natural stopping point, no visible cadence in the Routine list,
   and no single place to turn it off.
3. **Delete the old Routine in the same breath as creating its replacement.**
   Superseded-but-live is indistinguishable from intended, and it fires.
4. **Effort level is a window decision.** `max` on Opus is right for the hard
   design call and wrong for "check whether CI is green" — and most scheduled
   work is the latter.
5. **Before trimming schedules, count the live sessions.** The standing Routine
   cost was real but second-order here; concurrent long-lived max-effort
   sessions were the first-order term, exactly as the fleet registry warned.
6. **One investigation at a time.** Check `list_sessions` for an existing session
   on a question before opening another.

## How to run this diagnosis again

`list_sessions` with `mine: true` and `list_triggers`, then read three fields
and nothing else:

- `updated_at` — cluster it. Many sessions touched in one short span is the
  drain, whatever each one individually cost.
- `usage.cache_read_tokens` — the size of what gets re-read per turn. This,
  not output, is what makes an old session expensive to wake.
- each Routine's `persistent_session_id` plus its prompt text — a
  `persistent_session_id` next to a re-arm instruction is the pattern above.

`rate_limit_info.resetsAt` on any session gives the window boundary to filter
against; `status: "rejected"` marks the sessions that hit the wall.

## What was changed on 2026-08-24, and the catch in the fix

Acted on the same day, with the owner's approval:

- **Interrupted** the second live investigation session. It was running Opus at
  `max` effort on the fresh window, re-deriving this same answer in parallel,
  and had already metered $4.48-equivalent and opened a second pull request for it.
- **Deleted `Re-check PR #111`** — self-re-armed hourly into the VWAP session,
  fire time already in the past.
- **Deleted the old `Spec desk: XRP/DOGE stop-target watch`** — self-re-armed
  every three hours into the Kronos session. Its replacement had already existed
  for thirteen hours; only the deletion was missing.
- **Rebuilt the PR #78 check-in** as `25 13 * * *` with
  `create_new_session_on_fire`, and deleted the persistent-session original.

**The rebind is not free, and this is the part worth knowing before copying it.**
A Routine created through the MCP tool stores no connector grants. Firing into a
*persistent* session hides that — the session contributes its own tool surface,
which is why the fleet heartbeats reached GitHub fine. A fresh session per fire
has no such donor, so it starts **without `mcp__github__*`**: no CI status, no
review comments. Plain `git` over the session proxy still works, so merging,
resolving, validating and pushing all survive.

So the trade is real: persistent sessions are expensive but well-equipped; fresh
sessions are cheap but half-blind. The resolution is not to pick one — it is to
**write the Routine's prompt so it states which steps need which tools, and
degrades loudly**. The PR #78 prompt now opens by naming the capability it may
lack, marks its git-only steps as the primary job, and forbids treating absent
tooling as "nothing to do". The desk-agent weekly review had already reached the
same shape independently, which is a fair sign it is the right one.

The alternative — creating the Routine from the claude.ai routines UI, where
connectors can be attached — is available if a scheduled job genuinely needs
GitHub for its main purpose rather than its reporting.

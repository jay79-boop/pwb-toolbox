# The 2026-09-02 token drain: one session, left open for a day

A five-hour window was exhausted between 01:40 and 06:40 UTC on 2026-09-03. The
scheduled `Overnight research watchdog`, firing at 03:16, came back
`status: "rejected"` — it is the session that hit the wall, not the one that
built it.

This is the second measured drain here. The first
(`docs/token-drain-2026-08-24.md`) was caused by self-re-arming Routines bound
to persistent sessions. **That mechanism is gone and stayed gone** — every live
Routine was read on 2026-09-03 and none re-arms itself; the ones that mention
re-arming do so to forbid it. The fix held. This drain has a different shape,
and the 2026-08-24 rules did not cover it.

## One session was 38% of everything

Sixteen cloud sessions created between 1 and 3 September carried **$389.04 in
lifetime metered total and 336.3M cache reads**, measured from a single
`list_sessions` snapshot at 07:15:14Z on 3 September.

| Session | Created | Effort | Metered | Cache reads |
| --- | --- | --- | --- | --- |
| **Amplitude Analytics installation** | **09-02 06:34** | **high** | **$148.04** | **127.2M** |
| NVIDIA Cosmos installation and usage | 09-02 08:06 | high | $31.19 | 22.7M |
| Computer shutdowns investigation | 09-02 03:52 | high | $30.55 | 24.7M |
| Desk agent: push the run log it commits | 09-02 05:22 | — | $26.03 | 12.8M |
| GodMode revive — repoint the dead providers | 09-02 02:45 | — | $25.99 | 23.9M |
| Enterprise situational awareness layer | 09-02 10:56 | max | $24.68 | 25.6M |
| Desk adapter and content credentials | 09-02 23:39 | max | $23.69 | 22.9M |
| NVIDIA skills installation | 09-02 08:04 | high | $14.97 | 12.8M |
| NVIDIA API vision integration | 09-02 00:54 | max | $14.12 | 17.9M |
| Ge Xiong global instructions | 09-02 04:44 | high | $12.51 | 10.1M |
| Barehands setup without camera | 09-02 20:57 | max | $9.87 | 9.7M |
| NVIDIA API vision integration | 09-02 00:48 | max | $8.44 | 8.5M |
| Tulsa and Joint phone numbers | 09-01 21:15 | max | $6.74 | 6.6M |
| AIQ research skill | 09-02 01:02 | max | $4.67 | 4.5M |
| Superseded artifact pointers cleanup | 09-02 23:34 | max | $3.93 | 2.7M |
| NVIDIA API image analysis | 09-02 11:55 | max | $3.62 | 3.6M |

**These figures are not money and nothing was billed.** Every session reports
`isUsingOverage: false`. As established on 2026-08-24, `cost_usd` is an
API-equivalent valuation — on a subscription it is a meter, not an invoice.
Read the column as "how much of the window this consumed".

Two independent fields agree on the ranking: the top session is 38.1% of the
metered total and 37.8% of the cache reads. Twenty-two sessions were created on
2 September alone.

## What made it expensive was age, not effort

`Amplitude Analytics installation` was opened at 06:34 on 2 September and was
still connected at 07:20 the next morning — **24 hours and 46 minutes**, with
`context_usage.used_tokens` at 737,498 of a 1,000,000 cap.

That number is the whole story. Every model call in that session re-read three
quarters of a million tokens before doing anything at all. Sampled three times
in six minutes:

| Sample (UTC) | Metered | Cache reads | Output |
| --- | --- | --- | --- |
| 07:15:14 | $148.04 | 127,206,268 | 446,210 |
| 07:16:12 | $164.92 | 131,213,653 | 454,570 |
| 07:20:54 | $166.39 | 132,521,389 | 476,874 |

**One turn, between the first two samples, cost $16.88-equivalent and 4.01M
cache reads to produce 8,360 tokens of output.** The work was negligible; the
remembering was not. Between the second and third, with only a background agent
running, it drew a further $1.47 in 282 seconds unattended.

Two snapshots are what makes those rates legitimate. A single snapshot reports a
*lifetime* total and cannot yield a rate — the error `tools/spend_watch.py`
exists to refuse.

And note the effort column: that session ran at `high`, not `max`, and on its
own outspent all nine `max`-effort sessions combined ($148.04 against $99.77).
The 2026-08-24 rule that "effort level is a window decision" is still right, but
it is the second-order term. **Session age dominates it.** A cheap-effort
session left open all day beats an expensive-effort session used briefly.

## The lever that was missing

The 2026-08-24 diagnosis reads `usage.cache_read_tokens` — lifetime, and
therefore lagging. It tells you what a session *has* cost, so a session only
trips the check after the spending has happened.

`external_metadata.context_usage.used_tokens` is the forward-looking twin: it is
what the **next** turn will re-read, and it rises long before the lifetime total
looks alarming. It rides along in the same bulk `list_sessions` response, so it
costs no extra call. `spend_watch.py` was not reading it.

On the real 50-session snapshot the two checks behave very differently: the
lifetime check raises eleven `medium` findings, and the new context check raises
exactly one `high` — naming the right session. Both are kept, because a session
can trip either without the other (a finished session that spent a lot; a young
session about to). `find_heavy_context` is the new one.

## The compounding move, again

On 2026-09-02 at 00:48 and 00:54, two sessions were opened six minutes apart on
the same job and both drove it to the same pull request: $8.44 and $14.12,
**$22.56 for one piece of work done twice**. That is the incident already
recorded in
`docs/decisions/2026-09-02-one-paste-two-sessions-and-the-ledger-caught-it-late.md`,
priced.

It recurred while this drain was being investigated: two Opus sessions were
opened two minutes apart on 3 September, at 07:12 and 07:14, on the adjacent
questions "what was I charged" and "what ate my tokens". Rule 6 from
2026-08-24 — *one investigation at a time* — is the rule most often broken,
because the impulse to ask again is strongest exactly when the answer is
overdue.

## Rules that follow

The 2026-08-24 rules stand. These are additional, and they are about sessions
rather than Routines:

1. **A session is a consumable, not a workspace.** Close it when its thread is
   done. The cost of a turn is set by the conversation behind it, so an old
   session gets more expensive at doing less.
2. **Past half its context cap, finish the thread and start fresh.** Not when it
   feels slow — `context_usage.used_tokens` says it outright, and
   `spend_watch audit` now flags it.
3. **Never leave a session connected overnight with a background agent in it.**
   It bills for existing. This one drew $1.47 in under five minutes with nobody
   watching.
4. **Before opening a session, check whether one is already on the question.**
   Costed twice now: $22.56 on 2 September, and again the next morning.
5. **Read the meter from one snapshot, the rate from two.** Never quote a rate
   from a single `list_sessions` call.

## How to run this diagnosis again

`list_sessions` with `mine: true`, then:

- `external_metadata.context_usage.used_tokens` against `max_tokens` — the
  forward cost of the next turn, and the leading indicator.
- `external_metadata.usage.cost_usd` and `cache_read_tokens` — present in the
  bulk response for roughly half the sessions, and the lifetime totals. Do not
  call `get_session` per session for these unless the bulk call omits them.
- `rate_limit_info.status` — `rejected` marks the session that hit the wall,
  `allowed_warning` the account approaching one; `resetsAt` minus five hours
  gives the window to filter against.
- `created_at` against `updated_at` — the span is what makes a session
  expensive, more than its effort level.

`python tools/spend_watch.py audit snapshot.json` runs every structural check
over that snapshot, and with `--baseline` over two of them.

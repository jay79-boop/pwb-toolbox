# The dollars were never dollars

*Decided 2026-08-24.*
**Decision:** Stop pricing this account's work in dollars. Every figure this
ledger has quoted as a cost — $3.29 and $4.29 per fleet heartbeat, $266 and
$154 for the two long sessions, ~$180/day for hourly leads — is Claude Code's
**locally computed estimate at API list prices**, not money charged. The
documentation is explicit on both halves: the session cost figure "isn't
relevant for billing purposes" for Pro and Max subscribers, and Claude Code
"computes the dollar figure locally from token counts priced at standard list
rates".
**Confirmed, not assumed:** every session `list_sessions` returns carries
`rate_limit_info.isUsingOverage: false`, and usage credits — drawing past the
plan limit at real per-token rates — are the *only* mechanism by which a
subscription incurs a dollar charge. The sessions that failed on 2026-08-24
failed with `status: "rejected"` and "You've hit your session limit", which is
the blocking outcome, not the billing one. You cannot both be stopped at the
limit and be silently billed past it; those are the two exclusive branches.
**Why it matters past the accounting:** a wrong unit produced a real decision.
The heartbeat cadence was cut on a $180/day figure that does not exist. The
cadence was still right — the binding constraint is the five-hour window, which
this account hit twice that day — so the conclusion survives and the argument
under it is replaced. **Do not read the reverse into this.** "Not charged" is
not "free": the window is the scarcer resource, and it is shared with claude.ai
and every other session on the account.
**The unit to use instead** is tokens, from `list_sessions`' `usage` blob, and
specifically `cache_read / output`. One fleet wake is ~3M cache reads. The $290
session was 68.3M cache reads against 180K output — 379:1, against 80–100:1 for
well-behaved sessions in the same listing. That ratio names a session that
needed `/clear`, and it needs no currency at all.
**Where a real charge could still hide** — one place only: usage credits, at
claude.ai → Settings → Usage → Usage credits. Credits off, or $0 spent this
month, means nothing has been charged. A Console API key would bill at
platform.claude.com/usage instead, but this account authenticates by
subscription: five-hour windows are subscription behaviour, an API key gets
429s.

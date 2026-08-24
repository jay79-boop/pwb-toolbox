# Agent fleet: liveness by scheduler, judgment by model (PR #109)

*Decided 2026-08-24.*
**Decision:** Write the owner's multi-agent daily driver down and fix its
architecture on paper before arming anything: `docs/agent-fleet.md` (critique
and design) plus the `agent-fleet` skill (the operating procedure). The two
lead agents stop being each other's watchdog — mutual restart cannot catch
correlated failures, proven twice in one day when usage-limit outages stopped
both leads and the task they were carrying at once. Liveness moves to hourly
Routines with restart hysteresis; the leads keep cross-checking *decisions*.
Durable state moves from SendMessage threads to the ledger (each repo's state
block + PR state), so any restarted agent rehydrates from files. IC autonomy
is gated by checkpoint artifacts — draft PR by the end of the first work
block, CI green as the only "done" — not by time.
**Deliberately not armed:** standing Routines burn tokens around the clock;
arming is an explicit "arm the fleet" command, and the skill carries the
steps.

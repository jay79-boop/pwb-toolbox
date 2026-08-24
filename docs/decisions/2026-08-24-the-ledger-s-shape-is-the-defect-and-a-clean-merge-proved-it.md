# The ledger's shape is the defect, and a clean merge proved it

*Decided 2026-08-24.*
**Decision:** Fold the two competing lead-agent ledger corrections (#112, #114)
into one update, and record what arming the fleet measured. Fleet **paused**
at the owner's instruction the same night — Routines disabled, lead sessions
kept, resumable with two calls.
**What the collision actually was:** four of five open branches conflicted with
`main`, every one of them on `CLAUDE.md` and nothing else. Not bad luck on a
busy day and not the fleet's fault: every branch that does real work edits this
block, it is one dense region of prose, and git cannot merge two rewrites of
the same paragraph.
**The finding that matters is the fifth branch.** #111 merges into `main` with
zero conflicts and leaves this block asserting a `main` SHA three merges stale
with four PRs open. Nothing warns anybody. The conflicts were the safe failure;
the clean merge is the dangerous one, because "merge `main` before pushing"
cannot catch a merge with nothing to notice. So the fix is structural — derive
the volatile facts from GitHub at read time, or split into an append-only log
plus a small hand-edited summary — and it is the owner's call, not a passing
session's.
**Measured while arming** (in the notional unit corrected by the entry above —
these are list-price estimates, not charges): a lead's heartbeat wake reads
$3.29–$4.29, almost
all of it reading the ledger and listing PRs. Two leads hourly is ~$180/day
standing before any IC works, which is why the cadence went to 4-hourly (#115)
before the pause. But two long-running sessions ($266 and $154) each dwarfed
the entire fleet schedule — so the first cost question is always whether a
session is still running that should have finished, not whether a Routine is
too frequent.
**Also confirmed:** a Routine firing into a *persistent* session inherits that
session's MCP tools despite storing no connector grants. The creation-time
warning is real but applies to `create_new_session_on_fire`.

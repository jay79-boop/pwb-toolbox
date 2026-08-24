# Operating state

The durable half of this project's ledger. **Volatile facts are deliberately
not written here** — see the rule directly below. `CLAUDE.md` carries the
project documentation and points at this file; decisions live one-per-file in
`docs/decisions/`.

## The rule that replaced the stale copy

This file used to open with a `main` SHA, a count of open pull requests, and a
table of what each one was waiting on. All three were wrong within hours, every
time, and three separate sessions wrote a wrong count on 2026-08-24 alone. Worse,
every branch that did real work also edited that region, so branches collided
here and nowhere else — and one of them merged **cleanly** while silently
reverting the whole block, which no conflict marker could have caught.

So those facts are no longer recorded. **Derive them at read time instead:**

| Fact | Where it actually lives |
| --- | --- |
| Open pull requests, and what each waits on | GitHub — `list_pull_requests` / `pull_request_read` |
| Whether a PR's CI is green | `get_check_runs` (**not** `get_status` — this repo has no legacy statuses, so it returns 0 and means nothing) |
| What `main` points at | `git rev-parse origin/main` |
| Whether a branch conflicts | test-merge it, or `mergeable_state` on the PR |
| What a session or Routine is costing | `list_sessions` / `list_triggers`, in tokens — see the `agent-fleet` skill |

The SessionStart orientation hook already instructs every session to gather this
from git and the GitHub tools before its first reply. It was always doing that;
the hand-written copy here simply out-argued it. Now there is nothing to argue
with.

**If you catch yourself about to write a PR number, a SHA, or a count into this
file — don't.** That is the defect, not the fix for it.

## What still belongs here

Facts that are true for weeks and that no API will tell you: session and trigger
IDs, what each agent is for, the roadmap, the tech stack, and the lessons paid
for in wasted days. Ownership and intent, not status.

## Fleet registry (armed 2026-08-24)

The multi-agent fleet is **PAUSED** as of 2026-08-24 01:44 UTC, at the owner's
instruction. Both heartbeat Routines below are `enabled: false`; the two lead
sessions still exist and hold their context, so resuming is two
`update_trigger` calls with `enabled: true` and nothing has to be rebuilt.
Everything below describes the armed configuration it will resume into.

Protocol: the `agent-fleet` skill;
rationale: `docs/agent-fleet.md` (PR #109). This registry is the ledger entry
the design depends on — a restarted lead rehydrates from here, never from a
peer's memory. Keep it current or the watchdogs are chasing ghosts.

| Role | Session | Heartbeat Routine | Fires |
| --- | --- | --- | --- |
| Fleet lead A — portfolio, assignments | `session_01Wm3BaXEEuPpnMtYxQS5tqi` | `trig_013xjbYAWMedmDmSqcEiEWao` | `28 */4 * * *` |
| Fleet lead B — ledger accuracy, decision cross-check | `session_019HEb7SbiKqKJ5pbpkP84d7` | `trig_01LEMGfXYU3Ngdw4sdqVH4QB` | `58 2-22/4 * * *` |

**A heartbeat wake re-reads about 3M tokens, and that killed the hourly
cadence — though not for the reason first recorded here.** The `$3.29` and
`$4.29` measured on the leads' first wakes are **not charges**. Claude Code
computes that figure locally from token counts priced at API list rates, and
this account is on a subscription: every session `list_sessions` returns
reports `isUsingOverage: false`, and the sessions that died on 2026-08-24 died
with `status: "rejected"` — *blocked*, not billed. Nothing in claude.ai billing
will ever match those numbers. See the 2026-08-24 decision-log entry "The
dollars were never dollars".

What the figure is good for is **volume**. Lead B's wake was 2.78M cache-read
tokens against 18K of output; lead A's, 2.95M against 12K. The real currency is
the five-hour window, which this account hit twice on 2026-08-24 — so two leads
waking hourly is 48 wakes a day, each re-reading ~3M tokens, against a window
shared with every other session and with claude.ai. **The 4-hourly cadence
stands.** To go back to hourly: `update_trigger` with `28 * * * *` and
`58 * * * *` — but "it costs nothing" is not a reason to, because it never cost
dollars in the first place and the window is tighter than the wallet.

The lesson generalises past this repo: a heartbeat that "does nothing" is not
free, because reading the ledger and listing PRs is most of the tokens. If the
fleet needs tighter liveness than 4 hours, the cheap fix is a smaller check
(one `list_sessions` call, no repo read) rather than a more frequent full wake.

**Connector inheritance: confirmed working.** Both leads were fired manually at
01:32 UTC and came back having read GitHub PRs and session state, so a Routine
firing into a *persistent* session does inherit that session's MCP tools even
though the Routine itself stores no connector grants. The warning at creation
time is real but does not bite in mode 2. It would bite a
`create_new_session_on_fire` Routine.

**The two heartbeats are staggered on purpose.** Both were created
at `0 * * * *`, which the server anchors to the creation minute — so both
landed on :28 and would have woken in the same second. Simultaneous watchdog
wakes are the correlated-failure pattern this whole design exists to avoid,
and they would also spike the token burn twice an hour instead of smoothing
it. If either Routine is ever recreated, set the minute explicitly.

**Unverified at arming time:** Routines created through the MCP tool store no
connector grants, and the tool warned that the fired sessions may lack
`mcp__*` tools. Because these fire into *persistent* sessions (mode 2) they
should inherit that session's own tool surface, but this was not confirmed. A
heartbeat that cannot reach GitHub or `list_sessions` fails silently and looks
identical to "nothing changed" — so the first thing to check if the fleet
seems quiet is whether the leads still have their tools.

**Other Routines already on this account** (they are not fleet, do not restart
them): spec-desk stop/target watch, the PR #78 check-in, the desk-agent weekly
review, the daily Grok merge, the monthly credit check. The fleet's wakes are
additive to those — see the budget note in the skill.

**The big burn is long-running sessions, not Routines — and both named here
have since stopped.** When the fleet was armed, the Ollama/night-lab session
read ~$266 and the Kronos/spec-desk watcher ~$154, in the same notional unit as
above and charged to nobody. Re-checked 2026-08-24: the Ollama session is
**archived** at 290.6 after PR #117 merged, and the Kronos session is **idle and
disconnected** at 154.3, its last turn rejected on the session limit. Neither is
running; the "both still running" this block asserted was stale within hours.

What made the Ollama session the outlier was **68.3M cache-read tokens against
180K of output — 379:1**, where healthy sessions in the same listing run 80–100:1.
That is one conversation never cleared, re-read in full on every turn. So the
first question stays "is a session still running that should have finished", and
the second is `cache_read / output`: a ratio in the hundreds means `/clear` was
owed hours ago. `list_sessions` carries both in its `usage` blob.

**#87 and #90 are different jobs and both landed.** The lab asks whether one
strategy's edge survives the data — one strategy, many instruments, two vendor
feeds, a noise floor. The comparator asks whether several real edges belong in
the same account — many strategies, one dataset, correlation and portfolio
metrics. They came to look like one job because #90 filed its decision-log entry
and its roadmap checkboxes under "(PR #87)". If you find yourself about to
rebuild something, check the decision log first.

**Live/Backtesting Strategies:**
- **ICT AM OB** (PR #77, #76, merged) — Session timezones, history tracking, order cancellation. Live for testing.
- **ICT OB+FVG** (PR #75, merged) — Priced entries, session management, mintick conversion. Backtest baseline.
- **4-Week T-Bill Ladder** (PR #68, merged) — Exit planning via Treasury curve. Live with planner watcher.

## Keeping this honest

Four lessons, each paid for.

1. **A branch left alone does not hold still.** #78 went from seventeen commits
   behind and merging cleanly to conflicting and far behind in a single
   afternoon, without anybody touching it, purely because everything around it
   landed.
2. **File a decision entry under the pull request that actually carries the
   work.** #90's entry, filed under #87, is the whole reason two different tools
   looked like one built twice.
3. **A clean merge is not evidence the facts survived.** #111 merged into `main`
   with zero conflicts while reverting this block to a state three merges stale.
   A conflict stops you and demands a decision; a clean merge of two
   contradictory statements just quietly picks one. This is the observation the
   split above exists to answer.
4. **Believe GitHub over any file, including this one.** Where a written claim
   and the API disagree, the API is right and the file is stale by construction.

**#87 and #90 are different jobs and both landed.** The lab asks whether one
strategy's edge survives the data — one strategy, many instruments, two vendor
feeds, a noise floor. The comparator asks whether several real edges belong in
the same account — many strategies, one dataset, correlation and portfolio
metrics. They came to look like one job because #90 filed its decision entry
under "(PR #87)". If you find yourself about to rebuild something, read
`docs/decisions/` first.

## Tech Stack & Dependencies

| Component | Version | Status | Renewal/Update | Cost |
|-----------|---------|--------|-----------------|------|
| **Python** | 3.12 (local), 3.11 (CI) | Current | — | Free |
| **Backtrader** | 1.9.78.123 | Legacy (2019, stable) | No active updates | Free |
| **Interactive Brokers** | ib_insync | Current | Live subscription | ~$10/mo |
| **Hugging Face** | `datasets` | Current | API-based | Free tier / Paid |
| **pandas** | 3.0.5 | Current | Monthly updates | Free |
| **black** | Pinned (requirements) | Current | Jan yearly updates | Free |
| **pytest** | Current | Current | Regular updates | Free |
| **21st.dev MCP** | HTTP server | Current | Per-request quota | ~$0.01/req |

**Critical Path Dependencies:**
- Backtrader: strategy compilation + execution (single point of failure, no replacement)
- pandas: data munging + analysis
- Interactive Brokers: live execution + account data

## Roadmap

**Now (This week — parallel tracks):**

*StrategyComparator (PR #90):*
- [x] Implement `StrategyComparator` — runs all three strategies on identical price data
- [x] Add correlation matrix calculation (Pearson + rolling)
- [x] Add portfolio-level metrics (combined P&L, win rate, Sharpe, max drawdown)

*Backtest Lab (PR #87):*
- [ ] Test on 90-day ICT price history
- [ ] Success: See if 15-Min Reversal adds value or just adds noise

*Desk Agent (PR #78):*
- [ ] Implement risk model: Greeks calculator, margin tracker, exposure aggregator
- [ ] Define position size rules (per-strategy caps, portfolio exposure cap)
- [ ] Add live IB position feed + alerts on >80% margin usage
- [ ] Add position limit enforcement (reject trades that violate caps)
- [ ] Success: Can trade all three strategies without blowing up

*VWAP lab (PR #111):*
- [x] `SessionVwap` + `VwapStrategy` (fade / pullback / cross-as-control) + confirms
- [x] `tools/vwap_lab.py` — costs, bps, per-setup two-vendor noise floor
- [x] `pine/vwap_strategy.pine` for TradingView paper trading
- [ ] Run on real ES bars from both vendors (owner's machine — feeds live there)
- [ ] Success: fade clears the noise floor and the crossover control fails

*15-Minute Reversal (PR #71):*
- [ ] Finish strategy logic (entry, exit, hold conditions)
- [ ] Backtest on 6 months of data
- [ ] Validate win rate, Sharpe, max drawdown vs. ICT strategies
- [ ] Await backtest lab results before deciding if it's live-tradeable

**Next (After "Now" merges — 2-3 days):**
- [ ] Merge #87 (backtest lab) → use results to size positions in desk agent
- [ ] Merge #78 (desk agent) → build live execution harness on top of it
- [ ] Merge #71 (15-Min Reversal) → add to desk agent position tracking
- [ ] Run full portfolio backtest: all three strategies with desk agent constraints

**Later (Backlog):**
- [ ] Live execution: connect desk agent to IB, enable live trading
- [ ] Performance analytics: daily P&L dashboard, monthly statement generation
- [ ] Trade journal automation: hook desk agent events to trade journal
- [ ] Strategy upgrade: Backtrader 1.9.78 → investigate modern fork or Zipline
- [ ] Risk monitoring: multi-day drawdown alerts, portfolio stress tests

**Done (Reference):**
- [x] ICT AM/OB Strategy (PR #77, #76) — live testing
- [x] ICT OB+FVG Strategy (PR #75) — backtest baseline
- [x] T-Bill Ladder Tool (PR #68) — live with planner watcher
- [x] Operating System (PR #88) — CLAUDE.md as single source of truth

## Why this shape

The goal has not changed: "what's the current state?" should be answered by
reading, not by asking. What changed is where each kind of fact lives, because
one undifferentiated block could not hold all three at once.

| Kind of fact | Lives in | Why there |
| --- | --- | --- |
| Volatile — PRs, CI, SHAs, counts | **Nowhere.** Derived from git and GitHub at read time | It is wrong within hours however carefully it is written |
| Durable — ownership, roadmap, IDs, lessons | **This file**, hand-maintained | No API knows it, and it changes on the order of weeks |
| Historical — why a choice was made | **`docs/decisions/`, one file per entry** | Two branches adding different files never conflict; two branches inserting at the top of one list always do |

The third row is the load-bearing one. The old log was a single list that every
branch inserted into at the same point, which is most of why branches collided
on `CLAUDE.md` and nothing else. One file per decision removes the shared
insertion point entirely, so the conflict has nowhere left to form.

This file is no longer loaded into every session automatically — `CLAUDE.md` is,
and it points here. That is deliberate: most turns do not need the roadmap or
the fleet registry, and a session that does need them can read one file.

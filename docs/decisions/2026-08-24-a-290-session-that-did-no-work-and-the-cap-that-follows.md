# A $290 session that did no work, and the cap that follows

*Decided 2026-08-24.*

**Decision:** Cap PR stewardship in this repo. `.claude/skills/steward/SKILL.md`
is now read before any session acts on a CI or review event here. It forbids
self-re-arming check-ins outright, forbids binding a scheduled wake to a
long-lived session, and states that a green PR blocked on the owner is finished
work to be reported once, not polled.

**Read the figures as metering, not billing.** This entry originally said
"$290.64 billed". It is not billed — see
[The dollars were never dollars](2026-08-24-the-dollars-were-never-dollars.md),
confirmed with the owner the same day: no charge appears anywhere and every
session reports `isUsingOverage: false`. The number is Claude Code's local
estimate at API list prices. It still measures the thing that matters — how much
of a five-hour window a session consumed — so the finding stands unchanged and
only the unit was wrong.

**What happened:** "Ollama trade stress testing" was archived after 19 hours at
**$290.64-equivalent metered — 68,334,097 cache-read tokens against 180,373
output tokens**, a 379:1 read-to-write ratio. A second session doing the same
added $18.44. Across the account roughly 56 scheduled wakes a day were servicing
five open pull requests.

**The mechanism:** the session finished its real work (the night lab), pushed it,
opened PR #117 — and was then captured by the default PR-steward protocol, which
has a session subscribe to its own PR and schedule a self-re-arming hourly
check-in until the PR merges or closes. Each wake reloaded the whole 19-hour
context to perform a small PR check.

**The owner set none of this up.** It is default behaviour, and it is correct for
a repo with human reviewers who arrive on their own schedule. This is a
single-owner fork where the only reviewer merges by saying "merge it", so the
watcher always outlives the work.

**Compounding it:** every branch edited the Operating System block, so four of
five branches conflicted on that file and nothing else — giving each watcher a
conflict to re-resolve that regenerated on the very next merge. That defect is
fixed separately by
[The ledger's shape is the defect](2026-08-24-the-ledger-s-shape-is-the-defect-and-a-clean-merge-proved-it.md).

**Why no watcher is needed:** the SessionStart orientation hook already shows
open PRs and their CI at session start, so nothing has to stay awake to report
state.

**Also found:** the monthly credit-check Routine watches ElevenLabs, Voice.ai and
Blotato — but not Claude usage itself, and not Alpha Vantage. It was watching the
cheap things.

**Unresolved, and the owner's call:** Routines created from a session cannot
carry MCP connector grants on this org, so a fresh-session watch runs blind with
no market data. A scheduled watch that genuinely needs a connector has to be
created from the claude.ai Routines UI.

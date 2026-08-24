# Guard the irreversible path, not the whole surface

*Decided 2026-08-24.*

**Decision:** Bound what can actually cost money, after measuring the window
drain showed that token metering was never this repo's largest exposure.
`docs/token-drain-2026-08-24.md` has the measurement;
`docs/spend-safety.md` has the ranked inventory of every surface that can reach
a card; the `spend-safety` skill carries the rules to future sessions and other
projects.

**The constraint that shaped every choice was the owner's:** *a guardrail that
blocks your own automation gets switched off, and a switched-off guardrail
protects nothing.* So the question is never "is this risky?" but "can this
specific call move money?" — and if not, it is left completely alone. That
constraint corrected one of this work's own proposals: denying
`pwb_toolbox.execution` at the permission layer would have blocked imports and
the test suite, so the guard went into the code instead, where it can be precise.

**What was actually exposed:** `ib_connector.place_orders` and `execute_orders`
went straight through to `ib.placeOrder` on `main`, bounded by account equity and
nothing else, while `.claude/settings.json` carried one allow entry and zero deny
entries — the 72-allow/12-deny model that reasons about exactly this was sitting
unmerged on PR #78. A runaway meter costs a window; that path costs a position.

**The two-key pattern:** a live-account order needs `allow_live_orders=True` in
the calling code **and** `PWB_ALLOW_LIVE_ORDERS` in the environment. Neither
alone suffices, so a stray import, an unattended run, or a flipped config file
cannot trade. IB's paper ports (4002 — the module's own default — and TWS's 7497)
return immediately with no unlocks, so paper automation, backtests and the suite
never notice the brake exists. An unrecognised port is treated as live: fail
closed. Copy this shape for any new irreversible action, and make the error name
both remedies, because an error that does not say how to proceed just gets worked
around.

**The deny list is two entries** — `blotato_buy_credits` and Windsor's
`execute_action`, which writes budgets and bidding on five ad platforms. They sit
in `deny` rather than merely absent from `allow`, so a later blanket grant cannot
silently restore them. Keeping the list short is not laziness; it is the rule
above being obeyed.

**`tools/spend_watch.py` refuses to invent a burn rate.** A single snapshot
reports each session's *lifetime* metered total, so no rate can honestly come
from one file — and that exact misreading turned a figure accumulated over
nineteen hours into an apparent hourly one, twice, during this incident. Rate
findings require a `--baseline` snapshot to diff against.

**What actually protected the account was a default, not a decision.** The
five-hour limit *rejected* rather than continuing on overage. A limit that
refuses is safe; a limit that bills is a number with no upper bound attached to a
card. Every layer in `docs/spend-safety.md` exists so the next one is a decision.

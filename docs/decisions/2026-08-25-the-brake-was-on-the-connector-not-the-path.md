# The brake was on the connector, not on the path

*Decided 2026-08-25.*

**Decision:** Give `CCXTConnector` the same two-key live-order brake
`IBConnector` has, and move the shared primitives into
`pwb_toolbox/execution/_live_guard.py` so the two connectors cannot drift on
what "live" means. Sandbox is exempt, reads off the connected exchange, and an
exchange that is not demonstrably in sandbox is treated as live.

**What was exposed.** `2026-08-24-guard-the-irreversible-path-not-the-whole-surface.md`
closed the IB path and recorded the principle. It did not close the crypto one.
`CCXTConnector.place_orders` had no guard of any kind — no code key, no
environment key, no sandbox detection — and submitted straight to the exchange
on `PWB_CCXT_API_KEY`. The module docstring demonstrated exactly that. So the
same factory call was fail-closed for one broker and wide open for the other:

```python
create_connector({"broker": "ib"})                        # refused live orders
create_connector({"broker": "ccxt", "exchange": "..."})   # placed them
```

**Why it stayed invisible for a day, which is the part worth keeping.** Two
reasons, and neither was carelessness.

The brake was written as a property of `IBConnector` rather than of *placing an
order*. A guard attached to a class protects that class; a second class reaching
the same venue is a second, unguarded path that nothing announces. **A guard
belongs to a path, not to a package** — and the moment one exists, the question
to ask is "what else reaches this venue?", not "is this class safe now?"

And `docs/brokers.md` described the CCXT path as "built, unused". That was an
assumption about usage doing the work a guard should do. It was also wrong at
the time it was written — the owner reports using it live or intending to
shortly — but it would be the wrong basis for leaving a path open even if it had
been right. Unused today is not unused tomorrow, and crypto is the side the spec
desk actually trades.

**The shape of the fix.** Sandbox mode returns immediately with no unlocks, so
testnet automation and the suite never notice the brake — the owner's standing
constraint that a guardrail blocking legitimate automation gets switched off.
Live requires `allow_live_orders=True` in the calling code *and*
`PWB_ALLOW_LIVE_ORDERS` in the environment.

Two details carry most of the safety:

- **The factory reads the first key from the config mapping only, never from the
  environment.** The second key already is an environment variable; a factory
  that read both from the environment would let one exported variable satisfy
  the pair, collapsing two keys into one. Pinned by a test.
- **Sandbox is read from the connected exchange (`isSandboxModeEnabled`), never
  from the constructor flag.** A connector that asked for sandbox and did not
  get it is still live. `set_sandbox_mode` raises on an exchange with no testnet,
  so the failure is loud rather than a "sandbox" connector pointed at the live
  venue.

**Also corrected here:** `.claude/skills/steward/SKILL.md` said the 2026-08-24
incident cost "$290.64 billed". Nothing was billed — `cost_usd` is an
API-equivalent meter and the account never entered overage, as
`2026-08-24-the-dollars-were-never-dollars.md` establishes. The skill is read
before every PR event in this repo, so the error was propagating into how
sessions reason about consequence. `docs/spend-safety.md`'s "gap that is open
right now" section was likewise stale, still describing a permission model that
had since landed.

**What this does not claim.** The guard bounds the *order* path. It does nothing
about withdrawals, transfers, or anything else an exchange API key can authorise
— the real bound there is the key's own permission scope at the exchange, which
is a setting on the venue and not something this package can enforce.

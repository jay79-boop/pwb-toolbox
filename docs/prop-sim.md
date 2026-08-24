# The prop-firm pricer

`tools/prop_sim.py` answers one question with arithmetic instead of a
pitch: **under this firm's published rules, what is one evaluation attempt
worth?** Pass probability, expected attempts and cost to funded, expected
funded payouts, EV per attempt — for a literal coin flip, or for the real
R-distribution a sim exported for the night lab.

Where it came from: a live stream's EV-per-attempt calculator showing a
zero-edge strategy positive against a firm's fee structure
(`docs/field-notes-prop-firms-and-data.md`). The mechanism is a capped
left tail (you lose the fee, not the drawdown) against an open right tail
(funded payouts), priced flat — an edge against the fee schedule, never
the market.

## The result worth internalizing

For the pure symmetric walk, P(pass) = D/(T+D) **no matter the position
size** — pinned against the closed form in tests. "Sizing optimizes the
odds" is only true *through the rules*, and the simulator demonstrates
each mechanism because it is in the arithmetic:

- a **time limit** rewards size (big steps reach the target inside the
  window more often);
- a **consistency rule** taxes size (one giant day can't carry the pass);
- a **trailing drawdown** is strictly worse than fixed, and the demo run
  shows it crushing the naive 40% ruin odds to ~22%;
- on the fictional demo rule set, the coin flip is **EV-negative at every
  size** — the countermeasures exist because they work.

## Usage

```bash
python tools/prop_sim.py rules-template > firm.json   # fill from the ToS
python tools/prop_sim.py evaluate --rules firm.json --risk 200
python tools/prop_sim.py evaluate --rules firm.json \
    --r-dist night_lab/sim_trades.json --size-sweep 0.5,1,2
```

`rules-template` prints every knob: target, drawdown (fixed /
eod_trailing / intraday_trailing, with the lock-at-breakeven ratchet),
fees, daily loss limit, consistency %, min/max days, trades per day,
payout share and cadence, funded horizon, withdrawal buffer.

## The honesty section

- **Numbers in a rules file must come from the firm's current published
  terms.** The shipped default is fictional round numbers on purpose;
  rule sets heard second-hand are recorded in the field notes as as-heard,
  not here as facts.
- Several firms' terms permit payout denial at their discretion, and
  hold-time / consistency rules exist precisely to tax rule-arbitrage.
  A positive EV on paper is a claim about the rules as written.
- The funded model is deliberately conservative: finite horizon, periodic
  withdrawals above a buffer, blowout ends it. No infinite-run fantasies.
- None of this transfers to trading one's own capital, and nothing here
  places a trade. Any real experiment is a walled-off pot under the spec
  desk's contract: fixed budget of attempts, scored record, review gate.

Engine is pure, seeded, stdlib; Monte Carlo pinned against gambler's-ruin
closed forms (`tests/test_prop_sim.py`).

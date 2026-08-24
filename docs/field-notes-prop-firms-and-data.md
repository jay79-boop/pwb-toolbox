# Field notes: prop-firm economics, order-flow data, and a backtest checklist

Distilled from a live algo-trading stream the owner sat in on (2026-08-24).
Everything here is as-heard from a voice chat: claims are recorded with that
provenance and must be verified against primary sources before anything
trades on them. Handles and platform names are deliberately omitted.

The stream's value was threefold: one genuinely new piece of math worth
building (prop-firm expected value), one market-data economics briefing
worth writing down so we never chase it, and a live backtest-hygiene
argument that doubles as an audit of our own pipeline — which it caught a
real hole in.

---

## 1. The prop-firm EV thesis (the one thing worth building)

The centerpiece: a calculator showing **expected value per evaluation
attempt** for a strategy with *zero market edge* — a literal coin flip —
coming out positive against a prop firm's fee structure, over ~3,000
Monte Carlo simulations, eval fee included (~$540 all-in in their example).

The mechanism is not market edge and nobody claimed it was:

- An eval is "reach profit target +T before drawdown −D, pay fee F."
  For a symmetric zero-edge strategy this is the classic gambler's ruin:
  P(pass) ≈ D/(T+D) before the firm's complications.
- Your loss beyond −D is **capped** (you lose the fee, not the drawdown);
  a funded account's payouts are not capped symmetrically. Capped left
  tail, open right tail, priced at a flat fee — that asymmetry is the
  entire "edge," and it is an edge against the fee schedule, not the
  market.
- **Position sizing changes the pass probability without touching market
  edge** — "sizing only optimizes the odds of passing on their specific
  rule set." Bigger size = fewer, larger steps = different ruin odds and
  different interaction with consistency rules.
- The firms know, and their countermeasures are exactly the parameters a
  simulator must price: trailing drawdown (strictly worse than fixed D),
  consistency rules (max % of profit from one day), minimum trading days,
  time limits, activation fees, payout caps and schedules.

Firm-rule specifics read aloud on stream (verify against current ToS
before relying — these change):

| Firm | Rule as heard |
| --- | --- |
| TopStep | HFT defined as thousands of trades/day with average duration in seconds, explicitly not minutes |
| Lucid | HFT = high volume in seconds/milliseconds; flagged if ~40–50% of profits come from holds under 4.99s |
| Alpha Futures | imposes minimum hold times |
| (example math) | ~$540 total cost incl. eval fee; 3,000-sim pass model; 1% consistency rule |

**Why this fits this repo:** it is pure, testable arithmetic over a
published rule set — no model needed, closed forms (gambler's ruin)
available to pin the Monte Carlo against, and our sims already export the
R-distributions a non-coin-flip version would consume. A `prop_sim` tool
would answer, per firm: pass probability, expected attempts to funded,
expected cost to funded, and EV per attempt — for a coin flip AND for our
actual strategy distributions.

**The honest caveat the stream also supplied:** hold-time and consistency
rules exist precisely to tax this play, several firms' terms are written
broadly enough to deny payouts at discretion, and none of this transfers
to trading one's own capital. If built, it is a pricing tool for a
walled-off experiment under spec-desk-style rules, not a strategy.

## 2. Order-flow data economics (write it down so we never chase it)

As-heard price points, unverified:

- Historical L3 (market-by-order) on NQ: **~$2–3k for ~5 years** via a
  major vendor; a multi-instrument historical bundle quoted ~$1.4k.
- Live order-flow for commercial use: **~$1,750/month, minimum one-year
  commitment, through vendor sales.** Commercial licensing, not the data,
  is the expensive part everywhere.
- One retail platform bragging six figures spent on historical data offers
  exactly one month of L2 history — consistent with the licensing math.

The two structural arguments, which stand regardless of prices:

1. **Coverage:** consolidated tape misses off-exchange routing; the
   figure thrown around was "you see ~30% of the open market" for
   equities. (Futures are centralized at CME, so this argument is weaker
   there — the chat conflated the two.)
2. **Machinery:** using L3 means modeling queue position per order, which
   means Rust/C++, not a Python bar loop. The data cost is the cheap part.

**Standing conclusion for this repo:** L2/L3 is not applicable to
anything we run — the strategies operate on bars, the edge claims are
bar-scale, and the wisdom doc's evidence base is bar-scale. Recorded here
so the question is answered once. (Also heard and disputed on stream:
"Renaissance runs on L1" and "OHLC has no alpha" — bar talk in both
directions, no authority, recorded only as color.)

## 3. The hygiene argument — as an audit of our own pipeline

The chat spent an hour interrogating a screen-shared backtest with an 81%
win rate. Every question they asked is a check this repo either already
enforces or visibly lacks:

| Stream question | Our status |
| --- | --- |
| Same-candle TP+SL: win or loss? | **Enforced** — stop assumed first, entry fills only on a later bar (`reversal_15m_sim`, tested) |
| Slippage and fees included? | **CAUGHT US** — `backtest_lab` charges costs always; `reversal_15m_sim` charges **zero**, and its frictionless trades feed the night lab's record. Violates our own doctrine. |
| Sequence luck / Monte Carlo? | **Enforced** — `night_lab` bootstrap resampling of trade order |
| Out-of-sample split? | **Gap** — `season_scan` split-halves, `backtest_lab` has the vendor noise floor, but the reversal sim reports one undivided window |
| Parameter sensitivity with this many knobs? | **Half-built** — `night_lab.cliff_score` exists but nothing sweeps the sim's rr / sma-length into it automatically |
| Win rate without RR is meaningless | **Enforced** — everything scores in R-multiples |
| "Profitable" vs what benchmark? | **Enforced** — bps-of-price doctrine, T-bill ladder as the risk-free anchor |
| Feeding all candles at once (leakage)? | **Enforced** — bar-walk simulation |
| Session hours correct for the instrument? | **Enforced** — the timezone lessons are pinned by tests |
| Sharpe/Sortino/profit factor visible? | **Gap (minor)** — sim summary lacks them; `pwb_toolbox.performance` already implements all three, unwired |
| An "optimize" button | **Deliberately absent** — that button is an overfitting machine; our answer is fragility scoring, not parameter search |

## 4. Tackle list

- [x] **Charge costs in `reversal_15m_sim`** — per-side cost in ticks,
      on by default per doctrine, `--no-costs` for comparison runs; every
      R downstream (night lab record included) becomes net-of-friction
- [x] **`tools/prop_sim.py`** — firm rule set in, {pass probability,
      expected attempts, cost-to-funded, EV/attempt} out; Monte Carlo
      pinned against gambler's-ruin closed forms; consumes coin-flip or
      our sims' exported R-distributions
- [x] **Out-of-sample split in the sim** — report first/second half
      separately, same spirit as season_scan's halves gate
- [x] **Wire sim parameter sweeps into night-lab fragility** — rr and
      sma-length swept, cliff-scored overnight
- [x] **Sim summary metrics** — profit factor, Sortino, max drawdown in R,
      from the existing `pwb_toolbox.performance` functions
- [ ] **Wisdom-doc proposal** — a sourced section on prop-firm economics
      and the L2/L3 conclusion, staged under the propose-then-approve
      contract, not silently edited

## For any strategy tool built here (the generalizable checklist)

A backtest in this repo is not believable until: same-candle ambiguity is
resolved pessimistically; costs are charged; trade order has been
resampled; at least one out-of-sample or second-source check exists;
parameters near the chosen ones have been scored; results are in
R-multiples and bps, never raw points or win rate alone; and the
benchmark is named. The stream watched a room relearn each of these
against one 81%-win-rate screenshot — this list is what "we already knew"
looks like written down.

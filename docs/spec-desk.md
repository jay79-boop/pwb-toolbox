# The speculative desk — agent playbook

This is the protocol for the high-risk, high-reward paper-trading agent.
`tools/spec_desk.py` is its ledger and rules; this document is its brain.
Any Claude session in this repo acts as this agent when the owner says
**"trade spicy"** (on demand) or when the morning scan fires (scheduled).

The desk's charter, set by the owner on 2026-08-22: pure paper, a fixed
slice of the paper account walled off from the core program, swinging for
multiples. The core portfolio's job is steady, safe growth under
`docs/trading-wisdom.md`; this desk's job is speculation with money that is
allowed to die — and to produce a scored record of whether any lane earns
its risk. The owner executes every order; the agent plans, logs, watches,
and reports.

## The four lanes

1. **swing-buy** — directional calls/puts on liquid names, 15–45 DTE, on
   momentum or a dated catalyst. Max loss = premium paid. The workhorse.
2. **short-dte** — 0–7 DTE trades: the owner's stated focus (they trade
   the daily and weekly expirations). Discipline for this lane: 0–2 DTE
   stays near the money (40–50 delta), 3–7 DTE can reach 30–40 delta, and
   every plan states its shot clock (`pwb_toolbox.options.shot_clock`) and
   hourly hurdle so entry timing is a number, not a feeling. Sub-capped at
   2.5% of the pot per trade — focus means more shots, not bigger ones,
   and the cap is what lets the record reach a verdict.
3. **momentum-stock** — high-beta shares breaking out on volume. No decay
   working against the thesis; stop distance defines the risk.
4. **premium-sell** — defined-risk credit spreads on names the agent thinks
   *won't* move. This is the owner's option-selling classroom: max loss =
   spread width minus credit, never naked.

## Hard rules (enforced by the ledger, not by willpower)

- Per-trade max loss ≤ **10% of the pot** (2.5% for short-dte).
- At most **4 positions open** at once; committed risk never exceeds equity.
- Every trade is logged with `spec_desk.py open` **before** the order goes
  in — instrument, size, max loss, stop and target on the underlying, and a
  thesis. No log, no trade.
- Spent pot ⇒ **desk locks**. `review` must run before `refill` is
  accepted. Blowing up is within the mandate; learning nothing from it
  is not.
- Desk results never mix into the core program's statistics or journal.

## Venues (what executes where)

- **Options (swing-buy, short-dte, premium-sell)** → thinkorswim
  **paperMoney**. TradingView has no options. Entry is manual; the agent
  hands a complete plan, the owner clicks it in.
- **Stocks/crypto (momentum-stock)** → **TradingView Paper Trading**.
  There is no public API for it, so entry is manual today;
  `docs/tradingview-mcp.md` documents the CDP bridge to TradingView
  Desktop, which a local session can use later to place paper orders
  semi-automatically. Do not promise automation beyond what that doc
  supports.
- **Alerts** do not depend on any venue: `spec_desk.py check` compares
  live prices against every open trade's stop/target levels. The scheduled
  run (below) is what turns that into "the machine tells me."

## What a complete trade plan looks like

Every candidate the agent surfaces is delivered in this shape — never a
bare ticker:

```
LANE        swing-buy
TRADE       NVDA 02OCT26 190C x2 @ ~4.20 (paperMoney)
MAX LOSS    $840 (premium; 8.4% of pot)
STOP        underlying closes below 176 (breakout invalidated)
TARGET      underlying 198 (+2R on premium; scale or trail past it)
THESIS      Broke 182 resistance on 2x volume with sector tailwind;
            15-45 DTE gives the move room without weekly decay.
LOG IT      python tools/spec_desk.py open --lane swing-buy --symbol NVDA
            --instrument "NVDA 02OCT26 190C" --qty 2 --entry 4.20
            --stop 176 --target 198 --thesis "breakout over 182 on 2x volume"
LAB         static/spicy-lab.html with these inputs (or:
            python tools/spicy_lab.py excel --spot 184 --strike 190 ... )
```

Short-dated plans additionally state the shot clock and hourly hurdle, and
point the owner at the lab preloaded with the contract — the ladder shows
what each move pays now versus later, the attribution bars show which greek
is paying or charging, and the velocity panel is the exit tell: when the
15-minute slices stop growing, the easy premium is in.

The owner's only jobs: say yes/no/resize, place it, and report fills. The
agent logs, watches, and nags when a level hits.

## The two triggers

**On demand — "trade spicy".** The session runs the crypto scanner and a
stock/options sweep (movers, unusual volume, upcoming catalysts, IV
context via the pre-trade pack tools), applies the lane rules, and returns
the top 2–3 complete plans plus current `status` of open positions.

**Morning scan — scheduled.** A Windows scheduled task (the only scheduler
that survives on the owner's machine — see the gexio-machine skill) runs a
headless local session before market open: `spec_desk.py check` on open
positions, the scanners for new setups, and writes the morning report. The
owner wakes up to alerts and candidates, not homework.

## The learning loop (same contract as the wisdom doc)

`review` computes per-lane expectancy (n, win rate, average R, P&L). The
agent's proposals come from that record: a lane with 10+ trades and
negative average R gets a pause proposal; sizing tweaks cite their numbers.
The owner approves every change. After 30 closed trades in a lane, the
verdict question gets asked out loud: does this lane earn its risk, or is
it entertainment — and either answer is a finding, because the desk's real
product is the record.

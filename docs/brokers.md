# Broker field note: what the desk actually needs, and which of ten supply it

*Compiled 2026-08-24, against a ten-broker shortlist the owner brought in.*

Fee schedules and API terms change. Every number below carries its source and
was read on the compile date; **re-read the source before anything trades on
it.** This note is a decision aid, not a rate card.

---

## First, what this desk actually trades

Ranked by how much of the program each accounts for, read out of the repo
rather than assumed:

| Rank | What | Where it lives | Maturity |
| --- | --- | --- | --- |
| 1 | **0–7 DTE options** on liquid names — the owner's stated focus | `docs/spec-desk.md`, `short-dte` lane | Paper, active |
| 2 | **15–45 DTE directional options** | `spec-desk`, `swing-buy` lane | Paper, active |
| 3 | **Defined-risk credit spreads**, incl. weekly SPX/XSP condors | `spec-desk` `premium-sell`; the condor mock run | Paper, active |
| 4 | **Momentum stocks** | `spec-desk`, `momentum-stock` lane | Paper, active |
| 5 | **ES/NQ index futures** — ICT AM OB, ICT OB+FVG, VWAP fade | `pine/`, `tools/vwap_lab.py`, `tools/backtest_lab.py` | Backtest; **has not cleared the two-vendor noise floor** |
| 6 | **Crypto momentum** | `tools/crypto_scan.py`, `CCXTConnector` | Screener only |
| 7 | **T-bills** | `tools/bill_ladder.py` | Live, and not a brokerage question |

**Four of the seven are options on equities, ETFs and indices.** Futures is one
line, and it is the least mature one — `docs/backtesting.md` is an extended
argument that a single-vendor futures backtest here produced a confidently
wrong answer twice over. Any broker ranking that leads with futures is ranking
against the wrong desk.

## Second, what is already wired

| Venue | Role today | Programmatic? |
| --- | --- | --- |
| **Interactive Brokers** | The live execution path — `pwb_toolbox/execution/ib_connector.py`, `tools/ib_server/`, ~$10/mo data | **Yes**, `ib_insync`. Covers equities, options, futures, FX |
| **Schwab / thinkorswim** | Options paper (`paperMoney`); real fill history via `pwb_toolbox/journal/schwab.py` | **Partly** — see the trap below |
| **TradingView** | Charts, Pine strategies, stock/crypto paper | No public API; CDP bridge only (`docs/tradingview-mcp.md`) |
| **CCXT exchanges** | Crypto execution path, built, unused | Yes |

### The trap that decides this whole question

Schwab does have an individual Trader API — equities, ETFs, options including
multi-leg and index options, registered at `developer.schwab.com`. It looks like
the obvious free win, because the account already exists.

**It cannot drive paperMoney.** The Schwab Trader API connects to live accounts
only; its sandbox is a synthetic-data environment for validating auth and
payload shapes, not a paper account. Schwab support has confirmed this
directly.

That matters more than any commission number here, because of rule 9 in
`docs/trading-wisdom.md`: *no system trades real money until expectancy is
positive over ≥30 out-of-sample trades.* The paper record **is** the
out-of-sample sample. So the desk needs an API that trades **paper**, and the
one broker already holding the options account is the one that cannot supply
it.

Every order in the spec desk is currently typed in by hand. That is the
bottleneck — not commissions, not futures access.

---

## The ten, scored against that

### Worth adding

**1. Tradier — the recommendation.**

The only broker on the list whose *paper* trading is the same API surface as
live. The sandbox is a genuine paper-trading account against the full trading
API with delayed market data; flipping a lane to live is a token and base-URL
change, not a rewrite.

- Pro plan: $10/mo after the promo period, $0 commission on equity and option
  trades.
- Single-listed index options (SPX, XSP, NDX, RUT): $0.35/contract on Pro, on
  top of exchange/clearing/regulatory fees. A four-leg condor is ~$1.40 a side.
- US equities and options only, ETFs included.

*Pros:* closes the manual-entry loop, so `spec_desk.py open` could place and
record the paper order instead of nagging the owner to; cheapest venue on this
list for the condor program; REST + Python, which is what this repo is;
$0 equity/option commission means the scale-out protocol (sell half at +100%)
costs nothing to execute.

*Cons:* **no futures, no crypto** — it does not replace IB, it sits beside it;
delayed data in the sandbox, so fills are indicative and slippage assumptions
still have to be charged by hand per `docs/backtesting.md`; smaller firm than
the incumbents; no charting worth using, so TradingView stays.

**2. tastytrade — the alternative, if one account should cover options *and*
futures.**

An official free Open API with full read/write on equities, options, futures
and crypto, plus a sandbox and SDKs.

- Stock/ETF options: **$1/contract to open, $0 to close**, capped $10/leg.
- Options on futures: $2.50/contract to open, $0 to close.
- Micro futures: $0.75/contract each way.

*Pros:* $0-to-close is genuinely well-matched to the exit protocol in
`docs/spec-desk.md` — scale-outs, breakeven stops and the five exit flags all
mean closing more often than opening, and here that half is free; one account
spans lanes 1–5; the platform is built by and for options sellers, which is the
`premium-sell` lane's classroom.

*Cons:* the public sandbox **resets every 24 hours** and supports only a few
underlyings — useless for accumulating a 30-trade record. A real paper API
exists but it is third-party (tastyware, paid) and currently covers equity and
index options only, not futures options. And $1/contract to open is real money
against Tradier's $0 on the same trade.

### Add only if a specific need appears

**3. TradeStation.** One API key across equities, options and futures, REST/JSON
plus FIX. Micro E-mini $0.50/side ($1.00 round turn); futures via FuturesPlus
$1.75/contract/side; index options $1.00/executed contract, as are
direct-routed options. It is the only broker here besides IB covering all three
asset classes under one credential. But IB already does that, index options at
$1.00/contract is the wrong price for a weekly condor program, and adding it
buys no capability this desk lacks.

**4. NinjaTrader.** Futures only — **no stocks, no ETFs, no equity options**, so
it addresses one line of the seven. Commissions are tiered against the license:
free plan $0.39/micro/side, $99/mo plan $0.29, and a $1,499 lifetime license
that unlocks $0.09/micro and $0.59/standard per side, plus exchange, clearing
and NFA fees. The lifetime tier only pays back at volume that this desk does not
trade and has not earned the right to trade. NinjaScript is C#; this repo is
Python and Backtrader.

**5. Webull.** A real OpenAPI — stocks, options, futures, crypto and event
contracts over HTTP, MQTT streaming and gRPC, with Python and Java SDKs, and
approval in 1–2 business days. Technically the broadest API on the list after
IB's. But that is the problem: it duplicates IB's coverage and adds a second
integration for no new capability, and no paper endpoint that solves the rule-9
gate.

### Skip

**6. AMP Futures** and **7. Optimus Futures.** Futures-only, and genuinely cheap
— both start around $0.25/contract, with intraday micro margins of $40 (AMP) to
$50 (Optimus) and E-mini around $400. AMP's micro rate runs up to $0.85
depending on plan. Both reach the market through Rithmic, CQG and TT rather than
a first-party REST API, which means a paid third-party feed and a non-Python
integration path. They would be the right answer for a desk going all-in on ES
scalping. This desk's futures strategies have not yet been run on real ES bars
from two vendors, which is the gate `docs/backtesting.md` exists to enforce.
Buying execution before the edge clears the noise floor is backwards.

**8. Plus500 Futures.** $0.49/micro and $0.89/standard and E-mini, plus $0.02/side
NFA and exchange fees, with $0 platform fee and $0 market-data fee, CFTC/NFA
regulated, and API access is offered. Cheap and clean. But it is a retail app
with no options, a $10-per-contract liquidation fee, and an ecosystem built for
discretionary clicking rather than systematic traders.

**9. E*Trade.** The REST API is real and free — accounts, balances, positions,
quotes, option chains and order placement, with sandbox and production — but it
is secured by **OAuth 1.0a**, a dated scheme, and gated behind an API agreement
and user-intent survey. It offers nothing Schwab does not already, and Schwab is
already open here.

**10. Nothing at all** deserves to be on the list as a real option, and it is
the second-best answer. Adding a broker advances none of the open roadmap items
in `docs/state.md`: the VWAP fade still has to run on real ES bars from two
vendors, the desk agent's risk model is unfinished, and no lane has 30 closed
paper trades. The only argument that beats "wait" is the one made above — that
manual order entry is what is throttling the paper record, and a paper API is
the unlock.

---

## Verdict

| Question | Answer |
| --- | --- |
| Best fit for what this desk trades | **Tradier**, on the paper-API argument, not on price |
| Best if one account must span options *and* futures | **tastytrade** |
| Worth adding to the existing testing setup | Tradier — it is additive to IB, not a replacement |
| Cheapest for the SPX/XSP condor program | Tradier Pro ($0.35/contract) |
| Best futures execution, in isolation | NinjaTrader lifetime, then Optimus/AMP — **and premature** |
| Safe to skip entirely | E*Trade, Plus500, Webull, AMP, Optimus |

**What actually gets built if the answer is Tradier:** a `TradierConnector`
beside `IBConnector` and `CCXTConnector`, registered in
`pwb_toolbox/execution/broker_factory.py` under `PWB_BROKER=tradier`, pointed at
the sandbox first. `spec_desk.py open` gains the ability to place the paper
order it currently only logs. Nothing about the gates changes — costs still get
charged, R-multiples still get recorded, and rule 9 still holds the door.

## Sources

Read 2026-08-24. Verify before relying.

- tastytrade — [commissions & fees schedule](https://assets.contentstack.io/v3/assets/blt7dc2e3d4a7071563/blt2b752fef372188fe/commissions-and-fees), [developer portal](https://developer.tastytrade.com/), [sandbox environment](https://developer.tastytrade.com/sandbox/), [paper API docs](https://tastyworks-api.readthedocs.io/en/latest/paper.html)
- Tradier — [pricing & plans](https://tradier.com/individuals/pricing), [API getting started](https://docs.tradier.com/docs/getting-started), [FAQ](https://docs.tradier.com/docs/faq)
- TradeStation — [pricing](https://www.tradestation.com/pricing/), [futures pricing disclosures](https://www.tradestation.com/futures-pricing-disclosures/), [trading API](https://developer.tradestation.com/trading-api/)
- NinjaTrader — [pricing](https://ninjatrader.com/pricing/)
- Webull — [OpenAPI docs](https://developer.webull.com/apis/docs/), [options trading API](https://developer.webull.com/apis/docs/trade-api/options/)
- AMP Futures / Optimus Futures — [Optimus margin rates](https://optimusfutures.com/Margin-Rates.php), [AMP margin and data fees](https://www.quantvps.com/blog/amp-futures-data-and-margin-fees)
- Plus500 Futures — [fees](https://us.plus500.com/en/support/trading/arethereanyfees), [fees FAQ](https://brokerchooser.com/broker-reviews/plus500-futures-review/fees-faq)
- E*Trade — [getting started](https://developer.etrade.com/getting-started), [developer FAQ](https://developer.etrade.com/support/frequently-asked-questions)
- Interactive Brokers — [futures commissions](https://www.interactivebrokers.com/en/pricing/commissions-futures.php), [options commissions](https://www.interactivebrokers.com/en/pricing/commissions-options.php)
- Schwab — [Trader API workflow and limits](https://mylinedchart.com/resources/articles/schwab-api-for-technical-traders-workflow-fit-checklist), [paper trading availability](https://blog.traderspost.io/article/does-schwab-have-paper-trading)

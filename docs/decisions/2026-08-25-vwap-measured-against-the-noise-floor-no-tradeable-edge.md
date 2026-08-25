# VWAP measured against the noise floor: no tradeable edge (PR #127)

*Decided 2026-08-25.*

**Decision:** The VWAP setups have now been run against the two-vendor noise
floor that [the lab's own decision entry](2026-08-24-vwap-lab-the-fade-as-candidate-the-crossover-as-control.md)
said no number would be believed without. On BTC, three years, real matched
volume, costs charged: **all three setups lose, and the loss is transaction
cost.** Gross edge is approximately zero. Parked as a candidate; the harness
and the tooling are kept.

**The measurement.** BTC/USDT, 2023-08-26 to 2026-08-25, 5-minute bars
resampled to 15, both vendors from ccxt — Coinbase (298,713 bars, 0.0%
zero-volume) as primary and Binance.US (315,360 bars, 6.4% zero-volume) as
the second feed. `--mintick 7.5` against a mean price of 75,350 charges 1bp
of slippage per fill, so ~2bp the round trip.

| setup | trades | net bp/trade | **gross bp/trade** | mean vendor gap |
| --- | ---: | ---: | ---: | ---: |
| fade | 4,259 | −1.55 | **+0.44** | +2,415bp (24.1% of price) |
| pullback | 3,266 | −2.01 | **−0.02** | +355bp (3.5%) |
| cross (control) | 10,828 | −1.23 | **+0.76** | −4,532bp (−45.3%) |

**What that says.** Subtract the round trip and every setup sits within half a
basis point of zero. There is no edge here being eaten by bad execution;
there is no edge. The control losing worst in total (−13,274bp against fade's
−6,619) is the expected reading and says the harness, the cost charging and
the session anchor are behaving. Do not read the control's slightly better
*gross* figure as an edge: it is stop-and-reverse and therefore always in the
market across a strong three-year uptrend, so what it captures is drift, not
alpha.

**"Clears the noise floor" is a magnitude test, and all three cleared it on
the losing side.** `abs(edge) > abs(mean_gap) + gap_stdev` says the loss is
real rather than a sourcing artifact. It does not say anything is good, and
the verdict string reads as approval to anyone who has not read
`NoiseFloor.clears`.

**The vendor gap is the finding underneath the finding.** Two exchanges
quoting one asset, same strategy, same years, disagree by a quarter to a half
of price. Pullback is the dangerous shape this repo already warned about and
had never actually seen: **correlation 0.83 with a mean gap of +355bp** — the
feeds agree about the shape of every year and still disagree about the total.
A single-feed crypto backtest is worth even less than a single-feed equity
one, and nothing in this repository should quote one again.

**What it does not say.** This is crypto, not the S&P. Nothing here transfers
to SPY as a result. It transfers as a *prior*, and an unfavourable one:
intraday mean reversion was tested on the most volatile liquid asset
available — the easy case — and cleared zero gross by less than half a basis
point. A more efficient instrument is a worse bet, not a better one. Judging
SPY properly still needs years of real-volume bars plus a second equity
vendor, and that work is not obviously worth doing.

**Six defects were found on the way, all by running the lab rather than
reading it**, and all of the same species: an answer that was wrong while
looking right.

1. The zero-volume check ran on the primary feed only, so a volumeless second
   vendor priced a VWAP result against a TWAP one and reported the difference
   as vendor disagreement.
2. `_try_cross` consults no confirms, so a filtered table compared two gated
   setups against one ungated one. Proved with `--rvol-min 99`: fade and
   pullback go to zero trades, cross carries on unchanged at 491.
3. `mintick` *is* the per-trade slippage, so the crypto example's `0.5`
   charged 0.05bp on BTC near 100k — about forty times less than a real fill,
   on the rule this repo calls non-negotiable.
4. Nothing could fetch intraday bars at all; `season_scan fetch` is daily, and
   a session VWAP over daily bars is one point per day.
5. Yahoo's BTC-USD came back **50.6% zero-volume**, which builds the bands
   from half the data. It damaged the setups in proportion to band
   dependence — fade worst, pullback least, the naive control *in between* —
   an inversion that is a fact about the feed, not the market.
6. Kraken serves ~720 candles however far back `since` reaches and answers
   **successfully** with the cap. Asked for 1,095 days, it returned 2, and
   only the new guard distinguished that from a real reply.

**Kept:** `tools/fetch_bars.py` (yfinance and ccxt sources, naive-UTC stamps,
mintick guidance, capped-history guard), `volume_warnings()` and
`gate_report()` in the lab. **Parked:** the VWAP setups as trade candidates.

**Superseded nothing.** The 2026-08-24 entry stands as written; this is the
measurement it called for, arriving at the answer that entry allowed for.

# Trading wisdom — the knowledge base behind the desk

This is the standing knowledge base that trading sessions and the desk agent
consult: the documented practices of successful traders and the published
research on what works and what doesn't, distilled into rules a machine can
enforce. Every claim carries its source. Items marked **UNVERIFIED** are
widely circulated but were not confirmed against a primary source — treat
them as convention, not fact, and do not tighten a rule based on one.

Two attribution traps already caught while building this: the "1% rule" is
Larry Hite's (Market Wizards), not Paul Tudor Jones's; and PTJ's famous 5:1
quote is from Tony Robbins' *MONEY Master the Game* (2014), not Market
Wizards. Precision matters here — a knowledge base that misquotes its
sources will eventually misstate its rules.

## The hard rules

These are the machine-enforceable distillation. The desk agent implements
them; a trade that violates one is rejected, not debated.

1. **Risk per trade ≤ 1% of account equity**, measured entry-to-stop.
   (Larry Hite, in Schwager, *Market Wizards*, 1989: "Never risk more than
   1% of total account equity on any one trade... By risking 1%, I am
   indifferent to any individual trade.")
2. **Size from volatility, not conviction**: unit size = 1% of equity ÷
   ATR-dollars; stop at ~2× ATR, so a full stop-out costs ≤ 2% of equity.
   (The Original Turtle Trading Rules — Curtis Faith, *Way of the Turtle*,
   2007.)
3. **Never bet more than half-Kelly** of whatever edge is *measured* — full
   Kelly assumes the edge is known exactly, and it never is. (Ed Thorp,
   *A Man for All Markets*, 2017; Thorp, "The Kelly Criterion in Blackjack,
   Sports Betting, and the Stock Market", 2006.)
4. **No stop, no trade.** R is undefined without one, and every result is
   recorded as an R-multiple. (Van Tharp, *Trade Your Way to Financial
   Freedom*, 1999.)
5. **Reject any plan with reward:risk below 2:1; prefer 5:1.** (PTJ in
   Robbins, *MONEY Master the Game*, 2014: at 5:1 "I can be wrong 80% of
   the time, and I'm still not going to lose.")
6. **Never add to a losing position.** ("Losers average losers" — the sign
   over PTJ's desk, visible in the 1987 documentary *Trader*; also Livermore,
   *How to Trade in Stocks*, 1940.)
7. **Daily loss ≥ 5% of start-of-day balance → halt until next session.
   Total drawdown ≥ 10% → halt and review the system.** (FTMO's enforced
   evaluator limits — the numbers a professional gatekeeper uses.)
8. **In drawdown, cut size**: ~20% smaller units per 10% of equity lost,
   restored as equity recovers. (Original Turtle rules; PTJ: "decrease your
   trading volume when you are trading poorly.")
9. **No system trades live money until expectancy is positive over ≥ 30
   trades of out-of-sample results — 100 preferred.** (Tharp on sample
   size; Pardo, *Design, Testing and Optimization of Trading Systems*,
   1992, on walk-forward; Harvey & Liu, "Evaluating Trading Strategies",
   JPM 2014: demand t ≈ 3, not 2, because many strategies get tried.)
10. **Every trade gets a journal entry — entry, exit, R-multiple, thesis,
    emotional state — and the journal must be current before the next
    entry.** (Steenbarger, *The Daily Trading Coach*, 2009; Douglas,
    *Trading in the Zone*, 2000.)

Rule 9 is why everything here starts on paper. It is not a formality: the
paper record *is* the out-of-sample sample.

## Why the gates exist: the base rates

The published record on unstructured retail trading is brutal, and these
numbers are the reason the rules above are enforced rather than suggested:

- **Barber & Odean, "Trading Is Hazardous to Your Wealth" (J. Finance,
  2000)**: 66,465 households; the most active traders underperformed the
  market by 6.5 points a year.
- **Barber, Lee, Liu & Odean (Taiwan, complete exchange records
  1992–2006)**: about 1.6% of day traders are predictably profitable in an
  average year; 80% quit within two years.
- **Chague, De-Losso & Giovannetti, "Day Trading for a Living?" (2020,
  Brazil)**: of people who persisted 300+ sessions, **97% lost money**;
  0.5% out-earned a bank teller. No evidence of learning with experience.
- **Bryzgalova, Pavlova & Sikorskaya (J. Finance, 2023)**: aggregate retail
  options traders lost ~$2.1B over 20 months; retail-sized option spreads
  average ~8% of option value per round trip.

The goal "gain more early than I lose so I don't break the bank" is exactly
what these studies show does *not* happen by default. It happens — when it
happens — through rules 1–10: small constant risk, positive expectancy
proven on paper first, and losses cut mechanically.

## Crypto screening: what the evidence supports

What `tools/crypto_scan.py` implements, and why (full citations in the
scan's docstring and below):

- **1–4 week momentum is the documented signal.** Liu & Tsyvinski (*Review
  of Financial Studies*, 2021) and Liu, Tsyvinski & Wu (*Journal of
  Finance*, 2022): short-horizon continuation in liquid coins, attributed
  to underreaction. This is the best-sourced anomaly in crypto.
- **Beyond ~1 month it reverses.** Dobrynskaya (*J. Alternative
  Investments*, 2023): the momentum-to-reversal switch happens after about
  a month — never screen for "up the most over 3 months."
- **Moving-average trend filters have out-of-sample support.** Detzel et
  al. (*Financial Management*, 2021): price-to-MA ratios forecast BTC
  returns out-of-sample.
- **Volume matters; volatility is for sizing only.** Volume/liquidity is a
  documented cross-sectional characteristic (JF 2022). No evidence that
  volatility level predicts direction — it sets position size and stop
  distance, nothing else (Grobys, FMPM 2025: vol-scaling flatters averages
  without fixing tails).
- **Alts are mostly one trade.** Correlation to BTC is high enough that a
  screen full of bullish alts is largely one bet on BTC beta. The scan
  prints the BTC regime line for exactly this reason.
- **Assume the published edge has decayed by half.** Post-publication decay
  is the documented base rate, and one published paper (Grobys, IJFE 2025)
  argues crypto momentum may be illusory. Momentum crashes are violent and
  the tails are formally unmeasurable.
- **Excluded as folklore**: day-of-week/weekend effects (an artifact of one
  Sunday-night hour), and oscillators (RSI/MACD) as independent signals —
  no published evidence they add anything beyond the past-return and MA
  information they re-encode.

Honest summary: a real but shrinking, crash-prone edge concentrated in
liquid majors. Good enough to rank candidates for *paper* trades and to
practice the discipline loop; nowhere near good enough to skip the gates.

## Iron condors: venue, construction, and the honest caveat

**Venue: SPX — or XSP at small size.** Not SPY, not single names, not
crypto. Cash-settled European index options cannot be assigned early, have
no pin risk, and get Section 1256 tax treatment (60% long-term / 40%
short-term regardless of holding period). SPY options are American-style
(early assignment around dividends) and taxed as short-term. Single names
add earnings gaps, which are precisely the moves a condor cannot survive.
(Cboe index-options documentation; IRC §1256.)

**Why the trade exists at all** — the one peer-reviewed part: implied
volatility on indexes systematically exceeds realized volatility (Carr &
Wu, "Variance Risk Premiums", *RFS* 2009). A condor is a defined-risk way
to harvest that premium. Cboe's PUT/BXM benchmark history shows systematic
index option selling earning equity-like returns at lower volatility over
long samples.

**Construction conventions** — practitioner-grade, from tastylive's
in-house backtests, internally consistent but not peer-reviewed:
~45 DTE entry, short strikes at ~16–20 delta (≈1 standard deviation),
manage winners at 50% of credit, exit or roll at 21 DTE to dodge gamma.
The specific win-rate figures circulating for these rules are
**UNVERIFIED** at primary-source level. Expected move ≈ ATM straddle ×
0.85, or S × IV × √(DTE/365).

**The caveat that never goes away**: a condor is short vega and short
gamma. It wins often and loses big, and its losses arrive exactly when
implied vol was, for once, too low. The management rules trim that tail;
they do not remove it. Position sizing (rule 1) is what makes the bad month
survivable.

**Do we have enough test data to trust the range?** No — and that is the
point of the mock run. The variance risk premium says the range is priced
generously *on average*; nothing yet says anything about any particular
week. The paper condor series is the data collection: each week records the
expected move, the strikes, and whether the index stayed inside. Confidence
is the output of that series, not a precondition for starting it (rule 9:
30 occurrences minimum before real money — that is most of a year of weekly
condors, and that's the honest timescale).

## The learning loop

"Self-learning" here means something specific and auditable — the system
authors its own improvements, and the owner approves them:

1. **Collect**: every paper and live trade lands in the journal with its
   R-multiple, thesis, and the rule-gates it passed.
2. **Measure**: periodically (weekly at first), compute per-strategy
   expectancy, win rate, average R, drawdown, and rule-violation counts
   from the journal record. `tools/analyze_trades.py` is the pattern.
3. **Propose**: when the numbers support a change — a threshold that's too
   loose, a strategy whose expectancy has gone negative, a signal that
   stopped ranking — the session drafts the exact change (a diff to this
   file, to a tool's parameters, or to the desk agent's limits) with the
   evidence attached.
4. **Approve**: nothing applies until the owner says yes. The proposal is
   the machine's; the merge is theirs.
5. **Record**: applied changes land in this repo's history and CLAUDE.md's
   decision log, so every rule's lineage is checkable.

Models follow the same loop at a slower cadence: any retrained or
fine-tuned predictor (Kronos included) re-runs its evaluation harness
(`tools/kronos_lab.py`), and the scorecard — not the training loss —
decides whether it graduates. The 2026-08-22 zero-shot Kronos verdict
(noise on BTC and ES) is the standing example of the gate doing its job.

What this loop deliberately is **not**: a system that rewrites its own risk
limits unsupervised. Livermore wrote the rules and broke them himself —
bankrupt three times despite knowing better. The entire value of machine
enforcement is that the machine doesn't get talked out of the rules by a
good week or a bad one; letting it loosen its own limits would reintroduce
the exact failure mode it exists to prevent.

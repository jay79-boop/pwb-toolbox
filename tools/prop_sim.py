#!/usr/bin/env python
"""Price a prop-firm evaluation the way the firm already has.

The pitch overheard everywhere (and measured live on a stream the owner sat
in on): a strategy with ZERO market edge -- a literal coin flip -- can carry
positive expected value against a prop firm's fee structure. The mechanism
is not market edge and nothing here pretends it is:

    - an eval is "reach +T before -D, pay fee F". For a symmetric strategy
      that is the classic gambler's ruin, P(pass) = D/(T+D) before the
      firm's complications;
    - your loss beyond -D is capped at the fee; a funded account's payouts
      are not capped symmetrically. A capped left tail and an open right
      tail, priced at a flat fee -- the whole "edge" is against the fee
      schedule;
    - and the firms know. Trailing drawdowns, consistency rules, minimum
      days, time limits, activation fees and payout schedules exist to tax
      exactly this play. Those parameters are what this tool prices.

One result worth knowing before believing anyone's pitch: for the pure
symmetric walk, P(pass) = D/(T+D) NO MATTER THE POSITION SIZE -- "sizing
optimizes the odds" is only true through the rules (a finite time limit
rewards big steps; a consistency rule punishes them; a trailing ratchet
interacts with step size). The simulator demonstrates each of these because
they are in the arithmetic, not because anyone asserts them.

Everything is deterministic given a seed, stdlib-only, and pinned against
the gambler's-ruin closed form in tests. The strategy input is a coin flip
by default, or the real R-distribution a sim exported for the night lab
(--r-dist night_lab/sim_trades.json), so the question becomes concrete:
"what is MY strategy worth per attempt, under THIS firm's published rules?"

What this is not: a strategy, an endorsement, or a market edge. Rule sets
change and several firms' terms allow payout denial at their discretion --
verify every number in a rules file against the firm's CURRENT published
terms, and treat any experiment as a walled-off pot under spec-desk rules.

Examples::

    python tools/prop_sim.py rules-template > my_firm.json
    python tools/prop_sim.py evaluate --rules my_firm.json --risk 200
    python tools/prop_sim.py evaluate --rules my_firm.json \
        --r-dist night_lab/sim_trades.json --risk 150 --size-sweep 0.5,1,2
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

SIMS = 4000


@dataclass
class Rules:
    """One firm's published evaluation terms. Fill from the CURRENT ToS.

    The demo preset below is deliberately fictional round numbers -- rule
    sets heard second-hand do not belong in code as if they were facts.
    """

    name: str = "demo-50k"
    profit_target: float = 3000.0
    max_drawdown: float = 2000.0
    # "fixed": floor never moves. "eod_trailing": the floor ratchets up with
    # the end-of-day high-water mark. "intraday_trailing": it ratchets on
    # every trade -- the harshest common variant.
    drawdown_mode: str = "eod_trailing"
    # Most trailing schemes stop ratcheting once the floor reaches the
    # starting balance; the drawdown then behaves like a stop at breakeven.
    trailing_locks_at_start: bool = True
    eval_fee: float = 150.0
    activation_fee: float = 150.0
    daily_loss_limit: float | None = None
    # Max share of total profit one day may contribute at the moment of
    # passing (e.g. 0.5); None = no consistency rule.
    consistency_pct: float | None = None
    min_days: int = 2
    max_days: int | None = 60
    trades_per_day: int = 3
    payout_share: float = 0.9
    funded_drawdown: float | None = None  # defaults to max_drawdown
    payout_period_days: int = 21
    # Horizon on the funded account so its expected payout is finite and
    # conservative rather than an infinite-run fantasy.
    max_funded_days: int = 252
    # Cushion kept in the funded account at each withdrawal; withdrawing to
    # exactly a locked breakeven floor means the next losing trade ends the
    # account, which is the harsh reality of trailing-to-breakeven -- the
    # buffer models the survivable version of the policy.
    payout_buffer: float | None = None


def load_rules(path: str | None) -> Rules:
    if path is None:
        return Rules()
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    known = {f.name for f in fields(Rules)}
    unknown = set(raw) - known
    if unknown:
        raise SystemExit(f"unknown rule field(s): {', '.join(sorted(unknown))}")
    return Rules(**raw)


# ---------------------------------------------------------------------------
# Strategy inputs: a coin flip, or a real exported R record
# ---------------------------------------------------------------------------


def coin_flip(win_p: float = 0.5, win_r: float = 1.0, loss_r: float = 1.0):
    """Per-trade R sampler for the canonical zero-ish-edge strategy."""

    def draw(rng: random.Random) -> float:
        return win_r if rng.random() < win_p else -loss_r

    return draw


def r_from_records(records: list[dict]) -> list[float]:
    """R-multiples from night-lab-shaped closed trades (entry/stop/exit)."""
    out = []
    for t in records:
        try:
            entry, stop, exit_ = float(t["entry"]), float(t["stop"]), float(t["exit"])
        except (KeyError, TypeError, ValueError):
            continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        sign = -1.0 if str(t.get("direction", "long")).lower() == "short" else 1.0
        out.append(sign * (exit_ - entry) / risk)
    return out


def empirical(rs: list[float]):
    """Sample i.i.d. from a strategy's actual R history."""
    if not rs:
        raise SystemExit("no usable trades in the R distribution")

    def draw(rng: random.Random) -> float:
        return rng.choice(rs)

    return draw


# ---------------------------------------------------------------------------
# The engines. Everything is relative to the starting balance (equity 0).
# ---------------------------------------------------------------------------


def _floor(hwm: float, drawdown: float, rules: Rules) -> float:
    if rules.drawdown_mode == "fixed":
        return -drawdown
    floor = hwm - drawdown
    if rules.trailing_locks_at_start:
        floor = min(floor, 0.0)
    return floor


def run_eval(rng: random.Random, draw, risk: float, rules: Rules) -> dict:
    """One evaluation attempt. Returns pass/fail and how many days it took."""
    equity = hwm = best_day = 0.0
    floor = _floor(0.0, rules.max_drawdown, rules)
    day = 0
    while True:
        day += 1
        if rules.max_days is not None and day > rules.max_days:
            return {"passed": False, "days": day - 1, "why": "time limit"}
        day_pnl = 0.0
        for _ in range(rules.trades_per_day):
            pnl = draw(rng) * risk
            equity += pnl
            day_pnl += pnl
            if (
                rules.daily_loss_limit is not None
                and day_pnl <= -rules.daily_loss_limit
            ):
                return {"passed": False, "days": day, "why": "daily loss limit"}
            if rules.drawdown_mode == "intraday_trailing":
                hwm = max(hwm, equity)
                floor = _floor(hwm, rules.max_drawdown, rules)
            if equity <= floor:
                return {"passed": False, "days": day, "why": "drawdown"}
        best_day = max(best_day, day_pnl)
        if rules.drawdown_mode == "eod_trailing":
            hwm = max(hwm, equity)
            floor = _floor(hwm, rules.max_drawdown, rules)
            if equity <= floor:
                return {"passed": False, "days": day, "why": "drawdown"}
        if equity >= rules.profit_target and day >= rules.min_days:
            if (
                rules.consistency_pct is None
                or equity <= 0
                or best_day <= rules.consistency_pct * equity
            ):
                return {"passed": True, "days": day, "why": "target"}
            # Target met but one day carried too much of it: keep trading
            # until the ratio dilutes, the clock runs out, or the drawdown
            # bites -- which is exactly how consistency rules tax size.
    # unreachable


def run_funded(rng: random.Random, draw, risk: float, rules: Rules) -> float:
    """Total payouts from one funded account until blowout or horizon."""
    drawdown = rules.funded_drawdown or rules.max_drawdown
    buffer = rules.payout_buffer if rules.payout_buffer is not None else drawdown / 2
    equity = hwm = 0.0
    floor = _floor(0.0, drawdown, rules)
    payouts = 0.0
    for day in range(1, rules.max_funded_days + 1):
        for _ in range(rules.trades_per_day):
            equity += draw(rng) * risk
            if rules.drawdown_mode == "intraday_trailing":
                hwm = max(hwm, equity)
                floor = _floor(hwm, drawdown, rules)
            if equity <= floor:
                return payouts
        if rules.drawdown_mode == "eod_trailing":
            hwm = max(hwm, equity)
            floor = _floor(hwm, drawdown, rules)
            if equity <= floor:
                return payouts
        if day % rules.payout_period_days == 0 and equity > buffer:
            withdrawn = equity - buffer
            payouts += rules.payout_share * withdrawn
            equity -= withdrawn
    return payouts


def price(draw, risk: float, rules: Rules, sims: int = SIMS, seed: int = 7) -> dict:
    """The numbers that decide whether the game is worth playing."""
    rng = random.Random(seed)
    passes, pass_days = 0, []
    for _ in range(sims):
        got = run_eval(rng, draw, risk, rules)
        if got["passed"]:
            passes += 1
            pass_days.append(got["days"])
    p_pass = passes / sims
    funded = [run_funded(rng, draw, risk, rules) for _ in range(max(1, passes))]
    mean_payout = statistics.fmean(funded) if passes else 0.0
    ev = p_pass * (mean_payout - rules.activation_fee) - rules.eval_fee
    return {
        "risk_per_trade": risk,
        "pass_rate": round(p_pass, 4),
        "avg_days_to_pass": (
            round(statistics.fmean(pass_days), 1) if pass_days else None
        ),
        "attempts_to_funded": round(1 / p_pass, 1) if p_pass else float("inf"),
        "cost_to_funded": (
            round(rules.eval_fee / p_pass + rules.activation_fee, 2)
            if p_pass
            else float("inf")
        ),
        "mean_funded_payout": round(mean_payout, 2),
        "ev_per_attempt": round(ev, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_template(args):
    print(
        json.dumps({f.name: getattr(Rules(), f.name) for f in fields(Rules)}, indent=2)
    )
    return 0


def cmd_evaluate(args):
    rules = load_rules(args.rules)
    if args.r_dist:
        raw = json.loads(Path(args.r_dist).expanduser().read_text(encoding="utf-8"))
        rs = r_from_records(raw.get("trades", raw) if isinstance(raw, dict) else raw)
        draw = empirical(rs)
        source = f"{len(rs)} real trades from {args.r_dist}"
    else:
        draw = coin_flip(args.win_p, args.win_r, args.loss_r)
        source = f"coin flip (p={args.win_p}, +{args.win_r}R / -{args.loss_r}R)"
    sizes = [float(s) for s in args.size_sweep.split(",")] if args.size_sweep else [1.0]

    print(f"Rules: {rules.name} | strategy: {source} | {args.sims} sims")
    print(
        f"  target +{rules.profit_target:g} before -{rules.max_drawdown:g} "
        f"({rules.drawdown_mode}), fee {rules.eval_fee:g}"
        + (f" + activation {rules.activation_fee:g}" if rules.activation_fee else "")
    )
    header = (
        f"{'risk':>8} {'pass':>7} {'days':>6} {'attempts':>9} "
        f"{'cost->funded':>13} {'E[payout]':>10} {'EV/attempt':>11}"
    )
    print(header)
    for mult in sizes:
        got = price(draw, args.risk * mult, rules, sims=args.sims, seed=args.seed)
        print(
            f"{got['risk_per_trade']:>8g} {got['pass_rate']:>7.1%} "
            f"{str(got['avg_days_to_pass']):>6} {got['attempts_to_funded']:>9} "
            f"{got['cost_to_funded']:>13} {got['mean_funded_payout']:>10} "
            f"{got['ev_per_attempt']:>11}"
        )
    print(
        "\nEV is against the fee schedule under these exact rules, not market "
        "edge; verify every rule against the firm's current terms."
    )
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "rules-template", help="print a rules JSON to fill from a firm's ToS"
    )
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("evaluate", help="price pass odds and EV per attempt")
    p.add_argument("--rules", help="rules JSON (default: the fictional demo-50k)")
    p.add_argument(
        "--risk", type=float, default=200.0, help="dollars risked per trade (1R)"
    )
    p.add_argument("--r-dist", help="night-lab-shaped trades JSON to sample R from")
    p.add_argument("--win-p", type=float, default=0.5)
    p.add_argument("--win-r", type=float, default=1.0)
    p.add_argument("--loss-r", type=float, default=1.0)
    p.add_argument("--size-sweep", help="comma list of risk multipliers, e.g. 0.5,1,2")
    p.add_argument("--sims", type=int, default=SIMS)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_evaluate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

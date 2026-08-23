#!/usr/bin/env python
"""The speculative desk: a walled-off pot for high-risk paper trades.

This is the ledger and rules engine behind the "trade spicy" agent — the
counterpart to the core program's caution. The core portfolio grows steadily
under the wisdom-doc rules; this desk exists to swing for multiples with
money that is allowed to die. The wall between the two is the whole design:

    - The desk trades a fixed pot (a slice of the paper account, set at
      init). Wins and losses stay inside it and never touch core statistics.
    - Per-trade max loss is capped at 10% of the pot — high risk, but ten
      consecutive disasters to destroy it, not one.
    - The 0-7 DTE lottery lane is sub-capped at 2.5% of the pot per trade,
      because that lane's base rate is incineration.
    - At most 4 positions open at once.
    - When the pot is spent, the desk locks. No new trades until `review`
      has been run on the record and a refill is explicitly made. Blowing
      up is allowed; blowing up without learning anything is not.

Every open is a complete committed plan — instrument, size, max loss, stop
and target on the underlying, thesis — logged before execution, and closes
are scored in R-multiples so `review` can say which lanes actually earn
their risk. Execution stays human: the agent plans and logs, you click the
order into paperMoney (options) or TradingView paper (stock/crypto), and
`check` watches live prices against your stops and targets so you hear
about it when a level hits.

The ledger lives in spec_desk/spec_desk.json — gitignored; trade records
are personal and this fork is public.

Examples::

    python tools/spec_desk.py init --pot 10000
    python tools/spec_desk.py open --lane swing-buy --symbol NVDA \
        --instrument "NVDA 02OCT26 190C" --venue paperMoney --qty 2 \
        --entry 4.20 --stop 176 --target 198 --thesis "breakout over 182 on volume"
    python tools/spec_desk.py status
    python tools/spec_desk.py check          # which stops/targets have hit
    python tools/spec_desk.py close T1 --exit 7.90
    python tools/spec_desk.py review
    python tools/spec_desk.py refill --amount 10000
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "spec_desk"
LEDGER_NAME = "spec_desk.json"

# The desk's hard rules. High risk is the mandate; unbounded risk is not.
PER_TRADE_CAP = 0.10  # max loss per trade, as a fraction of the pot
SHORT_DTE_CAP = 0.025  # sub-cap for the 0-7 DTE lottery lane
MAX_OPEN = 4

LANES = {
    "swing-buy": "directional option buys, 15-45 DTE",
    "short-dte": "0-7 DTE lottery tickets (sub-capped)",
    "momentum-stock": "high-beta breakout shares",
    "premium-sell": "defined-risk credit spreads",
}
# Options settle 100 shares a contract; stock lanes are 1:1.
DEFAULT_MULTIPLIER = {
    "swing-buy": 100,
    "short-dte": 100,
    "premium-sell": 100,
    "momentum-stock": 1,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Ledger — plain dict in, plain dict out; all rules live here and are tested
# ---------------------------------------------------------------------------


def new_ledger(pot: float) -> dict:
    return {
        "pot": pot,
        "created": now_iso(),
        "trades": [],
        "reviews": [],
        "refills": [],
    }


def equity(ledger: dict) -> float:
    """What the desk has left: pot plus refills plus realized results."""
    realized = sum(t["pnl"] for t in ledger["trades"] if t["status"] == "closed")
    refilled = sum(r["amount"] for r in ledger["refills"])
    return ledger["pot"] + refilled + realized


def open_trades(ledger: dict) -> list[dict]:
    return [t for t in ledger["trades"] if t["status"] == "open"]


def open_risk(ledger: dict) -> float:
    return sum(t["max_loss"] for t in open_trades(ledger))


def validate_open(ledger: dict, lane: str, max_loss: float) -> str | None:
    """The gatekeeper. Returns a refusal reason, or None to admit the trade."""
    if lane not in LANES:
        return f"unknown lane {lane!r}; choose from {sorted(LANES)}"
    if max_loss <= 0:
        return "max loss must be positive — a trade with no defined risk is not a plan"
    eq = equity(ledger)
    if eq <= 0:
        return (
            "the pot is spent. The desk is locked until `review` is run on the "
            "record and a refill is made — blowups must produce lessons first."
        )
    if len(open_trades(ledger)) >= MAX_OPEN:
        return f"already {MAX_OPEN} positions open; close something first"
    cap = SHORT_DTE_CAP if lane == "short-dte" else PER_TRADE_CAP
    if max_loss > cap * ledger["pot"] + 1e-9:
        return (
            f"max loss {max_loss:.2f} exceeds the {lane} cap of {cap:.1%} of the "
            f"pot ({cap * ledger['pot']:.2f}). Size down."
        )
    if max_loss > eq - open_risk(ledger):
        return (
            f"not enough uncommitted pot: equity {eq:.2f}, already at risk "
            f"{open_risk(ledger):.2f}"
        )
    return None


def add_trade(ledger: dict, **fields) -> dict:
    reason = validate_open(ledger, fields["lane"], fields["max_loss"])
    if reason:
        raise ValueError(reason)
    trade = {
        "id": f"T{len(ledger['trades']) + 1}",
        "opened": now_iso(),
        "status": "open",
        "exit_price": None,
        "closed": None,
        "pnl": None,
        "r_multiple": None,
        **fields,
    }
    ledger["trades"].append(trade)
    return trade


def close_trade(
    ledger: dict, trade_id: str, exit_price: float, pnl: float | None = None
) -> dict:
    trade = next((t for t in ledger["trades"] if t["id"] == trade_id), None)
    if trade is None:
        raise ValueError(f"no trade {trade_id!r}")
    if trade["status"] == "closed":
        raise ValueError(f"{trade_id} is already closed")
    if pnl is None:
        pnl = (exit_price - trade["entry"]) * trade["qty"] * trade["multiplier"]
    trade.update(
        status="closed",
        exit_price=exit_price,
        closed=now_iso(),
        pnl=round(pnl, 2),
        r_multiple=round(pnl / trade["max_loss"], 2),
    )
    return trade


def lane_stats(ledger: dict) -> dict[str, dict]:
    """Per-lane expectancy from closed trades — the review's raw material."""
    out: dict[str, dict] = {}
    for lane in LANES:
        closed = [
            t for t in ledger["trades"] if t["status"] == "closed" and t["lane"] == lane
        ]
        if not closed:
            continue
        rs = [t["r_multiple"] for t in closed]
        wins = [r for r in rs if r > 0]
        out[lane] = {
            "trades": len(closed),
            "win_rate": len(wins) / len(closed),
            "avg_r": sum(rs) / len(rs),
            "total_pnl": round(sum(t["pnl"] for t in closed), 2),
        }
    return out


def last_close_time(ledger: dict) -> str | None:
    closes = [t["closed"] for t in ledger["trades"] if t["status"] == "closed"]
    return max(closes) if closes else None


def can_refill(ledger: dict) -> str | None:
    """A refill needs a review newer than the latest close: no fresh money
    until the record of how the last money died has been looked at."""
    last_close = last_close_time(ledger)
    if last_close is None:
        return None
    reviews_after = [r for r in ledger["reviews"] if r["at"] >= last_close]
    if not reviews_after:
        return "run `review` first — refills require a review newer than the last closed trade"
    return None


def check_alerts(trades: list[dict], prices: dict[str, float]) -> list[str]:
    """Compare open trades' stop/target levels (on the underlying) against
    current prices. Pure function so the alert logic is testable without
    a market."""
    alerts = []
    for t in trades:
        price = prices.get(t["symbol"])
        if price is None:
            alerts.append(
                f"{t['id']} {t['symbol']}: no price available — check manually"
            )
            continue
        direction = t.get("direction", "long")
        stop, target = t.get("stop"), t.get("target")
        if direction == "long":
            stop_hit = stop is not None and price <= stop
            target_hit = target is not None and price >= target
        else:
            stop_hit = stop is not None and price >= stop
            target_hit = target is not None and price <= target
        if stop_hit:
            alerts.append(
                f"{t['id']} {t['symbol']} STOP hit: {price:.2f} vs stop {stop:.2f} — exit per plan"
            )
        elif target_hit:
            alerts.append(
                f"{t['id']} {t['symbol']} TARGET hit: {price:.2f} vs target {target:.2f} — "
                "take it or trail it, but decide now"
            )
    return alerts


# ---------------------------------------------------------------------------
# Persistence and commands
# ---------------------------------------------------------------------------


def ledger_path(dir_arg: str | None) -> Path:
    return Path(dir_arg) / LEDGER_NAME if dir_arg else DEFAULT_DIR / LEDGER_NAME


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"no ledger at {path} — run `init --pot <amount>` first")
    return json.loads(path.read_text())


def save(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2))


def cmd_init(args):
    path = ledger_path(args.dir)
    if path.exists() and not args.force:
        raise SystemExit(
            f"{path} exists; pass --force to start over (this erases the record)"
        )
    save(path, new_ledger(args.pot))
    print(f"Speculative desk opened with a {args.pot:,.2f} pot -> {path}")
    print(
        f"Caps: {PER_TRADE_CAP:.0%} of pot per trade, {SHORT_DTE_CAP:.1%} for short-dte, {MAX_OPEN} open max."
    )


def cmd_open(args):
    path = ledger_path(args.dir)
    ledger = load(path)
    multiplier = args.multiplier or DEFAULT_MULTIPLIER[args.lane]
    max_loss = args.max_loss
    if max_loss is None:
        if args.lane == "momentum-stock" and args.stop is not None:
            max_loss = abs(args.entry - args.stop) * args.qty * multiplier
        else:
            # a bought option can go to zero; that premium is the honest max loss
            max_loss = args.entry * args.qty * multiplier
    try:
        trade = add_trade(
            ledger,
            lane=args.lane,
            symbol=args.symbol.upper(),
            instrument=args.instrument,
            venue=args.venue,
            direction=args.direction,
            qty=args.qty,
            entry=args.entry,
            multiplier=multiplier,
            max_loss=round(max_loss, 2),
            stop=args.stop,
            target=args.target,
            thesis=args.thesis,
        )
    except ValueError as e:
        raise SystemExit(f"REFUSED: {e}")
    save(path, ledger)
    print(f"{trade['id']} logged — now place it, then trade the plan, not the P&L.\n")
    print(
        f"  {args.lane}: {trade['instrument']} x{args.qty} @ {args.entry} ({args.venue})"
    )
    print(
        f"  max loss {trade['max_loss']:.2f} | stop {args.stop} | target {args.target}"
    )
    print(f"  thesis: {args.thesis}")
    print(f"\nPot equity {equity(ledger):,.2f}, at risk {open_risk(ledger):,.2f}.")


def cmd_close(args):
    path = ledger_path(args.dir)
    ledger = load(path)
    try:
        trade = close_trade(ledger, args.trade_id, args.exit, args.pnl)
    except ValueError as e:
        raise SystemExit(str(e))
    save(path, ledger)
    print(
        f"{trade['id']} closed: P&L {trade['pnl']:+,.2f} ({trade['r_multiple']:+.2f}R)."
    )
    print(f"Pot equity {equity(ledger):,.2f}.")
    if equity(ledger) <= 0:
        print(
            "The pot is spent. The desk is locked: run `review`, then decide on a refill."
        )


def cmd_status(args):
    ledger = load(ledger_path(args.dir))
    eq = equity(ledger)
    print(
        f"Pot equity {eq:,.2f} (started {ledger['pot']:,.2f}, at risk {open_risk(ledger):,.2f})"
    )
    opens = open_trades(ledger)
    if not opens:
        print("No open positions.")
    for t in opens:
        print(
            f"  {t['id']} [{t['lane']}] {t['instrument']} x{t['qty']} @ {t['entry']} "
            f"| max loss {t['max_loss']:.2f} | stop {t['stop']} | target {t['target']}"
        )
    if eq <= 0:
        print("DESK LOCKED — review before refill.")


def cmd_check(args):
    ledger = load(ledger_path(args.dir))
    opens = open_trades(ledger)
    if not opens:
        print("No open positions to check.")
        return
    import yfinance as yf

    symbols = sorted({t["symbol"] for t in opens})
    raw = yf.download(
        symbols, period="1d", interval="1m", progress=False, auto_adjust=False
    )
    prices: dict[str, float] = {}
    for sym in symbols:
        try:
            closes = raw["Close"][sym] if len(symbols) > 1 else raw["Close"]
            prices[sym] = float(closes.dropna().iloc[-1])
        except Exception:
            pass
    alerts = check_alerts(opens, prices)
    if alerts:
        for a in alerts:
            print(a)
    else:
        print(f"All {len(opens)} open positions inside their levels.")


def cmd_review(args):
    path = ledger_path(args.dir)
    ledger = load(path)
    stats = lane_stats(ledger)
    if not stats:
        print("No closed trades to review yet.")
    for lane, s in stats.items():
        print(
            f"{lane:<16} {s['trades']:>3} trades | win rate {s['win_rate']:.0%} | "
            f"avg {s['avg_r']:+.2f}R | P&L {s['total_pnl']:+,.2f}"
        )
    total = sum(s["total_pnl"] for s in stats.values()) if stats else 0.0
    print(f"\nDesk P&L {total:+,.2f}; pot equity {equity(ledger):,.2f}.")
    negative = [
        lane for lane, s in stats.items() if s["trades"] >= 10 and s["avg_r"] < 0
    ]
    if negative:
        print(f"Lanes with 10+ trades and negative expectancy: {', '.join(negative)}.")
        print(
            "Proposal: pause those lanes; the record says they don't earn their risk."
        )
    ledger["reviews"].append({"at": now_iso(), "stats": stats})
    save(path, ledger)
    print("Review recorded — refill is now unlocked if the pot is spent.")


def cmd_refill(args):
    path = ledger_path(args.dir)
    ledger = load(path)
    reason = can_refill(ledger)
    if reason:
        raise SystemExit(f"REFUSED: {reason}")
    ledger["refills"].append({"at": now_iso(), "amount": args.amount})
    save(path, ledger)
    print(f"Refilled {args.amount:,.2f}; pot equity {equity(ledger):,.2f}.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="spec_desk",
        description="Ledger and rules for the high-risk paper-trading desk.",
    )
    ap.add_argument("--dir", help=f"ledger directory (default {DEFAULT_DIR})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="open the desk with a fixed pot")
    p.add_argument("--pot", type=float, required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("open", help="log a committed trade plan (before placing it)")
    p.add_argument("--lane", required=True, choices=sorted(LANES))
    p.add_argument(
        "--symbol", required=True, help="underlying ticker, for price checks"
    )
    p.add_argument(
        "--instrument", required=True, help='e.g. "NVDA 02OCT26 190C" or "shares"'
    )
    p.add_argument("--venue", default="paperMoney", help="paperMoney / TradingView")
    p.add_argument("--direction", default="long", choices=["long", "short"])
    p.add_argument("--qty", type=int, required=True)
    p.add_argument(
        "--entry", type=float, required=True, help="entry price (premium for options)"
    )
    p.add_argument(
        "--multiplier",
        type=int,
        help="contract multiplier (default: 100 options, 1 stock)",
    )
    p.add_argument(
        "--max-loss",
        type=float,
        help="override; default = full premium, or stop distance for stock",
    )
    p.add_argument("--stop", type=float, help="stop level on the UNDERLYING")
    p.add_argument("--target", type=float, help="target level on the UNDERLYING")
    p.add_argument(
        "--thesis", required=True, help="why this trade, in one or two sentences"
    )
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("close", help="record an exit and score it in R")
    p.add_argument("trade_id")
    p.add_argument(
        "--exit", type=float, required=True, help="exit price (premium for options)"
    )
    p.add_argument(
        "--pnl", type=float, help="override computed P&L (spreads, assignments)"
    )
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("status", help="pot equity and open positions")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="live prices vs stops/targets on open positions")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("review", help="per-lane expectancy; unlocks refill")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("refill", help="add to the pot (requires a fresh review)")
    p.add_argument("--amount", type=float, required=True)
    p.set_defaults(func=cmd_refill)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

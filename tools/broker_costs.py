#!/usr/bin/env python
"""What does the same trade actually cost at each broker?

Broker comparisons are usually argued in headline commissions, which is the
one number that does not decide anything. A $10/month platform fee is $120 a
year whether you trade or not; a per-leg commission cap only matters above the
size where it bites; and market data you already pay for is not a cost of
adding a venue, it is a sunk cost of the one you have.

So this prices a *structure* — legs, contracts, how often, how often closed —
at every broker on the shortlist, and puts the platform fee in the total where
it belongs. `docs/brokers.md` is the qualitative half; this is the arithmetic.

Four commands::

    brokers   The fee models themselves, so a wrong number is visible rather
              than buried three layers down in a total.

    trade     One structure priced once at every broker. The unit case.

    condor    The weekly SPX/XSP iron condor program, a year at a time, with
              a size sweep — because the ranking changes with size and the
              headline commission never says so.

    spread    The size at which each pair of brokers crosses over, which is
              the only number that tells you whether a difference is real.

Every rate is an argument with a documented default, so all of it runs with no
network and a wrong published number can be overridden rather than patched::

    python tools/broker_costs.py brokers
    python tools/broker_costs.py trade --legs 4 --contracts 1 --index
    python tools/broker_costs.py condor --contracts 1 --close-rate 0.7
    python tools/broker_costs.py spread --legs 4 --index

Rates were read on 2026-08-24 and are sourced in `docs/brokers.md`. They change.
Re-read the source before trading on any of them.

**Exchange, clearing and regulatory fees are not broker commissions** and are
charged on top at every broker roughly equally, so they cancel out of a
comparison and are excluded by default. `--exchange-fee` puts them back when
the question is "what does this program cost" rather than "which broker is
cheaper". The one verified tier worth knowing: XSP carries no Cboe proprietary
index fee at 1-9 contracts per leg, and $0.07/contract at 10 or more.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class OptionRate:
    """Per-contract commission for one option class at one broker.

    ``per_leg_cap`` is a cap on the commission charged for a single leg of a
    multi-leg order, not on the order. tastytrade's $10 is the only one on the
    shortlist, and it is the reason its ranking flips with size.
    """

    open_per_contract: float
    close_per_contract: float
    per_leg_cap: Optional[float] = None

    def leg_cost(self, contracts: int, closing: bool) -> float:
        """Commission for one leg of ``contracts`` contracts."""

        rate = self.close_per_contract if closing else self.open_per_contract
        cost = rate * contracts
        if self.per_leg_cap is not None:
            cost = min(cost, self.per_leg_cap)
        return cost


@dataclass(frozen=True)
class Broker:
    """A broker's fee model, as read on the compile date."""

    name: str
    equity_option: OptionRate
    index_option: OptionRate
    platform_monthly: float = 0.0
    #: How much of ``platform_monthly`` is already being paid for another
    #: reason. Subtracted from the marginal total, because a sunk cost is not a
    #: reason to pick a broker. **Must not exceed ``platform_monthly``** — a
    #: cost cannot be sunk out of a total it was never added to, and doing so
    #: makes the broker that charges it look cheaper than one that charges
    #: nothing.
    already_paid_monthly: float = 0.0

    def __post_init__(self) -> None:
        if self.already_paid_monthly > self.platform_monthly:
            raise ValueError(
                f"{self.name}: already_paid_monthly "
                f"({self.already_paid_monthly}) exceeds platform_monthly "
                f"({self.platform_monthly}); a sunk cost cannot be subtracted "
                "from a total that never included it"
            )

    trades_index_options: bool = True
    note: str = ""

    def rate(self, index: bool) -> OptionRate:
        return self.index_option if index else self.equity_option

    def structure_cost(
        self, legs: int, contracts: int, index: bool, closing: bool
    ) -> float:
        """Commission to open (or close) one multi-leg structure."""

        return legs * self.rate(index).leg_cost(contracts, closing)

    def annual_cost(
        self,
        legs: int,
        contracts: int,
        cycles: int,
        close_rate: float,
        index: bool,
        exchange_fee: float = 0.0,
    ) -> Dict[str, float]:
        """Full-year cost of running a structure ``cycles`` times.

        ``close_rate`` is the fraction of cycles closed rather than left to
        expire worthless — a real lever, because several brokers charge nothing
        to close and the whole ranking moves with it.
        """

        opens = cycles
        closes = cycles * close_rate

        open_commission = opens * self.structure_cost(legs, contracts, index, False)
        close_commission = closes * self.structure_cost(legs, contracts, index, True)
        platform = 12.0 * self.platform_monthly
        sunk = 12.0 * self.already_paid_monthly

        contracts_traded = (opens + closes) * legs * contracts
        exchange = contracts_traded * exchange_fee

        commission = open_commission + close_commission
        return {
            "open": open_commission,
            "close": close_commission,
            "commission": commission,
            "platform": platform,
            "exchange": exchange,
            # What it costs standing alone.
            "total": commission + platform + exchange,
            # What adding it costs when its fixed fee is already being paid.
            "marginal": commission + platform + exchange - sunk,
            "contracts": contracts_traded,
        }


# ---------------------------------------------------------------------------
# The shortlist. Rates read 2026-08-24; sources in docs/brokers.md.
# ---------------------------------------------------------------------------

#: Interactive Brokers market data, already being paid per docs/state.md. It is
#: listed as already-paid so IB's marginal cost reflects reality: this desk is
#: buying that data whether or not it adds another broker.
IB_DATA_MONTHLY = 10.0


def default_brokers() -> List[Broker]:
    """The shortlist plus the two incumbents, with their published rates."""

    return [
        Broker(
            name="Tradier Pro",
            equity_option=OptionRate(0.00, 0.00),
            index_option=OptionRate(0.35, 0.35),
            platform_monthly=10.0,
            note="$0 equity/ETF options; $0.35 single-listed index. No futures.",
        ),
        Broker(
            name="tastytrade",
            equity_option=OptionRate(1.00, 0.00, per_leg_cap=10.0),
            index_option=OptionRate(1.00, 0.00, per_leg_cap=10.0),
            note="$1 to open, $0 to close, capped $10/leg. Cap bites at 10+.",
        ),
        Broker(
            name="IBKR (fixed)",
            equity_option=OptionRate(0.65, 0.65),
            index_option=OptionRate(0.65, 0.65),
            platform_monthly=IB_DATA_MONTHLY,
            already_paid_monthly=IB_DATA_MONTHLY,
            note="$0.65/contract, $1 order minimum. Data already paid here.",
        ),
        Broker(
            name="Schwab / tos",
            equity_option=OptionRate(0.65, 0.65),
            index_option=OptionRate(0.65, 0.65),
            note="$0.65/contract. Account already open; API is live-only.",
        ),
        Broker(
            name="Webull",
            equity_option=OptionRate(0.00, 0.00),
            index_option=OptionRate(0.50, 0.50),
            note="$0 equity options, $0.50 index. OpenAPI, no paper endpoint.",
        ),
        Broker(
            name="TradeStation",
            equity_option=OptionRate(0.60, 0.60),
            index_option=OptionRate(1.00, 1.00),
            note="$1.00/contract index and direct-routed. Covers futures too.",
        ),
        Broker(
            name="E*Trade",
            equity_option=OptionRate(0.65, 0.65),
            index_option=OptionRate(0.65, 0.65),
            note="$0.65/contract. OAuth 1.0a API. No advantage over Schwab.",
        ),
    ]


def _money(x: float) -> str:
    return f"${x:,.2f}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_brokers(args: argparse.Namespace) -> int:
    brokers = default_brokers()
    print("Fee models — read 2026-08-24, sourced in docs/brokers.md\n")
    header = f"{'Broker':<16}{'Equity option':<30}{'Index option':<16}{'Platform':<11}"
    print(header)
    print("-" * len(header))
    for b in brokers:
        eq = b.equity_option
        ix = b.index_option
        cap = f" cap {_money(eq.per_leg_cap)}/leg" if eq.per_leg_cap else ""
        eq_s = f"{_money(eq.open_per_contract)}/{_money(eq.close_per_contract)}{cap}"
        ix_s = f"{_money(ix.open_per_contract)}/{_money(ix.close_per_contract)}"
        plat = _money(b.platform_monthly * 12) + "/yr" if b.platform_monthly else "—"
        print(f"{b.name:<16}{eq_s:<30}{ix_s:<16}{plat:<11}")
    print("\nEquity/index columns are open/close per contract.")
    print("Exchange, clearing and regulatory fees are extra at every broker.")
    for b in brokers:
        if b.note:
            print(f"  {b.name:<16} {b.note}")
    return 0


def cmd_trade(args: argparse.Namespace) -> int:
    brokers = default_brokers()
    kind = "index" if args.index else "equity/ETF"
    print(
        f"One {args.legs}-leg {kind} structure, "
        f"{args.contracts} contract(s) per leg — open, then close\n"
    )
    rows = []
    for b in brokers:
        opening = b.structure_cost(args.legs, args.contracts, args.index, False)
        closing = b.structure_cost(args.legs, args.contracts, args.index, True)
        rows.append((b.name, opening, closing, opening + closing))
    rows.sort(key=lambda r: r[3])

    header = f"{'Broker':<16}{'Open':>10}{'Close':>10}{'Round trip':>13}"
    print(header)
    print("-" * len(header))
    for name, opening, closing, total in rows:
        print(
            f"{name:<16}{_money(opening):>10}{_money(closing):>10}{_money(total):>13}"
        )
    print("\nPlatform fees excluded — a per-trade view cannot carry them fairly.")
    print("Use `condor` for the annual view, where the fixed fee is the story.")
    return 0


def cmd_condor(args: argparse.Namespace) -> int:
    brokers = default_brokers()
    sizes = args.sizes or [1, 2, 5, 10, 25]

    print(
        f"Weekly {args.legs}-leg index condor, {args.cycles} cycles/yr, "
        f"{args.close_rate:.0%} closed rather than expired\n"
    )
    if args.exchange_fee:
        print(
            f"Exchange/regulatory fees charged at {_money(args.exchange_fee)}"
            " per contract per side.\n"
        )

    header = f"{'Broker':<16}" + "".join(f"{str(s) + ' lot':>12}" for s in sizes)
    print(header)
    print("-" * len(header))

    totals: Dict[str, List[float]] = {}
    for b in brokers:
        row = []
        for size in sizes:
            cost = b.annual_cost(
                legs=args.legs,
                contracts=size,
                cycles=args.cycles,
                close_rate=args.close_rate,
                index=True,
                exchange_fee=args.exchange_fee,
            )
            row.append(cost["marginal" if args.marginal else "total"])
        totals[b.name] = row
        print(f"{b.name:<16}" + "".join(f"{_money(c):>12}" for c in row))

    print("\nCheapest at each size:")
    for i, size in enumerate(sizes):
        best = min(totals.items(), key=lambda kv: kv[1][i])
        worst = max(totals.items(), key=lambda kv: kv[1][i])
        gap = worst[1][i] - best[1][i]
        print(
            f"  {size:>3} lot: {best[0]:<16} {_money(best[1][i]):>11}"
            f"   (spread to worst: {_money(gap)})"
        )

    basis = (
        "marginal (IB data treated as already paid)" if args.marginal else "standalone"
    )
    print(f"\nTotals are {basis}, platform fees included.")
    return 0


def cmd_spread(args: argparse.Namespace) -> int:
    """How big does the difference get, and does it ever matter?"""

    brokers = default_brokers()
    print(
        f"Annual cost spread across brokers — {args.legs}-leg "
        f"{'index' if args.index else 'equity'} structure, "
        f"{args.cycles} cycles/yr, {args.close_rate:.0%} closed\n"
    )
    header = f"{'Size':>6}{'Cheapest':<18}{'Best':>11}{'Worst':>11}{'Spread':>11}"
    print(header)
    print("-" * len(header))
    for size in args.sizes or [1, 2, 5, 10, 25, 50]:
        costs = {
            b.name: b.annual_cost(
                legs=args.legs,
                contracts=size,
                cycles=args.cycles,
                close_rate=args.close_rate,
                index=args.index,
                exchange_fee=args.exchange_fee,
            )["total"]
            for b in brokers
        }
        best_name, best = min(costs.items(), key=lambda kv: kv[1])
        _, worst = max(costs.items(), key=lambda kv: kv[1])
        print(
            f"{size:>6}{best_name:<18}{_money(best):>11}"
            f"{_money(worst):>11}{_money(worst - best):>11}"
        )
    print(
        "\nThe spread is the whole decision. Where it is small against the size "
        "of\nthe account, broker choice is not a cost question and should be "
        "settled on\ncapability instead — which is the argument docs/brokers.md "
        "makes."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Price the same option structure at every broker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_brokers = sub.add_parser("brokers", help="show the fee models")
    p_brokers.set_defaults(func=cmd_brokers)

    p_trade = sub.add_parser("trade", help="price one structure everywhere")
    p_trade.add_argument("--legs", type=int, default=4)
    p_trade.add_argument("--contracts", type=int, default=1)
    p_trade.add_argument(
        "--index", action="store_true", help="single-listed index option"
    )
    p_trade.set_defaults(func=cmd_trade)

    p_condor = sub.add_parser(
        "condor", help="the weekly condor program, a year at a time"
    )
    p_condor.add_argument("--legs", type=int, default=4)
    p_condor.add_argument("--cycles", type=int, default=52)
    p_condor.add_argument("--close-rate", type=float, default=0.7)
    p_condor.add_argument("--exchange-fee", type=float, default=0.0)
    p_condor.add_argument("--sizes", type=int, nargs="+")
    p_condor.add_argument(
        "--marginal",
        action="store_true",
        help="treat already-paid fixed costs (IB data) as sunk",
    )
    p_condor.set_defaults(func=cmd_condor)

    p_spread = sub.add_parser("spread", help="best vs worst, by size")
    p_spread.add_argument("--legs", type=int, default=4)
    p_spread.add_argument("--cycles", type=int, default=52)
    p_spread.add_argument("--close-rate", type=float, default=0.7)
    p_spread.add_argument("--exchange-fee", type=float, default=0.0)
    p_spread.add_argument("--index", action="store_true", default=True)
    p_spread.add_argument("--sizes", type=int, nargs="+")
    p_spread.set_defaults(func=cmd_spread)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:  # pragma: no cover - `| head` closed the pipe
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

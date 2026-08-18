#!/usr/bin/env python
"""Pre-trade commitment card and hold-time checker for long single-leg options.

Two commands:

    plan   Before entry. You supply what you know; it computes the exits, runs
           every gate, and appends a row to the log.

    check  While the position is open and losing. Tells you whether recovery
           is still within reach or whether you are paying rent on a hope.

A gate you did not answer fails the card. "I did not look up the earnings date"
is not the same statement as "there is no earnings date", and only one of them
is a reason to trade.

Examples::

    python tools/trade_card.py plan --symbol AAPL --spot 232 --strike 230 \\
        --dte 38 --iv 0.28 --premium 9.40 --contracts 1 --account 20000 \\
        --iv-rank 24 --bid 9.30 --ask 9.50 --target 245 --no-earnings

    python tools/trade_card.py check --spot 226 --strike 230 --dte 24 \\
        --iv 0.31 --premium 9.40
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import sys

import click

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pwb_toolbox.options import (  # noqa: E402
    FAIL,
    PASS,
    UNKNOWN,
    black_scholes,
    decay_schedule,
    drop_dead,
    hurdle_ratio,
    implied_vol,
    recovery,
    run_all,
    verdict,
)

LOG_PATH = os.environ.get("PWB_TRADE_LOG", "trade_log.csv")
EXIT_DTE = 21.0
STOP_PCT = 40.0
SCALE_OUT_PCT = 50.0

STATUS_STYLE = {
    PASS: ("PASS", "green"),
    FAIL: ("FAIL", "red"),
    UNKNOWN: ("????", "yellow"),
}

FIELDS = [
    "entry_date",
    "entry_time",
    "symbol",
    "kind",
    "spot",
    "strike",
    "dte_entry",
    "iv",
    "iv_rank",
    "spread_pct",
    "earnings",
    "target",
    "premium",
    "contracts",
    "cost",
    "pct_account",
    "delta",
    "theta_day",
    "hurdle",
    "verdict",
    "exit_21dte",
    "drop_dead",
    "stop_premium",
    "scale_out_premium",
    "max_loss",
    "exit_date",
    "exit_reason",
    "pnl",
    "followed_plan",
]


def _rule(char: str = "-") -> str:
    return char * 66


def _fmt_date(days_ahead: float) -> str:
    return (dt.date.today() + dt.timedelta(days=round(days_ahead))).isoformat()


@click.group()
def cli() -> None:
    """Trade card and hold-time analytics."""


@cli.command()
@click.option("--symbol", required=True, help="Underlying ticker.")
@click.option("--spot", required=True, type=float, help="Underlying price now.")
@click.option("--strike", required=True, type=float)
@click.option("--dte", required=True, type=float, help="Calendar days to expiry.")
@click.option("--premium", required=True, type=float, help="Premium per share.")
@click.option(
    "--iv",
    type=float,
    help="Implied vol as a decimal. Omit to solve it from the premium you paid.",
)
@click.option("--contracts", default=1, type=int)
@click.option("--account", required=True, type=float, help="Account equity.")
@click.option("--kind", default="call", type=click.Choice(["call", "put"]))
@click.option("--iv-rank", type=float, help="IV rank 0-100, from the option chain.")
@click.option("--bid", type=float, help="Current bid on the contract.")
@click.option("--ask", type=float, help="Current ask on the contract.")
@click.option(
    "--earnings",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Next earnings date, YYYY-MM-DD.",
)
@click.option(
    "--no-earnings",
    is_flag=True,
    help="Declare no earnings before your exit. Say it explicitly or the gate fails.",
)
@click.option("--target", type=float, help="Your price target on the underlying.")
@click.option("--rate", default=0.045, type=float)
@click.option("--log/--no-log", default=True, help="Append to the trade log.")
def plan(
    symbol,
    spot,
    strike,
    dte,
    iv,
    premium,
    contracts,
    account,
    kind,
    iv_rank,
    bid,
    ask,
    earnings,
    no_earnings,
    target,
    rate,
    log,
):
    """Build the commitment card and run every gate against it."""
    # The premium paid is the observable fact. A typed-in volatility that
    # disagrees with it would silently poison every greek below.
    solved = implied_vol(premium, spot, strike, dte, rate, kind)
    if iv is None:
        if solved is None:
            raise click.ClickException(
                f"No volatility reprices a premium of {premium:g} on this contract. Check the quote, or pass --iv explicitly."
            )
        iv = solved
        click.echo(f"  Implied volatility solved from your premium: {iv:.1%}")
    elif solved is not None and abs(solved - iv) / iv > 0.10:
        click.echo(
            click.style(
                f"  Note: a premium of {premium:g} implies {solved:.1%} vol, not the {iv:.1%} you gave.\n"
                f"  Using {iv:.1%} as instructed, but every greek below follows from it.",
                fg="yellow",
            )
        )
    g = black_scholes(spot, strike, dte, iv, rate, kind)
    h = hurdle_ratio(spot, strike, dte, iv, rate, kind)
    dd = drop_dead(premium, spot, strike, dte, iv, rate, kind, floor_dte=EXIT_DTE)

    cost = premium * 100 * contracts
    stop_premium = premium * (1 - STOP_PCT / 100)
    scale_premium = premium * (1 + SCALE_OUT_PCT / 100)
    max_loss = (premium - stop_premium) * 100 * contracts

    checks = run_all(
        spot=spot,
        strike=strike,
        dte=dte,
        vol=iv,
        premium=premium,
        contracts=contracts,
        account=account,
        kind=kind,
        rate=rate,
        hurdle=h,
        iv_rank=iv_rank,
        bid=bid,
        ask=ask,
        earnings=earnings.date() if earnings else None,
        declared_no_earnings=no_earnings,
        target=target,
    )
    overall = verdict(checks)

    click.echo(_rule("="))
    click.echo(
        f"  {symbol.upper()} {strike:g} {kind.upper()}  ·  {dte:g} DTE  ·  IV {iv:.1%}"
    )
    click.echo(_rule("="))

    click.echo("\n  POSITION")
    click.echo(f"    Premium paid          {premium:>10.2f} /share")
    click.echo(f"    Contracts             {contracts:>10d}")
    click.echo(f"    Cost                  {cost:>10,.0f}")
    click.echo(f"    Delta                 {g.delta:>10.3f}")
    click.echo(f"    Theta / day           {g.theta * 100 * contracts:>10.2f}  $/day")
    click.echo(f"    Extrinsic at risk     {g.extrinsic * 100 * contracts:>10.2f}")

    click.echo("\n  GATES")
    for c in checks:
        label, colour = STATUS_STYLE[c.status]
        click.echo(
            f"    {click.style(label, fg=colour, bold=True)}  "
            f"{c.name:<28}{c.detail}"
        )

    click.echo("\n  EXITS  (set these as resting orders at entry)")
    click.echo(
        f"    Scale out half at     {scale_premium:>10.2f}  (+{SCALE_OUT_PCT:g}%)"
    )
    click.echo(f"    Stop at               {stop_premium:>10.2f}  (-{STOP_PCT:g}%)")
    click.echo(
        f"    Max loss              {max_loss:>10,.0f}  "
        f"({max_loss / account * 100:.2f}% of account)"
    )
    hard_exit = _fmt_date(dte - EXIT_DTE) if dte > EXIT_DTE else "IMMEDIATE"
    click.echo(f"    Hard exit  {EXIT_DTE:g} DTE      {hard_exit:>10}")
    if dd.days_to_expiry is not None and dd.sessions_from_now:
        click.echo(
            f"    Drop-dead date        {_fmt_date(dte - dd.days_to_expiry):>10}"
            f"  ({dd.sessions_from_now:.0f} sessions — {dd.reason})"
        )
    else:
        click.echo(f"    Drop-dead date        {'n/a':>10}  ({dd.reason})")

    click.echo("\n  IF SPOT NEVER MOVES")
    for d, extrinsic, remaining in decay_schedule(spot, strike, dte, iv, rate, kind):
        click.echo(
            f"    {d:>3.0f} DTE  {extrinsic * 100 * contracts:>8.0f}  "
            f"{remaining:>5.1f}%  {'#' * max(1, round(remaining / 4))}"
        )

    click.echo()
    if overall == PASS:
        click.echo(
            "  "
            + click.style("CARD CLEARS — every gate passed.", fg="green", bold=True)
        )
    elif overall == FAIL:
        failed = [c.name for c in checks if c.status == FAIL]
        click.echo(
            "  " + click.style(f"CARD FAILS — {', '.join(failed)}", fg="red", bold=True)
        )
    else:
        missing = [c.name for c in checks if c.status == UNKNOWN]
        click.echo(
            "  "
            + click.style(
                f"CARD INCOMPLETE — {', '.join(missing)}", fg="yellow", bold=True
            )
        )
        click.echo(
            click.style(
                "  An unanswered gate is not a passed gate. Look it up.", fg="yellow"
            )
        )

    click.echo("\n  Still to answer out loud:")
    click.echo("    - Thesis, one sentence")
    click.echo("    - Entry trigger")
    click.echo("    - Price level on the underlying that proves me wrong")
    click.echo(_rule("="))

    if log:
        now = dt.datetime.now()
        spread_pct = ""
        if bid is not None and ask is not None and (bid + ask) > 0:
            spread_pct = f"{(ask - bid) / ((bid + ask) / 2) * 100:.2f}"
        row = {
            "entry_date": now.date().isoformat(),
            "entry_time": now.strftime("%H:%M"),
            "symbol": symbol.upper(),
            "kind": kind,
            "spot": f"{spot:.2f}",
            "strike": f"{strike:g}",
            "dte_entry": f"{dte:g}",
            "iv": f"{iv:.4f}",
            "iv_rank": "" if iv_rank is None else f"{iv_rank:.0f}",
            "spread_pct": spread_pct,
            "earnings": (
                earnings.date().isoformat()
                if earnings
                else ("none" if no_earnings else "")
            ),
            "target": "" if target is None else f"{target:g}",
            "premium": f"{premium:.2f}",
            "contracts": contracts,
            "cost": f"{cost:.2f}",
            "pct_account": f"{cost / account * 100:.2f}",
            "delta": f"{g.delta:.4f}",
            "theta_day": f"{g.theta * 100 * contracts:.2f}",
            "hurdle": f"{h:.3f}",
            "verdict": overall,
            "exit_21dte": hard_exit,
            "drop_dead": (
                _fmt_date(dte - dd.days_to_expiry)
                if dd.days_to_expiry is not None
                else ""
            ),
            "stop_premium": f"{stop_premium:.2f}",
            "scale_out_premium": f"{scale_premium:.2f}",
            "max_loss": f"{max_loss:.2f}",
            "exit_date": "",
            "exit_reason": "",
            "pnl": "",
            "followed_plan": "",
        }
        exists = os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerow(row)
        click.echo(
            f"\n  Logged to {LOG_PATH}. Fill exit_reason and followed_plan on close."
        )


@cli.command()
@click.option("--spot", required=True, type=float, help="Underlying price now.")
@click.option("--strike", required=True, type=float)
@click.option("--dte", required=True, type=float, help="Calendar days left now.")
@click.option("--iv", required=True, type=float)
@click.option("--premium", required=True, type=float, help="Premium you PAID.")
@click.option("--contracts", default=1, type=int)
@click.option("--kind", default="call", type=click.Choice(["call", "put"]))
@click.option("--rate", default=0.045, type=float)
def check(spot, strike, dte, iv, premium, contracts, kind, rate):
    """Am I still in a trade, or am I hoping? Run this on an open loser."""
    # Past the hard exit there is no budgeted horizon left, so measure against
    # the whole remaining life — the most generous reading available.
    past_exit = dte <= EXIT_DTE
    horizon = dte if past_exit else dte - EXIT_DTE
    r = recovery(premium, spot, strike, dte, iv, rate, kind, horizon_days=horizon)
    g = black_scholes(spot, strike, dte, iv, rate, kind)

    click.echo(_rule("="))
    click.echo(f"  {strike:g} {kind.upper()}  ·  {dte:g} DTE left  ·  spot {spot:g}")
    click.echo(_rule("="))
    click.echo(f"    Paid                  {premium:>10.2f}")
    click.echo(f"    Worth now             {r.current_premium:>10.2f}")
    click.echo(
        f"    Open P&L              {-r.loss_per_share * 100 * contracts:>10,.0f}"
    )
    click.echo(f"    Theta / day           {g.theta * 100 * contracts:>10.2f}  $/day")

    if r.loss_per_share > 0:
        click.echo("\n  TO GET BACK TO ENTRY")
        if r.breakeven_spot is None:
            click.echo("    Breakeven is unreachable before your exit date.")
        else:
            click.echo(f"    Spot must reach       {r.breakeven_spot:>10.2f}")
            click.echo(f"    That is a move of     {r.required_move:>+10.2f}")
            click.echo(f"    In sigma of the time  {r.sigma_required:>10.2f}  sigma")
            budget = (
                "all the way to expiry"
                if past_exit
                else f"to the {EXIT_DTE:g}-DTE exit"
            )
            click.echo(f"    Days you have         {horizon:>10.0f}  ({budget})")

    if past_exit:
        click.echo(
            click.style(
                f"\n  You are inside {EXIT_DTE:g} DTE. The rule says you should already be out;"
                "\n  the figures above assume you hold to expiry, which is the most"
                "\n  generous case and still counts against you.",
                fg="yellow",
            )
        )

    colour = "red" if r.verdict.startswith("EXIT") else "green"
    click.echo("\n  " + click.style(r.verdict.upper(), fg=colour, bold=True))
    click.echo(_rule("="))


if __name__ == "__main__":
    cli()

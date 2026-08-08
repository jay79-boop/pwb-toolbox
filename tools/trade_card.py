#!/usr/bin/env python
"""Pre-trade commitment card and hold-time checker for long single-leg options.

Two commands:

    plan   Before entry. You supply what you know; it computes the exits,
           checks the size against your account, and appends a row to the log.

    check  While the position is open and losing. Tells you whether recovery
           is still within reach or whether you are paying rent on a hope.

Examples::

    python tools/trade_card.py plan --symbol AAPL --spot 232 --strike 230 \\
        --dte 38 --iv 0.28 --premium 9.40 --contracts 1 --account 20000

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
    black_scholes,
    decay_schedule,
    drop_dead,
    hurdle_ratio,
    recovery,
)

LOG_PATH = os.environ.get("PWB_TRADE_LOG", "trade_log.csv")
EXIT_DTE = 21.0
MAX_PREMIUM_PCT = 4.0
STOP_PCT = 40.0
SCALE_OUT_PCT = 50.0

FIELDS = [
    "entry_date",
    "entry_time",
    "symbol",
    "kind",
    "spot",
    "strike",
    "dte_entry",
    "iv",
    "premium",
    "contracts",
    "cost",
    "pct_account",
    "delta",
    "theta_day",
    "hurdle",
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
    return char * 62


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
@click.option("--iv", required=True, type=float, help="Implied vol as a decimal.")
@click.option("--premium", required=True, type=float, help="Premium per share.")
@click.option("--contracts", default=1, type=int)
@click.option("--account", required=True, type=float, help="Account equity.")
@click.option("--kind", default="call", type=click.Choice(["call", "put"]))
@click.option("--rate", default=0.045, type=float)
@click.option("--log/--no-log", default=True, help="Append to the trade log.")
def plan(symbol, spot, strike, dte, iv, premium, contracts, account, kind, rate, log):
    """Build the commitment card and check the trade against the rules."""
    g = black_scholes(spot, strike, dte, iv, rate, kind)
    h = hurdle_ratio(spot, strike, dte, iv, rate, kind)
    dd = drop_dead(premium, spot, strike, dte, iv, rate, kind, floor_dte=EXIT_DTE)

    cost = premium * 100 * contracts
    pct = cost / account * 100
    stop_premium = premium * (1 - STOP_PCT / 100)
    scale_premium = premium * (1 + SCALE_OUT_PCT / 100)
    max_loss = (premium - stop_premium) * 100 * contracts

    click.echo(_rule("="))
    click.echo(
        f"  {symbol.upper()} {strike:g} {kind.upper()}  ·  {dte:g} DTE  ·  IV {iv:.1%}"
    )
    click.echo(_rule("="))

    click.echo("\n  POSITION")
    click.echo(f"    Premium paid          {premium:>10.2f} /share")
    click.echo(f"    Contracts             {contracts:>10d}")
    click.echo(f"    Cost                  {cost:>10,.0f}")
    click.echo(
        f"    % of account          {pct:>10.2f} %   (limit {MAX_PREMIUM_PCT:g}%)"
    )

    click.echo("\n  DECAY")
    click.echo(f"    Delta                 {g.delta:>10.3f}")
    click.echo(f"    Theta / day           {g.theta * 100 * contracts:>10.2f}  $/day")
    click.echo(f"    Extrinsic at risk     {g.extrinsic * 100 * contracts:>10.2f}")
    click.echo(f"    Daily hurdle          {h:>10.2f}  x a normal day's move")

    click.echo("\n  EXITS  (set these as resting orders at entry)")
    click.echo(
        f"    Scale out half at     {scale_premium:>10.2f}  (+{SCALE_OUT_PCT:g}%)"
    )
    click.echo(f"    Stop at               {stop_premium:>10.2f}  (-{STOP_PCT:g}%)")
    click.echo(
        f"    Max loss              {max_loss:>10,.0f}  ({max_loss / account * 100:.2f}% of account)"
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
        bar = "#" * max(1, round(remaining / 4))
        click.echo(
            f"    {d:>3.0f} DTE  {extrinsic * 100 * contracts:>8.0f}  "
            f"{remaining:>5.1f}%  {bar}"
        )

    warnings = []
    if pct > MAX_PREMIUM_PCT:
        warnings.append(
            f"Cost is {pct:.1f}% of account, over the {MAX_PREMIUM_PCT:g}% limit."
        )
    if dte < 30:
        warnings.append(f"{dte:g} DTE is short — the rule is 30-45.")
    if abs(g.delta) < 0.55 or abs(g.delta) > 0.75:
        warnings.append(f"Delta {g.delta:.2f} is outside the 0.60-0.70 band.")
    if h > 0.25:
        warnings.append(
            f"Hurdle {h:.2f} — decay is expensive relative to daily movement."
        )

    if warnings:
        click.echo("\n  " + click.style("RULE BREAKS", fg="red", bold=True))
        for w in warnings:
            click.echo(click.style(f"    ! {w}", fg="red"))
    else:
        click.echo("\n  " + click.style("All rules satisfied.", fg="green", bold=True))

    click.echo("\n  Blank fields you must still answer out loud:")
    click.echo("    - Thesis, one sentence")
    click.echo("    - Entry trigger")
    click.echo("    - Price level on the underlying that proves me wrong")
    click.echo(_rule("="))

    if log:
        now = dt.datetime.now()
        row = {
            "entry_date": now.date().isoformat(),
            "entry_time": now.strftime("%H:%M"),
            "symbol": symbol.upper(),
            "kind": kind,
            "spot": f"{spot:.2f}",
            "strike": f"{strike:g}",
            "dte_entry": f"{dte:g}",
            "iv": f"{iv:.4f}",
            "premium": f"{premium:.2f}",
            "contracts": contracts,
            "cost": f"{cost:.2f}",
            "pct_account": f"{pct:.2f}",
            "delta": f"{g.delta:.4f}",
            "theta_day": f"{g.theta * 100 * contracts:.2f}",
            "hurdle": f"{h:.3f}",
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

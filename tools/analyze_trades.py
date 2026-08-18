#!/usr/bin/env python
"""Turn a Schwab transaction export into a diagnosis of your trading.

    python tools/analyze_trades.py path/to/export.csv

Redact the account number before sharing the file anywhere. The export itself
never leaves your machine when this runs — it does no network access.
"""

from __future__ import annotations

import os
import sys

import click

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pwb_toolbox.journal import (  # noqa: E402
    ParseError,
    by_dte_bucket,
    by_entry_hour,
    exit_census,
    hold_time_summary,
    load,
    summary,
    wash_sale_candidates,
)


def _bar(pct: float, width: int = 20) -> str:
    return "#" * round(pct / 100 * width)


def _table(title: str, buckets, empty_note: str) -> None:
    click.echo(f"\n  {title}")
    if not buckets:
        click.echo(f"    {empty_note}")
        return
    click.echo(f"    {'':<20}{'trades':>7}{'win %':>8}{'avg P&L':>11}")
    for b in buckets:
        click.echo(
            f"    {b.label:<20}{b.trades:>7}{b.win_rate:>7.0f}%{b.avg_pnl:>11,.0f}"
            f"  {_bar(b.win_rate)}"
        )


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def main(path: str) -> None:
    """Analyze a Schwab transaction export."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()

    try:
        trips, unmatched = load(text)
    except ParseError as exc:
        raise click.ClickException(
            f"{exc}\n\nThe parser refuses to guess at rows it does not recognize, "
            "since a silently dropped fill produces a log that looks complete and "
            "is wrong. Send me the offending line and I will widen the parser."
        )

    if not trips:
        raise click.ClickException(
            "No completed round trips found. If this export covers only open "
            "positions, widen the date range and export again."
        )

    s = summary(trips)
    click.echo("=" * 62)
    click.echo(f"  {s['trades']} round trips  ·  {os.path.basename(path)}")
    click.echo("=" * 62)

    click.echo("\n  HEADLINE")
    click.echo(f"    Win rate              {s['win_rate']:>10.1f} %")
    click.echo(f"    Total P&L             {s['total_pnl']:>10,.0f}")
    click.echo(f"    Average win           {s['avg_win']:>10,.0f}")
    click.echo(f"    Average loss          {s['avg_loss']:>10,.0f}")
    click.echo(f"    Profit factor         {s['profit_factor']:>10.2f}")

    census = exit_census(trips)
    expired = census.get("expired", 0)
    click.echo("\n  HOW POSITIONS ENDED")
    for reason, count in census.most_common():
        share = count / len(trips) * 100
        click.echo(f"    {reason:<20}{count:>7}{share:>7.0f}%  {_bar(share)}")
    if expired:
        click.echo(
            click.style(
                f"    -> {s['expired_worthless']} expired worthless. Every one of those "
                "was a decision not to exit.",
                fg="red",
            )
        )

    h = hold_time_summary(trips)
    click.echo("\n  HOLD TIME")
    click.echo(f"    Winners               {h['avg_hold_winners']:>10.1f} days")
    click.echo(f"    Losers                {h['avg_hold_losers']:>10.1f} days")
    click.echo(f"    Longest               {h['max_hold']:>10.0f} days")
    if h["avg_hold_losers"] > h["avg_hold_winners"]:
        ratio = h["avg_hold_losers"] / max(h["avg_hold_winners"], 0.1)
        click.echo(
            click.style(
                f"    -> Losers held {ratio:.1f}x longer than winners. "
                "That is the disposition effect, measured.",
                fg="red",
            )
        )

    _table(
        "BY DAYS TO EXPIRY AT ENTRY",
        by_dte_bucket(trips),
        "no trades bucketed",
    )
    _table(
        "BY ENTRY TIME OF DAY",
        by_entry_hour(trips),
        "This export carries no execution times. Use the thinkorswim\n"
        "    Account Statement export instead to test the 10am question.",
    )

    hits = wash_sale_candidates(trips)
    if hits:
        click.echo(f"\n  WASH SALE CANDIDATES  ({len(hits)})")
        click.echo("    A screen, not a determination — confirm with your accountant.")
        for underlying, closed, reopened, pnl in hits[:10]:
            gap = (reopened - closed).days
            click.echo(
                f"    {underlying:<6} loss {pnl:>9,.0f} closed {closed}, "
                f"reopened {reopened} ({gap}d)"
            )

    if unmatched:
        click.echo(
            f"\n  {len(unmatched)} unmatched fills (open positions, or closes whose "
            "open predates this export)."
        )
    click.echo("=" * 62)


if __name__ == "__main__":
    main()

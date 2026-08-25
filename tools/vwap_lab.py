"""Run the VWAP setups over one or two feeds and say what survived.

The strategy lives in ``pwb_toolbox.backtesting.vwap``; this is the driver
that holds it to the house standard from ``tools/backtest_lab.py``: costs are
always charged, results are normalised to basis points of price, and -- when a
second vendor's feed of the same instrument is supplied -- every setup has to
clear the two-vendor noise floor before its number means anything.

The crossover setup is run on purpose as a control. The published parameter
sweeps found it worthless, so the expected reading of the table is fade and/or
pullback with a real edge and cross without one; a run where cross wins is
evidence about the harness or the data, not about the market.

Single feed, ES minute bars from histdata::

    python tools/vwap_lab.py SPXUSD.csv --vendor histdata --mintick 0.25

Both vendors, per-year, with the confirms on::

    python tools/vwap_lab.py SPXUSD.csv --vendor histdata \
        --second SPX_oanda.csv --second-vendor oanda \
        --rvol-min 1.5 --day-type-bp 30 --ma-len 200

Crypto (24/7, UTC-midnight anchor -- a convention, not a fact)::

    python tools/vwap_lab.py BTC.csv --vendor generic --crypto --mintick 0.5

VWAP needs volume. Feeds that carry none (histdata index CFDs do this) make
the indicator degrade to TWAP; the zero-volume share is printed so that run
is read for what it is.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwb_toolbox.backtesting.vwap import SETUPS, VwapStrategy
from tools.backtest_lab import (
    KNOWN_FEED_TIMEZONES,
    backtest,
    noise_floor,
    read_generic,
    read_histdata,
    to_bars,
)


def load_feed(path, vendor, tz=None, time_column="time"):
    """One vendor's file -> naive-UTC OHLCV frame, via the lab's loaders."""
    if vendor == "histdata":
        return read_histdata(path, tz=tz or KNOWN_FEED_TIMEZONES["histdata"])
    return read_generic(
        path, tz=tz or KNOWN_FEED_TIMEZONES.get(vendor, "UTC"), time_column=time_column
    )


def zero_volume_share(frame) -> float:
    """The fraction of bars carrying no volume -- the TWAP-degeneracy gauge."""
    if not len(frame):
        return 0.0
    return float((frame["volume"] <= 0).mean())


#: Every confirm the CLI can request, in the order the report lists them.
CONFIRMS = ("rvol_min", "day_type_bp", "ma_len", "rsi_len")

#: The flag each one arrives as, for a report that names what was typed.
FLAG_NAMES = {
    "rvol_min": "--rvol-min",
    "day_type_bp": "--day-type-bp",
    "ma_len": "--ma-len",
    "rsi_len": "--rsi",
}

#: Which confirms each setup's entry path actually consults, read off
#: ``VwapStrategy``: ``_try_fade`` tests day type, rvol, MA and RSI;
#: ``_try_pullback`` tests all but RSI; ``_try_cross`` tests the crossover and
#: nothing else. The control is deliberately left naive -- gating a
#: stop-and-reverse system that is always in the market would make it neither
#: the naive strategy nor a filtered one -- so this table exists to say so
#: rather than to change it.
SETUP_GATES = {
    "fade": ("day_type_bp", "rvol_min", "ma_len", "rsi_len"),
    "pullback": ("day_type_bp", "rvol_min", "ma_len"),
    "cross": (),
}


def gate_report(setups, params):
    """Which requested confirms each setup honoured, and which it ignored.

    The results table puts setups side by side under one set of flags, which
    reads as a like-for-like comparison. Under any confirm it is not: a cross
    row printed beneath ``--rvol-min 1.5`` is the *ungated* result, so its
    trade count looks like a fact about how selective the setup is when it is
    a fact about which setups read the flag. Measured on real SPY bars, three
    stacked confirms took fade from 149 trades to 2 and cross from 316 to
    300 -- a difference that is entirely this, and nothing about the market.

    Returns ``[]`` when no confirm was requested; there is then nothing to
    disclaim and the table already means what it looks like.
    """
    requested = [c for c in CONFIRMS if params.get(c)]
    if not requested:
        return []

    lines = ["", "Confirms requested: " + ", ".join(FLAG_NAMES[c] for c in requested)]
    for setup in setups:
        honoured = SETUP_GATES.get(setup, ())
        ignored = [c for c in requested if c not in honoured]
        if not ignored:
            lines.append(f"  {setup:<9} applies all of them")
            continue
        applied = [c for c in requested if c in honoured]
        detail = (
            "applies none of them"
            if not applied
            else ("applies " + ", ".join(FLAG_NAMES[c] for c in applied))
        )
        lines.append(
            f"  {setup:<9} IGNORES "
            + ", ".join(FLAG_NAMES[c] for c in ignored)
            + f" -- {detail}"
        )
        if setup == "cross":
            lines.append(
                "            so the cross row above is the ungated result. Read "
                "its trade count"
            )
            lines.append("            as a fact about the flags, not about the setup.")
            if "day_type_bp" in ignored:
                lines.append(
                    "            (--day-type-bp still holds every setup back "
                    "until the day is"
                )
                lines.append(
                    "            classified, which is why the count moves a "
                    "little even so.)"
                )
    return lines


def volume_warnings(frame, second=None, labels=("primary", "second")):
    """Every feed too volumeless for VWAP to mean anything, named.

    Checked per feed rather than once per run. The noise floor compares one
    strategy across two vendors, so a volumeless *second* feed leaves half of
    that comparison a TWAP result while the printed verdict still calls the
    gap vendor disagreement -- the run then reads as a finding about data
    sourcing when what actually differs is which indicator each side computed.

    A feed carrying volume against one that does not is the worst of the three
    cases and gets its own line: two volumeless feeds at least compare like
    with like, but a mixed pair prices VWAP against TWAP and reports the
    difference as a fact about the vendors.
    """
    feeds = [(f, l) for f, l in zip((frame, second), labels) if f is not None]
    shares = [(zero_volume_share(f), l) for f, l in feeds]
    out = [
        f"WARNING: {100 * share:.0f}% of bars on the {label} feed carry zero "
        "volume -- this VWAP is a TWAP wearing the name. Read the numbers "
        "accordingly."
        for share, label in shares
        if share > 0.5
    ]
    if len(shares) == 2 and (shares[0][0] > 0.5) != (shares[1][0] > 0.5):
        empty, full = sorted(shares, key=lambda s: -s[0])
        out.append(
            f"WARNING: the {empty[1]} feed is volumeless and the {full[1]} feed "
            "is not, so the noise floor below compares a TWAP result against a "
            "VWAP one. That gap is not vendor disagreement and must not be read "
            "as any."
        )
    return out


def per_year(frame, min_bars=500):
    """Split a frame by calendar year, dropping stubs too short to mean much."""
    return {
        int(year): sub
        for year, sub in frame.groupby(frame.index.year)
        if len(sub) >= min_bars
    }


def run_setup(frame, setup, mintick, minutes, **params):
    """One setup over one frame, costs charged, via the lab's ``backtest``."""
    return backtest(
        frame, VwapStrategy, mintick=mintick, minutes=minutes, setup=setup, **params
    )


def run_lab(frame, setups, mintick, minutes, second=None, min_bars=500, **params):
    """Every requested setup over one feed, and the noise floor when two.

    Returns ``{setup: {"result": Result, "floor": NoiseFloor | None}}``. With
    a second feed, per-year results from both vendors feed ``noise_floor``;
    the headline Result still comes from the first feed, which is arbitrary
    and exactly why the floor verdict is printed beside it.
    """
    out = {}
    years_a = per_year(frame, min_bars) if second is not None else {}
    years_b = per_year(second, min_bars) if second is not None else {}
    for setup in setups:
        result = run_setup(frame, setup, mintick, minutes, **params)
        floor = None
        if second is not None:
            a = {
                y: run_setup(sub, setup, mintick, minutes, **params)
                for y, sub in years_a.items()
            }
            b = {
                y: run_setup(sub, setup, mintick, minutes, **params)
                for y, sub in years_b.items()
            }
            common = set(a) & set(b)
            if len(common) >= 2:
                floor = noise_floor(
                    {y: a[y] for y in common}, {y: b[y] for y in common}
                )
        out[setup] = {"result": result, "floor": floor}
    return out


def trades_as_records(trade_log, symbol):
    """Closed trades in the shape the night lab's arithmetic reads.

    Same contract as ``reversal_15m_sim.trades_as_records``: entry/stop/exit
    and direction are what ``night_lab.trade_r`` computes R from, the lane
    keeps sim trades distinguishable in leak-mining, and the timestamps carry
    the patterns leaks are mined over.
    """
    records = []
    for i, t in enumerate(trade_log):
        records.append(
            {
                "id": f"sim-vwap-{i}",
                "lane": "sim-vwap",
                "symbol": symbol,
                "direction": t["direction"],
                "status": "closed",
                "entry": t["entry"],
                "stop": t["stop"],
                "exit": t["exit"],
                "opened": t["opened"],
                "closed": t["closed"],
                "reason": t["reason"],
            }
        )
    return records


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", help="bar file from the primary vendor")
    ap.add_argument(
        "--vendor",
        default="generic",
        help="histdata | oanda | generic (decides loader and timezone)",
    )
    ap.add_argument("--tz", default=None, help="override the vendor's known timezone")
    ap.add_argument("--second", help="the same instrument from a second vendor")
    ap.add_argument("--second-vendor", default="generic")
    ap.add_argument("--second-tz", default=None)
    ap.add_argument("--minutes", type=int, default=5, help="bar size to resample to")
    ap.add_argument("--mintick", type=float, default=0.25)
    ap.add_argument(
        "--setups",
        default=",".join(SETUPS),
        help=f"comma-separated subset of {', '.join(SETUPS)}",
    )
    ap.add_argument("--band-k", type=float, default=2.0)
    ap.add_argument("--stop-k", type=float, default=1.0)
    ap.add_argument("--session-start", default="09:30")
    ap.add_argument("--session-end", default="15:55")
    ap.add_argument(
        "--crypto",
        action="store_true",
        help="24/7 mode: UTC timezone, no session filter, UTC-midnight anchor",
    )
    ap.add_argument(
        "--anchor",
        default=None,
        help="anchored-VWAP mode: naive-UTC ISO timestamp to anchor at "
        "(pairs naturally with --setups pullback)",
    )
    ap.add_argument("--rvol-min", type=float, default=0.0)
    ap.add_argument("--day-type-bp", type=float, default=0.0)
    ap.add_argument("--ma-len", type=int, default=0)
    ap.add_argument(
        "--rsi", type=int, default=0, metavar="LEN", help="RSI control gate on fades"
    )
    ap.add_argument("--symbol", default=None, help="symbol label on exported trades")
    ap.add_argument(
        "--trades-out",
        metavar="PATH",
        help="write closed trades as JSON for the night lab (plan --sim)",
    )
    args = ap.parse_args(argv)

    frame = to_bars(load_feed(args.csv, args.vendor, tz=args.tz), minutes=args.minutes)
    second = None
    if args.second:
        second = to_bars(
            load_feed(args.second, args.second_vendor, tz=args.second_tz),
            minutes=args.minutes,
        )

    for line in volume_warnings(
        frame,
        second,
        labels=(f"primary ({args.vendor})", f"second ({args.second_vendor})"),
    ):
        print(line)

    params = dict(
        band_k=args.band_k,
        stop_k=args.stop_k,
        session_start=args.session_start,
        session_end=args.session_end,
        rvol_min=args.rvol_min,
        day_type_bp=args.day_type_bp,
        ma_len=args.ma_len,
        rsi_len=args.rsi,
        anchor=args.anchor,
    )
    if args.crypto:
        params.update(tz="UTC", rth_only=False)

    setups = [s.strip() for s in args.setups.split(",") if s.strip()]
    results = run_lab(
        frame, setups, args.mintick, args.minutes, second=second, **params
    )

    print(f"{'setup':<10} {'trades':>6} {'win%':>6} {'bps':>8}  noise floor")
    for setup, row in results.items():
        r = row["result"]
        floor = row["floor"]
        verdict = str(floor) if floor is not None else "-- (one feed: unjudged)"
        print(f"{setup:<10} {r.trades:>6} {r.win_rate:>6.1f} {r.bps:>8.0f}  {verdict}")
    for line in gate_report(setups, params):
        print(line)
    if "cross" in results and results["cross"]["result"].bps > 0:
        print(
            "\nNote: the crossover control came out positive. The published "
            "sweeps found it worthless -- before believing any row above, "
            "work out why the control passed."
        )

    if args.trades_out:
        logs = []
        for setup, row in results.items():
            strat = row["result"].strategy
            if strat is not None:
                logs.extend(trades_as_records(strat.trade_log, args.symbol or "VWAP"))
        Path(args.trades_out).write_text(
            json.dumps({"trades": logs}, indent=2), encoding="utf-8"
        )
        print(f"\n{len(logs)} closed trade(s) -> {args.trades_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

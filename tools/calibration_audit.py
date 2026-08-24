#!/usr/bin/env python
"""When Black-Scholes said 30%, how often did it actually happen?

Every option decision in this repo leans on two model numbers: the chance
price *finishes* beyond a level, and the chance it ever *touches* one. Both
come out of a lognormal with constant volatility, which is a convenient
fiction. This measures the fiction against the record.

The method, and why each piece is the way it is:

    barriers        For each window the level sits k standard deviations away
                    (k x sigma x sqrt(T)), so the model's own probability is
                    the *same* for every window in a row. That makes each row
                    an exact binomial test rather than an approximation, and
                    it makes the rows a reliability curve: 0.5 sigma is a
                    ~62% touch, 2.0 sigma a ~5% one.
    windows         Non-overlapping, so the outcomes inside a row are drawn
                    from disjoint stretches of history. Overlapping windows
                    would multiply the apparent sample without adding any
                    information, which is how a calibration audit convicts
                    itself of significance it never had.
    two vol modes   `traded` sets the barrier from the volatility you could
                    have known (trailing realized); `static` uses one
                    volatility for the whole record. The pair separates two
                    different failures — a model that is wrong, and a model
                    that is right but fed a bad estimate — and it prices the
                    trailing estimate itself: if `static` misses badly where
                    `traded` does not, keeping a live volatility estimate is
                    earning its keep. (`static` is computed over the full
                    sample, so it knows the future; it is a benchmark, not
                    something you could have traded.)
    no drift        The model probability is computed at rate 0, the same
                    driftless assumption `touch_probability` makes. A real
                    equity drift therefore shows up as a *finding* -- up
                    barriers over-hitting while down barriers under-hit --
                    instead of being quietly absorbed into the model.
    touch, honestly Touch is judged on intraday highs and lows when the data
                    carries them, and on closes when it does not. Closes
                    alone can only understate touching, so the report says
                    which reading it used.

One asymmetry is structural and worth expecting before you see it: the
reflection principle assumes the price is watched *continuously*, and a
daily bar is not continuous. A close barrier — half a standard deviation
out, where a path wanders across it and back inside a single session — is
therefore touched less often in daily data than P(touch) = 2 P(finish)
predicts, while distant barriers, which take a real move to reach, are
barely affected. A near-barrier row that under-hits is usually this, not a
discovery. The far rows are where a genuine fat tail shows up.

Two dependencies are worth stating rather than hiding. Windows do not
overlap, but the four barrier distances share a window, and tickers scanned
together share a market; the p-values are therefore optimistic against
cross-sectional correlation. Run a single ticker for the clean version, and
treat one marginal row among sixteen as nothing.

Data is any CSV of daily bars with `timestamp` and `close` columns, plus
`high`/`low` if you have them -- which is exactly what `season_scan.py fetch`
writes into season/data. Nothing here touches the network.

Examples::

    python tools/calibration_audit.py                       # season/data, 21d
    python tools/calibration_audit.py --symbols SPY --horizon 5
    python tools/calibration_audit.py --json audit.json
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwb_toolbox.options.greeks import DAYS_PER_YEAR, TRADING_DAYS  # noqa: E402
from pwb_toolbox.options.probability import (  # noqa: E402
    finish_probability,
    touch_probability,
)

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "season" / "data"
SIGMAS = (0.5, 1.0, 1.5, 2.0)
FDR_Q = 0.10


@dataclass(frozen=True)
class Day:
    day: date
    high: float
    low: float
    close: float


def read_series(path: Path) -> list[Day]:
    """Daily bars from a CSV, high/low falling back to the close.

    A file with only closes still audits; it just cannot see a barrier that
    was touched intraday and given back, so its touch frequencies are a
    floor rather than a measurement.
    """
    out: list[Day] = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            raw = row.get("timestamp") or row.get("date")
            try:
                day = datetime.fromisoformat(str(raw)).date()
                close = float(row["close"])
            except (TypeError, ValueError, KeyError):
                continue
            if close <= 0:
                continue

            def side(name: str) -> float:
                try:
                    value = float(row[name])
                except (TypeError, ValueError, KeyError):
                    return close
                return value if value > 0 else close

            out.append(Day(day, side("high"), side("low"), close))
    return sorted(out, key=lambda d: d.day)


def has_intraday(series: list[Day]) -> bool:
    return any(d.high > d.close or d.low < d.close for d in series)


def realized_vol(closes: list[float]) -> float:
    """Annualized close-to-close volatility, on 252 trading days."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return 0.0
    return statistics.stdev(rets) * math.sqrt(TRADING_DAYS)


def windows(n: int, horizon: int, lookback: int) -> list[int]:
    """Start indices of non-overlapping forward windows.

    Index i means "stand on bar i, having seen `lookback` returns, and watch
    the next `horizon` bars". The next window starts where this one ended,
    so no bar is ever counted twice as evidence.
    """
    if horizon < 1 or lookback < 2:
        raise ValueError("horizon must be >= 1 and lookback >= 2")
    starts = []
    i = lookback
    while i + horizon < n:
        starts.append(i)
        i += horizon
    return starts


def _log_pmf(n: int, k: int, p: float) -> float:
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def binom_two_sided_p(n: int, k: int, p: float) -> float:
    """Exact two-sided binomial p, by the method of small p-values.

    Every outcome at least as unlikely as the observed one is summed, which
    is the two-sided test that does not assume the distribution is
    symmetric — and it is not, away from p=0.5, which is precisely where the
    interesting barriers live.
    """
    if n <= 0:
        return 1.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    observed = _log_pmf(n, k, p) + 1e-9  # tolerance so exact ties count
    total = sum(
        math.exp(lp) for i in range(n + 1) if (lp := _log_pmf(n, i, p)) <= observed
    )
    return min(1.0, total)


def bh_fdr(pvalues: list[float], q: float = FDR_Q) -> list[bool]:
    """Benjamini-Hochberg across the rows of one audit."""
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    threshold_rank = 0
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= q * rank / n:
            threshold_rank = rank
    passing = [False] * n
    for rank, idx in enumerate(order, start=1):
        if rank <= threshold_rank:
            passing[idx] = True
    return passing


def collect(
    series: list[Day],
    horizon: int,
    lookback: int,
    mode: str,
    sigmas: tuple[float, ...] = SIGMAS,
) -> list[dict]:
    """One record per (window, barrier): what the model said, what happened.

    ``mode`` is "traded" (barrier set from trailing volatility, the number
    you could have used at the time) or "static" (one volatility for the
    whole record, which is the benchmark the trailing estimate has to beat).

    Setting the barrier from the window's *own* realized volatility would be
    the obvious third mode and is deliberately absent: dividing a move by
    the volatility that move helped produce is self-normalization, and it
    shrinks the tails mechanically. It would report a fat-tailed series as
    thin-tailed.
    """
    if mode not in ("traded", "static"):
        raise ValueError("mode must be 'traded' or 'static'")
    closes = [d.close for d in series]
    static_vol = realized_vol(closes) if mode == "static" else 0.0
    # The volatility is annualized on trading days; the pricing functions
    # take CALENDAR days. Converting here is not cosmetic -- mixing the two
    # conventions silently rescales every probability in the table.
    days = horizon * DAYS_PER_YEAR / TRADING_DAYS
    years = days / DAYS_PER_YEAR

    records = []
    for i in windows(len(series), horizon, lookback):
        spot = closes[i]
        forward = series[i + 1 : i + 1 + horizon]
        sigma = (
            realized_vol(closes[i - lookback : i + 1])
            if mode == "traded"
            else static_vol
        )
        if sigma <= 0 or spot <= 0:
            continue
        move = sigma * math.sqrt(years)
        for k in sigmas:
            for side in (1, -1):
                target = spot * math.exp(side * k * move)
                finished = (
                    forward[-1].close >= target
                    if side > 0
                    else forward[-1].close <= target
                )
                touched = any(
                    (d.high >= target) if side > 0 else (d.low <= target)
                    for d in forward
                )
                records.append(
                    {
                        "day": series[i].day.isoformat(),
                        "k": k,
                        "side": "up" if side > 0 else "down",
                        "sigma": sigma,
                        "p_finish": finish_probability(
                            spot, target, sigma, days, rate=0.0
                        ),
                        "p_touch": touch_probability(spot, target, sigma, days),
                        "finished": finished,
                        "touched": touched,
                    }
                )
    return records


def reliability(records: list[dict], q: float = FDR_Q) -> list[dict]:
    """Model probability against realized frequency, one row per barrier."""
    rows = []
    for kind, p_key, hit_key in (
        ("finish", "p_finish", "finished"),
        ("touch", "p_touch", "touched"),
    ):
        for side in ("up", "down"):
            for k in sorted({r["k"] for r in records}):
                group = [r for r in records if r["side"] == side and r["k"] == k]
                if not group:
                    continue
                n = len(group)
                hits = sum(1 for r in group if r[hit_key])
                # Within a row the model probability is all but constant (the
                # barrier is defined in sigma units), so its mean is the
                # binomial parameter and the test is essentially exact.
                predicted = sum(r[p_key] for r in group) / n
                rows.append(
                    {
                        "kind": kind,
                        "side": side,
                        "k": k,
                        "n": n,
                        "hits": hits,
                        "predicted": round(predicted, 4),
                        "actual": round(hits / n, 4),
                        "diff": round(hits / n - predicted, 4),
                        "p": round(binom_two_sided_p(n, hits, predicted), 4),
                    }
                )
    for row, passed in zip(rows, bh_fdr([r["p"] for r in rows], q)):
        row["miscalibrated"] = passed
    return rows


def audit(
    all_series: dict[str, list[Day]],
    horizon: int,
    lookback: int,
    mode: str,
) -> dict:
    records: list[dict] = []
    for symbol in sorted(all_series):
        for record in collect(all_series[symbol], horizon, lookback, mode):
            record["symbol"] = symbol
            records.append(record)
    rows = reliability(records)
    return {
        "mode": mode,
        "horizon": horizon,
        "lookback": lookback,
        "symbols": sorted(all_series),
        "windows": len({(r["symbol"], r["day"]) for r in records}),
        "intraday": all(has_intraday(s) for s in all_series.values()),
        "rows": rows,
    }


def render(result: dict) -> str:
    label = {
        "traded": f"as traded (trailing {result['lookback']}d volatility)",
        "static": "one static volatility for the whole record",
    }[result["mode"]]
    out = [
        f"{result['horizon']}-day horizon, {label}",
        f"{result['windows']} non-overlapping windows over "
        f"{len(result['symbols'])} ticker(s); touch read from "
        + ("intraday highs and lows" if result["intraday"] else "CLOSES ONLY"),
        "",
    ]
    head = f"{'':<8}{'barrier':>9}{'n':>7}{'model':>9}{'actual':>9}{'gap':>9}{'p':>9}"
    for kind in ("finish", "touch"):
        rows = [r for r in result["rows"] if r["kind"] == kind]
        if not rows:
            continue
        out.append(kind.upper())
        out.append(head)
        for r in rows:
            flag = "  <-- miscalibrated" if r["miscalibrated"] else ""
            out.append(
                f"{r['side']:<8}{r['k']:>8.1f}s{r['n']:>7}"
                f"{100 * r['predicted']:>8.1f}%{100 * r['actual']:>8.1f}%"
                f"{100 * r['diff']:>+8.1f}{r['p']:>9.4f}{flag}"
            )
        out.append("")
    convicted = [r for r in result["rows"] if r["miscalibrated"]]
    out.append(
        f"{len(convicted)} of {len(result['rows'])} rows miss by more than luck "
        f"explains (Benjamini-Hochberg, q={FDR_Q})."
    )
    if not result["intraday"]:
        out.append(
            "Touch was judged on closes, which can only understate it: a level "
            "reached and given back inside a day does not appear here."
        )
    out.append(
        "Barrier distances share their window and tickers share a market, so "
        "these p-values run optimistic. One marginal row is nothing."
    )
    return "\n".join(out)


def load(directory: Path, symbols: list[str] | None) -> dict[str, list[Day]]:
    paths = (
        [directory / f"{s.upper()}.csv" for s in symbols]
        if symbols
        else sorted(directory.glob("*.csv"))
    )
    out = {}
    for path in paths:
        if not path.exists():
            print(f"  {path.name}: not found")
            continue
        series = read_series(path)
        if series:
            out[path.stem] = series
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="directory of daily CSVs")
    ap.add_argument("--symbols", nargs="*", help="tickers (default: every CSV there)")
    ap.add_argument("--horizon", type=int, default=21, help="trading days ahead")
    ap.add_argument(
        "--lookback", type=int, default=20, help="trailing days for the vol estimate"
    )
    ap.add_argument(
        "--mode",
        choices=("traded", "static", "both"),
        default="both",
        help="which volatility sets the barrier",
    )
    ap.add_argument("--json", metavar="PATH", help="write the full result as JSON")
    args = ap.parse_args(argv)

    directory = Path(args.dir).expanduser()
    all_series = load(directory, args.symbols)
    if not all_series:
        print(
            f"No usable CSVs under {directory}. "
            "Run `python tools/season_scan.py fetch` first."
        )
        return 1

    modes = ("traded", "static") if args.mode == "both" else (args.mode,)
    results = [audit(all_series, args.horizon, args.lookback, m) for m in modes]
    for result in results:
        print(render(result))
        print()
    if args.json:
        import json

        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"JSON -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

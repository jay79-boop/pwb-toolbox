#!/usr/bin/env python
"""Does Kronos actually predict anything? Measure it before believing it.

Kronos (https://github.com/shiyu-coder/Kronos) is an open-source foundation
model for candlesticks: it tokenizes OHLCV bars and autoregressively samples
the next ones, the way a language model samples the next word. The demo charts
look impressive. Demo charts always look impressive. This tool exists to
replace the impression with a number.

Three commands:

    fetch      Download bars for a ticker to CSV with yfinance, so the eval
               and forecast steps run offline and repeatably afterward.

    eval       Walk forward through a CSV of bars. At each origin, feed the
               model only the past, ask for the next `horizon` bars, and score
               the forecast against what actually happened. Reports direction
               hit rate with an exact p-value against coin-flipping, the
               information coefficient (correlation of predicted vs realized
               returns), and path error relative to a "price stays where it
               is" persistence baseline. Windows do not overlap by default,
               so the p-value is not flattered by counting the same move
               twice.

    forecast   The discretionary aid: predict the next N bars from the end of
               the data and print them, optionally with a chart. Run eval
               first; a forecast from a model with no measured edge is a
               random number generator with good production values.

The model itself (weights from Hugging Face, code cloned from GitHub, torch)
is only loaded by the commands that need it, and everything scoring-related is
a pure function of recorded numbers — so the scorecard math is testable, and
tested, without torch, network, or GPU (tests/test_kronos_lab.py).

Examples::

    python tools/kronos_lab.py fetch BTC-USD --interval 1h --period 720d --out btc.csv
    python tools/kronos_lab.py eval btc.csv --model small --windows 40
    python tools/kronos_lab.py eval btc.csv --dump btc_preds.csv
    python tools/kronos_lab.py forecast btc.csv --horizon 24 --plot btc.png

The first model run downloads ~100 MB of weights from Hugging Face and clones
the Kronos repo to a cache directory (override with --kronos-repo or
KRONOS_REPO). CPU works; a GPU is only about speed.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pandas as pd

KRONOS_GIT_URL = "https://github.com/shiyu-coder/Kronos.git"

# Model name -> (Hugging Face predictor repo, tokenizer repo, max context).
MODELS = {
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
    "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_bars(path: str | Path) -> pd.DataFrame:
    """Read a CSV of bars into the canonical frame: a `timestamps` datetime
    column plus lowercase `open, high, low, close` and optionally `volume`.

    Accepts yfinance exports (Date/Datetime column, capitalized columns) and
    anything already in canonical form. Rows with missing prices are dropped
    rather than interpolated — an invented bar is worse than a shorter series.
    """
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    for alias in ("timestamps", "timestamp", "date", "datetime", "time"):
        if alias in df.columns:
            df = df.rename(columns={alias: "timestamps"})
            break
    else:
        raise ValueError(
            f"{path}: no timestamp column (looked for Date/Datetime/timestamps)"
        )
    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    cols = ["timestamps"] + required + (["volume"] if "volume" in df.columns else [])
    df = df[cols].copy()
    df["timestamps"] = pd.to_datetime(df["timestamps"], utc=True).dt.tz_localize(None)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=required).sort_values("timestamps").reset_index(drop=True)
    if len(df) < 2:
        raise ValueError(f"{path}: only {len(df)} usable bars")
    return df


# ---------------------------------------------------------------------------
# Scoring — pure math, no model
# ---------------------------------------------------------------------------


def binom_two_sided(hits: int, n: int) -> float:
    """Exact two-sided binomial test against p=0.5.

    The probability, if direction calls were coin flips, of an outcome at
    least as extreme as the one observed. Exact integer arithmetic — n here
    is tens of windows, where normal approximations are at their worst.
    """
    if n == 0:
        return 1.0
    observed = math.comb(n, hits)
    total = sum(math.comb(n, k) for k in range(n + 1) if math.comb(n, k) <= observed)
    return float(min(Fraction(total, 2**n), Fraction(1)))


def _ranks(xs: list[float]) -> list[float]:
    """Average ranks (1-based), ties sharing their mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        mean_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return float("nan")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: list[float], ys: list[float]) -> float:
    return pearson(_ranks(xs), _ranks(ys))


@dataclass
class WindowResult:
    """One walk-forward window: what the model said, what the market did."""

    origin: pd.Timestamp  # timestamp of the last bar the model saw
    last_close: float
    pred_close: float  # model's close at the end of the horizon
    real_close: float  # actual close at the end of the horizon
    pred_path_mae: float  # mean |predicted close - actual close| over the path
    naive_path_mae: float  # same for "price stays at last_close"

    @property
    def pred_ret(self) -> float:
        return self.pred_close / self.last_close - 1

    @property
    def real_ret(self) -> float:
        return self.real_close / self.last_close - 1


def evaluate(
    bars, predict_fn, lookback, horizon, step=None, max_windows=None, on_window=None
):
    """Walk forward through `bars`, scoring `predict_fn` at each origin.

    `predict_fn(x_df, x_timestamps, y_timestamps)` gets the lookback bars
    (columns open/high/low/close and volume if present) and must return a
    DataFrame with a `close` column of len(y_timestamps) forecasts. The model
    never sees anything after the origin; the future rows supply only their
    timestamps (which the model would know: bar spacing is the calendar, not
    the market).

    Windows advance by `step` bars (default: `horizon`, i.e. non-overlapping,
    so each realized return is counted once). `max_windows` keeps the most
    recent that fit. Returns a list of WindowResult.
    """
    step = horizon if step is None else step
    if step < 1:
        raise ValueError("step must be >= 1")
    n = len(bars)
    origins = list(range(lookback - 1, n - horizon, step))
    if not origins:
        raise ValueError(
            f"not enough bars: have {n}, need at least lookback+horizon = {lookback + horizon}"
        )
    if max_windows is not None:
        origins = origins[-max_windows:]

    feature_cols = [
        c for c in ("open", "high", "low", "close", "volume") if c in bars.columns
    ]
    results = []
    for count, t in enumerate(origins):
        x = bars.iloc[t - lookback + 1 : t + 1]
        y = bars.iloc[t + 1 : t + 1 + horizon]
        pred = predict_fn(
            x[feature_cols].reset_index(drop=True),
            x["timestamps"].reset_index(drop=True),
            y["timestamps"].reset_index(drop=True),
        )
        pred_closes = [float(v) for v in pred["close"]]
        real_closes = [float(v) for v in y["close"]]
        last_close = float(x["close"].iloc[-1])
        results.append(
            WindowResult(
                origin=x["timestamps"].iloc[-1],
                last_close=last_close,
                pred_close=pred_closes[-1],
                real_close=real_closes[-1],
                pred_path_mae=sum(abs(p - r) for p, r in zip(pred_closes, real_closes))
                / horizon,
                naive_path_mae=sum(abs(last_close - r) for r in real_closes) / horizon,
            )
        )
        if on_window:
            on_window(count + 1, len(origins))
    return results


def scorecard(results: list[WindowResult]) -> dict:
    """Reduce window results to the numbers that decide whether this is signal."""
    decided = [r for r in results if r.pred_ret != 0 and r.real_ret != 0]
    hits = sum(1 for r in decided if (r.pred_ret > 0) == (r.real_ret > 0))
    pred_rets = [r.pred_ret for r in results]
    real_rets = [r.real_ret for r in results]
    naive_mae = sum(r.naive_path_mae for r in results)
    return {
        "windows": len(results),
        "decided": len(decided),
        "hits": hits,
        "hit_rate": hits / len(decided) if decided else float("nan"),
        "p_value": binom_two_sided(hits, len(decided)),
        "ic_pearson": pearson(pred_rets, real_rets),
        "ic_spearman": spearman(pred_rets, real_rets),
        "mase": (
            sum(r.pred_path_mae for r in results) / naive_mae
            if naive_mae > 0
            else float("nan")
        ),
        "up_calls": sum(1 for r in results if r.pred_ret > 0),
    }


def render_scorecard(card: dict, label: str = "") -> str:
    """The scorecard as text, with the verdict spelled out rather than implied."""

    def fmt(v, spec=".3f"):
        return "n/a" if v != v else format(v, spec)  # NaN-safe

    lines = []
    if label:
        lines.append(label)
    lines += [
        f"  windows scored          {card['windows']}  ({card['decided']} with a decidable direction)",
        f"  direction hit rate      {fmt(card['hit_rate'], '.1%')}  ({card['hits']}/{card['decided']}, p={fmt(card['p_value'])} vs coin flip)",
        f"  info coefficient        {fmt(card['ic_pearson'])} pearson / {fmt(card['ic_spearman'])} spearman",
        f"  path error vs 'no move' {fmt(card['mase'])}  (<1 beats persistence)",
        f"  bullish calls           {card['up_calls']}/{card['windows']}",
    ]
    p, ic = card["p_value"], card["ic_pearson"]
    if card["decided"] < 20:
        verdict = "Too few windows to conclude anything — get more data before trusting either outcome."
    elif p < 0.05 and ic == ic and ic > 0:
        verdict = (
            "Direction calls beat coin-flipping on this sample. Worth a deeper look "
            "(other tickers, other periods) before any capital decision."
        )
    else:
        verdict = (
            "Indistinguishable from noise on this sample. That is the expected result "
            "for a zero-shot model on liquid markets; don't trade its forecasts."
        )
    lines.append(f"  verdict: {verdict}")
    return "\n".join(lines)


def results_frame(results: list[WindowResult]) -> pd.DataFrame:
    """Per-window predictions as a DataFrame — the raw material for feeding
    Kronos into the cross-instrument backtest lab as a signal series later."""
    return pd.DataFrame(
        {
            "origin": [r.origin for r in results],
            "last_close": [r.last_close for r in results],
            "pred_close": [r.pred_close for r in results],
            "real_close": [r.real_close for r in results],
            "pred_ret": [r.pred_ret for r in results],
            "real_ret": [r.real_ret for r in results],
        }
    )


# ---------------------------------------------------------------------------
# The model itself — everything below needs torch, network, or both
# ---------------------------------------------------------------------------


def resolve_kronos_repo(explicit: str | None) -> Path:
    """Find (or clone) the Kronos source tree; its `model` package is not on
    PyPI, so the code comes straight from the repo."""
    candidates = [explicit, os.environ.get("KRONOS_REPO")]
    for c in candidates:
        if c:
            path = Path(c).expanduser()
            if (path / "model" / "kronos.py").exists():
                return path
            raise SystemExit(
                f"{path} does not look like a Kronos checkout (no model/kronos.py)"
            )
    cache = Path.home() / ".cache" / "kronos-lab" / "Kronos"
    if not (cache / "model" / "kronos.py").exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        print(f"Cloning Kronos to {cache} ...", flush=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", KRONOS_GIT_URL, str(cache)],
            check=True,
        )
    return cache


def make_predict_fn(
    model_name, kronos_repo=None, device=None, temperature=1.0, top_p=0.9, samples=3
):
    """Load Kronos and return a `predict_fn(x_df, x_ts, y_ts) -> DataFrame`
    matching what `evaluate` expects. Downloads weights on first use."""
    if model_name not in MODELS:
        raise SystemExit(f"unknown model {model_name!r}; choose from {sorted(MODELS)}")
    repo = resolve_kronos_repo(kronos_repo)
    sys.path.insert(0, str(repo))
    try:
        from model import Kronos, KronosTokenizer, KronosPredictor
    except ImportError as exc:
        raise SystemExit(
            f"could not import Kronos from {repo}: {exc}\n"
            "Its dependencies (torch, einops, huggingface_hub, safetensors) must be "
            "installed: pip install torch einops huggingface_hub safetensors"
        )
    predictor_repo, tokenizer_repo, max_context = MODELS[model_name]
    print(f"Loading {predictor_repo} (first run downloads the weights) ...", flush=True)
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_repo)
    model = Kronos.from_pretrained(predictor_repo)
    predictor = KronosPredictor(
        model, tokenizer, device=device, max_context=max_context
    )
    print(f"Model on {predictor.device}.", flush=True)

    def predict_fn(x_df, x_ts, y_ts):
        return predictor.predict(
            df=x_df,
            x_timestamp=x_ts,
            y_timestamp=y_ts,
            pred_len=len(y_ts),
            T=temperature,
            top_p=top_p,
            sample_count=samples,
            verbose=False,
        )

    return predict_fn


def fetch_bars(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """Download bars with yfinance into the canonical frame."""
    import yfinance as yf

    raw = yf.download(
        ticker, interval=interval, period=period, progress=False, auto_adjust=False
    )
    if raw is None or raw.empty:
        raise SystemExit(
            f"yfinance returned nothing for {ticker!r} ({interval}, {period})"
        )
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df = df.rename(columns={"date": "timestamps", "datetime": "timestamps"})
    keep = ["timestamps", "open", "high", "low", "close"] + (
        ["volume"] if "volume" in df.columns else []
    )
    df = df[keep]
    df["timestamps"] = pd.to_datetime(df["timestamps"], utc=True).dt.tz_localize(None)
    return df.dropna().reset_index(drop=True)


def load_or_fetch(source: str, interval: str, period: str) -> pd.DataFrame:
    """A path that exists is a CSV; anything else is treated as a ticker."""
    if Path(source).exists():
        return load_bars(source)
    print(
        f"{source!r} is not a file — fetching it as a ticker via yfinance.", flush=True
    )
    return fetch_bars(source, interval, period)


def future_timestamps(bars: pd.DataFrame, horizon: int) -> pd.Series:
    """Extend the series' median bar spacing past its last timestamp.

    Honest caveat: for session-bound markets this marches through nights and
    weekends as if they traded. Fine for crypto; for equity intraday bars the
    timestamps drift, though Kronos only reads coarse calendar features from
    them so the forecast values are affected far less than the labels.
    """
    ts = bars["timestamps"]
    delta = ts.diff().median()
    last = ts.iloc[-1]
    return pd.Series([last + delta * (i + 1) for i in range(horizon)])


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_fetch(args):
    df = fetch_bars(args.ticker, args.interval, args.period)
    out = args.out or f"{args.ticker.replace('/', '-')}_{args.interval}.csv"
    df.to_csv(out, index=False)
    print(
        f"{len(df)} bars ({df['timestamps'].iloc[0]} .. {df['timestamps'].iloc[-1]}) -> {out}"
    )


def cmd_eval(args):
    bars = load_or_fetch(args.source, args.interval, args.period)
    predict_fn = make_predict_fn(
        args.model,
        kronos_repo=args.kronos_repo,
        device=args.device,
        temperature=args.temperature,
        top_p=args.top_p,
        samples=args.samples,
    )

    def progress(done, total):
        print(
            f"\r  window {done}/{total}", end="" if done < total else "\n", flush=True
        )

    results = evaluate(
        bars,
        predict_fn,
        lookback=args.lookback,
        horizon=args.horizon,
        step=args.step,
        max_windows=args.windows,
        on_window=progress,
    )
    if args.dump:
        results_frame(results).to_csv(args.dump, index=False)
        print(f"Per-window predictions -> {args.dump}")
    label = (
        f"Kronos-{args.model} on {args.source}: {args.horizon}-bar horizon, "
        f"{args.lookback}-bar lookback"
    )
    print(render_scorecard(scorecard(results), label))


def cmd_forecast(args):
    bars = load_or_fetch(args.source, args.interval, args.period)
    if len(bars) < args.lookback:
        raise SystemExit(f"need {args.lookback} bars, have {len(bars)}")
    predict_fn = make_predict_fn(
        args.model,
        kronos_repo=args.kronos_repo,
        device=args.device,
        temperature=args.temperature,
        top_p=args.top_p,
        samples=args.samples,
    )
    feature_cols = [
        c for c in ("open", "high", "low", "close", "volume") if c in bars.columns
    ]
    x = bars.iloc[-args.lookback :]
    y_ts = future_timestamps(bars, args.horizon)
    pred = predict_fn(
        x[feature_cols].reset_index(drop=True),
        x["timestamps"].reset_index(drop=True),
        y_ts,
    )
    last_close = float(x["close"].iloc[-1])
    print(f"\nLast close {last_close:.4f} at {x['timestamps'].iloc[-1]}")
    print(
        f"{'timestamp':<20} {'open':>10} {'high':>10} {'low':>10} {'close':>10} {'vs last':>8}"
    )
    for ts, row in zip(y_ts, pred.itertuples(index=False)):
        chg = row.close / last_close - 1
        print(
            f"{str(ts):<20} {row.open:>10.4f} {row.high:>10.4f} {row.low:>10.4f} "
            f"{row.close:>10.4f} {chg:>+7.2%}"
        )
    end_chg = float(pred["close"].iloc[-1]) / last_close - 1
    print(f"\nHorizon-end call: {end_chg:+.2%} over {args.horizon} bars.")
    print(
        "This is a sampled guess, not a measurement — see `eval` for whether it earns trust."
    )
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(x["timestamps"], x["close"], label="history", linewidth=1.2)
        ax.plot(
            y_ts, pred["close"], label="Kronos forecast", linewidth=1.2, linestyle="--"
        )
        ax.legend()
        ax.set_title(f"Kronos-{args.model}: {args.source} +{args.horizon} bars")
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Chart -> {args.plot}")


def _add_model_args(p):
    p.add_argument(
        "--model", default="small", choices=sorted(MODELS), help="Kronos size"
    )
    p.add_argument(
        "--kronos-repo",
        help="existing Kronos checkout (else $KRONOS_REPO or auto-clone)",
    )
    p.add_argument("--device", help="cpu / cuda:0 / mps (default: auto-detect)")
    p.add_argument(
        "--temperature", type=float, default=1.0, help="sampling temperature"
    )
    p.add_argument(
        "--top-p", type=float, default=0.9, help="nucleus sampling threshold"
    )
    p.add_argument(
        "--samples", type=int, default=3, help="forecast paths averaged per window"
    )


def _add_source_args(p):
    p.add_argument("source", help="CSV of bars, or a ticker to fetch live via yfinance")
    p.add_argument(
        "--interval", default="1h", help="bar interval when fetching (default 1h)"
    )
    p.add_argument(
        "--period", default="720d", help="history span when fetching (default 720d)"
    )
    p.add_argument(
        "--lookback", type=int, default=240, help="bars of context per forecast"
    )
    p.add_argument(
        "--horizon", type=int, default=12, help="bars to predict per forecast"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="kronos_lab",
        description="Measure the Kronos K-line foundation model before trusting it.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download bars to CSV via yfinance")
    p.add_argument("ticker")
    p.add_argument("--interval", default="1h")
    p.add_argument("--period", default="720d")
    p.add_argument("--out", help="output CSV (default <ticker>_<interval>.csv)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("eval", help="walk-forward scorecard on historical bars")
    _add_source_args(p)
    _add_model_args(p)
    p.add_argument("--step", type=int, help="bars between origins (default: horizon)")
    p.add_argument(
        "--windows", type=int, default=60, help="most recent windows to score"
    )
    p.add_argument("--dump", help="write per-window predictions to this CSV")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser(
        "forecast", help="predict the next bars from the end of the data"
    )
    _add_source_args(p)
    _add_model_args(p)
    p.add_argument("--plot", help="save a history+forecast chart to this PNG")
    p.set_defaults(func=cmd_forecast)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

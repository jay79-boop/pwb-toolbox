#!/usr/bin/env python
"""The night lab: unattended trade stress testing between 1am and 8am.

This is the "good night" command. It grinds through a queue of stress jobs
while the machine is idle, yields the moment you touch the keyboard, and
leaves a verdict waiting at breakfast.

The design rule, and the reason this is worth building at all:

    **The model proposes. Python computes.**

A local LLM cannot calculate a drawdown, and asking it to produces a fluent
number that is wrong and unfalsifiable — seven hours of confident fiction.
So the model is used for the one thing it is genuinely good at and that
scales with cheap overnight compute: generating *hypotheses* at volume.
Every hypothesis is then parsed into a structured spec, clamped to sane
bounds, and executed by deterministic arithmetic over the real record.
A proposal that will not parse, or that names no checkable condition, is
discarded rather than repaired — a dropped job is a fine outcome, an
unverifiable finding is not.

The four job kinds, and who does what in each:

    redteam    model attacks an open thesis; kept only if the attack names
               a falsifiable condition (symbol, operator, level, deadline)
               you could check tomorrow. Vibes are dropped.
    shock      model authors a shock *spec* (gap, stop-slip, slippage,
               correlation, resampling); `apply_shock` computes every
               resulting number from your actual closed trades.
    fragility  pure math, no model involved — sweeps a parameter and
               reports how far the metric falls one step either side. A
               setting that is the lone peak of its own sweep is fitted to
               history, not to the market.
    leaks      model proposes a pattern in the closed record ("short-dte
               loses on Fridays"); `verify_pattern` tests it against the
               real trades and reports the effect size and n. Claims
               without support are dropped.

Scheduling is deliberately dumb and interruptible. Jobs are small and the
queue is checkpointed after every one, so being interrupted costs at most
the job in flight. `next_action` decides run/yield/stop from the clock and
the idle timer alone — it is a pure function, so the whole policy is
testable without waiting for 3am.

Nothing here needs the network beyond localhost: Ollama serves on
127.0.0.1:11434 and the HTTP call is injectable, so the tests run offline
against a fake. Data lands in night_lab/, which is gitignored — trade
records are personal and this fork is public.

Examples::

    python tools/night_lab.py plan                 # build tonight's queue
    python tools/night_lab.py run                  # the overnight grind
    python tools/night_lab.py run --once           # one job, for a smoke test
    python tools/night_lab.py status               # queue state
    python tools/night_lab.py verdict              # the morning one-screen
    python tools/night_lab.py verdict --quiet      # silent unless something broke
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "night_lab"
QUEUE_NAME = "queue.jsonl"
VERDICT_NAME = "verdict.json"
PROPOSALS_NAME = "proposals.jsonl"
# Picked up by `plan` automatically when it exists and no --sim was passed.
# The "good night" agent runs a bare `plan`; without this convention that
# bare plan would silently rebuild the record without the sim trades the
# owner armed earlier -- dropping every shock and leak job on the floor.
DEFAULT_SIM_NAME = "sim_trades.json"
# Any file matching this glob in the lab dir feeds fragility jobs -- the
# reversal sim's --fragility-out writes them, one file per instrument, and a
# bare `plan` (what the "good night" agent runs) sweeps them all in.
FRAGILITY_GLOB = "fragility*.json"
# The closed record `plan` snapshotted for tonight. `run` reads this rather
# than re-deriving the record at 1am, so the night computes on exactly the
# state that was armed -- including sim trades merged in via --sim, which the
# desk ledger alone would not carry.
RECORD_NAME = "record.json"

# The window. Defaults match the ask: start at 1am, hard stop at 8am.
START_HOUR = 1
END_HOUR = 8
# How long the keyboard and mouse must be quiet before the lab considers the
# machine its own. Two minutes is long enough not to fight a pause in typing
# and short enough that stepping away is noticed within one job.
IDLE_THRESHOLD_S = 120

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_HOST = "http://127.0.0.1:11434"

RUN, YIELD, STOP = "run", "yield", "stop"

JOB_KINDS = ("redteam", "shock", "fragility", "leaks")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Scheduling policy — pure, so 3am behaviour is testable at 3pm
# ---------------------------------------------------------------------------


def in_window(
    hour: int, start_hour: int = START_HOUR, end_hour: int = END_HOUR
) -> bool:
    """Is `hour` inside the nightly window? Handles windows that wrap midnight."""
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def next_action(
    hour: int,
    idle_seconds: float | None,
    *,
    start_hour: int = START_HOUR,
    end_hour: int = END_HOUR,
    idle_threshold: float = IDLE_THRESHOLD_S,
) -> str:
    """Decide what the runner should do right now.

    STOP when the window has closed — the night is over, whatever is left
    stays queued for tomorrow. YIELD when the machine is in use, so the
    stress test never competes with the person who owns the keyboard.
    RUN otherwise.

    `idle_seconds` of None means "cannot tell" (not Windows, or the API
    failed). That is treated as idle: on a machine where idle cannot be
    measured, refusing to ever run would be the worse failure.
    """
    if not in_window(hour, start_hour, end_hour):
        return STOP
    if idle_seconds is not None and idle_seconds < idle_threshold:
        return YIELD
    return RUN


def windows_idle_seconds():
    """Seconds since the last keyboard or mouse input, or None if unknowable.

    Uses GetLastInputInfo, which is the only reading that reflects real user
    presence — CPU load does not, and a busy machine with nobody at it is
    exactly when the lab should be working.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return None
    if not hasattr(ctypes, "windll"):
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0.0, millis / 1000.0)


# ---------------------------------------------------------------------------
# Queue — JSONL, checkpointed after every job so an interrupt costs one job
# ---------------------------------------------------------------------------


def make_job(kind: str, payload: dict, job_id: str) -> dict:
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    return {
        "id": job_id,
        "kind": kind,
        "payload": payload,
        "status": "pending",
        "result": None,
        "queued": now_iso(),
    }


def read_queue(path: Path) -> list[dict]:
    if not path.exists():
        return []
    jobs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            jobs.append(json.loads(line))
    return jobs


def write_queue(path: Path, jobs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs)
    path.write_text(body, encoding="utf-8")


def pending(jobs: list[dict]) -> list[dict]:
    return [j for j in jobs if j["status"] == "pending"]


# ---------------------------------------------------------------------------
# Ollama — localhost only, injectable transport so tests never need a model
# ---------------------------------------------------------------------------


class Ollama:
    """Minimal Ollama client. stdlib only; `post` is injectable for tests."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        post=None,
        timeout: float = 300.0,
        keep_alive: str = "0",
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        # keep_alive "0" unloads the model as soon as the call returns, so a
        # yield hands the GPU straight back instead of holding VRAM all night.
        self.keep_alive = keep_alive
        self._post = post or self._http_post

    def _http_post(self, url: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Ask for one JSON answer. Returns raw text; parsing is the caller's."""
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.8},
        }
        if system:
            body["system"] = system
        out = self._post(f"{self.host}/api/generate", body)
        return out.get("response", "")


def parse_json_block(text: str):
    """Pull a JSON object out of model output, tolerating the usual mess.

    Local models wrap JSON in prose or fences even when asked not to. This
    tries the whole string, then a fenced block, then the outermost braces.
    Returns None rather than raising: an unparseable answer is a dropped
    job, which is the correct outcome and not an error worth crashing on.
    """
    if not text:
        return None
    for candidate in _json_candidates(text):
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, (dict, list)):
            return obj
    return None


def _json_candidates(text: str):
    yield text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        yield fenced.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            yield text[start : end + 1]


# ---------------------------------------------------------------------------
# redteam — an attack is only kept if you could check it tomorrow
# ---------------------------------------------------------------------------

VALID_OPS = (">=", "<=", ">", "<")

# Macro levels are legitimate attacks on a single-name thesis — "this only
# works while SPY holds up" is real reasoning. Anything outside this set and
# the trade's own ticker is the model drifting to a different instrument,
# which is the one failure mode that would put a wrong symbol in front of you
# at breakfast.
MACRO_SYMBOLS = frozenset(
    {"SPY", "SPX", "ES", "QQQ", "NDX", "IWM", "VIX", "DXY", "TLT", "BTC-USD"}
)


def validate_attack(obj, expect_symbol: str | None = None) -> dict | None:
    """Keep an attack only when it names a falsifiable condition.

    "The trade could go against you" is not an attack, it is a description
    of trading. What earns a place in the morning report is a condition with
    a symbol, a comparison, a level and a deadline — something tomorrow's
    price either satisfies or does not.

    `expect_symbol` guards against the failure that matters most here: a
    local model drifting onto a different ticker, which would print a check
    on a stock you do not hold. The falsifier must name the trade's own
    symbol or a recognised macro level, or the attack is dropped.
    """
    if not isinstance(obj, dict):
        return None
    claim = str(obj.get("claim", "")).strip()
    falsifier = obj.get("falsifier")
    if not claim or not isinstance(falsifier, dict):
        return None
    symbol = str(falsifier.get("symbol", "")).strip().upper()
    op = str(falsifier.get("op", "")).strip()
    by = str(falsifier.get("by", "")).strip()
    if not symbol or op not in VALID_OPS or not by:
        return None
    if expect_symbol:
        want = expect_symbol.strip().upper()
        if symbol != want and symbol not in MACRO_SYMBOLS:
            return None
    try:
        level = float(falsifier.get("level"))
    except (TypeError, ValueError):
        return None
    if level <= 0:
        return None
    severity = str(obj.get("severity", "medium")).strip().lower()
    if severity not in ("low", "medium", "high"):
        severity = "medium"
    return {
        "claim": claim,
        "severity": severity,
        "falsifier": {"symbol": symbol, "op": op, "level": level, "by": by},
    }


def dedupe_attacks(attacks: list[dict]) -> list[dict]:
    """Collapse attacks that test the same condition.

    Volume is the point of running overnight, but fifty rephrasings of one
    idea is not fifty findings. Two attacks are the same when they check the
    same symbol, direction and level — which is a fact about the falsifier,
    not about the wording.
    """
    seen, out = set(), []
    for a in attacks:
        f = a["falsifier"]
        key = (f["symbol"], f["op"], round(f["level"], 4))
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# shock — the model picks the scenario, this arithmetic produces the numbers
# ---------------------------------------------------------------------------

SHOCK_BOUNDS = {
    # An adverse gap through the stop, as a fraction of the stop price.
    "gap_pct": (0.0, 0.30),
    # How much worse losers get when stops slip in a fast tape.
    "loss_mult": (1.0, 5.0),
    # What happens to winners. Defaults to 1.0: a vol spike does not improve
    # your exits, and letting the model widen wins would flatter the result.
    "win_mult": (0.0, 1.5),
    # Round-trip execution cost in basis points of the entry price.
    "slippage_bps": (0.0, 500.0),
}


def validate_shock(obj) -> dict | None:
    """Clamp a proposed scenario into bounds. Out-of-range is clipped, not rejected.

    The model is allowed to be dramatic about *which* stress to apply; it is
    not allowed to invent a 90% gap that would make every strategy look
    equally doomed and the exercise meaningless.
    """
    if not isinstance(obj, dict):
        return None
    name = str(obj.get("name", "")).strip()
    if not name:
        return None
    spec = {"name": name, "rationale": str(obj.get("rationale", "")).strip()}
    for field, (lo, hi) in SHOCK_BOUNDS.items():
        default = 1.0 if field.endswith("_mult") else 0.0
        try:
            value = float(obj.get(field, default))
        except (TypeError, ValueError):
            value = default
        spec[field] = min(hi, max(lo, value))
    spec["corr_to_one"] = bool(obj.get("corr_to_one", False))
    try:
        spec["resample"] = int(obj.get("resample", 0))
    except (TypeError, ValueError):
        spec["resample"] = 0
    spec["resample"] = min(20000, max(0, spec["resample"]))
    return spec


def trade_r(trade: dict) -> float | None:
    """R-multiple of a closed trade: result divided by what it risked."""
    try:
        entry = float(trade["entry"])
        exit_ = float(trade["exit"])
        stop = float(trade["stop"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    sign = -1.0 if str(trade.get("direction", "long")).lower() == "short" else 1.0
    return sign * (exit_ - entry) / risk


def shock_trade_r(trade: dict, spec: dict) -> float | None:
    """One trade's R after the scenario is applied. Every step is arithmetic.

    Order matters and mirrors what actually happens in a bad tape: the stop
    gaps first (you are filled beyond it), then execution costs come off,
    then the surviving result is scaled by how much worse the regime made
    losers relative to winners.
    """
    base = trade_r(trade)
    if base is None:
        return None
    try:
        entry = float(trade["entry"])
        stop = float(trade["stop"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    r = base
    # A losing trade is one the stop was responsible for. In the scenario it
    # fills `gap_pct` beyond the stop instead of at it, which costs a further
    # slice of R proportional to how far the gap runs against the risk.
    if r < 0 and spec.get("gap_pct"):
        r -= (spec["gap_pct"] * stop) / risk
    # Round-trip execution cost, expressed in R.
    if spec.get("slippage_bps"):
        r -= (spec["slippage_bps"] / 10000.0 * entry) / risk
    if r < 0:
        r *= spec.get("loss_mult", 1.0)
    else:
        r *= spec.get("win_mult", 1.0)
    return r


def max_drawdown(series: list[float]) -> float:
    """Deepest peak-to-trough fall of a cumulative path, as a positive number."""
    peak, worst, running = 0.0, 0.0, 0.0
    for value in series:
        running += value
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return abs(worst)


def apply_shock(trades: list[dict], spec: dict, *, ruin_r: float = 10.0) -> dict:
    """Run the scenario over the real closed record and report what happened.

    `ruin_r` is how many R of drawdown empties the pot — 10 R at the desk's
    10%-per-trade cap. Every figure below is computed here, never quoted
    from the model.
    """
    base_rs, shocked_rs = [], []
    for t in trades:
        b, s = trade_r(t), shock_trade_r(t, spec)
        if b is None or s is None:
            continue
        base_rs.append(b)
        shocked_rs.append(s)

    result = {
        "scenario": spec["name"],
        "rationale": spec.get("rationale", ""),
        "n_trades": len(shocked_rs),
        "spec": {k: spec[k] for k in SHOCK_BOUNDS},
    }
    if not shocked_rs:
        result["skipped"] = "no closed trades with a usable entry/stop/exit"
        return result

    result["total_r_before"] = round(sum(base_rs), 3)
    result["total_r_after"] = round(sum(shocked_rs), 3)
    result["max_dd_r_before"] = round(max_drawdown(base_rs), 3)
    result["max_dd_r_after"] = round(max_drawdown(shocked_rs), 3)
    result["worst_trade_r_after"] = round(min(shocked_rs), 3)
    result["ruin_r"] = ruin_r
    # Correlation to one: concurrent positions stop diversifying and their
    # worst losses land together. The stressed sequence is the same trades
    # with the losers stacked, which is the drawdown that actually matters.
    if spec.get("corr_to_one"):
        losers = sorted(r for r in shocked_rs if r < 0)
        winners = [r for r in shocked_rs if r >= 0]
        result["max_dd_r_after"] = round(max_drawdown(losers + winners), 3)
    result["survives"] = bool(result["max_dd_r_after"] < ruin_r)

    if spec.get("resample"):
        result.update(resample_paths(shocked_rs, spec["resample"], ruin_r))
    return result


def resample_paths(rs: list[float], n_paths: int, ruin_r: float, seed: int = 7) -> dict:
    """Bootstrap the trade order to ask how much of the record was sequence luck.

    The same trades in a different order produce a different worst drawdown.
    A strategy that only survives its own historical ordering has not been
    tested, it has been remembered.
    """
    rng = random.Random(seed)
    dds, ruined = [], 0
    for _ in range(n_paths):
        path = [rng.choice(rs) for _ in rs]
        dd = max_drawdown(path)
        dds.append(dd)
        if dd >= ruin_r:
            ruined += 1
    dds.sort()
    idx = min(len(dds) - 1, int(0.95 * len(dds)))
    return {
        "paths": n_paths,
        "dd_median_r": round(statistics.median(dds), 3),
        "dd_p95_r": round(dds[idx], 3),
        "p_ruin": round(ruined / n_paths, 4),
    }


# ---------------------------------------------------------------------------
# fragility — no model involved; a peak that is a spike is a fitted parameter
# ---------------------------------------------------------------------------


def cliff_score(sweep: dict, chosen) -> dict:
    """How much of the metric is lost by being one step off the chosen value.

    A robust parameter sits on a plateau: its neighbours score nearly as
    well. A fitted one is the lone spike of its own sweep, and the drop to
    the next setting along is the tell.
    """
    if not sweep:
        return {"error": "empty sweep"}
    values = sorted(sweep)
    if chosen not in sweep:
        return {"error": f"chosen value {chosen!r} not in sweep"}
    i = values.index(chosen)
    neighbours = [sweep[values[j]] for j in (i - 1, i + 1) if 0 <= j < len(values)]
    here = sweep[chosen]
    ranked = sorted(sweep.values(), reverse=True)
    out = {
        "chosen": chosen,
        "metric": round(here, 4),
        "rank": ranked.index(here) + 1,
        "of": len(values),
        "is_peak": here >= max(sweep.values()),
    }
    if not neighbours:
        out["cliff"] = None
        out["verdict"] = "edge of sweep — widen the grid before trusting it"
        return out
    worst = min(neighbours)
    out["neighbour_worst"] = round(worst, 4)
    denominator = abs(here) if here else 1.0
    out["cliff"] = round((here - worst) / denominator, 4)
    # A lone peak that loses more than a third of its metric one step away is
    # describing the history it was fitted to, not an edge.
    if out["is_peak"] and out["cliff"] > 0.33:
        out["verdict"] = "fitted — lone peak with a steep drop to its neighbour"
    elif out["cliff"] > 0.5:
        out["verdict"] = "fragile — the setting either side is much worse"
    else:
        out["verdict"] = "robust — neighbours score comparably"
    return out


# ---------------------------------------------------------------------------
# leaks — the model proposes a pattern, this tests it against the record
# ---------------------------------------------------------------------------


def _matches(trade: dict, filt: dict) -> bool:
    for key, want in filt.items():
        if str(trade.get(key, "")).lower() != str(want).lower():
            return False
    return True


def verify_pattern(trades: list[dict], pattern, *, min_n: int = 5) -> dict | None:
    """Test a claimed pattern against the closed record.

    The model is good at noticing "these all seem to be short-dte Fridays"
    and bad at knowing whether that is true. So it supplies the filter and
    this supplies the verdict: the group's mean R against everything else,
    the sample size, and whether the claimed direction actually holds.
    Anything under `min_n` trades is dropped — with four trades you can find
    any pattern you like.
    """
    if not isinstance(pattern, dict):
        return None
    filt = pattern.get("filter")
    if not isinstance(filt, dict) or not filt:
        return None
    direction = str(pattern.get("direction", "worse")).strip().lower()
    if direction not in ("worse", "better"):
        return None

    inside, outside = [], []
    for t in trades:
        r = trade_r(t)
        if r is None:
            continue
        (inside if _matches(t, filt) else outside).append(r)

    if len(inside) < min_n:
        return None
    group_mean = statistics.fmean(inside)
    rest_mean = statistics.fmean(outside) if outside else 0.0
    delta = group_mean - rest_mean
    holds = delta < 0 if direction == "worse" else delta > 0
    return {
        "pattern": str(pattern.get("pattern", "")).strip(),
        "filter": filt,
        "direction": direction,
        "n": len(inside),
        "n_rest": len(outside),
        "mean_r": round(group_mean, 3),
        "rest_mean_r": round(rest_mean, 3),
        "delta_r": round(delta, 3),
        "holds": holds,
    }


# ---------------------------------------------------------------------------
# The morning: a verdict that stays quiet when nothing broke
# ---------------------------------------------------------------------------


def build_verdict(results: list[dict]) -> dict:
    """Reduce a night of findings to what deserves a line at breakfast.

    Silence is the good outcome. `broke` gates the whole thing: when nothing
    failed, the morning catch-up says nothing rather than reporting that it
    found nothing, which is noise dressed as diligence.
    """
    lines, broke = [], False
    # Scenarios are collapsed rather than listed. A night of six shocks often
    # breaks in five near-identical ways, and printing each one turns the
    # morning screen into a wall that hides the other findings under it.
    broken = [
        (r.get("result") or {})
        for r in results
        if r.get("kind") == "shock" and (r.get("result") or {}).get("survives") is False
    ]
    if broken:
        broke = True
        worst = max(broken, key=lambda d: d.get("max_dd_r_after", 0))
        also = f" (+{len(broken) - 1} more)" if len(broken) > 1 else ""
        lines.append(
            f"BROKE  worst of {len(broken)}: {worst.get('scenario')} — drawdown "
            f"{worst.get('max_dd_r_after')}R vs {worst.get('ruin_r')}R pot{also}"
        )
    riskiest = max(
        (
            (r.get("result") or {})
            for r in results
            if r.get("kind") == "shock"
            and (r.get("result") or {}).get("survives") is not False
        ),
        key=lambda d: d.get("p_ruin", 0),
        default={},
    )
    if riskiest.get("p_ruin", 0) >= 0.05:
        broke = True
        lines.append(
            f"RISK   {riskiest.get('scenario')}: {riskiest['p_ruin']:.0%} of "
            f"resampled orderings empty the pot"
        )
    for r in results:
        kind = r.get("kind")
        data = r.get("result") or {}
        if kind == "redteam":
            high = [a for a in data.get("attacks", []) if a.get("severity") == "high"]
            if high:
                broke = True
                subject = data.get("subject", "a thesis")
                lines.append(
                    f"THESIS {subject}: {len(high)} falsifiable high-severity attack(s)"
                )
                for a in high[:3]:
                    f = a["falsifier"]
                    lines.append(
                        f"       check {f['symbol']} {f['op']} {f['level']} by {f['by']}"
                    )
        elif kind == "fragility":
            if "fitted" in str(data.get("verdict", "")) or "fragile" in str(
                data.get("verdict", "")
            ):
                broke = True
                lines.append(
                    f"FRAGILE {data.get('param', 'parameter')}={data.get('chosen')}: "
                    f"{data.get('verdict')}"
                )
        elif kind == "leaks":
            for p in data.get("patterns", []):
                if p.get("holds"):
                    broke = True
                    lines.append(
                        f"LEAK   {p['pattern']} (n={p['n']}, "
                        f"{p['delta_r']:+}R vs the rest)"
                    )
    return {
        "generated": now_iso(),
        "jobs": len(results),
        "broke": broke,
        "lines": lines,
    }


def render_report(results: list[dict], verdict: dict) -> str:
    """The full write-up, for when you want to know why something was flagged."""
    out = [
        f"# Night lab — {verdict['generated'][:10]}",
        "",
        f"{verdict['jobs']} job(s) completed. "
        + ("Findings below." if verdict["broke"] else "Nothing broke."),
        "",
    ]
    if verdict["lines"]:
        out += ["## Verdict", ""] + [f"- {line}" for line in verdict["lines"]] + [""]
    out += ["## Detail", ""]
    for r in results:
        out.append(f"### {r.get('kind')} — {r.get('id')}")
        out.append("")
        out.append("```json")
        out.append(json.dumps(r.get("result"), indent=2, ensure_ascii=False))
        out.append("```")
        out.append("")
    return "\n".join(out)


def proposals_from(results: list[dict]) -> list[dict]:
    """Findings that want a rule change, staged for approval — never applied.

    Same contract as docs/trading-wisdom.md: the system drafts, the owner
    approves. Nothing in night_lab/ changes how anything trades on its own.
    """
    out = []
    for r in results:
        data = r.get("result") or {}
        if r.get("kind") == "leaks":
            for p in data.get("patterns", []):
                if p.get("holds") and p["n"] >= 10:
                    out.append(
                        {
                            "raised": now_iso(),
                            "source": r.get("id"),
                            "kind": "rule",
                            "status": "pending",
                            "proposal": (
                                f"{p['pattern']} — {p['n']} trades run "
                                f"{p['delta_r']:+}R against the rest. "
                                f"Consider a rule excluding {p['filter']}."
                            ),
                        }
                    )
    # One sizing proposal per night, not one per scenario. Six shocks that all
    # say "risk less" are one finding; six pending items would make the queue
    # look like six decisions.
    broken = [
        (r.get("id"), r.get("result") or {})
        for r in results
        if r.get("kind") == "shock" and (r.get("result") or {}).get("survives") is False
    ]
    if broken:
        source, data = max(broken, key=lambda pair: pair[1].get("max_dd_r_after", 0))
        also = f" ({len(broken)} scenarios broke)" if len(broken) > 1 else ""
        out.append(
            {
                "raised": now_iso(),
                "source": source,
                "kind": "sizing",
                "status": "pending",
                "proposal": (
                    f"Worst scenario '{data.get('scenario')}' empties the pot "
                    f"({data.get('max_dd_r_after')}R drawdown vs "
                    f"{data.get('ruin_r')}R){also}. Consider cutting per-trade risk."
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Prompts — the model's whole job is to propose in a shape Python can check
# ---------------------------------------------------------------------------

SYSTEM = (
    "You are a risk analyst stress-testing a trader's positions overnight. "
    "You never estimate outcomes, prices, or probabilities — a separate "
    "engine computes every number from the real record. Your job is to "
    "propose specific, checkable hypotheses. Reply with JSON only."
)

REDTEAM_PROMPT = """Attack this trade thesis. Find ways it is wrong.

Thesis: {thesis}
Instrument: {instrument}   Symbol: {symbol}
Entry: {entry}   Stop: {stop}   Target: {target}

Give {n} distinct attacks. Each must name a condition that tomorrow's market
either satisfies or does not — a symbol, a comparison, a price level, and a
deadline. An attack with no checkable condition is worthless; do not include one.

Reply with JSON: {{"attacks": [{{"claim": "...", "severity": "low|medium|high",
"falsifier": {{"symbol": "XYZ", "op": ">=", "level": 123.45, "by": "2026-08-29"}}}}]}}
"""

SHOCK_PROMPT = """Propose a market stress scenario for this trading record.

Lanes traded: {lanes}
Closed trades: {n}
Already tested tonight: {seen}

Propose one scenario that is different from those, plausible, and specific.
You choose the scenario; the engine computes the damage.

Fields: gap_pct (0-0.30, adverse gap through the stop), loss_mult (1-5, how
much worse losers get when stops slip), win_mult (0-1.5, what happens to
winners — 1.0 unless you can justify otherwise), slippage_bps (0-500),
corr_to_one (true if positions stop diversifying), resample (0 or 2000).

Reply with JSON: {{"name": "...", "rationale": "...", "gap_pct": 0.0,
"loss_mult": 1.0, "win_mult": 1.0, "slippage_bps": 0, "corr_to_one": false,
"resample": 2000}}
"""

LEAKS_PROMPT = """Here is a summary of closed trades. Propose patterns that
might be costing money — a group that loses more than the rest.

{summary}

Propose {n} candidate patterns as filters over the trade fields shown. Do not
state whether they are true; the engine tests each against the full record.

Reply with JSON: {{"patterns": [{{"pattern": "short-dte trades lose more",
"filter": {{"lane": "short-dte"}}, "direction": "worse"}}]}}
"""


# ---------------------------------------------------------------------------
# Job execution — dispatch, then hand every number to the deterministic side
# ---------------------------------------------------------------------------


def run_redteam(job: dict, llm: Ollama) -> dict:
    p = job["payload"]
    raw = llm.generate(
        REDTEAM_PROMPT.format(
            thesis=p.get("thesis", ""),
            instrument=p.get("instrument", ""),
            symbol=p.get("symbol", ""),
            entry=p.get("entry", ""),
            stop=p.get("stop", ""),
            target=p.get("target", ""),
            n=p.get("n", 8),
        ),
        system=SYSTEM,
    )
    obj = parse_json_block(raw) or {}
    proposed = obj.get("attacks", []) if isinstance(obj, dict) else []
    want = str(p.get("symbol", "")).strip() or None
    kept = [a for a in (validate_attack(x, expect_symbol=want) for x in proposed) if a]
    kept = dedupe_attacks(kept)
    return {
        "subject": p.get("instrument") or p.get("symbol") or "thesis",
        "proposed": len(proposed),
        "kept": len(kept),
        "attacks": kept,
    }


def run_shock(job: dict, llm: Ollama, trades: list[dict]) -> dict:
    p = job["payload"]
    raw = llm.generate(
        SHOCK_PROMPT.format(
            lanes=", ".join(sorted({str(t.get("lane", "?")) for t in trades})) or "n/a",
            n=len(trades),
            seen=", ".join(p.get("seen", [])) or "nothing yet",
        ),
        system=SYSTEM,
    )
    spec = validate_shock(parse_json_block(raw))
    if spec is None:
        return {"skipped": "model proposed no usable scenario"}
    return apply_shock(trades, spec, ruin_r=float(p.get("ruin_r", 10.0)))


def run_fragility(job: dict) -> dict:
    """No model involved. The sweep is supplied; the verdict is arithmetic."""
    p = job["payload"]
    sweep = p.get("sweep") or {}
    if not isinstance(sweep, dict):
        return {
            "param": p.get("param", "parameter"),
            "error": "sweep must be an object",
        }
    # JSON object keys are strings; restore numeric keys so ordering is numeric.
    restored = {}
    for k, v in sweep.items():
        try:
            restored[float(k)] = float(v)
        except (TypeError, ValueError):
            restored[k] = v
    chosen = p.get("chosen")
    try:
        chosen = float(chosen)
    except (TypeError, ValueError):
        pass
    out = cliff_score(restored, chosen)
    out["param"] = p.get("param", "parameter")
    return out


def run_leaks(job: dict, llm: Ollama, trades: list[dict]) -> dict:
    p = job["payload"]
    raw = llm.generate(
        LEAKS_PROMPT.format(summary=summarize_trades(trades), n=p.get("n", 6)),
        system=SYSTEM,
    )
    obj = parse_json_block(raw) or {}
    proposed = obj.get("patterns", []) if isinstance(obj, dict) else []
    verified = [
        v
        for v in (
            verify_pattern(trades, x, min_n=int(p.get("min_n", 5))) for x in proposed
        )
        if v
    ]
    return {
        "proposed": len(proposed),
        "verified": len(verified),
        "patterns": verified,
    }


def summarize_trades(trades: list[dict], limit: int = 60) -> str:
    """A compact record for the model to look at — fields only, no conclusions."""
    rows = []
    for t in trades[:limit]:
        r = trade_r(t)
        rows.append(
            f"- lane={t.get('lane')} symbol={t.get('symbol')} "
            f"direction={t.get('direction', 'long')} "
            f"opened={str(t.get('opened', ''))[:10]} R={'n/a' if r is None else round(r, 2)}"
        )
    return "\n".join(rows) or "(no closed trades)"


def run_job(job: dict, llm: Ollama, trades: list[dict]) -> dict:
    kind = job["kind"]
    if kind == "redteam":
        return run_redteam(job, llm)
    if kind == "shock":
        return run_shock(job, llm, trades)
    if kind == "fragility":
        return run_fragility(job)
    if kind == "leaks":
        return run_leaks(job, llm, trades)
    raise ValueError(f"unknown job kind: {kind}")


# ---------------------------------------------------------------------------
# The runner — checkpoint after every job, so an interrupt costs at most one
# ---------------------------------------------------------------------------


def run_night(
    jobs: list[dict],
    llm: Ollama,
    trades: list[dict],
    *,
    on_checkpoint=None,
    now_fn=None,
    idle_fn=None,
    sleep_fn=None,
    start_hour: int = START_HOUR,
    end_hour: int = END_HOUR,
    idle_threshold: float = IDLE_THRESHOLD_S,
    poll_s: float = 60.0,
    max_yields: int | None = None,
    limit: int | None = None,
) -> dict:
    """Grind the queue until the window closes or the work runs out.

    Everything the loop depends on — the clock, the idle timer, sleeping — is
    injectable, so the yield-and-resume behaviour is tested in milliseconds
    rather than observed at 3am and hoped about.
    """
    now_fn = now_fn or (lambda: datetime.now())
    idle_fn = idle_fn or windows_idle_seconds
    if sleep_fn is None:
        import time as _time

        sleep_fn = _time.sleep

    done, yields, stopped = 0, 0, None
    while True:
        todo = pending(jobs)
        if not todo:
            stopped = "queue empty"
            break
        if limit is not None and done >= limit:
            stopped = "limit reached"
            break

        action = next_action(
            now_fn().hour,
            idle_fn(),
            start_hour=start_hour,
            end_hour=end_hour,
            idle_threshold=idle_threshold,
        )
        if action == STOP:
            stopped = "window closed"
            break
        if action == YIELD:
            yields += 1
            if max_yields is not None and yields > max_yields:
                stopped = "yield limit reached"
                break
            sleep_fn(poll_s)
            continue

        job = todo[0]
        try:
            result = run_job(job, llm, trades)
            job["result"] = result
            # A job that reported an error produced no usable finding, whether
            # it raised or returned the error politely. Status has to reflect
            # that or `status` stops being worth reading in the morning.
            job["status"] = (
                "failed" if isinstance(result, dict) and "error" in result else "done"
            )
        except Exception as exc:  # noqa: BLE001 - see below
            # Deliberately broad. This runs unattended at 3am with nobody to
            # restart it, so any escaping exception costs every remaining hour
            # of the window, not one job. A failed job is recorded and skipped
            # -- never retried into a loop that would burn the night on one
            # broken payload. KeyboardInterrupt and SystemExit are not
            # Exception subclasses and still stop the run, which is correct.
            job["result"] = {"error": f"{type(exc).__name__}: {exc}"}
            job["status"] = "failed"
        job["finished"] = now_iso()
        done += 1
        if on_checkpoint:
            on_checkpoint(jobs)

    return {
        "completed": done,
        "yields": yields,
        "stopped_because": stopped,
        "remaining": len(pending(jobs)),
    }


# ---------------------------------------------------------------------------
# Building tonight's queue from what the desk is actually holding
# ---------------------------------------------------------------------------


def load_desk(path: Path) -> dict:
    if not path.exists():
        return {"trades": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"trades": []}


def closed_trades(ledger: dict) -> list[dict]:
    return [t for t in ledger.get("trades", []) if t.get("status") == "closed"]


def load_sim_trades(path: Path) -> list[dict]:
    """Closed trades exported by a simulator (reversal_15m_sim --trades-out).

    Accepts {"trades": [...]} or a bare list. Anything that is not a closed
    trade with the fields the arithmetic needs is dropped here, at the door,
    so a malformed export cannot poison the night's record.
    """
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    trades = obj.get("trades", obj) if isinstance(obj, dict) else obj
    if not isinstance(trades, list):
        return []
    kept = []
    for t in trades:
        if not isinstance(t, dict) or t.get("status") != "closed":
            continue
        if trade_r(t) is None:
            continue
        kept.append(t)
    return kept


def build_queue(ledger: dict, *, shocks: int = 6, attacks_per: int = 8) -> list[dict]:
    """Tonight's work: attack what is open, shock what is closed, mine the record."""
    jobs, n = [], 0
    for t in ledger.get("trades", []):
        if t.get("status") != "open":
            continue
        n += 1
        jobs.append(
            make_job(
                "redteam",
                {
                    "thesis": t.get("thesis", ""),
                    "instrument": t.get("instrument", ""),
                    "symbol": t.get("symbol", ""),
                    "entry": t.get("entry"),
                    "stop": t.get("stop"),
                    "target": t.get("target"),
                    "n": attacks_per,
                },
                f"redteam-{t.get('id', n)}",
            )
        )
    closed = closed_trades(ledger)
    if closed:
        for i in range(shocks):
            jobs.append(make_job("shock", {"ruin_r": 10.0}, f"shock-{i + 1}"))
        jobs.append(make_job("leaks", {"n": 6, "min_n": 5}, "leaks-1"))
    return jobs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def lab_dir(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else DEFAULT_DIR


def cmd_plan(args):
    out = lab_dir(args.dir)
    ledger = load_desk(Path(args.ledger) if args.ledger else _default_ledger())

    sim_paths = list(args.sim or [])
    if not sim_paths:
        default_sim = out / DEFAULT_SIM_NAME
        if default_sim.exists():
            sim_paths = [str(default_sim)]
            print(f"Including {default_sim} (pass --sim to override).")

    sim_trades: list[dict] = []
    for sim_path in sim_paths:
        found = load_sim_trades(Path(sim_path).expanduser())
        if not found:
            print(f"No usable closed trades in {sim_path}; skipping it.")
        sim_trades.extend(found)
    if sim_trades:
        # Sim trades join the closed record only. They carry no thesis, so
        # they can be shocked, resampled and mined -- but never red-teamed.
        ledger = dict(ledger)
        ledger["trades"] = list(ledger.get("trades", [])) + sim_trades

    fragility_paths = [Path(f).expanduser() for f in (args.fragility or [])]
    if not fragility_paths:
        fragility_paths = sorted(out.glob(FRAGILITY_GLOB))
    fragility_specs: list[dict] = []
    for path in fragility_paths:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"Unreadable fragility specs in {path}; skipping it.")
            continue
        for spec in loaded if isinstance(loaded, list) else []:
            if isinstance(spec, dict) and isinstance(spec.get("sweep"), dict):
                spec = dict(spec)
                spec.setdefault("param", "parameter")
                spec["param"] = (
                    f"{path.stem.replace('fragility', '').strip('_-') or 'sim'}:{spec['param']}"
                )
                fragility_specs.append(spec)

    jobs = build_queue(ledger, shocks=args.shocks, attacks_per=args.attacks)
    for i, spec in enumerate(fragility_specs, start=1):
        jobs.append(make_job("fragility", spec, f"fragility-{i}-{spec['param']}"))
    if not jobs:
        print("Nothing to queue: the desk has no open positions and no closed record.")
        print("Open a trade, or feed a backtest in with --sim (see")
        print("  python tools/reversal_15m_sim.py --help, --trades-out).")
        return 1

    record = closed_trades(ledger)
    (out / RECORD_NAME).parent.mkdir(parents=True, exist_ok=True)
    (out / RECORD_NAME).write_text(
        json.dumps({"snapshotted": now_iso(), "trades": record}, indent=2),
        encoding="utf-8",
    )
    write_queue(out / QUEUE_NAME, jobs)
    kinds = {}
    for j in jobs:
        kinds[j["kind"]] = kinds.get(j["kind"], 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    print(f"Queued {len(jobs)} job(s): {summary}")
    if sim_trades:
        print(f"Record: {len(record)} closed trade(s), {len(sim_trades)} from sims.")
    print(f"  {out / QUEUE_NAME}")
    return 0


def _default_ledger() -> Path:
    return Path(__file__).resolve().parent.parent / "spec_desk" / "spec_desk.json"


def cmd_run(args):
    out = lab_dir(args.dir)
    queue_path = out / QUEUE_NAME
    jobs = read_queue(queue_path)
    if not jobs:
        print(f"No queue at {queue_path}. Run `night_lab.py plan` first.")
        return 1
    record_path = out / RECORD_NAME
    if record_path.exists():
        trades = load_sim_trades(record_path)
    else:
        ledger = load_desk(Path(args.ledger) if args.ledger else _default_ledger())
        trades = closed_trades(ledger)
    llm = Ollama(model=args.model, host=args.host)

    stats = run_night(
        jobs,
        llm,
        trades,
        on_checkpoint=lambda js: write_queue(queue_path, js),
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        idle_threshold=args.idle,
        limit=1 if args.once else None,
        # --now-anyway is the smoke test: ignore both the window and the idle
        # timer so a run can be proved working at 3pm with the owner watching.
        now_fn=(
            (lambda: datetime.now().replace(hour=args.start_hour))
            if args.now_anyway
            else None
        ),
        idle_fn=(lambda: None) if args.now_anyway else None,
    )
    write_queue(queue_path, jobs)

    results = [j for j in jobs if j["status"] in ("done", "failed")]
    verdict = build_verdict(results)
    (out / VERDICT_NAME).write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report = out / f"report-{verdict['generated'][:10]}.md"
    report.write_text(render_report(results, verdict), encoding="utf-8")

    proposals = proposals_from(results)
    if proposals:
        with (out / PROPOSALS_NAME).open("a", encoding="utf-8") as fh:
            for p in proposals:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(
        f"{stats['completed']} job(s) run, {stats['yields']} yield(s), "
        f"{stats['remaining']} left ({stats['stopped_because']})."
    )
    print(f"Report: {report}")
    if proposals:
        print(f"{len(proposals)} proposal(s) staged for approval.")
    return 0


def cmd_status(args):
    out = lab_dir(args.dir)
    jobs = read_queue(out / QUEUE_NAME)
    if not jobs:
        print("Queue is empty.")
        return 0
    counts = {}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    print(
        f"{len(jobs)} job(s): "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    )
    for j in jobs:
        marker = {"done": "+", "failed": "!", "pending": "."}.get(j["status"], "?")
        print(f"  {marker} {j['id']:<20} {j['kind']}")
    return 0


def cmd_verdict(args):
    path = lab_dir(args.dir) / VERDICT_NAME
    if not path.exists():
        if not args.quiet:
            print("No verdict yet — the lab has not run.")
        return 0
    verdict = json.loads(path.read_text(encoding="utf-8"))
    # Silence is the good outcome: with --quiet, a night that found nothing
    # prints nothing at all, so the morning catch-up stays clean.
    if not verdict.get("broke"):
        if not args.quiet:
            print(f"Night lab: {verdict['jobs']} job(s), nothing broke.")
        return 0
    print(f"Night lab ({verdict['generated'][:10]}) — {verdict['jobs']} job(s):")
    for line in verdict["lines"]:
        print(f"  {line}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def shared(p):
        p.add_argument("--dir", help=f"lab directory (default {DEFAULT_DIR})")
        p.add_argument("--ledger", help="spec desk ledger JSON")

    p = sub.add_parser("plan", help="build tonight's queue from the desk")
    shared(p)
    p.add_argument(
        "--sim",
        action="append",
        metavar="PATH",
        help="closed-trade JSON from a simulator (reversal_15m_sim --trades-out); "
        "repeatable; joins the record for shocks and leak-mining",
    )
    p.add_argument("--shocks", type=int, default=6, help="scenario jobs to queue")
    p.add_argument(
        "--fragility",
        action="append",
        metavar="PATH",
        help="fragility spec JSON from reversal_15m_sim --fragility-out; "
        f"defaults to every {FRAGILITY_GLOB} in the lab dir",
    )
    p.add_argument("--attacks", type=int, default=8, help="attacks per open thesis")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="grind the queue while the machine is idle")
    shared(p)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--start-hour", type=int, default=START_HOUR)
    p.add_argument("--end-hour", type=int, default=END_HOUR)
    p.add_argument(
        "--idle",
        type=float,
        default=IDLE_THRESHOLD_S,
        help="seconds of no input before the lab claims the machine",
    )
    p.add_argument("--once", action="store_true", help="run a single job and stop")
    p.add_argument(
        "--now-anyway",
        action="store_true",
        help="ignore the window and the idle timer (smoke test)",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="what is queued and what has run")
    shared(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("verdict", help="the morning one-screen")
    shared(p)
    p.add_argument(
        "--quiet", action="store_true", help="print nothing unless something broke"
    )
    p.set_defaults(func=cmd_verdict)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

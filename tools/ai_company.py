"""The one-person AI company, made checkable.

`docs/blueprint-one-person-ai-company.json` is the reference architecture:
a local service business run as a loop -- marketing rings the phone, intake
catches it, sales books it, operations does the job, finance gets paid, and
the collected cash sets next week's ad budget. This module is the part of it
that can be computed rather than asserted.

Five things it does, and one it refuses:

``roster``   derives the agent roster *from the map*. Every step whose
             executor is ``ai`` names its agent in ``owner``, so the roster is
             true by construction and cannot drift from the process it
             describes. There is no second list to maintain.
``gates``    convicts any AI step that reaches a money- or risk-committing
             tool without a person immediately in front of it. This is the
             doctrine -- agents move information, people move money -- turned
             into an assertion something can fail.
``hours``    prices the person steps: duration x frequency, per department.
             With ``--baseline`` it reports hours returned against a
             current-state map.
``loop``     the loop economics. Does a job pay back what it cost to win, and
             how fast, and what does that make next week's ad budget.
``reprice``  quoted against actual, per job type, behind a sample-size gate.

What it refuses: an hours-returned figure from a single map. A target design
has no baseline in it, and subtracting from an imagined "before" is how a
tool starts producing numbers nobody can check. Same rule as
``tools/spend_watch.py`` refusing a burn rate from one snapshot.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE = REPO_ROOT / "docs" / "blueprint-one-person-ai-company.json"

#: Steps that are actual work. A delay, a terminator and a go-to are flow,
#: not effort -- the same split ``tools/blueprint_converter.py`` makes.
WORK_KINDS = ("task", "decision")

#: Tool categories through which a step can commit the business: spend money,
#: bind it to a contract, or move cash. An AI step touching one of these needs
#: a person in front of it. Overridable with ``--committing`` because another
#: business will have another stack.
COMMITTING_CATEGORIES = ("Payments", "Advertising", "Contracts")


# --- duration ---------------------------------------------------------------
#
# A faithful port of ``parseDuration`` in static/process-grammar.js, so a load
# figure here and the one flow-canvas shows in its panel are the same number.
# tests/test_ai_company.py runs both against a shared set of strings and
# requires them to agree; drift between the two would be invisible otherwise.

_DUR_UNITS = {
    "m": 1,
    "min": 1,
    "mins": 1,
    "minute": 1,
    "minutes": 1,
    "h": 60,
    "hr": 60,
    "hrs": 60,
    "hour": 60,
    "hours": 60,
    "d": 480,
    "day": 480,
    "days": 480,
}

_BARE = re.compile(r"^\d+(\.\d+)?$")
_RANGE = re.compile(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*([a-z]+)$")
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")


def parse_duration(value: Any) -> float | None:
    """Minutes, or ``None`` when the text is not a duration.

    ``None`` rather than zero on purpose: an unreadable duration leaves the
    step unpriced and visible, instead of quietly worth nothing. A day is
    eight hours, not twenty-four.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ("instant", "immediate"):
        return 0.0
    text = re.sub(r"\s+to\s+", "-", text)
    if _BARE.match(text):
        return float(text)

    span = _RANGE.match(text)
    if span:
        unit = _DUR_UNITS.get(span.group(3))
        if unit is None:
            return None
        return (float(span.group(1)) + float(span.group(2))) / 2 * unit

    total, hit = 0.0, False
    for number, word in _TOKEN.findall(text):
        unit = _DUR_UNITS.get(word)
        if unit is None:
            return None
        hit = True
        total += float(number) * unit
    # whatever the numbers did not account for has to be punctuation, or this
    # is prose we should not pretend to have understood
    rest = re.sub(r"[\s,;.]+", "", _TOKEN.sub("", text))
    return total if hit and not rest else None


# --- blueprint access -------------------------------------------------------


def load_blueprint(path: Path | str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def work_steps(blueprint: dict) -> Iterable[tuple[dict, dict]]:
    """Every (process, step) pair that is actual work."""
    for proc in blueprint.get("processes", []):
        for step in proc.get("steps", []):
            if step.get("kind", "task") in WORK_KINDS:
                yield proc, step


def successors(steps: Sequence[dict]) -> Dict[Any, List[Any]]:
    """Where each step can go next.

    Flow falls through to the next number unless a decision's branches say
    otherwise, a go-to jumps, or the step is a terminator. ``"end"`` branch
    targets are terminal and carry no successor.
    """
    numbers = sorted(s.get("number") for s in steps if s.get("number") is not None)
    by_number = {s.get("number"): s for s in steps}
    out: Dict[Any, List[Any]] = {}
    for number in numbers:
        step = by_number[number]
        kind = step.get("kind", "task")
        if kind == "goto":
            target = step.get("goto")
            out[number] = [target] if target in by_number else []
        elif step.get("branches"):
            out[number] = [
                b.get("to") for b in step["branches"] if b.get("to") in by_number
            ]
        elif kind == "end":
            out[number] = []
        else:
            later = [n for n in numbers if n > number]
            out[number] = [later[0]] if later else []
    return out


def predecessors(steps: Sequence[dict]) -> Dict[Any, List[Any]]:
    """The inverse of :func:`successors`: who can hand control to each step."""
    back: Dict[Any, List[Any]] = {
        s.get("number"): [] for s in steps if s.get("number") is not None
    }
    for source, targets in successors(steps).items():
        for target in targets:
            if target in back:
                back[target].append(source)
    return back


# --- roster -----------------------------------------------------------------


def roster(blueprint: dict) -> Dict[str, Dict[str, Any]]:
    """Who runs what, derived from the map rather than kept beside it.

    Returns ``{executor: {owner: {"steps": n, "processes": [...]}}}`` for the
    three executor kinds. The AI section *is* the agent roster: an agent
    exists exactly when a step says it runs one.
    """
    found: Dict[str, Dict[str, Dict[str, Any]]] = {
        "ai": OrderedDict(),
        "automation": OrderedDict(),
        "person": OrderedDict(),
    }
    for proc, step in work_steps(blueprint):
        executor = step.get("executor", "person")
        if executor not in found:
            continue
        owner = step.get("owner") or "(unnamed)"
        entry = found[executor].setdefault(owner, {"steps": 0, "processes": []})
        entry["steps"] += 1
        if proc["id"] not in entry["processes"]:
            entry["processes"].append(proc["id"])
    return found


def fanout(blueprint: dict) -> Dict[str, List[dict]]:
    """The backlog sub-agents each role would split into, grouped by role.

    These are roadmap items with ``category: agent-fanout`` and they are all
    backlog on purpose. Counting them alongside the built roles is how the
    headline agent count stays honest about which ones exist.
    """
    grouped: Dict[str, List[dict]] = OrderedDict()
    for item in blueprint.get("roadmap", []):
        if item.get("category") != "agent-fanout":
            continue
        grouped.setdefault(item.get("owner", "(unassigned)"), []).append(item)
    return grouped


# --- gate audit -------------------------------------------------------------


def committing_tools(blueprint: dict, categories: Sequence[str]) -> Dict[str, str]:
    """Tool id -> category, for the tools through which money or risk moves."""
    wanted = {c.lower() for c in categories}
    return {
        tool["id"]: tool.get("category", "")
        for tool in blueprint.get("tools", [])
        if str(tool.get("category", "")).lower() in wanted
    }


def audit_gates(
    blueprint: dict, categories: Sequence[str] = COMMITTING_CATEGORIES
) -> List[dict]:
    """Findings for every step that commits the business.

    A step declares commitment with ``commits: true`` rather than having it
    inferred from the tools it touches, because reading a payment system and
    charging through one are the same tool. The tool categories are still used,
    but only to raise an *unmarked* step for a second look -- a list to confirm,
    not a verdict.

    The verdict on a committing AI step turns on its immediate predecessors:

    ``gated``     every path into it passes through a person step.
    ``partial``   some do. That is what a threshold looks like -- small orders
                  go straight through, large ones wait -- so it is a design
                  decision to see rather than a fault, and the bypass is named.
    ``ungated``   none do. An agent commits money on its own reading of the
                  situation, which is the failure the architecture exists to
                  prevent.
    """
    committing = committing_tools(blueprint, categories)
    findings: List[dict] = []
    for proc in blueprint.get("processes", []):
        steps = proc.get("steps", [])
        by_number = {s.get("number"): s for s in steps}
        back = predecessors(steps)
        for step in steps:
            if step.get("kind", "task") not in WORK_KINDS:
                continue
            touched = [t for t in step.get("tools", []) or [] if t in committing]
            executor = step.get("executor", "person")
            number = step.get("number")
            where = {
                "process": proc["id"],
                "step": number,
                "title": step.get("title", ""),
                "owner": step.get("owner", ""),
                "tools": touched,
            }

            if not step.get("commits"):
                if touched and executor != "person":
                    findings.append(
                        {
                            **where,
                            "code": "unmarked_touch",
                            "severity": "info",
                            "message": (
                                f"reaches {', '.join(touched)} but is not marked "
                                "as committing — confirm it only reads"
                            ),
                        }
                    )
                continue

            if executor == "person":
                findings.append(
                    {
                        **where,
                        "code": "person_commits",
                        "severity": "ok",
                        "message": "a person commits this directly",
                    }
                )
                continue
            if executor == "automation":
                findings.append(
                    {
                        **where,
                        "code": "automation_commits",
                        "severity": "warning",
                        "message": (
                            "automation commits — fine if its trigger is "
                            "deterministic, check that it is"
                        ),
                    }
                )
                continue

            paths = back.get(number, [])
            gated_by = [
                p for p in paths if by_number.get(p, {}).get("executor") == "person"
            ]
            bypass = [p for p in paths if p not in gated_by]
            if gated_by and not bypass:
                findings.append(
                    {
                        **where,
                        "code": "gated",
                        "severity": "ok",
                        "message": f"every path in passes step {_join(gated_by)}",
                    }
                )
            elif gated_by:
                findings.append(
                    {
                        **where,
                        "code": "partial_gate",
                        "severity": "warning",
                        "message": (
                            f"gated by step {_join(gated_by)}, bypassed from step "
                            f"{_join(bypass)} — deliberate threshold, or a hole?"
                        ),
                    }
                )
            else:
                findings.append(
                    {
                        **where,
                        "code": "ungated_ai_commit",
                        "severity": "error",
                        "message": (
                            "AI step commits with no person step in front of it"
                        ),
                    }
                )
    return findings


def _join(numbers: Iterable[Any]) -> str:
    return ", ".join(str(n) for n in sorted(numbers))


# --- labour -----------------------------------------------------------------


def monthly_load(blueprint: dict) -> dict:
    """Person-minutes a month, by department, with the unpriced steps named.

    Only ``person`` steps count. An agent's time is not a labour bill, and a
    step missing either number is unpriced rather than free -- reported, not
    silently dropped to zero.
    """
    dept_of: Dict[str, str] = {}
    for dept in blueprint.get("departments", []):
        for proc_id in dept.get("processes", []) or []:
            dept_of[proc_id] = dept.get("name", dept.get("id", "?"))

    per_dept: Dict[str, float] = OrderedDict()
    per_process: Dict[str, float] = OrderedDict()
    unpriced: List[dict] = []
    total = 0.0
    for proc, step in work_steps(blueprint):
        if step.get("executor") != "person":
            continue
        each = parse_duration(step.get("duration"))
        frequency = step.get("frequency")
        if each is None or not frequency:
            unpriced.append(
                {
                    "process": proc["id"],
                    "step": step.get("number"),
                    "title": step.get("title", ""),
                }
            )
            continue
        minutes = each * float(frequency)
        total += minutes
        name = dept_of.get(proc["id"], "(no department)")
        per_dept[name] = per_dept.get(name, 0.0) + minutes
        per_process[proc["id"]] = per_process.get(proc["id"], 0.0) + minutes
    return {
        "minutes": total,
        "hours": total / 60.0,
        "by_department": per_dept,
        "by_process": per_process,
        "unpriced": unpriced,
    }


# --- loop economics ---------------------------------------------------------


class LoopError(ValueError):
    """The inputs do not describe a loop that can be priced."""


def loop_economics(
    spend: float,
    leads: float,
    jobs: float,
    job_value: float,
    margin: float,
    cycle_days: float | None = None,
) -> dict:
    """Does a job pay back what it cost to win it, and how fast.

    ``margin`` is gross margin as a fraction. ``cycle_days`` is lead to
    collected cash; without it the days figure is not produced rather than
    guessed, because that number is the one people quote.
    """
    if leads <= 0 or jobs <= 0:
        raise LoopError("leads and jobs must both be above zero")
    if jobs > leads:
        raise LoopError(f"more jobs ({jobs:g}) than leads ({leads:g})")
    if not 0 < margin < 1:
        raise LoopError("margin is a fraction between 0 and 1")
    if job_value <= 0:
        raise LoopError("job value must be above zero")

    contribution = job_value * margin
    cac = spend / jobs
    result = {
        "cost_per_lead": spend / leads,
        "cost_per_job": cac,
        "close_rate": jobs / leads,
        "contribution_per_job": contribution,
        "payback_jobs": cac / contribution if contribution else math.inf,
        "contribution_after_cac": contribution - cac,
        "payback_days": None,
    }
    if cycle_days is not None:
        result["payback_days"] = result["payback_jobs"] * cycle_days
    return result


def next_budget(
    economics: dict,
    spend: float,
    rule_payback_jobs: float,
    ceiling: float | None = None,
    step: float = 0.20,
) -> dict:
    """What the loop's own arithmetic makes next week's ad budget.

    A loop that pays back inside the rule gets fed, capped by the ceiling --
    which is capacity, not confidence. One that does not gets cut. This is a
    *proposal*: in the reference architecture a person approves the number
    before an agent applies it.
    """
    clears = economics["payback_jobs"] <= rule_payback_jobs
    if clears:
        proposed = spend * (1 + step)
        capped = ceiling is not None and proposed > ceiling
        if capped:
            proposed = ceiling
    else:
        proposed = spend * (1 - step)
        capped = False
    return {
        "clears_rule": clears,
        "current": spend,
        "proposed": proposed,
        "change": proposed - spend,
        "capped_by_ceiling": capped,
        "direction": "raise" if clears else "cut",
    }


# --- repricing --------------------------------------------------------------
#
# Two gates before a price may move, because a one-van shop closing ~38 jobs a
# month across several job types does not generate enough of any one type to
# tell a margin drift from noise. Chasing that noise is not a harmless error:
# you find out you overpriced by losing bids, weeks later, with no signal
# saying why.

#: Two-sided 95% t critical values by degrees of freedom. Embedded rather than
#: imported so this tool stays stdlib-only; beyond 30 the normal value is
#: within a percent and the sample size stopped being the binding constraint.
_T95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def t_critical(df: int) -> float:
    if df < 1:
        return math.inf
    return _T95.get(df, 1.960)


# The interval above is what a reader looks at. The conviction is made on a
# p-value corrected across every job type in the run, because repricing scans
# a grid -- one test per job type, every month -- and a grid is a fishing
# expedition whether or not it was meant as one. At twelve job types and an
# uncorrected 5% threshold you would expect to "discover" a price change in
# roughly one clean month in two. Same charge tools/season_scan.py and
# tools/calibration_audit.py pay, same implementation.


#: Continued fraction for the incomplete beta, by the modified Lentz method.
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return (
        1.0
        - math.exp(
            math.lgamma(a + b)
            - math.lgamma(a)
            - math.lgamma(b)
            + b * math.log1p(-x)
            + a * math.log(x)
        )
        * _betacf(b, a, 1.0 - x)
        / b
    )


def t_two_sided_p(t: float, df: int) -> float:
    """Two-sided p for a t statistic. Stdlib only, like the other labs here."""
    if df < 1:
        return 1.0
    if t == 0.0:
        return 1.0
    return min(1.0, _betainc(df / 2.0, 0.5, df / (df + t * t)))


#: Family-wise error rate the repricing run is held to across its job types.
FDR_Q = 0.05


def bh_fdr(pvalues: List[float], q: float = FDR_Q) -> List[bool]:
    """Benjamini-Hochberg across the job types of one run."""
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


#: Below this many jobs of a type, no price moves. The reference architecture
#: keeps the real figure in the business's ``rules.md``; this is the default
#: the tool argues for.
DEFAULT_MIN_JOBS = 8


def read_jobs(path: Path | str) -> List[dict]:
    """Closed jobs from a CSV: ``job_type``, ``quoted``, ``cost``.

    ``actual``/``actual_cost`` are accepted for the cost column, and ``type``
    for the job type, because the export you get is rarely the one you asked
    for. A row missing either number is dropped and counted, never defaulted.
    """
    rows: List[dict] = []
    dropped = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            keys = {
                (k or "").strip().lower(): (v or "").strip() for k, v in raw.items()
            }
            job_type = keys.get("job_type") or keys.get("type") or ""
            quoted = keys.get("quoted") or keys.get("price") or ""
            cost = (
                keys.get("cost") or keys.get("actual") or keys.get("actual_cost") or ""
            )
            try:
                quoted_f, cost_f = float(quoted), float(cost)
            except ValueError:
                dropped += 1
                continue
            if not job_type or quoted_f <= 0:
                dropped += 1
                continue
            rows.append({"job_type": job_type, "quoted": quoted_f, "cost": cost_f})
    if dropped:
        print(
            f"note: dropped {dropped} row(s) with no usable job type, price or cost",
            file=sys.stderr,
        )
    return rows


def reprice(
    rows: Sequence[dict],
    target_margin: float,
    min_jobs: int = DEFAULT_MIN_JOBS,
    q: float = FDR_Q,
) -> List[dict]:
    """Per job type: realized margin, its interval, and whether a price may move.

    Four outcomes, and three of them are "leave it alone":

    ``too_few``       fewer than ``min_jobs`` of this type. The correct answer
                      most months, and it says how many more are needed.
    ``inside_noise``  enough jobs, but the drift is not distinguishable from
                      job-to-job variance on its own.
    ``family_noise``  it would have cleared on its own and does not survive
                      Benjamini-Hochberg across the other job types in this
                      run. Repricing tests every type every month, so it is a
                      grid scan and is charged as one.
    ``reprice``       enough jobs, and clear of zero after that correction.
                      Carries the exact price multiplier that restores the
                      target margin, since cost is fixed and
                      price = cost / (1 - margin).
    """
    if not 0 < target_margin < 1:
        raise LoopError("target margin is a fraction between 0 and 1")

    by_type: Dict[str, List[float]] = OrderedDict()
    for row in rows:
        margin = (row["quoted"] - row["cost"]) / row["quoted"]
        by_type.setdefault(row["job_type"], []).append(margin)

    out: List[dict] = []
    for job_type, margins in by_type.items():
        n = len(margins)
        mean = sum(margins) / n
        drift = mean - target_margin
        result = {
            "job_type": job_type,
            "jobs": n,
            "mean_margin": mean,
            "target_margin": target_margin,
            "drift": drift,
            "ci_low": None,
            "ci_high": None,
            "p": None,
            "fdr_pass": False,
            "verdict": "too_few",
            "detail": f"needs {min_jobs - n} more job(s) before a price may move",
            "price_multiplier": None,
        }
        if n >= min_jobs:
            if n >= 2:
                variance = sum((m - mean) ** 2 for m in margins) / (n - 1)
                stderr = math.sqrt(variance / n)
            else:
                stderr = math.inf
            half = t_critical(n - 1) * stderr
            result["ci_low"] = drift - half
            result["ci_high"] = drift + half
            if stderr == 0:
                # every job of this type landed on the same margin: no spread
                # to test against, and the drift is exactly what it looks like
                result["p"] = 0.0 if drift else 1.0
            elif math.isinf(stderr):
                result["p"] = 1.0
            else:
                result["p"] = t_two_sided_p(drift / stderr, n - 1)
        out.append(result)

    # the correction runs across the types that actually cleared the sample
    # gate -- a type with four jobs was never a test, and counting it would
    # make the correction weaker for everyone else
    tested = [r for r in out if r["p"] is not None]
    for result, passed in zip(tested, bh_fdr([r["p"] for r in tested], q)):
        result["fdr_pass"] = passed
        if passed:
            result["verdict"] = "reprice"
            result["price_multiplier"] = (1 - result["mean_margin"]) / (
                1 - target_margin
            )
            way = "under" if result["drift"] < 0 else "over"
            result["detail"] = (
                f"margin runs {abs(result['drift']) * 100:.1f}pp {way} target "
                f"across {result['jobs']} jobs (p={result['p']:.4f}, clears "
                f"Benjamini-Hochberg at q={q:g} over {len(tested)} type(s))"
            )
        elif result["p"] <= q:
            result["verdict"] = "family_noise"
            result["detail"] = (
                f"would clear on its own (p={result['p']:.4f}) but does not "
                f"survive the correction across {len(tested)} job type(s) "
                "tested this run"
            )
        else:
            result["verdict"] = "inside_noise"
            result["detail"] = (
                f"drift is not distinguishable from job-to-job variance "
                f"(p={result['p']:.4f})"
            )
    return out


# --- rendering --------------------------------------------------------------


def _hm(minutes: float) -> str:
    hours = minutes / 60.0
    return f"{hours:,.1f} h/mo" if hours >= 1 else f"{minutes:,.0f} min/mo"


def render_roster(blueprint: dict, expansion: bool = False) -> str:
    found = roster(blueprint)
    lines = [f"{blueprint['meta']['name']}", ""]

    agents = found["ai"]
    lines.append(f"AI AGENTS — {len(agents)} role(s), derived from the map")
    for name, entry in agents.items():
        lines.append(
            f"  {name:24} {entry['steps']:>2} step(s)  "
            f"{', '.join(entry['processes'])}"
        )
    lines.append("")
    lines.append(f"AUTOMATION — {len(found['automation'])} integration(s), not agents")
    for name, entry in found["automation"].items():
        lines.append(f"  {name:24} {entry['steps']:>2} step(s)")
    lines.append("")
    lines.append(f"PEOPLE — {len(found['person'])}")
    for name, entry in found["person"].items():
        lines.append(f"  {name:24} {entry['steps']:>2} step(s)")

    grouped = fanout(blueprint)
    planned = sum(len(v) for v in grouped.values())
    lines.append("")
    lines.append(
        f"{len(agents)} role(s) specified, {planned} backlog sub-agent(s) — "
        f"{len(agents) + planned} at full fan-out"
    )
    if expansion:
        lines.append("")
        lines.append("FAN-OUT (all backlog — none of these exist)")
        for role, items in grouped.items():
            lines.append(f"  {role}")
            for item in items:
                lines.append(f"    - {item['title']}")
                lines.append(f"      {item['description']}")
    else:
        lines.append("Run with --expansion to list the backlog sub-agents.")
    return "\n".join(lines)


def render_gates(findings: Sequence[dict]) -> str:
    groups = [
        (
            "UNGATED — an agent commits with nobody in front of it",
            ("ungated_ai_commit",),
        ),
        ("PARTIALLY GATED — a deliberate threshold, or a hole", ("partial_gate",)),
        ("AUTOMATION — no gate needed, but check the trigger", ("automation_commits",)),
        ("GATED", ("gated", "person_commits")),
        (
            "TOUCHES A COMMITTING SYSTEM, NOT MARKED — confirm read-only",
            ("unmarked_touch",),
        ),
    ]
    lines = []
    for label, codes in groups:
        group = [f for f in findings if f["code"] in codes]
        if not group:
            continue
        lines.append(f"{label} — {len(group)}")
        for f in group:
            lines.append(f"  {f['process']} step {f['step']}: {f['title']}")
            lines.append(f"    {f['owner']} — {f['message']}")
        lines.append("")
    if not findings:
        lines.append("No step in this map commits the business.")
    counts = {
        s: sum(1 for f in findings if f["severity"] == s)
        for s in ("error", "warning", "ok", "info")
    }
    lines.append(
        f"{counts['error']} ungated, {counts['warning']} to justify, "
        f"{counts['ok']} gated, {counts['info']} to confirm."
    )
    return "\n".join(lines).rstrip()


def render_hours(load: dict, baseline: dict | None = None) -> str:
    lines = [f"Person load: {_hm(load['minutes'])}", ""]
    for name, minutes in load["by_department"].items():
        lines.append(f"  {name:28} {_hm(minutes)}")
    if load["unpriced"]:
        lines.append("")
        lines.append(
            f"UNPRICED — {len(load['unpriced'])} person step(s) with no duration x frequency:"
        )
        for item in load["unpriced"]:
            lines.append(f"  {item['process']} step {item['step']}: {item['title']}")
        lines.append("  The total above is missing whatever these cost.")
    lines.append("")
    if baseline is None:
        lines.append(
            "No baseline given, so no hours-returned figure. A target design has\n"
            "no 'before' inside it — map how the business runs today and pass it\n"
            "with --baseline. Subtracting from an imagined before is how this\n"
            "kind of tool starts producing numbers nobody can check."
        )
    else:
        delta = baseline["minutes"] - load["minutes"]
        lines.append(f"Baseline load:  {_hm(baseline['minutes'])}")
        lines.append(f"Target load:    {_hm(load['minutes'])}")
        verb = "returned" if delta >= 0 else "ADDED"
        lines.append(f"Hours {verb}:  {_hm(abs(delta))}")
        if baseline["unpriced"] or load["unpriced"]:
            lines.append(
                f"  Held loosely: {len(baseline['unpriced'])} unpriced step(s) in the "
                f"baseline and {len(load['unpriced'])} in the target are not in either total."
            )
    return "\n".join(lines)


def render_loop(economics: dict, budget: dict | None = None) -> str:
    lines = [
        f"Cost per lead          {economics['cost_per_lead']:>10,.2f}",
        f"Close rate             {economics['close_rate'] * 100:>9.1f}%",
        f"Cost per job (CAC)     {economics['cost_per_job']:>10,.2f}",
        f"Contribution per job   {economics['contribution_per_job']:>10,.2f}",
        f"Left after CAC         {economics['contribution_after_cac']:>10,.2f}",
        f"Payback                {economics['payback_jobs']:>10,.2f} jobs",
    ]
    if economics["payback_days"] is None:
        lines.append(
            "Payback in days        not computed — pass --cycle-days (lead to "
            "collected cash)"
        )
    else:
        lines.append(f"Payback                {economics['payback_days']:>10,.1f} days")
    if budget is not None:
        lines.append("")
        verdict = "clears the rule" if budget["clears_rule"] else "misses the rule"
        lines.append(f"Loop {verdict}: proposed {budget['direction']}")
        lines.append(
            f"  {budget['current']:,.2f} -> {budget['proposed']:,.2f} "
            f"({budget['change']:+,.2f})"
            + ("  [held at ceiling]" if budget["capped_by_ceiling"] else "")
        )
        lines.append(
            "  A proposal, not a change: a person approves the number before an "
            "agent applies it."
        )
    return "\n".join(lines)


_VERDICT_LABEL = {
    "too_few": "TOO FEW",
    "inside_noise": "NOISE",
    "family_noise": "NOT vs FAMILY",
    "reprice": "REPRICE",
}


def render_reprice(results: Sequence[dict], min_jobs: int) -> str:
    tested = sum(1 for r in results if r["p"] is not None)
    lines = [
        f"Sample-size gate: {min_jobs} jobs of a type before any price may move.",
        f"Benjamini-Hochberg at q={FDR_Q:g} across the {tested} type(s) that "
        "cleared it — repricing tests every type every month, and a grid is a "
        "fishing expedition whether or not it was meant as one.",
        "",
        f"{'JOB TYPE':22} {'N':>3} {'MARGIN':>7} {'DRIFT':>7} {'INTERVAL':>17} "
        f"{'P':>7}  VERDICT",
    ]
    for r in results:
        if r["ci_low"] is None:
            interval = "—"
        else:
            interval = f"[{r['ci_low'] * 100:+.1f}, {r['ci_high'] * 100:+.1f}]pp"
        shown_p = "—" if r["p"] is None else f"{r['p']:.4f}"
        lines.append(
            f"{r['job_type'][:22]:22} {r['jobs']:>3} "
            f"{r['mean_margin'] * 100:>6.1f}% {r['drift'] * 100:>+6.1f}pp "
            f"{interval:>17} {shown_p:>7}  {_VERDICT_LABEL[r['verdict']]}"
        )
    lines.append("")
    for r in results:
        lines.append(f"{r['job_type']}: {r['detail']}")
        if r["verdict"] == "reprice":
            lines.append(
                f"  proposed price x{r['price_multiplier']:.4f} "
                f"({(r['price_multiplier'] - 1) * 100:+.1f}%) to restore "
                f"{r['target_margin'] * 100:.0f}% margin — for the owner to approve"
            )
    movable = sum(1 for r in results if r["verdict"] == "reprice")
    lines.append("")
    lines.append(
        f"{movable} of {len(results)} job type(s) cleared every gate. "
        "The rest keep their prices."
    )
    return "\n".join(lines)


# --- page -------------------------------------------------------------------
#
# Rendered from the blueprint rather than written beside it, for the same
# reason the roster is derived: a hand-kept page and the map it describes stop
# agreeing, and the page is the one people read.
#
# Palette and font pairing came from .claude/skills/ui-ux-pro-max (the
# "Financial Dashboard" colors and the "Developer Mono" pairing) rather than
# being invented. Fonts are linked with a full system fallback stack so the
# file still opens correctly from file:// with no network.

_PAGE_CSS = """
/* Committed to light. The artifact host paints its own ground in the viewer's
   theme, so every surface and every ink below is stated rather than inherited
   -- that is what lets one deliberate palette hold on either host. */
:root{
  --page:#fbfbfa; --card:#ffffff; --sunken:#f5f5f3;
  --ink:#141413; --ink-2:#57564f; --ink-3:#8a8880;
  --rule:#e6e5e0; --rule-2:#f0efec;

  /* Executor -- the page's argument, and the only colour on 90 step rows.
     Blue/orange is the safest categorical pair in the set; automation is
     deliberately greyed because it is plumbing, not a party to the argument.
     Validated all-pairs light: CVD dE 16.6, normal dE 21.2. */
  --ai:#2a78d6;        --ai-bg:#e9f1fc;     --ai-edge:#c2dbf6;
  --person:#c4491d;    --person-bg:#fdece5; --person-edge:#f7cdb9;
  --auto:#57564f;      --auto-bg:#f0efec;   --auto-edge:#dedcd5;

  /* Loop stages -- large named blocks only, never small marks, and every one
     carries its name so hue reinforces the label rather than replacing it.
     FOUR hues for five stages on purpose: marketing and finance share green
     because they are the same money. The loop closes when what finance
     collected becomes what marketing spends, and the colour says so.
     Validated all-pairs light: CVD dE 16.2, normal dE 19.6. The two sub-3:1
     hues never carry text -- they are rails and rules; ink is the -ink token. */
  --s1:#008300; --s1-ink:#00701f; --s1-bg:#e3f4e6;
  --s2:#4a3aa7; --s2-ink:#4a3aa7; --s2-bg:#eeecfa;
  --s3:#e87ba4; --s3-ink:#a3305e; --s3-bg:#fceaf2;
  --s4:#eda100; --s4-ink:#8a5d00; --s4-bg:#fbf1dc;
  --s5:#008300; --s5-ink:#00701f; --s5-bg:#e3f4e6;

  /* Status -- reserved, and never carried by hue alone: every one ships with
     a glyph and a word. */
  --good:#00701f; --good-bg:#e3f4e6;
  --warn:#9a6200; --warn-bg:#fbf1dc;
  --bad:#b5292a;  --bad-bg:#fdeaea;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font-family:"Public Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  font-size:16.5px;line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:56px 28px 120px}
h1,h2,h3{font-family:Newsreader,Georgia,"Times New Roman",serif;font-weight:600;
  line-height:1.15;letter-spacing:-0.012em;text-wrap:balance;margin:0}
h1{font-size:clamp(2.1rem,5vw,3.1rem)}
h2{font-size:clamp(1.5rem,3vw,1.95rem);margin:0 0 6px}
h3{font-size:1.15rem;margin:0}
p{margin:0;max-width:68ch}
.stack{display:flex;flex-direction:column}
.g8{gap:8px}.g12{gap:12px}.g16{gap:16px}.g24{gap:24px}.g40{gap:40px}
.lede{font-size:1.18rem;line-height:1.55;color:var(--ink-2);max-width:60ch}
.eyebrow{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.72rem;
  font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3)}
section{border-top:1px solid var(--rule);padding-top:34px;
  display:flex;flex-direction:column;gap:20px}
.note{color:var(--ink-2);max-width:68ch}

/* ------------------------------------------------------------ loop diagram */
.figure{background:var(--card);border:1px solid var(--rule);border-radius:14px;
  padding:26px 24px 20px;overflow-x:auto}
.figure svg{display:block;min-width:900px;width:100%;height:auto}

/* -------------------------------------------------------------- stat tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:14px}
.tile{background:var(--card);border:1px solid var(--rule);border-radius:12px;
  padding:18px 18px 16px;display:flex;flex-direction:column;gap:2px}
.tile .n{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:2.2rem;
  font-weight:600;line-height:1.05;font-variant-numeric:tabular-nums;
  letter-spacing:-.02em}
.tile .s{color:var(--ink-2);font-size:.9rem;line-height:1.4;margin-top:4px}

/* ------------------------------------------------------------------- chips */
.chip{display:inline-flex;align-items:center;gap:5px;white-space:nowrap;
  font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.73rem;
  font-weight:500;padding:2px 8px;border-radius:6px;border:1px solid transparent}
.chip .dot{width:7px;height:7px;border-radius:50%;flex:none}
.c-ai{background:var(--ai-bg);color:var(--ai);border-color:var(--ai-edge)}
.c-ai .dot{background:var(--ai)}
.c-person{background:var(--person-bg);color:var(--person);border-color:var(--person-edge)}
.c-person .dot{background:var(--person)}
.c-automation{background:var(--auto-bg);color:var(--auto);border-color:var(--auto-edge)}
.c-automation .dot{background:var(--auto);border-radius:1px}
.c-backlog{background:var(--sunken);color:var(--ink-3);border-color:var(--rule)}
.legend{display:flex;flex-wrap:wrap;gap:10px 22px;align-items:center;
  background:var(--sunken);border-radius:10px;padding:12px 16px}
.legend b{font-weight:600;font-size:.9rem}
.legend .k{color:var(--ink-2);font-size:.88rem}

/* ------------------------------------------------------------------ status */
.st{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;font-weight:600;
  padding:2px 9px;border-radius:6px;white-space:nowrap}
.st-good{background:var(--good-bg);color:var(--good)}
.st-warn{background:var(--warn-bg);color:var(--warn)}
.st-bad{background:var(--bad-bg);color:var(--bad)}
.st-person{background:var(--person-bg);color:var(--person)}
.st .gl{font-family:"JetBrains Mono",ui-monospace,monospace}

/* ------------------------------------------------------------------- cards */
.card{background:var(--card);border:1px solid var(--rule);border-radius:12px;
  padding:20px 22px;display:flex;flex-direction:column;gap:10px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.gate{border-left:4px solid var(--person)}
.meta{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.76rem;
  color:var(--ink-3);font-variant-numeric:tabular-nums}
.card p{font-size:.95rem;color:var(--ink-2)}

/* ------------------------------------------------------------------- stage */
.stage{border-left:4px solid var(--stage,var(--rule));padding-left:22px;
  display:flex;flex-direction:column;gap:14px}
.stage .eyebrow{color:var(--stage-ink)}
.stage-head{display:flex;flex-direction:column;gap:3px}

/* ------------------------------------------------------------------ tables */
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:10px;
  background:var(--card)}
.scroll:focus-visible{outline:2px solid var(--ai);outline-offset:2px}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th{text-align:left;font-family:"JetBrains Mono",ui-monospace,monospace;
  font-size:.7rem;font-weight:600;letter-spacing:.09em;text-transform:uppercase;
  color:var(--ink-3);padding:10px 14px;background:var(--sunken);
  border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:9px 14px;border-bottom:1px solid var(--rule-2);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{font-family:"JetBrains Mono",ui-monospace,monospace;color:var(--ink-3);
  width:3rem;text-align:right;font-variant-numeric:tabular-nums}
td.cost{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.8rem;
  color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.sub{color:var(--ink-3);font-size:.8rem;line-height:1.5;margin-top:2px}
.sub .arrow{font-family:"JetBrains Mono",ui-monospace,monospace}
tr.is-person{background:var(--person-bg)}
tr.is-person td{border-bottom-color:var(--person-edge)}

/* ------------------------------------------------------------------- files */
.file code{font-family:"JetBrains Mono",ui-monospace,monospace;font-weight:600;
  font-size:.95rem;color:var(--ai)}
.callout{background:var(--sunken);border-radius:12px;padding:22px 24px;
  display:flex;flex-direction:column;gap:10px}
.callout .eyebrow{color:var(--ink-3)}
footer{margin-top:64px;padding-top:22px;border-top:1px solid var(--rule);
  color:var(--ink-3);font-size:.85rem;max-width:68ch}
footer code{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.8rem}
@media (max-width:640px){
  .wrap{padding:36px 18px 80px}
  .stage{padding-left:14px}
}
"""

#: The loop, in order. Each stage owns one hue, used only on blocks that carry
#: its name -- so the colour reinforces the label rather than replacing it.
_STAGES = [
    ("dept-marketing", "Marketing", "rings the phone", "s1"),
    ("dept-intake", "Intake", "catches it", "s2"),
    ("dept-sales", "Sales", "books it", "s3"),
    ("dept-ops", "Operations", "does the job", "s4"),
    ("dept-finance", "Finance", "gets you paid", "s5"),
]
_STAGE_TOKEN = {dept: token for dept, _, _, token in _STAGES}

_EXECUTOR_LABEL = {"ai": "AI agent", "person": "Person", "automation": "Automation"}


def _esc(text: Any) -> str:
    from html import escape

    return escape(str(text), quote=True)


def _chip(executor: str, label: str) -> str:
    return (
        f'<span class="chip c-{executor}"><span class="dot"></span>'
        f"{_esc(label)}</span>"
    )


def _loop_svg(blueprint: dict) -> str:
    """The thesis as a picture: five stages, and the leg that closes the loop.

    Drawn rather than described because the return leg -- collected cash
    setting next week's budget -- is the one relationship prose keeps losing.
    """
    names = {d["id"]: d for d in blueprint.get("departments", [])}
    x, y, w, h, gap = 14, 16, 168, 84, 22
    parts = []
    for i, (dept_id, short, caption, token) in enumerate(_STAGES):
        if dept_id not in names:
            continue
        left = x + i * (w + gap)
        parts.append(
            f'<g><rect x="{left}" y="{y}" width="{w}" height="{h}" rx="10" '
            f'fill="var(--{token}-bg)"/>'
            f'<rect x="{left}" y="{y}" width="{w}" height="4" rx="2" '
            f'fill="var(--{token})"/>'
            f'<text x="{left + w / 2}" y="{y + 44}" text-anchor="middle" '
            f'fill="var(--ink)" font-size="17" font-weight="600" '
            f'font-family="Newsreader,Georgia,serif">{_esc(short)}</text>'
            f'<text x="{left + w / 2}" y="{y + 66}" text-anchor="middle" '
            f'fill="var(--ink-2)" font-size="12.5">{_esc(caption)}</text></g>'
        )
        if i:
            parts.append(
                f'<path d="M{left - gap + 3} {y + h / 2} h{gap - 12}" '
                f'stroke="var(--ink-3)" stroke-width="1.5" fill="none" '
                f'marker-end="url(#tip)"/>'
            )
    first = x + w / 2
    last = x + 4 * (w + gap) + w / 2
    base = y + h + 48
    parts.append(
        f'<path d="M{last} {y + h + 4} V{base} H{first} V{y + h + 3}" '
        f'stroke="var(--s5)" stroke-width="2.5" fill="none" '
        f'marker-end="url(#tip-loop)"/>'
        f'<text x="{(first + last) / 2}" y="{base + 21}" text-anchor="middle" '
        f'fill="var(--s5-ink)" font-size="13" font-weight="600">'
        f"collected cash sets next week&#39;s ad budget</text>"
        f'<text x="{(first + last) / 2}" y="{base + 38}" text-anchor="middle" '
        f'fill="var(--ink-3)" font-size="11.5">'
        f"the two ends of the loop share a colour because they are the same "
        f"money</text>"
    )
    platform = names.get("dept-platform")
    if platform:
        top = base + 54
        width = 4 * (w + gap) + w
        parts.append(
            f'<rect x="{x}" y="{top}" width="{width}" height="44" rx="10" '
            f'fill="none" stroke="var(--rule)" stroke-width="1.5" '
            f'stroke-dasharray="5 4"/>'
            f'<text x="{x + width / 2}" y="{top + 27}" text-anchor="middle" '
            f'fill="var(--ink-3)" font-size="13" '
            f'font-family="JetBrains Mono,ui-monospace,monospace">'
            f"Claude Code over MCP &#183; pricing.yaml &#183; rules.md &#183; "
            f"bench.csv</text>"
        )
    return (
        '<svg viewBox="0 0 960 276" role="img" aria-label="Five stages in a '
        "row -- marketing, intake, sales, operations, finance -- with an arrow "
        "from finance back to marketing labelled collected cash sets next "
        'week&#39;s ad budget, over a shared integration layer">'
        "<defs>"
        '<marker id="tip" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" '
        'markerHeight="6" orient="auto"><path d="M0 0 L8 4 L0 8 z" '
        'fill="var(--ink-3)"/></marker>'
        '<marker id="tip-loop" viewBox="0 0 8 8" refX="7" refY="4" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M0 0 L8 4 L0 8 z" fill="var(--s5)"/></marker>'
        "</defs>" + "".join(parts) + "</svg>"
    )


def _steps_table(proc: dict) -> str:
    rows = []
    for step in proc.get("steps", []):
        kind = step.get("kind", "task")
        executor = step.get("executor", "person")
        detail = []
        if kind == "decision":
            for b in step.get("branches", []) or []:
                detail.append(
                    f'<span class="arrow">{_esc(b["label"])} &rarr; '
                    f'{_esc(b["to"])}</span>'
                )
        elif kind == "goto":
            detail.append(
                f'<span class="arrow">jumps to {_esc(step.get("goto"))}</span>'
            )
        elif kind == "delay":
            detail.append(f"waits {_esc(step.get('duration', '?'))}")
        if step.get("commits"):
            detail.append('<span class="arrow">commits the business</span>')
        cost = ""
        if executor == "person" and step.get("duration") and step.get("frequency"):
            cost = f"{_esc(step['duration'])} &times; {_esc(step['frequency'])}/mo"
        klass = ' class="is-person"' if executor == "person" else ""
        rows.append(
            f"<tr{klass}><td class=\"num\">{_esc(step['number'])}</td>"
            f"<td>{_esc(step['title'])}"
            + (
                f'<div class="sub">{" &nbsp;·&nbsp; ".join(detail)}</div>'
                if detail
                else ""
            )
            + f"</td><td>{_chip(executor, step.get('owner', ''))}</td>"
            + f'<td class="cost">{cost}</td></tr>'
        )
    return (
        '<div class="scroll" tabindex="0"><table><thead><tr><th></th><th>Step</th>'
        "<th>Who runs it</th><th>Person cost</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


_STATUS = {
    "gated": ("st-good", "&#10003;", "gated"),
    "person_commits": ("st-good", "&#10003;", "a person does it"),
    "partial_gate": ("st-warn", "!", "threshold"),
    "automation_commits": ("st-warn", "!", "check the trigger"),
    "ungated_ai_commit": ("st-bad", "&#10005;", "ungated"),
}


def _status(code: str) -> str:
    klass, glyph, word = _STATUS.get(code, ("st-warn", "?", code))
    return f'<span class="st {klass}"><span class="gl">{glyph}</span>{word}</span>'


def build_page(blueprint: dict, out: Path) -> Path:
    """Render the architecture as one standalone light page.

    Everything here is read off the blueprint, including the counts. A page
    that carries its own numbers is a second copy of the map, and the two stop
    agreeing on the first edit -- which is the failure the derived roster
    exists to prevent, applied to the readable artefact.
    """
    meta = blueprint["meta"]
    found = roster(blueprint)
    load = monthly_load(blueprint)
    findings = audit_gates(blueprint)
    committing = [f for f in findings if f["code"] != "unmarked_touch"]
    grouped = fanout(blueprint)
    planned = sum(len(v) for v in grouped.values())
    procs = {p["id"]: p for p in blueprint.get("processes", [])}
    tools_by_id = {t["id"]: t for t in blueprint.get("tools", [])}
    person_steps = [
        (proc, step)
        for proc, step in work_steps(blueprint)
        if step.get("executor") == "person"
    ]
    gate_steps = [s for _, s in person_steps if s.get("kind") == "decision"]
    ops_hours = load["by_department"].get("Operations & Delivery", 0) / 60
    admin_hours = load["hours"] - ops_hours

    P = []
    add = P.append

    add(f"<title>{_esc(meta['name'])}</title>")
    add('<link rel="preconnect" href="https://fonts.googleapis.com">')
    add('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    add(
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=Newsreader:opsz,wght@6..72,500;6..72,600&"
        "family=Public+Sans:wght@400;500;600&"
        'family=JetBrains+Mono:wght@400;500;600&display=swap">'
    )
    add(f"<style>{_PAGE_CSS}</style>")
    add('<div class="wrap stack g40">')

    # ---------------------------------------------------------------- hero
    add(
        '<header class="stack g16">'
        '<div class="eyebrow">Reference architecture</div>'
        f"<h1>{_esc(meta['name'])}</h1>"
        '<p class="lede">A local service business is not a funnel. It is a '
        "loop: marketing rings the phone, intake catches it, sales books it, "
        "operations does the job, finance gets you paid &mdash; and that money "
        "decides next week&#39;s ads.</p>"
        "</header>"
    )
    add(f'<div class="figure">{_loop_svg(blueprint)}</div>')

    add(
        '<div class="tiles">'
        f'<div class="tile"><span class="eyebrow">Agent roles</span>'
        f'<span class="n" style="color:var(--ai)">{len(found["ai"])}</span>'
        f'<span class="s">derived from the map, not kept in a list</span></div>'
        f'<div class="tile"><span class="eyebrow">Person steps</span>'
        f'<span class="n" style="color:var(--person)">{len(person_steps)}</span>'
        f'<span class="s">{len(gate_steps)} of them approval gates on money '
        f"or a customer</span></div>"
        f'<div class="tile"><span class="eyebrow">Person hours</span>'
        f'<span class="n">{load["hours"]:,.0f}</span>'
        f'<span class="s">a month &mdash; {ops_hours:,.0f} of it the job '
        f"itself, {admin_hours:,.0f} everything else</span></div>"
        f'<div class="tile"><span class="eyebrow">At full fan-out</span>'
        f'<span class="n" style="color:var(--ink-3)">'
        f'{len(found["ai"]) + planned}</span>'
        f'<span class="s">{len(found["ai"])} built, {planned} backlog and '
        f"none of them running</span></div>"
        "</div>"
    )

    add(
        '<div class="legend">'
        "<b>Who runs a step</b>"
        + "".join(
            f'<span class="k">{_chip(k, _EXECUTOR_LABEL[k])} {gloss}</span>'
            for k, gloss in (
                ("ai", f"{len(found['ai'])} roles"),
                ("person", "the owner"),
                ("automation", f"{len(found['automation'])} integrations"),
            )
        )
        + "</div>"
    )

    # ------------------------------------------------------ the human gates
    add(
        "<section><div class='stack g8'>"
        '<div class="eyebrow" style="color:var(--person)">The rule</div>'
        "<h2>AI moves information. You move money and risk.</h2>"
        f'<p class="note">Every step below is a human one. {len(gate_steps)} '
        "are approval gates on money or a customer relationship; the rest is "
        "the work itself and the judgement immediately around it. None of "
        "them is here because it was hard to automate.</p></div>"
        '<div class="grid2">'
        + "".join(
            f'<div class="card gate"><div class="stack g8">'
            f"<h3>{_esc(step['title'])}</h3>"
            + (
                '<span class="st st-person">'
                '<span class="gl">&#9679;</span>approval gate</span>'
                if step.get("kind") == "decision"
                else ""
            )
            + f'<div class="meta">{_esc(proc["id"])} &#183; step '
            f"{_esc(step['number'])}"
            + (
                f" &#183; {_esc(step['duration'])} &times; "
                f"{_esc(step['frequency'])}/mo"
                if step.get("duration") and step.get("frequency")
                else ""
            )
            + "</div>"
            + "<p>"
            + _esc(
                (step.get("notes", "").split(chr(10))[0] or "").removeprefix(
                    "Purpose: "
                )
            )
            + "</p></div></div>"
            for proc, step in person_steps
        )
        + "</div></section>"
    )

    # ------------------------------------------------------------ the gates
    add(
        "<section><div class='stack g8'>"
        '<div class="eyebrow">The check</div>'
        "<h2>What can commit the business</h2>"
        '<p class="note">A step that spends money, moves cash or binds the '
        "business to a contract says so in the map. Every one of them run by "
        "an agent has a person immediately in front of it &mdash; that is a "
        "property <code>ai_company.py gates</code> tests, and a test that can "
        "fail.</p></div>"
        '<div class="scroll" tabindex="0"><table><thead><tr><th></th>'
        "<th>Step</th><th>Who runs it</th><th>Gate</th>"
        "</tr></thead><tbody>"
        + "".join(
            f"<tr><td class=\"num\">{_esc(f['step'])}</td>"
            f"<td>{_esc(f['title'])}<div class=\"sub\">{_esc(f['process'])}"
            f"</div></td>"
            f"<td>{_chip(_owner_executor(f, blueprint), f['owner'])}</td>"
            f"<td>{_status(f['code'])}"
            f"<div class=\"sub\">{_esc(f['message'])}</div></td></tr>"
            for f in committing
        )
        + "</tbody></table></div></section>"
    )

    # ------------------------------------------------------- stage by stage
    add(
        "<section><div class='stack g8'>"
        '<div class="eyebrow">The loop</div>'
        "<h2>Stage by stage</h2>"
        '<p class="note">Six processes, every step carrying who runs it. The '
        "person rows are tinted, so the labour bill is visible by scanning "
        "rather than by reading.</p></div>"
    )
    for dept in blueprint.get("departments", []):
        if not dept.get("processes"):
            continue
        token = _STAGE_TOKEN.get(dept["id"], "s1")
        add(
            f'<div class="stage" style="--stage:var(--{token});'
            f'--stage-ink:var(--{token}-ink)">'
            f'<div class="stage-head">'
            f'<div class="eyebrow">{_esc(dept["name"])}</div>'
            f'<p class="note">{_esc(dept.get("description", ""))}</p></div>'
        )
        for proc_id in dept["processes"]:
            proc = procs.get(proc_id)
            if not proc:
                continue
            add(
                f'<div class="stack g12"><div class="stack g8">'
                f"<h3>{_esc(proc['name'])}</h3>"
                f'<div class="meta">{_esc(proc.get("frequency", ""))}</div>'
                f'<p class="note">{_esc(proc.get("description", ""))}</p></div>'
                + _steps_table(proc)
                + "</div>"
            )
        add("</div>")
    add("</section>")

    # ------------------------------------------------------------ the files
    platform = next(
        (d for d in blueprint.get("departments", []) if d["id"] == "dept-platform"),
        None,
    )
    if platform:
        chips = [
            f'<div class="card file"><code>{_esc(tools_by_id[t]["name"])}</code>'
            f'<p>{_esc(tools_by_id[t]["purpose"])}</p></div>'
            for t in platform.get("tools", [])
            if t in tools_by_id
            and str(tools_by_id[t].get("category", "")).startswith("Config")
        ]
        add(
            "<section><div class='stack g8'>"
            '<div class="eyebrow">Underneath</div>'
            "<h2>Three plain files</h2>"
            f'<p class="note">{_esc(platform.get("description", ""))}</p></div>'
            f'<div class="grid3">{"".join(chips)}</div>'
            '<p class="note">Every threshold in this architecture is a number '
            "in one of these rather than a judgement an agent makes. That is "
            "what turns &#8220;is this quote too big to send unattended&#8221; "
            "from a prompt-engineering problem into a comparison.</p></section>"
        )

    # -------------------------------------------------------- the other 33
    add(
        "<section><div class='stack g8'>"
        '<div class="eyebrow">Not built</div>'
        f"<h2>The other {planned}</h2>"
        f'<p class="note">{len(found["ai"])} roles run the whole loop. The '
        f"{planned} below are what each would split into if volume ever "
        "justified it, and every one names the volume that would. They are "
        "backlog, all of them. A committed list of named agents that reads as "
        "built is how a customer-facing promise gets wired to something that "
        "is not there.</p></div>"
        '<div class="grid2">'
        + "".join(
            f'<div class="card"><div class="stack g8">'
            f"<h3>{_esc(role)}</h3>"
            + "".join(
                f'<div class="stack g8" style="padding-top:4px">'
                f'<div>{_chip("backlog", i["title"])}'
                f'<div class="sub">{_esc(i["description"])}</div></div></div>'
                for i in items
            )
            + "</div></div>"
            for role, items in grouped.items()
        )
        + "</div></section>"
    )

    # ------------------------------------------------------- what it cannot
    add(
        "<section>"
        '<div class="callout">'
        '<div class="eyebrow">What this page does not know</div>'
        "<p>Every KPI on this map reads <strong>unmeasured</strong>, and the "
        "volumes are illustrative for a one-van shop. There is no "
        "current-state map behind it, so there is no hours-returned figure "
        "&mdash; <code>ai_company.py hours</code> refuses to produce one "
        "without a baseline. Tool costs are absent on purpose: phase 1 of the "
        "readiness framework fills them from real invoices, and a placeholder "
        "in a committed reference gets read as a fact.</p>"
        "</div></section>"
    )

    add(
        f"<footer>Generated from <code>docs/{REFERENCE.name}</code> by "
        f"<code>tools/ai_company.py page</code> &mdash; the counts, the roster "
        f"and the gate verdicts are all read off that file. Edit the "
        f"blueprint, not this page. Version "
        f"{_esc(meta.get('version', '?'))}.</footer>"
    )
    add("</div>")

    out = Path(out)
    out.write_text("\n".join(P) + "\n", encoding="utf-8")
    return out


def _owner_executor(finding: dict, blueprint: dict) -> str:
    """The executor of the step a finding came from, for its chip."""
    for proc in blueprint.get("processes", []):
        if proc["id"] != finding["process"]:
            continue
        for step in proc.get("steps", []):
            if step.get("number") == finding["step"]:
                return step.get("executor", "person")
    return "person"


# --- CLI --------------------------------------------------------------------


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai_company.py",
        description="Check the one-person AI company architecture "
        "(docs/one-person-ai-company.md) against its blueprint.",
    )
    parser.add_argument(
        "--blueprint",
        type=Path,
        default=REFERENCE,
        help="blueprint to read (default: the reference architecture)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("roster", help="who runs what, derived from the map")
    p.add_argument(
        "--expansion",
        action="store_true",
        help="also list the backlog sub-agents each role would split into",
    )

    p = sub.add_parser(
        "gates", help="convict AI steps that commit money with no person in front"
    )
    p.add_argument(
        "--committing",
        help="comma-separated tool categories that commit the business "
        f"(default: {','.join(COMMITTING_CATEGORIES)})",
    )

    p = sub.add_parser("hours", help="person-hours a month, by department")
    p.add_argument(
        "--baseline",
        type=Path,
        help="a current-state blueprint, to report hours returned against",
    )

    p = sub.add_parser("loop", help="does a job pay back what it cost to win it")
    p.add_argument("--spend", type=float, required=True, help="ad spend for the period")
    p.add_argument("--leads", type=float, required=True)
    p.add_argument(
        "--jobs", type=float, required=True, help="jobs won from those leads"
    )
    p.add_argument("--job-value", type=float, required=True, help="average job value")
    p.add_argument(
        "--margin", type=float, required=True, help="gross margin, e.g. 0.42"
    )
    p.add_argument(
        "--cycle-days",
        type=float,
        help="lead to collected cash; without it no days figure is produced",
    )
    p.add_argument(
        "--rule-payback-jobs",
        type=float,
        help="propose next period's budget against this payback ceiling",
    )
    p.add_argument("--ceiling", type=float, help="most the budget may become")
    p.add_argument("--step", type=float, default=0.20, help="budget move size")

    p = sub.add_parser(
        "reprice", help="quoted against actual, per job type, behind a sample gate"
    )
    p.add_argument("jobs_csv", type=Path, help="closed jobs: job_type, quoted, cost")
    p.add_argument(
        "--target-margin", type=float, required=True, help="the margin pricing assumes"
    )
    p.add_argument("--min-jobs", type=int, default=DEFAULT_MIN_JOBS)

    p = sub.add_parser("page", help="render the architecture as a standalone page")
    p.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "docs" / "one-person-ai-company.html",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "reprice":
            rows = read_jobs(args.jobs_csv)
            if not rows:
                print("error: no usable rows in that CSV", file=sys.stderr)
                return 1
            print(
                render_reprice(
                    reprice(rows, args.target_margin, args.min_jobs), args.min_jobs
                )
            )
            return 0

        if args.command == "loop":
            economics = loop_economics(
                args.spend,
                args.leads,
                args.jobs,
                args.job_value,
                args.margin,
                args.cycle_days,
            )
            budget = None
            if args.rule_payback_jobs is not None:
                budget = next_budget(
                    economics,
                    args.spend,
                    args.rule_payback_jobs,
                    args.ceiling,
                    args.step,
                )
            print(render_loop(economics, budget))
            return 0

        blueprint = load_blueprint(args.blueprint)

        if args.command == "roster":
            print(render_roster(blueprint, args.expansion))
        elif args.command == "gates":
            categories = (
                [c.strip() for c in args.committing.split(",") if c.strip()]
                if args.committing
                else COMMITTING_CATEGORIES
            )
            findings = audit_gates(blueprint, categories)
            print(render_gates(findings))
            if any(f["severity"] == "error" for f in findings):
                return 1
        elif args.command == "hours":
            baseline = (
                monthly_load(load_blueprint(args.baseline)) if args.baseline else None
            )
            print(render_hours(monthly_load(blueprint), baseline))
        elif args.command == "page":
            out = build_page(blueprint, args.out)
            print(f"Page written to {out}")
    except LoopError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # Downstream (e.g. `... | head`) closed the pipe: that is their say,
        # not our failure.
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 0
    raise SystemExit(code)

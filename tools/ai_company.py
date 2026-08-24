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
) -> List[dict]:
    """Per job type: realized margin, its interval, and whether a price may move.

    Three outcomes, and two of them are "leave it alone":

    ``too_few``       fewer than ``min_jobs`` of this type. The correct answer
                      most months, and it says how many more are needed.
    ``inside_noise``  enough jobs, but the drift interval straddles zero, so
                      the observed gap is not distinguishable from variance.
    ``reprice``       enough jobs and an interval clear of zero. Carries the
                      exact price multiplier that restores the target margin,
                      since cost is fixed and price = cost / (1 - margin).
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
            "verdict": "too_few",
            "detail": f"needs {min_jobs - n} more job(s) before a price may move",
            "price_multiplier": None,
        }
        if n >= min_jobs:
            if n >= 2:
                variance = sum((m - mean) ** 2 for m in margins) / (n - 1)
                half = t_critical(n - 1) * math.sqrt(variance / n)
            else:
                half = math.inf
            result["ci_low"] = drift - half
            result["ci_high"] = drift + half
            if result["ci_low"] <= 0 <= result["ci_high"]:
                result["verdict"] = "inside_noise"
                result["detail"] = (
                    "drift interval straddles zero — observed gap is not "
                    "distinguishable from job-to-job variance"
                )
            else:
                result["verdict"] = "reprice"
                result["price_multiplier"] = (1 - mean) / (1 - target_margin)
                way = "under" if drift < 0 else "over"
                result["detail"] = (
                    f"margin runs {abs(drift) * 100:.1f}pp {way} target across "
                    f"{n} jobs, interval clear of zero"
                )
        out.append(result)
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
    "reprice": "REPRICE",
}


def render_reprice(results: Sequence[dict], min_jobs: int) -> str:
    lines = [
        f"Sample-size gate: {min_jobs} jobs of a type before any price may move.",
        "",
        f"{'JOB TYPE':22} {'N':>3} {'MARGIN':>7} {'DRIFT':>7} {'INTERVAL':>17}  VERDICT",
    ]
    for r in results:
        if r["ci_low"] is None:
            interval = "—"
        else:
            interval = f"[{r['ci_low'] * 100:+.1f}, {r['ci_high'] * 100:+.1f}]pp"
        lines.append(
            f"{r['job_type'][:22]:22} {r['jobs']:>3} "
            f"{r['mean_margin'] * 100:>6.1f}% {r['drift'] * 100:>+6.1f}pp "
            f"{interval:>17}  {_VERDICT_LABEL[r['verdict']]}"
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
        f"{movable} of {len(results)} job type(s) cleared both gates. "
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
:root{
  --bg:#f8fafc; --fg:#0f172a; --card:#ffffff; --muted:#f1f5f9;
  --muted-fg:#475569; --border:#cbd5e1; --accent:#15803d; --accent-soft:#dcfce7;
  --warn:#b45309; --warn-soft:#fef3c7; --person:#b91c1c; --person-soft:#fee2e2;
  --ai:#1d4ed8; --ai-soft:#dbeafe; --auto:#475569; --auto-soft:#e2e8f0;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#020617; --fg:#f8fafc; --card:#0e1223; --muted:#1a1e2f;
    --muted-fg:#94a3b8; --border:#334155; --accent:#22c55e; --accent-soft:#052e16;
    --warn:#fbbf24; --warn-soft:#3f2d06; --person:#f87171; --person-soft:#450a0a;
    --ai:#60a5fa; --ai-soft:#0b2545; --auto:#94a3b8; --auto-soft:#1e293b;
  }
}
:root[data-theme="dark"]{
  --bg:#020617; --fg:#f8fafc; --card:#0e1223; --muted:#1a1e2f;
  --muted-fg:#94a3b8; --border:#334155; --accent:#22c55e; --accent-soft:#052e16;
  --warn:#fbbf24; --warn-soft:#3f2d06; --person:#f87171; --person-soft:#450a0a;
  --ai:#60a5fa; --ai-soft:#0b2545; --auto:#94a3b8; --auto-soft:#1e293b;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  line-height:1.6;font-size:16px}
.wrap{max-width:1080px;margin:0 auto;padding:48px 24px 96px}
h1,h2,h3{font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-weight:600;line-height:1.25;letter-spacing:-0.01em;text-wrap:balance}
h1{font-size:2rem;margin:0 0 8px}
h2{font-size:1.25rem;margin:56px 0 4px;padding-top:24px;border-top:1px solid var(--border)}
h3{font-size:1rem;margin:28px 0 8px}
p{margin:8px 0 16px;max-width:70ch}
.lede{font-size:1.05rem;color:var(--muted-fg);max-width:70ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:28px 0}
.tile{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px}
.tile .n{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:1.9rem;font-weight:600;
  display:block;line-height:1.1;font-variant-numeric:tabular-nums}
.tile .k{color:var(--muted-fg);font-size:.82rem;text-transform:uppercase;letter-spacing:.06em}
.tile .s{color:var(--muted-fg);font-size:.86rem;margin-top:6px;display:block}
.diagram{overflow-x:auto;margin:24px 0;background:var(--card);border:1px solid var(--border);
  border-radius:10px;padding:20px}
.diagram svg{display:block;min-width:940px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:18px 20px;margin:14px 0}
.gate{border-left:3px solid var(--person)}
.gate .where{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.8rem;
  color:var(--muted-fg)}
table{width:100%;border-collapse:collapse;font-size:.9rem}
.scroll{overflow-x:auto;margin:10px 0}
.scroll:focus-visible,.diagram:focus-visible{outline:2px solid var(--accent);
  outline-offset:2px}
th{text-align:left;font-weight:600;color:var(--muted-fg);font-size:.78rem;
  text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;
  border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--muted);vertical-align:top}
td.num{font-family:"JetBrains Mono",ui-monospace,monospace;color:var(--muted-fg);
  width:2.5rem;text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;font-size:.72rem;font-weight:600;padding:2px 7px;
  border-radius:20px;white-space:nowrap;
  font-family:"JetBrains Mono",ui-monospace,monospace}
.b-ai{background:var(--ai-soft);color:var(--ai)}
.b-person{background:var(--person-soft);color:var(--person)}
.b-automation{background:var(--auto-soft);color:var(--auto)}
.b-backlog{background:var(--warn-soft);color:var(--warn)}
.kind{color:var(--muted-fg);font-size:.78rem;
  font-family:"JetBrains Mono",ui-monospace,monospace}
.branch{color:var(--muted-fg);font-size:.82rem}
.files{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.file code{font-family:"JetBrains Mono",ui-monospace,monospace;font-weight:600;
  color:var(--accent)}
ul{max-width:70ch}
.note{background:var(--muted);border-radius:8px;padding:14px 18px;
  color:var(--muted-fg);font-size:.92rem;max-width:74ch}
footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--border);
  color:var(--muted-fg);font-size:.85rem}
"""

_STAGE_ORDER = [
    "dept-marketing",
    "dept-intake",
    "dept-sales",
    "dept-ops",
    "dept-finance",
]
_STAGE_CAPTION = {
    "dept-marketing": "rings the phone",
    "dept-intake": "catches it",
    "dept-sales": "books it",
    "dept-ops": "does the job",
    "dept-finance": "gets you paid",
}


def _loop_svg(blueprint: dict) -> str:
    by_id = {d["id"]: d for d in blueprint.get("departments", [])}
    boxes = []
    x, y, w, h, gap = 20, 30, 168, 76, 24
    for i, dept_id in enumerate(_STAGE_ORDER):
        dept = by_id.get(dept_id)
        if not dept:
            continue
        left = x + i * (w + gap)
        name = _esc(dept["name"].split(" & ")[0])
        caption = _esc(_STAGE_CAPTION.get(dept_id, ""))
        boxes.append(
            f'<rect x="{left}" y="{y}" width="{w}" height="{h}" rx="8" '
            f'fill="var(--muted)" stroke="var(--border)"/>'
            f'<text x="{left + w / 2}" y="{y + 32}" text-anchor="middle" '
            f'fill="var(--fg)" font-size="15" font-weight="600" '
            f'font-family="ui-monospace,monospace">{name}</text>'
            f'<text x="{left + w / 2}" y="{y + 54}" text-anchor="middle" '
            f'fill="var(--muted-fg)" font-size="12">{caption}</text>'
        )
        if i:
            ax = left - gap
            boxes.append(
                f'<path d="M{ax} {y + h / 2} h{gap - 7}" stroke="var(--border)" '
                f'stroke-width="2" fill="none" marker-end="url(#a)"/>'
            )
    last = x + 4 * (w + gap) + w / 2
    first = x + w / 2
    boxes.append(
        f'<path d="M{last} {y + h} V{y + h + 46} H{first} V{y + h + 8}" '
        f'stroke="var(--accent)" stroke-width="2.5" fill="none" '
        f'marker-end="url(#g)"/>'
        f'<text x="{(first + last) / 2}" y="{y + h + 64}" text-anchor="middle" '
        f'fill="var(--accent)" font-size="12.5" font-weight="600">'
        f"collected cash sets next week&#39;s ad budget</text>"
    )
    platform = by_id.get("dept-platform")
    if platform:
        top = y + h + 92
        width = 4 * (w + gap) + w
        boxes.append(
            f'<rect x="{x}" y="{top}" width="{width}" height="46" rx="8" '
            f'fill="var(--card)" stroke="var(--border)" stroke-dasharray="4 3"/>'
            f'<text x="{x + width / 2}" y="{top + 28}" text-anchor="middle" '
            f'fill="var(--muted-fg)" font-size="13" '
            f'font-family="ui-monospace,monospace">'
            f'{_esc(platform["name"])} — Claude Code over MCP, over plain files'
            f"</text>"
        )
    return (
        '<svg viewBox="0 0 980 290" role="img" '
        'aria-label="The five stages of the loop, with collected cash feeding '
        'back into the ad budget, over a shared integration layer">'
        "<defs>"
        '<marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" '
        'fill="var(--border)"/></marker>'
        '<marker id="g" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" '
        'fill="var(--accent)"/></marker>'
        "</defs>" + "".join(boxes) + "</svg>"
    )


def _esc(text: Any) -> str:
    from html import escape

    return escape(str(text), quote=True)


def _steps_table(proc: dict) -> str:
    rows = []
    for step in proc.get("steps", []):
        kind = step.get("kind", "task")
        executor = step.get("executor", "person")
        extra = []
        if kind == "decision":
            for b in step.get("branches", []) or []:
                extra.append(f"{_esc(b['label'])} &rarr; {_esc(b['to'])}")
        elif kind == "goto":
            extra.append(f"jumps to step {_esc(step.get('goto'))}")
        elif kind == "delay":
            extra.append(f"waits {_esc(step.get('duration', '?'))}")
        cost = ""
        if executor == "person" and step.get("duration") and step.get("frequency"):
            cost = f"{_esc(step['duration'])} &times; {_esc(step['frequency'])}/mo"
        rows.append(
            f'<tr><td class="num">{_esc(step["number"])}</td>'
            f'<td>{_esc(step["title"])}'
            + (
                f'<div class="branch">{" &nbsp;·&nbsp; ".join(extra)}</div>'
                if extra
                else ""
            )
            + f'</td><td><span class="badge b-{executor}">{_esc(step.get("owner", ""))}</span>'
            + (f'<div class="kind">{_esc(kind)}</div>' if kind != "task" else "")
            + f'</td><td class="kind">{cost}</td></tr>'
        )
    return (
        '<div class="scroll"><table><thead><tr><th></th><th>Step</th>'
        "<th>Who</th><th>Person cost</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def build_page(blueprint: dict, out: Path) -> Path:
    meta = blueprint["meta"]
    found = roster(blueprint)
    load = monthly_load(blueprint)
    gates = [f for f in audit_gates(blueprint) if f["severity"] in ("ok", "error")]
    grouped = fanout(blueprint)
    planned = sum(len(v) for v in grouped.values())
    procs = {p["id"]: p for p in blueprint.get("processes", [])}
    person_steps = [
        (proc, step)
        for proc, step in work_steps(blueprint)
        if step.get("executor") == "person"
    ]
    # a person step that forks the flow is an approval gate; the rest is the
    # work itself, and conflating the two overstates how much of this is
    # oversight
    gate_steps = [s for _, s in person_steps if s.get("kind") == "decision"]

    parts = [
        f"<title>{_esc(meta['name'])}</title>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@500;600"
        '&display=swap">',
        f"<style>{_PAGE_CSS}</style>",
        '<div class="wrap">',
        f"<h1>{_esc(meta['name'])}</h1>",
        '<p class="lede">A local service business is not a funnel. It is a loop: '
        "marketing rings the phone, intake catches it, sales books it, operations "
        "does the job, finance gets you paid — and that money decides next "
        "week's ads.</p>",
        '<div class="diagram">' + _loop_svg(blueprint) + "</div>",
        '<div class="tiles">',
        f'<div class="tile"><span class="k">Agent roles</span>'
        f'<span class="n">{len(found["ai"])}</span>'
        f'<span class="s">derived from the map, not a list</span></div>',
        f'<div class="tile"><span class="k">Person steps</span>'
        f'<span class="n">{len(person_steps)}</span>'
        f'<span class="s">{len(gate_steps)} of them approval gates</span></div>',
        f'<div class="tile"><span class="k">Person load</span>'
        f'<span class="n">{load["hours"]:,.0f}</span>'
        f'<span class="s">hours a month, mostly the job itself</span></div>',
        f'<div class="tile"><span class="k">At full fan-out</span>'
        f'<span class="n">{len(found["ai"]) + planned}</span>'
        f'<span class="s">{len(found["ai"])} built, {planned} backlog</span></div>',
        "</div>",
        "<h2>Where a person stays</h2>",
        "<p>Agents move information. A person moves money and risk. Every step "
        f"below is a human one: {len(gate_steps)} are approval gates on money or "
        "a customer relationship, and the rest is the work itself and the "
        "judgement immediately around it. None of them is here because it was "
        "hard to automate.</p>",
    ]
    for proc, step in person_steps:
        parts.append(
            f'<div class="card gate"><strong>{_esc(step["title"])}</strong>'
            + (
                ' <span class="badge b-person">approval gate</span>'
                if step.get("kind") == "decision"
                else ""
            )
            + f'<div class="where">{_esc(proc["id"])} · step {_esc(step["number"])}'
            + (
                f' · {_esc(step["duration"])} &times; {_esc(step["frequency"])}/mo'
                if step.get("duration") and step.get("frequency")
                else ""
            )
            + "</div>"
            + f'<p>{_esc((step.get("notes", "").split(chr(10))[0] or "").removeprefix("Purpose: "))}</p>'
            + "</div>"
        )

    parts.append("<h2>What commits the business, and what gates it</h2>")
    parts.append(
        "<p>A step that reaches payments, advertising or contracts can commit "
        "the business. Every one of them run by an agent has a person "
        "immediately in front of it — that is the property "
        "<code>ai_company.py gates</code> checks, and it is a test that can "
        "fail.</p>"
    )
    rows = "".join(
        f'<tr><td class="num">{_esc(f["step"])}</td><td>{_esc(f["title"])}'
        f'<div class="branch">{_esc(f["process"])}</div></td>'
        f'<td><span class="badge b-ai">{_esc(f["owner"])}</span></td>'
        f'<td class="kind">{_esc(f["message"])}</td></tr>'
        for f in gates
    )
    parts.append(
        '<div class="scroll"><table><thead><tr><th></th><th>Step</th>'
        "<th>Agent</th><th>Gate</th></tr></thead><tbody>"
        + rows
        + "</tbody></table></div>"
    )

    parts.append("<h2>The loop, stage by stage</h2>")
    for dept in blueprint.get("departments", []):
        if not dept.get("processes"):
            continue
        parts.append(f"<h3>{_esc(dept['name'])}</h3>")
        parts.append(f"<p>{_esc(dept.get('description', ''))}</p>")
        for proc_id in dept["processes"]:
            proc = procs.get(proc_id)
            if not proc:
                continue
            parts.append(
                f'<div class="card"><strong>{_esc(proc["name"])}</strong>'
                f'<div class="where kind">{_esc(proc.get("frequency", ""))}</div>'
                f'<p>{_esc(proc.get("description", ""))}</p>'
                + _steps_table(proc)
                + "</div>"
            )

    platform = next(
        (d for d in blueprint.get("departments", []) if d["id"] == "dept-platform"),
        None,
    )
    if platform:
        parts.append("<h2>Underneath: plain files</h2>")
        parts.append(f"<p>{_esc(platform.get('description', ''))}</p>")
        by_id = {t["id"]: t for t in blueprint.get("tools", [])}
        chips = [
            f'<div class="card file"><code>{_esc(by_id[t]["name"])}</code>'
            f'<p>{_esc(by_id[t]["purpose"])}</p></div>'
            for t in platform.get("tools", [])
            if t in by_id and by_id[t].get("category", "").startswith("Config")
        ]
        parts.append('<div class="files">' + "".join(chips) + "</div>")

    parts.append("<h2>The other thirty-three</h2>")
    parts.append(
        f"<p>{len(found['ai'])} roles are specified above and run the whole loop. "
        f"The {planned} below are what each role would split into if volume ever "
        "justified it. They are backlog, all of them, and none exists. A "
        "committed list of named agents that reads as built is how a "
        "customer-facing promise gets wired to something that is not there.</p>"
    )
    for role, items in grouped.items():
        rows = "".join(
            f"<tr><td>{_esc(i['title'])}"
            f'<div class="branch">{_esc(i["description"])}</div></td>'
            f'<td><span class="badge b-backlog">backlog</span></td></tr>'
            for i in items
        )
        parts.append(
            f'<div class="card"><strong>{_esc(role)}</strong>'
            f'<div class="scroll"><table><tbody>{rows}</tbody></table></div></div>'
        )

    parts.append("<h2>What this page does not know</h2>")
    parts.append(
        '<div class="note">Every KPI on this map reads <em>unmeasured</em>, and '
        "the volumes are illustrative for a one-van shop. There is no "
        "current-state map behind it, so there is no hours-returned figure — "
        "<code>ai_company.py hours</code> refuses to produce one without a "
        "baseline. Tool costs are absent on purpose: phase 1 of the readiness "
        "framework fills them from real invoices, and a placeholder would be "
        "read as a fact.</div>"
    )
    parts.append(
        f"<footer>Generated from <code>docs/{REFERENCE.name}</code> by "
        f"<code>tools/ai_company.py page</code>. Edit the blueprint, not this "
        f"file. Blueprint version {_esc(meta.get('version', '?'))}.</footer>"
    )
    parts.append("</div>")

    out = Path(out)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return out


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

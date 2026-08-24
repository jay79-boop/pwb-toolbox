"""The one-person AI company architecture, held to its own claims.

Three of these tests are the load-bearing ones, and each pins a way this kind
of tool goes wrong quietly:

:func:`test_audit_convicts_a_planted_ungated_commit` — an audit that only ever
    passes is indistinguishable from no audit. The reference architecture is
    supposed to be clean, so something has to prove the check can still fail;
    the planted map is that proof.
:func:`test_reprice_refuses_a_large_drift_on_too_few_jobs` — the sample gate
    only earns its place if it refuses a drift it can *see*. A gate that
    declines to reprice when the evidence is also weak is not a gate.
:func:`test_fanout_is_entirely_backlog` — the roadmap carries 33 named
    sub-agents that do not exist. One of them marked ``completed`` would make
    the page and the roster claim an agent is running that nobody built.

The duration parser is cross-checked against ``static/process-grammar.js``
rather than tested alone, for the same reason ``tests/test_option_lab.py``
prices contracts through both implementations: two parsers for one field is
only safe if a disagreement fails here instead of showing up as a load figure
that differs between this tool and flow-canvas.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.ai_company import (  # noqa: E402
    COMMITTING_CATEGORIES,
    DEFAULT_MIN_JOBS,
    LoopError,
    audit_gates,
    build_page,
    fanout,
    load_blueprint,
    loop_economics,
    monthly_load,
    next_budget,
    parse_duration,
    predecessors,
    read_jobs,
    render_hours,
    render_reprice,
    render_roster,
    reprice,
    roster,
    successors,
)
from tools.blueprint_converter import check_blueprint  # noqa: E402

REFERENCE = ROOT / "docs" / "blueprint-one-person-ai-company.json"
GRAMMAR = ROOT / "static" / "process-grammar.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


@pytest.fixture(scope="module")
def blueprint():
    return load_blueprint(REFERENCE)


# ---------------------------------------------------------------- durations

#: Every shape a blueprint or a canvas actually writes one in, plus the ones
#: that must come back unparsed rather than silently worth zero.
DURATIONS = [
    "45m",
    "90",
    "1h30m",
    "1.5h",
    "2d",
    "30 minutes",
    "1 hour",
    "2-8 hours",
    "2 to 8 hours",
    "instant",
    "immediate",
    "4 hours",
    "8 minutes",
    "1-14 days",
    "12 minutes",
    "",
    "   ",
    "a while",
    "soon",
    "3 fortnights",
    "1h 30m",
    "0.5 day",
    "15",
    "2.5",
]


def test_duration_parses_the_shapes_a_map_uses():
    assert parse_duration("45m") == 45
    assert parse_duration("1h30m") == 90
    assert parse_duration("2-8 hours") == 300  # midpoint, in minutes
    assert parse_duration("2d") == 960  # a working day is eight hours
    assert parse_duration("instant") == 0
    assert parse_duration("90") == 90  # a bare number is minutes


def test_unreadable_duration_is_none_not_zero():
    """Unpriced and visible beats free and silent."""
    for text in ("a while", "soon", "", None, "3 fortnights"):
        assert parse_duration(text) is None


@needs_node
def test_duration_matches_the_browser_parser(tmp_path):
    """Python and process-grammar.js must read a duration identically.

    They price the same maps -- this tool in the terminal, flow-canvas in its
    panel -- so a disagreement is a load figure that depends on which one you
    happened to open.
    """
    script = tmp_path / "cross.js"
    script.write_text(
        "const g = require(%s);\n"
        "const cases = JSON.parse(process.argv[2]);\n"
        "console.log(JSON.stringify(cases.map(c => g.parseDuration(c))));\n"
        % json.dumps(str(GRAMMAR)),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(script), json.dumps(DURATIONS)],
        capture_output=True,
        text=True,
        check=True,
    )
    from_js = json.loads(result.stdout)
    from_py = [parse_duration(c) for c in DURATIONS]
    assert len(from_js) == len(from_py) == len(DURATIONS)
    for case, js, py in zip(DURATIONS, from_js, from_py):
        assert js == py, f"{case!r}: node said {js!r}, python said {py!r}"


# ------------------------------------------------------------- the reference


def test_reference_blueprint_validates(blueprint):
    errors, _ = check_blueprint(blueprint)
    assert errors == []


def test_reference_flow_is_sound(blueprint):
    """Every step reachable from the trigger, and every path ends somewhere.

    A branch into a step nobody reaches, or a work step the flow falls off the
    end of, reads perfectly well in a JSON list and loses the process on any
    map built from it.
    """
    for proc in blueprint["processes"]:
        steps = proc["steps"]
        numbers = sorted(s["number"] for s in steps)
        forward = successors(steps)
        seen, stack = set(), [numbers[0]]
        while stack:
            number = stack.pop()
            if number in seen:
                continue
            seen.add(number)
            stack.extend(forward[number])
        assert seen == set(numbers), f"{proc['id']}: unreachable {set(numbers) - seen}"
        for step in steps:
            if step.get("kind", "task") == "end":
                continue
            ends_here = step.get("branches") and all(
                b["to"] == "end" for b in step["branches"]
            )
            assert (
                forward[step["number"]] or ends_here
            ), f"{proc['id']} step {step['number']} goes nowhere and is not an end"


def test_reference_has_no_ungated_ai_commit(blueprint):
    """The doctrine, as an assertion: no agent commits money on its own."""
    ungated = [f for f in audit_gates(blueprint) if f["code"] == "ungated_ai_commit"]
    assert ungated == []


def test_every_person_approval_can_refuse(blueprint):
    """An approval step with only one way out is not an approval.

    Caught in review on the supplier order, which originally approved and fell
    straight through to placing it.
    """
    for proc in blueprint["processes"]:
        for step in proc["steps"]:
            title = step.get("title", "").lower()
            if step.get("executor") != "person" or not title.startswith(
                ("approve", "review and")
            ):
                continue
            assert (
                step.get("kind") == "decision"
            ), f"{proc['id']} step {step['number']} approves but cannot refuse"
            assert len(step.get("branches", [])) >= 2


# ------------------------------------------------------------------- roster


def test_roster_is_derived_from_the_map(blueprint):
    found = roster(blueprint)
    named = {
        step["owner"]
        for proc in blueprint["processes"]
        for step in proc["steps"]
        if step.get("executor") == "ai"
        and step.get("kind", "task") in ("task", "decision")
    }
    assert set(found["ai"]) == named
    assert found["person"], "a map with no person in it is the failure case"


def test_roster_ignores_flow_only_steps():
    """An agent that only ever appears on a go-to is not a role."""
    blueprint = {
        "meta": {"name": "t", "owner": "o"},
        "departments": [],
        "tools": [],
        "processes": [
            {
                "id": "p",
                "name": "P",
                "category": "internal",
                "owner": "o",
                "steps": [
                    {
                        "number": 1,
                        "title": "Work",
                        "executor": "ai",
                        "owner": "Real agent",
                    },
                    {
                        "number": 2,
                        "title": "Go to: Work",
                        "kind": "goto",
                        "goto": 1,
                        "executor": "ai",
                        "owner": "Phantom agent",
                    },
                ],
            }
        ],
    }
    assert list(roster(blueprint)["ai"]) == ["Real agent"]


def test_fanout_is_entirely_backlog(blueprint):
    """The other 33 do not exist, and nothing in the file may say they do."""
    grouped = fanout(blueprint)
    items = [i for group in grouped.values() for i in group]
    assert len(items) == 33
    assert {i["status"] for i in items} == {"backlog"}
    # every sub-agent hangs off a role that actually exists
    assert set(grouped) == set(roster(blueprint)["ai"])


def test_roster_text_states_built_against_planned(blueprint):
    text = render_roster(blueprint)
    assert "7 role(s) specified, 33 backlog sub-agent(s) — 40 at full fan-out" in text


# -------------------------------------------------------------- gate audit


def _map(steps, tools=None):
    return {
        "meta": {"name": "t", "owner": "o"},
        "departments": [],
        "tools": tools
        or [
            {
                "id": "tool-pay",
                "name": "Payments",
                "category": "Payments",
                "purpose": "p",
                "owner": "o",
            }
        ],
        "processes": [
            {
                "id": "p",
                "name": "P",
                "category": "internal",
                "owner": "o",
                "steps": steps,
            }
        ],
    }


def test_audit_convicts_a_planted_ungated_commit():
    """The check has to be able to fail, or its silence means nothing."""
    findings = audit_gates(
        _map(
            [
                {"number": 1, "title": "Decide", "executor": "ai", "owner": "A"},
                {
                    "number": 2,
                    "title": "Charge the card",
                    "executor": "ai",
                    "owner": "A",
                    "tools": ["tool-pay"],
                    "commits": True,
                },
            ]
        )
    )
    convicted = [f for f in findings if f["code"] == "ungated_ai_commit"]
    assert [f["step"] for f in convicted] == [2]
    assert convicted[0]["severity"] == "error"


def test_audit_clears_a_commit_a_person_hands_over():
    findings = audit_gates(
        _map(
            [
                {
                    "number": 1,
                    "title": "Approve it",
                    "executor": "person",
                    "owner": "Owner",
                },
                {
                    "number": 2,
                    "title": "Charge the card",
                    "executor": "ai",
                    "owner": "A",
                    "tools": ["tool-pay"],
                    "commits": True,
                },
            ]
        )
    )
    assert [f["code"] for f in findings] == ["gated"]


def test_audit_names_a_threshold_bypass():
    """Some paths gated and some not is a limit -- visible, not silent."""
    findings = audit_gates(
        _map(
            [
                {
                    "number": 1,
                    "title": "Over the limit?",
                    "executor": "ai",
                    "owner": "A",
                    "kind": "decision",
                    "branches": [
                        {"label": "Under", "to": 3},
                        {"label": "Over", "to": 2},
                    ],
                },
                {
                    "number": 2,
                    "title": "Approve it",
                    "executor": "person",
                    "owner": "Owner",
                },
                {
                    "number": 3,
                    "title": "Charge the card",
                    "executor": "ai",
                    "owner": "A",
                    "tools": ["tool-pay"],
                    "commits": True,
                },
            ]
        )
    )
    partial = [f for f in findings if f["code"] == "partial_gate"]
    assert [f["step"] for f in partial] == [3]
    assert "bypassed from step 1" in partial[0]["message"]


def test_reading_a_committing_tool_is_not_committing():
    """Reading Stripe and charging through it are the same tool.

    The unmarked step is raised to confirm, never convicted -- an audit that
    flags every read of a payment system is an audit people stop reading.
    """
    findings = audit_gates(
        _map(
            [
                {
                    "number": 1,
                    "title": "Has it been paid?",
                    "executor": "ai",
                    "owner": "A",
                    "tools": ["tool-pay"],
                },
            ]
        )
    )
    assert [f["code"] for f in findings] == ["unmarked_touch"]
    assert findings[0]["severity"] == "info"


def test_committing_categories_are_overridable():
    steps = [
        {
            "number": 1,
            "title": "Post an ad",
            "executor": "ai",
            "owner": "A",
            "tools": ["tool-ads"],
            "commits": True,
        },
    ]
    tools = [
        {
            "id": "tool-ads",
            "name": "Ads",
            "category": "Advertising",
            "purpose": "p",
            "owner": "o",
        }
    ]
    assert (
        audit_gates(_map(steps, tools), ["Payments"])[0]["code"] == "ungated_ai_commit"
    )
    assert "Advertising" in COMMITTING_CATEGORIES


# ------------------------------------------------------------------- graph


def test_predecessors_follow_branches_and_gotos():
    steps = [
        {
            "number": 1,
            "title": "Fork",
            "kind": "decision",
            "branches": [{"label": "a", "to": 2}, {"label": "b", "to": 4}],
        },
        {"number": 2, "title": "Fix"},
        {"number": 3, "title": "Back", "kind": "goto", "goto": 1},
        {"number": 4, "title": "Done", "kind": "end"},
    ]
    assert successors(steps) == {1: [2, 4], 2: [3], 3: [1], 4: []}
    assert predecessors(steps) == {1: [3], 2: [1], 3: [2], 4: [1]}


def test_a_branch_to_end_carries_no_successor():
    steps = [
        {
            "number": 1,
            "title": "Spam?",
            "kind": "decision",
            "branches": [{"label": "Real", "to": 2}, {"label": "Spam", "to": "end"}],
        },
        {"number": 2, "title": "Carry on"},
    ]
    assert successors(steps)[1] == [2]


# -------------------------------------------------------------------- hours


def test_monthly_load_counts_only_people(blueprint):
    load = monthly_load(blueprint)
    assert load["unpriced"] == []
    assert load["minutes"] > 0
    # the job itself dominates, and the map is only honest if it says so
    assert load["by_department"]["Operations & Delivery"] > load["minutes"] / 2


def test_monthly_load_names_an_unpriced_step():
    blueprint = _map(
        [
            {
                "number": 1,
                "title": "Do it",
                "executor": "person",
                "owner": "Owner",
                "duration": "10 minutes",
            },
            {
                "number": 2,
                "title": "Do it again",
                "executor": "person",
                "owner": "Owner",
                "duration": "5 minutes",
                "frequency": 4,
            },
        ]
    )
    load = monthly_load(blueprint)
    assert load["minutes"] == 20
    assert [u["step"] for u in load["unpriced"]] == [1]


def test_hours_refuses_a_returned_figure_without_a_baseline(blueprint):
    text = render_hours(monthly_load(blueprint))
    assert "No baseline given" in text
    assert "returned" not in text.split("No baseline given")[0]


def test_hours_reports_a_return_against_a_baseline(blueprint):
    target = monthly_load(blueprint)
    baseline = dict(target, minutes=target["minutes"] + 600)
    text = render_hours(target, baseline)
    assert "Hours returned" in text
    assert "10.0 h/mo" in text


def test_hours_says_added_when_the_target_costs_more(blueprint):
    target = monthly_load(blueprint)
    baseline = dict(target, minutes=target["minutes"] - 60)
    assert "Hours ADDED" in render_hours(target, baseline)


# ---------------------------------------------------------- loop economics


def test_loop_economics_arithmetic():
    e = loop_economics(
        spend=2400, leads=120, jobs=38, job_value=1450, margin=0.42, cycle_days=24
    )
    assert e["cost_per_lead"] == pytest.approx(20.0)
    assert e["cost_per_job"] == pytest.approx(2400 / 38)
    assert e["contribution_per_job"] == pytest.approx(609.0)
    assert e["payback_jobs"] == pytest.approx((2400 / 38) / 609.0)
    assert e["payback_days"] == pytest.approx(e["payback_jobs"] * 24)


def test_no_cycle_days_means_no_days_figure():
    e = loop_economics(spend=100, leads=10, jobs=5, job_value=500, margin=0.4)
    assert e["payback_days"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(spend=100, leads=0, jobs=1, job_value=500, margin=0.4),
        dict(spend=100, leads=5, jobs=9, job_value=500, margin=0.4),
        dict(spend=100, leads=10, jobs=5, job_value=500, margin=1.4),
        dict(spend=100, leads=10, jobs=5, job_value=0, margin=0.4),
    ],
)
def test_loop_refuses_impossible_inputs(kwargs):
    with pytest.raises(LoopError):
        loop_economics(**kwargs)


def test_budget_raises_when_the_loop_clears_and_holds_at_the_ceiling():
    e = loop_economics(spend=2400, leads=120, jobs=38, job_value=1450, margin=0.42)
    proposal = next_budget(e, 2400, rule_payback_jobs=0.35, ceiling=2500)
    assert proposal["clears_rule"] and proposal["direction"] == "raise"
    assert proposal["proposed"] == 2500 and proposal["capped_by_ceiling"]


def test_budget_cuts_when_payback_misses_the_rule():
    e = loop_economics(spend=9000, leads=120, jobs=38, job_value=1450, margin=0.42)
    proposal = next_budget(e, 9000, rule_payback_jobs=0.35)
    assert not proposal["clears_rule"] and proposal["direction"] == "cut"
    assert proposal["proposed"] == pytest.approx(7200.0)


# ---------------------------------------------------------------- repricing


def _jobs(job_type, n, quoted, margin):
    """n jobs of one type at an exact realized margin."""
    return [
        {"job_type": job_type, "quoted": quoted, "cost": quoted * (1 - margin)}
        for _ in range(n)
    ]


def _spread(job_type, quoted, margins):
    return [
        {"job_type": job_type, "quoted": quoted, "cost": quoted * (1 - m)}
        for m in margins
    ]


def test_reprice_catches_a_planted_drift():
    rows = _spread(
        "Bathroom", 1400, [0.28, 0.31, 0.29, 0.30, 0.32, 0.27, 0.30, 0.29, 0.31, 0.30]
    )
    (result,) = reprice(rows, target_margin=0.42)
    assert result["verdict"] == "reprice"
    assert result["drift"] < 0
    assert result["ci_high"] < 0


def test_reprice_refuses_a_large_drift_on_too_few_jobs():
    """The gate is only a gate if it refuses evidence it can see.

    Four jobs at 20% against a 42% target is a 22-point gap and still not
    enough to move a price on: a one-van shop's monthly job count per type is
    small enough that a run of four hard jobs is an ordinary week.
    """
    rows = _spread("Rewire", 2600, [0.18, 0.22, 0.19, 0.21])
    (result,) = reprice(rows, target_margin=0.42)
    assert result["verdict"] == "too_few"
    assert result["price_multiplier"] is None
    assert "4 more job" in result["detail"]


def test_reprice_calls_a_noisy_at_target_type_noise():
    rows = _spread(
        "Boiler",
        900,
        [0.30, 0.55, 0.38, 0.48, 0.35, 0.50, 0.41, 0.44, 0.33, 0.52],
    )
    (result,) = reprice(rows, target_margin=0.42)
    assert result["verdict"] == "inside_noise"
    assert result["ci_low"] <= 0 <= result["ci_high"]
    assert result["price_multiplier"] is None


def test_price_multiplier_restores_the_target_margin():
    """Applying the proposal must actually land on the target, not near it."""
    quoted, target = 1400.0, 0.42
    rows = _spread("Bathroom", quoted, [0.28, 0.31, 0.29, 0.30, 0.32, 0.27, 0.30, 0.33])
    (result,) = reprice(rows, target_margin=target)
    new_price = quoted * result["price_multiplier"]
    repriced = [(new_price - r["cost"]) / new_price for r in rows]
    assert sum(repriced) / len(repriced) == pytest.approx(target)


def test_reprice_gate_floor_is_configurable():
    rows = _spread("Rewire", 2600, [0.18, 0.22, 0.19, 0.21])
    assert reprice(rows, 0.42, min_jobs=4)[0]["verdict"] == "reprice"
    assert reprice(rows, 0.42, min_jobs=DEFAULT_MIN_JOBS)[0]["verdict"] == "too_few"


def test_reprice_rejects_an_impossible_target():
    with pytest.raises(LoopError):
        reprice(_jobs("A", 10, 100, 0.3), target_margin=1.5)


def test_reprice_text_counts_what_moved():
    rows = _spread("Bathroom", 1400, [0.30] * 10) + _spread("Rewire", 2600, [0.20] * 4)
    text = render_reprice(reprice(rows, 0.42), DEFAULT_MIN_JOBS)
    assert "1 of 2 job type(s) cleared both gates" in text
    assert "TOO FEW" in text


def test_read_jobs_accepts_the_column_names_an_export_actually_uses(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(
        "type,price,actual_cost\nBathroom,1400,980\nRewire,,500\nBathroom,1400,1000\n",
        encoding="utf-8",
    )
    rows = read_jobs(csv_path)
    assert [r["job_type"] for r in rows] == ["Bathroom", "Bathroom"]
    assert rows[0]["quoted"] == 1400 and rows[0]["cost"] == 980


# --------------------------------------------------------------------- page


def test_page_renders_from_the_blueprint(tmp_path, blueprint):
    out = build_page(blueprint, tmp_path / "page.html")
    html = out.read_text(encoding="utf-8")
    assert "<title>" in html
    for tag in ("div", "table", "tr", "td", "p"):
        opened = html.count(f"<{tag} ") + html.count(f"<{tag}>")
        assert opened == html.count(f"</{tag}>"), f"unbalanced <{tag}>"
    # the numbers on the page are the derived ones, not typed ones
    assert ">7</span>" in html  # agent roles
    assert "7 built, 33 backlog" in html
    assert html.count('class="badge b-backlog">backlog</span>') == 33


def test_page_says_what_it_does_not_know(tmp_path, blueprint):
    html = build_page(blueprint, tmp_path / "page.html").read_text(encoding="utf-8")
    assert "unmeasured" in html
    assert "refuses to produce one without a" in html

"""Tests for tools/blueprint_converter.py.

Two things are pinned here. First, that a blueprint survives a trip through
Excel: an earlier version of the converter wrote no Steps sheet at all, so
``json-to-xlsx`` followed by ``xlsx-to-json`` silently deleted every step of
every process. Second, that the branch grammar validates — a branch pointing
at a step that does not exist reads fine in a list and loses the flow on any
map built from it.
"""

import copy
import json
from pathlib import Path

import pytest

from tools.blueprint_converter import (
    LOOP_BACK_LIMIT,
    check_blueprint,
    check_process,
    format_branches,
    json_to_xlsx,
    parse_branches,
    row_to_step,
    step_to_row,
    xlsx_to_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "docs" / "blueprint-example.json"


@pytest.fixture
def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _minimal(steps):
    """A valid one-process blueprint wrapped around the given steps."""
    return {
        "meta": {"name": "Test", "owner": "Owner"},
        "departments": [{"id": "d1", "name": "Dept", "owner": "Owner"}],
        "processes": [
            {
                "id": "p1",
                "name": "Process",
                "category": "internal",
                "owner": "Owner",
                "steps": steps,
            }
        ],
        "tools": [],
    }


# --------------------------------------------------------------- branch cells


def test_format_branches_renders_one_cell():
    branches = [{"label": "Approved", "to": 3}, {"label": "Rejected", "to": "end"}]
    assert format_branches(branches) == "Approved > 3; Rejected > end"


def test_format_branches_of_nothing_is_empty():
    assert format_branches(None) == ""
    assert format_branches([]) == ""


def test_parse_branches_round_trips_format():
    branches = [{"label": "Over $10k", "to": 7}, {"label": "Under $10k", "to": "end"}]
    assert parse_branches(format_branches(branches)) == branches


def test_parse_branches_skips_unreadable_pieces():
    assert parse_branches("Approved > 3; nonsense; > 9; Rejected > end") == [
        {"label": "Approved", "to": 3},
        {"label": "Rejected", "to": "end"},
    ]


def test_parse_branches_keeps_an_unusable_destination_verbatim():
    # dropping it here would hide the mistake; validation names it instead
    assert parse_branches("Approved > later") == [{"label": "Approved", "to": "later"}]


def test_parse_branches_of_blank_cells():
    assert parse_branches(None) == []
    assert parse_branches("") == []


# ------------------------------------------------------------------ step rows


def test_step_row_round_trips_every_field():
    step = {
        "number": 3,
        "title": "Did the signal pass review?",
        "kind": "decision",
        "executor": "person",
        "owner": "Execution Lead",
        "duration": "2 minutes",
        "frequency": 40,
        "tools": ["tool-slack", "tool-ib"],
        "branches": [
            {"label": "Approved", "to": 4},
            {"label": "Rejected", "to": "end"},
        ],
        "notes": "The fork the process turns on.",
    }
    assert row_to_step(step_to_row("p1", step)) == step


def test_step_row_round_trips_a_commitment():
    """``commits`` is what tools/ai_company.py gates reads.

    It was appended to STEP_HEADERS rather than inserted so an existing Steps
    sheet keeps every column index it already had; this pins that it survives
    the trip in both directions.
    """
    step = {
        "number": 4,
        "title": "Apply the approved budget",
        "executor": "ai",
        "owner": "Pricing analyst agent",
        "commits": True,
    }
    assert row_to_step(step_to_row("p1", step)) == step


def test_a_step_that_commits_nothing_gains_no_flag():
    step = {"number": 1, "title": "Read the calendar", "executor": "automation"}
    assert "commits" not in row_to_step(step_to_row("p1", step))


def test_step_row_round_trips_a_goto():
    step = {"number": 9, "title": "Go to: Review", "kind": "goto", "goto": 2}
    assert row_to_step(step_to_row("p1", step)) == step


def test_absent_fields_stay_absent():
    """A plain linear step must not gain a kind or an executor on the way through."""
    step = {"number": 1, "title": "Do the thing", "owner": "Someone"}
    assert row_to_step(step_to_row("p1", step)) == step


def test_short_rows_do_not_raise():
    # a hand-edited sheet can have fewer columns than the writer produces
    assert row_to_step(("p1", 2, "Trimmed row")) == {
        "number": 2,
        "title": "Trimmed row",
    }


def test_whole_number_frequency_stays_an_int():
    step = {"number": 1, "title": "Step", "frequency": 40}
    assert row_to_step(step_to_row("p1", step))["frequency"] == 40
    step["frequency"] = 2.5
    assert row_to_step(step_to_row("p1", step))["frequency"] == 2.5


# --------------------------------------------------------------- excel trips


def test_round_trip_keeps_every_step(tmp_path, example):
    """The regression this file exists for: steps used to vanish through Excel."""
    pytest.importorskip("openpyxl")
    book = tmp_path / "bp.xlsx"
    back = tmp_path / "bp.json"
    json_to_xlsx(str(EXAMPLE), str(book))
    xlsx_to_json(str(book), str(back))

    result = json.loads(back.read_text(encoding="utf-8"))
    before = {p["id"]: p.get("steps", []) for p in example["processes"]}
    after = {p["id"]: p.get("steps", []) for p in result["processes"]}
    assert after == before
    assert sum(len(s) for s in after.values()) > 0


def test_round_trip_keeps_branches_and_gotos(tmp_path, example):
    pytest.importorskip("openpyxl")
    book = tmp_path / "bp.xlsx"
    back = tmp_path / "bp.json"
    json_to_xlsx(str(EXAMPLE), str(book))
    xlsx_to_json(str(book), str(back))

    result = json.loads(back.read_text(encoding="utf-8"))
    steps = next(p for p in result["processes"] if p["id"] == "proc-live-execution")[
        "steps"
    ]
    decision = next(s for s in steps if s.get("kind") == "decision")
    assert decision["branches"] == [
        {"label": "Approved", "to": 4},
        {"label": "Rejected", "to": "end"},
    ]
    goto = next(s for s in steps if s.get("kind") == "goto")
    assert goto["goto"] == 2


def test_steps_come_back_in_number_order(tmp_path):
    pytest.importorskip("openpyxl")
    source = tmp_path / "bp.json"
    book = tmp_path / "bp.xlsx"
    back = tmp_path / "back.json"
    source.write_text(
        json.dumps(
            _minimal(
                [
                    {"number": 3, "title": "Third"},
                    {"number": 1, "title": "First"},
                    {"number": 2, "title": "Second"},
                ]
            )
        ),
        encoding="utf-8",
    )
    json_to_xlsx(str(source), str(book))
    xlsx_to_json(str(book), str(back))
    steps = json.loads(back.read_text(encoding="utf-8"))["processes"][0]["steps"]
    assert [s["number"] for s in steps] == [1, 2, 3]


def test_orphan_step_rows_are_skipped_not_fatal(tmp_path, capsys):
    pytest.importorskip("openpyxl")
    import openpyxl

    source = tmp_path / "bp.json"
    book = tmp_path / "bp.xlsx"
    back = tmp_path / "back.json"
    source.write_text(
        json.dumps(_minimal([{"number": 1, "title": "First"}])), encoding="utf-8"
    )
    json_to_xlsx(str(source), str(book))

    workbook = openpyxl.load_workbook(book)
    workbook["Steps"].append(["p-does-not-exist", 1, "Orphan"])
    workbook.save(book)

    xlsx_to_json(str(book), str(back))
    assert "p-does-not-exist" in capsys.readouterr().out
    steps = json.loads(back.read_text(encoding="utf-8"))["processes"][0]["steps"]
    assert [s["title"] for s in steps] == ["First"]


# ------------------------------------------------------------------ validation


def test_shipped_example_is_clean(example):
    errors, warnings = check_blueprint(example)
    assert errors == []
    assert warnings == []


def test_branch_to_a_missing_step_is_an_error():
    errors, _ = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Fork",
                    "kind": "decision",
                    "branches": [{"label": "Yes", "to": 2}, {"label": "No", "to": 99}],
                },
                {"number": 2, "title": "Next"},
            ]
        )
    )
    assert any("99" in e for e in errors)


def test_branch_to_end_is_allowed():
    errors, _ = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Fork",
                    "kind": "decision",
                    "branches": [
                        {"label": "Yes", "to": 2},
                        {"label": "No", "to": "end"},
                    ],
                },
                {"number": 2, "title": "Next"},
            ]
        )
    )
    assert errors == []


def test_unlabelled_branch_is_an_error():
    errors, _ = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Fork",
                    "kind": "decision",
                    "branches": [{"label": "", "to": 2}, {"label": "No", "to": "end"}],
                },
                {"number": 2, "title": "Next"},
            ]
        )
    )
    assert any("branch with no label" in e for e in errors)


def test_fork_with_one_way_out_warns():
    _, warnings = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Fork",
                    "kind": "decision",
                    "branches": [{"label": "Yes", "to": 2}],
                },
                {"number": 2, "title": "Next"},
            ]
        )
    )
    assert any("fewer than two ways out" in w for w in warnings)


def test_branches_on_a_non_decision_warn():
    _, warnings = check_blueprint(
        _minimal(
            [
                {"number": 1, "title": "Task", "branches": [{"label": "Yes", "to": 2}]},
                {"number": 2, "title": "Next"},
            ]
        )
    )
    assert any("not a decision" in w for w in warnings)


def test_long_backward_branch_should_be_a_goto():
    steps = [{"number": n, "title": f"Step {n}"} for n in range(1, 7)]
    steps[5] = {
        "number": 6,
        "title": "Fork",
        "kind": "decision",
        "branches": [{"label": "Again", "to": 1}, {"label": "Done", "to": "end"}],
    }
    _, warnings = check_blueprint(_minimal(steps))
    assert any("go-to step" in w for w in warnings)


def test_short_backward_branch_is_fine():
    steps = [{"number": n, "title": f"Step {n}"} for n in range(1, 6)]
    steps[4] = {
        "number": 5,
        "title": "Fork",
        "kind": "decision",
        "branches": [
            {"label": "Again", "to": 5 - LOOP_BACK_LIMIT},
            {"label": "Done", "to": "end"},
        ],
    }
    _, warnings = check_blueprint(_minimal(steps))
    assert not any("go-to step" in w for w in warnings)


def test_goto_without_a_destination_is_an_error():
    errors, _ = check_blueprint(
        _minimal(
            [
                {"number": 1, "title": "Step"},
                {"number": 2, "title": "Go to", "kind": "goto"},
            ]
        )
    )
    assert any("no destination" in e for e in errors)


def test_goto_to_a_missing_step_is_an_error():
    errors, _ = check_blueprint(
        _minimal(
            [
                {"number": 1, "title": "Step"},
                {"number": 2, "title": "Go to", "kind": "goto", "goto": 42},
            ]
        )
    )
    assert any("42" in e for e in errors)


def test_a_long_goto_does_not_warn():
    """The whole point of a go-to step is reaching further than a wire should."""
    steps = [{"number": n, "title": f"Step {n}"} for n in range(1, 8)]
    steps[6] = {"number": 7, "title": "Go to: Step 1", "kind": "goto", "goto": 1}
    _, warnings = check_blueprint(_minimal(steps))
    assert not any("go-to step" in w for w in warnings)


def test_duplicate_step_numbers_are_an_error():
    errors, _ = check_blueprint(
        _minimal([{"number": 1, "title": "One"}, {"number": 1, "title": "Also one"}])
    )
    assert any("more than one step numbered 1" in e for e in errors)


def test_unknown_kind_and_executor_are_errors():
    errors, _ = check_blueprint(
        _minimal(
            [{"number": 1, "title": "Step", "kind": "wibble", "executor": "robot"}]
        )
    )
    assert any("wibble" in e for e in errors)
    assert any("robot" in e for e in errors)


def test_unpriced_person_steps_warn_once():
    steps = [
        {
            "number": n,
            "title": f"Step {n}",
            "executor": "person",
            "duration": "10 minutes",
        }
        for n in range(1, 4)
    ]
    _, warnings = check_blueprint(_minimal(steps))
    unpriced = [w for w in warnings if "monthly frequency" in w]
    assert len(unpriced) == 1
    assert "3 person steps" in unpriced[0]


def test_a_priced_person_step_does_not_warn():
    _, warnings = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Step",
                    "executor": "person",
                    "duration": "10 minutes",
                    "frequency": 20,
                }
            ]
        )
    )
    assert not any("monthly frequency" in w for w in warnings)


def test_automation_steps_are_not_labour():
    _, warnings = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Step",
                    "executor": "automation",
                    "duration": "instant",
                }
            ]
        )
    )
    assert not any("monthly frequency" in w for w in warnings)


def test_existing_owner_checks_still_hold(example):
    broken = copy.deepcopy(example)
    broken["processes"][0].pop("owner")
    errors, _ = check_blueprint(broken)
    assert any("has no owner" in e for e in errors)


def test_end_is_a_valid_kind():
    """A branch that stops can name a terminator step instead of the sentinel."""
    errors, warnings = check_blueprint(
        _minimal(
            [
                {
                    "number": 1,
                    "title": "Fork",
                    "kind": "decision",
                    "duration": "2 minutes",
                    "frequency": 10,
                    "branches": [{"label": "Yes", "to": 2}, {"label": "No", "to": 3}],
                },
                {
                    "number": 2,
                    "title": "Carry on",
                    "duration": "5 minutes",
                    "frequency": 10,
                },
                {"number": 3, "title": "End — rejected", "kind": "end"},
            ]
        )
    )
    assert errors == []
    assert warnings == []


def test_a_person_step_with_neither_number_is_unpriced():
    """Half-priced and not priced at all are the same hole in the total."""
    _, warnings = check_blueprint(
        _minimal([{"number": 1, "title": "Step", "executor": "person"}])
    )
    assert any("1 person step without" in w for w in warnings)


def test_waits_and_terminators_are_not_labour():
    """A wait is elapsed time, not somebody's hour."""
    _, warnings = check_blueprint(
        _minimal(
            [
                {"number": 1, "title": "Hold", "kind": "delay", "duration": "2 days"},
                {"number": 2, "title": "Stop", "kind": "end"},
            ]
        )
    )
    assert not any("monthly frequency" in w for w in warnings)


def test_check_process_reports_codes():
    """The codes are what tests/test_process_grammar.py compares against JS."""
    findings = check_process(
        {
            "steps": [
                {"number": 1, "title": "One", "duration": "5 minutes", "frequency": 4},
                {
                    "number": 2,
                    "title": "Fork",
                    "kind": "decision",
                    "duration": "1 minute",
                    "frequency": 4,
                    "branches": [{"label": "", "to": 99}],
                },
            ]
        }
    )
    codes = sorted(f["code"] for f in findings)
    assert codes == ["branch_target_missing", "thin_fork", "unlabelled_branch"]
    assert all(f["step"] == 2 for f in findings)
    assert [f["severity"] for f in findings if f["code"] == "thin_fork"] == ["warning"]

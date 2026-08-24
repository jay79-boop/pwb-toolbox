"""Reconcile ``static/process-grammar.js`` against the Python validator.

The branch grammar has to be enforced in three places: the validator that
``tools/blueprint_converter.py validate`` runs, the canvas
(``static/flow-canvas.html``) and the builder (``static/blueprint-builder.html``).
The two browser tools share one JavaScript copy, and this holds that copy
against ``check_process`` case for case — by finding code and step number, so a
reworded message does not fail the build but a rule that only one side enforces
does. Without it the first symptom of drift is a tool calling a map finished
that the validator then rejects.

The second test runs the JavaScript suite proper, which covers what Python has
no counterpart for: the canvas adapter, the layering that makes "more than
three steps back" mean anything, the duration parser, and the renumbering.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "static", "process-grammar.test.js")
EXAMPLE = os.path.join(ROOT, "docs", "blueprint-example.json")

sys.path.insert(0, ROOT)

from tools.blueprint_converter import check_process  # noqa: E402

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _priced(number, title, **extra):
    step = {
        "number": number,
        "title": title,
        "executor": "person",
        "duration": "5 minutes",
        "frequency": 4,
    }
    step.update(extra)
    return step


def _cases():
    """Every rule, plus the shipped example, as (name, process) pairs."""
    cases = [
        ("linear", {"steps": [_priced(n, f"Step {n}") for n in (1, 2, 3)]}),
        (
            "labelled fork with a terminal branch",
            {
                "steps": [
                    _priced(
                        1,
                        "Fork",
                        kind="decision",
                        branches=[
                            {"label": "Yes", "to": 2},
                            {"label": "No", "to": "end"},
                        ],
                    ),
                    _priced(2, "Carry on"),
                ]
            },
        ),
        (
            "branch pointing nowhere",
            {
                "steps": [
                    _priced(
                        1,
                        "Fork",
                        kind="decision",
                        branches=[{"label": "Yes", "to": 2}, {"label": "No", "to": 99}],
                    ),
                    _priced(2, "Carry on"),
                ]
            },
        ),
        (
            "branch with no destination at all",
            {
                "steps": [
                    _priced(
                        1,
                        "Fork",
                        kind="decision",
                        branches=[
                            {"label": "Yes", "to": None},
                            {"label": "No", "to": "end"},
                        ],
                    ),
                    _priced(2, "Carry on"),
                ]
            },
        ),
        (
            "unlabelled branch",
            {
                "steps": [
                    _priced(
                        1,
                        "Fork",
                        kind="decision",
                        branches=[{"label": "", "to": 2}, {"label": "No", "to": "end"}],
                    ),
                    _priced(2, "Carry on"),
                ]
            },
        ),
        (
            "fork with one way out",
            {
                "steps": [
                    _priced(
                        1, "Fork", kind="decision", branches=[{"label": "Yes", "to": 2}]
                    ),
                    _priced(2, "Carry on"),
                ]
            },
        ),
        (
            "fork with no branches at all",
            {"steps": [_priced(1, "Fork", kind="decision"), _priced(2, "Carry on")]},
        ),
        (
            "branches on a step that is not a fork",
            {
                "steps": [
                    _priced(1, "Task", branches=[{"label": "Yes", "to": 2}]),
                    _priced(2, "Carry on"),
                ]
            },
        ),
        (
            "go-to with no destination",
            {
                "steps": [
                    _priced(1, "One"),
                    {"number": 2, "title": "Jump", "kind": "goto"},
                ]
            },
        ),
        (
            "go-to pointing at nothing",
            {
                "steps": [
                    _priced(1, "One"),
                    {"number": 2, "title": "Jump", "kind": "goto", "goto": 42},
                ]
            },
        ),
        (
            "long loop-back",
            {
                "steps": [_priced(n, f"Step {n}") for n in (1, 2, 3, 4)]
                + [
                    _priced(
                        5,
                        "Fork",
                        kind="decision",
                        branches=[
                            {"label": "Again", "to": 1},
                            {"label": "On", "to": "end"},
                        ],
                    )
                ]
            },
        ),
        (
            "short loop-back",
            {
                "steps": [_priced(n, f"Step {n}") for n in (1, 2, 3, 4)]
                + [
                    _priced(
                        5,
                        "Fork",
                        kind="decision",
                        branches=[
                            {"label": "Again", "to": 2},
                            {"label": "On", "to": "end"},
                        ],
                    )
                ]
            },
        ),
        (
            "long go-to, which is the point of one",
            {
                "steps": [_priced(n, f"Step {n}") for n in (1, 2, 3, 4, 5)]
                + [{"number": 6, "title": "Go to: Step 1", "kind": "goto", "goto": 1}]
            },
        ),
        (
            "duplicate step numbers",
            {"steps": [_priced(1, "One"), _priced(1, "Also one")]},
        ),
        (
            "unknown kind and executor",
            {
                "steps": [
                    {"number": 1, "title": "One", "kind": "wibble", "executor": "robot"}
                ]
            },
        ),
        (
            "half-priced person steps",
            {
                "steps": [
                    {
                        "number": 1,
                        "title": "One",
                        "executor": "person",
                        "duration": "5 minutes",
                    },
                    {"number": 2, "title": "Two", "executor": "person", "frequency": 4},
                    {"number": 3, "title": "Three", "executor": "person"},
                ]
            },
        ),
        (
            "waits and terminators are not labour",
            {
                "steps": [
                    {
                        "number": 1,
                        "title": "Hold",
                        "kind": "delay",
                        "duration": "2 days",
                    },
                    {"number": 2, "title": "Stop", "kind": "end"},
                ]
            },
        ),
        (
            "automation is not labour",
            {
                "steps": [
                    {
                        "number": 1,
                        "title": "Run",
                        "executor": "automation",
                        "duration": "instant",
                    }
                ]
            },
        ),
        ("empty process", {"steps": []}),
    ]

    with open(EXAMPLE, encoding="utf-8") as handle:
        blueprint = json.load(handle)
    for proc in blueprint["processes"]:
        cases.append(("example: " + proc["id"], proc))

    return cases


def _fingerprint(findings):
    """(code, step) pairs, sorted — what both sides must agree on."""
    return sorted(
        (f["code"], f["step"] if f["step"] is not None else -1) for f in findings
    )


@needs_node
def test_javascript_agrees_with_the_validator(tmp_path):
    cases = _cases()
    payload = tmp_path / "cases.json"
    payload.write_text(
        json.dumps({"cases": [{"name": n, "process": p} for n, p in cases]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", SUITE, "--cross", str(payload)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    js = {r["name"]: r["findings"] for r in json.loads(result.stdout)["results"]}

    assert len(js) == len(cases)
    mismatches = []
    for name, proc in cases:
        want = _fingerprint(check_process(proc))
        got = _fingerprint(js[name])
        if want != got:
            mismatches.append(f"{name}\n    python: {want}\n    js:     {got}")
    assert not mismatches, "the two implementations disagree:\n" + "\n".join(mismatches)


@needs_node
def test_javascript_agrees_on_severity(tmp_path):
    """An error in one and a warning in the other would gate differently."""
    cases = _cases()
    payload = tmp_path / "cases.json"
    payload.write_text(
        json.dumps({"cases": [{"name": n, "process": p} for n, p in cases]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", SUITE, "--cross", str(payload)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    severity = {}
    for row in json.loads(result.stdout)["results"]:
        for finding in row["findings"]:
            severity.setdefault(finding["code"], set()).add(finding["severity"])
    for _, proc in cases:
        for finding in check_process(proc):
            severity.setdefault(finding["code"], set()).add(finding["severity"])

    split = {code: sorted(kinds) for code, kinds in severity.items() if len(kinds) > 1}
    assert not split, f"the same code is graded differently: {split}"


@needs_node
def test_every_rule_is_exercised():
    """A parity test that never sees a rule fire proves nothing about it."""
    seen = set()
    for _, proc in _cases():
        for finding in check_process(proc):
            seen.add(finding["code"])
    expected = {
        "branch_target_missing",
        "branches_on_non_decision",
        "duplicate_step_number",
        "goto_no_destination",
        "goto_target_missing",
        "long_loop_back",
        "thin_fork",
        "unknown_executor",
        "unknown_kind",
        "unlabelled_branch",
        "unpriced_person_steps",
    }
    assert expected - seen == set(), f"never fired: {sorted(expected - seen)}"


@needs_node
def test_process_grammar_suite():
    result = subprocess.run(
        ["node", SUITE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "0 failed" in output, output

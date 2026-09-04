"""The hard stop must convict AND acquit.

`.claude/hooks/session-hard-stop.sh` is the only guardrail in this repo that can
actually refuse a tool call. session-size.sh warns and is ignorable; this one is
not. That makes both of its failure modes expensive:

- failing to block  -> the window burns, which is the thing it was built for
- blocking wrongly  -> a working session dies, or worse, cannot save its work

So every path gets a test, including the ones that must stay open.
"""

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "session-hard-stop.sh"

ALLOW, BLOCK = 0, 2


def _transcript(tmp_path, cache_read_tokens):
    """A transcript whose usage records sum to `cache_read_tokens`."""
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {"message": {"usage": {"cache_read_input_tokens": cache_read_tokens}}}
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _run(payload, env=None, threshold="1000000"):
    settings = {
        "PWB_HARD_STOP_TOKENS": threshold,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    settings.update(env or {})
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=settings,
    )


def test_blocks_once_over_threshold(tmp_path):
    r = _run({"transcript_path": str(_transcript(tmp_path, 5_000_000)), "tool_name": "Read"})
    assert r.returncode == BLOCK
    decision = json.loads(r.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "HARD STOP" in decision["permissionDecisionReason"]


def test_silent_under_threshold(tmp_path):
    r = _run({"transcript_path": str(_transcript(tmp_path, 10)), "tool_name": "Read"})
    assert r.returncode == ALLOW
    assert r.stdout == ""


@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit", "TodoWrite"])
def test_work_in_flight_can_still_be_saved(tmp_path, tool):
    """A stop that strands uncommitted work costs more than the tokens it saves."""
    r = _run({"transcript_path": str(_transcript(tmp_path, 5_000_000)), "tool_name": tool})
    assert r.returncode == ALLOW


@pytest.mark.parametrize(
    "command", ["git commit -m x", "git add -A", "git push -u origin b", "git status"]
)
def test_git_stays_open_so_the_branch_can_land(tmp_path, command):
    r = _run(
        {
            "transcript_path": str(_transcript(tmp_path, 5_000_000)),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    assert r.returncode == ALLOW


@pytest.mark.parametrize("command", ["rm -rf /", "pytest tests/", "curl http://x"])
def test_bash_is_not_a_loophole(tmp_path, command):
    """Only save-my-work git commands pass -- not arbitrary shell."""
    r = _run(
        {
            "transcript_path": str(_transcript(tmp_path, 5_000_000)),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    assert r.returncode == BLOCK


def test_env_escape_hatch(tmp_path):
    r = _run(
        {"transcript_path": str(_transcript(tmp_path, 5_000_000)), "tool_name": "Read"},
        env={"PWB_HARD_STOP_OFF": "1"},
    )
    assert r.returncode == ALLOW


def test_file_escape_hatch(tmp_path, monkeypatch):
    """An unattended 2am desk-agent run must have a way through that is not a code edit."""
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / ".hard-stop-off").write_text("", encoding="utf-8")
    r = _run(
        {"transcript_path": str(_transcript(tmp_path, 5_000_000)), "tool_name": "Read"},
        env={"CLAUDE_PROJECT_DIR": str(project)},
    )
    assert r.returncode == ALLOW


@pytest.mark.parametrize(
    "payload", ["not json", "", "{}", '{"transcript_path": "/nope/missing.jsonl"}']
)
def test_fails_open_on_anything_unexpected(payload):
    """Runs before EVERY tool call. Breaking the session it protects is the worse bug."""
    r = subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env={"PWB_HARD_STOP_TOKENS": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert r.returncode == ALLOW

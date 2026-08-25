"""The global installer, exercised against a throwaway HOME.

This tool writes to the user's real `~/.claude/settings.json`, which holds their
model, permissions and every other hook. The load-bearing tests are the ones that
prove it merges rather than replaces, and that running it twice does not stack
duplicate hooks -- a corrupted settings file breaks every session on the machine,
not just this repo's.
"""

import json
import os

import pytest

from tools import install_spend_hook as installer


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    (root / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))  # Windows
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(root), 1))
    return root / ".claude"


def settings_of(home):
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


def test_existing_settings_survive_the_merge(home):
    (home / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "permissions": {"allow": ["Bash(git status:*)"]},
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo existing"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    assert installer.main([]) == 0

    after = settings_of(home)
    assert after["model"] == "opus"
    assert after["permissions"]["allow"] == ["Bash(git status:*)"]
    assert after["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo existing"
    assert len(after["hooks"]["UserPromptSubmit"]) == 1


def test_running_twice_does_not_stack_the_hook(home):
    installer.main([])
    installer.main([])
    assert len(settings_of(home)["hooks"]["UserPromptSubmit"]) == 1
    assert (home / "CLAUDE.md").read_text(encoding="utf-8").count(installer.MARKER) == 1


def test_check_writes_nothing(home):
    assert installer.main(["--check"]) == 0
    assert not (home / "settings.json").exists()
    assert not (home / "CLAUDE.md").exists()
    assert not (home / "hooks" / installer.HOOK_NAME).exists()


def test_unparseable_settings_are_left_alone(home):
    """Refuse rather than repair: never overwrite a file we cannot read."""

    (home / "settings.json").write_text("{ this is not json", encoding="utf-8")
    installer.main([])
    assert (home / "settings.json").read_text(encoding="utf-8") == "{ this is not json"


def test_the_previous_file_is_backed_up(home):
    (home / "settings.json").write_text('{"model": "opus"}', encoding="utf-8")
    installer.main([])
    assert json.loads((home / "settings.json.bak").read_text(encoding="utf-8")) == {
        "model": "opus"
    }


def test_it_works_from_nothing(home):
    assert installer.main([]) == 0
    assert (home / "hooks" / installer.HOOK_NAME).is_file()
    assert "UserPromptSubmit" in settings_of(home)["hooks"]
    assert installer.MARKER in (home / "CLAUDE.md").read_text(encoding="utf-8")


def test_it_refuses_a_machine_with_no_claude_home(tmp_path, monkeypatch):
    """A cloud container has no ~/.claude worth writing to; say so, do nothing."""

    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(empty), 1))
    assert installer.main([]) == 1


def test_the_embedded_hook_is_valid_python():
    """It is written to disk as source and run by the harness, never imported."""

    import ast

    ast.parse(installer.HOOK_SOURCE)


def test_the_installer_is_ascii_only():
    """Windows PowerShell 5.1 reads a BOM-less file as Windows-1252, and a single
    non-ASCII byte can stop a script parsing at all."""

    path = os.path.join("tools", "install_spend_hook.py")
    assert all(b < 128 for b in open(path, "rb").read())

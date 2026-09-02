"""The global-instructions installer, exercised against a throwaway HOME.

It writes to the user's real `~/.claude/CLAUDE.md`, which every session in every
project reads and which `install_spend_hook.py` also appends to. The load-bearing
tests are the ones proving it touches only its own marked region: a line written
by hand, or the Action Ledger rule, has to come through a refresh untouched.
"""

import os

import pytest

from tools import install_global_instructions as installer


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    (root / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))  # Windows
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(root), 1))
    return root / ".claude"


def claude_md(home):
    return (home / "CLAUDE.md").read_text(encoding="utf-8")


def test_it_works_from_nothing(home):
    assert installer.main([]) == 0
    text = claude_md(home)
    assert text.startswith(installer.START)
    assert text.rstrip("\n").endswith(installer.END)
    assert "# Working with Gexio" in text


def test_lines_outside_the_region_survive_a_refresh(home):
    ledger = "## Action items: one ledger, every project\n\nsome rule\n"
    (home / "CLAUDE.md").write_text("hand-written line\n\n" + ledger, encoding="utf-8")
    installer.main([])

    # Simulate the source moving on: rewrite the region with an old body.
    stale = installer.splice(claude_md(home), "# old body\n")
    (home / "CLAUDE.md").write_text(stale, encoding="utf-8")
    assert "# old body" in claude_md(home)

    installer.main([])
    text = claude_md(home)
    assert text.startswith("hand-written line\n")
    assert ledger in text
    assert "# old body" not in text
    assert "# Working with Gexio" in text
    assert text.count(installer.START) == 1
    assert text.count(installer.END) == 1


def test_running_twice_changes_nothing(home):
    installer.main([])
    first = claude_md(home)
    installer.main([])
    assert claude_md(home) == first
    assert not (home / "CLAUDE.md.bak").exists() or (
        (home / "CLAUDE.md.bak").read_text(encoding="utf-8") == first
    )


def test_check_and_diff_write_nothing(home):
    assert installer.main(["--check"]) == 0
    assert not (home / "CLAUDE.md").exists()
    assert installer.main(["--diff"]) == 0
    assert not (home / "CLAUDE.md").exists()


def test_the_previous_file_is_backed_up(home):
    (home / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    installer.main([])
    assert (home / "CLAUDE.md.bak").read_text(encoding="utf-8") == "mine\n"


def test_a_lone_marker_is_left_as_ordinary_text():
    """Never delete past a marker that has no partner."""

    existing = "before\n" + installer.END + "\nafter\n"
    result = installer.splice(existing, "body\n")
    assert result.startswith(existing)
    assert result.count(installer.START) == 1


def test_it_refuses_a_machine_with_no_claude_home(tmp_path, monkeypatch):
    """A cloud container has no ~/.claude worth writing to; say so, do nothing."""

    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(empty), 1))
    assert installer.main([]) == 1


def test_the_source_explains_itself_only_to_the_repository():
    """The leading comment is for a reader of docs/, not a rule for the model."""

    raw = open(installer.SOURCE, "r", encoding="utf-8").read()
    assert raw.lstrip().startswith("<!--")
    body = installer.load_source()
    assert body.startswith("# Working with Gexio")
    assert "<!--" not in body


def test_the_source_stays_impersonal():
    """This fork is public. The rules travel; the owner's identity does not."""

    body = installer.load_source().lower()
    for word in ("tulsa", "xiong", "qbasic"):
        assert word not in body


def test_the_installer_is_ascii_only():
    """Windows PowerShell 5.1 reads a BOM-less file as Windows-1252, and a single
    non-ASCII byte can stop a script parsing at all."""

    path = os.path.join("tools", "install_global_instructions.py")
    assert all(b < 128 for b in open(path, "rb").read())

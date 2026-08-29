"""Tests for `tools.obsidian_sync`.

No network: every vault here is a synthetic directory tree built in the test
itself. Git operations run against a throwaway `git init` repo in tmp_path,
never a real remote.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
import pytest

from tools.obsidian_sync import (
    MARKER_NAME,
    build_link_index,
    commit_and_push,
    convert_note,
    iter_vault_files,
    load_syncignore,
    sync_vault,
)


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_wikilink_plain_resolves_to_relative_link(tmp_path):
    note = tmp_path / "Trades.md"
    other = tmp_path / "SPY Setup.md"
    index = build_link_index(tmp_path, [note, other])
    referenced = set()
    out = convert_note(
        "See [[SPY Setup]] for context.", Path("Trades.md"), index, referenced
    )
    assert out == "See [SPY Setup](SPY Setup.md) for context."
    assert referenced == set()


def test_wikilink_alias(tmp_path):
    note = tmp_path / "Trades.md"
    other = tmp_path / "SPY Setup.md"
    index = build_link_index(tmp_path, [note, other])
    out = convert_note("See [[SPY Setup|the setup]].", Path("Trades.md"), index, set())
    assert out == "See [the setup](SPY Setup.md)."


def test_wikilink_heading_becomes_anchor(tmp_path):
    note = tmp_path / "Trades.md"
    other = tmp_path / "SPY Setup.md"
    index = build_link_index(tmp_path, [note, other])
    out = convert_note(
        "See [[SPY Setup#Entry Rules]].", Path("Trades.md"), index, set()
    )
    assert out == "See [SPY Setup](SPY Setup.md#entry-rules)."


def test_wikilink_unresolved_degrades_to_plain_text(tmp_path):
    note = tmp_path / "Trades.md"
    index = build_link_index(tmp_path, [note])
    out = convert_note("See [[Missing Note]].", Path("Trades.md"), index, set())
    assert out == "See Missing Note."


def test_wikilink_resolves_across_subfolders(tmp_path):
    note = tmp_path / "Journal" / "2026-08-25.md"
    other = tmp_path / "Setups" / "SPY.md"
    index = build_link_index(tmp_path, [note, other])
    out = convert_note("[[SPY]]", Path("Journal/2026-08-25.md"), index, set())
    assert out == "[SPY](../Setups/SPY.md)"


def test_embed_image_is_rewritten_and_recorded(tmp_path):
    note = tmp_path / "Trades.md"
    image = tmp_path / "attachments" / "chart.png"
    index = build_link_index(tmp_path, [note])
    index["chart"] = Path("attachments/chart.png")
    referenced = set()
    out = convert_note("![[chart.png]]", Path("Trades.md"), index, referenced)
    assert out == "![chart.png](attachments/chart.png)"
    assert referenced == {Path("attachments/chart.png")}


def test_embed_of_a_note_is_a_link_not_inlined(tmp_path):
    note = tmp_path / "Trades.md"
    other = tmp_path / "Playbook.md"
    index = build_link_index(tmp_path, [note, other])
    referenced = set()
    out = convert_note("![[Playbook]]", Path("Trades.md"), index, referenced)
    assert out == "[Playbook](Playbook.md)"
    assert referenced == set()


def test_frontmatter_is_preserved_verbatim(tmp_path):
    note = tmp_path / "Trades.md"
    index = build_link_index(tmp_path, [note])
    text = "---\ntags: [journal]\ndate: 2026-08-25\n---\n\nBody text.\n"
    assert convert_note(text, Path("Trades.md"), index, set()) == text


def test_iter_vault_files_skips_dotfolders_and_junk(tmp_path):
    _write(tmp_path / "Note.md", "hello")
    _write(tmp_path / ".obsidian" / "config.json", "{}")
    _write(tmp_path / ".trash" / "Deleted.md", "gone")
    _write(tmp_path / "Thumbs.db", "junk")
    _write(tmp_path / "sub" / "Nested.md", "nested")

    files = iter_vault_files(tmp_path, [])
    rels = {p.relative_to(tmp_path).as_posix() for p in files}
    assert rels == {"Note.md", "sub/Nested.md"}


def test_syncignore_excludes_matching_patterns(tmp_path):
    _write(tmp_path / "Private" / "Diary.md", "secret")
    _write(tmp_path / "Public.md", "fine")
    _write(tmp_path / ".syncignore", "Private/*\n")

    patterns = load_syncignore(tmp_path)
    files = iter_vault_files(tmp_path, patterns)
    rels = {p.relative_to(tmp_path).as_posix() for p in files}
    assert rels == {"Public.md"}


def test_sync_vault_writes_notes_and_assets(tmp_path):
    vault = tmp_path / "vault"
    _write(vault / "Trades.md", "Setup: [[SPY Setup]]\n\n![[chart.png]]\n")
    _write(vault / "SPY Setup.md", "The setup.\n")
    _write(vault / "chart.png", "not really a png")

    output = tmp_path / "docs" / "journal"
    result = sync_vault(vault, output, dry_run=False)

    assert result.notes_written == 2
    assert result.assets_copied == 1
    assert (output / MARKER_NAME).exists()
    assert (
        output / "Trades.md"
    ).read_text() == "Setup: [SPY Setup](SPY Setup.md)\n\n![chart.png](chart.png)\n"
    assert (output / "chart.png").read_text() == "not really a png"


def test_sync_vault_dry_run_writes_nothing(tmp_path):
    vault = tmp_path / "vault"
    _write(vault / "Trades.md", "hello")
    output = tmp_path / "docs" / "journal"

    result = sync_vault(vault, output, dry_run=True)

    assert result.notes_written == 1
    assert not output.exists()


def test_sync_vault_is_idempotent(tmp_path):
    vault = tmp_path / "vault"
    _write(vault / "Trades.md", "hello [[Other]]")
    _write(vault / "Other.md", "world")
    output = tmp_path / "docs" / "journal"

    sync_vault(vault, output, dry_run=False)
    first = (output / "Trades.md").read_text()
    sync_vault(vault, output, dry_run=False)
    second = (output / "Trades.md").read_text()

    assert first == second == "hello [Other](Other.md)"


def test_sync_vault_refuses_to_overwrite_foreign_directory(tmp_path):
    vault = tmp_path / "vault"
    _write(vault / "Trades.md", "hello")
    output = tmp_path / "docs" / "journal"
    _write(output / "hand-written.md", "please don't delete me")

    with pytest.raises(click.ClickException):
        sync_vault(vault, output, dry_run=False)

    # --force bypasses the guard.
    sync_vault(vault, output, dry_run=False, force=True)
    assert not (output / "hand-written.md").exists()
    assert (output / "Trades.md").exists()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)


def test_commit_and_push_commits_generated_notes(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    vault = tmp_path / "vault"
    _write(vault / "Trades.md", "hello")

    output = repo / "docs" / "journal"
    result = sync_vault(vault, output, dry_run=False)
    status = commit_and_push(repo, output, result, push=False, remote=None, branch=None)

    assert status == "committed"
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Sync Obsidian vault into docs/journal" in log.stdout
    assert "1 notes" in log.stdout


def test_commit_and_push_is_a_no_op_when_nothing_changed(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    vault = tmp_path / "vault"
    _write(vault / "Trades.md", "hello")

    output = repo / "docs" / "journal"
    result = sync_vault(vault, output, dry_run=False)
    commit_and_push(repo, output, result, push=False, remote=None, branch=None)

    # Second sync produces identical content, so nothing should be staged.
    result2 = sync_vault(vault, output, dry_run=False)
    status = commit_and_push(
        repo, output, result2, push=False, remote=None, branch=None
    )
    assert status == "no changes to commit"

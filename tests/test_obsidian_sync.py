"""Tests for `tools.obsidian_sync`.

No network: every vault here is a synthetic directory tree built in the test
itself. Git operations run against a throwaway `git init` repo in tmp_path,
never a real remote.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click
import pytest

from tools.obsidian_sync import (
    MARKER_NAME,
    gitignored_paths,
    assert_publish_is_deliberate,
    build_link_index,
    commit_and_push,
    convert_note,
    discover_vaults,
    iter_vault_files,
    load_syncignore,
    read_vault_registry,
    resolve_vault,
    scan_for_vaults,
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


# --- Finding the vault -------------------------------------------------------
#
# The registry is the whole point of these: the vault's path is a fact Obsidian
# already recorded, so the tool should never have to ask for it. Each test both
# convicts (discovery finds what is there) and acquits (it does not invent a
# vault, or pick between two).


def _registry(config_dir: Path, vaults: dict) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "obsidian.json"
    path.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")
    return path


def test_registry_reads_vault_paths(tmp_path):
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    _registry(
        tmp_path / "config",
        {"a1b2": {"path": str(vault), "ts": 1755000000000, "open": True}},
    )

    found = read_vault_registry(tmp_path / "config")

    assert len(found) == 1
    assert found[0].path == vault
    assert found[0].source == "registry"
    assert found[0].is_open is True
    assert found[0].last_opened == 1755000000000
    assert found[0].exists is True


def test_registry_marks_a_vault_that_is_no_longer_on_disk(tmp_path):
    """Obsidian remembers moved and deleted vaults; say so rather than hiding it."""
    _registry(
        tmp_path / "config",
        {"gone": {"path": str(tmp_path / "Moved Away"), "ts": 1, "open": False}},
    )

    found = read_vault_registry(tmp_path / "config")

    assert len(found) == 1
    assert found[0].exists is False


def test_registry_absent_or_corrupt_yields_nothing_rather_than_raising(tmp_path):
    assert read_vault_registry(tmp_path / "nope") == []

    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "obsidian.json").write_text("{not json", encoding="utf-8")
    assert read_vault_registry(broken) == []

    wrong_shape = tmp_path / "other"
    wrong_shape.mkdir()
    (wrong_shape / "obsidian.json").write_text('{"vaults": []}', encoding="utf-8")
    assert read_vault_registry(wrong_shape) == []


def test_scan_finds_a_folder_holding_dot_obsidian(tmp_path):
    vault = tmp_path / "Documents" / "Second Brain"
    (vault / ".obsidian").mkdir(parents=True)
    _write(vault / "Note.md", "hi")
    (tmp_path / "Documents" / "Not A Vault").mkdir(parents=True)

    found = scan_for_vaults([tmp_path])

    assert [c.path for c in found] == [vault]
    assert found[0].source == "scan"


def test_scan_does_not_report_a_folder_inside_a_vault_as_a_second_vault(tmp_path):
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "Nested" / ".obsidian").mkdir(parents=True)

    found = scan_for_vaults([tmp_path])

    assert [c.path for c in found] == [vault]


def test_scan_respects_the_depth_limit(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "Vault"
    (deep / ".obsidian").mkdir(parents=True)

    assert scan_for_vaults([tmp_path], max_depth=2) == []
    assert [c.path for c in scan_for_vaults([tmp_path], max_depth=6)] == [deep]


def test_scan_skips_heavy_directories(tmp_path):
    buried = tmp_path / "AppData" / "Vault"
    (buried / ".obsidian").mkdir(parents=True)

    assert scan_for_vaults([tmp_path]) == []


def test_discovery_orders_open_and_recent_vaults_first(tmp_path, monkeypatch):
    old = tmp_path / "Old"
    recent = tmp_path / "Recent"
    current = tmp_path / "Current"
    for path in (old, recent, current):
        (path / ".obsidian").mkdir(parents=True)
    _registry(
        tmp_path / "config",
        {
            "1": {"path": str(old), "ts": 100, "open": False},
            "2": {"path": str(recent), "ts": 900, "open": False},
            "3": {"path": str(current), "ts": 50, "open": True},
        },
    )
    monkeypatch.setattr(
        "tools.obsidian_sync.obsidian_config_dirs", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr("tools.obsidian_sync.default_scan_roots", lambda: [])

    assert [c.path for c in discover_vaults()] == [current, recent, old]


def test_discovery_prefers_the_registry_entry_over_the_scanned_one(
    tmp_path, monkeypatch
):
    """Only the registry carries timestamps, so it must win on the same path."""
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    _registry(tmp_path / "config", {"1": {"path": str(vault), "ts": 7, "open": True}})
    monkeypatch.setattr(
        "tools.obsidian_sync.obsidian_config_dirs", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr("tools.obsidian_sync.default_scan_roots", lambda: [tmp_path])

    found = discover_vaults()

    assert len(found) == 1
    assert found[0].source == "registry"
    assert found[0].last_opened == 7


def test_resolve_vault_returns_an_explicit_path_untouched(tmp_path):
    assert resolve_vault(tmp_path / "Anywhere") == tmp_path / "Anywhere"


def test_resolve_vault_finds_the_only_vault_with_no_argument(tmp_path, monkeypatch):
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    _registry(tmp_path / "config", {"1": {"path": str(vault), "ts": 5, "open": True}})
    monkeypatch.setattr(
        "tools.obsidian_sync.obsidian_config_dirs", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr("tools.obsidian_sync.default_scan_roots", lambda: [])

    assert resolve_vault() == vault


def test_resolve_vault_says_where_it_looked_when_there_is_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "tools.obsidian_sync.obsidian_config_dirs", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr("tools.obsidian_sync.default_scan_roots", lambda: [tmp_path])

    with pytest.raises(click.ClickException) as excinfo:
        resolve_vault()

    message = str(excinfo.value)
    assert "No Obsidian vault found" in message
    assert str(tmp_path / "config") in message
    assert "never been opened" in message


def test_resolve_vault_refuses_to_guess_between_two_vaults(tmp_path, monkeypatch):
    """It wipes docs/journal, so picking the wrong vault is not recoverable."""
    work = tmp_path / "Work"
    personal = tmp_path / "Personal"
    for path in (work, personal):
        (path / ".obsidian").mkdir(parents=True)
    _registry(
        tmp_path / "config",
        {
            "1": {"path": str(work), "ts": 200, "open": False},
            "2": {"path": str(personal), "ts": 100, "open": False},
        },
    )
    monkeypatch.setattr(
        "tools.obsidian_sync.obsidian_config_dirs", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr("tools.obsidian_sync.default_scan_roots", lambda: [])

    with pytest.raises(click.ClickException) as excinfo:
        resolve_vault()

    message = str(excinfo.value)
    assert "Found 2 Obsidian vaults" in message
    assert f'--vault "{work}"' in message
    assert f'--vault "{personal}"' in message


def test_resolve_vault_ignores_a_registered_vault_that_is_gone(tmp_path, monkeypatch):
    """A stale registry entry must not become an ambiguity, or a bad pick."""
    live = tmp_path / "Live"
    (live / ".obsidian").mkdir(parents=True)
    _registry(
        tmp_path / "config",
        {
            "1": {"path": str(tmp_path / "Deleted"), "ts": 999, "open": False},
            "2": {"path": str(live), "ts": 1, "open": False},
        },
    )
    monkeypatch.setattr(
        "tools.obsidian_sync.obsidian_config_dirs", lambda: [tmp_path / "config"]
    )
    monkeypatch.setattr("tools.obsidian_sync.default_scan_roots", lambda: [])

    assert resolve_vault() == live


# --- Not publishing the vault by accident ------------------------------------
#
# docs/journal is committed, not gitignored, and this fork is public. The guard
# is on the irreversible half only: dry runs and local syncs must stay free.


def test_commit_is_refused_when_the_vault_has_no_syncignore(tmp_path):
    with pytest.raises(click.ClickException) as excinfo:
        assert_publish_is_deliberate(tmp_path, allow_publish=False)

    message = str(excinfo.value)
    assert "Refusing to commit" in message
    assert "this fork is public" in message
    assert str(tmp_path / ".syncignore") in message


def test_a_syncignore_is_enough_to_allow_committing(tmp_path):
    (tmp_path / ".syncignore").write_text("Legal/\n", encoding="utf-8")

    assert_publish_is_deliberate(tmp_path, allow_publish=False) is None


def test_allow_publish_overrides_the_guard(tmp_path):
    assert_publish_is_deliberate(tmp_path, allow_publish=True) is None


def test_the_guard_does_not_touch_a_dry_run_or_a_plain_sync(tmp_path):
    """Acquit: syncing locally must not need a .syncignore."""
    vault = tmp_path / "vault"
    _write(vault / "Note.md", "no syncignore here")
    out = tmp_path / "out"

    assert sync_vault(vault, out, dry_run=True).notes_written == 1
    assert sync_vault(vault, out).notes_written == 1
    assert (out / "Note.md").exists()


# --- Reusing the vault's own .gitignore --------------------------------------
#
# The real vault is a git repo whose .gitignore already excludes session
# transcripts carrying personal detail. Honouring it reuses curation the owner
# already maintains, instead of asking this tool to infer the same list.


def _git_vault(root: Path, gitignore: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    return root


def test_gitignored_files_are_not_mirrored(tmp_path):
    vault = _git_vault(tmp_path / "vault", "Projects/\nsecrets.md\n")
    _write(vault / "Notes.md", "keep me")
    _write(vault / "secrets.md", "claim numbers")
    _write(vault / "Projects" / "transcript.md", "a whole session")

    kept = iter_vault_files(vault, [])

    assert [p.name for p in kept] == ["Notes.md"]


def test_gitignore_can_be_turned_off(tmp_path):
    vault = _git_vault(tmp_path / "vault", "secrets.md\n")
    _write(vault / "Notes.md", "keep me")
    _write(vault / "secrets.md", "claim numbers")

    kept = iter_vault_files(vault, [], respect_gitignore=False)

    assert sorted(p.name for p in kept) == ["Notes.md", "secrets.md"]


def test_a_tracked_file_is_mirrored_even_if_a_rule_would_match(tmp_path):
    """Acquit: git does not ignore what is already tracked, and neither do we."""
    vault = _git_vault(tmp_path / "vault", "")
    _write(vault / "Notes.md", "tracked on purpose")
    subprocess.run(["git", "-C", str(vault), "add", "Notes.md"], check=True)
    (vault / ".gitignore").write_text("Notes.md\n", encoding="utf-8")

    assert [p.name for p in iter_vault_files(vault, [])] == ["Notes.md"]


def test_a_vault_that_is_not_a_git_repo_is_unaffected(tmp_path):
    """Acquit: no repo means no answer from git, and nothing extra excluded."""
    vault = tmp_path / "plain"
    _write(vault / "Notes.md", "hi")
    _write(vault / "Also.md", "hi")

    assert gitignored_paths(vault, [Path("Notes.md")]) == set()
    assert len(iter_vault_files(vault, [])) == 2


def test_sync_reports_how_many_files_the_vault_withheld(tmp_path):
    vault = _git_vault(tmp_path / "vault", "Projects/\n")
    _write(vault / "Notes.md", "keep me")
    _write(vault / "Projects" / "a.md", "private")
    _write(vault / "Projects" / "b.md", "private")

    result = sync_vault(vault, tmp_path / "out", dry_run=True)

    assert result.notes_written == 1
    assert result.gitignored == 2


def test_gitignored_paths_returns_empty_for_an_empty_request(tmp_path):
    assert gitignored_paths(tmp_path, []) == set()

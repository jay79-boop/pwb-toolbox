"""The workspace-directory installer, exercised against a throwaway HOME.

This tool rewrites the user's real `~/.claude/settings.json`, which holds their
model, permission rules and every hook on the machine. The load-bearing tests are
the ones proving it merges rather than replaces, that a second run does not stack
duplicates, and that a file it cannot parse is left alone -- a corrupted settings
file breaks every session on the machine, not just this repo's.

The scan tests matter for a different reason: an over-broad scan is how session
transcripts holding SSNs and claim numbers would end up readable without a prompt
by every session, so `.claude` staying out by default is pinned here.
"""

import json
import os

import pytest

from tools import install_workspace_dirs as installer


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    (root / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))  # Windows
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(root), 1))
    return root


def settings_of(home):
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def registered(home):
    return settings_of(home)["permissions"][installer.SETTINGS_KEY]


# --- the scan -------------------------------------------------------------


def test_finds_repos_and_skips_noise(tmp_path):
    make_repo(tmp_path / "pwb-toolbox")
    make_repo(tmp_path / "OneDrive" / "pwb-toolbox")
    make_repo(tmp_path / "OneDrive" / "trade-journal")
    make_repo(tmp_path / "node_modules" / "left-pad")
    make_repo(tmp_path / "AppData" / "Local" / "vendored")
    (tmp_path / "OneDrive" / "not-a-repo").mkdir(parents=True)

    found = {os.path.basename(p) for p in installer.find_repos([str(tmp_path)])}
    assert found == {"pwb-toolbox", "trade-journal"}
    assert len(installer.find_repos([str(tmp_path)])) == 3  # two are both named that


def test_does_not_descend_into_a_found_repo(tmp_path):
    outer = make_repo(tmp_path / "outer")
    make_repo(outer / "vendor" / "inner")
    make_repo(outer / ".claude" / "worktrees" / "wt-1")

    assert installer.find_repos([str(tmp_path)]) == [str(outer)]


def test_a_worktree_git_file_still_counts_as_a_repo(tmp_path):
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: ../real/.git/worktrees/linked\n")

    assert installer.find_repos([str(tmp_path)]) == [str(linked)]


def test_claude_is_skipped_unless_asked_for(tmp_path):
    make_repo(tmp_path / ".claude")
    make_repo(tmp_path / "project")

    assert installer.find_repos([str(tmp_path)]) == [str(tmp_path / "project")]

    both = installer.find_repos([str(tmp_path)], include_claude=True)
    assert str(tmp_path / ".claude") in both


def test_depth_limit_is_respected(tmp_path):
    deep = make_repo(tmp_path / "a" / "b" / "c" / "deep")

    assert installer.find_repos([str(tmp_path)], depth=2) == []
    assert installer.find_repos([str(tmp_path)], depth=4) == [str(deep)]


def test_a_missing_root_is_not_an_error(tmp_path):
    assert installer.find_repos([str(tmp_path / "nope")]) == []


# --- merging into settings ------------------------------------------------


def test_existing_settings_survive_the_merge(home):
    make_repo(home / "project")
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "permissions": {
                    "allow": ["Bash(git status:*)"],
                    "deny": ["Read(.env)"],
                },
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo existing"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    assert installer.main(["--repos-only", "--root", str(home)]) == 0

    after = settings_of(home)
    assert after["model"] == "opus"
    assert after["permissions"]["allow"] == ["Bash(git status:*)"]
    # the blocklist is appended on either path; what must not happen is the
    # owner's own rule being dropped or reordered
    assert after["permissions"]["deny"][0] == "Read(.env)"
    assert after["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo existing"
    assert registered(home) == [str(home / "project")]


def test_running_twice_does_not_duplicate(home):
    make_repo(home / "project")
    installer.main(["--repos-only", "--root", str(home)])
    installer.main(["--repos-only", "--root", str(home)])

    assert registered(home) == [str(home / "project")]


def test_hand_added_entries_are_kept(home):
    make_repo(home / "project")
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {"permissions": {installer.SETTINGS_KEY: ["C:\\somewhere\\by-hand"]}}
        ),
        encoding="utf-8",
    )
    installer.main(["--repos-only", "--root", str(home)])

    assert "C:\\somewhere\\by-hand" in registered(home)
    assert str(home / "project") in registered(home)


def test_prune_drops_only_what_is_gone(home):
    kept = make_repo(home / "project")
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    installer.SETTINGS_KEY: [str(kept), str(home / "deleted-repo")]
                }
            }
        ),
        encoding="utf-8",
    )
    installer.main(["--repos-only", "--root", str(home), "--prune"])

    assert registered(home) == [str(kept)]


def test_check_writes_nothing(home):
    make_repo(home / "project")
    assert installer.main(["--repos-only", "--root", str(home), "--check"]) == 0
    assert not (home / ".claude" / "settings.json").exists()


def test_scan_writes_nothing(home):
    make_repo(home / "project")
    assert installer.main(["--root", str(home), "--scan"]) == 0
    assert not (home / ".claude" / "settings.json").exists()


def test_unparseable_settings_are_left_alone(home):
    target = home / ".claude" / "settings.json"
    target.write_text("{ this is not json", encoding="utf-8")
    make_repo(home / "project")

    assert installer.main(["--repos-only", "--root", str(home)]) == 1
    assert target.read_text(encoding="utf-8") == "{ this is not json"


def test_no_claude_home_reports_the_cloud_case(tmp_path, monkeypatch, capsys):
    root = tmp_path / "container"
    root.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(root), 1))
    make_repo(root / "project")

    assert installer.main(["--repos-only", "--root", str(root)]) == 1
    assert "not in a cloud session" in capsys.readouterr().out


def test_add_registers_a_path_that_is_not_a_repo(home):
    journal = home / "trade-journal"
    journal.mkdir()

    installer.main(["--repos-only", "--root", str(home), "--add", str(journal)])
    assert str(journal) in registered(home)


def test_add_does_not_duplicate_a_repo_already_found(home):
    project = make_repo(home / "project")

    installer.main(["--repos-only", "--root", str(home), "--add", str(project)])
    assert registered(home) == [str(project)]


def test_a_backup_is_written_before_overwriting(home):
    make_repo(home / "project")
    target = home / ".claude" / "settings.json"
    target.write_text(json.dumps({"model": "opus"}), encoding="utf-8")

    installer.main(["--repos-only", "--root", str(home)])
    backup = json.loads((home / ".claude" / "settings.json.bak").read_text())
    assert backup == {"model": "opus"}


# --- the durable default, and what makes it safe --------------------------


def test_default_registers_home_so_a_new_repo_needs_no_rerun(home):
    make_repo(home / "project")
    assert installer.main([]) == 0

    entries = registered(home)
    assert entries == [str(home)]

    # the whole point: a repo created afterwards is already covered
    later = make_repo(home / "made-next-month")
    assert str(later).startswith(entries[0])


def test_default_installs_the_blocklist(home):
    installer.main([])

    deny = settings_of(home)["permissions"][installer.DENY_KEY]
    for rule in installer.BLOCKLIST:
        assert rule in deny
    # the transcripts are the reason the broad grant needs guarding at all
    assert "Read(~/.claude/projects/**)" in deny


def test_blocklist_does_not_disturb_existing_deny_rules(home):
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {installer.DENY_KEY: ["Bash(rm:*)"]}}),
        encoding="utf-8",
    )
    installer.main([])

    deny = settings_of(home)["permissions"][installer.DENY_KEY]
    assert deny[0] == "Bash(rm:*)"
    assert "Read(~/.ssh/**)" in deny


def test_blocklist_is_not_duplicated_on_a_second_run(home):
    installer.main([])
    installer.main([])

    deny = settings_of(home)["permissions"][installer.DENY_KEY]
    for rule in installer.BLOCKLIST:
        assert deny.count(rule) == 1


def test_no_blocklist_leaves_deny_untouched(home):
    installer.main(["--no-blocklist"])

    assert installer.DENY_KEY not in settings_of(home)["permissions"]


def test_diagnose_writes_nothing_and_names_the_cloud_case(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "container"
    root.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(root), 1))

    assert installer.main(["--diagnose"]) == 1
    out = capsys.readouterr().out
    assert "CLOUD session" in out
    assert not (root / ".claude").exists()


def test_diagnose_reports_an_unreachable_repo(home, capsys):
    make_repo(home / "project")
    assert installer.main(["--diagnose", "--root", str(home)]) == 0

    out = capsys.readouterr().out
    assert "NO   %s" % (home / "project") in out


def test_diagnose_reports_a_reachable_repo_after_install(home, capsys):
    make_repo(home / "project")
    installer.main([])
    capsys.readouterr()

    assert installer.main(["--diagnose", "--root", str(home)]) == 0
    out = capsys.readouterr().out
    assert "yes  %s" % (home / "project") in out


def test_diagnose_refuses_to_read_past_broken_json(home, capsys):
    (home / ".claude" / "settings.json").write_text("{ nope", encoding="utf-8")

    assert installer.main(["--diagnose"]) == 1
    assert "not valid JSON" in capsys.readouterr().out

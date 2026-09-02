"""Tests for tools/awareness.py.

The lab standard's matched pairs apply to a watchdog as much as to a scoring
lab: one that fires on every quiet day is exactly as useless as one that never
fires, and the acquit half is what catches the first kind.

Four tests here are regressions against false alarms this tool produced on its
own first run, against the real ``runs.jsonl``. Each is named for what it got
wrong, because each one would read as plausible if you were not checking:

* ``test_partial_does_not_count_as_a_failure`` -- reported premarket's streak
  as 8 when the truth was 3.
* ``test_a_job_whose_newest_record_is_a_skip_is_dormant_not_failing`` -- called
  a fortnight-old failure "its last run" for a job switched off since.
* ``test_a_blocker_cleared_from_the_latest_run_is_not_live`` -- convicted a
  blocker the run log explicitly recorded as closed.
* ``test_a_blocker_on_a_switched_off_job_blocks_nothing`` -- reported a real
  blocker on a job nobody runs.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

from tools import awareness as aw

NOW = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def run(job, outcome, hours_ago=0, blockers=(), summary=""):
    stamp = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    return {
        "job": job,
        "outcome": outcome,
        "finished": stamp,
        "started": stamp,
        "summary": summary,
        "blockers": [{"key": b, "detail": b.replace("-", " ")} for b in blockers],
        "metrics": {},
    }


DAILY = aw.ScheduledJob(job="premarket", hours=(7,), minute=0, enabled=True)
JOURNAL = aw.ScheduledJob(job="journal", hours=(16,), minute=30, enabled=True)
OFF = aw.ScheduledJob(job="alerts", hours=(9, 10, 11), minute=0, enabled=False)


# ---------------------------------------------------------------------------
# Failure streaks: convict and acquit
# ---------------------------------------------------------------------------


def test_a_planted_failure_run_is_convicted():
    records = [run("premarket", "failed", h) for h in (72, 48, 24)]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    stopped = [o for o in obs if o.trigger == "stopped"]
    assert len(stopped) == 1
    assert "3 runs in a row" in stopped[0].summary
    assert stopped[0].severity == "act"


def test_a_healthy_job_raises_nothing():
    records = [run("premarket", "ok", h) for h in (72, 48, 24)]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    assert [o for o in obs if o.trigger] == []
    assert obs[0].severity == "info"


def test_one_bad_run_is_watch_not_act():
    # A single failure is a bad day. Promoting it would train the owner to
    # ignore the channel before it ever caught anything.
    records = [run("premarket", "ok", 48), run("premarket", "failed", 24)]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    assert obs[0].severity == "watch"
    assert obs[0].trigger == ""


def test_partial_does_not_count_as_a_failure():
    # Regression. runlog's vocabulary is (failed, partial, skipped, ok); a run
    # that did some of its job is not a run that did nothing.
    records = [
        run("premarket", "partial", 96),
        run("premarket", "partial", 72),
        run("premarket", "failed", 48),
        run("premarket", "failed", 24),
    ]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    stopped = [o for o in obs if o.trigger == "stopped"][0]
    assert "2 runs in a row" in stopped.summary


def test_a_skip_between_failures_is_transparent():
    # A holiday in the middle of a broken week is neither a failure nor a
    # recovery -- runlog makes that distinction load-bearing.
    records = [
        run("premarket", "failed", 96),
        run("premarket", "skipped", 72),
        run("premarket", "failed", 24),
    ]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    stopped = [o for o in obs if o.trigger == "stopped"][0]
    assert "2 runs in a row" in stopped.summary


def test_a_job_whose_newest_record_is_a_skip_is_dormant_not_failing():
    # Regression. Transparency applies between failures, never at the head.
    records = [
        run("premarket", "failed", 400),
        run("premarket", "skipped", 48),
        run("premarket", "skipped", 24),
    ]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    assert [o for o in obs if o.trigger] == []
    assert "skipped its last run" in obs[0].summary


# ---------------------------------------------------------------------------
# Blockers: live, stale, and irrelevant
# ---------------------------------------------------------------------------


def test_a_recurring_live_blocker_is_convicted_as_blocking():
    records = [
        run("premarket", "failed", h, blockers=("permission-missing",))
        for h in (72, 48, 24)
    ]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    blocking = [o for o in obs if o.trigger == "blocking"]
    assert len(blocking) == 1
    assert blocking[0].entity == "blocker:permission-missing"


def test_a_blocker_seen_once_is_not_yet_blocking():
    # Acquit. One appearance is a bad run, not a decision nobody has made.
    records = [
        run("premarket", "failed", 48),
        run("premarket", "failed", 24, blockers=("permission-missing",)),
    ]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    assert [o for o in obs if o.trigger == "blocking"] == []


def test_a_blocker_cleared_from_the_latest_run_is_not_live():
    # Regression, and the real case: journal-path-outside-session-working-
    # directory killed five runs and was explicitly recorded as closed. A
    # count over history alone convicted it again the next day.
    records = [
        run("premarket", "failed", h, blockers=("old-problem",)) for h in (96, 72, 48)
    ] + [run("premarket", "failed", 24, blockers=("new-problem",))]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    keys = {o.entity for o in obs if o.trigger == "blocking"}
    assert "blocker:old-problem" not in keys


def test_a_blocker_on_a_switched_off_job_blocks_nothing():
    # Regression. no-alerts-configured-on-agent-login is a real blocker on a
    # job disabled on purpose since 2026-08-29.
    records = [
        run("alerts", "failed", h, blockers=("no-alerts-configured",))
        for h in (72, 48, 24)
    ]
    obs = aw.observe_jobs(records, NOW, [OFF])
    assert obs == []


def test_with_no_schedule_every_job_is_treated_as_live():
    # The pure function stays usable on its own; the filter is opt-in.
    records = [
        run("alerts", "failed", h, blockers=("no-alerts-configured",))
        for h in (72, 48, 24)
    ]
    assert aw.observe_jobs(records, NOW) != []


# ---------------------------------------------------------------------------
# What is connected
# ---------------------------------------------------------------------------


def test_one_blocker_across_two_jobs_is_a_connection():
    records = [
        run("premarket", "failed", h, blockers=("shared",)) for h in (72, 48, 24)
    ] + [run("journal", "failed", h, blockers=("shared",)) for h in (72, 48, 24)]
    obs = aw.observe_jobs(records, NOW, [DAILY, JOURNAL])
    graph = aw.connections(obs)
    assert set(graph["blocker:shared"]) == {"job:premarket", "job:journal"}


def test_separate_blockers_are_never_linked():
    # Acquit. An invented edge is a shortcut across the whole graph -- the
    # over-linking defect tools/graph_audit.py convicts elsewhere.
    records = [
        run("premarket", "failed", h, blockers=("mine",)) for h in (72, 48, 24)
    ] + [run("journal", "failed", h, blockers=("yours",)) for h in (72, 48, 24)]
    graph = aw.connections(aw.observe_jobs(records, NOW, [DAILY, JOURNAL]))
    assert "job:journal" not in graph["blocker:mine"]
    assert "job:premarket" not in graph["blocker:yours"]


# ---------------------------------------------------------------------------
# What is changing -- and the refusal that matters most
# ---------------------------------------------------------------------------


def test_no_history_is_reported_as_unanswerable_not_as_calm():
    obs = aw.observe_jobs([run("premarket", "failed", 24)], NOW, [DAILY])
    changes, no_history = aw.changes(obs, [])
    assert no_history is True
    assert changes == []
    # The rendered form must say the question cannot be answered, not that
    # the world is calm. A layer reporting calm because it had never looked
    # before would be worse than one reporting nothing at all.
    section = aw.render(aw.assemble(obs, [], [DAILY], NOW))
    section = section.split("WHAT IS CHANGING")[1].split("WHAT IS LIKELY")[0]
    assert "Unanswerable" in section
    assert "Nothing, against the last recorded observation." not in section


def test_a_recovery_is_reported_as_a_change():
    before = aw.observe_jobs(
        [run("premarket", "failed", h) for h in (72, 48, 24)], NOW, [DAILY]
    )
    after = aw.observe_jobs([run("premarket", "ok", 1)], NOW, [DAILY])
    changes, no_history = aw.changes(after, before)
    assert no_history is False
    assert any(c.entity == "job:premarket" and "cleanly" in c.now for c in changes)


def test_an_unchanged_world_reports_no_changes():
    obs = aw.observe_jobs(
        [run("premarket", "failed", h) for h in (48, 24)], NOW, [DAILY]
    )
    changes, no_history = aw.changes(obs, obs)
    assert no_history is False
    assert changes == []


# ---------------------------------------------------------------------------
# What is likely next -- and what it refuses to guess
# ---------------------------------------------------------------------------


def test_a_live_blocker_projects_the_next_run_failing():
    records = [
        run("premarket", "failed", h, blockers=("permission-missing",))
        for h in (72, 48, 24)
    ]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    projections = aw.project(obs, [DAILY], NOW)
    assert any("fails the same way" in p.expectation for p in projections)


def test_nothing_is_projected_without_a_rule():
    # Acquit. A failing job with no identified blocker gets a schedule
    # projection and no forecast -- night_lab drops model output it cannot
    # check for the same reason.
    records = [run("premarket", "failed", h) for h in (72, 48, 24)]
    obs = aw.observe_jobs(records, NOW, [DAILY])
    projections = aw.project(obs, [DAILY], NOW)
    assert not any("fails the same way" in p.expectation for p in projections)
    assert all(p.rule for p in projections)


def test_a_disabled_job_gets_no_schedule_projection():
    assert aw.project([], [OFF], NOW) == []


def test_the_next_run_is_labelled_as_machine_local_time():
    # The scheduler runs in the machine's zone and this module is not told
    # which. Stamping a UTC offset on it would be a plausible wrong answer.
    projections = aw.project([], [DAILY], NOW)
    assert projections[0].when.endswith("machine local time")


# ---------------------------------------------------------------------------
# Attention and the safety gate
# ---------------------------------------------------------------------------


def test_an_untriggered_observation_is_never_promoted():
    obs = [
        aw.Observation(
            domain="fleet",
            entity="repo:x",
            summary="interesting",
            at="",
            severity="act",
        )
    ]
    assert aw.attention(obs) == []


def test_attention_is_ranked_worst_first():
    obs = [
        aw.Observation("fleet", "b", "watchable", "", "watch", "stopped"),
        aw.Observation("fleet", "a", "urgent", "", "act", "stopped"),
    ]
    assert [o.entity for o in aw.attention(obs)] == ["a", "b"]


def test_an_action_that_moves_money_is_never_proposed_as_safe():
    # The gate, asserted rather than trusted: agents move information, people
    # move money. Same doctrine as tools/ai_company.py's `gates`.
    obs = [aw.Observation("desk", "position:NVDA", "margin at 91%", "", "act", "money")]
    actions = aw.safest_actions(obs)
    assert actions and all(not a.safe for a in actions)
    assert "person" in actions[0].why or "money" in actions[0].why


def test_a_blocking_decision_is_routed_to_a_person():
    obs = [aw.Observation("fleet", "blocker:x", "stuck", "", "act", "blocking")]
    actions = aw.safest_actions(obs)
    assert actions and not actions[0].safe


def test_a_stopped_job_gets_a_read_only_next_step():
    obs = [aw.Observation("fleet", "job:x", "failed 3 in a row", "", "act", "stopped")]
    actions = aw.safest_actions(obs)
    assert actions[0].safe is True
    assert "read" in actions[0].step


# ---------------------------------------------------------------------------
# Silence: the failure this repository keeps meeting
# ---------------------------------------------------------------------------


def test_a_job_that_has_gone_quiet_is_convicted():
    records = [run("premarket", "ok", 24 * 5)]
    obs = aw.observe_schedule([DAILY], records, NOW)
    silent = [o for o in obs if o.trigger == "stopped"]
    assert len(silent) == 1
    assert "has not reported" in silent[0].summary


def test_a_job_that_ran_recently_is_not_convicted():
    records = [run("premarket", "ok", 3)]
    assert [o for o in aw.observe_schedule([DAILY], records, NOW) if o.trigger] == []


def test_one_missed_cycle_is_within_grace():
    # Acquit. A machine asleep for a night is not a broken job.
    records = [run("premarket", "ok", 30)]
    assert [o for o in aw.observe_schedule([DAILY], records, NOW) if o.trigger] == []


def test_a_scheduled_job_that_never_reported_is_convicted():
    obs = aw.observe_schedule([DAILY], [], NOW)
    assert any("never reported" in o.summary for o in obs)


def test_a_disabled_job_is_never_overdue():
    assert [o for o in aw.observe_schedule([OFF], [], NOW) if o.trigger] == []


def test_a_disabled_job_is_still_reported_once():
    # A job everybody forgot was switched off looks exactly like a healthy one.
    obs = aw.observe_schedule([OFF], [], NOW)
    assert any("switched off" in o.summary for o in obs)


def test_hourly_jobs_get_an_hourly_cycle():
    assert aw._cycle_hours((9, 10, 11)) == 1.0
    assert aw._cycle_hours((7,)) == 24.0


# ---------------------------------------------------------------------------
# Git: work that has not reached anywhere anyone else can see it
# ---------------------------------------------------------------------------


def test_unpushed_commits_are_convicted():
    facts = aw.GitFacts(branch="main", unpushed_tracked=("abc log entry",))
    obs = aw.observe_git(facts, NOW)
    assert any(o.trigger == "stopped" and o.entity == "repo:unpushed" for o in obs)


def test_a_clean_pushed_branch_raises_nothing():
    obs = aw.observe_git(aw.GitFacts(branch="main"), NOW)
    assert [o for o in obs if o.trigger] == []
    assert "clean and pushed" in obs[0].summary


def test_an_unpushed_branch_gets_the_one_safe_write_action():
    facts = aw.GitFacts(branch="main", unpushed_tracked=("abc log entry",))
    actions = aw.safest_actions(aw.observe_git(facts, NOW))
    assert actions[0].safe is True
    assert "push" in actions[0].step


# ---------------------------------------------------------------------------
# The edge: parsing the real scheduler table
# ---------------------------------------------------------------------------


def test_the_real_scheduler_table_parses():
    text = (aw.REPO_ROOT / "tools" / "register_desk_agent.ps1").read_text(
        encoding="utf-8"
    )
    jobs = {j.job: j for j in aw.parse_scheduled_jobs(text)}
    assert {"premarket", "alerts", "journal"} <= set(jobs)
    assert jobs["premarket"].enabled is True
    assert jobs["premarket"].hours == (7,)
    # Off since 2026-08-29 on purpose; the table is the record of that.
    assert jobs["alerts"].enabled is False
    assert jobs["alerts"].needs_desktop is True
    assert jobs["premarket"].needs_desktop is False


def test_the_log_round_trips(tmp_path):
    obs = [
        aw.Observation(
            "fleet",
            "job:x",
            "failed",
            "2026-09-01T00:00:00+00:00",
            "act",
            "stopped",
            metrics=(("n", 3.0),),
        )
    ]
    path = tmp_path / "log.jsonl"
    aw.append_log(obs, path)
    assert aw.load_log(path) == obs


def test_an_unparseable_log_line_is_dropped_not_guessed(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"domain": "fleet"\nnot json at all\n', encoding="utf-8")
    assert aw.load_log(path) == []


def test_an_unparseable_run_record_is_dropped(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text('{"job": "a", "outcome": "ok"}\nbroken\n', encoding="utf-8")
    assert len(aw.read_run_records(path)) == 1


def test_an_invalid_trigger_is_refused():
    import pytest

    with pytest.raises(ValueError):
        aw.Observation("fleet", "x", "y", "", "act", "vibes")


# ---------------------------------------------------------------------------
# End to end, against this repository
# ---------------------------------------------------------------------------


def test_the_brief_runs_against_this_repository(capsys):
    code = aw.main(["brief", "--log", "/nonexistent/log.jsonl"])
    out = capsys.readouterr().out
    assert "WHAT IS HAPPENING NOW" in out
    assert "WHAT ACTION IS SAFEST" in out
    # Exit 1 means something wants a person; either answer is legitimate here,
    # so only the contract is asserted.
    assert code in (0, 1)


def test_the_short_form_stays_short(capsys):
    aw.main(["brief", "--short", "--log", "/nonexistent/log.jsonl"])
    out = capsys.readouterr().out.strip()
    assert 0 < len(out.splitlines()) <= 6


def test_the_json_form_is_machine_readable(capsys):
    aw.main(["brief", "--json", "--log", "/nonexistent/log.jsonl"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["no_history"] is True
    assert "attention" in payload and "connections" in payload


def test_the_layer_names_what_it_cannot_see(capsys):
    aw.main(["sources"])
    out = capsys.readouterr().out
    assert "BLIND" in out
    # A domain the owner asked for and this slice does not cover reads exactly
    # like a domain with nothing wrong, unless it says so.
    assert "adapters not built yet" in out

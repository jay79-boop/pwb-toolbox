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
    # Every domain the owner asked for now has an adapter, so the thing that
    # must still be named out loud is a wired domain whose *source* is missing:
    # a checkout with no `signals/desk.json` and no `engagements/` sees neither,
    # and either one reads exactly like nothing being wrong unless it says so.
    assert "the desk (no signals/desk.json" in out
    assert "any engagement (nothing under engagements/" in out


def test_a_domain_with_a_bridge_and_no_signal_is_blind_rather_than_quiet(tmp_path):
    """desk and content are wired but carried, not read. No carrier, no claim.

    This is the same failure the whole layer exists to catch, one level up: an
    adapter that exists and has never been fed reads exactly like a domain with
    nothing wrong, so the absence of the signal is named rather than assumed.
    """
    (tmp_path / "tools").mkdir()
    _, _, blind = aw.collect(tmp_path, NOW)
    assert any("no signals/desk.json" in item for item in blind)
    assert any("no signals/content.json" in item for item in blind)


# ---------------------------------------------------------------------------
# Business: the engagement gates
#
# The pair that matters most here is the last one. Every other test asserts
# what this adapter *says*; `test_the_adapter_names_the_same_gate_engagement_
# actually_refuses_on` asserts it against `engagement.advance` itself, so a
# phase added to PHASES cannot leave the adapter describing a gate that no
# longer exists while every test still passes.
# ---------------------------------------------------------------------------


def build_engagement(root, name, stop_before, opened=dt.date(2026, 8, 20)):
    """A real engagement folder, advanced through the real gates to a phase.

    Uses ``engagement.advance`` rather than hand-writing the state file, so the
    fixture cannot drift into a shape the tracker would never produce.
    """
    from tools import engagement as eng

    data = eng.new_engagement(root, name, today=opened)
    slug = data["slug"]
    for phase in eng.PHASES:
        if phase.key == stop_before:
            break
        if phase.deliverable:
            (root / slug / phase.deliverable).write_text(
                f"what actually happened in {phase.key}", encoding="utf-8"
            )
        if phase.key == "present":
            (root / slug / eng.DECK_FILENAME).write_text("<html>", encoding="utf-8")
        eng.advance(
            root,
            slug,
            approved_by="A Stakeholder" if phase.key == "approval" else None,
            today=opened,
        )
    return slug


def engagements_root(tmp_path):
    folder = tmp_path / "engagements"
    folder.mkdir()
    return folder


def only(observations, entity):
    matches = [o for o in observations if o.entity == entity]
    assert len(matches) == 1, [o.entity for o in observations]
    return matches[0]


def test_an_engagement_waiting_on_approval_is_convicted_as_blocking(tmp_path):
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Elm Landscaping", "approval")
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "act"
    assert obs.trigger == "blocking"
    assert "approve" in obs.summary


def test_an_engagement_with_a_clear_gate_is_never_an_interruption(tmp_path):
    """The acquit half. A phase whose gate would pass is work in progress, and
    a layer that flagged it would flag every engagement on every run."""
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Cedar Dental", "audit")
    (root / slug / "01-audit.md").write_text("a real audit", encoding="utf-8")
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "info"
    assert obs.trigger == ""
    assert aw.attention([obs]) == []


def test_a_deliverable_that_is_still_the_seeded_reference_is_convicted(tmp_path):
    """The sneaky one: the file exists and is not empty, so every listing and
    every "is it written?" check reads as done. Only the marker separates it
    from a real deliverable, and ``advance`` refuses on exactly that."""
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Bolt Roofing", "audit")
    (root / slug / "01-audit.md").write_text(
        "# Audit\nUNEDITED REFERENCE\nsome generic business\n", encoding="utf-8"
    )
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "watch"
    assert "unedited reference" in obs.summary


def test_an_edited_deliverable_is_acquitted(tmp_path):
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Bolt Roofing", "audit")
    (root / slug / "01-audit.md").write_text(
        "# Audit\nBolt Roofing runs two vans and a shared inbox.\n", encoding="utf-8"
    )
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "info"


def test_unwritten_work_is_watch_and_never_carries_a_trigger(tmp_path):
    """A deliverable nobody has written is real, and it is *work*, not an
    undecided *decision*. Only approval is blocking, and this is the test that
    stops the trigger spreading to everything that looks stuck."""
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Acme Plumbing", "audit")
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "watch"
    assert obs.trigger == ""


def test_a_deck_that_was_never_built_blocks_the_presentation(tmp_path):
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Dune Logistics", "present")
    (root / slug / "08-feedback.md").write_text("they liked it", encoding="utf-8")
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "watch"
    assert "deck" in obs.summary


def test_a_finished_engagement_is_reported_once_and_gates_nothing(tmp_path):
    root = engagements_root(tmp_path)
    slug = build_engagement(root, "Fir Dental", stop_before="")
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), f"engagement:{slug}")
    assert obs.severity == "info"
    assert "completed all twelve phases" in obs.summary


def test_the_adapter_names_the_same_gate_engagement_actually_refuses_on(tmp_path):
    """The adapter is a *prediction* of what ``advance`` would say. This is the
    test that holds it to that, in both directions: four planted states where
    the real gate must refuse, and one where it must pass."""
    from tools import engagement as eng

    root = engagements_root(tmp_path)
    cases = {}

    cases["missing"] = (build_engagement(root, "Acme Plumbing", "audit"), False)

    seeded = build_engagement(root, "Bolt Roofing", "audit")
    (root / seeded / "01-audit.md").write_text("UNEDITED REFERENCE\n", encoding="utf-8")
    cases["seeded"] = (seeded, False)

    clear = build_engagement(root, "Cedar Dental", "audit")
    (root / clear / "01-audit.md").write_text("a real audit", encoding="utf-8")
    cases["clear"] = (clear, True)

    deck = build_engagement(root, "Dune Logistics", "present")
    (root / deck / "08-feedback.md").write_text("feedback", encoding="utf-8")
    cases["deck"] = (deck, False)

    cases["approver"] = (build_engagement(root, "Elm Landscaping", "approval"), False)

    facts = {f.slug: f for f in aw.read_engagements(tmp_path)[0]}
    for label, (slug, should_pass) in cases.items():
        predicted_clear = facts[slug].gate == aw.GATE_CLEAR
        try:
            eng.advance(root, slug, today=dt.date(2026, 9, 2))
            really_passed = True
        except eng.EngagementError:
            really_passed = False
        assert predicted_clear == really_passed == should_pass, label


# ---------------------------------------------------------------------------
# Business: the money gates in the company blueprint
# ---------------------------------------------------------------------------


def gate_finding(code, process="proc-job", step=7, title="Place the supplier order"):
    return {
        "process": process,
        "step": step,
        "title": title,
        "code": code,
        "message": f"{code} message",
    }


def test_an_ungated_ai_commit_is_convicted_as_money():
    obs = aw.observe_business([], [gate_finding("ungated_ai_commit")], NOW)
    finding = only(obs, "gate:proc-job:7")
    assert finding.severity == "act"
    assert finding.trigger == "money"
    assert finding.depends_on == ("blueprint:one-person-ai-company",)


def test_a_warning_gate_is_counted_and_never_raised():
    """The acquit half. A partial gate is a threshold to confirm, and
    automation issuing an invoice is the architecture working. Raising either
    would put a permanent interruption on a permanent fact."""
    obs = aw.observe_business(
        [],
        [gate_finding("partial_gate"), gate_finding("automation_commits", step=20)],
        NOW,
    )
    assert aw.attention(obs) == []
    summary = only(obs, "blueprint:one-person-ai-company")
    assert "0 ungated" in summary.summary
    assert "2 flagged" in summary.summary


def test_a_step_that_only_touches_a_money_tool_is_never_reported():
    """``unmarked_touch`` is ai_company's "list to confirm, not a verdict", and
    the committed blueprint produces nine of them. Nine permanent info lines is
    how a brief stops being read."""
    obs = aw.observe_business([], [gate_finding("unmarked_touch")] * 9, NOW)
    assert [o.entity for o in obs] == ["blueprint:one-person-ai-company"]
    assert "0 ungated AI commit step(s) and 0 flagged" in obs[0].summary


def test_the_committed_blueprint_convicts_nothing_as_money():
    """Against the real document, not a fixture. If this ever fails, either the
    blueprint grew an ungated commit or the gate broke -- both worth stopping
    for."""
    gates, blind = aw.read_blueprint_gates(aw.REPO_ROOT)
    assert blind == []
    assert gates, "the blueprint parsed to no findings at all"
    assert aw.attention(aw.observe_business([], gates, NOW)) == []


def test_money_from_the_business_domain_is_never_a_safe_action():
    obs = aw.observe_business([], [gate_finding("ungated_ai_commit")], NOW)
    action = aw.safest_actions(obs)[0]
    assert action.safe is False
    assert "money" in action.why


def test_the_blueprint_edge_is_declared_and_engagements_are_never_linked(tmp_path):
    """No edge is inferred. Two engagements stuck in the same way share
    nothing, and the only business edge points at the document the finding was
    read out of."""
    root = engagements_root(tmp_path)
    build_engagement(root, "Acme Plumbing", "audit")
    build_engagement(root, "Bolt Roofing", "audit")
    facts, _ = aw.read_engagements(tmp_path)

    graph = aw.connections(
        aw.observe_business(facts, [gate_finding("ungated_ai_commit")], NOW)
    )
    assert graph["engagement:acme-plumbing"] == []
    assert graph["engagement:bolt-roofing"] == []
    assert graph["gate:proc-job:7"] == ["blueprint:one-person-ai-company"]


# ---------------------------------------------------------------------------
# Business: what it refuses to turn into a verdict
# ---------------------------------------------------------------------------


def test_how_long_it_has_been_stuck_is_a_metric_and_never_a_trigger(tmp_path):
    """The owner declined a thresholds trigger. A count of days is evidence for
    the session reading the brief, and nothing here promotes it."""
    root = engagements_root(tmp_path)
    build_engagement(root, "Acme Plumbing", "audit", opened=dt.date(2026, 1, 1))
    facts, _ = aw.read_engagements(tmp_path)

    obs = only(aw.observe_business(facts, [], NOW), "engagement:acme-plumbing")
    assert dict(obs.metrics)["days_at_this_phase"] > 200
    assert obs.trigger == ""
    assert obs.severity == "watch"


def test_the_day_count_never_reaches_the_summary_so_nothing_changes_at_midnight(
    tmp_path,
):
    """`changes` diffs summaries. A day count in the summary would report every
    open engagement as changed every single day, and a delta that always fires
    carries no information at all."""
    root = engagements_root(tmp_path)
    build_engagement(root, "Acme Plumbing", "audit")
    facts, _ = aw.read_engagements(tmp_path)

    today = aw.observe_business(facts, [], NOW)
    next_week = aw.observe_business(facts, [], NOW + dt.timedelta(days=7))
    delta, no_history = aw.changes(next_week, today)
    assert (delta, no_history) == ([], False)


def test_an_empty_engagements_folder_is_a_blind_spot_not_silence(tmp_path):
    """A domain with no data reads exactly like a domain with nothing wrong.
    ``engagements/`` is gitignored, so this is the normal case in the cloud."""
    engagements_root(tmp_path)
    facts, blind = aw.read_engagements(tmp_path)
    assert facts == []
    assert any("engagement" in note for note in blind)


def test_a_missing_engagements_folder_is_named_rather_than_assumed(tmp_path):
    facts, blind = aw.read_engagements(tmp_path)
    assert facts == []
    assert blind and "does not exist" in blind[0]


# ---------------------------------------------------------------------------
# Business: end to end
# ---------------------------------------------------------------------------


def test_the_business_domain_reaches_the_brief(capsys):
    """The whole point of a domain-agnostic schema: the adapter dropped in and
    the assembly did not change. This asserts it arrived."""
    assert aw.main(["brief", "--json", "--log", "/nonexistent/log.jsonl"]) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    domains = {o["domain"] for o in payload["now"]}
    assert "business" in domains
    assert not any("businesses" in note for note in payload["blind"])


def test_the_businesses_are_no_longer_named_as_an_unwired_domain(capsys):
    """The line this adapter exists to delete. Every other domain that got
    wired removed its own; leaving it would tell the owner on every run that
    something is unwatched when it is not."""
    aw.main(["sources"])
    out = capsys.readouterr().out
    assert "the businesses -- adapter not built yet" not in out
    assert "blueprint:one-person-ai-company" in out


def test_the_cli_runs_as_a_script_without_pythonpath():
    """The business adapter imports ``tools.engagement`` and ``tools.ai_company``,
    which run as a script are not importable without the sys.path bootstrap:
    Python puts ``tools/`` on sys.path, not the repository root. Every test here
    imports ``tools.awareness`` as a module, which pytest makes importable
    regardless, so only a subprocess with PYTHONPATH stripped and a cwd outside
    the repository convicts."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(aw.REPO_ROOT / "tools" / "awareness.py"), "sources"],
        cwd=str(aw.REPO_ROOT.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "blueprint:one-person-ai-company" in proc.stdout

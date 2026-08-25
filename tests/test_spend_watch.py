"""The drain detector, exercised on synthetic snapshots.

The load-bearing test is the last one: a single snapshot must never produce a
rate. Session metadata reports a *lifetime* metered total, so deriving a burn
rate from one file silently turns a figure accumulated over a day into an
apparent hourly one -- which is exactly the misreading the incident this tool
was written for produced twice.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tools.spend_watch import (
    CONCURRENCY_HORIZON,
    FIVE_HOURS,
    audit,
    cache_reads,
    find_concurrency,
    find_duplicate_triggers,
    find_fat_sessions,
    find_persistent_triggers,
    find_rate,
    find_self_rearming,
    find_session_size,
    latest_activity,
    limit_type,
    metered,
    parse_time,
    read_transcript,
    render,
    transcript_usage,
    window_span,
    window_start,
)

RESET = 1787594400  # 2026-08-24 18:00 UTC


def session(sid, *, updated, cost=1.0, reads=0, resets=RESET, title=None, limit=None):
    info = {"resetsAt": resets}
    if limit:
        info["rateLimitType"] = limit
    return {
        "id": sid,
        "title": title or sid,
        "updated_at": updated,
        "external_metadata": {
            "rate_limit_info": info,
            "usage": {"cost_usd": cost, "cache_read_tokens": reads},
        },
    }


def trigger(tid, prompt, *, persistent=None, name=None):
    t = {
        "id": tid,
        "name": name or tid,
        "job_config": {"ccr": {"events": [{"data": {"message": {"content": prompt}}}]}},
    }
    if persistent:
        t["persistent_session_id"] = persistent
    return t


# --- timestamp parsing ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "2026-08-24T15:02:14Z",
        "2026-08-24T15:02:14.502834Z",
        "2026-08-24T15:02:14.502834981Z",  # more than six digits
        "2026-08-24T15:02:14+00:00",
    ],
)
def test_parse_time_accepts_real_shapes(text):
    parsed = parse_time(text)
    assert parsed is not None and parsed.tzinfo is not None


@pytest.mark.parametrize("text", [None, "", "not a date"])
def test_parse_time_rejects_junk(text):
    assert parse_time(text) is None


# --- the self-re-arming pattern -------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "If nothing changed, re-arm this check-in about an hour out.",
        "re-arm another check-in ~3 hours out silently",
        "Otherwise schedule the next check for later.",
        "Please REARM yourself tomorrow.",
    ],
)
def test_self_rearming_detected(prompt):
    findings = find_self_rearming([trigger("t1", prompt, persistent="s1")])
    assert [f.code for f in findings] == ["self-rearming-persistent"]
    assert findings[0].severity == "high"


def test_rearm_without_persistent_binding_is_not_high():
    """A cron that re-arms is odd but cheap; the cost comes from persistence."""

    assert find_self_rearming([trigger("t1", "re-arm in an hour")]) == []


def test_persistent_without_rearm_is_medium():
    findings = find_persistent_triggers(
        [trigger("t1", "Check CI and stop.", persistent="s1")]
    )
    assert [f.severity for f in findings] == ["medium"]


def test_a_trigger_is_never_reported_twice():
    """The high finding supersedes the medium one for the same Routine."""

    triggers = [trigger("t1", "re-arm an hour out", persistent="s1")]
    codes = [
        f.code
        for f in find_self_rearming(triggers) + find_persistent_triggers(triggers)
    ]
    assert codes == ["self-rearming-persistent"]


def test_plain_cron_trigger_is_clean():
    triggers = [trigger("t1", "Check CI. Do not re-arm yourself.")]
    assert find_persistent_triggers(triggers) == []


# --- fat sessions ---------------------------------------------------------


def test_fat_session_flagged_above_threshold():
    findings = find_fat_sessions(
        [session("s1", updated="2026-08-24T15:00:00Z", reads=68_000_000)]
    )
    assert findings and findings[0].code == "expensive-to-wake"
    assert "68.0M" in findings[0].title


def test_small_session_not_flagged():
    assert (
        find_fat_sessions([session("s1", updated="2026-08-24T15:00:00Z", reads=5_000)])
        == []
    )


# --- concurrency ----------------------------------------------------------


def test_concurrency_flagged_when_many_sessions_are_live():
    cutoff = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    sessions = [session(f"s{i}", updated="2026-08-24T14:00:00Z") for i in range(8)]
    findings = find_concurrency(sessions, cutoff)
    assert findings and findings[0].severity == "high"
    assert len(findings[0].subjects) == 8


def test_sessions_outside_the_window_do_not_count():
    cutoff = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    sessions = [session(f"s{i}", updated="2026-08-20T01:00:00Z") for i in range(8)]
    assert find_concurrency(sessions, cutoff) == []


def test_window_start_is_five_hours_before_the_reset():
    """Unstated limit type falls back to the shorter, safer span."""

    start = window_start([session("s1", updated="2026-08-24T15:00:00Z")])
    assert start == datetime.fromtimestamp(RESET, tz=timezone.utc) - FIVE_HOURS


def test_window_start_is_none_without_rate_limit_info():
    assert window_start([{"id": "s1"}]) is None


# --- malformed input ------------------------------------------------------


def test_missing_usage_reads_as_zero_not_a_crash():
    assert metered({"id": "s1"}) == 0.0
    assert cache_reads({"id": "s1"}) == 0


def test_non_numeric_usage_reads_as_zero():
    bad = {
        "external_metadata": {"usage": {"cost_usd": "oops", "cache_read_tokens": None}}
    }
    assert metered(bad) == 0.0
    assert cache_reads(bad) == 0


def test_audit_tolerates_empty_payload():
    assert audit({}) == []
    assert "No findings" in render([])


# --- the rule that matters ------------------------------------------------


def test_a_single_snapshot_never_produces_a_rate():
    """Lifetime totals cannot become a rate without something to diff against."""

    sessions = [
        session(f"s{i}", updated="2026-08-24T14:00:00Z", cost=290.0) for i in range(9)
    ]
    findings = audit({"sessions": sessions})
    assert findings, "structural findings should still fire"
    assert not any(f.code == "growth" for f in findings)


def test_rate_appears_only_with_a_baseline():
    before = [session("s1", updated="2026-08-24T13:00:00Z", cost=10.0)]
    after = [session("s1", updated="2026-08-24T14:00:00Z", cost=25.5)]
    findings = find_rate(after, before)
    assert [f.code for f in findings] == ["growth"]
    assert "15.50" in findings[0].title


def test_unchanged_sessions_report_no_growth():
    snap = [session("s1", updated="2026-08-24T14:00:00Z", cost=10.0)]
    assert find_rate(snap, snap) == []


def test_a_new_session_counts_its_whole_total_as_growth():
    after = [session("new", updated="2026-08-24T14:00:00Z", cost=4.0)]
    findings = find_rate(after, [])
    assert findings and "4.00" in findings[0].title


# --- ordering and rendering -----------------------------------------------


def test_findings_are_ordered_most_severe_first():
    payload = {
        "sessions": [
            session(f"s{i}", updated="2026-08-24T14:00:00Z", reads=20_000_000)
            for i in range(7)
        ],
        "triggers": [trigger("t1", "re-arm in an hour", persistent="s1")],
    }
    severities = [f.severity for f in audit(payload)]
    assert severities == sorted(
        severities, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s]
    )


def test_render_truncates_long_subject_lists():
    payload = {
        "sessions": [session(f"s{i}", updated="2026-08-24T14:00:00Z") for i in range(9)]
    }
    text = render(audit(payload))
    assert "and 5 more" in text


# --- which clock is binding ----------------------------------------------
#
# The bug these pin: window_start subtracted five hours from resetsAt whatever
# the limit actually was. With a seven-day limit in force on 2026-08-24 that
# put the window start four days in the FUTURE, so every check filtered by it
# measured an empty set and passed. A green check that measured nothing is the
# failure mode this whole file exists to prevent.


def test_window_start_follows_a_seven_day_limit():
    sessions = [session("s1", updated="2026-08-24T15:00:00Z", limit="seven_day")]
    assert window_start(sessions) == datetime.fromtimestamp(
        RESET, tz=timezone.utc
    ) - timedelta(days=7)


def test_window_start_never_lands_in_the_future():
    """The original defect, stated as the property it broke."""

    sessions = [session("s1", updated="2026-08-24T15:00:00Z", limit="seven_day")]
    assert window_start(sessions) < parse_time("2026-08-24T15:00:00Z")


def test_window_span_defaults_to_five_hours_when_unstated():
    assert window_span([session("s1", updated="2026-08-24T15:00:00Z")]) == FIVE_HOURS
    assert limit_type([session("s1", updated="2026-08-24T15:00:00Z")]) is None


def test_latest_activity_is_the_newest_timestamp():
    sessions = [
        session("s1", updated="2026-08-24T15:00:00Z"),
        session("s2", updated="2026-08-24T19:30:00Z"),
        session("s3", updated="2026-08-24T04:00:00Z"),
    ]
    assert latest_activity(sessions) == parse_time("2026-08-24T19:30:00Z")


def test_latest_activity_is_none_without_timestamps():
    assert latest_activity([{"id": "s1"}]) is None


def test_concurrency_survives_a_seven_day_limit():
    """Convict: many sessions at once must still be found under a weekly clock."""

    sessions = [
        session(f"s{i}", updated="2026-08-24T22:00:00Z", limit="seven_day")
        for i in range(8)
    ]
    findings = audit({"sessions": sessions})
    assert "concurrency" in [f.code for f in findings]


def test_a_week_of_finished_work_is_not_concurrency():
    """Acquit: spreading the same sessions over the weekly window must be quiet."""

    sessions = [
        session(f"s{i}", updated=f"2026-08-{18 + i}T09:00:00Z", limit="seven_day")
        for i in range(7)
    ]
    findings = audit({"sessions": sessions})
    assert "concurrency" not in [f.code for f in findings]


# --- re-arm detection must not fire on the cure --------------------------
#
# Every Routine prompt on the account was edited on 2026-08-24 to end with
# "do NOT re-arm yourself". A substring search flags precisely the Routines
# that were fixed.


@pytest.mark.parametrize(
    "prompt",
    [
        "Do NOT create a follow-up check-in and do NOT re-arm yourself.",
        "One check per firing, then stop. Never re-arm yourself.",
        "An earlier version re-armed itself every ~3 hours into a long-lived session.",
        "The previous version re-armed itself and became a significant token expense.",
        "This fires on a fixed cron instead of re-arming from inside its own prompt.",
    ],
)
def test_a_prohibition_or_a_history_lesson_is_not_a_re_arm(prompt):
    assert find_self_rearming([trigger("t1", prompt, persistent="s1")]) == []


def test_a_prohibition_still_leaves_the_persistent_binding_reported():
    """Acquitting the re-arm must not acquit the expensive part."""

    triggers = [trigger("t1", "Do NOT re-arm yourself.", persistent="s1")]
    assert [f.code for f in find_persistent_triggers(triggers)] == [
        "persistent-session-trigger"
    ]


def test_a_prohibition_next_to_a_real_instruction_is_still_caught():
    """Convict: one negated mention must not launder a directive one."""

    prompt = "Do not re-arm hourly. Instead, schedule the next check in 30 minutes."
    findings = find_self_rearming([trigger("t1", prompt, persistent="s1")])
    assert [f.code for f in findings] == ["self-rearming-persistent"]


# --- duplicate Routines --------------------------------------------------


def cron_trigger(tid, prompt, cron, *, enabled=True, name=None, persistent=None):
    t = trigger(tid, prompt, name=name, persistent=persistent)
    t["cron_expression"] = cron
    t["enabled"] = enabled
    return t


SPEC_DESK = (
    "Spec-desk stop/target check for two crypto paper trades. Get prices from "
    "Alpha Vantage, compare XRP and DOGE against their stops and targets, act "
    "only if a level has traded, then end the session silently."
)


def test_duplicate_routines_on_one_cron_are_flagged():
    """Convict: the real 2026-08-24 pair, sixty-two seconds apart."""

    triggers = [
        cron_trigger("t1", SPEC_DESK, "0 2,14 * * *", name="Spec desk"),
        cron_trigger(
            "t2",
            SPEC_DESK + " One check per firing, then stop.",
            "0 2,14 * * *",
            name="Spec desk (2x daily)",
        ),
    ]
    findings = find_duplicate_triggers(triggers)
    assert [f.code for f in findings] == ["duplicate-trigger"]
    assert findings[0].severity == "high"
    assert set(findings[0].subjects) == {"t1", "t2"}


def test_the_same_job_on_different_crons_is_not_a_double_fire():
    triggers = [
        cron_trigger("t1", SPEC_DESK, "0 2,14 * * *"),
        cron_trigger("t2", SPEC_DESK, "0 6 * * *"),
    ]
    assert find_duplicate_triggers(triggers) == []


def test_different_jobs_sharing_a_cron_are_not_duplicates():
    """Acquit: a shared schedule is not a shared purpose."""

    triggers = [
        cron_trigger("t1", SPEC_DESK, "0 2,14 * * *"),
        cron_trigger(
            "t2",
            "Merge the exported Grok chat history and group it by topic.",
            "0 2,14 * * *",
        ),
    ]
    assert find_duplicate_triggers(triggers) == []


def test_a_disabled_twin_does_not_double_fire():
    triggers = [
        cron_trigger("t1", SPEC_DESK, "0 2,14 * * *"),
        cron_trigger("t2", SPEC_DESK, "0 2,14 * * *", enabled=False),
    ]
    assert find_duplicate_triggers(triggers) == []


def test_a_routine_with_no_cron_is_not_compared():
    """A one-shot has no schedule to collide on."""

    triggers = [
        cron_trigger("t1", SPEC_DESK, ""),
        cron_trigger("t2", SPEC_DESK, ""),
    ]
    assert find_duplicate_triggers(triggers) == []


# --- this session's own size ---------------------------------------------


def turn(reads=0, out=0, writes=0):
    return {
        "message": {
            "usage": {
                "cache_read_input_tokens": reads,
                "cache_creation_input_tokens": writes,
                "output_tokens": out,
            }
        }
    }


def test_a_big_session_is_flagged():
    usage = transcript_usage([turn(reads=30_000_000, out=100_000)])
    findings = find_session_size(usage)
    assert [f.code for f in findings] == ["session-size"]
    assert findings[0].severity == "medium"


def test_a_small_session_is_silent():
    """Acquit -- the load-bearing half. A warning on every session is wallpaper."""

    assert find_session_size(transcript_usage([turn(reads=200_000, out=9_000)])) == []


def test_size_tiers_escalate():
    severities = [
        find_session_size(transcript_usage([turn(reads=reads, out=1_000)]))[0].severity
        for reads in (12_000_000, 30_000_000, 60_000_000)
    ]
    assert severities == ["low", "medium", "high"]


def test_transcript_usage_sums_across_turns_and_ignores_the_rest():
    records = [
        turn(reads=1_000, out=10),
        {"type": "queue-operation"},
        {"message": {"role": "user"}},
        turn(reads=2_500, out=40, writes=7),
    ]
    assert transcript_usage(records) == {
        "cache_read_tokens": 3_500,
        "cache_write_tokens": 7,
        "input_tokens": 0,
        "output_tokens": 50,
        "turns": 2,
    }


def test_a_session_with_no_output_reports_no_ratio():
    """Zero output must read as 'n/a', never divide by zero."""

    findings = find_session_size(transcript_usage([turn(reads=11_000_000, out=0)]))
    assert "n/a" in findings[0].detail


def test_read_transcript_skips_malformed_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"message": {"usage": {"cache_read_input_tokens": 5}}}\n'
        "not json at all\n"
        "\n"
        "[1, 2, 3]\n"
        '{"message": {"usage": {"cache_read_input_tokens": 7}}}\n',
        encoding="utf-8",
    )
    assert transcript_usage(read_transcript(str(path)))["cache_read_tokens"] == 12


def test_a_missing_transcript_is_not_a_crash():
    """This runs from a hook on every prompt; it must never break the session."""

    assert read_transcript("/no/such/transcript.jsonl") == []
    assert find_session_size(transcript_usage([])) == []

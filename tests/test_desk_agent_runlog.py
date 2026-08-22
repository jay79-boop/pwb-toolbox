"""Checks on the desk agent's run log.

What a memory for an unattended agent has to get right is not storage, it is
refusing to flatter itself. Three ways the obvious implementation lies, and all
three are pinned here: counting a skipped run as a success, counting a run that
did nothing as a failure, and counting one recurring problem as several
one-offs because the timestamps in its message differ.
"""

import json

import pytest

from tools.desk_agent.runlog import (
    Blocker,
    LogError,
    RunRecord,
    append_record,
    blocker_key,
    dead_jobs,
    main,
    outcome_trend,
    read_records,
    recurring_blockers,
    review,
    summarize,
)


def rec(job="premarket", outcome="ok", actions=(), blockers=(), **kw):
    return RunRecord(
        job=job,
        outcome=outcome,
        actions=list(actions),
        blockers=list(blockers),
        **kw,
    )


# ------------------------------------------------------------ round tripping --


def test_append_then_read_preserves_the_record(tmp_path):
    log = tmp_path / "runs.jsonl"
    append_record(rec(summary="three candidates", actions=["deployed OB-FVG"]), log)
    append_record(rec(job="journal", outcome="skipped"), log)

    back = read_records(log)
    assert [r.job for r in back] == ["premarket", "journal"]
    assert back[0].summary == "three candidates"
    assert back[0].actions == ["deployed OB-FVG"]
    assert back[1].outcome == "skipped"


def test_every_record_is_exactly_one_line(tmp_path):
    # A newline inside a record would split it in two and corrupt the next read.
    log = tmp_path / "runs.jsonl"
    append_record(rec(summary="line one\nline two", actions=["a\nb"]), log)
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1
    assert read_records(log)[0].summary == "line one\nline two"


def test_a_corrupt_line_costs_only_that_record(tmp_path):
    log = tmp_path / "runs.jsonl"
    append_record(rec(summary="before"), log)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
        fh.write(json.dumps({"job": "x", "outcome": "nonsense"}) + "\n")
    append_record(rec(summary="after"), log)

    back = read_records(log)
    assert [r.summary for r in back] == ["before", "after"]


def test_an_unknown_outcome_is_refused_at_the_door(tmp_path):
    with pytest.raises(LogError):
        rec(outcome="fine")
    with pytest.raises(LogError):
        RunRecord(job="", outcome="ok")


def test_missing_timestamps_are_filled_and_ordered(tmp_path):
    record = rec()
    assert record.finished
    assert record.started == record.finished


# ------------------------------------------------- skipped is not failed --


def test_a_week_of_clean_skips_does_not_read_as_healthy():
    # The specific failure this design exists to catch: a scheduler firing
    # faithfully into a job that never does anything, reporting no errors.
    records = [rec(outcome="skipped") for _ in range(7)]
    summary = summarize(records)

    assert summary.by_outcome.get("failed", 0) == 0
    assert summary.actions == 0
    assert summary.healthy is False


def test_health_needs_both_no_failures_and_something_done():
    assert summarize([rec(actions=["did a thing"])]).healthy is True
    assert summarize([rec(outcome="failed", actions=["tried"])]).healthy is False


def test_skipped_runs_do_not_count_towards_dead_jobs():
    # A job that never got the chance to act has not been given one.
    skipped = [rec(job="alerts", outcome="skipped") for _ in range(9)]
    assert dead_jobs(skipped, min_runs=5) == []

    tried = [rec(job="alerts", outcome="ok") for _ in range(5)]
    assert dead_jobs(tried, min_runs=5) == ["alerts"]


def test_a_correct_quiet_run_is_not_dead_weight():
    # Four honest "found nothing" runs and one that acted: not dead weight.
    records = [rec(job="premarket") for _ in range(4)]
    records.append(rec(job="premarket", actions=["deployed a strategy"]))
    assert dead_jobs(records, min_runs=5) == []


# ------------------------------------------- one problem is not three problems --


def test_blocker_key_collapses_timestamps_paths_and_numbers():
    a = blocker_key("connection refused on 127.0.0.1:9222 at 07:01:33")
    b = blocker_key("Connection refused on 127.0.0.1:9222 at 07:02:10")
    assert a == b

    c = blocker_key(r"cannot read C:\Users\Gexio\OneDrive\thing.txt")
    d = blocker_key(r"cannot read C:\Users\Gexio\other\else.txt")
    assert c == d


def test_blocker_key_still_separates_genuinely_different_problems():
    assert blocker_key("connection refused on 9222") != blocker_key(
        "tradingview not running"
    )


def test_blocker_with_no_words_still_gets_a_key():
    assert blocker_key("2026 07:01 12345") == "unknown"


def test_recurring_blockers_counts_the_slug_not_the_message():
    records = [
        rec(outcome="failed", blockers=[f"connection refused on 9222 at 07:0{i}:00"])
        for i in range(3)
    ]
    found = recurring_blockers(records, min_count=3)

    assert len(found) == 1
    assert found[0].count == 3
    assert found[0].jobs == ["premarket"]
    assert "07:0" in found[0].latest_detail


def test_a_blocker_below_the_threshold_is_not_raised():
    records = [rec(outcome="failed", blockers=["a one-off wobble"])]
    assert recurring_blockers(records, min_count=3) == []


def test_an_explicit_key_beats_the_derived_one():
    blocker = Blocker.of({"key": "cdp-port-closed", "detail": "refused at 07:01"})
    assert blocker.key == "cdp-port-closed"
    records = [rec(outcome="failed", blockers=[blocker]) for _ in range(3)]
    assert recurring_blockers(records)[0].key == "cdp-port-closed"


def test_blockers_are_ranked_worst_first():
    records = [rec(outcome="failed", blockers=["port refused"]) for _ in range(5)]
    records += [
        rec(outcome="failed", blockers=["pine compile error"]) for _ in range(3)
    ]
    found = recurring_blockers(records, min_count=3)
    assert [b.count for b in found] == [5, 3]


# ------------------------------------------------------------------- trend --


def test_trend_declines_to_guess_from_too_few_runs():
    assert outcome_trend([rec() for _ in range(3)]) == "not enough runs to say"


def test_trend_reads_improvement_and_regression():
    improving = [rec(outcome="failed") for _ in range(4)] + [rec() for _ in range(4)]
    assert outcome_trend(improving).startswith("improving")

    regressing = [rec() for _ in range(4)] + [rec(outcome="failed") for _ in range(4)]
    assert outcome_trend(regressing).startswith("regressing")


def test_skipped_runs_do_not_move_the_trend():
    records = [rec(outcome="skipped") for _ in range(20)]
    assert outcome_trend(records) == "not enough runs to say"


# ------------------------------------------------------------------ review --


def test_review_of_an_empty_log_says_so(tmp_path):
    assert "Nothing to review" in review(read_records(tmp_path / "none.jsonl"))


def test_review_names_the_recurring_blocker_and_the_dead_job():
    records = [
        rec(outcome="failed", blockers=["connection refused on 9222 at 07:0%d:00" % i])
        for i in range(3)
    ]
    records += [rec(job="alerts", outcome="ok") for _ in range(5)]
    text = review(records)

    assert "Recurring blockers" in text
    assert "connection-refused-on-at" in text
    assert "alerts" in text.split("never produced an action")[1]


# --------------------------------------------------------------------- cli --


def test_cli_append_then_review_round_trips(tmp_path, capsys):
    log = tmp_path / "runs.jsonl"
    for i in range(3):
        code = main(
            [
                "--log",
                str(log),
                "append",
                "--job",
                "premarket",
                "--outcome",
                "failed",
                "--summary",
                "could not reach the chart",
                "--blocker",
                f"connection refused on 9222 at 07:0{i}:00",
                "--metric",
                "candidates=0",
            ]
        )
        assert code == 0
    capsys.readouterr()

    assert main(["--log", str(log), "review"]) == 0
    out = capsys.readouterr().out
    assert "3 runs" in out
    assert "connection-refused-on-at" in out


def test_cli_summary_reports_unhealthy_for_silent_skips(tmp_path, capsys):
    log = tmp_path / "runs.jsonl"
    main(["--log", str(log), "append", "--job", "journal", "--outcome", "skipped"])
    capsys.readouterr()

    main(["--log", str(log), "summary"])
    assert "healthy: no" in capsys.readouterr().out


def test_cli_rejects_a_malformed_metric(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "--log",
                str(tmp_path / "runs.jsonl"),
                "append",
                "--job",
                "x",
                "--outcome",
                "ok",
                "--metric",
                "candidates",
            ]
        )

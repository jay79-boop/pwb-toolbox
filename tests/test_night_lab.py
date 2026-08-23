"""night_lab — the overnight stress lab's policy, arithmetic, and filters.

The contract this pins down:

    - the run/yield/stop policy is correct at every hour and idle state,
      including a window that wraps midnight, without waiting for 3am;
    - every reported number comes from arithmetic here, not from the model;
    - model output that cannot be checked is dropped, not repaired — an
      attack with no falsifier, a pattern with too few trades, a scenario
      that will not parse;
    - the runner checkpoints after each job and yields to a person at the
      keyboard without losing completed work.

No network and no model: the Ollama transport is injected, and the runner's
clock, idle timer and sleep are all injectable.
"""

import json

import pytest

from tools.night_lab import (
    RUN,
    STOP,
    YIELD,
    Ollama,
    apply_shock,
    build_queue,
    build_verdict,
    cliff_score,
    dedupe_attacks,
    in_window,
    make_job,
    max_drawdown,
    next_action,
    parse_json_block,
    pending,
    proposals_from,
    read_queue,
    resample_paths,
    run_leaks,
    run_night,
    run_redteam,
    shock_trade_r,
    trade_r,
    validate_attack,
    validate_shock,
    verify_pattern,
    write_queue,
)


def closed(**over):
    base = dict(
        id="T1",
        lane="swing-buy",
        symbol="NVDA",
        direction="long",
        status="closed",
        entry=100.0,
        stop=90.0,
        exit=120.0,
        opened="2026-08-01T12:00:00Z",
    )
    base.update(over)
    return base


def fake_llm(response):
    """An Ollama whose transport returns a canned body."""
    payload = response if isinstance(response, str) else json.dumps(response)
    return Ollama(post=lambda url, body: {"response": payload})


# ---------------------------------------------------------------------------
# Scheduling policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour", [1, 3, 7])
def test_in_window_covers_the_asked_for_night(hour):
    assert in_window(hour, 1, 8)


@pytest.mark.parametrize("hour", [0, 8, 9, 23])
def test_outside_the_window_is_outside(hour):
    assert not in_window(hour, 1, 8)


@pytest.mark.parametrize("hour", [23, 0, 3, 5])
def test_window_can_wrap_midnight(hour):
    assert in_window(hour, 23, 6)


@pytest.mark.parametrize("hour", [6, 12, 22])
def test_wrapping_window_still_closes(hour):
    assert not in_window(hour, 23, 6)


def test_idle_machine_inside_the_window_runs():
    assert next_action(3, 600.0, start_hour=1, end_hour=8) == RUN


def test_a_person_at_the_keyboard_yields():
    # Touched the mouse 5 seconds ago: the lab is not taking the machine.
    assert next_action(3, 5.0, start_hour=1, end_hour=8) == YIELD


def test_window_close_beats_idleness():
    # 9am with nobody there is still morning. Whatever is left waits.
    assert next_action(9, 99999.0, start_hour=1, end_hour=8) == STOP


def test_unknowable_idle_is_treated_as_idle():
    # Off Windows the API returns None. Refusing to ever run would be worse
    # than running, so None means go.
    assert next_action(3, None, start_hour=1, end_hour=8) == RUN


def test_idle_threshold_is_the_boundary():
    assert next_action(3, 119.0, idle_threshold=120) == YIELD
    assert next_action(3, 120.0, idle_threshold=120) == RUN


# ---------------------------------------------------------------------------
# Parsing what a local model actually returns
# ---------------------------------------------------------------------------


def test_plain_json_parses():
    assert parse_json_block('{"a": 1}') == {"a": 1}


def test_fenced_json_parses():
    assert parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_buried_in_prose_parses():
    text = 'Sure! Here is the analysis:\n{"a": 1}\nHope that helps.'
    assert parse_json_block(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "{unclosed", None])
def test_unparseable_output_is_dropped_not_raised(text):
    assert parse_json_block(text) is None


# ---------------------------------------------------------------------------
# redteam: an attack you cannot check is not an attack
# ---------------------------------------------------------------------------


def test_attack_with_a_falsifier_is_kept():
    got = validate_attack(
        {
            "claim": "the breakout is unconfirmed",
            "severity": "high",
            "falsifier": {
                "symbol": "nvda",
                "op": ">=",
                "level": 182.0,
                "by": "2026-08-29",
            },
        }
    )
    assert got["severity"] == "high"
    assert got["falsifier"]["symbol"] == "NVDA"  # normalized


@pytest.mark.parametrize(
    "bad",
    [
        {"claim": "it could go down"},  # no falsifier at all
        {
            "claim": "x",
            "falsifier": {"symbol": "NVDA", "op": "~", "level": 1, "by": "x"},
        },
        {"claim": "x", "falsifier": {"symbol": "", "op": ">=", "level": 1, "by": "x"}},
        {"claim": "x", "falsifier": {"symbol": "N", "op": ">=", "level": 0, "by": "x"}},
        {
            "claim": "x",
            "falsifier": {"symbol": "N", "op": ">=", "level": "abc", "by": "x"},
        },
        {"claim": "", "falsifier": {"symbol": "N", "op": ">=", "level": 1, "by": "x"}},
        {
            "claim": "x",
            "falsifier": {"symbol": "N", "op": ">=", "level": 1},
        },  # no deadline
        "not a dict",
    ],
)
def test_uncheckable_attacks_are_dropped(bad):
    assert validate_attack(bad) is None


def test_unknown_severity_falls_back_to_medium():
    got = validate_attack(
        {
            "claim": "x",
            "severity": "catastrophic",
            "falsifier": {"symbol": "N", "op": "<", "level": 5, "by": "friday"},
        }
    )
    assert got["severity"] == "medium"


def test_rephrasings_of_one_condition_collapse():
    def attack(claim, level):
        return {
            "claim": claim,
            "severity": "high",
            "falsifier": {"symbol": "NVDA", "op": ">=", "level": level, "by": "friday"},
        }

    got = dedupe_attacks(
        [
            attack("breakout fails", 182.0),
            attack("no follow-through", 182.0),
            attack("gap", 190.0),
        ]
    )
    assert len(got) == 2


def test_redteam_reports_how_much_it_threw_away():
    llm = fake_llm(
        {
            "attacks": [
                {
                    "claim": "real",
                    "severity": "high",
                    "falsifier": {
                        "symbol": "NVDA",
                        "op": ">=",
                        "level": 182,
                        "by": "fri",
                    },
                },
                {"claim": "vibes only"},
            ]
        }
    )
    got = run_redteam(make_job("redteam", {"symbol": "NVDA"}, "r1"), llm)
    assert got["proposed"] == 2
    assert got["kept"] == 1


def test_redteam_survives_a_model_that_returns_garbage():
    got = run_redteam(make_job("redteam", {"symbol": "NVDA"}, "r1"), fake_llm("sorry!"))
    assert got["kept"] == 0


# ---------------------------------------------------------------------------
# shock: the model picks the scenario, the arithmetic is ours
# ---------------------------------------------------------------------------


def test_shock_spec_is_clamped_to_sane_bounds():
    spec = validate_shock(
        {"name": "apocalypse", "gap_pct": 5.0, "loss_mult": 99, "slippage_bps": -3}
    )
    assert spec["gap_pct"] == 0.30
    assert spec["loss_mult"] == 5.0
    assert spec["slippage_bps"] == 0.0


def test_shock_spec_without_a_name_is_dropped():
    assert validate_shock({"gap_pct": 0.1}) is None
    assert validate_shock("nonsense") is None


def test_resample_count_is_bounded():
    assert validate_shock({"name": "x", "resample": 10**9})["resample"] == 20000


def test_r_multiple_is_result_over_risk():
    # Risked 10 to make 20.
    assert trade_r(closed(entry=100, stop=90, exit=120)) == pytest.approx(2.0)


def test_short_trades_score_the_other_way():
    assert trade_r(
        closed(direction="short", entry=100, stop=110, exit=80)
    ) == pytest.approx(2.0)


def test_a_trade_with_no_risk_defined_is_skipped():
    assert trade_r(closed(entry=100, stop=100)) is None
    assert trade_r({"entry": "x"}) is None


def test_an_untouched_scenario_changes_nothing():
    spec = validate_shock({"name": "calm"})
    t = closed(entry=100, stop=90, exit=120)
    assert shock_trade_r(t, spec) == pytest.approx(trade_r(t))


def test_a_gap_only_punishes_the_losers():
    spec = validate_shock({"name": "gap", "gap_pct": 0.05})
    winner = closed(entry=100, stop=90, exit=120)
    loser = closed(entry=100, stop=90, exit=90)
    assert shock_trade_r(winner, spec) == pytest.approx(trade_r(winner))
    # Stopped at 90, but filled 5% below it: another 4.5 points on 10 of risk.
    assert shock_trade_r(loser, spec) == pytest.approx(-1.45)


def test_losses_scale_but_wins_do_not_flatter_by_default():
    spec = validate_shock({"name": "vol", "loss_mult": 2.0})
    assert shock_trade_r(closed(exit=90), spec) == pytest.approx(-2.0)
    assert shock_trade_r(closed(exit=120), spec) == pytest.approx(2.0)


def test_slippage_costs_both_sides():
    spec = validate_shock({"name": "slip", "slippage_bps": 100})
    # 100bps of a 100 entry is 1.0, against 10 of risk: 0.1R.
    assert shock_trade_r(closed(exit=120), spec) == pytest.approx(1.9)


def test_max_drawdown_finds_the_worst_peak_to_trough():
    assert max_drawdown([1.0, 1.0, -3.0, 1.0]) == pytest.approx(3.0)
    assert max_drawdown([1.0, 2.0]) == pytest.approx(0.0)


def test_a_survivable_scenario_says_so():
    trades = [closed(exit=120) for _ in range(5)]
    got = apply_shock(trades, validate_shock({"name": "mild"}), ruin_r=10.0)
    assert got["survives"]
    assert got["n_trades"] == 5


def test_a_scenario_that_empties_the_pot_is_flagged():
    trades = [closed(exit=90) for _ in range(12)]
    spec = validate_shock({"name": "wipeout", "gap_pct": 0.10, "loss_mult": 2.0})
    got = apply_shock(trades, spec, ruin_r=10.0)
    assert got["survives"] is False
    assert got["max_dd_r_after"] > 10.0


def test_shock_reports_rather_than_crashes_on_an_empty_record():
    got = apply_shock([], validate_shock({"name": "x"}))
    assert "skipped" in got


def test_correlation_to_one_stacks_the_losses():
    trades = [closed(exit=120), closed(exit=90), closed(exit=120), closed(exit=90)]
    spec = validate_shock({"name": "corr", "corr_to_one": True})
    netted = apply_shock(trades, validate_shock({"name": "n"}))
    stacked = apply_shock(trades, spec)
    assert stacked["max_dd_r_after"] > netted["max_dd_r_after"]


def test_resampling_is_deterministic_for_a_given_seed():
    rs = [2.0, -1.0, -1.0, 3.0, -1.0]
    assert resample_paths(rs, 500, 10.0) == resample_paths(rs, 500, 10.0)


def test_resampling_finds_ruin_a_fixed_record_never_showed():
    # Nine losers and a big winner: in this order the drawdown is survivable
    # only because of where the winner happens to fall.
    rs = [-1.0] * 9 + [9.0]
    got = resample_paths(rs, 3000, 8.0)
    assert got["p_ruin"] > 0.0
    assert got["dd_p95_r"] >= got["dd_median_r"]


# ---------------------------------------------------------------------------
# fragility: a lone peak is a fitted parameter
# ---------------------------------------------------------------------------


def test_a_plateau_reads_as_robust():
    got = cliff_score({10: 1.00, 20: 1.05, 30: 0.98}, 20)
    assert got["cliff"] < 0.33
    assert "robust" in got["verdict"]


def test_a_lone_spike_reads_as_fitted():
    got = cliff_score({10: 0.2, 20: 2.0, 30: 0.1}, 20)
    assert got["is_peak"]
    assert "fitted" in got["verdict"]


def test_the_edge_of_a_sweep_asks_for_a_wider_grid():
    got = cliff_score({10: 1.0}, 10)
    assert got["cliff"] is None
    assert "widen" in got["verdict"]


def test_cliff_score_reports_where_the_choice_ranks():
    got = cliff_score({10: 0.5, 20: 2.0, 30: 1.0}, 30)
    assert (got["rank"], got["of"]) == (2, 3)


def test_a_value_outside_its_own_sweep_is_an_error():
    assert "error" in cliff_score({10: 1.0}, 99)
    assert "error" in cliff_score({}, 1)


# ---------------------------------------------------------------------------
# leaks: the model proposes, the record decides
# ---------------------------------------------------------------------------


def test_a_pattern_with_too_few_trades_is_dropped():
    trades = [closed(lane="short-dte", exit=90) for _ in range(3)]
    got = verify_pattern(
        trades, {"filter": {"lane": "short-dte"}, "direction": "worse"}
    )
    assert got is None


def test_a_real_leak_is_confirmed_with_its_effect_size():
    trades = [closed(lane="short-dte", exit=90) for _ in range(6)]
    trades += [closed(lane="swing-buy", exit=120) for _ in range(6)]
    got = verify_pattern(
        trades,
        {
            "pattern": "short-dte bleeds",
            "filter": {"lane": "short-dte"},
            "direction": "worse",
        },
    )
    assert got["holds"]
    assert got["n"] == 6
    assert got["delta_r"] < 0


def test_a_pattern_the_record_contradicts_is_reported_as_not_holding():
    # Claimed to be the weak lane; it is in fact the strong one.
    trades = [closed(lane="short-dte", exit=120) for _ in range(6)]
    trades += [closed(lane="swing-buy", exit=90) for _ in range(6)]
    got = verify_pattern(
        trades, {"filter": {"lane": "short-dte"}, "direction": "worse"}
    )
    assert got["holds"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"filter": {}},
        {"direction": "worse"},
        {"filter": {"lane": "x"}, "direction": "sideways"},
        "x",
    ],
)
def test_malformed_patterns_are_dropped(bad):
    assert verify_pattern([closed()] * 10, bad) is None


def test_leaks_job_only_reports_what_the_record_supports():
    trades = [closed(lane="short-dte", exit=90) for _ in range(6)]
    trades += [closed(lane="swing-buy", exit=120) for _ in range(6)]
    llm = fake_llm(
        {
            "patterns": [
                {
                    "pattern": "short-dte bleeds",
                    "filter": {"lane": "short-dte"},
                    "direction": "worse",
                },
                {
                    "pattern": "unsupported",
                    "filter": {"lane": "never-traded"},
                    "direction": "worse",
                },
            ]
        }
    )
    got = run_leaks(make_job("leaks", {"min_n": 5}, "l1"), llm, trades)
    assert got["proposed"] == 2
    assert got["verified"] == 1


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self, hour):
        self.hour = hour


def queue_of(n, kind="fragility"):
    return [
        make_job(
            kind, {"param": "p", "sweep": {"1": 1.0, "2": 1.0}, "chosen": 1}, f"j{i}"
        )
        for i in range(n)
    ]


def test_the_runner_drains_the_queue_when_the_machine_is_free():
    jobs = queue_of(3)
    stats = run_night(
        jobs, fake_llm("{}"), [], now_fn=lambda: Clock(3), idle_fn=lambda: 999
    )
    assert stats["completed"] == 3
    assert stats["stopped_because"] == "queue empty"
    assert not pending(jobs)


def test_the_runner_stops_at_the_end_of_the_window_and_leaves_the_rest():
    jobs = queue_of(3)
    stats = run_night(
        jobs, fake_llm("{}"), [], now_fn=lambda: Clock(9), idle_fn=lambda: 999
    )
    assert stats["completed"] == 0
    assert stats["remaining"] == 3
    assert stats["stopped_because"] == "window closed"


def test_the_runner_yields_to_someone_at_the_keyboard():
    slept = []
    stats = run_night(
        queue_of(2),
        fake_llm("{}"),
        [],
        now_fn=lambda: Clock(3),
        idle_fn=lambda: 1.0,
        sleep_fn=slept.append,
        max_yields=3,
    )
    assert stats["completed"] == 0
    assert stats["stopped_because"] == "yield limit reached"
    assert slept, "yielding should wait rather than spin"


def test_work_done_before_an_interruption_is_kept():
    # Idle for the first poll, then someone sits down.
    jobs = queue_of(4)
    states = iter([999, 1.0, 1.0, 1.0, 1.0])
    run_night(
        jobs,
        fake_llm("{}"),
        [],
        now_fn=lambda: Clock(3),
        idle_fn=lambda: next(states),
        sleep_fn=lambda s: None,
        max_yields=2,
    )
    assert len([j for j in jobs if j["status"] == "done"]) == 1
    assert len(pending(jobs)) == 3


def test_every_finished_job_is_checkpointed(tmp_path):
    path = tmp_path / "queue.jsonl"
    jobs = queue_of(3)
    seen = []
    run_night(
        jobs,
        fake_llm("{}"),
        [],
        now_fn=lambda: Clock(3),
        idle_fn=lambda: 999,
        on_checkpoint=lambda js: (write_queue(path, js), seen.append(1)),
    )
    assert len(seen) == 3
    assert all(j["status"] == "done" for j in read_queue(path))


def test_one_broken_job_does_not_cost_the_night():
    jobs = (
        queue_of(1)
        + [make_job("fragility", {"sweep": "not-a-dict"}, "bad")]
        + queue_of(1)
    )
    jobs[2]["id"] = "j-last"
    stats = run_night(
        jobs, fake_llm("{}"), [], now_fn=lambda: Clock(3), idle_fn=lambda: 999
    )
    assert stats["completed"] == 3
    assert jobs[1]["status"] == "failed"
    assert jobs[2]["status"] == "done"


def test_once_runs_exactly_one_job():
    jobs = queue_of(5)
    stats = run_night(
        jobs, fake_llm("{}"), [], now_fn=lambda: Clock(3), idle_fn=lambda: 999, limit=1
    )
    assert stats["completed"] == 1
    assert stats["remaining"] == 4


# ---------------------------------------------------------------------------
# The morning
# ---------------------------------------------------------------------------


def test_a_quiet_night_produces_no_lines():
    verdict = build_verdict(
        [{"kind": "shock", "id": "s1", "result": {"scenario": "x", "survives": True}}]
    )
    assert verdict["broke"] is False
    assert verdict["lines"] == []


def test_a_scenario_that_empties_the_pot_reaches_the_morning():
    verdict = build_verdict(
        [
            {
                "kind": "shock",
                "id": "s1",
                "result": {
                    "scenario": "gap-and-slip",
                    "survives": False,
                    "max_dd_r_after": 14.0,
                    "ruin_r": 10.0,
                },
            }
        ]
    )
    assert verdict["broke"]
    assert "gap-and-slip" in verdict["lines"][0]


def test_a_high_ruin_probability_reaches_the_morning_even_if_it_survived():
    verdict = build_verdict(
        [
            {
                "kind": "shock",
                "id": "s",
                "result": {"scenario": "x", "survives": True, "p_ruin": 0.12},
            }
        ]
    )
    assert verdict["broke"]


def test_only_high_severity_attacks_wake_you_up():
    def result(severity):
        return {
            "kind": "redteam",
            "id": "r",
            "result": {
                "subject": "NVDA 190C",
                "attacks": [
                    {
                        "claim": "c",
                        "severity": severity,
                        "falsifier": {
                            "symbol": "NVDA",
                            "op": ">=",
                            "level": 182,
                            "by": "fri",
                        },
                    }
                ],
            },
        }

    assert build_verdict([result("medium")])["broke"] is False
    high = build_verdict([result("high")])
    assert high["broke"]
    assert any("NVDA >= 182" in line for line in high["lines"])


def test_a_fitted_parameter_reaches_the_morning():
    verdict = build_verdict(
        [
            {
                "kind": "fragility",
                "id": "f",
                "result": {
                    "param": "lookback",
                    "chosen": 20,
                    "verdict": "fitted — lone peak",
                },
            }
        ]
    )
    assert verdict["broke"]


def test_a_confirmed_leak_reaches_the_morning():
    verdict = build_verdict(
        [
            {
                "kind": "leaks",
                "id": "l",
                "result": {
                    "patterns": [
                        {"pattern": "p", "n": 7, "delta_r": -0.8, "holds": True}
                    ]
                },
            }
        ]
    )
    assert verdict["broke"]


def test_findings_become_proposals_that_are_only_ever_pending():
    results = [
        {
            "kind": "leaks",
            "id": "l",
            "result": {
                "patterns": [
                    {
                        "pattern": "p",
                        "filter": {"lane": "x"},
                        "n": 12,
                        "delta_r": -0.9,
                        "holds": True,
                    }
                ]
            },
        },
        {
            "kind": "shock",
            "id": "s",
            "result": {
                "scenario": "x",
                "survives": False,
                "max_dd_r_after": 14.0,
                "ruin_r": 10.0,
            },
        },
    ]
    proposals = proposals_from(results)
    assert len(proposals) == 2
    assert all(p["status"] == "pending" for p in proposals)


def test_a_thin_leak_does_not_become_a_rule_proposal():
    results = [
        {
            "kind": "leaks",
            "id": "l",
            "result": {
                "patterns": [
                    {
                        "pattern": "p",
                        "filter": {},
                        "n": 6,
                        "delta_r": -0.9,
                        "holds": True,
                    }
                ]
            },
        }
    ]
    assert proposals_from(results) == []


# ---------------------------------------------------------------------------
# Planning tonight's queue
# ---------------------------------------------------------------------------


def test_the_queue_attacks_what_is_open_and_shocks_what_is_closed():
    ledger = {
        "trades": [
            dict(closed(), status="open", id="T1", thesis="breakout"),
            closed(id="T2"),
            closed(id="T3"),
        ]
    }
    jobs = build_queue(ledger, shocks=3)
    kinds = [j["kind"] for j in jobs]
    assert kinds.count("redteam") == 1
    assert kinds.count("shock") == 3
    assert kinds.count("leaks") == 1


def test_a_desk_with_no_closed_record_queues_no_scenarios():
    ledger = {"trades": [dict(closed(), status="open", id="T1")]}
    jobs = build_queue(ledger)
    assert [j["kind"] for j in jobs] == ["redteam"]


def test_an_empty_desk_queues_nothing():
    assert build_queue({"trades": []}) == []


def test_an_unknown_job_kind_is_refused_at_the_door():
    with pytest.raises(ValueError):
        make_job("telepathy", {}, "j1")


# ---------------------------------------------------------------------------
# Guards added after the first end-to-end run put a wrong ticker on screen
# ---------------------------------------------------------------------------


def attack_on(symbol, severity="high"):
    return {
        "claim": "c",
        "severity": severity,
        "falsifier": {"symbol": symbol, "op": ">=", "level": 182.0, "by": "2026-08-29"},
    }


def test_an_attack_naming_a_different_ticker_is_dropped():
    # The failure this exists to stop: a TSLA thesis attacked with an NVDA
    # level, which would put a check on a stock you do not hold in front of
    # you at breakfast.
    assert validate_attack(attack_on("NVDA"), expect_symbol="TSLA") is None


def test_an_attack_on_its_own_ticker_survives():
    assert validate_attack(attack_on("TSLA"), expect_symbol="TSLA") is not None


def test_macro_levels_are_legitimate_attacks_on_a_single_name():
    # "This only works while SPY holds up" is real reasoning, not drift.
    assert validate_attack(attack_on("SPY"), expect_symbol="TSLA") is not None


def test_without_an_expected_symbol_any_ticker_is_allowed():
    assert validate_attack(attack_on("ANYTHING")) is not None


def test_the_redteam_job_enforces_the_trade_s_own_symbol():
    llm = fake_llm({"attacks": [attack_on("NVDA"), attack_on("TSLA")]})
    got = run_redteam(make_job("redteam", {"symbol": "TSLA"}, "r1"), llm)
    assert got["proposed"] == 2
    assert got["kept"] == 1
    assert got["attacks"][0]["falsifier"]["symbol"] == "TSLA"


def shock_result(name, dd, survives=False):
    return {
        "kind": "shock",
        "id": name,
        "result": {
            "scenario": name,
            "survives": survives,
            "max_dd_r_after": dd,
            "ruin_r": 10.0,
        },
    }


def test_many_broken_scenarios_collapse_to_one_line_naming_the_worst():
    verdict = build_verdict(
        [shock_result("a", 12.0), shock_result("b", 48.6), shock_result("c", 43.7)]
    )
    assert verdict["broke"]
    assert len(verdict["lines"]) == 1
    assert "b" in verdict["lines"][0] and "48.6" in verdict["lines"][0]
    assert "+2 more" in verdict["lines"][0]


def test_a_single_broken_scenario_does_not_claim_there_are_others():
    verdict = build_verdict([shock_result("solo", 12.0)])
    assert "more" not in verdict["lines"][0]


def test_collapsing_scenarios_does_not_bury_other_findings():
    results = [
        shock_result("a", 12.0),
        shock_result("b", 48.6),
        {
            "kind": "leaks",
            "id": "l",
            "result": {
                "patterns": [
                    {
                        "pattern": "short-dte bleeds",
                        "n": 8,
                        "delta_r": -0.45,
                        "holds": True,
                    }
                ]
            },
        },
    ]
    lines = build_verdict(results)["lines"]
    assert len(lines) == 2
    assert any("LEAK" in line for line in lines)


def test_only_the_riskiest_surviving_scenario_is_reported():
    results = [
        {
            "kind": "shock",
            "id": "a",
            "result": {"scenario": "a", "survives": True, "p_ruin": 0.08},
        },
        {
            "kind": "shock",
            "id": "b",
            "result": {"scenario": "b", "survives": True, "p_ruin": 0.58},
        },
    ]
    lines = build_verdict(results)["lines"]
    assert len(lines) == 1
    assert "58%" in lines[0]


def test_a_night_of_broken_scenarios_stages_one_sizing_proposal():
    proposals = proposals_from(
        [shock_result("a", 12.0), shock_result("b", 48.6), shock_result("c", 43.7)]
    )
    assert len(proposals) == 1
    assert "b" in proposals[0]["proposal"]
    assert "3 scenarios broke" in proposals[0]["proposal"]


# ---------------------------------------------------------------------------
# The sim bridge: backtests feed the record, so a strategy is stressed
# before it ever risks paper money
# ---------------------------------------------------------------------------

from tools.night_lab import RECORD_NAME, cmd_plan, load_sim_trades


def sim_record(n=6, lane="sim-15m"):
    return {
        "trades": [
            dict(
                closed(),
                id=f"sim-{i}",
                lane=lane,
                exit=118.0 if i % 2 else 89.0,
            )
            for i in range(n)
        ]
    }


def test_sim_trades_load_from_the_exported_shape(tmp_path):
    path = tmp_path / "sim.json"
    path.write_text(json.dumps(sim_record(4)))
    assert len(load_sim_trades(path)) == 4


def test_a_bare_list_loads_too(tmp_path):
    path = tmp_path / "sim.json"
    path.write_text(json.dumps(sim_record(3)["trades"]))
    assert len(load_sim_trades(path)) == 3


def test_malformed_sim_trades_are_dropped_at_the_door(tmp_path):
    path = tmp_path / "sim.json"
    path.write_text(
        json.dumps(
            {
                "trades": [
                    dict(closed(), id="good"),
                    dict(closed(), id="open-one", status="open"),
                    dict(closed(), id="no-risk", stop=100.0),  # entry == stop
                    "not a dict",
                ]
            }
        )
    )
    kept = load_sim_trades(path)
    assert [t["id"] for t in kept] == ["good"]


def test_unreadable_or_garbage_files_load_as_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_sim_trades(bad) == []
    assert load_sim_trades(tmp_path / "missing.json") == []


class PlanArgs:
    def __init__(self, **over):
        self.dir = over.get("dir")
        self.ledger = over.get("ledger")
        self.sim = over.get("sim")
        self.shocks = over.get("shocks", 3)
        self.attacks = over.get("attacks", 4)


def test_plan_with_only_sim_trades_queues_shocks_and_leaks(tmp_path):
    sim = tmp_path / "sim.json"
    sim.write_text(json.dumps(sim_record(8)))
    empty_desk = tmp_path / "desk.json"
    empty_desk.write_text(
        json.dumps({"pot": 1000, "trades": [], "reviews": [], "refills": []})
    )
    rc = cmd_plan(
        PlanArgs(dir=str(tmp_path / "lab"), ledger=str(empty_desk), sim=[str(sim)])
    )
    assert rc == 0
    kinds = [j["kind"] for j in read_queue(tmp_path / "lab" / "queue.jsonl")]
    assert kinds.count("shock") == 3
    assert kinds.count("leaks") == 1
    assert kinds.count("redteam") == 0  # sim trades carry no thesis to attack


def test_plan_snapshots_the_merged_record_for_the_night(tmp_path):
    sim = tmp_path / "sim.json"
    sim.write_text(json.dumps(sim_record(5)))
    desk = tmp_path / "desk.json"
    desk.write_text(
        json.dumps(
            {"pot": 1000, "trades": [closed(id="D1")], "reviews": [], "refills": []}
        )
    )
    cmd_plan(PlanArgs(dir=str(tmp_path / "lab"), ledger=str(desk), sim=[str(sim)]))
    snapshot = load_sim_trades(tmp_path / "lab" / RECORD_NAME)
    ids = {t["id"] for t in snapshot}
    assert "D1" in ids
    assert len(snapshot) == 6  # 1 desk + 5 sim


def test_an_empty_desk_and_no_sim_still_queues_nothing(tmp_path):
    empty_desk = tmp_path / "desk.json"
    empty_desk.write_text(
        json.dumps({"pot": 1000, "trades": [], "reviews": [], "refills": []})
    )
    rc = cmd_plan(PlanArgs(dir=str(tmp_path / "lab"), ledger=str(empty_desk)))
    assert rc == 1


# ---------------------------------------------------------------------------
# The bare-`plan` convention: the "good night" agent must not silently drop
# the sim record the owner armed earlier
# ---------------------------------------------------------------------------


def test_a_bare_plan_picks_up_the_sim_file_by_its_conventional_name(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "sim_trades.json").write_text(json.dumps(sim_record(6)))
    empty_desk = tmp_path / "desk.json"
    empty_desk.write_text(
        json.dumps({"pot": 1000, "trades": [], "reviews": [], "refills": []})
    )
    rc = cmd_plan(PlanArgs(dir=str(lab), ledger=str(empty_desk)))  # no --sim
    assert rc == 0
    kinds = [j["kind"] for j in read_queue(lab / "queue.jsonl")]
    assert kinds.count("shock") == 3
    assert kinds.count("leaks") == 1


def test_an_explicit_sim_overrides_the_conventional_file(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "sim_trades.json").write_text(json.dumps(sim_record(6, lane="sim-old")))
    other = tmp_path / "fresh.json"
    other.write_text(json.dumps(sim_record(4, lane="sim-fresh")))
    empty_desk = tmp_path / "desk.json"
    empty_desk.write_text(
        json.dumps({"pot": 1000, "trades": [], "reviews": [], "refills": []})
    )
    cmd_plan(PlanArgs(dir=str(lab), ledger=str(empty_desk), sim=[str(other)]))
    from tools.night_lab import RECORD_NAME as _RN

    lanes = {t["lane"] for t in load_sim_trades(lab / _RN)}
    assert lanes == {"sim-fresh"}

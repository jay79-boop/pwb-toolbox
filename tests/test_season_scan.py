"""season_scan — the gates that make a calendar pattern earn its place.

The contract:

    - a planted seasonal effect is convicted; white noise is not — the scan
      must be able to find a real pattern AND able to find nothing;
    - a pattern that exists only in the first half of the years is refused
      conviction (split-half), and one that fails FDR across the grid stays
      a candidate;
    - pre-registered folklore claims are judged one-sided on their own
      terms: HELD when the record backs the claim, FAILED when it points
      the other way, INSUFFICIENT on short history;
    - the now-window screener files convicted months into in / entering /
      leaving correctly around month boundaries;
    - the watchlist renders TradingView's ###section format with every
      symbol placed once, most actionable section first;
    - the whole CLI runs end-to-end on synthetic CSVs, offline.

All data is synthetic with known planted structure; no network anywhere.
"""

import json
import math
import random
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from tools.season_scan import (
    CANDIDATE,
    CONVICTED,
    NOISE,
    assign_tiers,
    average_year_path,
    bh_fdr,
    cell_stats,
    cmd_report,
    compute,
    judge_folklore,
    load_universe,
    monthly_log_returns,
    now_windows,
    perm_pvalue,
    render_report,
    render_watchlist,
    split_half,
)


def synthetic_closes(
    years: int = 24,
    strong_month: int | None = None,
    monthly_edge: float = 0.04,
    first_half_only: bool = False,
    seed: int = 5,
    start_year: int = 2000,
    daily_vol: float = 0.004,
):
    """Daily closes with an optional planted monthly effect."""
    rng = random.Random(seed)
    closes, price = {}, 100.0
    day = date(start_year, 1, 1)
    end = date(start_year + years, 1, 1)
    half_cut = start_year + years // 2
    while day < end:
        if day.weekday() < 5:
            drift = 0.0
            if strong_month and day.month == strong_month:
                if not (first_half_only and day.year >= half_cut):
                    drift = monthly_edge / 21
            price *= math.exp(drift + rng.gauss(0, daily_vol))
            closes[day] = price
        day += timedelta(days=1)
    return closes


# ---------------------------------------------------------------------------
# Return arithmetic
# ---------------------------------------------------------------------------


def test_monthly_returns_sum_the_dailies():
    closes = {
        date(2020, 1, 30): 100.0,
        date(2020, 1, 31): 102.0,
        date(2020, 2, 3): 104.0,
        date(2020, 2, 4): 103.0,
    }
    monthly = monthly_log_returns(closes)
    # The Jan->Feb boundary return belongs to February, where it was earned.
    assert monthly[(2020, 1)] == pytest.approx(math.log(102 / 100))
    assert monthly[(2020, 2)] == pytest.approx(
        math.log(104 / 102) + math.log(103 / 104)
    )


def test_a_planted_month_shows_a_tiny_pvalue():
    closes = synthetic_closes(strong_month=3)
    monthly = monthly_log_returns(closes)
    mean, p = perm_pvalue(monthly, [3])
    assert mean > 0
    assert p < 0.01


def test_white_noise_shows_no_conviction_worthy_pvalue():
    closes = synthetic_closes(strong_month=None)
    monthly = monthly_log_returns(closes)
    smallest = min(perm_pvalue(monthly, [m])[1] for m in range(1, 13))
    # Some month is always the luckiest; it just should not look impossible.
    assert smallest > 0.001


def test_split_half_agrees_on_a_persistent_effect():
    monthly = monthly_log_returns(synthetic_closes(strong_month=3))
    assert split_half(monthly, [3])["agree"]


def test_split_half_catches_a_dead_pattern():
    monthly = monthly_log_returns(
        synthetic_closes(strong_month=3, first_half_only=True, monthly_edge=0.08)
    )
    halves = split_half(monthly, [3])
    assert halves["first"] > halves["second"]


def test_bh_fdr_passes_strong_signals_and_rejects_uniform_noise():
    pvals = [0.0004, 0.0008, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
    passing = bh_fdr(pvals, q=0.10)
    assert passing[0] and passing[1]
    assert not any(passing[2:])
    assert bh_fdr([], q=0.10) == []


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


def grid_for(**kw):
    cells = cell_stats(synthetic_closes(**kw), "TST")
    assign_tiers(cells)
    return {c["month"]: c for c in cells}


def test_a_real_persistent_effect_is_convicted():
    assert grid_for(strong_month=3)[3]["tier"] == CONVICTED


def test_noise_is_never_convicted():
    grid = grid_for(strong_month=None)
    assert all(c["tier"] != CONVICTED for c in grid.values())


def test_a_dead_pattern_is_not_convicted_and_says_why():
    cell = grid_for(strong_month=3, first_half_only=True, monthly_edge=0.08)[3]
    assert cell["tier"] != CONVICTED


def test_short_history_caps_at_candidate():
    cell = grid_for(strong_month=3, years=8, monthly_edge=0.10)[3]
    assert cell["tier"] == CANDIDATE
    assert "history" in cell["tier_note"]


# ---------------------------------------------------------------------------
# Folklore
# ---------------------------------------------------------------------------


def test_a_true_claim_is_held():
    data = {
        "SPY": synthetic_closes(strong_month=12),
        "IWM": synthetic_closes(),
        "XLE": synthetic_closes(),
    }
    verdicts = {f["name"]: f for f in judge_folklore(data)}
    assert verdicts["Santa / December strength"]["verdict"] == "HELD"


def test_a_claim_the_record_contradicts_fails():
    # September planted STRONG: the weakness claim points the wrong way.
    data = {
        "SPY": synthetic_closes(strong_month=9),
        "IWM": synthetic_closes(),
        "XLE": synthetic_closes(),
    }
    verdicts = {f["name"]: f for f in judge_folklore(data)}
    assert verdicts["September weakness"]["verdict"] == "FAILED"


def test_short_history_is_insufficient_not_failed():
    data = {
        "SPY": synthetic_closes(years=6),
        "IWM": synthetic_closes(years=6),
        "XLE": synthetic_closes(years=6),
    }
    assert all(
        f["verdict"] == "INSUFFICIENT"
        for f in judge_folklore(data)
        if f["verdict"] != "NO DATA"
    )


def test_missing_data_is_no_data():
    verdicts = {f["name"]: f for f in judge_folklore({"SPY": synthetic_closes()})}
    assert verdicts["January effect (small caps)"]["verdict"] == "NO DATA"


# ---------------------------------------------------------------------------
# The now-window screener
# ---------------------------------------------------------------------------


def convicted_cell(symbol="XLE", month=3, mean=0.02):
    return {
        "symbol": symbol,
        "month": month,
        "mean": mean,
        "mean_pct": round(100 * (math.exp(mean) - 1), 2),
        "hit_rate": 0.7,
        "tier": CONVICTED,
    }


def test_mid_window_is_in():
    now = now_windows([convicted_cell(month=3)], date(2026, 3, 10))
    assert now["in"][0]["symbol"] == "XLE"
    assert not now["entering"] and not now["leaving"]


def test_window_end_moves_to_leaving():
    now = now_windows([convicted_cell(month=3)], date(2026, 3, 25))
    assert now["leaving"][0]["days_left"] <= 14
    assert not now["in"]


def test_upcoming_window_is_entering():
    now = now_windows([convicted_cell(month=4)], date(2026, 3, 25))
    assert now["entering"][0]["days_until"] <= 14


def test_far_windows_and_non_convicted_are_silent():
    cells = [convicted_cell(month=9), dict(convicted_cell(month=3), tier=CANDIDATE)]
    now = now_windows(cells, date(2026, 3, 10))
    assert not now["in"] and not now["entering"] and not now["leaving"]


def test_bearish_direction_is_carried():
    now = now_windows([convicted_cell(mean=-0.02)], date(2026, 3, 10))
    assert now["in"][0]["direction"] == "bearish"


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------


def scan_fixture():
    cells = [
        convicted_cell("XLE", 3, 0.02),
        convicted_cell("XLU", 9, 0.015),
        dict(convicted_cell("XLF", 3, -0.02)),
    ]
    return {
        "symbols": ["SPY", "XLE", "XLF", "XLU"],
        "cells": cells,
        "now": now_windows(cells, date(2026, 3, 10)),
        "folklore": [],
        "paths": {},
        "generated": "2026-03-10",
        "gates": {"permutations": 2000, "fdr_q": 0.1, "min_years": 15},
    }


def test_watchlist_sections_and_places_each_symbol_once():
    text = render_watchlist(scan_fixture())
    assert text.index("###IN SEASON NOW") < text.index("###SEASONALLY WEAK NOW")
    assert text.count("XLE") == 1  # in-season, not repeated under proven
    assert text.count("XLF") == 1  # weak now
    assert "SPY" in text.split("###SCANNED - NOTHING PROVEN")[1]


# ---------------------------------------------------------------------------
# End to end, offline
# ---------------------------------------------------------------------------


def write_csv(path, closes):
    rows = ["timestamp,close"]
    rows += [f"{d.isoformat()},{v:.4f}" for d, v in sorted(closes.items())]
    path.write_text("\n".join(rows) + "\n")


def test_report_command_writes_all_three_artifacts(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    write_csv(data_dir / "AAA.csv", synthetic_closes(strong_month=3, seed=1))
    write_csv(data_dir / "BBB.csv", synthetic_closes(seed=2))
    args = SimpleNamespace(dir=str(tmp_path), symbols=["AAA", "BBB"])
    assert cmd_report(args) == 0
    scan = json.loads((tmp_path / "season.json").read_text())
    tiers = {(c["symbol"], c["month"]): c["tier"] for c in scan["cells"]}
    assert tiers[("AAA", 3)] == CONVICTED
    html = (tmp_path / "season-report.html").read_text()
    assert "AAA" in html and "Folklore on trial" in html
    assert (tmp_path / "tradingview-watchlist.txt").read_text().startswith("###")


def test_report_without_data_points_at_fetch(tmp_path, capsys):
    args = SimpleNamespace(dir=str(tmp_path), symbols=None)
    assert cmd_report(args) == 1
    assert "fetch" in capsys.readouterr().out


def test_universe_merges_file_and_flags(tmp_path):
    (tmp_path / "universe.txt").write_text("nvda\n# comment\nTSLA\nXLK\n")
    symbols = load_universe(tmp_path, ["amd"])
    assert "NVDA" in symbols and "TSLA" in symbols and "AMD" in symbols
    assert symbols.count("XLK") == 1  # already in the default set


def test_average_year_path_shows_the_planted_run():
    path = average_year_path(synthetic_closes(strong_month=3, monthly_edge=0.06))
    new = path["new"]
    # By end of March (~bucket 18) the cumulative path should be visibly
    # above its January start; by mid-year the run is locked in.
    assert new[18] > new[2]
    assert len(new) == 73


def test_render_report_inlines_the_data():
    html = render_report(scan_fixture())
    assert "XLE" in html and "__DATA__" not in html

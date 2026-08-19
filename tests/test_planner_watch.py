"""Checks on the alert watcher.

The thing an alerting tool has to get right is silence: a message that fires
when nothing happened trains you to ignore the next one, and a message computed
from a stale price is worse than none at all. Most of what is pinned here is
therefore about when the watcher says nothing.
"""

import json

import pytest

from tools.planner_watch import (
    Unreadable,
    _number,
    csv_url,
    check,
    load_state,
    parse,
    read_csv,
    save_state,
)

WATCH_CSV = """Watch
What the alert watcher reads.
Plan,Holding,Ticker,Feed,Units held,Avg cost,Price,Weight,Next rung,Target price,Away,Units to sell,Net cash
Plan 1,XRP,CURRENCY:XRPUSD,live,"5,000",$0.50,$1.19,44.0%,25%,$1.25,4.8%,"1,250",$1478.13
Plan 2,Gala,CURRENCY:GALAUSD,no feed — using your price,"13,545",$0.04,$0.04,1.4%,5%,$0.05,20.0%,"1,354",$61.55
Plan 3,,,,,,,,,,,,
,,,,,,,,,,,,
Holdings
Holding,Ticker,Class,Status,Feed,Units,Avg cost,Price,Market value,Weight,Unrealised %
XRP,CURRENCY:XRPUSD,Crypto,Active,live,"5,000",$0.50,$1.19,"$5,950.00",44.0%,138.0%
Gala,CURRENCY:GALAUSD,Crypto,Active,no feed — using your price,"13,545",$0.04,$0.04,$541.80,4.0%,-9.1%
Cardano,CURRENCY:ADAUSD,Crypto,Active,live,250,$0.48,$0.18,$45.00,0.3%,-62.5%
Old Coin,CURRENCY:XYZUSD,Crypto,Closed,live,100,$1.00,$5.00,$500.00,3.7%,400.0%
EXAMPLE — overwrite or delete this row,CURRENCY:BTCUSD,Crypto,Active,live,0.05,"$50,000.00","$66,470.52","$3,323.53",100.0%,32.9%
"""


def rows():
    import csv
    import io

    return list(csv.reader(io.StringIO(WATCH_CSV)))


def test_reads_numbers_the_way_a_person_wrote_them():
    assert _number("$1,234.50") == 1234.50
    assert _number("44.0%") == 0.44
    assert _number("(44)") == -44
    assert _number("-62.5%") == -0.625
    for empty in ("", "  ", None, "—", "#N/A", "#REF!"):
        assert _number(empty) is None


def test_finds_both_blocks_by_their_headers():
    plans, holdings = parse(rows())
    assert [p.plan for p in plans] == ["Plan 1", "Plan 2"], "an empty plan is not one"
    assert [h.holding for h in holdings][:4] == ["XRP", "Gala", "Cardano", "Old Coin"]
    assert plans[0].away == 0.048
    assert holdings[0].weight == 0.44


def test_a_rung_within_reach_carries_the_decision():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {})
    rung = [a for a in report.alerts if a.startswith("XRP is")]
    assert len(rung) == 1
    # Everything needed to act, without opening anything.
    assert "4.8% from its +25% rung" in rung[0]
    assert "$1.25" in rung[0] and "$1.19" in rung[0]
    assert "sell 1,250" in rung[0]
    assert "$1,478 net" in rung[0], "round cash figures stay legible"
    assert "leaving 3,750" in rung[0]


def test_a_rung_still_far_off_says_nothing():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {}, near=0.02)
    assert not [a for a in report.alerts if "rung" in a]


def test_a_passed_rung_reads_as_passed():
    plans, holdings = parse(rows())
    plans[0].away = -0.03
    report = check(plans, holdings, {})
    assert "past its +25% rung" in report.alerts[0]


def test_a_position_without_a_live_price_is_never_alerted_on():
    """Gala's rung is 20% away and its price has not moved in three years.

    Alerting on it would be a guess dressed as a fact, and one false alarm is
    enough to make the next real one ignorable.
    """
    plans, holdings = parse(rows())
    plans[1].away = 0.0
    report = check(plans, holdings, {"Gala": 0.10})
    assert not [a for a in report.alerts if "Gala" in a]
    assert "Gala" in report.skipped
    assert "no live price" in report.text()
    assert (
        "Gala" not in report.prices
    ), "a price nobody updates is not worth remembering"


def test_a_big_move_needs_a_previous_price_to_compare():
    plans, holdings = parse(rows())
    assert not [a for a in check(plans, holdings, {}).alerts if "moved" in a]
    report = check(plans, holdings, {"XRP": 1.00})
    moved = [a for a in report.alerts if "moved" in a]
    assert len(moved) == 1
    assert "+19.0%" in moved[0]


def test_a_small_move_says_nothing():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {"XRP": 1.15})
    assert not [a for a in report.alerts if "moved" in a]


def test_a_fall_is_worth_saying_too():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {"Cardano": 0.30})
    assert any("Cardano moved -40.0%" in a for a in report.alerts)


def test_an_overweight_holding_is_flagged_against_the_limit():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {}, max_weight=0.20)
    assert any(
        "XRP is 44% of the portfolio, past your 20% limit" in a for a in report.alerts
    )
    assert not [a for a in report.alerts if "Cardano is" in a]


def test_a_closed_position_is_left_alone():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {"Old Coin": 1.00}, max_weight=0.01)
    assert not [a for a in report.alerts if "Old Coin" in a]


def test_silence_reads_as_silence():
    plans, holdings = parse(rows())
    for plan in plans:
        plan.away = 0.9
    report = check(plans, holdings, {}, max_weight=0.99)
    assert not report.alerts
    assert report.text().startswith("Nothing needs a decision.")


def test_state_survives_a_round_trip(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(str(path)) == {}
    save_state(str(path), {"XRP": 1.19})
    assert load_state(str(path)) == {"XRP": 1.19}
    path.write_text("not json at all")
    assert load_state(str(path)) == {}, "a corrupt file must not stop the run"


def test_reads_a_csv_off_disk(tmp_path):
    path = tmp_path / "watch.csv"
    path.write_text("﻿" + WATCH_CSV, encoding="utf-8")
    plans, _ = parse(read_csv(url=None, path=str(path)))
    assert plans[0].holding == "XRP", "a byte-order mark must not eat the first column"


def test_a_reordered_tab_does_not_shift_every_field():
    """Headers locate the blocks, so a note added at the top is harmless."""
    padded = [["a note someone added"], [""]] + rows()
    plans, holdings = parse(padded)
    assert plans[0].holding == "XRP"
    assert holdings[0].weight == 0.44


def test_the_message_names_what_it_could_not_check():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {})
    assert "1 skipped, no live price: Gala" in report.text()


def test_prices_are_remembered_only_for_what_was_checked():
    plans, holdings = parse(rows())
    report = check(plans, holdings, {})
    assert set(report.prices) == {"XRP", "Cardano"}
    assert json.loads(json.dumps(report.prices)) == report.prices


SHEET = "https://docs.google.com/spreadsheets/d/1Jj0AlILnoTrUCs52aTaJNUAjRFSMTl4Xak-ri-aOqgI"


def test_the_link_from_the_address_bar_is_turned_into_data():
    """What a browser puts on the clipboard is an editor page.

    Fetching it returns HTML or a 401, which surfaced as a traceback ending in
    urllib rather than as anything a person could act on.
    """
    assert csv_url(f"{SHEET}/edit#gid=737126113").endswith(
        "/export?format=csv&gid=737126113"
    )
    assert csv_url(f"{SHEET}/edit?usp=sharing", "42").endswith("gid=42")


def test_a_published_csv_link_is_left_alone():
    published = (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vABC/pub"
        "?gid=737126113&single=true&output=csv"
    )
    assert csv_url(published) == published


def test_a_link_with_no_tab_says_which_part_is_missing():
    with pytest.raises(Unreadable) as problem:
        csv_url(f"{SHEET}/edit?usp=sharing")
    assert "does not say which tab" in str(problem.value)
    assert "--gid" in str(problem.value)


def test_something_that_is_not_a_sheet_says_so():
    with pytest.raises(Unreadable) as problem:
        csv_url("https://example.com/whatever")
    assert "does not look like a Google Sheets link" in str(problem.value)


def test_a_login_page_is_not_read_as_an_empty_portfolio(tmp_path):
    """HTML parsed as CSV is a nonsense row, not an error.

    Left alone it would report no holdings and no alerts, which reads exactly
    like a calm portfolio.
    """
    path = tmp_path / "login.csv"
    path.write_text("<!DOCTYPE html><html><body>Sign in</body></html>")
    with pytest.raises(Unreadable) as problem:
        read_csv(url=None, path=str(path))
    assert "wants a login" in str(problem.value)


def test_the_example_row_is_not_a_position():
    """It ships filled in to show the shape of a complete holding.

    On day one it is also the only row with a quantity, so it is 100% of the
    portfolio — and the first thing the watcher ever said was that the example
    had breached the weight limit.
    """
    plans, holdings = parse(rows())
    assert any(
        h.holding.startswith("EXAMPLE") for h in holdings
    ), "still in the fixture"
    report = check(
        plans,
        holdings,
        {"EXAMPLE — overwrite or delete this row": 1.0},
        max_weight=0.20,
    )
    assert not [a for a in report.alerts if "EXAMPLE" in a]
    assert not [s for s in report.skipped if "EXAMPLE" in s]

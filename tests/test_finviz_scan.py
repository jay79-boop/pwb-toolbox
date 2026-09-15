"""finviz_scan's pure logic -- no network, no finviz.com.

Rendering and filter-parsing are tested directly on fake data. The one thing
worth checking against the real finvizfinance package (installed, but never
called over the network here) is that every PRESETS entry is a filter name
and option value finviz actually has -- that's the failure mode a hand-typed
preset table would otherwise only surface live, on the owner's machine.
"""

import pandas as pd
import pytest

from tools.finviz_scan import (
    LOOKUP_ADDONS,
    PRESETS,
    human_number,
    parse_filter_kv,
    render_lookup,
    render_screener,
    run_addons,
)


def test_parse_filter_kv_splits_on_first_equals():
    assert parse_filter_kv("Sector=Technology") == ("Sector", "Technology")
    # values may legitimately contain '=' downstream from a preset edit; only
    # the first '=' is the separator
    assert parse_filter_kv("Market Cap.=+Large (over $10bln)") == (
        "Market Cap.",
        "+Large (over $10bln)",
    )


@pytest.mark.parametrize("bad", ["Sector", "Sector=", "=Technology", "  =  "])
def test_parse_filter_kv_rejects_malformed_input(bad):
    with pytest.raises(ValueError):
        parse_filter_kv(bad)


def test_render_screener_empty():
    assert "No tickers matched" in render_screener(pd.DataFrame(), 20)
    assert "No tickers matched" in render_screener(None, 20)


def test_render_screener_shows_all_rows_within_top():
    df = pd.DataFrame({"Ticker": ["AAPL", "MSFT"], "Price": [190.0, 410.0]})
    out = render_screener(df, top=20)
    assert "AAPL" in out and "MSFT" in out
    assert "more not shown" not in out


def test_render_screener_truncates_and_says_so():
    df = pd.DataFrame({"Ticker": [f"T{i}" for i in range(5)]})
    out = render_screener(df, top=2)
    assert "T0" in out and "T1" in out
    assert "T4" not in out
    assert "3 more not shown" in out


def test_render_screener_says_when_the_fetch_limit_cut_it_off():
    # Live 2026-09-15: a 100-row fetch printed "100 match(es)" while far more
    # tickers matched. At the limit the true count is unknown and must say so.
    df = pd.DataFrame({"Ticker": [f"T{i}" for i in range(100)]})
    out = render_screener(df, top=5, limit=100)
    assert "fetch limit" in out
    assert "100 match(es), showing" not in out


def test_render_screener_below_the_limit_is_a_real_count():
    df = pd.DataFrame({"Ticker": ["AAPL", "MSFT"]})
    out = render_screener(df, top=5, limit=100)
    assert "2 match(es), showing 2" in out
    assert "fetch limit" not in out


def test_render_screener_prints_readable_numbers_not_scientific():
    df = pd.DataFrame(
        {"Ticker": ["A"], "Market Cap": [4.139e10], "Volume": [1646765.0]}
    )
    out = render_screener(df, top=5)
    assert "41.39B" in out
    assert "1,646,765" in out
    assert "e+" not in out


def test_render_screener_does_not_alter_the_callers_frame():
    df = pd.DataFrame({"Ticker": ["A"], "Market Cap": [4.139e10]})
    render_screener(df, top=5)
    assert df["Market Cap"].iloc[0] == 4.139e10  # CSV export keeps the raw number


@pytest.mark.parametrize(
    "value,expected",
    [
        (4.861e12, "4.86T"),
        (4.139e10, "41.39B"),
        (3.94e6, "3.94M"),
        (1500, "1.50K"),
        (12, "12"),
        (float("nan"), ""),
        ("n/a", "n/a"),
    ],
)
def test_human_number(value, expected):
    assert human_number(value) == expected


def test_render_lookup_collapses_wrapped_headlines():
    # Live Finviz titles arrive as "\n      Title text\n    ".
    news = pd.DataFrame(
        {
            "Date": ["2026-09-14 22:39:00"],
            "Title": ["\n            Apple event\n            recap\n        "],
        }
    )
    out = render_lookup("AAPL", {"Company": "Apple Inc"}, news, None, None, None)
    assert "  2026-09-14 22:39:00  Apple event recap" in out


def test_lookup_addons_ship_empty():
    # Paid/AI sources are opt-in; the tool spends no tokens by default.
    assert LOOKUP_ADDONS == []


def test_run_addons_isolates_a_failing_source():
    def good(ticker):
        return f"summary for {ticker}"

    def broken(ticker):
        raise RuntimeError("no API key set")

    out = run_addons("AAPL", [("Broken source", broken), ("Good source", good)])
    assert out == [
        ("Broken source", None, "no API key set"),
        ("Good source", "summary for AAPL", None),
    ]


def test_render_lookup_shows_addon_sections_after_finviz():
    addons = [
        ("Research add-on (paid)", "line one\nline two", None),
        ("Other add-on", None, "timed out"),
    ]
    out = render_lookup(
        "AAPL", {"Company": "Apple Inc"}, None, None, None, None, addons=addons
    )
    assert out.index("-- Insider trades") < out.index("-- Research add-on (paid) --")
    assert "  line one\n  line two" in out
    assert "-- Other add-on --\n  could not load: timed out" in out


def test_render_lookup_reports_missing_fundamentals():
    out = render_lookup("XXXX", None, None, None, None, None)
    assert "No fundamentals returned" in out


def test_render_lookup_shows_selected_fields_in_order():
    fundament = {"Company": "Apple Inc", "P/E": "31.2", "Irrelevant Field": "1"}
    out = render_lookup("AAPL", fundament, None, None, None, None)
    assert "Apple Inc" in out
    assert "31.2" in out
    assert "Irrelevant Field" not in out
    # Company must render before P/E: FUNDAMENT_FIELDS order, not dict order
    assert out.index("Company") < out.index("P/E")


def test_render_lookup_news_and_insiders_happy_path():
    news = pd.DataFrame(
        {
            "Date": ["Sep-15-26"],
            "Title": ["Apple beats estimates"],
            "Link": ["https://example.com"],
        }
    )
    insiders = pd.DataFrame(
        {
            "Insider Trading": ["Cook Timothy"],
            "Relationship": ["CEO"],
            "Date": ["Sep-10-26"],
            "Transaction": ["Sale"],
            "Cost": ["190.00"],
            "#Shares": ["10,000"],
            "Value ($)": ["1,900,000"],
        }
    )
    out = render_lookup("AAPL", {"Company": "Apple Inc"}, news, None, insiders, None)
    assert "Apple beats estimates" in out
    assert "Cook Timothy" in out
    assert "could not load" not in out


def test_render_lookup_reports_errors_without_hiding_the_rest():
    out = render_lookup(
        "AAPL",
        {"Company": "Apple Inc"},
        None,
        "page layout changed",
        None,
        "no insider table on this ticker",
    )
    assert "Apple Inc" in out
    assert "could not load news: page layout changed" in out
    assert "could not load insider trades: no insider table on this ticker" in out


def test_presets_use_real_finviz_filter_names_and_options():
    """Offline sanity check against finvizfinance's own vendored filter
    table -- catches a typo'd preset (wrong name, wrong option spelling)
    without ever calling finviz.com."""
    from finvizfinance.screener.util import get_filter_options, get_filters

    valid_filters = set(get_filters())
    for preset_name, filters in PRESETS.items():
        for filter_name, option_value in filters.items():
            assert (
                filter_name in valid_filters
            ), f"preset {preset_name!r} uses unknown filter {filter_name!r}"
            assert option_value in get_filter_options(filter_name), (
                f"preset {preset_name!r} uses invalid option {option_value!r} "
                f"for filter {filter_name!r}"
            )

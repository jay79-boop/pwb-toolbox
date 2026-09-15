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
    PRESETS,
    parse_filter_kv,
    render_lookup,
    render_screener,
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

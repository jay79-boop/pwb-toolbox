"""finviz_scan's pure logic -- no network, no finviz.com.

Rendering and filter-parsing are tested directly on fake data. The one thing
worth checking against the real finvizfinance package (installed, but never
called over the network here) is that every PRESETS entry is a filter name
and option value finviz actually has -- that's the failure mode a hand-typed
preset table would otherwise only surface live, on the owner's machine.
"""

import argparse
from pathlib import Path

import pandas as pd
import pytest

import tools.finviz_scan as finviz_scan
from tools.finviz_scan import (
    LOOKUP_ADDONS,
    PRESETS,
    STOCK_ONLY_PRESETS,
    _grid_page_count,
    _grid_screener_view,
    _parse_grid_rows,
    cmd_screener,
    exclude_etfs,
    human_number,
    load_watchlist,
    parse_filter_kv,
    render_lookup,
    render_screener,
    run_addons,
    screener_tickers,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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


def _bars(closes):
    """Minimal daily bars for vet_candidates' fetch_bars mock -- a smaller,
    local twin of test_finviz_watchlist.make_bars kept separate rather than
    cross-imported, same reasoning as fetch_bars' own module comment."""
    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * n,
        }
    )


def _screener_args(tmp_path, **overrides):
    defaults = dict(
        preset=None,
        filter=None,
        order="Ticker",
        desc=False,
        limit=100,
        top=20,
        out=None,
        vet=False,
        add_flagged=False,
        vet_limit=25,
        file=str(tmp_path / "watchlist.txt"),
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# screener_tickers -- what --vet/--add-flagged and the menu's vetting step
# feed to vet_candidates()
# ---------------------------------------------------------------------------


def test_screener_tickers_dedupes_uppercases_and_preserves_order():
    df = pd.DataFrame({"Ticker": ["aapl", "MSFT", "AAPL", "nvda"]})
    assert screener_tickers(df) == ["AAPL", "MSFT", "NVDA"]


def test_screener_tickers_respects_limit():
    df = pd.DataFrame({"Ticker": [f"T{i}" for i in range(10)]})
    assert screener_tickers(df, limit=3) == ["T0", "T1", "T2"]


@pytest.mark.parametrize("df", [None, pd.DataFrame(), pd.DataFrame({"Price": [1.0]})])
def test_screener_tickers_empty_missing_or_no_ticker_column(df):
    assert screener_tickers(df) == []


# ---------------------------------------------------------------------------
# exclude_etfs -- Finviz has no stocks-only filter, so STOCK_ONLY_PRESETS
# drop funds client-side. Real-world case that flagged the bug (2026-09-22
# live run): breakout_squeeze matched AAXJ/ACWX/ACYN/AAPU alongside real
# stocks on price/volume filters alone.
# ---------------------------------------------------------------------------


def test_exclude_etfs_drops_only_the_fund_rows():
    df = pd.DataFrame(
        {
            "Ticker": ["AAPL", "AAXJ", "MSFT"],
            "Industry": [
                "Consumer Electronics",
                "Exchange Traded Fund",
                "Software - Application",
            ],
        }
    )
    out = exclude_etfs(df)
    assert list(out["Ticker"]) == ["AAPL", "MSFT"]


@pytest.mark.parametrize(
    "df", [None, pd.DataFrame(), pd.DataFrame({"Ticker": ["AAPL"]})]
)
def test_exclude_etfs_passes_through_when_nothing_to_filter(df):
    out = exclude_etfs(df)
    assert out is df or (out is not None and out.empty)


def test_stock_only_presets_are_the_three_breakout_short_presets():
    # a preset landing here without a real entry in PRESETS (or the reverse)
    # would silently stop filtering, or filter a preset that doesn't exist
    assert STOCK_ONLY_PRESETS == {
        "breakout_squeeze",
        "breakout_momentum",
        "megacap_short_exhaustion",
    }
    assert STOCK_ONLY_PRESETS <= set(PRESETS)


# ---------------------------------------------------------------------------
# cmd_screener --vet / --add-flagged -- the missing link the screener alone
# never had: nothing vetted a match, and nothing carried it to the
# watchlist. Both are exercised through cmd_screener itself, not just the
# pure pieces, so a wiring mistake between them would actually fail here.
# ---------------------------------------------------------------------------


def test_cmd_screener_vet_reports_but_does_not_touch_the_watchlist(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        finviz_scan,
        "fetch_screener",
        lambda *a, **k: pd.DataFrame({"Ticker": ["HOT", "COLD"]}),
    )
    monkeypatch.setattr(
        finviz_scan,
        "fetch_bars",
        lambda symbols: {
            "HOT": _bars([100.0] * 259 + [140.0]),  # sharp move -- something fires
            "COLD": _bars([100.0] * 260),  # flat -- nothing should fire
        },
    )
    args = _screener_args(tmp_path, vet=True)
    cmd_screener(args)
    out = capsys.readouterr().out
    assert "candidate vetting" in out
    assert not Path(args.file).exists()  # --vet alone never writes


def test_cmd_screener_add_flagged_writes_only_the_flagged_tickers(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        finviz_scan,
        "fetch_screener",
        lambda *a, **k: pd.DataFrame({"Ticker": ["HOT", "COLD"]}),
    )
    monkeypatch.setattr(
        finviz_scan,
        "fetch_bars",
        lambda symbols: {
            "HOT": _bars([100.0] * 259 + [140.0]),
            "COLD": _bars([100.0] * 260),
        },
    )
    args = _screener_args(tmp_path, add_flagged=True)
    cmd_screener(args)
    out = capsys.readouterr().out
    assert "added 1 flagged ticker(s)" in out
    saved = load_watchlist(Path(args.file))
    assert saved == ["HOT"]


def test_cmd_screener_add_flagged_leaves_watchlist_unchanged_when_nothing_flags(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        finviz_scan,
        "fetch_screener",
        lambda *a, **k: pd.DataFrame({"Ticker": ["COLD"]}),
    )
    monkeypatch.setattr(
        finviz_scan, "fetch_bars", lambda symbols: {"COLD": _bars([100.0] * 260)}
    )
    args = _screener_args(tmp_path, add_flagged=True)
    cmd_screener(args)
    out = capsys.readouterr().out
    assert "nothing flagged -- watchlist unchanged" in out
    assert not Path(args.file).exists()


def test_cmd_screener_drops_etfs_for_a_stock_only_preset(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        finviz_scan,
        "fetch_screener",
        lambda *a, **k: pd.DataFrame(
            {
                "Ticker": ["AAPL", "AAXJ"],
                "Industry": ["Consumer Electronics", "Exchange Traded Fund"],
            }
        ),
    )
    monkeypatch.setattr(
        finviz_scan, "fetch_bars", lambda symbols: {"AAPL": _bars([100.0] * 260)}
    )
    args = _screener_args(tmp_path, preset="breakout_squeeze", vet=True)
    cmd_screener(args)
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "AAXJ" not in out


def test_cmd_screener_keeps_etfs_for_a_non_stock_only_preset(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        finviz_scan,
        "fetch_screener",
        lambda *a, **k: pd.DataFrame(
            {
                "Ticker": ["AAPL", "AAXJ"],
                "Industry": ["Consumer Electronics", "Exchange Traded Fund"],
            }
        ),
    )
    args = _screener_args(tmp_path, preset="large_cap_uptrend")
    cmd_screener(args)
    out = capsys.readouterr().out
    assert "AAXJ" in out


def test_cmd_screener_vet_with_no_matches_skips_fetching_bars(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(finviz_scan, "fetch_screener", lambda *a, **k: pd.DataFrame())

    def boom(symbols):
        raise AssertionError("fetch_bars should not be called with nothing to vet")

    monkeypatch.setattr(finviz_scan, "fetch_bars", boom)
    args = _screener_args(tmp_path, vet=True)
    cmd_screener(args)
    assert "Nothing to vet" in capsys.readouterr().out


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


# ---------------------------------------------------------------------------
# finviz.com's 2026-10 redesign replaced the screener's <table>/<select
# id="pageSelect"> markup with a div-based ARIA grid; finvizfinance 1.5.0's
# own Overview.screener_view() still expects the old markup and returns None
# for every query against it. fetch_screener() now parses the grid itself
# (_parse_grid_rows/_grid_page_count/_grid_screener_view below) -- these
# tests pin that parsing against a real captured page, offline, so a future
# finviz redesign is caught here rather than only live on the owner's
# machine. The fixture is a trimmed single-page screener result (header +
# two data rows, no pager) saved 2026-10-22 from a real megacap_short_exhaustion
# query that returned too few matches to paginate -- exactly the case that
# silently produced None (0 results) before this fix, even though real rows
# were on the page.
# ---------------------------------------------------------------------------


def _load_grid_fixture():
    from bs4 import BeautifulSoup

    html = (FIXTURES_DIR / "finviz_screener_grid_single_page.html").read_text(
        encoding="utf-8"
    )
    return BeautifulSoup(html, "html.parser")


def test_parse_grid_rows_reads_real_ticker_not_duplicated_cell_text():
    """The ticker cell's visible text duplicates the symbol (a hidden
    logo-fallback span repeats it, so get_text() on ticker "A" reads "AA")
    -- the parser must read the clean value from the cell's data-ticker
    attribute instead, the same fix finvizfinance's own old table parser
    made for the identical bug under a different attribute name."""
    rows = _parse_grid_rows(_load_grid_fixture())
    assert [r["Ticker"] for r in rows] == ["COST", "CVX"]


def test_parse_grid_rows_converts_only_the_numeric_columns():
    rows = _parse_grid_rows(_load_grid_fixture())
    cost = rows[0]
    assert cost["Market Cap"] == pytest.approx(442_380_000_000.0)
    assert cost["P/E"] == pytest.approx(48.09)
    assert cost["Price"] == pytest.approx(998.31)
    assert cost["Volume"] == pytest.approx(1_512_498.0)
    # Change % is deliberately left as finviz's own string ("0.37%"), not
    # converted -- _fetch_map in finviz_dash.py parses the "%" itself.
    assert cost["Change %"] == "0.37%"
    assert isinstance(cost["Change %"], str)


def test_grid_page_count_is_zero_when_results_fit_on_one_page():
    """A real, common case (a narrow filter combo matches a handful of
    tickers): finviz doesn't render the "N / M" pager button at all, which
    must not be read as 0 matches -- see
    test_grid_screener_view_keeps_rows_even_with_no_pager below."""
    assert _grid_page_count(_load_grid_fixture()) == 0


def test_grid_screener_view_keeps_rows_even_with_no_pager(monkeypatch):
    """The regression this fix is for: _grid_page_count legitimately reads 0
    for a single-page result (no pager button rendered), and that must not
    make _grid_screener_view discard the real rows already parsed off page
    one and return None instead."""
    monkeypatch.setattr(
        "finvizfinance.util.web_scrap", lambda url, params: _load_grid_fixture()
    )

    class FakeOverview:
        url = "https://finviz.com/screener.ashx"
        size = 20
        request_params = {"v": 111}

        def reset(self):
            pass

    df = _grid_screener_view(FakeOverview(), limit=100)
    assert df is not None
    assert list(df["Ticker"]) == ["COST", "CVX"]

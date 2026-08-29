"""The symbol filter in `load_dataset`, pinned across the upstream merge.

Upstream's 16ad974 pushes the symbol filter down into each parquet read, which
is what makes a large sharded dataset loadable when only a few symbols are
wanted. This fork had independently added a free yfinance fallback and had
expanded `sp500` by mutating the caller's list in place. Merging the two kept
both, and settled the overlap in upstream's favour: the filter is resolved once
onto a copy and handed to whichever loader runs.

These tests exist because that resolution changed real behaviour in a file with
almost no coverage, and none of it is reachable without a token or a network
call unless the loaders are stubbed.
"""

import pandas as pd
import pytest

import pwb_toolbox.datasets as ds


@pytest.fixture
def captured(monkeypatch):
    """Run load_dataset with every loader stubbed, and record what it was given."""

    seen = {}

    def fake_pwb(dataset_name, split, pwb_api_key, symbols=None):
        seen["loader"] = "pwb"
        seen["symbols"] = symbols
        return pd.DataFrame(
            {"date": ["2026-01-02", "2026-01-02"], "symbol": ["AAPL", "ZZZZ"]}
        )

    def fake_hf(dataset_name, split, hf_token, symbols=None):
        seen["loader"] = "hf"
        seen["symbols"] = symbols
        return pd.DataFrame({"date": ["2026-01-02"], "symbol": ["AAPL"]})

    def fake_yf(dataset_name, symbols):
        seen["loader"] = "yfinance"
        seen["symbols"] = symbols
        return pd.DataFrame({"date": ["2026-01-02"], "symbol": ["AAPL"]})

    monkeypatch.setattr(ds, "_load_dataset_from_pwb", fake_pwb)
    monkeypatch.setattr(ds, "_load_dataset_from_hf", fake_hf)
    monkeypatch.setattr(ds, "_load_dataset_from_yfinance", fake_yf)
    return seen


def use_pwb(monkeypatch):
    monkeypatch.setattr(ds, "_get_pwb_api_key", lambda: "key")


def use_no_token(monkeypatch):
    monkeypatch.setattr(ds, "_get_pwb_api_key", lambda: None)
    monkeypatch.delenv("HF_ACCESS_TOKEN", raising=False)


def test_the_filter_is_pushed_down_to_the_loader(captured, monkeypatch):
    use_pwb(monkeypatch)
    ds.load_dataset("Stocks-Daily-Price", symbols=["AAPL"], to_usd=False)

    assert captured["loader"] == "pwb"
    assert captured["symbols"] == ["AAPL"]


def test_the_callers_list_is_not_mutated(captured, monkeypatch):
    """The pre-merge code did `symbols.remove("sp500")` on the caller's list, so
    a caller reusing its list got a different universe on the second call."""

    use_pwb(monkeypatch)
    mine = ["sp500", "AAPL"]
    ds.load_dataset("Stocks-Daily-Price", symbols=mine, to_usd=False)

    assert mine == ["sp500", "AAPL"]


def test_sp500_is_expanded_before_the_loader_sees_it(captured, monkeypatch):
    use_pwb(monkeypatch)
    ds.load_dataset("Stocks-Daily-Price", symbols=["sp500"], to_usd=False)

    got = captured["symbols"]
    assert "sp500" not in got
    assert len(got) == len(ds.SP500_SYMBOLS)
    assert "AAPL" in got


def test_the_yfinance_fallback_gets_the_expanded_filter(captured, monkeypatch):
    """The free path must resolve `sp500` exactly as the paid paths do."""

    use_no_token(monkeypatch)
    ds.load_dataset("Stocks-Daily-Price", symbols=["sp500"], to_usd=False)

    assert captured["loader"] == "yfinance"
    assert "sp500" not in captured["symbols"]
    assert "AAPL" in captured["symbols"]


def test_the_post_filter_still_drops_unrequested_symbols(captured, monkeypatch):
    """A loader that ignores the pushdown must not widen the result."""

    use_pwb(monkeypatch)
    df = ds.load_dataset("Stocks-Daily-Price", symbols=["AAPL"], to_usd=False)

    assert sorted(df["symbol"].unique()) == ["AAPL"]


def test_no_symbols_means_no_filter(captured, monkeypatch):
    use_pwb(monkeypatch)
    df = ds.load_dataset("Stocks-Daily-Price", to_usd=False)

    assert captured["symbols"] is None
    assert sorted(df["symbol"].unique()) == ["AAPL", "ZZZZ"]


def test_a_symbols_list_column_is_filtered_by_overlap(monkeypatch):
    """News-style rows carry a list of tickers; a row matches on any overlap."""

    monkeypatch.setattr(ds, "_get_pwb_api_key", lambda: "key")
    monkeypatch.setattr(
        ds,
        "_load_dataset_from_pwb",
        lambda *a, **k: pd.DataFrame(
            {"date": ["2026-01-02"] * 3, "symbols": [["AAPL", "MSFT"], ["TSLA"], None]}
        ),
    )
    df = ds.load_dataset("News", symbols=["AAPL"], to_usd=False)

    assert len(df) == 1
    assert df.iloc[0]["symbols"] == ["AAPL", "MSFT"]


def test_no_token_and_no_fallback_still_raises(monkeypatch):
    use_no_token(monkeypatch)
    with pytest.raises(ValueError, match="PWB_API_KEY or HF_ACCESS_TOKEN"):
        ds.load_dataset("Some-Unsupported-Dataset", symbols=["AAPL"])

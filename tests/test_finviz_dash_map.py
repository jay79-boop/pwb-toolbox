"""_fetch_map's heat map builder -- no network, no finviz.com.

Older finvizfinance releases returned None (not an empty DataFrame) when a
filter set matched zero tickers; _fetch_map() guards for that. finvizfinance
1.7.0 (confirmed live 2026-11-18) instead returns an empty DataFrame for the
same case, so the guard checks both shapes -- otherwise a quiet S&P 500
index fetch would either crash with AttributeError: 'NoneType' object has no
attribute 'iterrows' (the None case) or silently fall through (the empty
case, harmless here only because the iterrows loop below happens to no-op).
"""

import pandas as pd

import tools.finviz_dash as dash


def test_fetch_map_handles_none_frame(monkeypatch):
    monkeypatch.setattr(dash.finviz_scan, "fetch_screener", lambda *a, **k: None)
    result = dash._fetch_map()
    assert result["sectors"] == []
    assert result["skipped"] == 0
    assert "fetched_at" in result


def test_fetch_map_handles_empty_frame(monkeypatch):
    monkeypatch.setattr(
        dash.finviz_scan, "fetch_screener", lambda *a, **k: pd.DataFrame()
    )
    result = dash._fetch_map()
    assert result["sectors"] == []
    assert result["skipped"] == 0
    assert "fetched_at" in result

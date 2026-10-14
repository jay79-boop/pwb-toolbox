"""_fetch_map's heat map builder -- no network, no finviz.com.

finvizfinance's screener_view() returns None (not an empty DataFrame) when a
filter set matches zero tickers -- _screener_payload() already guards for
that (see the comment next to it in finviz_dash.py); _fetch_map() did not,
so a quiet S&P 500 index fetch would crash the heat map with
AttributeError: 'NoneType' object has no attribute 'iterrows'.
"""

import tools.finviz_dash as dash


def test_fetch_map_handles_none_frame(monkeypatch):
    monkeypatch.setattr(dash.finviz_scan, "fetch_screener", lambda *a, **k: None)
    result = dash._fetch_map()
    assert result["sectors"] == []
    assert result["skipped"] == 0
    assert "fetched_at" in result

"""The dashboard's local API must not be drivable by other websites.

Any page the owner visits can make the browser send a GET to 127.0.0.1 -- an
<img> tag is enough. Before this guard, that was enough to edit the watchlist,
write a CSV to the Desktop, or open a PowerShell window. These tests run a real
server on a free port and speak raw HTTP to it, so they check the actual
headers the handler sees. No network beyond loopback, nothing is launched.
"""

import http.client
import threading

import pytest

import tools.finviz_dash as dash


@pytest.fixture()
def server(monkeypatch):
    calls = []
    # Stub every action with a side effect, so a test that wrongly gets through
    # records a call instead of opening a console or editing the real list.
    monkeypatch.setattr(
        dash, "_open_text_menu", lambda: calls.append("menu") or {"ok": True}
    )
    monkeypatch.setattr(
        dash, "_watchlist_add", lambda t: calls.append(("add", t)) or {"ok": True}
    )
    monkeypatch.setattr(
        dash, "_watchlist_remove", lambda t: calls.append(("rm", t)) or {"ok": True}
    )
    monkeypatch.setattr(dash, "_presets_payload", lambda: [])
    srv = dash.ThreadingHTTPServer(("127.0.0.1", 0), dash._Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1], calls
    finally:
        srv.shutdown()
        srv.server_close()


def _get(port, path, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("GET", path, skip_host=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    conn.endheaders()
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


@pytest.mark.parametrize(
    "path",
    [
        "/api/text-menu",
        "/api/watchlist-add?t=EVIL",
        "/api/watchlist-remove?t=AAPL",
        "/api/screener-export?preset=x",
    ],
)
def test_write_actions_refused_without_page_header(server, path):
    port, calls = server
    assert _get(port, path) == 403
    assert calls == []


def test_write_action_allowed_from_the_page(server):
    port, calls = server
    assert _get(port, "/api/text-menu", {"X-Finviz-Dash": "1"}) == 200
    assert _get(port, "/api/watchlist-add?t=NVDA", {"X-Finviz-Dash": "1"}) == 200
    assert calls == ["menu", ("add", "NVDA")]


def test_localhost_name_is_accepted(server):
    port, calls = server
    status = _get(
        port, "/api/text-menu", {"X-Finviz-Dash": "1"}, host=f"localhost:{port}"
    )
    assert status == 200
    assert calls == ["menu"]


@pytest.mark.parametrize(
    "host", ["evil.example:{port}", "127.0.0.1:1", "", "127.0.0.1.evil.example"]
)
def test_foreign_host_refused_even_with_header(server, host):
    """DNS rebinding: a hostile domain re-pointed at 127.0.0.1 still sends its
    own name as Host, so the header alone is not enough."""
    port, calls = server
    status = _get(
        port, "/api/text-menu", {"X-Finviz-Dash": "1"}, host=host.format(port=port)
    )
    assert status == 403
    assert calls == []


def test_foreign_host_cannot_read_either(server):
    port, _ = server
    assert _get(port, "/api/presets", host="evil.example") == 403
    assert _get(port, "/api/presets") == 200


def test_read_actions_need_no_header(server):
    port, _ = server
    assert _get(port, "/api/presets") == 200


def test_preflight_is_never_approved(server):
    """A cross-origin fetch with a custom header triggers an OPTIONS preflight.
    If the server ever answered it with CORS headers, the guard would be moot."""
    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "OPTIONS",
        "/api/text-menu",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-finviz-dash",
        },
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status >= 400
    assert resp.getheader("Access-Control-Allow-Origin") is None

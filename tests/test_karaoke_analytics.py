"""The karaoke queue page reports to Amplitude without ever touching a CDN.

The page's network surface is pinned to fonts and the YouTube player by
``test_karaoke_qr.py``, and the reason is in the page: on a captive portal a
CDN <script> "loads" a login page. So the Amplitude browser SDK travels the
same way the QR encoder does -- inside what the queue server serves -- as the
exact UMD build ``@amplitude/unified`` ships, vendored under ``static/vendor``
and embedded into the standalone build. These tests pin that route, and that a
page whose SDK did not load still joins singers.
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.karaoke_server import build_standalone, queue_server

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "karaoke-queue.html"
SDK = ROOT / "static" / "vendor" / "amplitude-unified.umd.js"


def test_the_sdk_is_the_unified_packages_own_umd_build():
    text = SDK.read_text(encoding="utf-8")
    assert text.startswith("!function("), "not a UMD bundle"
    assert ".amplitude={}" in text, "the bundle must define window.amplitude"
    assert "initAll" in text


def test_the_page_loads_the_sdk_from_the_server_never_a_cdn():
    page = PAGE.read_text(encoding="utf-8")
    assert page.count('<script src="vendor/amplitude-unified.umd.js"></script>') == 1
    for ref in re.findall(r"<script src=\"([^\"]+)\"", page):
        assert not ref.startswith(("http:", "https:", "//")), ref


def test_init_is_guarded_and_happens_once():
    page = PAGE.read_text(encoding="utf-8")
    assert page.count("amplitude.initAll(") == 1
    guard = page.index('typeof amplitude !== "undefined"')
    assert guard < page.index("amplitude.initAll("), "init before the guard"
    assert "Amplitude SDK missing" in page, "a missing SDK has to say so"
    assert '"sessionReplay":{"sampleRate":1}' in page
    assert '"analytics":{"autocapture":true}' in page


def test_the_one_event_fires_on_join_success_only():
    page = PAGE.read_text(encoding="utf-8")
    assert page.count("amplitude.track(") == 1
    track = page.index("amplitude.track('Joined Queue'")
    assert "prompt_version: 'BA400.4'" in page[track : track + 120]
    # inside the /join success callback, after the error banner is cleared
    join = page.index('post("/join"')
    cleared = page.index('show("joinErr", false);', join)
    next_card = page.index('phoneShow("songCard")', join)
    assert cleared < track < next_card
    # and never on the path that shows the error
    assert "amplitude.track" not in page[next_card : next_card + 200]


def test_the_key_is_inline_and_labelled_public():
    page = PAGE.read_text(encoding="utf-8")
    assert 'var AMPLITUDE_KEY = "' in page
    assert "public by design" in page


def test_the_server_serves_exactly_the_vendored_files():
    assert queue_server.vendor_name("/vendor/amplitude-unified.umd.js") == (
        "amplitude-unified.umd.js"
    )
    assert queue_server.vendor_name("/vendor/../queue_server.py") is None
    assert queue_server.vendor_name("/vendor/") is None
    assert queue_server.vendor_name("/amplitude-unified.umd.js") is None
    assert queue_server.vendor_source("amplitude-unified.umd.js") == SDK.read_text(
        encoding="utf-8"
    )
    assert queue_server.vendor_source("anything-else.js") is None


def test_the_embedded_copy_wins_over_the_disk(monkeypatch):
    monkeypatch.setattr(
        queue_server, "EMBEDDED_VENDOR", {"amplitude-unified.umd.js": "// embedded"}
    )
    assert queue_server.vendor_source("amplitude-unified.umd.js") == "// embedded"


def test_the_sdk_travels_inside_the_standalone_file():
    built = build_standalone.build()
    assert "EMBEDDED_VENDOR = " in built
    namespace = {}
    start = built.index("EMBEDDED_VENDOR = ")
    end = built.index("\n", start)
    exec(built[start:end], namespace)  # one assignment, the dict literal
    assert namespace["EMBEDDED_VENDOR"] == {
        "amplitude-unified.umd.js": SDK.read_text(encoding="utf-8")
    }

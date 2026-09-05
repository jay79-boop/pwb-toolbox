"""The QR on the screen is drawn on the machine, not fetched from a CDN.

The screen used to pull qrcodejs from cdnjs at render time. A pub's Wi-Fi has
no reason to reach the internet, and a captive portal is worse than no
internet at all: the ``<script>`` loads, is a login page rather than the
library, and the QR call throws. Either way the one thing that gets singers
into the queue is the thing that breaks, and it breaks at the door on a busy
night rather than in a test.

So the encoder now lives inside ``static/karaoke-queue.html`` and ships inside
``karaoke_os.py``. ``static/karaoke-qr.test.js`` is the suite proper --
matrices compared against fixtures that python-qrcode produced and OpenCV
decoded. This module runs it, and pins the page's remaining network surface so
a new CDN dependency cannot arrive unnoticed.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "karaoke-queue.html"
SUITE = ROOT / "static" / "karaoke-qr.test.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


@needs_node
def test_the_qr_suite_passes():
    done = subprocess.run(
        ["node", str(SUITE)], capture_output=True, text=True, cwd=ROOT
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_no_qr_library_is_fetched_at_render_time():
    page = PAGE.read_text(encoding="utf-8")
    assert "cdnjs" not in page
    assert "qrcode.min.js" not in page
    assert "new QRCode(" not in page


def test_the_pages_whole_network_surface_is_fonts_and_the_youtube_player():
    """Everything else has to work with the venue's router unplugged.

    Fonts fall back to a real stack when they do not load, and the YouTube
    player needs the internet by definition -- there is no offline karaoke
    video. Nothing else may reach out, least of all on the join path.
    """
    page = PAGE.read_text(encoding="utf-8")
    hosts = {
        re.sub(r"(https?://[^/]+).*", r"\1", ref)
        for ref in re.findall(r"https?://[^\"' )]*", page)
    }
    assert hosts == {
        "https://fonts.googleapis.com",
        "https://www.youtube.com",
        "https://youtube.com",
    }


def test_the_encoder_is_where_the_suite_looks_for_it():
    page = PAGE.read_text(encoding="utf-8")
    assert page.count("  // ==== qr, drawn here") == 1
    assert page.count("  // ==== end qr") == 1
    assert page.index("// ==== qr, drawn here") < page.index("// ==== end qr")

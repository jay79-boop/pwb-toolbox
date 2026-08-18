"""Run the trade-journal field suite under pytest.

``static/trade-journal.html`` is deliberately one self-contained file — it opens
from ``file://`` with no server and no build step, which is what makes it usable
straight out of a synced folder. Its arithmetic therefore cannot be imported, so
``static/trade-journal.test.js`` slices the pure block out of the HTML between
its comment markers and runs it under node.

Shelling out to node keeps that arithmetic inside the same ``pytest tests/ -v``
everything else is gated by, without adding a JavaScript toolchain to the
repository: the suite is plain ``node``, no dependencies and no config.

The cap those tests cover is the reason to bother. A journal that records more
at risk than the position can lose poisons every R multiple drawn from that
column in the direction that flatters the trader, and it does so silently.
"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "static", "trade-journal.test.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_trade_journal_suite():
    result = subprocess.run(
        ["node", SUITE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "0 failed" in output, output

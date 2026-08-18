"""Run the trade-journal autofill suite under pytest.

The journal is a single HTML file, so its arithmetic lives in JavaScript rather
than in ``pwb_toolbox``. Shelling out to node keeps that arithmetic inside the
same ``pytest tests/ -v`` everything else is gated by, without adding a
JavaScript toolchain to the repository: the suite is plain ``node``, no
dependencies and no config.

The clamp these tests cover is the reason to bother. A journal that records a
risk larger than the debit paid poisons every statistic drawn from that column
in the direction that flatters the trader, and it does so silently.
"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "static", "trade-journal-autofill.test.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_trade_journal_autofill_suite():
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

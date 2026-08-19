"""Run the journal screenshot suite under pytest.

``static/journal-shots.js`` re-encodes a pasted chart and then decides whether
the result fits the roughly 5 MB of localStorage a ``file://`` page is given.
The encoding needs a canvas and is checked in a browser; what is checked here is
the arithmetic around it, which is where a quiet mistake costs data. An
overstated byte count or an off-by-one budget lets through a screenshot that
cannot be stored, and localStorage reports that by throwing at save time — after
the trade is already in memory and looks logged.
"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "static", "journal-shots.test.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_journal_shots_suite():
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

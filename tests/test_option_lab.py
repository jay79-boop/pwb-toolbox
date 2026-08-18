"""Reconcile ``static/option-lab.js`` against ``pwb_toolbox.options``.

The journal is a single HTML file that opens from ``file://`` with no server
and no build step, so its options math has to be JavaScript. That leaves two
implementations of Black-Scholes in one repository, which is only safe if
something keeps them honest.

This does. It prices a spread of contracts through the Python module — the
authority, since it is what the backtests and ``tools/trade_card.py`` use — and
hands the same cases to node to price again, requiring agreement to 1e-9. A
change to either side that moves a number now fails here rather than showing up
as a journal that quietly disagrees with the trade card about the same
contract.

The second test runs the JavaScript suite proper, which covers what Python has
no counterpart for: rho, the touch and finish probabilities, and the ladders.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "static", "option-lab.test.js")

sys.path.insert(0, ROOT)

from pwb_toolbox.options.decay import (  # noqa: E402
    breakeven_spot,
    decay_schedule,
    hurdle_ratio,
)
from pwb_toolbox.options.greeks import (  # noqa: E402
    black_scholes,
    expected_move,
    implied_vol,
)

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)

# Spread deliberately across the awkward corners: at the money, far out of the
# money on a week, a long-dated put, one day left, and the low-priced
# high-volatility contract that a retail account actually ends up holding.
CONTRACTS = [
    (232.0, 230.0, 38.0, 0.28, 0.045, "call"),
    (232.0, 230.0, 38.0, 0.28, 0.045, "put"),
    (100.0, 120.0, 7.0, 0.60, 0.045, "call"),
    (500.0, 480.0, 400.0, 0.18, 0.045, "put"),
    (50.0, 50.0, 1.0, 0.35, 0.045, "call"),
    (12.6, 14.0, 21.0, 0.85, 0.045, "call"),
    (4.0, 2.5, 90.0, 1.20, 0.045, "call"),
]

FIELDS = ["price", "delta", "gamma", "theta", "vega", "intrinsic", "extrinsic"]


def _cases():
    """Every (function, args, expected) triple, expectations from Python."""
    out = []
    for args in CONTRACTS:
        spot, strike, days, vol, rate, kind = args
        g = black_scholes(*args)
        out.append(
            {
                "fn": "blackScholes",
                "args": list(args),
                "fields": FIELDS,
                "expected": [getattr(g, f) for f in FIELDS],
            }
        )
        out.append(
            {
                "fn": "impliedVol",
                "args": [g.price, spot, strike, days, rate, kind],
                "expected": implied_vol(g.price, spot, strike, days, rate, kind),
            }
        )
        out.append(
            {
                "fn": "expectedMove",
                "args": [spot, vol, 5.0],
                "expected": expected_move(spot, vol, 5.0),
            }
        )
        out.append(
            {
                "fn": "hurdleRatio",
                "args": list(args),
                "expected": hurdle_ratio(*args),
            }
        )
        target = g.price * 1.5
        out.append(
            {
                "fn": "breakevenSpot",
                "args": [target, strike, days, vol, rate, kind],
                "expected": breakeven_spot(target, strike, days, vol, rate, kind),
            }
        )
        out.append(
            {
                "fn": "decaySchedule",
                "args": list(args),
                "expected": [list(t) for t in decay_schedule(*args)],
            }
        )
    return out


@needs_node
def test_js_agrees_with_python(tmp_path):
    spec = tmp_path / "cross.json"
    spec.write_text(json.dumps({"tolerance": 1e-9, "cases": _cases()}))
    result = subprocess.run(
        ["node", SUITE, "--cross", str(spec)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "0 failed" in output, output


@needs_node
def test_option_lab_suite():
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

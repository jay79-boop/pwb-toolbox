"""Run the Strategy Lab: `python -m tools.strategy_lab`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .server import build_server
from .store import RunStore


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.strategy_lab", description=__doc__
    )
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="0.0.0.0 to reach the lab from a phone on the same network",
    )
    ap.add_argument("--runs", default="strategy-runs", help="directory of run records")
    ap.add_argument(
        "--export",
        metavar="PATH",
        help="write a standalone snapshot with the runs baked in, and exit",
    )
    args = ap.parse_args(argv)

    store = RunStore(Path(args.runs))

    if args.export:
        from .server import page_html

        html = page_html(runs=store.all(), live=False)
        Path(args.export).write_text(html, encoding="utf-8")
        print(f"wrote {args.export} ({len(store.ids())} runs, {len(html) // 1024} KB)")
        return 0

    httpd = build_server(store, args.host, args.port)
    shown = "localhost" if args.host == "127.0.0.1" else args.host
    print(
        f"Strategy Lab on http://{shown}:{args.port}  ({len(store.ids())} runs in {args.runs}/)"
    )
    print("Post a run:  python tools/reversal_15m_sim.py bars.csv --post")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build karaoke_os.py: the whole karaoke queue OS as one portable file.

    python tools/karaoke_server/build_standalone.py --out dist/karaoke_os.py

The output is a single stdlib-only Python file carrying the rotation
engine, the room layer, the HTTP server, and the page itself (embedded as
a string), plus a launcher that opens the big screen in a browser. Copy it
to any computer with Python 3.10+ and run it -- no repo, no pip, no
setup:

    python karaoke_os.py

The release workflow additionally freezes it into KaraokeQueue.exe with
PyInstaller so a Windows machine needs no Python at all.

The build is mechanical concatenation, not a rewrite: module text is
taken verbatim with only relative imports, ``__future__`` lines, and the
final __main__ block stripped, and the page is injected by rebinding
``EMBEDDED_PAGE``. Anything cleverer would mean the tested modules and
the shipped file stop being the same code -- the exact trap the journal's
inline-verbatim rule exists to avoid. tests/test_karaoke_standalone.py
imports the built artifact and runs a night through it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .queue_server import VENDOR_FILES
except ImportError:  # run as a script: python tools/karaoke_server/build_standalone.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.karaoke_server.queue_server import VENDOR_FILES

PKG = Path(__file__).resolve().parent
REPO_ROOT = PKG.parents[1]
PAGE = REPO_ROOT / "static" / "karaoke-queue.html"
VENDOR = REPO_ROOT / "static" / "vendor"
MODULES = ("rotation.py", "room.py", "queue_server.py")

HEADER = '''"""karaoke-os: a random-but-fair karaoke queue for one room. One file.

Run it on a machine on the venue's Wi-Fi:

    python karaoke_os.py

It prints one address. Open /screen on that address on the big screen
(it opens itself in a browser on this machine); everyone else scans the
QR the screen shows and lands on the phone page: name, song, and a
full-screen YOU'RE UP when the random draw lands on them.

Standard library only. Singer memory is saved next to this program in
karaoke-profiles.json. Generated from the pwb-toolbox repo by
tools/karaoke_server/build_standalone.py -- edit there, not here.
"""

from __future__ import annotations

import sys
'''

QUEUE_SERVER_TAIL = 'if __name__ == "__main__":\n    main()\n'

FOOTER = """

# ==== standalone launcher ============================================


def _home_dir():
    # Where this program lives -- the frozen exe's folder, or the .py's.
    # NOT the working directory: a double-clicked exe run as administrator
    # gets C:\\Windows\\System32, and one opened from inside a zip gets a
    # temp dir that is later deleted, so singer memory would scatter or
    # vanish. Under PyInstaller __file__ points into the extracted _MEI
    # dir, which is also wrong -- sys.executable is the exe itself.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _standalone_main(argv=None):
    import webbrowser

    parser = argparse.ArgumentParser(description="karaoke-os: one-room karaoke queue")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--profiles", default=None, help="singer memory JSON path")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open the screen here"
    )
    parser.add_argument(
        "--selfcheck", action="store_true", help="prove the build works, then exit"
    )
    args = parser.parse_args(argv)

    if args.selfcheck:
        import random as _random

        room = QueueRoom(None, RotationConfig(), rng=_random.Random(1))
        ada = room.join({"name": "Ada"}, 0.0)["singer_id"]
        room.song({"singer_id": ada, "title": "Nine to Five"}, 0.0)
        state = room.state(1.0, singer_id=ada)
        assert state["called"]["singer_id"] == ada, "solo singer was not called"
        room.here({"singer_id": ada}, 10.0)
        room.retime({"singer_id": ada, "remaining_s": 5.0}, 20.0)
        state = room.state(30.0, singer_id=ada)
        assert state["singing"] is None and state["you"]["needs_song"]
        assert state["house_on"], "house music did not come back up"
        doc = page_html("screen")
        assert "<title>Karaoke Queue</title>" in doc, "page is not embedded"
        print("karaoke-os selfcheck: OK")
        return 0

    profiles = args.profiles or os.environ.get("KARAOKE_PROFILES")
    if not profiles:
        profiles = str(_home_dir() / "karaoke-profiles.json")

    if not args.no_browser:
        # Open the screen on the address PHONES will use, never localhost:
        # the page builds its QR from location.origin, so a screen opened
        # at localhost shows a QR that every phone in the room fails to
        # reach. This is the whole product on the default double-click path.
        host = args.host if args.host not in ("0.0.0.0", "") else None
        reachable = host or lan_address() or "localhost"
        threading.Timer(
            1.5, webbrowser.open, [f"http://{reachable}:{args.port}/screen"]
        ).start()
    serve(args.host, args.port, profiles)
    return 0


if __name__ == "__main__":
    raise SystemExit(_standalone_main())
"""


def module_body(name: str) -> str:
    text = (PKG / name).read_text(encoding="utf-8")
    if name == "queue_server.py":
        if QUEUE_SERVER_TAIL not in text:
            raise SystemExit(f"{name}: expected __main__ block not found")
        text = text.replace(QUEUE_SERVER_TAIL, "")
    kept = []
    in_relative = False
    for line in text.splitlines():
        if in_relative:  # inside a multi-line "from .x import (...)"
            if line.strip().endswith(")"):
                in_relative = False
            continue
        if line.startswith("from __future__"):
            continue
        if line.startswith("from ."):
            in_relative = line.rstrip().endswith("(")
            continue
        kept.append(line)
    return "\n".join(kept).strip() + "\n"


def build() -> str:
    parts = [HEADER]
    for name in MODULES:
        parts.append(f"\n\n# ==== {name} " + "=" * max(1, 54 - len(name)) + "\n\n")
        parts.append(module_body(name))
    page = PAGE.read_text(encoding="utf-8")
    parts.append("\n\n# ==== the page, embedded ==============================\n\n")
    parts.append("EMBEDDED_PAGE = " + repr(page) + "\n")
    vendor = {
        name: (VENDOR / name).read_text(encoding="utf-8") for name in VENDOR_FILES
    }
    parts.append("\n# ==== scripts the page loads from the server, embedded ====\n\n")
    parts.append("EMBEDDED_VENDOR = " + repr(vendor) + "\n")
    parts.append(FOOTER)
    return "".join(parts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="dist/karaoke_os.py")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"built {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

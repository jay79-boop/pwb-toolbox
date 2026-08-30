"""HTTP shell for the random singer rotation: one command, one address.

    python -m tools.karaoke_server.queue_server

Run it on a machine on the venue's Wi-Fi (same rule as the leaderboard:
a cloud session cannot host this -- see the README). It prints one
address. The big screen opens ``/screen`` on that address; everyone else
scans the QR the screen shows and lands on the phone page. That is the
whole setup.

Threading server, so a lock serializes every touch of the room. Real time
enters here and only here.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .room import QueueRoom, RotationError

MAX_BODY = 8 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "static" / "karaoke-queue.html"

HEAD = (
    '<!doctype html>\n<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="karaoke-queue" content="/api">\n'
    '<meta name="karaoke-role" content="{role}">\n'
)

POSTS = {
    "/api/join": "join",
    "/api/song": "song",
    "/api/here": "here",
    "/api/back": "back",
    "/api/leave": "leave",
    "/api/retime": "retime",
}


def page_html(role: str, source: str | None = None) -> str:
    """The queue page as a document, told its role and where the API is."""
    html = source if source is not None else PAGE.read_text(encoding="utf-8")
    head = HEAD.format(role=role)
    marker = "</style>"
    cut = html.find(marker)
    if cut == -1:
        return head + "</head>\n<body>\n" + html + "\n</body>\n</html>\n"
    cut += len(marker)
    return (
        head + html[:cut] + "\n</head>\n<body>\n" + html[cut:] + "\n</body>\n</html>\n"
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "KaraokeQueue/1.0"
    room: QueueRoom = None
    lock: threading.Lock = None

    def log_message(self, fmt, *args):
        if os.environ.get("KARAOKE_QUIET"):
            return
        super().log_message(fmt, *args)

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path in ("/", "/index.html", "/screen"):
            if not PAGE.exists():
                self._html(404, "<h1>karaoke-queue.html not found</h1>")
                return
            role = "screen" if route.path == "/screen" else "phone"
            self._html(200, page_html(role))
        elif route.path == "/api/state":
            query = parse_qs(route.query)
            singer_id = query.get("singer_id", [None])[0]
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            with self.lock:
                state = self.room.state(time.time(), singer_id, since)
            self._json(200, state)
        elif route.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif route.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        method = POSTS.get(urlparse(self.path).path)
        if method is None:
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": "body must be 1..%d bytes" % MAX_BODY})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "body must be a JSON object"})
            return
        try:
            with self.lock:
                out = getattr(self.room, method)(payload, time.time())
        except RotationError as err:
            self._json(400, {"error": str(err)})
            return
        self._json(200, out)


def build(profiles_path=None):
    """A handler class bound to one room -- handy for tests."""
    return type(
        "BoundHandler",
        (Handler,),
        {"room": QueueRoom(profiles_path), "lock": threading.Lock()},
    )


def lan_address() -> str | None:
    """The address phones on the same Wi-Fi can actually reach."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # no packets sent; routing only
            return probe.getsockname()[0]
    except OSError:
        return None


def serve(host="0.0.0.0", port=8772, profiles_path=None):
    profiles_path = profiles_path or os.environ.get(
        "KARAOKE_PROFILES", "karaoke-profiles.json"
    )
    httpd = ThreadingHTTPServer((host, port), build(profiles_path))
    lan = lan_address()
    shown = lan or ("localhost" if host in ("0.0.0.0", "") else host)
    print(f"Karaoke queue on http://{shown}:{port}  (singer memory in {profiles_path})")
    print(f"Big screen: open http://{shown}:{port}/screen and scan the QR to join.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--profiles", default=None, help="singer memory JSON path")
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.profiles)


if __name__ == "__main__":
    main()

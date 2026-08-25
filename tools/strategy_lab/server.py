"""HTTP shell for the Strategy Lab.

Serves the dashboard and the run API from one origin, so a browser open on the
page and a session posting a backtest are talking to the same place with no CORS
in the way. Standard library only.

The page is told it has a live backend by a meta tag injected here. Opened
straight from disk — or published as an Artifact — the same file finds no tag and
renders whatever data was baked into it instead of firing doomed requests at
nothing. That is the arrangement `tools/karaoke_server` uses, for the same reason.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .store import RunStore, ValidationError

MAX_BODY = 8 * 1024 * 1024  # a ten-year 15m backtest is a few hundred KB
REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "static" / "strategy-lab.html"
STATS = REPO_ROOT / "static" / "strategy-lab-stats.js"

LIVE_META = '<meta name="strategy-lab-api" content="/api/runs">'

BASE_HEAD = (
    '<!doctype html>\n<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="color-scheme" content="light dark">\n'
)
HEAD = BASE_HEAD + LIVE_META + "\n"

DATA_TAG = '<script id="strategy-lab-data" type="application/json">'


def page_html(
    source: str | None = None,
    stats_source: str | None = None,
    runs: list | None = None,
    live: bool = True,
) -> str:
    """The dashboard as a real document.

    The page is authored as an artifact fragment — title, styles, markup, with no
    `<html>` around it — so the identical file can be published as an Artifact.
    Served raw it would land in quirks mode, so head and body are split at the
    stylesheet and wrapped here.

    `live` injects the meta tag that tells the page it has an API to poll; a
    standalone export leaves it out so the page renders its baked-in `runs`
    instead of firing requests that cannot succeed.

    The stats module is inlined at the placeholder rather than linked, so the
    served page and the published Artifact carry the same single copy of the
    arithmetic.
    """
    html = source if source is not None else PAGE.read_text(encoding="utf-8")
    stats = (
        stats_source if stats_source is not None else STATS.read_text(encoding="utf-8")
    )
    html = html.replace("/*__STRATEGY_LAB_STATS__*/", stats)

    if runs is not None:
        start = html.index(DATA_TAG) + len(DATA_TAG)
        end = html.index("</script>", start)
        # `</script>` inside the JSON would close the block early; the escape is
        # invisible to JSON.parse and keeps the parser from ending the tag.
        payload = json.dumps({"runs": runs}).replace("</", "<\\/")
        html = html[:start] + payload + html[end:]

    head = HEAD if live else BASE_HEAD
    marker = "</style>"
    cut = html.find(marker)
    if cut == -1:
        return head + "</head>\n<body>\n" + html + "\n</body>\n</html>\n"
    cut += len(marker)
    return (
        head + html[:cut] + "\n</head>\n<body>\n" + html[cut:] + "\n</body>\n</html>\n"
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "StrategyLab/1.0"
    store: RunStore = None  # type: ignore[assignment]
    origin = "*"

    def log_message(self, fmt, *args):  # pragma: no cover - noise control
        if os.environ.get("STRATEGY_LAB_QUIET"):
            return
        super().log_message(fmt, *args)

    # ---- helpers ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValidationError("empty body")
        if length > MAX_BODY:
            raise ValidationError("body too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- routes ----
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/":
            self._html(200, page_html())
        elif path == "/api/runs":
            self._json(200, {"runs": self.store.index()})
        elif path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/") :]
            record = self.store.get(run_id)
            if record is None:
                self._json(404, {"error": "no such run"})
            else:
                self._json(200, record)
        elif path == "/api/health":
            self._json(200, {"ok": True, "runs": len(self.store.ids())})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path.rstrip("/") != "/api/runs":
            self._json(404, {"error": "not found"})
            return
        try:
            record = self.store.save(self._body())
        except ValidationError as exc:
            self._json(400, {"error": str(exc)})
        except json.JSONDecodeError:
            self._json(400, {"error": "body was not valid JSON"})
        else:
            self._json(201, {"id": record["id"], "trades": len(record["trades"])})

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if not path.startswith("/api/runs/"):
            self._json(404, {"error": "not found"})
            return
        try:
            removed = self.store.delete(path[len("/api/runs/") :])
        except ValidationError as exc:
            self._json(400, {"error": str(exc)})
        else:
            self._json(200 if removed else 404, {"deleted": removed})


def build_server(
    store: RunStore, host: str = "127.0.0.1", port: int = 8771
) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"store": store})
    return ThreadingHTTPServer((host, port), handler)

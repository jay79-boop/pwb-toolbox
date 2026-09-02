"""HTTP shell for the random singer rotation: one command, one address.

    python -m tools.karaoke_server.queue_server

Run it on a machine on the venue's Wi-Fi (same rule as the leaderboard:
a cloud session cannot host this -- see the README). It prints one
address. The big screen opens ``/screen`` on that address; everyone else
scans the QR the screen shows and lands on the phone page. That is the
whole setup.

Threading server, so a lock serializes every touch of the room. Real time
enters here and only here -- and so does the one outbound request the
whole system makes: a pasted YouTube link is turned into a song title via
YouTube's oEmbed endpoint, from this module and never from the engine or
the room, with the raw link kept when that fails for any reason at all.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import urllib.request
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .room import QueueRoom, RotationError

MAX_BODY = 8 * 1024

# A pasted link is shown as the video's title, not the URL. The title comes
# from YouTube's oEmbed endpoint, which needs no key and no login; the raw
# link is kept when the answer is anything but a clean one.
OEMBED_URL = (
    "https://www.youtube.com/oembed?url="
    "https://www.youtube.com/watch?v={video_id}&format=json"
)
OEMBED_TIMEOUT_S = 2.0
OEMBED_MAX_BYTES = 64 * 1024


def youtube_title(video_id: str, timeout: float = OEMBED_TIMEOUT_S) -> str | None:
    """The video's title, or None for any failure: offline, slow, odd."""
    try:
        url = OEMBED_URL.format(video_id=quote(video_id, safe=""))
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read(OEMBED_MAX_BYTES).decode("utf-8"))
    except Exception:  # noqa: BLE001 -- a venue with no uplink must not care why
        return None
    title = data.get("title") if isinstance(data, dict) else None
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


def resolve_title(payload: dict, lookup, cache: dict) -> dict:
    """Swap a link's raw title for the video's, when the lookup can say.

    Only a ``link`` with a ``ref`` is looked up; one fetch per video id for
    the life of the process, misses included, so a room with no internet
    pays the timeout once per link rather than once per poll. The payload
    is returned unchanged in every other case -- the room never learns a
    lookup happened.
    """
    if payload.get("source") != "link":
        return payload
    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref:
        return payload
    if ref not in cache:
        try:
            title = lookup(ref)
        except Exception:  # noqa: BLE001 -- same rule: the link is the fallback
            title = None
        cache[ref] = title if isinstance(title, str) and title.strip() else None
    if cache[ref]:
        payload = dict(payload, title=cache[ref])
    return payload


def _repo_page() -> Path | None:
    """The page in the repo checkout, or None when there isn't one.

    This module is concatenated verbatim into the standalone karaoke_os.py,
    which can legitimately sit at C:\\karaoke\\ or a USB-stick root. A bare
    ``parents[2]`` raises IndexError there -- at import, before any
    friendly message or the embedded fallback could run.
    """
    here = Path(__file__).resolve()
    if len(here.parents) < 3:
        return None
    return here.parents[2] / "static" / "karaoke-queue.html"


PAGE = _repo_page()

# The standalone build (tools/karaoke_server/build_standalone.py) rebinds
# this to the page's full text, so the single file needs no static/ dir.
EMBEDDED_PAGE = None

# Scripts the page loads from this server rather than from a CDN. On a
# captive portal a CDN <script> "loads" a login page instead of the library;
# served from here, the file is on the same LAN as the phone that asked.
# Only these names are served: the route is not a directory listing.
VENDOR_FILES = ("amplitude-unified.umd.js",)


def _repo_vendor() -> Path | None:
    here = Path(__file__).resolve()
    if len(here.parents) < 3:
        return None
    return here.parents[2] / "static" / "vendor"


VENDOR = _repo_vendor()

# The standalone build rebinds this to {name: text} for every VENDOR_FILES
# entry, the same way it rebinds EMBEDDED_PAGE.
EMBEDDED_VENDOR: dict = {}


def vendor_name(path: str) -> str | None:
    """The vendored file a GET path asks for, or None if it is not one."""
    prefix = "/vendor/"
    if not path.startswith(prefix):
        return None
    name = path[len(prefix) :]
    return name if name in VENDOR_FILES else None


def vendor_source(name: str) -> str | None:
    if name not in VENDOR_FILES:
        return None
    if name in EMBEDDED_VENDOR:
        return EMBEDDED_VENDOR[name]
    if VENDOR is not None and (VENDOR / name).exists():
        return (VENDOR / name).read_text(encoding="utf-8")
    return None


def page_source() -> str | None:
    # embedded first: the standalone must never consult a disk path that
    # belongs to whatever happens to sit three levels above it
    if EMBEDDED_PAGE is not None:
        return EMBEDDED_PAGE
    if PAGE is not None and PAGE.exists():
        return PAGE.read_text(encoding="utf-8")
    return None


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
    # the host desk on the big screen
    "/api/host/add": "host_add",
    "/api/host/skip": "host_skip",
    "/api/host/end": "host_end",
}

# the two routes that can carry a pasted link
TITLED = ("song", "host_add")


def page_html(role: str, source: str | None = None, join_url: str | None = None) -> str:
    """The queue page as a document, told its role, the API, and the join URL.

    The join URL is the one the screen turns into a QR code. It comes from
    here rather than from the browser's ``location.origin`` because only
    this process knows which address phones can actually reach: a screen
    opened at localhost would otherwise print a QR every phone in the room
    fails to resolve, which is the entire product silently broken.
    """
    html = source if source is not None else page_source()
    head = HEAD.format(role=role)
    if join_url:
        head += '<meta name="karaoke-join" content="%s">\n' % escape(
            join_url, quote=True
        )
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
    join_url: str | None = None
    # injectable so the suite never reaches YouTube; build() rebinds both
    title_lookup = staticmethod(youtube_title)
    title_cache: dict = {}

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

    def _script(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
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
            if page_source() is None:
                self._html(404, "<h1>karaoke-queue.html not found</h1>")
                return
            role = "screen" if route.path == "/screen" else "phone"
            self._html(200, page_html(role, join_url=self.join_url))
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
        elif vendor_name(route.path):
            source = vendor_source(vendor_name(route.path))
            if source is None:
                self._json(404, {"error": "not found"})
            else:
                self._script(200, source)
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
        if method in TITLED:
            # outside the lock: a slow uplink must not stall every poll
            payload = resolve_title(payload, self.title_lookup, self.title_cache)
        try:
            with self.lock:
                out = getattr(self.room, method)(payload, time.time())
        except RotationError as err:
            self._json(400, {"error": str(err)})
            return
        self._json(200, out)


def build(profiles_path=None, join_url=None, title_lookup=None):
    """A handler class bound to one room -- handy for tests.

    ``title_lookup`` replaces the YouTube oEmbed fetch; the suite passes a
    fake so no test ever reaches the network.
    """
    return type(
        "BoundHandler",
        (Handler,),
        {
            "room": QueueRoom(profiles_path),
            "lock": threading.Lock(),
            "join_url": join_url,
            "title_lookup": staticmethod(title_lookup or youtube_title),
            "title_cache": {},
        },
    )


def _default_route_address() -> str | None:
    """Whichever interface the OS would send internet traffic out of.

    Alone this is the wrong question, and it answered wrongly on the
    owner's machine: a VPN was up, so the route to the internet ran
    through a tunnel at 10.5.0.2 and the QR published an address no
    phone in the room could resolve. Kept as one candidate among
    several, never as the answer.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # no packets sent; routing only
            return probe.getsockname()[0]
    except OSError:
        return None


def _host_addresses() -> list[str]:
    """Every IPv4 this machine answers to, as far as name resolution knows."""
    found = []
    for family, _, _, _, sockaddr in _getaddrinfo_safe():
        if family == socket.AF_INET and sockaddr[0] not in found:
            found.append(sockaddr[0])
    return found


def _getaddrinfo_safe():
    try:
        return socket.getaddrinfo(socket.gethostname(), None)
    except (OSError, UnicodeError):
        return []


def address_rank(address: str) -> int:
    """Lower sorts first. How likely is this the Wi-Fi phones are on?

    A venue or a house hands out 192.168.x.x almost without exception,
    so that wins. 172.16-31 is the next most likely private range. A
    10.x address is real too, but it is also what every VPN client and
    container bridge helps itself to, so it sorts last of the private
    ranges -- the case that actually bit.
    """
    parts = address.split(".")
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return 90
    if len(octets) != 4:
        return 90
    if octets[0] == 127:
        return 99  # loopback: reaches nothing but this machine
    if octets[0] == 169 and octets[1] == 254:
        return 98  # link-local: the adapter never got an address
    if octets[0] == 192 and octets[1] == 168:
        return 0
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return 1
    if octets[0] == 10:
        return 2
    return 50  # a public address: unusual here, but it is reachable


def lan_addresses() -> list[str]:
    """Every address a phone might reach this machine on, best guess first.

    Ranked rather than chosen, because no rule gets this right on every
    machine: the list is printed so a wrong guess costs the owner one
    glance instead of a support round trip.
    """
    found = []
    for address in [_default_route_address()] + _host_addresses():
        if address and address not in found:
            found.append(address)
    return sorted(found, key=lambda a: (address_rank(a), a))


def lan_address() -> str | None:
    """The single best guess at the address phones can reach."""
    ranked = lan_addresses()
    return ranked[0] if ranked else None


# The inbound rule the launcher (start_karaoke.ps1) looks for and, when it is
# running elevated, offers to create. Named rather than matched by port so a
# non-coder can find it again in Windows Defender Firewall, and so the check
# and the fix can never drift: both sides import these two from here.
FIREWALL_RULE_NAME = "Karaoke Queue"


def firewall_command(port: int = 8772) -> str:
    """The exact one-line PowerShell that lets phones reach this machine.

    Printed, never run from here. A server process that silently reconfigured
    the host firewall would be a worse thing to ship than a room that cannot
    join, and elevation belongs to whoever opened the window.
    """
    return (
        "New-NetFirewallRule -DisplayName '%s' -Direction Inbound "
        "-Action Allow -Protocol TCP -LocalPort %d -Profile Any"
        % (FIREWALL_RULE_NAME, port)
    )


def port_in_use_message(port: int) -> str:
    """What a busy port means to the person who double-clicked the icon.

    Not "OSError: [WinError 10048]". The cause is almost always the last
    karaoke window still open behind this one, and that is a sentence, not a
    stack trace. 2026-09-02: one of the four things that went wrong in a
    single sitting.
    """
    return (
        "Karaoke is already running -- close the other karaoke window first.\n"
        "(Something on this machine is already using port %d. If you are sure "
        "karaoke is not open, restart the computer or start it on another "
        "port with --port %d.)" % (port, port + 1)
    )


def serve(host="0.0.0.0", port=8772, profiles_path=None):
    profiles_path = profiles_path or os.environ.get(
        "KARAOKE_PROFILES", "karaoke-profiles.json"
    )
    bound = host not in ("0.0.0.0", "")
    ranked = lan_addresses()
    shown = host if bound else (ranked[0] if ranked else "localhost")
    try:
        httpd = ThreadingHTTPServer(
            (host, port), build(profiles_path, f"http://{shown}:{port}/")
        )
    except OSError:
        # Almost always EADDRINUSE, and there is nothing useful to tell apart:
        # every way this fails means "this program cannot have that port", and
        # the reply that helps is the same one.
        print(port_in_use_message(port))
        return 1
    print(f"Karaoke queue on http://{shown}:{port}  (singer memory in {profiles_path})")
    print(f"Big screen: open http://{shown}:{port}/screen and scan the QR to join.")
    others = [a for a in ranked if a != shown]
    if others and not bound:
        # The guess is a guess. A machine with a VPN up, a container
        # bridge, or two network cards has several, and only one of them
        # is the Wi-Fi the phones are on -- so print them all rather than
        # make anyone go and ask the operating system.
        print("")
        print("If phones cannot reach that address, this machine also answers to:")
        for address in others:
            print(f"    http://{address}:{port}/screen")
        print(
            f"Pick the one starting 192.168 if there is one, then restart with "
            f"--host <that address> so the QR publishes it."
        )
        print("")
    print(
        "If phones cannot connect: allow this app through the firewall for "
        "BOTH private and public networks (venue Wi-Fi usually counts as public)."
    )
    print("Run this once, in a PowerShell window opened as administrator:")
    print("    " + firewall_command(port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--profiles", default=None, help="singer memory JSON path")
    parser.add_argument(
        "--print-address",
        action="store_true",
        help="print the address phones should use, then exit",
    )
    args = parser.parse_args(argv)
    if args.print_address:
        # The launcher asks this rather than ranking addresses itself. Two
        # implementations of "which address can phones reach" is how the VPN
        # bug of 2026-09-02 comes back on a path no test covers.
        bound = args.host not in ("0.0.0.0", "")
        print(args.host if bound else (lan_address() or "localhost"))
        return 0
    return serve(args.host, args.port, args.profiles)


if __name__ == "__main__":
    raise SystemExit(main())

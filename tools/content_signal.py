#!/usr/bin/env python3
"""The content bridge: what got made, what got posted, and which half we can see.

``docs/awareness.md`` recorded content as blind because "content needs
credentials that are not connected". On 2026-09-02 that was checked and is no
longer true: Blotato answers with an active subscription and one connected
TikTok account, and Windsor.ai answers as the owner on a Trial plan with
``tiktok_organic`` connected. The blind spot moved rather than closed -- the
credentials are live, but they live in an **MCP connector inside a Claude
session**, and no Python process here can call one.

So the shape is the desk's, for the same reason and with a difference that
matters. The content pipeline has two halves that are reachable from different
places:

* ``render/`` -- the market-close script and its segments, produced by
  ``tools/market_close``. On the owner's machine, gitignored.
* the platforms -- Blotato (publishing, an active paid plan) and Windsor.ai
  (``tiktok_organic`` analytics, on an unpaid trial). Reachable from any session
  holding those connectors, and from nowhere else.

**Each half carries its own timestamp**, because they are captured by different
things at different moments and one stale half must never make the other look
stale, or fresh. A signal whose platform half is a week old and whose render
half is an hour old is two facts, and it is reported as two.

Redaction is the desk module's, by the same schema-enforced route: integers,
floats, booleans, ISO dates and closed vocabularies only. No caption, no video
title, no account handle, no filename. There is nothing to scrub because there is
nowhere for free text to go.

**The trial is a first-class fact.** Windsor.ai reports ``is_paid: false``, and a
trial that lapses turns a working adapter into a silent one -- an analytics
source reporting nothing looks exactly like a channel nobody watched. So the plan
is carried in the signal and a lapsed one is an observation, not a surprise.

Usage::

    python tools/content_signal.py capture --platform-json -   # session pipes MCP reads in
    python tools/content_signal.py capture                     # render half only
    python tools/content_signal.py show

The payload ``capture`` accepts on stdin, all keys optional::

    {"blotato": {"subscription": "active", "accounts": 1,
                 "last_post": "2026-09-01T18:00:00Z", "posts_7d": 3},
     "windsor":  {"is_paid": false, "connectors": 1, "accounts": 1}}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from dataclasses import dataclass, asdict, fields

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SIGNAL_DIR = REPO_ROOT / "signals"
DEFAULT_SIGNAL = SIGNAL_DIR / "content.json"
DEFAULT_RENDER = REPO_ROOT / "render"

SUBSCRIPTIONS = ("active", "inactive", "unknown")
PLANS = ("paid", "trial", "unknown")

# Whether the account that PUBLISHES and the account that is MEASURED are the
# same channel. Tri-state, and `unknown` is the default that must be earned out
# of: a publishing handle and an analytics account *name* are different kinds of
# string, so two that differ are not evidence of two channels. This module never
# guesses it -- the verdict is supplied by whoever read both, and validated here.
CHANNEL_MATCHES = ("same", "different", "unknown")

SCHEMA: dict[str, str] = {
    "taken": "stamp",
    "render_taken": "stamp?",
    "render_age_hours": "float",
    "render_segments": "int",
    "render_has_description": "bool",
    "platform_taken": "stamp?",
    "publish_subscription": "subscription",
    "publish_accounts": "int",
    "publish_last_post_days": "float",
    "publish_posts_7d": "int",
    "analytics_plan": "plan",
    "analytics_connectors": "int",
    "analytics_accounts": "int",
    "channel_match": "channel_match",
}


class Unpublishable(ValueError):
    """A value that would leak. Raised before anything is written."""


@dataclass(frozen=True)
class ContentSignal:
    """One redacted reading of the content pipeline, in two independently
    stamped halves. ``None`` is *could not be read*, never zero."""

    taken: str = ""
    render_taken: str = ""
    render_age_hours: float | None = None
    render_segments: int | None = None
    render_has_description: bool | None = None
    platform_taken: str = ""
    publish_subscription: str = "unknown"
    publish_accounts: int | None = None
    publish_last_post_days: float | None = None
    publish_posts_7d: int | None = None
    analytics_plan: str = "unknown"
    analytics_connectors: int | None = None
    analytics_accounts: int | None = None
    channel_match: str = "unknown"

    @property
    def render_seen(self) -> bool:
        return bool(self.render_taken)

    @property
    def platform_seen(self) -> bool:
        return bool(self.platform_taken)

    def as_dict(self) -> dict:
        payload = asdict(self)
        validate(payload)
        return payload

    @staticmethod
    def from_dict(raw: dict) -> "ContentSignal":
        known = {f.name for f in fields(ContentSignal)}
        return ContentSignal(**{k: v for k, v in raw.items() if k in known})


def _is_stamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate(payload: dict) -> dict:
    """Reject anything the schema does not allow, before it can be written."""
    unknown = sorted(set(payload) - set(SCHEMA))
    if unknown:
        raise Unpublishable(f"field(s) not in the schema: {', '.join(unknown)}")
    for key, kind in SCHEMA.items():
        value = payload.get(key)
        if kind == "stamp":
            if not _is_stamp(value):
                raise Unpublishable(f"{key} must be an ISO timestamp, got {value!r}")
        elif kind == "stamp?":
            if value not in ("", None) and not _is_stamp(value):
                raise Unpublishable(
                    f"{key} must be an ISO timestamp or empty, got {value!r}"
                )
        elif kind == "subscription":
            if value not in SUBSCRIPTIONS:
                raise Unpublishable(
                    f"{key} must be one of {SUBSCRIPTIONS}, got {value!r}"
                )
        elif kind == "plan":
            if value not in PLANS:
                raise Unpublishable(f"{key} must be one of {PLANS}, got {value!r}")
        elif kind == "channel_match":
            if value not in CHANNEL_MATCHES:
                raise Unpublishable(
                    f"{key} must be one of {CHANNEL_MATCHES}, got {value!r}"
                )
        elif kind == "int":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise Unpublishable(f"{key} must be an int or null, got {value!r}")
        elif kind == "bool":
            if value is not None and not isinstance(value, bool):
                raise Unpublishable(f"{key} must be a bool or null, got {value!r}")
        elif kind == "float":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise Unpublishable(f"{key} must be a number or null, got {value!r}")
    return payload


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def reduce_platform(payload: dict | None, now: dt.datetime) -> dict:
    """Reduce an MCP read to publishable fields. Anything unrecognised is dropped.

    Dropped, not coerced: a subscription string the emitter has never seen
    becomes ``unknown`` rather than being mapped to the nearest thing that looks
    like it, because guessing an active plan out of an unfamiliar word is exactly
    the kind of quiet repair the rest of this repository refuses.
    """
    if not isinstance(payload, dict):
        return {}
    blotato = payload.get("blotato") if isinstance(payload.get("blotato"), dict) else {}
    windsor = payload.get("windsor") if isinstance(payload.get("windsor"), dict) else {}

    subscription = blotato.get("subscription")
    subscription = subscription if subscription in SUBSCRIPTIONS else "unknown"

    is_paid = windsor.get("is_paid")
    if is_paid is True:
        plan = "paid"
    elif is_paid is False:
        plan = "trial"
    else:
        plan = "unknown"

    last_post_days = None
    raw_post = blotato.get("last_post")
    if isinstance(raw_post, str):
        try:
            stamp = dt.datetime.fromisoformat(raw_post.replace("Z", "+00:00"))
        except ValueError:
            stamp = None
        if stamp is not None:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            last_post_days = round((now - stamp).total_seconds() / 86400.0, 2)

    # Taken, never derived. A handle and an account label are different kinds of
    # string and comparing them would manufacture mismatches; the session that
    # read both is the thing that can tell, so it says, and this validates.
    match = payload.get("channel_match")
    match = match if match in CHANNEL_MATCHES else "unknown"

    return {
        "platform_taken": now.isoformat(),
        "channel_match": match,
        "publish_subscription": subscription,
        "publish_accounts": _int(blotato.get("accounts")),
        "publish_last_post_days": last_post_days,
        "publish_posts_7d": _int(blotato.get("posts_7d")),
        "analytics_plan": plan,
        "analytics_connectors": _int(windsor.get("connectors")),
        "analytics_accounts": _int(windsor.get("accounts")),
    }


def derive(
    render: dict | None, platform: dict | None, now: dt.datetime
) -> ContentSignal:
    """Merge the two halves. Pure: the caller does the reading."""
    payload: dict = {"taken": now.isoformat()}
    if render:
        payload.update(render)
    if platform:
        payload.update(platform)
    signal = ContentSignal(**{k: v for k, v in payload.items() if k in SCHEMA})
    validate(asdict(signal))
    return signal


# ---------------------------------------------------------------------------
# The dirty edge
# ---------------------------------------------------------------------------


def read_render(directory: pathlib.Path, now: dt.datetime) -> dict | None:
    """The render folder's shape and age. Never its text.

    ``render/`` holds the spoken script for the day's broadcast, which is the
    owner's writing. Only the file count, the newest mtime and whether a
    description was produced cross this line.
    """
    if not directory.is_dir():
        return None
    segments = 0
    newest: float | None = None
    has_description = False
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
        if path.name.startswith("description"):
            has_description = True
        elif path.suffix.lower() == ".txt":
            segments += 1
    age = None
    if newest is not None:
        stamp = dt.datetime.fromtimestamp(newest, dt.timezone.utc)
        age = round((now - stamp).total_seconds() / 3600.0, 2)
    return {
        "render_taken": now.isoformat(),
        "render_age_hours": age,
        "render_segments": segments,
        "render_has_description": has_description,
    }


def load_signal(path: pathlib.Path) -> ContentSignal | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        validate({k: v for k, v in raw.items() if k in SCHEMA})
    except Unpublishable:
        return None
    return ContentSignal.from_dict(raw)


def write_signal(signal: ContentSignal, path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(signal.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")

    cap = sub.add_parser("capture", help="write the content signal")
    cap.add_argument("--out", default=str(DEFAULT_SIGNAL))
    cap.add_argument("--render", default=str(DEFAULT_RENDER))
    cap.add_argument(
        "--platform-json",
        default="",
        help="file holding the MCP read, or - for stdin. Omitted: render half only.",
    )
    cap.add_argument(
        "--keep-platform",
        action="store_true",
        help="carry the existing signal's platform half forward, with its own stamp",
    )
    cap.add_argument("--dry-run", action="store_true")

    show = sub.add_parser("show", help="what the committed signal says, per half")
    show.add_argument("--signal", default=str(DEFAULT_SIGNAL))

    args = parser.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)

    if args.command == "show" or args.command is None:
        path = pathlib.Path(getattr(args, "signal", DEFAULT_SIGNAL))
        signal = load_signal(path)
        if signal is None:
            print(f"No readable content signal at {path}.")
            return 1
        print(json.dumps(signal.as_dict(), indent=2, sort_keys=True))
        print(
            f"\nrender half: {'seen' if signal.render_seen else 'NOT CAPTURED'};"
            f" platform half: {'seen' if signal.platform_seen else 'NOT CAPTURED'}"
        )
        return 0

    platform = None
    if args.platform_json == "-":
        platform = reduce_platform(json.loads(sys.stdin.read() or "{}"), now)
    elif args.platform_json:
        text = pathlib.Path(args.platform_json).read_text(encoding="utf-8")
        platform = reduce_platform(json.loads(text or "{}"), now)
    elif args.keep_platform:
        prior = load_signal(pathlib.Path(args.out))
        if prior is not None and prior.platform_seen:
            platform = {
                k: getattr(prior, k)
                for k in SCHEMA
                if k.startswith(("publish_", "analytics_")) or k == "platform_taken"
            }

    signal = derive(read_render(pathlib.Path(args.render), now), platform, now)
    if args.dry_run:
        print(json.dumps(signal.as_dict(), indent=2, sort_keys=True))
        return 0
    out = write_signal(signal, pathlib.Path(args.out))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

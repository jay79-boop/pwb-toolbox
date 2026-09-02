"""One karaoke room, ready to sit behind HTTP: the no-brainer layer.

Wraps a Rotation in plain-dict requests and responses so the server above
is a shell and the suite below never opens a socket. Adds the three things
an engine cannot own: real time (every method takes ``now`` so tests can
lie about it), singer memory on disk (profiles saved atomically on every
change), and an event feed with sequence numbers so the screen can narrate
the night and a phone can notice "YOU'RE UP" without missing it between
polls.

What a poll deliberately does not reveal: the queue order. The waiting
list comes back alphabetical, because nobody knowing who is next is the
product. The only ordering that ever leaves this module is the call
itself.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from collections import deque

from .rotation import (
    Rotation,
    RotationConfig,
    RotationError,
)

EVENTS_KEPT = 300


class QueueRoom:
    def __init__(
        self,
        profiles_path: str | None = None,
        config: RotationConfig | None = None,
        rng: random.Random | None = None,
    ):
        self.profiles_path = profiles_path
        profiles = {}
        if profiles_path and os.path.exists(profiles_path):
            try:
                with open(profiles_path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    profiles = loaded
            except (OSError, ValueError):
                # refuse rather than repair: an unreadable memory file means
                # the night starts fresh, not that we guess at its contents
                profiles = {}
        self.rot = Rotation(config, rng=rng, profiles=profiles)
        self.events: deque = deque(maxlen=EVENTS_KEPT)
        self.seq = 0

    # ---- plumbing ----

    def _absorb(self, events) -> None:
        for event in events:
            self.seq += 1
            self.events.append(
                {
                    "seq": self.seq,
                    "kind": event.kind,
                    "at": event.at,
                    "singer_id": event.singer_id,
                    "name": (
                        self.rot.singers[event.singer_id].name
                        if event.singer_id in self.rot.singers
                        else None
                    ),
                    "detail": event.detail,
                }
            )
        if events:
            self._save_profiles()

    def _save_profiles(self) -> None:
        if not self.profiles_path:
            return
        directory = os.path.dirname(os.path.abspath(self.profiles_path))
        tmp = None
        try:
            # mkstemp itself raises on a read-only folder (an exe dropped in
            # Program Files), and that must cost the night its memory, not
            # its ability to run: singers still sing, nothing is remembered
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.rot.profiles, fh)
            os.replace(tmp, self.profiles_path)
        except OSError:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def tick(self, now: float) -> None:
        self._absorb(self.rot.tick(now))

    # ---- the API, one dict in and one dict out ----

    def join(self, payload: dict, now: float) -> dict:
        singer = self.rot.join(payload.get("name"), now)
        self._save_profiles()
        past = self.rot.profiles.get(singer.key, {}).get("songs", [])
        return {
            "singer_id": singer.id,
            "name": singer.name,
            "returning": singer.returning,
            # "your usual?" -- what this name sang here before, latest first
            "past_songs": list(reversed(past[-8:])),
        }

    def song(self, payload: dict, now: float) -> dict:
        song = self.rot.set_song(
            payload.get("singer_id", ""),
            payload.get("title"),
            duration_s=payload.get("duration_s"),
            source=payload.get("source", "title"),
            ref=payload.get("ref"),
            now=now,
        )
        self._save_profiles()
        return {"ok": True, "title": song.title}

    def here(self, payload: dict, now: float) -> dict:
        self._absorb(self.rot.appeared(payload.get("singer_id", ""), now))
        return {"ok": True}

    def back(self, payload: dict, now: float) -> dict:
        self.rot.mark_back(payload.get("singer_id", ""), now)
        return {"ok": True}

    def leave(self, payload: dict, now: float) -> dict:
        self.rot.leave(payload.get("singer_id", ""), now)
        return {"ok": True}

    def retime(self, payload: dict, now: float) -> dict:
        self.rot.retime_stage(
            payload.get("singer_id", ""), now, payload.get("remaining_s")
        )
        return {"ok": True}

    # ---- the host desk: the screen's corner panel, never the phone ----

    def host_add(self, payload: dict, now: float) -> dict:
        """Sign a singer in from the desk, with a song when one was typed.

        A blank title means "joined, still choosing" -- the same state a
        phone sits in after I'M IN. A bad song refuses the whole add rather
        than leaving a ghost with no song in the room.
        """
        singer = self.rot.join(payload.get("name"), now)
        title = payload.get("title")
        song_title = None
        if isinstance(title, str) and title.strip():
            try:
                song = self.rot.set_song(
                    singer.id,
                    title,
                    duration_s=payload.get("duration_s"),
                    source=payload.get("source", "title"),
                    ref=payload.get("ref"),
                    now=now,
                )
            except RotationError:
                self.rot.leave(singer.id, now)
                raise
            song_title = song.title
        self._save_profiles()
        return {"singer_id": singer.id, "name": singer.name, "title": song_title}

    def host_skip(self, payload: dict, now: float) -> dict:
        """The called singer is not coming: strike them now and redraw."""
        skipped = self.rot.call.singer_id if self.rot.call else None
        self._absorb([self.rot.skip_call(now)])
        self.tick(now)  # the redraw happens in the same request
        return {"ok": True, "skipped": skipped}

    def host_end(self, payload: dict, now: float) -> dict:
        """The song is over whatever the player thinks: end it now."""
        if not self.rot.stage:
            raise RotationError("nobody is on stage")
        ended = self.rot.stage.singer_id
        self.rot.retime_stage(ended, now, 0)
        self.tick(now)
        return {"ok": True, "ended": ended}

    def state(self, now: float, singer_id: str | None = None, since: int = 0) -> dict:
        self.tick(now)
        rot = self.rot
        singing = None
        if rot.stage:
            on_stage = rot.singers[rot.stage.singer_id]
            singing = {
                "singer_id": on_stage.id,
                "name": on_stage.name,
                "title": on_stage.song.title if on_stage.song else "",
                "source": on_stage.song.source if on_stage.song else "title",
                "ref": on_stage.song.ref if on_stage.song else None,
                "ends_in_s": round(max(0.0, rot.stage.ends_at - now), 1),
            }
        called = None
        if rot.call:
            up = rot.singers[rot.call.singer_id]
            called = {
                "singer_id": up.id,
                "name": up.name,
                "title": up.song.title if up.song else "",
                "source": up.song.source if up.song else "title",
                "ref": up.song.ref if up.song else None,
                "appeared": rot.call.appeared_at is not None,
                # a solo call has no deadline: None, never inf (JSON)
                "deadline_in_s": (
                    None
                    if rot.call.solo
                    else round(max(0.0, rot.call.deadline - now), 1)
                ),
                "solo": rot.call.solo,
            }
        waiting = sorted(
            s.name
            for s in rot.singers.values()
            if s.state == "waiting" and s.song is not None
        )
        out = {
            "singing": singing,
            "called": called,
            "house_on": rot.house_on,
            "waiting": waiting,  # alphabetical on purpose: order is a secret
            "waiting_count": len(waiting),
            "seq": self.seq,
            "events": [e for e in self.events if e["seq"] > since],
        }
        if singer_id and singer_id in rot.singers:
            you = rot.singers[singer_id]
            out["you"] = {
                "state": you.state,
                "name": you.name,
                "song": you.song.title if you.song else None,
                "songs_sung": you.songs_sung,
                "called": bool(rot.call and rot.call.singer_id == singer_id),
                "solo": bool(
                    rot.call and rot.call.singer_id == singer_id and rot.call.solo
                ),
                "on_stage": bool(rot.stage and rot.stage.singer_id == singer_id),
                "needs_song": you.state == "waiting" and you.song is None,
            }
        return out


__all__ = ["QueueRoom", "RotationError"]

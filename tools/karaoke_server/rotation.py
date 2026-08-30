"""The random singer rotation: who is called to the stage next, and when.

This is the queue "operating system" for a karaoke room. People sign in with
a song already attached; the rotation calls singers in a weighted random
order, so nobody knows who is next -- but the randomness is bounded by
fairness rules so it can never starve anyone or let one voice hog the mic:

- **Ceiling.** Odds climb with every draw you lose, and past the
  ceiling you are simply taken next. In a small room the ceiling is the
  flat ``max_misses`` ("nobody waits more than four or five draws"); in a
  big room that flat promise is impossible -- one winner per draw means
  average misses equal the queue depth -- so the ceiling scales to
  ``ceiling_ratio`` times your fair share of the rotation. Either way,
  random stays fun only while it cannot leave someone waiting all night.
- **Cooldown.** Just sang, you sit out the next ``cooldown_draws`` draws
  (waived when nobody else is queued -- a one-person room still works).
- **Fewest songs first.** Each song you have already sung tonight cuts
  your odds by ``fewest_factor``, so a first-timer outranks a fourth-timer
  however the dice fall.
- **Newcomer boost.** Someone who walked in late starts with better odds
  than their zero history would give them, so the night does not calcify
  around the eight-o'clock crowd.

The call goes out ``lead`` seconds before the current song ends, so the
walk-up overlaps the outro and a no-show costs the room nothing -- the
rotation just draws again while the music is still playing. The lead adapts
to the room: more people present means a longer walk, and the observed
walk-up times (remembered per singer, across nights) feed both the lead and
each singer's grace timer. Miss your call ``max_no_shows`` times and you are
timed out (state AWAY) until you tell the desk you are back; stay away long
enough and the rotation concludes you left.

Pure core, dirty edge: no clock, no socket, no file. Time is a float passed
into every method, randomness is an injected ``random.Random``, and singer
memory is a plain dict the caller loads and saves. The suite drives whole
nights without sleeping once.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

MAX_NAME = 24
MAX_TITLE = 80
SOURCES = ("link", "search", "songbook", "title")

# Event kinds emitted by tick()/appeared(); the UI narrates these.
CALL = "call"  # a singer was drawn and announced
SONG_STARTED = "song_started"
SONG_ENDED = "song_ended"
NEEDS_SONG = "needs_song"  # the stay-and-sing-again prompt
NO_SHOW = "no_show"  # called, never appeared; back in the pool
TIMED_OUT = "timed_out"  # too many no-shows; AWAY until marked back
PRUNED = "pruned"  # away so long the rotation concluded they left

WAITING, ON_DECK, SINGING, AWAY, LEFT = (
    "waiting",
    "on_deck",
    "singing",
    "away",
    "left",
)


class RotationError(ValueError):
    """A request the rotation refuses. Carries a message safe to show."""


@dataclass
class RotationConfig:
    # fairness
    max_misses: int = 4  # small-room ceiling: guaranteed next past this
    ceiling_ratio: float = 1.5  # big-room ceiling: this x your fair-share wait
    miss_boost: float = 0.5  # each lost draw multiplies odds by 1 + this
    cooldown_draws: int = 2  # draws sat out after singing
    fewest_factor: float = 0.35  # odds multiplier per song already sung
    newcomer_boost: float = 1.6  # odds multiplier while newly arrived
    newcomer_window_s: float = 900.0
    # showing up
    max_no_shows: int = 2  # missed calls before you are timed out
    grace_base_s: float = 60.0  # walk-up allowance with no history
    grace_safety: float = 1.5  # allowance = observed walk-up EMA x this
    grace_min_s: float = 30.0
    grace_max_s: float = 180.0
    end_slack_s: float = 20.0  # extra allowance past the song's actual end
    away_prune_s: float = 1800.0  # away this long means they left
    # the call lead: how far before the song's end the next draw happens
    lead_min_s: float = 20.0
    lead_max_s: float = 150.0
    crowd_s_per_singer: float = 3.0  # busier room, longer walk to the stage
    walkup_alpha: float = 0.3  # EMA weight for a fresh walk-up observation
    # songs
    default_song_s: float = 240.0  # title-only mode: no media, assumed length
    min_song_s: float = 30.0
    max_song_s: float = 900.0
    profile_songs_kept: int = 50


@dataclass
class Song:
    title: str
    duration_s: float | None = None  # None = title-only; default applies
    source: str = "title"
    ref: str | None = None  # link / songbook id; opaque here


@dataclass
class Singer:
    id: str
    name: str
    key: str
    joined_at: float
    state: str = WAITING
    song: Song | None = None
    songs_sung: int = 0
    misses: int = 0
    cooldown_left: int = 0
    no_shows: int = 0
    eligible_since: float | None = None
    away_since: float | None = None
    walkup_ema: float | None = None
    returning: bool = False  # seen on a previous night


@dataclass
class Call:
    singer_id: str
    made_at: float
    deadline: float
    misses_at_call: int
    by: str  # "ceiling" or "lottery"
    appeared_at: float | None = None


@dataclass
class Stage:
    singer_id: str
    started_at: float
    ends_at: float


@dataclass
class Event:
    kind: str
    at: float
    singer_id: str | None = None
    detail: dict = field(default_factory=dict)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _clean_text(raw, what: str, limit: int) -> str:
    if not isinstance(raw, str):
        raise RotationError(f"{what} must be text")
    value = "".join(ch for ch in raw if ch >= " " and ch != "\x7f").strip()
    if not value:
        raise RotationError(f"{what} is required")
    return value[:limit]


class Rotation:
    """One room's rotation for one night."""

    def __init__(
        self,
        config: RotationConfig | None = None,
        rng: random.Random | None = None,
        profiles: dict | None = None,
    ):
        self.cfg = config or RotationConfig()
        self.rng = rng or random.Random()
        self.profiles = profiles if profiles is not None else {}
        self.singers: dict[str, Singer] = {}
        self.stage: Stage | None = None
        self.call: Call | None = None
        self.room_walkup_ema: float | None = None
        self._joined_tonight: set[str] = set()
        self._next_id = 1

    # -- signing in and choosing a song --------------------------------

    def join(self, name: str, now: float) -> Singer:
        name = _clean_text(name, "name", MAX_NAME)
        key = name.lower()
        singer = Singer(id=f"s{self._next_id}", name=name, key=key, joined_at=now)
        self._next_id += 1
        profile = self.profiles.get(key)
        if profile:
            singer.returning = True
            singer.walkup_ema = profile.get("walkup_ema")
        if key not in self._joined_tonight:
            self._joined_tonight.add(key)
            entry = self.profiles.setdefault(
                key, {"walkup_ema": None, "nights": 0, "no_shows": 0, "songs": []}
            )
            entry["nights"] = int(entry.get("nights", 0)) + 1
        self.singers[singer.id] = singer
        return singer

    def set_song(
        self,
        singer_id: str,
        title: str,
        duration_s: float | None = None,
        source: str = "title",
        ref: str | None = None,
        now: float = 0.0,
    ) -> Song:
        singer = self._singer(singer_id)
        if singer.state in (SINGING, ON_DECK):
            raise RotationError("cannot change the song mid-call")
        title = _clean_text(title, "title", MAX_TITLE)
        if source not in SOURCES:
            raise RotationError(f"source must be one of {', '.join(SOURCES)}")
        if duration_s is not None:
            if not isinstance(duration_s, (int, float)) or isinstance(duration_s, bool):
                raise RotationError("duration must be a number of seconds")
            # refuse rather than repair: a 4-second or 40-minute "song" is a
            # bad submission, not something to silently clamp into shape
            if not self.cfg.min_song_s <= duration_s <= self.cfg.max_song_s:
                raise RotationError(
                    "duration must be between "
                    f"{self.cfg.min_song_s:.0f} and {self.cfg.max_song_s:.0f} seconds"
                )
        song = Song(title=title, duration_s=duration_s, source=source, ref=ref)
        singer.song = song
        if singer.state == AWAY:
            self.mark_back(singer_id, now)
        self._refresh_eligible(singer, now)
        return song

    def mark_away(self, singer_id: str, now: float) -> None:
        singer = self._singer(singer_id)
        if singer.state in (SINGING, ON_DECK):
            raise RotationError("cannot step away mid-call")
        singer.state = AWAY
        singer.away_since = now
        singer.eligible_since = None

    def mark_back(self, singer_id: str, now: float) -> None:
        singer = self._singer(singer_id)
        if singer.state != AWAY:
            return
        singer.state = WAITING
        singer.away_since = None
        singer.no_shows = 0  # a fresh chance; the lifetime count is in the profile
        self._refresh_eligible(singer, now)

    def leave(self, singer_id: str, now: float) -> None:
        singer = self._singer(singer_id)
        if singer.state == SINGING:
            raise RotationError("finish the song first")
        if self.call and self.call.singer_id == singer_id:
            self.call = None
        singer.state = LEFT
        singer.eligible_since = None

    # -- the stage ------------------------------------------------------

    def appeared(self, singer_id: str, now: float) -> list[Event]:
        """The called singer reached the stage."""
        call = self.call
        if not call or call.singer_id != singer_id:
            raise RotationError("that singer has not been called")
        if call.appeared_at is not None:
            return []
        call.appeared_at = now
        singer = self._singer(singer_id)
        walkup = max(0.0, now - call.made_at)
        alpha = self.cfg.walkup_alpha
        singer.walkup_ema = (
            walkup
            if singer.walkup_ema is None
            else (1 - alpha) * singer.walkup_ema + alpha * walkup
        )
        self.room_walkup_ema = (
            walkup
            if self.room_walkup_ema is None
            else (1 - alpha) * self.room_walkup_ema + alpha * walkup
        )
        profile = self.profiles.get(singer.key)
        if profile is not None:
            profile["walkup_ema"] = singer.walkup_ema
        if self.stage is None:
            return [self._start_song(singer, now)]
        return []  # on deck; starts the moment the current song ends

    def _start_song(self, singer: Singer, now: float) -> Event:
        duration = (
            singer.song.duration_s
            if singer.song and singer.song.duration_s is not None
            else self.cfg.default_song_s
        )
        self.stage = Stage(singer.id, started_at=now, ends_at=now + duration)
        singer.state = SINGING
        self.call = None
        return Event(SONG_STARTED, now, singer.id, {"ends_at": now + duration})

    def lead_s(self) -> float:
        """How far before the song's end the next call should go out."""
        base = (
            self.room_walkup_ema * self.cfg.grace_safety
            if self.room_walkup_ema is not None
            else self.cfg.grace_base_s
        )
        present = sum(
            1 for s in self.singers.values() if s.state in (WAITING, ON_DECK, SINGING)
        )
        crowd = self.cfg.crowd_s_per_singer * max(0, present - 2)
        return _clamp(base + crowd, self.cfg.lead_min_s, self.cfg.lead_max_s)

    def _grace_s(self, singer: Singer) -> float:
        ema = singer.walkup_ema or self.room_walkup_ema
        base = ema * self.cfg.grace_safety if ema else self.cfg.grace_base_s
        return _clamp(base, self.cfg.grace_min_s, self.cfg.grace_max_s)

    # -- the draw -------------------------------------------------------

    def _pool(self) -> list[Singer]:
        eligible = [
            s
            for s in self.singers.values()
            if s.state == WAITING and s.song is not None
        ]
        ready = [s for s in eligible if s.cooldown_left == 0]
        # a room where everyone queued is cooling down still needs a singer
        return ready or eligible

    def _weight(self, singer: Singer, min_songs: int, now: float) -> float:
        weight = 1.0 + self.cfg.miss_boost * singer.misses
        weight *= self.cfg.fewest_factor ** (singer.songs_sung - min_songs)
        if (
            singer.songs_sung == 0
            and now - singer.joined_at <= self.cfg.newcomer_window_s
        ):
            weight *= self.cfg.newcomer_boost
        return weight

    def ceiling(self, pool_size: int) -> int:
        """Misses past which a singer is taken next, for this queue depth.

        A flat limit below the queue depth cannot be honoured -- each draw
        has one winner, so with ``n`` queued the average wait is ``n - 1``
        draws. The flat ``max_misses`` therefore only rules small rooms;
        past that the promise becomes relative: never wait more than
        ``ceiling_ratio`` times a perfectly even rotation.
        """
        fair = max(0, pool_size - 1)
        return max(self.cfg.max_misses, math.ceil(fair * self.cfg.ceiling_ratio))

    def _draw(self, now: float, deadline_floor: float) -> Event:
        pool = self._pool()
        over = [s for s in pool if s.misses >= self.ceiling(len(pool))]
        if over:
            over.sort(key=lambda s: (-s.misses, s.songs_sung, s.joined_at, s.id))
            chosen, by = over[0], "ceiling"
        else:
            min_songs = min(s.songs_sung for s in pool)
            weights = [self._weight(s, min_songs, now) for s in pool]
            chosen, by = self.rng.choices(pool, weights=weights)[0], "lottery"
        for singer in self.singers.values():
            if singer.cooldown_left > 0:
                singer.cooldown_left -= 1
                self._refresh_eligible(singer, now)
        for singer in pool:
            if singer is not chosen:
                singer.misses += 1
        waited = (
            now - chosen.eligible_since if chosen.eligible_since is not None else 0.0
        )
        self.call = Call(
            chosen.id,
            made_at=now,
            deadline=max(now + self._grace_s(chosen), deadline_floor),
            misses_at_call=chosen.misses,
            by=by,
        )
        chosen.state = ON_DECK
        chosen.misses = 0
        chosen.eligible_since = None
        return Event(
            CALL,
            now,
            chosen.id,
            {
                "by": by,
                "waited_s": round(waited, 1),
                "misses_at_call": self.call.misses_at_call,
                "pool": len(pool),
                "deadline": self.call.deadline,
                "title": chosen.song.title,
            },
        )

    def _refresh_eligible(self, singer: Singer, now: float) -> None:
        if (
            singer.state == WAITING
            and singer.song is not None
            and singer.cooldown_left == 0
        ):
            if singer.eligible_since is None:
                singer.eligible_since = now
        else:
            singer.eligible_since = None

    # -- time -----------------------------------------------------------

    def tick(self, now: float) -> list[Event]:
        """Advance every timer to ``now``; returns what happened."""
        events: list[Event] = []
        for _ in range(50):  # a no-show can trigger a redraw in the same tick
            event = self._step(now)
            if event is None:
                return events
            events.extend(event)
        raise RuntimeError("rotation tick failed to settle")

    def _step(self, now: float) -> list[Event] | None:
        for singer in list(self.singers.values()):
            if (
                singer.state == AWAY
                and singer.away_since is not None
                # same expression next_due() schedules, so the two can never
                # disagree at the float boundary and spin the caller
                and now >= singer.away_since + self.cfg.away_prune_s
            ):
                singer.state = LEFT
                return [Event(PRUNED, now, singer.id)]
        call = self.call
        if call and call.appeared_at is None and now >= call.deadline:
            return [self._no_show(now)]
        if self.stage and now >= self.stage.ends_at:
            return self._finish_song(self.stage.ends_at)
        if self.stage is None and self.call is None and self._pool():
            return [self._draw(now, deadline_floor=now)]
        if (
            self.stage
            and self.call is None
            and now >= self.stage.ends_at - self.lead_s()
            and self._pool()
        ):
            floor = self.stage.ends_at + self.cfg.end_slack_s
            return [self._draw(now, deadline_floor=floor)]
        return None

    def _no_show(self, now: float) -> Event:
        call = self.call
        self.call = None
        singer = self._singer(call.singer_id)
        singer.no_shows += 1
        singer.misses = 0  # the offer was made; the climb starts over
        profile = self.profiles.get(singer.key)
        if profile is not None:
            profile["no_shows"] = int(profile.get("no_shows", 0)) + 1
        if singer.no_shows >= self.cfg.max_no_shows:
            singer.state = AWAY
            singer.away_since = now
            singer.eligible_since = None
            return Event(TIMED_OUT, now, singer.id)
        singer.state = WAITING
        self._refresh_eligible(singer, now)
        return Event(NO_SHOW, now, singer.id)

    def _finish_song(self, at: float) -> list[Event]:
        stage = self.stage
        self.stage = None
        singer = self._singer(stage.singer_id)
        singer.songs_sung += 1
        singer.cooldown_left = self.cfg.cooldown_draws
        title = singer.song.title if singer.song else ""
        singer.song = None
        singer.state = WAITING
        singer.eligible_since = None
        profile = self.profiles.get(singer.key)
        if profile is not None:
            songs = profile.setdefault("songs", [])
            songs.append(title)
            del songs[: -self.cfg.profile_songs_kept]
        events = [
            Event(SONG_ENDED, at, singer.id, {"title": title}),
            Event(NEEDS_SONG, at, singer.id),  # "staying for another?"
        ]
        call = self.call
        if call and call.appeared_at is not None:
            events.append(self._start_song(self._singer(call.singer_id), at))
        return events

    def next_due(self, now: float) -> float | None:
        """When tick() next has something to do; None means nothing pending."""
        times: list[float] = []
        if self.call and self.call.appeared_at is None:
            times.append(self.call.deadline)
        if self.stage:
            times.append(self.stage.ends_at)
            if self.call is None and self._pool():
                times.append(self.stage.ends_at - self.lead_s())
        elif self.call is None and self._pool():
            times.append(now)
        for singer in self.singers.values():
            if singer.state == AWAY and singer.away_since is not None:
                times.append(singer.away_since + self.cfg.away_prune_s)
        if not times:
            return None
        return max(now, min(times))

    def _singer(self, singer_id: str) -> Singer:
        singer = self.singers.get(singer_id)
        if singer is None:
            raise RotationError("no such singer")
        return singer

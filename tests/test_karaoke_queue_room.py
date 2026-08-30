"""The no-brainer layer: QueueRoom and the page wrapper, tested offline.

Exercises the room directly rather than over a socket, per the house rule;
the HTTP handler above it is a thin shell around these calls.
"""

import json
import random

import pytest

from tools.karaoke_server.queue_server import page_html
from tools.karaoke_server.room import QueueRoom, RotationError
from tools.karaoke_server.rotation import RotationConfig


def make_room(tmp_path=None, seed=1):
    path = str(tmp_path / "profiles.json") if tmp_path else None
    return QueueRoom(path, RotationConfig(), rng=random.Random(seed))


def join_and_queue(room, name, now=0.0, title=None):
    singer_id = room.join({"name": name}, now)["singer_id"]
    room.song({"singer_id": singer_id, "title": title or f"{name}'s song"}, now)
    return singer_id


class TestFlow:
    def test_join_song_call_sing_and_the_stay_prompt(self):
        room = make_room()
        ada = join_and_queue(room, "Ada", 0.0)
        state = room.state(1.0, singer_id=ada)
        # solo room: called immediately
        assert state["called"]["singer_id"] == ada
        assert state["you"]["called"] is True
        room.here({"singer_id": ada}, 10.0)
        state = room.state(11.0, singer_id=ada)
        assert state["you"]["on_stage"] is True
        assert state["singing"]["name"] == "Ada"
        assert state["house_on"] is False
        state = room.state(11.0 + 240.0, singer_id=ada)  # default song over
        assert state["you"]["needs_song"] is True  # the stay-for-another prompt
        assert state["house_on"] is True  # music back up, no silence

    def test_the_waiting_list_never_leaks_the_order(self):
        room = make_room()
        # join in one order, stack misses in another; the list stays sorted
        for name in ("Zoe", "Ada", "Mel"):
            join_and_queue(room, name)
        room.rot.singers["s3"].misses = 3
        state = room.state(0.5)
        names = state["waiting"]
        assert names == sorted(names)
        assert state["waiting_count"] == len(names)

    def test_only_the_called_singer_is_told_they_are_called(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        zoe = join_and_queue(room, "Zoe")
        state = room.state(1.0)
        up = state["called"]["singer_id"]
        other = zoe if up == ada else ada
        assert room.state(1.0, singer_id=up)["you"]["called"] is True
        assert room.state(1.0, singer_id=other)["you"]["called"] is False

    def test_events_come_back_only_past_the_cursor(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        state = room.state(1.0, singer_id=ada)
        seq = state["seq"]
        assert state["events"]  # the call at least
        again = room.state(2.0, singer_id=ada, since=seq)
        assert again["events"] == []

    def test_an_unknown_singer_id_gets_no_you_block(self):
        room = make_room()
        state = room.state(0.0, singer_id="s999")
        assert "you" not in state


class TestRetime:
    def test_the_screen_corrects_the_guess_with_the_real_duration(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 10.0)
        room.retime({"singer_id": ada, "remaining_s": 30.0}, 20.0)
        assert room.rot.stage.ends_at == 50.0
        state = room.state(51.0, singer_id=ada)
        assert state["singing"] is None  # ended on the player's clock

    def test_remaining_zero_means_the_song_just_ended(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 10.0)
        room.retime({"singer_id": ada, "remaining_s": 0}, 60.0)
        state = room.state(60.0, singer_id=ada)
        assert state["singing"] is None
        assert state["you"]["needs_song"] is True

    def test_only_the_singer_on_stage_can_be_retimed(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        with pytest.raises(RotationError):
            room.retime({"singer_id": ada, "remaining_s": 10}, 1.0)
        room.state(1.0)
        room.here({"singer_id": ada}, 5.0)
        with pytest.raises(RotationError):
            room.retime({"singer_id": "s99", "remaining_s": 10}, 6.0)
        with pytest.raises(RotationError):
            room.retime({"singer_id": ada, "remaining_s": -5}, 6.0)


class TestMemory:
    def test_a_singer_is_remembered_between_nights(self, tmp_path):
        night_one = make_room(tmp_path)
        ada = join_and_queue(night_one, "Ada", title="Nine to Five")
        night_one.state(1.0)
        night_one.here({"singer_id": ada}, 10.0)
        night_one.state(300.0)  # song finished; profiles saved

        night_two = make_room(tmp_path)
        joined = night_two.join({"name": "ada"}, 0.0)  # case-insensitive
        assert joined["returning"] is True
        assert joined["past_songs"] == ["Nine to Five"]  # the "your usual?" chips

    def test_a_corrupt_memory_file_means_a_fresh_start(self, tmp_path):
        path = tmp_path / "profiles.json"
        path.write_text("not json {{{")
        room = QueueRoom(str(path))
        assert room.join({"name": "Ada"}, 0.0)["returning"] is False

    def test_the_memory_file_is_valid_json_after_every_save(self, tmp_path):
        room = make_room(tmp_path)
        join_and_queue(room, "Ada")
        room.state(1.0)
        saved = json.loads((tmp_path / "profiles.json").read_text())
        assert saved["ada"]["nights"] == 1


class TestPageWrapper:
    def test_the_fragment_is_wrapped_with_its_role(self):
        fragment = "<title>t</title><style>b{}</style><main>hi</main>"
        doc = page_html("screen", fragment)
        assert doc.startswith("<!doctype html>")
        assert '<meta name="karaoke-role" content="screen">' in doc
        assert '<meta name="karaoke-queue" content="/api">' in doc
        assert doc.index("</style>") < doc.index("<body>") < doc.index("<main>")
        assert page_html("phone", fragment).count('content="phone"') == 1

    def test_the_real_page_wraps_and_carries_both_roles(self):
        doc = page_html("phone")
        assert "<title>Karaoke Queue</title>" in doc
        assert 'id="youreUp"' in doc and 'id="stageCard"' in doc

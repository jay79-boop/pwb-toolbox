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

    def test_the_screen_is_told_which_address_to_publish(self):
        """Only the server knows an address phones can reach."""
        doc = page_html(
            "screen",
            "<style>b{}</style><main>x</main>",
            join_url="http://192.168.1.50:8772/",
        )
        assert '<meta name="karaoke-join" content="http://192.168.1.50:8772/">' in doc

    def test_without_one_the_page_is_left_to_work_it_out(self):
        doc = page_html("screen", "<style>b{}</style><main>x</main>")
        assert "karaoke-join" not in doc

    def test_a_join_url_cannot_break_out_of_its_attribute(self):
        doc = page_html(
            "screen",
            "<style>b{}</style><main>x</main>",
            join_url='http://x/"><script>alert(1)</script>',
        )
        assert "<script>alert(1)" not in doc
        assert "&quot;" in doc

    def test_the_real_page_wraps_and_carries_both_roles(self):
        doc = page_html("phone")
        assert "<title>Karaoke Queue</title>" in doc
        assert 'id="youreUp"' in doc and 'id="stageCard"' in doc


# ---------- what testing alone on one PC found (2026-09-02) ----------


class TestSoloInTheState:
    def test_a_lone_singer_sees_solo_and_no_countdown(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        state = room.state(1.0, singer_id=ada)
        assert state["called"]["solo"] is True
        assert state["called"]["deadline_in_s"] is None  # never inf: it is JSON
        assert state["you"]["solo"] is True
        json.dumps(state)  # the whole poll still serialises
        state = room.state(3600.0, singer_id=ada)  # an hour later, still up
        assert state["you"]["called"] is True and state["you"]["state"] == "on_deck"

    def test_a_second_singer_puts_the_clock_on_the_call(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        zoe = join_and_queue(room, "Zoe", now=30.0)
        state = room.state(31.0, singer_id=ada)
        assert state["called"]["singer_id"] == ada  # still Ada's call
        assert state["called"]["solo"] is False
        assert state["you"]["solo"] is False
        assert state["called"]["deadline_in_s"] > 0
        assert room.state(31.0, singer_id=zoe)["you"]["solo"] is False


class TestHostDesk:
    def test_add_joins_and_queues_in_one_go(self):
        room = make_room()
        out = room.host_add({"name": "Lin", "title": "Nine to Five"}, 0.0)
        assert out["name"] == "Lin" and out["title"] == "Nine to Five"
        state = room.state(1.0, singer_id=out["singer_id"])
        assert state["called"]["name"] == "Lin"  # alone, so called at once
        assert state["you"]["song"] == "Nine to Five"

    def test_add_without_a_song_leaves_them_choosing(self):
        room = make_room()
        out = room.host_add({"name": "Lin"}, 0.0)
        assert out["title"] is None
        state = room.state(1.0, singer_id=out["singer_id"])
        assert state["called"] is None
        assert state["you"]["needs_song"] is True
        assert room.host_add({"name": "Mo", "title": "   "}, 0.0)["title"] is None

    def test_add_carries_a_link_the_way_the_phone_does(self):
        room = make_room()
        out = room.host_add(
            {
                "name": "Lin",
                "title": "https://youtu.be/dQw4w9WgXcQ",
                "source": "link",
                "ref": "dQw4w9WgXcQ",
            },
            0.0,
        )
        called = room.state(1.0, singer_id=out["singer_id"])["called"]
        assert called["source"] == "link" and called["ref"] == "dQw4w9WgXcQ"

    def test_add_refuses_a_blank_name(self):
        room = make_room()
        with pytest.raises(RotationError):
            room.host_add({"name": "   ", "title": "x"}, 0.0)
        with pytest.raises(RotationError):
            room.host_add({}, 0.0)
        assert room.rot.singers == {}

    def test_a_bad_song_refuses_the_whole_add(self):
        room = make_room()
        with pytest.raises(RotationError):
            room.host_add({"name": "Lin", "title": "x", "duration_s": 4}, 0.0)
        assert all(s.state == "left" for s in room.rot.singers.values())
        assert room.state(1.0)["waiting"] == []

    def test_skip_strikes_the_called_singer_and_redraws(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        zoe = join_and_queue(room, "Zoe")
        up = room.state(1.0)["called"]["singer_id"]
        other = zoe if up == ada else ada
        room.rot.singers[other].misses = room.rot.ceiling(2)  # settle the redraw
        out = room.host_skip({}, 2.0)
        assert out["skipped"] == up
        state = room.state(2.0)
        assert state["called"]["singer_id"] == other  # redrawn in the same call
        assert room.rot.singers[up].no_shows == 1
        kinds = [e["kind"] for e in state["events"]]
        assert "no_show" in kinds and kinds.count("call") == 2

    def test_skip_refuses_with_no_call_or_a_singer_already_there(self):
        room = make_room()
        with pytest.raises(RotationError):
            room.host_skip({}, 0.0)
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 2.0)
        with pytest.raises(RotationError):
            room.host_skip({}, 3.0)  # on stage, not on the way
        assert room.state(3.0)["singing"]["singer_id"] == ada

    def test_end_finishes_the_song_now(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 2.0)
        assert room.state(10.0)["singing"] is not None
        out = room.host_end({}, 10.0)
        assert out["ended"] == ada
        state = room.state(10.0, singer_id=ada)
        assert state["singing"] is None
        assert state["you"]["needs_song"] is True
        assert state["house_on"] is True
        assert room.rot.singers[ada].songs_sung == 1

    def test_end_refuses_when_nobody_is_on_stage(self):
        room = make_room()
        with pytest.raises(RotationError):
            room.host_end({}, 0.0)
        join_and_queue(room, "Ada")
        room.state(1.0)  # called, not yet on stage
        with pytest.raises(RotationError):
            room.host_end({}, 2.0)


class TestHostRemoveAndTheWaitingList:
    """The host can see who is waiting, and take one of them off the list.

    Deliberately NOT a running order and deliberately not reorderable: the
    list is alphabetical, the same set the public poll already publishes,
    and the only thing the host may do to it is remove somebody. Nobody
    knowing who is next is the product (docs/karaoke-rotation.md).
    """

    def test_the_list_carries_ids_in_the_same_alphabetical_order(self):
        room = make_room()
        # mixed case on purpose: the public list and the host's list are one
        # sort, not two, or they disagree about names like these
        for name in ("Zoe", "Ada", "mel", "bo"):
            join_and_queue(room, name)
        room.rot.singers["s1"].misses = 5  # stack pressure in a third order
        state = room.state(0.5)
        rows = state["waiting_list"]
        assert [r["name"] for r in rows] == state["waiting"] == sorted(state["waiting"])
        assert len(rows) == state["waiting_count"]
        assert all(r["singer_id"] in room.rot.singers for r in rows)
        json.dumps(state)  # still one poll payload

    def test_the_list_never_holds_someone_with_no_song(self):
        # exactly the set the public poll already publishes: in the draw
        # means a song is queued, so a walk-up still choosing is not on it,
        # and neither is whoever the draw has already called
        room = make_room()
        for name in ("Ada", "Zoe", "Lin"):
            join_and_queue(room, name)
        room.host_add({"name": "Mel"}, 0.0)  # signed in, still choosing
        state = room.state(0.5)
        names = [r["name"] for r in state["waiting_list"]]
        assert "Mel" not in names
        assert names == state["waiting"]
        assert len(names) == 2  # the third of the three was called

    def test_remove_takes_a_waiting_singer_off_the_draw(self):
        room = make_room()
        join_and_queue(room, "Ada")
        zoe = join_and_queue(room, "Zoe")
        up = room.state(1.0)["called"]["singer_id"]
        spare = zoe if up != zoe else "s1"
        out = room.host_remove({"singer_id": spare}, 2.0)
        assert out["name"] == room.rot.singers[spare].name
        state = room.state(2.0)
        assert out["name"] not in state["waiting"]
        assert all(r["singer_id"] != spare for r in state["waiting_list"])
        assert state["called"]["singer_id"] == up  # somebody else's call stands

    def test_removing_the_called_singer_redraws_and_costs_no_strike(self):
        # they went home; that is not a no-show, and they are not struck
        room = make_room()
        ada = join_and_queue(room, "Ada")
        zoe = join_and_queue(room, "Zoe")
        up = room.state(1.0)["called"]["singer_id"]
        other = zoe if up == ada else ada
        room.host_remove({"singer_id": up}, 2.0)
        state = room.state(2.0)
        assert state["called"]["singer_id"] == other  # redrawn in the same call
        assert room.rot.singers[up].no_shows == 0
        assert room.rot.singers[up].state == "left"

    def test_remove_refuses_the_singer_on_stage(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 2.0)
        with pytest.raises(RotationError):
            room.host_remove({"singer_id": ada}, 3.0)
        assert room.state(3.0)["singing"]["singer_id"] == ada

    def test_remove_refuses_a_singer_who_is_not_here(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        for payload in ({"singer_id": "s999"}, {}, {"singer_id": None}):
            with pytest.raises(RotationError):
                room.host_remove(payload, 1.0)
        assert room.rot.singers[ada].state != "left"  # nobody else disturbed

    def test_the_route_is_wired(self):
        from tools.karaoke_server.queue_server import POSTS

        assert POSTS["/api/host/remove"] == "host_remove"


class TestTheWalkUpSinger:
    """Someone who signed up at the screen and never had a phone.

    ``/api/here`` carries a ``singer_id`` only that singer's phone holds,
    so for a walk-up it can never be sent. Without ``host_here`` the draw
    calls them, nobody can answer, and the clock strikes them out -- which
    is why sign-up alone would have shipped a broken feature.
    """

    def test_a_singer_who_never_touched_a_phone_gets_on_stage(self):
        room = make_room()
        added = room.host_add({"name": "Ada", "title": "Sweet Caroline"}, 0.0)
        assert room.state(1.0)["called"]["singer_id"] == added["singer_id"]
        out = room.host_here({}, 2.0)
        assert out["singer_id"] == added["singer_id"] and out["name"] == "Ada"
        state = room.state(3.0)
        assert state["singing"]["singer_id"] == added["singer_id"]
        assert state["singing"]["title"] == "Sweet Caroline"
        assert state["called"] is None
        assert state["house_on"] is False

    def test_without_it_the_walk_up_is_struck_out(self):
        # the convicting case: the same room, nobody able to answer the call
        room = make_room()
        room.host_add({"name": "Ada", "title": "Sweet Caroline"}, 0.0)
        join_and_queue(room, "Zoe", 0.0)  # a second singer, so the call is timed
        called = room.state(1.0)["called"]
        assert called["deadline_in_s"] > 0
        late = called["deadline_in_s"] + 2.0
        assert room.state(1.0 + late)["called"]["singer_id"] != called["singer_id"]

    def test_it_narrates_and_remembers_like_the_phone_path(self):
        room = make_room()
        added = room.host_add({"name": "Ada", "title": "Sweet Caroline"}, 0.0)
        room.state(1.0)
        room.host_here({}, 2.0)
        kinds = [e["kind"] for e in room.state(2.0)["events"]]
        assert "song_started" in kinds and "house_off" in kinds
        assert room.rot.singers[added["singer_id"]].walkup_ema is not None

    def test_it_refuses_when_nobody_has_been_called(self):
        room = make_room()
        with pytest.raises(RotationError) as err:
            room.host_here({}, 0.0)
        assert "called" in str(err.value)
        assert room.state(1.0)["singing"] is None

    def test_it_refuses_a_second_press(self):
        # the on-deck case: called during someone else's outro, so the
        # confirmed singer waits at the stage rather than starting at once
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 2.0)
        room.host_add({"name": "Zoe", "title": "Nine to Five"}, 3.0)
        end = room.rot.stage.ends_at
        assert room.state(end - 5.0)["called"]["name"] == "Zoe"
        assert room.host_here({}, end - 4.0)["name"] == "Zoe"
        assert room.state(end - 4.0)["called"]["appeared"] is True
        with pytest.raises(RotationError) as err:
            room.host_here({}, end - 3.0)
        assert "already" in str(err.value)

    def test_it_does_not_disturb_the_singer_on_stage(self):
        room = make_room()
        ada = join_and_queue(room, "Ada")
        room.state(1.0)
        room.here({"singer_id": ada}, 2.0)  # the phone path, untouched
        with pytest.raises(RotationError):
            room.host_here({}, 3.0)
        assert room.state(3.0)["singing"]["singer_id"] == ada

    def test_the_route_is_wired(self):
        from tools.karaoke_server.queue_server import POSTS

        assert POSTS["/api/host/here"] == "host_here"


class TestTheWalkUpCardIsTheScreensAlone:
    """Always on the big screen, never on a phone, never behind a button."""

    @pytest.fixture
    def page(self):
        from tools.karaoke_server import queue_server

        return queue_server.PAGE.read_text(encoding="utf-8")

    def test_the_sign_up_markup_exists_and_reads_as_an_invitation(self, page):
        for control in ("walkupCard", "walkupName", "walkupSong", "walkupBtn"):
            assert page.count('id="%s"' % control) == 1, control
        assert "No phone? No problem" in page
        assert "Sign up right here" in page
        assert "Put me in the draw" in page
        assert 'id="walkupErr" hidden' in page  # the red line, like hostErr

    def test_the_card_is_not_behind_the_host_button(self, page):
        # visible the moment the screen opens: no hidden attribute of its
        # own, and it is a sibling of the QR box rather than a child of the
        # host panel
        assert 'id="walkupCard">' in page
        aside = page[page.index('id="sideCol"') : page.index("</aside>")]
        assert 'id="walkupCard"' in aside
        assert aside.index('id="qrBox"') < aside.index('id="walkupCard"')
        assert aside.index('id="walkupCard"') < aside.index('id="hostPanel"')
        # nothing ever toggles the card itself
        assert 'show("walkupCard"' not in page

    def test_the_song_is_optional_and_a_link_is_parsed_like_the_phone(self, page):
        assert page.count("function youtubeId(") == 1  # still one regex
        block = page[
            page.index("// ==== walk-up desk") : page.index("// ==== end walk-up desk")
        ]
        assert "youtubeId(raw)" in block
        assert 'source: vid ? "link" : "title"' in block
        assert "payload.ref = vid" in block
        assert "if (raw) { payload.title = raw; }" in block  # blank song allowed
        assert "You're in, " in block  # the confirmation names them

    def test_the_arrival_button_lives_on_the_stage_card(self, page):
        stage = page[
            page.index('id="stageCard"') : page.index(
                "</section>", page.index('id="stageCard"')
            )
        ]
        assert 'id="stageHereBtn" hidden' in stage
        assert "is at the stage — start the song" in stage
        assert 'id="stageHereErr" hidden' in stage
        # and it is shown only while somebody is called and not yet there
        assert 'show("stageHereBtn", walking)' in page
        assert "var walking = !!(state.called && !state.called.appeared);" in page

    def test_only_the_screen_role_wires_it(self, page):
        script = page[page.index("<script>\n(function") :]
        start = script.index("// ==== walk-up desk")
        end = script.index("// ==== end walk-up desk")
        assert start < end
        block = script[start:end]
        assert script.count("wireWalkup(") == 2  # one definition, one call
        assert "function wireWalkup()" in block
        assert 'if (ROLE === "screen") walkupRender = wireWalkup();' in block
        outside = script[:start] + script[end:]
        for ident in ("walkupBtn", "walkupErr", "walkupNote", "stageHereBtn"):
            assert ident not in outside, ident
        # the phone-side render never touches the walk-up desk either
        phone = script[
            script.index("function renderPhone") : script.index(
                "/* ============ SCREEN"
            )
        ]
        assert "walkup" not in phone.lower()
        assert "stagehere" not in phone.lower()

    def test_the_phone_arrival_path_is_untouched(self, page):
        assert (
            '$("hereBtn").onclick = function () { post("/here", {singer_id: singerId}, poll); };'
            in page
        )


class TestTheHostPanelIsTheScreensAlone:
    """The desk lives on the big screen. A phone must never grow one."""

    @pytest.fixture
    def page(self):
        from tools.karaoke_server import queue_server

        return queue_server.PAGE.read_text(encoding="utf-8")

    def test_the_markup_exists_and_is_hidden_by_default(self, page):
        assert 'id="hostPanel" hidden' in page
        assert 'id="hostBtn"' in page and "hidden>Host</button>" in page
        for control in (
            "hostName",
            "hostSong",
            "hostAddBtn",
            "hostSkipBtn",
            "hostEndBtn",
        ):
            assert page.count('id="%s"' % control) == 1
        assert 'id="hostSkipBtn" hidden' in page and 'id="hostEndBtn" hidden' in page

    def test_only_the_screen_role_wires_it(self, page):
        script = page[page.index("<script>\n(function") :]
        start, end = script.index("// ==== host desk"), script.index(
            "// ==== end host desk"
        )
        assert start < end
        block = script[start:end]
        # one definition, one call site, and the call is gated on the role
        assert script.count("wireHost(") == 2
        assert "function wireHost()" in block
        assert 'if (ROLE === "screen") hostRender = wireHost();' in block
        # and nothing outside that block ever reveals the panel or its button
        outside = script[:start] + script[end:]
        for ident in ("hostPanel", "hostBtn", "hostSkipBtn", "hostEndBtn"):
            assert ident not in outside, ident
        # the phone-side render never touches it either
        phone = script[
            script.index("function renderPhone") : script.index(
                "/* ============ SCREEN"
            )
        ]
        assert "host" not in phone.lower()

    def test_the_solo_call_shows_no_countdown_on_the_phone(self, page):
        assert 'id="upSolo" hidden' in page
        assert "Only you in the draw so far" in page
        assert "get to the stage whenever you're ready" in page
        assert (
            'show("upCount", !solo); show("upMiss", !solo); show("upSolo", solo);'
            in page
        )

    def test_the_host_add_parses_a_link_like_the_phone(self, page):
        # one regex, two callers: the host desk must not grow its own
        assert page.count("function youtubeId(") == 1
        host = page[
            page.index("// ==== host desk") : page.index("// ==== end host desk")
        ]
        assert "youtubeId(raw)" in host
        assert 'source: vid ? "link" : "title"' in host
        assert "payload.ref = vid" in host


# ---------- the title lookup: the one outbound request, at the edge ----------


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def socketless_get(handler_cls, path):
    """The same trick for do_GET: a handler with no connection under it."""
    import io

    handler = handler_cls.__new__(handler_cls)
    handler.rfile = io.BytesIO(b"")
    handler.wfile = io.BytesIO()
    handler.headers = {}
    handler.path = path
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET %s HTTP/1.1" % path
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.do_GET()
    raw = handler.wfile.getvalue().decode("utf-8")
    status = int(raw.split(" ", 2)[1])
    return status, raw.split("\r\n\r\n", 1)[1]


def socketless_get_json(handler_cls, path):
    status, body = socketless_get(handler_cls, path)
    return status, json.loads(body)


def socketless(handler_cls, path, payload):
    """Drive do_POST on a handler with no socket under it.

    The handler is built the way http.server would, minus the connection:
    request bytes come from a BytesIO and the response lands in another.
    """
    import io

    handler = handler_cls.__new__(handler_cls)
    body = json.dumps(payload).encode("utf-8")
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.headers = {"Content-Length": str(len(body))}
    handler.path = path
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "POST %s HTTP/1.1" % path
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.do_POST()
    raw = handler.wfile.getvalue().decode("utf-8")
    status = int(raw.split(" ", 2)[1])
    return status, json.loads(raw.split("\r\n\r\n", 1)[1])


@pytest.fixture
def no_network(monkeypatch):
    """Any urlopen in a test is a bug in the test.

    Records rather than raises: the fetcher swallows every exception by
    design, so a raising stub would be silently forgiven.
    """
    import urllib.request

    reached = []

    def refuse(*args, **kwargs):
        reached.append(args[0] if args else kwargs)
        raise OSError("a test reached for the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setenv("KARAOKE_QUIET", "1")
    yield reached
    assert reached == [], "the suite must never reach YouTube"


class TestTitleLookup:
    def test_a_link_becomes_its_title_on_the_phone_and_the_screen(self, no_network):
        from tools.karaoke_server.queue_server import build

        asked = []

        def lookup(video_id):
            asked.append(video_id)
            return "Dolly Parton - 9 to 5"

        handler = build(title_lookup=lookup)
        status, joined = socketless(handler, "/api/join", {"name": "Ada"})
        assert status == 200
        status, out = socketless(
            handler,
            "/api/song",
            {
                "singer_id": joined["singer_id"],
                "source": "link",
                "ref": "UbxUSsFXYo4",
                "title": "https://www.youtube.com/watch?v=UbxUSsFXYo4",
            },
        )
        assert status == 200 and out["title"] == "Dolly Parton - 9 to 5"
        state = handler.room.state(1.0, singer_id=joined["singer_id"])
        assert state["called"]["title"] == "Dolly Parton - 9 to 5"
        assert state["called"]["ref"] == "UbxUSsFXYo4"  # the player still gets the id
        assert state["you"]["song"] == "Dolly Parton - 9 to 5"
        assert asked == ["UbxUSsFXYo4"]

    def test_the_host_desk_gets_the_same_lookup(self, no_network):
        from tools.karaoke_server.queue_server import build

        handler = build(title_lookup=lambda vid: "Sweet Caroline (Official Audio)")
        status, out = socketless(
            handler,
            "/api/host/add",
            {
                "name": "Lin",
                "source": "link",
                "ref": "1vrEljMfXYo",
                "title": "https://youtu.be/1vrEljMfXYo",
            },
        )
        assert status == 200
        assert out["title"] == "Sweet Caroline (Official Audio)"

    @pytest.mark.parametrize(
        "lookup",
        [
            lambda vid: None,
            lambda vid: "",
            lambda vid: (_ for _ in ()).throw(OSError("no uplink")),
            lambda vid: (_ for _ in ()).throw(TimeoutError()),
        ],
    )
    def test_a_failed_lookup_keeps_the_raw_link(self, no_network, lookup):
        from tools.karaoke_server.queue_server import build

        handler = build(title_lookup=lookup)
        _, joined = socketless(handler, "/api/join", {"name": "Ada"})
        raw = "https://www.youtube.com/watch?v=UbxUSsFXYo4"
        status, out = socketless(
            handler,
            "/api/song",
            {
                "singer_id": joined["singer_id"],
                "source": "link",
                "ref": "UbxUSsFXYo4",
                "title": raw,
            },
        )
        assert status == 200 and out["title"] == raw  # exactly as before

    def test_a_typed_title_is_never_looked_up(self, no_network):
        from tools.karaoke_server.queue_server import build

        asked = []
        handler = build(title_lookup=lambda vid: asked.append(vid) or "never")
        _, joined = socketless(handler, "/api/join", {"name": "Ada"})
        status, out = socketless(
            handler,
            "/api/song",
            {"singer_id": joined["singer_id"], "title": "Hey Jude"},
        )
        assert status == 200 and out["title"] == "Hey Jude"
        # a link with no id is a bare title too (nothing has ticked, so the
        # song can still be changed)
        status, out = socketless(
            handler,
            "/api/song",
            {"singer_id": joined["singer_id"], "source": "link", "title": "no ref"},
        )
        assert status == 200 and out["title"] == "no ref"
        assert asked == []

    def test_one_fetch_per_video_for_the_life_of_the_process(self, no_network):
        from tools.karaoke_server.queue_server import resolve_title

        calls = []

        def lookup(vid):
            calls.append(vid)
            return "Title" if vid == "known000000" else None

        cache = {}
        for _ in range(3):
            out = resolve_title(
                {"source": "link", "ref": "known000000", "title": "u"}, lookup, cache
            )
            assert out["title"] == "Title"
            miss = resolve_title(
                {"source": "link", "ref": "unknown0000", "title": "u"}, lookup, cache
            )
            assert (
                miss["title"] == "u"
            )  # a miss is remembered too: no 2s stall per poll
        assert calls == ["known000000", "unknown0000"]

    def test_the_default_is_the_oembed_fetch_and_the_suite_never_runs_it(
        self, no_network
    ):
        from tools.karaoke_server import queue_server

        assert queue_server.Handler.title_lookup is queue_server.youtube_title
        assert queue_server.build().title_lookup is queue_server.youtube_title
        # and a room built with a fake never touches the default
        handler = queue_server.build(title_lookup=lambda vid: "faked")
        assert handler.title_lookup is not queue_server.youtube_title
        _, joined = socketless(handler, "/api/join", {"name": "Ada"})
        _, out = socketless(
            handler,
            "/api/song",
            {
                "singer_id": joined["singer_id"],
                "source": "link",
                "ref": "UbxUSsFXYo4",
                "title": "u",
            },
        )
        assert out["title"] == "faked"

    def test_the_oembed_parser_reads_a_title_and_shrugs_at_everything_else(
        self, monkeypatch
    ):
        import urllib.request

        from tools.karaoke_server import queue_server

        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"], seen["timeout"] = url, timeout
            return FakeResponse(
                b'{"title": "  Dolly Parton - 9 to 5  ", "author_name": "x"}'
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert queue_server.youtube_title("UbxUSsFXYo4") == "Dolly Parton - 9 to 5"
        assert seen["url"] == (
            "https://www.youtube.com/oembed?url="
            "https://www.youtube.com/watch?v=UbxUSsFXYo4&format=json"
        )
        assert seen["timeout"] == 2.0
        # no uplink at all: urlopen raises, the answer is None, nothing escapes
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: (_ for _ in ()).throw(OSError("unreachable")),
        )
        assert queue_server.youtube_title("UbxUSsFXYo4") is None
        for body, status in (
            (b"<html>captive portal</html>", 200),
            (b'{"no_title": 1}', 200),
            (b'{"title": ""}', 200),
            (b'{"title": "x"}', 503),
            (b"[1, 2]", 200),
        ):
            monkeypatch.setattr(
                urllib.request,
                "urlopen",
                lambda url, timeout=None, b=body, s=status: FakeResponse(b, s),
            )
            assert queue_server.youtube_title("UbxUSsFXYo4") is None, (body, status)


class TestWhichAddressPhonesCanReach:
    """The QR is only as good as the address it publishes.

    2026-09-02: the owner's machine had a VPN up, so the route to the
    internet ran through a tunnel and the server published
    http://10.5.0.2:8772 -- an address no phone on the Wi-Fi could
    resolve. The default route answers "how do I reach the internet",
    which is not the question. These pin the ranking that replaced it.
    """

    def rank(self, *addresses):
        from tools.karaoke_server.queue_server import address_rank

        return sorted(addresses, key=lambda a: (address_rank(a), a))

    def test_the_wifi_address_beats_the_vpn_tunnel(self):
        # the exact pair the owner's machine offered
        assert self.rank("10.5.0.2", "192.168.1.50")[0] == "192.168.1.50"

    def test_private_ranges_sort_by_how_likely_a_venue_hands_them_out(self):
        assert self.rank("10.0.0.5", "172.20.0.3", "192.168.0.9") == [
            "192.168.0.9",
            "172.20.0.3",
            "10.0.0.5",
        ]

    def test_loopback_and_a_dead_adapter_sort_below_anything_usable(self):
        # 169.254.x is what Windows assigns when DHCP never answered
        assert self.rank("127.0.0.1", "169.254.7.7", "10.5.0.2") == [
            "10.5.0.2",
            "169.254.7.7",
            "127.0.0.1",
        ]

    def test_a_junk_string_is_ranked_not_raised(self):
        from tools.karaoke_server.queue_server import address_rank

        # getaddrinfo can hand back things this parser was not promised
        assert address_rank("not-an-address") == 90
        assert address_rank("1.2.3") == 90

    def test_every_address_is_offered_best_guess_first(self, monkeypatch):
        from tools.karaoke_server import queue_server

        monkeypatch.setattr(queue_server, "_default_route_address", lambda: "10.5.0.2")
        monkeypatch.setattr(
            queue_server,
            "_host_addresses",
            lambda: ["10.5.0.2", "192.168.1.50", "127.0.0.1"],
        )
        found = queue_server.lan_addresses()
        assert found == ["192.168.1.50", "10.5.0.2", "127.0.0.1"]
        # the alternates have to survive: they are what the operator reads
        # off the screen when the guess is wrong
        assert queue_server.lan_address() == "192.168.1.50"

    def test_a_machine_with_nothing_to_offer_says_so_rather_than_guessing(
        self, monkeypatch
    ):
        from tools.karaoke_server import queue_server

        monkeypatch.setattr(queue_server, "_default_route_address", lambda: None)
        monkeypatch.setattr(queue_server, "_host_addresses", lambda: [])
        assert queue_server.lan_addresses() == []
        assert queue_server.lan_address() is None

    def test_a_broken_hostname_lookup_is_not_fatal(self, monkeypatch):
        from tools.karaoke_server import queue_server

        def boom(*a, **k):
            raise OSError("no name resolution here")

        monkeypatch.setattr(queue_server.socket, "getaddrinfo", boom)
        assert queue_server._host_addresses() == []


# ---------- song search: an addition, never a replacement ----------


class TestTheSearchFetcher:
    """The one keyed request the system makes, and every way it fails."""

    def test_it_asks_the_data_api_and_reads_the_rows_back(self, monkeypatch):
        import urllib.request

        from tools.karaoke_server import queue_server

        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"], seen["timeout"] = url, timeout
            return FakeResponse(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": {"videoId": "1vrEljMfXYo"},
                                "snippet": {
                                    "title": "Neil Diamond - Sweet Caroline",
                                    "channelTitle": "Neil Diamond",
                                },
                            },
                            {
                                "id": {"videoId": "UbxUSsFXYo4"},
                                "snippet": {
                                    # the API HTML-escapes titles; the page
                                    # sets them with textContent, so they are
                                    # unescaped here or the entities show
                                    "title": "Don&#39;t Stop Believin&#39;",
                                    "channelTitle": "Journey &amp; friends",
                                },
                            },
                        ]
                    }
                ).encode("utf-8")
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        found = queue_server.youtube_search("sweet caroline", api_key="KEY-123")
        assert found == [
            {
                "ref": "1vrEljMfXYo",
                "title": "Neil Diamond - Sweet Caroline",
                "channel": "Neil Diamond",
            },
            {
                "ref": "UbxUSsFXYo4",
                "title": "Don't Stop Believin'",
                "channel": "Journey & friends",
            },
        ]
        assert seen["timeout"] == 3.0  # short: a search must never hang a join
        assert seen["url"].startswith("https://www.googleapis.com/youtube/v3/search?")
        for want in (
            "part=snippet",
            "type=video",
            "videoEmbeddable=true",
            "maxResults=8",
            "q=sweet+caroline",
            "key=KEY-123",
        ):
            assert want in seen["url"], want

    def test_no_key_means_no_request_at_all(self, monkeypatch, no_network):
        from tools.karaoke_server import queue_server

        monkeypatch.delenv(queue_server.SEARCH_ENV_KEY, raising=False)
        assert queue_server.search_key() == ""
        assert queue_server.search_available() is False
        assert queue_server.youtube_search("sweet caroline") is None
        # no_network asserts on teardown that urlopen was never reached

    def test_an_unexpanded_placeholder_is_not_a_key(self, monkeypatch, no_network):
        from tools.karaoke_server import queue_server

        monkeypatch.setenv(queue_server.SEARCH_ENV_KEY, "$YOUTUBE_API_KEY")
        assert queue_server.search_key() == ""
        assert queue_server.youtube_search("anything") is None
        monkeypatch.setenv(queue_server.SEARCH_ENV_KEY, "   ")
        assert queue_server.search_available() is False

    def test_a_key_in_the_environment_is_the_one_it_uses(self, monkeypatch):
        import urllib.request

        from tools.karaoke_server import queue_server

        monkeypatch.setenv(queue_server.SEARCH_ENV_KEY, "  from-the-env  ")
        assert queue_server.search_key() == "from-the-env"
        assert queue_server.search_available() is True
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"] = url
            return FakeResponse(b'{"items": []}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert queue_server.youtube_search("x") == []
        assert "key=from-the-env" in seen["url"]

    @pytest.mark.parametrize(
        "body,status",
        [
            (b"<html>captive portal login</html>", 200),
            (b'{"error": {"code": 403, "message": "quotaExceeded"}}', 403),
            (b'{"items": "not a list"}', 200),
            (b"[1, 2]", 200),
            (b'{"items": []}', 500),
        ],
    )
    def test_every_bad_answer_is_none_rather_than_a_raise(
        self, monkeypatch, body, status
    ):
        import urllib.request

        from tools.karaoke_server import queue_server

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: FakeResponse(body, status),
        )
        assert queue_server.youtube_search("x", api_key="k") is None

    def test_no_uplink_and_a_timeout_are_both_just_none(self, monkeypatch):
        import urllib.request

        from tools.karaoke_server import queue_server

        for boom in (OSError("unreachable"), TimeoutError()):
            monkeypatch.setattr(
                urllib.request,
                "urlopen",
                lambda url, timeout=None, e=boom: (_ for _ in ()).throw(e),
            )
            assert queue_server.youtube_search("x", api_key="k") is None

    def test_half_broken_rows_are_dropped_not_shown(self, monkeypatch):
        import urllib.request

        from tools.karaoke_server import queue_server

        payload = {
            "items": [
                {"id": {"kind": "channel"}, "snippet": {"title": "no video id"}},
                {"id": {"videoId": "goodgoodgo1"}, "snippet": {"title": "  Keep  "}},
                {"id": {"videoId": "nosnippet01"}},
                "not even a dict",
                {"id": {"videoId": "blanktitle"}, "snippet": {"title": "   "}},
            ]
        }
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: FakeResponse(json.dumps(payload).encode()),
        )
        assert queue_server.youtube_search("x", api_key="k") == [
            {"ref": "goodgoodgo1", "title": "Keep", "channel": ""}
        ]


class TestTheSearchCache:
    """100 searches a day, for the whole venue. One query, one spend."""

    def test_one_call_per_query_for_the_life_of_the_process(self):
        from tools.karaoke_server.queue_server import resolve_search

        calls = []

        def lookup(query):
            calls.append(query)
            return [{"ref": "r", "title": query, "channel": ""}]

        cache = {}
        for _ in range(4):
            out = resolve_search("Sweet Caroline", lookup, cache)
            assert out[0]["title"] == "Sweet Caroline"
        # case and whitespace fold onto the same slot: a room typing the same
        # song four ways must not spend four searches on it
        resolve_search("  sweet   caroline ", lookup, cache)
        resolve_search("SWEET CAROLINE", lookup, cache)
        assert calls == ["Sweet Caroline"]

    def test_a_miss_and_a_failure_are_remembered_too(self):
        from tools.karaoke_server.queue_server import resolve_search

        calls = []

        def lookup(query):
            calls.append(query)
            if query == "boom":
                raise TimeoutError()
            return []

        cache = {}
        for _ in range(3):
            assert resolve_search("nothing here", lookup, cache) == []
            assert resolve_search("boom", lookup, cache) is None
        assert calls == ["nothing here", "boom"]

    def test_a_blank_query_is_never_a_request(self):
        from tools.karaoke_server.queue_server import resolve_search

        calls = []
        cache = {}
        for blank in ("", "   ", None):
            assert resolve_search(blank, lookup=calls.append, cache=cache) == []
        assert calls == []


class TestSearchOverHTTP:
    def test_a_search_comes_back_as_tappable_rows(self, no_network):
        from tools.karaoke_server.queue_server import build

        asked = []

        def lookup(query):
            asked.append(query)
            return [
                {"ref": "1vrEljMfXYo", "title": "Sweet Caroline", "channel": "Neil"}
            ]

        handler = build(
            search_lookup=lookup, search_enabled=True, title_lookup=lambda vid: None
        )
        status, body = socketless_get_json(handler, "/api/search?q=sweet%20caroline")
        assert status == 200
        assert body["ok"] is True
        assert body["results"][0]["ref"] == "1vrEljMfXYo"
        assert asked == ["sweet caroline"]
        # and tapping the row queues it exactly like a pasted link would
        _, joined = socketless(handler, "/api/join", {"name": "Ada"})
        _, out = socketless(
            handler,
            "/api/song",
            {
                "singer_id": joined["singer_id"],
                "source": "link",
                "ref": "1vrEljMfXYo",
                "title": "Sweet Caroline",
            },
        )
        assert out["title"] == "Sweet Caroline"
        called = handler.room.state(1.0)["called"]
        assert called["source"] == "link" and called["ref"] == "1vrEljMfXYo"

    def test_nothing_found_is_a_success_with_no_rows(self, no_network):
        from tools.karaoke_server.queue_server import build

        handler = build(search_lookup=lambda q: [], search_enabled=True)
        status, body = socketless_get_json(handler, "/api/search?q=asdfghjkl")
        assert status == 200 and body["ok"] is True and body["results"] == []

    @pytest.mark.parametrize(
        "lookup",
        [
            lambda q: (_ for _ in ()).throw(TimeoutError()),
            lambda q: (_ for _ in ()).throw(OSError("no uplink")),
            lambda q: None,  # no key, quota, captive portal: all the same
            lambda q: "not a list at all",
        ],
    )
    def test_a_failed_search_never_breaks_the_join_path(self, no_network, lookup):
        """The pin: search can die however it likes, joining still works."""
        from tools.karaoke_server import queue_server

        handler = queue_server.build(
            search_lookup=lookup,
            search_enabled=True,
            title_lookup=lambda vid: None,
        )
        status, body = socketless_get_json(handler, "/api/search?q=sweet%20caroline")
        assert status == 200, "a dead search must never be an HTTP error"
        assert body["ok"] is False and body["results"] == []
        assert body["note"] == queue_server.SEARCH_OFF_NOTE
        assert "key" not in json.dumps(body).lower()

        # everything the phone could do before search existed, it still does
        _, joined = socketless(handler, "/api/join", {"name": "Ada"})
        _, typed = socketless(
            handler,
            "/api/song",
            {"singer_id": joined["singer_id"], "title": "Hey Jude"},
        )
        assert typed["title"] == "Hey Jude"
        raw = "https://www.youtube.com/watch?v=UbxUSsFXYo4"
        _, pasted = socketless(
            handler,
            "/api/song",
            {
                "singer_id": joined["singer_id"],
                "source": "link",
                "ref": "UbxUSsFXYo4",
                "title": raw,
            },
        )
        assert pasted["title"] == raw
        assert handler.room.state(1.0)["called"]["ref"] == "UbxUSsFXYo4"

    def test_the_default_fetcher_is_the_data_api_and_no_test_runs_it(self, no_network):
        from tools.karaoke_server import queue_server

        assert queue_server.Handler.search_lookup is queue_server.youtube_search
        assert queue_server.build().search_lookup is queue_server.youtube_search
        faked = queue_server.build(search_lookup=lambda q: [], search_enabled=True)
        assert faked.search_lookup is not queue_server.youtube_search

    def test_the_key_is_never_echoed_to_a_phone(self, monkeypatch, no_network):
        from tools.karaoke_server import queue_server

        monkeypatch.setenv(queue_server.SEARCH_ENV_KEY, "SECRET-KEY-VALUE")
        handler = queue_server.build(
            search_lookup=lambda q: [{"ref": "r0", "title": "t", "channel": "c"}]
        )
        assert handler.search_enabled is True
        for path in ("/api/search?q=x", "/", "/screen", "/api/state"):
            status, text = socketless_get(handler, path)
            assert status == 200
            assert "SECRET-KEY-VALUE" not in text, path


class TestNoKeyIsTodaysPhoneExactly:
    """The degradation pin. A venue with no key must be no worse off.

    Not "mostly the same": the document the phone is served is the document
    it was served before search was written, character for character, minus
    a meta tag that is not there.
    """

    def test_the_served_page_is_byte_for_byte_the_page_it_always_was(self):
        from tools.karaoke_server import queue_server

        on = queue_server.page_html("phone", search=True)
        off = queue_server.page_html("phone")
        # the tag itself, not the word: the page's own comments name the
        # meta in prose, and that prose is in both documents
        assert '<meta name="karaoke-search" content="on">' in on
        assert '<meta name="karaoke-search"' not in off
        assert off == on.replace('<meta name="karaoke-search" content="on">\n', "")

    def test_with_no_key_the_search_box_is_never_revealed(
        self, monkeypatch, no_network
    ):
        from tools.karaoke_server import queue_server

        monkeypatch.delenv(queue_server.SEARCH_ENV_KEY, raising=False)
        handler = queue_server.build(title_lookup=lambda vid: None)
        assert handler.search_enabled is False
        status, page = socketless_get(handler, "/")
        assert status == 200
        assert '<meta name="karaoke-search"' not in page
        assert 'id="searchWrap" hidden' in page  # inert markup, never shown
        # the path that always worked is still the visible one
        assert "Paste a YouTube link, or just type the song" in page

    def test_and_the_endpoint_says_so_in_one_plain_sentence(
        self, monkeypatch, no_network
    ):
        from tools.karaoke_server import queue_server

        monkeypatch.delenv(queue_server.SEARCH_ENV_KEY, raising=False)
        handler = queue_server.build()
        status, body = socketless_get_json(handler, "/api/search?q=sweet")
        assert status == 200 and body["ok"] is False
        assert body["note"] == queue_server.SEARCH_OFF_NOTE
        assert "paste a YouTube link" in body["note"]

    def test_the_operator_is_told_which_night_they_are_running(self, monkeypatch):
        from tools.karaoke_server import queue_server

        monkeypatch.delenv(queue_server.SEARCH_ENV_KEY, raising=False)
        off = queue_server.search_status_line()
        assert "off" in off and queue_server.SEARCH_ENV_KEY in off
        monkeypatch.setenv(queue_server.SEARCH_ENV_KEY, "SECRET-KEY-VALUE")
        on = queue_server.search_status_line()
        assert "ON" in on
        assert "SECRET-KEY-VALUE" not in on  # the fact, never the key


class TestTheSearchBoxOnThePhone:
    @pytest.fixture
    def page(self):
        from tools.karaoke_server import queue_server

        return queue_server.PAGE.read_text(encoding="utf-8")

    def test_the_markup_is_hidden_and_sits_above_the_paste_box(self, page):
        card = page[page.index('id="songCard"') : page.index('id="waitCard"')]
        assert 'id="searchWrap" hidden' in card
        for control in ("searchIn", "searchBtn", "searchResults", "searchNote"):
            assert card.count('id="%s"' % control) == 1, control
        assert card.index('id="searchWrap"') < card.index('id="songIn"')
        assert "Paste a YouTube link, or just type the song" in card

    def test_it_is_revealed_only_when_the_server_says_there_is_a_key(self, page):
        assert 'var SEARCH = meta("karaoke-search") === "on"' in page
        assert "if (SEARCH) {" in page
        assert 'show("searchWrap", true);' in page
        assert page.count('show("searchWrap"') == 1  # nothing else ever shows it

    def test_a_result_and_a_pasted_link_queue_by_the_same_route(self, page):
        assert page.count("function queueSong(") == 1
        assert "queueSong(raw, youtubeId(raw));" in page  # typed or pasted
        assert "queueSong(row.title, row.ref);" in page  # a tapped result
        assert page.count('post("/song"') == 1  # one call site

    def test_every_failure_lands_in_one_plain_sentence(self, page):
        assert "Search isn't available right now" in page
        assert "paste a YouTube link or just type the song title" in page
        assert "Nothing found — try fewer words, or paste a link below." in page
        block = page[
            page.index("// ==== song search") : page.index("// ==== end song search")
        ]
        assert "SEARCH_FALLBACK" in block
        assert ".catch(function () { saySearch(SEARCH_FALLBACK); })" in block

    def test_the_page_asks_its_own_server_and_never_google(self, page):
        assert 'fetch(API + "/search?q=" + encodeURIComponent(query))' in page
        assert "googleapis.com/youtube" not in page
        assert "YOUTUBE_API_KEY" not in page  # the key never reaches the page


class TestTheHostSeesWhoIsWaitingAndNeverWhoIsNext:
    @pytest.fixture
    def page(self):
        from tools.karaoke_server import queue_server

        return queue_server.PAGE.read_text(encoding="utf-8")

    def test_the_list_lives_inside_the_host_panel(self, page):
        panel = page[page.index('id="hostPanel"') : page.index("</aside>")]
        for control in ("hostWaiting", "hostWaitingHead", "hostWaitingList"):
            assert panel.count('id="%s"' % control) == 1, control
        assert 'id="hostWaitingNone" hidden' in panel

    def test_it_is_wired_only_inside_the_host_block(self, page):
        script = page[page.index("<script>\n(function") :]
        start = script.index("// ==== host desk")
        end = script.index("// ==== end host desk")
        block = script[start:end]
        assert 'post("/host/remove"' in block
        assert "state.waiting_list" in block
        outside = script[:start] + script[end:]
        for ident in ("hostWaitingList", "hostWaitingHead", "/host/remove"):
            assert ident not in outside, ident

    def test_the_panel_says_it_is_alphabetical_and_offers_no_reorder(self, page):
        block = page[
            page.index("// ==== host desk") : page.index("// ==== end host desk")
        ]
        assert "A–Z, not the running order" in block
        # the only verb the host has over the list is Remove: no move, no
        # promote, no drag. The order is the secret -- docs/karaoke-rotation.md
        assert 'drop.textContent = "Remove";' in block
        for banned in ("/host/move", "/host/promote", "/host/reorder", "draggable"):
            assert banned not in page, banned
        # and the page never re-sorts the server's alphabetical list
        assert ".sort(" not in block

    def test_the_rows_are_not_rebuilt_under_the_hosts_finger(self, page):
        block = page[
            page.index("// ==== host desk") : page.index("// ==== end host desk")
        ]
        assert "if (signature === listedIds) return;" in block

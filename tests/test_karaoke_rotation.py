"""The random rotation must be fun-random, never unfair-random.

Convict and acquit on planted rooms: every fairness rule has one test where
the pressure is planted and the rule must bite, and one where the room is
clean and the rule must stay out of the way. No clock and no socket -- time
is a float and randomness is seeded.
"""

import math
import random

import pytest

from tools.karaoke_server.rotation import (
    AWAY,
    CALL,
    HOUSE_OFF,
    HOUSE_ON,
    LEFT,
    NEEDS_SONG,
    NO_SHOW,
    ON_DECK,
    PRUNED,
    SINGING,
    SONG_STARTED,
    TIMED_OUT,
    WAITING,
    Rotation,
    RotationConfig,
    RotationError,
)
from tools.karaoke_server import sim


def make(seed=1, profiles=None, **cfg):
    return Rotation(RotationConfig(**cfg), rng=random.Random(seed), profiles=profiles)


def join_with_song(rot, name, now=0.0, duration=240.0):
    singer = rot.join(name, now)
    rot.set_song(singer.id, f"{name}'s song", duration_s=duration, now=now)
    return singer


def drain(rot, now):
    return rot.tick(now)


class TestDrawFairness:
    def test_fewest_songs_tonight_dominates_the_odds(self):
        # planted imbalance: A already sang twice, B not at all
        rot = make(seed=5)
        a = join_with_song(rot, "A")
        b = join_with_song(rot, "B")
        wins = {"A": 0, "B": 0}
        for trial in range(200):
            a.songs_sung, b.songs_sung = 2, 0
            a.misses = b.misses = 0
            a.state = b.state = WAITING
            a.cooldown_left = b.cooldown_left = 0
            rot.call = None
            event = rot.tick(float(trial * 1000))[0]
            wins[rot.singers[event.singer_id].name] += 1
        # weight ratio is 0.35^2 vs 1, so B should take ~89% of draws
        assert wins == {"A": 21, "B": 179}

    def test_a_level_room_splits_the_draws_evenly(self):
        # acquit: identical singers must see no systematic favourite
        rot = make(seed=5)
        a = join_with_song(rot, "A")
        b = join_with_song(rot, "B")
        wins = {"A": 0, "B": 0}
        for trial in range(200):
            a.songs_sung = b.songs_sung = 0
            a.misses = b.misses = 0
            a.state = b.state = WAITING
            a.cooldown_left = b.cooldown_left = 0
            a.joined_at = b.joined_at = -1e6  # nobody is a newcomer
            rot.call = None
            event = rot.tick(float(trial * 1000))[0]
            wins[rot.singers[event.singer_id].name] += 1
        assert wins == {"A": 91, "B": 109}

    def test_the_ceiling_takes_the_longest_waiter_not_the_dice(self):
        rot = make(seed=1)
        unlucky = join_with_song(rot, "Unlucky")
        join_with_song(rot, "Lucky")
        unlucky.misses = rot.ceiling(2)
        event = drain(rot, 0.0)[0]
        assert event.singer_id == unlucky.id
        assert event.detail["by"] == "ceiling"

    def test_no_lottery_win_while_anyone_sits_over_the_ceiling(self):
        rot = make(seed=3)
        singers = [join_with_song(rot, f"S{i}") for i in range(6)]
        singers[4].misses = rot.ceiling(6) + 3
        for _ in range(3):
            event = drain(rot, 0.0)[0]
            assert event.detail["by"] in ("ceiling", "lottery")
            over = [
                s
                for s in rot.singers.values()
                if s.state == WAITING and s.misses >= rot.ceiling(6)
            ]
            if event.detail["by"] == "lottery":
                assert not over
            rot.call = None
            rot.singers[event.singer_id].state = WAITING

    def test_ceiling_scales_with_the_queue_because_flat_cannot_hold(self):
        rot = make()
        assert rot.ceiling(3) == 4  # small room keeps the flat promise
        assert rot.ceiling(15) == 21  # deep queue: 1.5x fair share


class TestCooldownAndNewcomers:
    def test_just_sang_means_sitting_out_the_next_draws(self):
        rot = make(seed=2, cooldown_draws=2)
        a = join_with_song(rot, "A")
        join_with_song(rot, "B")
        join_with_song(rot, "C")
        a.songs_sung, a.cooldown_left = 1, 2  # as if A just left the stage
        for trial in range(2):
            event = drain(rot, trial * 1000.0)[0]
            assert event.singer_id != a.id
            rot.singers[event.singer_id].state = WAITING
            rot.call = None
        assert a.cooldown_left == 0  # suppression served, eligible again

    def test_a_one_person_room_still_gets_to_sing(self):
        # acquit the cooldown: suppression must never silence the room
        rot = make(seed=2, cooldown_draws=2)
        solo = join_with_song(rot, "Solo", duration=100.0)
        drain(rot, 0.0)
        rot.appeared(solo.id, 10.0)
        drain(rot, 200.0)  # song over
        assert solo.songs_sung == 1
        rot.set_song(solo.id, "encore", duration_s=100.0, now=210.0)
        events = drain(rot, 220.0)
        assert [e.kind for e in events] == [CALL]
        assert events[0].singer_id == solo.id

    def test_a_late_arrival_outdraws_an_equal_early_bird(self):
        rot = make(seed=9)
        early = join_with_song(rot, "Early", now=0.0)
        late = join_with_song(rot, "Late", now=10000.0)
        early.joined_at = 0.0
        wins = {"Early": 0, "Late": 0}
        for trial in range(200):
            early.state = late.state = WAITING
            early.misses = late.misses = 0
            rot.call = None
            event = rot.tick(10000.0 + trial)[0]
            wins[rot.singers[event.singer_id].name] += 1
        assert wins["Late"] > wins["Early"]
        assert wins == {"Early": 81, "Late": 119}  # 1.6x boost, pinned


class TestShowingUpOrNot:
    def test_a_no_show_restarts_the_climb_and_counts_a_strike(self):
        rot = make(seed=4)
        flake = join_with_song(rot, "Flake")
        join_with_song(rot, "Other")
        flake.misses = rot.ceiling(2)  # force the draw onto the flake
        call_event = drain(rot, 0.0)[0]
        assert call_event.singer_id == flake.id
        deadline = rot.call.deadline
        events = drain(rot, deadline + 1.0)
        kinds = [e.kind for e in events]
        assert NO_SHOW in kinds and CALL in kinds  # redraw in the same tick
        assert flake.no_shows == 1
        assert flake.misses == 0  # priority is spent by the missed offer

    def test_strike_out_times_you_out_until_you_come_back(self):
        rot = make(seed=4, max_no_shows=2)
        flake = join_with_song(rot, "Flake")
        other = join_with_song(rot, "Other")
        t = 0.0
        while flake.state != AWAY:
            flake.misses = rot.ceiling(2)
            drain(rot, t)
            if rot.call and rot.call.singer_id == flake.id:
                t = rot.call.deadline + 1.0
                drain(rot, t)
            else:  # other singer got drawn; let them vanish too
                rot.call = None
                other.state = WAITING
            t += 1.0
        assert flake.no_shows == 2
        events = drain(rot, t)  # away singers are out of the pool
        assert all(e.singer_id != flake.id for e in events if e.kind == CALL)
        rot.mark_back(flake.id, t + 100.0)
        assert flake.state == WAITING
        assert flake.no_shows == 0  # a fresh chance tonight
        assert rot.profiles["flake"]["no_shows"] == 2  # memory keeps the truth

    def test_away_long_enough_means_you_left(self):
        rot = make(seed=4, away_prune_s=1800.0)
        idler = join_with_song(rot, "Idler")
        rot.mark_away(idler.id, 100.0)
        assert drain(rot, 1899.9) == []
        # exactly at the boundary: the scheduler and the step must agree,
        # or a caller sleeping until next_due() spins forever (found by sim)
        due = rot.next_due(1899.9)
        events = drain(rot, due)
        assert [e.kind for e in events] == [PRUNED]
        assert idler.state == LEFT


class TestTimingAndMemory:
    def test_the_call_goes_out_during_the_outro_and_lands_back_to_back(self):
        rot = make(seed=6)
        first = join_with_song(rot, "First", duration=240.0)
        second = join_with_song(rot, "Second", duration=240.0)
        first.misses = rot.ceiling(2)
        drain(rot, 0.0)
        rot.appeared(first.id, 20.0)  # song runs 20.0 -> 260.0
        lead = rot.lead_s()
        assert drain(rot, 260.0 - lead - 1.0) == []
        call = drain(rot, 260.0 - lead)[0]
        assert call.kind == CALL and call.singer_id == second.id
        rot.appeared(second.id, 250.0)  # walked up during the outro
        events = drain(rot, 260.0)
        started = [e for e in events if e.kind == SONG_STARTED]
        assert started and started[0].at == 260.0  # zero dead air

    def test_an_outro_no_show_is_resolved_the_moment_the_song_ends(self):
        # an outro call always allows until the song's actual end (the
        # floor), so a flake costs the room nothing while music plays and
        # the redraw fires in the same tick the song finishes
        rot = make(seed=6, end_slack_s=0.0)
        first = join_with_song(rot, "First", duration=300.0)
        flake = join_with_song(rot, "Flake")
        backup = join_with_song(rot, "Backup")
        first.misses = rot.ceiling(3)
        drain(rot, 0.0)
        rot.appeared(first.id, 40.0)  # a 40s walk; sings 40.0 -> 340.0
        # the learned 40s walk-up makes the lead (60s + crowd) outrun the
        # grace (60s), so the song's-end floor is the later, binding bound
        flake.misses = rot.ceiling(2)
        outro_at = 340.0 - rot.lead_s()
        call = drain(rot, outro_at)[0]
        assert call.kind == CALL and call.singer_id == flake.id
        assert rot.call.deadline == 340.0  # floored at the song's end
        events = drain(rot, 340.0)
        kinds = [e.kind for e in events]
        assert NO_SHOW in kinds  # the flake struck out exactly at the end
        redraw = [e for e in events if e.kind == CALL]
        assert redraw and redraw[0].singer_id == backup.id  # same tick

    def test_walkups_teach_the_lead_and_the_room_size_stretches_it(self):
        rot = make(seed=7)
        assert rot.lead_s() == 60.0  # no history: the base guess
        singer = join_with_song(rot, "Steady")
        singer.misses = rot.ceiling(1)
        drain(rot, 0.0)
        rot.appeared(singer.id, 30.0)  # an observed 30s walk to the stage
        assert rot.room_walkup_ema == 30.0
        lead_small = rot.lead_s()
        assert lead_small == 45.0  # 30s x 1.5 safety, room too small for crowd
        for i in range(20):
            join_with_song(rot, f"Crowd{i}")
        assert rot.lead_s() > lead_small  # busier room, earlier call
        assert rot.lead_s() <= rot.cfg.lead_max_s

    def test_the_rotation_remembers_a_singer_across_nights(self):
        profiles = {}
        night_one = make(seed=8, profiles=profiles)
        ada = join_with_song(night_one, "Ada")
        ada.misses = night_one.ceiling(1)
        drain(night_one, 0.0)
        night_one.appeared(ada.id, 45.0)
        drain(night_one, 400.0)
        assert profiles["ada"]["songs"] == ["Ada's song"]
        assert profiles["ada"]["nights"] == 1
        assert profiles["ada"]["walkup_ema"] == 45.0

        night_two = make(seed=8, profiles=profiles)
        ada_again = night_two.join("Ada", 0.0)
        assert ada_again.returning
        assert ada_again.walkup_ema == 45.0  # her pace, remembered
        night_two.join("Ada", 10.0)  # rejoin the same night
        assert profiles["ada"]["nights"] == 2  # counted once per night


class TestRefusals:
    def test_bad_input_is_refused_not_repaired(self):
        rot = make()
        singer = rot.join("Ada", 0.0)
        with pytest.raises(RotationError):
            rot.set_song(singer.id, "x", duration_s=4.0)  # not a song
        with pytest.raises(RotationError):
            rot.set_song(singer.id, "x", duration_s=3600.0)  # not karaoke
        with pytest.raises(RotationError):
            rot.set_song(singer.id, "x", source="telepathy")
        with pytest.raises(RotationError):
            rot.set_song(singer.id, "   ")
        with pytest.raises(RotationError):
            rot.join("\x00\x01", 0.0)
        assert singer.song is None

    def test_no_meddling_mid_call(self):
        rot = make()
        singer = join_with_song(rot, "Ada")
        singer.misses = rot.ceiling(1)
        drain(rot, 0.0)
        with pytest.raises(RotationError):
            rot.set_song(singer.id, "swap", duration_s=200.0)
        with pytest.raises(RotationError):
            rot.mark_away(singer.id, 1.0)

    def test_house_music_covers_every_gap_and_never_flickers(self):
        # the room is never silent: music is assumed playing when the night
        # opens, fades for a singer, and returns the moment the stage is bare
        rot = make(seed=1)
        assert rot.house_on  # the pub had music on before we booted
        first = join_with_song(rot, "First", duration=180.0)
        second = join_with_song(rot, "Second", duration=180.0)
        first.misses = rot.ceiling(2)
        drain(rot, 0.0)
        events = rot.appeared(first.id, 10.0)
        assert [e.kind for e in events] == [HOUSE_OFF, SONG_STARTED]
        assert not rot.house_on
        # second is called during the outro and walks up in time
        outro = 190.0 - rot.lead_s()
        drain(rot, outro)
        rot.appeared(second.id, 185.0)
        events = drain(rot, 190.0)
        kinds = [e.kind for e in events]
        assert SONG_STARTED in kinds
        assert HOUSE_ON not in kinds and HOUSE_OFF not in kinds  # no flicker
        assert not rot.house_on  # music stayed down across the handover
        # second finishes with nobody on deck: the music comes straight up
        events = drain(rot, 400.0)
        assert [e.kind for e in events][-1] == HOUSE_ON
        assert rot.house_on

    def test_a_no_show_gap_stays_covered(self):
        # a called singer who never appears must not leave the music down
        rot = make(seed=1)
        first = join_with_song(rot, "First", duration=120.0)
        flake = join_with_song(rot, "Flake")
        join_with_song(rot, "Backup")
        first.misses = rot.ceiling(3)
        drain(rot, 0.0)
        rot.appeared(first.id, 10.0)  # sings 10.0 -> 130.0
        assert not rot.house_on
        flake.misses = rot.ceiling(2)
        drain(rot, 130.0 - rot.lead_s())  # the flake is called in the outro
        assert rot.call and rot.call.singer_id == flake.id
        assert not rot.call.solo  # a backup is in the draw: a timed call
        drain(rot, 135.0)  # song over, the flake still walking: music back up
        assert rot.house_on
        events = drain(rot, rot.call.deadline + 1.0)
        assert NO_SHOW in [e.kind for e in events]
        assert rot.house_on  # struck out; the room keeps its music

    def test_finishing_prompts_for_the_next_song(self):
        rot = make(seed=1)
        singer = join_with_song(rot, "Ada", duration=180.0)
        singer.misses = rot.ceiling(1)
        drain(rot, 0.0)
        rot.appeared(singer.id, 10.0)
        events = drain(rot, 200.0)
        assert NEEDS_SONG in [e.kind for e in events]
        assert singer.song is None  # ineligible until they choose again
        assert drain(rot, 300.0) == []


class TestSoloMode:
    """One singer in the draw is nobody to move on to, so no deadline.

    Found by the owner testing alone on one PC: queue a song, get called
    at once, miss the walk-up twice, and be told "we called you twice" by
    a room containing nobody else.
    """

    def test_a_lone_singer_is_called_solo_and_never_struck_out(self):
        rot = make(seed=1)
        solo = join_with_song(rot, "Solo", duration=120.0)
        events = drain(rot, 0.0)
        assert [e.kind for e in events] == [CALL]
        assert events[0].detail["solo"] is True
        assert events[0].detail["deadline"] is None  # nothing to count down
        assert rot.call.solo and rot.call.deadline == math.inf
        # however long the clock runs, the call stands and no strike lands
        for hours in (1, 6, 48):
            assert drain(rot, hours * 3600.0) == []
            assert rot.call is not None and rot.call.singer_id == solo.id
            assert solo.no_shows == 0 and solo.state == ON_DECK
        assert rot.next_due(1000.0) is None  # nothing to wake up for
        # and they sing the moment they appear, at their own pace
        events = rot.appeared(solo.id, 48 * 3600.0 + 5.0)
        assert [e.kind for e in events] == [HOUSE_OFF, SONG_STARTED]
        assert solo.state == SINGING and rot.call is None

    def test_a_second_singer_turns_the_solo_call_into_a_timed_one(self):
        rot = make(seed=1)
        solo = join_with_song(rot, "Solo")
        drain(rot, 0.0)
        assert rot.call.solo
        join_with_song(rot, "Second", now=500.0)
        events = drain(rot, 500.0)
        assert events == []  # same singer, same call: no second announcement
        assert rot.call.singer_id == solo.id
        assert not rot.call.solo
        assert rot.call.deadline == 500.0 + rot.cfg.grace_base_s  # a fresh clock
        assert rot.next_due(500.0) == rot.call.deadline
        # and from here it is an ordinary call: miss it and the draw moves on
        events = drain(rot, rot.call.deadline + 1.0)
        kinds = [e.kind for e in events]
        assert NO_SHOW in kinds and CALL in kinds
        assert solo.no_shows == 1

    def test_the_conversion_keeps_the_songs_end_floor_during_an_outro(self):
        rot = make(seed=1, end_slack_s=20.0)
        first = join_with_song(rot, "First", duration=300.0)
        first.misses = rot.ceiling(1)
        drain(rot, 0.0)
        rot.appeared(first.id, 10.0)  # sings 10.0 -> 310.0
        second = join_with_song(rot, "Second", now=20.0)
        outro = 310.0 - rot.lead_s()
        call = drain(rot, outro)[0]
        assert call.singer_id == second.id and call.detail["solo"] is True
        join_with_song(rot, "Third", now=outro + 1.0)
        drain(rot, outro + 1.0)
        assert not rot.call.solo
        assert rot.call.deadline >= 310.0 + 20.0  # never shorter than the outro

    def test_a_room_of_two_never_produces_a_solo_call(self):
        rot = make(seed=3)
        a = join_with_song(rot, "A")
        b = join_with_song(rot, "B")
        for trial in range(50):
            a.state = b.state = WAITING
            a.misses = b.misses = 0
            rot.call = None
            event = drain(rot, float(trial * 1000))[0]
            assert event.detail["solo"] is False
            assert not rot.call.solo
            assert rot.call.deadline < math.inf

    def test_next_due_never_returns_inf(self):
        rot = make(seed=1)
        solo = join_with_song(rot, "Solo", duration=100.0)
        drain(rot, 0.0)
        assert rot.next_due(0.0) is None
        rot.mark_away(join_with_song(rot, "Idler").id, 5.0)
        due = rot.next_due(5.0)
        assert due == 5.0 + rot.cfg.away_prune_s  # the prune, not the call
        assert math.isfinite(due)
        assert solo.state == ON_DECK


class TestHostSkip:
    def test_skip_is_a_no_show_now_and_the_draw_moves_on(self):
        rot = make(seed=4)
        flake = join_with_song(rot, "Flake")
        backup = join_with_song(rot, "Backup")
        flake.misses = rot.ceiling(2)
        drain(rot, 0.0)
        assert rot.call.singer_id == flake.id
        event = rot.skip_call(5.0)
        assert event.kind == NO_SHOW and event.singer_id == flake.id
        assert flake.no_shows == 1 and flake.state == WAITING
        assert rot.call is None
        backup.misses = rot.ceiling(2)  # so the redraw is not the dice
        redraw = drain(rot, 5.0)[0]
        assert redraw.kind == CALL and redraw.singer_id == backup.id

    def test_skipping_a_solo_call_strikes_like_any_other(self):
        rot = make(seed=4, max_no_shows=2)
        solo = join_with_song(rot, "Solo")
        drain(rot, 0.0)
        rot.skip_call(10.0)
        assert solo.no_shows == 1
        drain(rot, 10.0)  # drawn again at once: still the only singer
        assert rot.call.singer_id == solo.id and rot.call.solo
        event = rot.skip_call(20.0)
        assert event.kind == TIMED_OUT and solo.state == AWAY

    def test_skip_refuses_when_there_is_nothing_to_skip(self):
        rot = make(seed=4)
        with pytest.raises(RotationError):
            rot.skip_call(0.0)

    def test_skip_refuses_once_the_singer_is_at_the_stage(self):
        """Skip is for the walk-up that never came, not for a performance."""
        rot = make(seed=4)
        singer = join_with_song(rot, "Ada")
        drain(rot, 0.0)
        rot.appeared(singer.id, 5.0)  # on stage now, not called
        with pytest.raises(RotationError):
            rot.skip_call(6.0)

    def test_the_skipped_singer_sits_out_the_very_next_draw(self):
        """A button reading "draw again" must not return the same name.

        The lottery alone did exactly that in 32% of seeded two-singer
        rooms, which is honest randomness and a broken-looking button.
        Every seed here has to move the mic, so the assertion is the rule
        and not one lucky draw.
        """
        for seed in range(40):
            rot = make(seed=seed)
            join_with_song(rot, "Ada")
            join_with_song(rot, "Lin")
            drain(rot, 0.0)
            skipped = rot.call.singer_id
            rot.skip_call(5.0)
            drain(rot, 5.0)
            assert rot.call.singer_id != skipped, f"seed {seed} handed it back"

    def test_but_the_only_singer_left_is_still_called(self):
        """Sitting out is a courtesy, never a room with nobody on stage."""
        rot = make(seed=4)
        alone = join_with_song(rot, "Alone")
        drain(rot, 0.0)
        rot.skip_call(5.0)
        drain(rot, 5.0)
        assert rot.call.singer_id == alone.id and rot.call.solo

    def test_sitting_out_lasts_exactly_one_draw(self):
        """The skipped singer is back in the lottery from the draw after."""
        rot = make(seed=4)
        ada = join_with_song(rot, "Ada")
        lin = join_with_song(rot, "Lin")
        drain(rot, 0.0)
        first = rot.call.singer_id
        rot.skip_call(5.0)
        drain(rot, 5.0)
        skipped_second = rot.call.singer_id
        assert skipped_second != first
        rot.skip_call(10.0)
        drain(rot, 10.0)
        # only two in the room, so the draw after has to come back around
        assert rot.call.singer_id == first
        assert {ada.id, lin.id} == {first, skipped_second}


class TestSimulatedNights:
    def test_a_planted_hog_room_is_held_level(self):
        report = sim.run_night("eager", seed=3)
        assert max(report.songs_per_singer) - min(report.songs_per_singer) <= 2
        assert report.starved == []

    def test_a_flaky_crowd_costs_strikes_not_silence(self):
        report = sim.run_night("mixed", seed=3)
        assert report.no_shows > 0  # the flakes did flake
        assert report.dead_air_s < 900.0  # under 4% of the night silent
        assert report.starved == []

    def test_the_same_seed_replays_the_same_night(self):
        # the regression pin: numbers move only when behaviour moves
        report = sim.run_night("mixed", seed=7)
        assert (
            report.songs,
            report.served,
            report.no_shows,
            round(report.dead_air_s, 1),
            round(report.silent_s, 1),
        ) == (61, 18, 9, 471.6, 0.0)

    def test_the_acquit_room_passes_without_the_ceiling_running_it(self):
        report = sim.run_night("quiet", seed=2)
        assert report.starved == []
        assert report.ceiling_share() < 0.10

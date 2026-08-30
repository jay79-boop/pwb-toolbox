"""The random rotation must be fun-random, never unfair-random.

Convict and acquit on planted rooms: every fairness rule has one test where
the pressure is planted and the rule must bite, and one where the room is
clean and the rule must stay out of the way. No clock and no socket -- time
is a float and randomness is seeded.
"""

import random

import pytest

from tools.karaoke_server.rotation import (
    AWAY,
    CALL,
    LEFT,
    NEEDS_SONG,
    NO_SHOW,
    PRUNED,
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
        ) == (61, 18, 9, 471.6)

    def test_the_acquit_room_passes_without_the_ceiling_running_it(self):
        report = sim.run_night("quiet", seed=2)
        assert report.starved == []
        assert report.ceiling_share() < 0.10

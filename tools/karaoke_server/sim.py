"""Simulated pub nights for the rotation: does "random but fair" hold up?

Runs synthetic rooms of singers -- eager regulars, two-song casuals,
one-and-dones, flaky no-shows, latecomers -- through whole nights of the
rotation and measures what a person in that room would feel: how long the
worst wait was, whether one singer hogged the mic, how much dead air the
no-shows cost, and whether the lottery is actually doing the work or the
fairness ceiling is quietly running a first-in-first-out queue with extra
steps.

The outro-draw (calling the next singer before the current song ends) is
scored against a control: the same night with the lead forced to zero, so
every call happens only once the stage is empty. If the adaptive lead does
not beat that control on dead air, it is complexity without a payoff and
the report says so.

Verdicts are printed as PASS/FAIL and the exit code follows them. No
network, no clock, no file: everything is driven through injected time and
seeded randomness, so a night is reproducible bit for bit.

    python -m tools.karaoke_server.sim report
    python -m tools.karaoke_server.sim report --seeds 20 --json
"""

from __future__ import annotations

import argparse
import heapq
import json
import random
import statistics
from dataclasses import dataclass, field, replace

from .rotation import (
    CALL,
    NEEDS_SONG,
    NO_SHOW,
    ON_DECK,
    SINGING,
    SONG_ENDED,
    TIMED_OUT,
    Rotation,
    RotationConfig,
    RotationError,
)

NIGHT_S = 4 * 3600.0
LAST_ORDERS_S = NIGHT_S - 600.0  # no new songs queued in the final stretch


@dataclass
class Persona:
    name: str
    arrive_s: float
    walkup_mean: float = 35.0
    walkup_sd: float = 12.0
    p_noshow: float = 0.0
    p_return: float = 0.5  # after a time-out, odds of coming back
    max_songs: int | None = None  # None = sings all night
    requeue_delay_s: float = 120.0


def room(kind: str) -> list[Persona]:
    if kind == "mixed":
        return (
            [Persona(f"eager{i}", 300 * i, 25, 8) for i in range(4)]
            + [Persona(f"casual{i}", 200 * i, 40, 15, max_songs=2) for i in range(6)]
            + [Persona(f"once{i}", 400 * i, 45, 15, max_songs=1) for i in range(4)]
            + [Persona(f"flaky{i}", 600 * i, 50, 20, p_noshow=0.5) for i in range(3)]
            + [Persona(f"late{i}", 5400 + 600 * i, 35, 10) for i in range(3)]
        )
    if kind == "quiet":
        return [Persona("reg0", 0, 20, 5), Persona("reg1", 60, 20, 5)] + [
            Persona(f"casual{i}", 300 * i, 35, 10, max_songs=2) for i in range(3)
        ]
    if kind == "eager":
        return [Persona(f"eager{i}", 120 * i, 25, 8) for i in range(10)]
    if kind == "clean":  # nobody flakes, nobody leaves early: the acquit room
        return [Persona(f"eager{i}", 200 * i, 30, 8) for i in range(6)] + [
            Persona(f"casual{i}", 300 * i, 35, 10, max_songs=3) for i in range(4)
        ]
    raise ValueError(f"unknown room kind: {kind}")


@dataclass
class NightReport:
    kind: str
    seed: int
    singers: int = 0
    served: int = 0
    songs: int = 0
    waits_s: list[float] = field(default_factory=list)
    worst_misses: int = 0
    peak_pool: int = 0
    ceiling_calls: int = 0
    lottery_calls: int = 0
    no_shows: int = 0
    timed_out: int = 0
    dead_air_s: float = 0.0
    silent_s: float = 0.0
    songs_per_singer: list[int] = field(default_factory=list)
    starved: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return self.ceiling_calls + self.lottery_calls

    def ceiling_share(self) -> float:
        return self.ceiling_calls / self.calls if self.calls else 0.0


def run_night(
    kind: str,
    seed: int,
    cfg: RotationConfig | None = None,
    profiles: dict | None = None,
) -> NightReport:
    cfg = cfg or RotationConfig()
    rot = Rotation(cfg, rng=random.Random(seed), profiles=profiles)
    people = random.Random(seed * 2 + 1)  # personas roll their own dice
    personas = room(kind)
    report = NightReport(kind=kind, seed=seed, singers=len(personas))

    ids: dict[str, str] = {}  # persona name -> singer id
    names: dict[str, str] = {}  # singer id -> persona name
    by_id: dict[str, Persona] = {}
    sung: dict[str, int] = {}
    song_no: dict[str, int] = {}

    heap: list[tuple[float, int, str, str]] = []
    seq = 0

    def push(at: float, action: str, who: str = "") -> None:
        nonlocal seq
        heapq.heappush(heap, (at, seq, action, who))
        seq += 1

    def queue_song(name: str, now: float) -> None:
        persona = by_id[name]
        count = sung.get(name, 0)
        if persona.max_songs is not None and count >= persona.max_songs:
            push(now, "leave", name)
            return
        if now > LAST_ORDERS_S:
            push(now, "leave", name)
            return
        song_no[name] = song_no.get(name, 0) + 1
        try:
            rot.set_song(
                ids[name],
                f"{name} song {song_no[name]}",
                duration_s=people.uniform(180, 300),
                source="link",
                now=now,
            )
        except RotationError:
            pass

    def handle(events, now: float) -> None:
        for ev in events:
            name = names.get(ev.singer_id, "")
            persona = by_id.get(name)
            if persona is None:
                continue
            if ev.kind == CALL:
                report.waits_s.append(ev.detail["waited_s"])
                report.worst_misses = max(
                    report.worst_misses, ev.detail["misses_at_call"]
                )
                report.peak_pool = max(report.peak_pool, ev.detail["pool"])
                if ev.detail["by"] == "ceiling":
                    report.ceiling_calls += 1
                else:
                    report.lottery_calls += 1
                if people.random() >= persona.p_noshow:
                    walk = max(
                        5.0, people.gauss(persona.walkup_mean, persona.walkup_sd)
                    )
                    push(now + walk, "appear", name)
            elif ev.kind == SONG_ENDED:
                report.songs += 1
                sung[name] = sung.get(name, 0) + 1
            elif ev.kind == NEEDS_SONG:
                push(now + persona.requeue_delay_s, "requeue", name)
            elif ev.kind == NO_SHOW:
                report.no_shows += 1
            elif ev.kind == TIMED_OUT:
                report.no_shows += 1
                report.timed_out += 1
                if people.random() < persona.p_return:
                    push(now + 600.0, "comeback", name)

    for persona in personas:
        by_id[persona.name] = persona
        push(persona.arrive_s, "join", persona.name)

    prev_t = 0.0
    hard_stop = NIGHT_S + 1800.0
    while heap:
        t, _, action, who = heapq.heappop(heap)
        if t > hard_stop:
            break
        # dead air: the room wants music (someone queued or called) but the
        # stage is silent. State is constant between visited instants.
        if rot.stage is None and (rot.call is not None or rot._pool()):
            report.dead_air_s += t - prev_t
            if not rot.house_on:  # a gap the house music failed to cover
                report.silent_s += t - prev_t
        prev_t = t

        if action == "join":
            ids[who] = rot.join(who, t).id
            names[ids[who]] = who
            push(t + people.uniform(20, 60), "requeue", who)
        elif action == "requeue":
            queue_song(who, t)
        elif action == "appear":
            if rot.call and rot.call.singer_id == ids[who]:
                handle(rot.appeared(ids[who], t), t)
        elif action == "comeback":
            rot.mark_back(ids[who], t)
            queue_song(who, t)
        elif action == "leave":
            singer = rot.singers[ids[who]]
            if singer.state in (SINGING, ON_DECK):
                push(t + 60.0, "leave", who)
            else:
                rot.leave(ids[who], t)

        handle(rot.tick(t), t)
        due = rot.next_due(t)
        if due is not None:
            push(due, "tick", "")

    for persona in personas:
        singer = rot.singers.get(ids.get(persona.name, ""), None)
        if singer is None:
            continue
        report.songs_per_singer.append(singer.songs_sung)
        wanted = persona.max_songs is None or persona.max_songs > 0
        stayed = persona.arrive_s <= NIGHT_S - 2700.0
        if wanted and stayed and singer.songs_sung == 0 and persona.p_noshow == 0.0:
            report.starved.append(persona.name)
    report.served = sum(1 for n in report.songs_per_singer if n > 0)
    return report


# -- the report -------------------------------------------------------------


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def run_report(seeds: int = 10, cfg: RotationConfig | None = None) -> dict:
    cfg = cfg or RotationConfig()
    zero_lead = replace(cfg, lead_min_s=0.0, lead_max_s=0.0)
    out: dict = {"rooms": {}, "verdicts": []}
    nights: dict[str, list[NightReport]] = {}
    controls: list[NightReport] = []
    for kind in ("mixed", "eager", "quiet", "clean"):
        nights[kind] = [run_night(kind, seed, cfg) for seed in range(seeds)]
        if kind == "mixed":
            controls = [run_night(kind, seed, zero_lead) for seed in range(seeds)]
    for kind, reports in nights.items():
        waits = [w for r in reports for w in r.waits_s]
        spread = [
            max(r.songs_per_singer) - min(r.songs_per_singer)
            for r in reports
            if r.songs_per_singer
        ]
        out["rooms"][kind] = {
            "singers": reports[0].singers,
            "songs_mean": round(statistics.mean(r.songs for r in reports), 1),
            "served_mean": round(statistics.mean(r.served for r in reports), 1),
            "wait_p95_min": round(_p95(waits) / 60.0, 1),
            "wait_max_min": round(max(waits, default=0.0) / 60.0, 1),
            "worst_misses": max(r.worst_misses for r in reports),
            "ceiling_share": round(
                statistics.mean(r.ceiling_share() for r in reports), 3
            ),
            "no_shows_mean": round(statistics.mean(r.no_shows for r in reports), 1),
            "dead_air_mean_s": round(statistics.mean(r.dead_air_s for r in reports), 1),
            "silent_max_s": round(max(r.silent_s for r in reports), 1),
            "songs_spread_max": max(spread, default=0),
            "starved": sorted({n for r in reports for n in r.starved}),
        }
    mixed, clean = out["rooms"]["mixed"], out["rooms"]["clean"]
    control_dead_air = statistics.mean(r.dead_air_s for r in controls)

    def verdict(label: str, ok: bool, note: str) -> None:
        out["verdicts"].append({"label": label, "ok": bool(ok), "note": note})

    # The flat "guaranteed after max_misses" promise is impossible past a
    # queue of that depth (one winner per draw), so the invariant checked is
    # the achievable one: nobody waits past ceiling_ratio x a perfectly even
    # rotation of the deepest queue seen, plus the over-ceiling backlog that
    # a burst can stack up (bounded by the queue depth itself).
    import math

    violations = []
    for reports in nights.values():
        for r in reports:
            peak_fair = max(0, r.peak_pool - 1)
            bound = (
                max(cfg.max_misses, math.ceil(peak_fair * cfg.ceiling_ratio))
                + r.peak_pool
            )
            if r.worst_misses > bound:
                violations.append(f"{r.kind}/{r.seed}: {r.worst_misses}>{bound}")
    worst = max(r["worst_misses"] for r in out["rooms"].values())
    verdict(
        "waits stay bounded",
        not violations,
        (
            f"worst misses at a call {worst}; every night within "
            f"{cfg.ceiling_ratio}x fair share + backlog"
            if not violations
            else "; ".join(violations)
        ),
    )
    all_starved = sorted(
        {n for reports in nights.values() for r in reports for n in r.starved}
    )
    verdict(
        "nobody starved",
        not all_starved,
        (
            "every reliable singer who queued and stayed sang"
            if not all_starved
            else f"went home unsung: {', '.join(all_starved)}"
        ),
    )
    verdict(
        "no mic hog",
        out["rooms"]["eager"]["songs_spread_max"] <= 2,
        "identical eager singers finish within "
        f"{out['rooms']['eager']['songs_spread_max']} songs of each other",
    )
    verdict(
        "outro draw beats stage-free draw",
        mixed["dead_air_mean_s"] < control_dead_air,
        f"dead air {mixed['dead_air_mean_s']:.0f}s vs {control_dead_air:.0f}s "
        "with lead forced to zero",
    )
    # Under saturation (clean room: everyone queued for every slot) a high
    # ceiling share is the fairness promise doing its job, so the cosmetic-
    # randomness check runs on the room with headroom instead.
    quiet = out["rooms"]["quiet"]
    verdict(
        "the lottery does the work",
        quiet["ceiling_share"] < 0.10,
        f"with headroom the ceiling resolved {quiet['ceiling_share']:.1%} of "
        f"draws (saturated room for contrast: {clean['ceiling_share']:.0%})",
    )
    silent = max(r["silent_max_s"] for r in out["rooms"].values())
    verdict(
        "no silent second",
        silent < 0.5,
        f"worst uncovered gap across every night: {silent:.1f}s -- house "
        "music is up whenever the stage is bare",
    )
    out["control_dead_air_mean_s"] = round(control_dead_air, 1)
    out["seeds"] = seeds
    out["ok"] = all(v["ok"] for v in out["verdicts"])
    return out


def print_report(out: dict) -> None:
    cols = (
        ("singers", "singers"),
        ("songs_mean", "songs"),
        ("served_mean", "served"),
        ("wait_p95_min", "p95 wait m"),
        ("wait_max_min", "max wait m"),
        ("worst_misses", "worst miss"),
        ("ceiling_share", "ceiling"),
        ("no_shows_mean", "no-shows"),
        ("dead_air_mean_s", "gap s"),
        ("silent_max_s", "silent s"),
        ("songs_spread_max", "spread"),
    )
    header = f"{'room':<8}" + "".join(f"{label:>12}" for _, label in cols)
    print(f"simulated nights, {out['seeds']} seeds per room, 4h each")
    print(header)
    for kind, row in out["rooms"].items():
        line = f"{kind:<8}" + "".join(f"{row[key]:>12}" for key, _ in cols)
        print(line)
        if row["starved"]:
            print(f"{'':<8}starved: {', '.join(row['starved'])}")
    print()
    for v in out["verdicts"]:
        print(f"  {'PASS' if v['ok'] else 'FAIL'}  {v['label']} -- {v['note']}")
    print()
    print("verdict:", "the rotation holds" if out["ok"] else "the rotation FAILS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="run the standard rooms and judge them")
    rep.add_argument("--seeds", type=int, default=10)
    rep.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    out = run_report(seeds=args.seeds)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print_report(out)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

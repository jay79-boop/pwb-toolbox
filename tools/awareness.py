#!/usr/bin/env python3
"""The situational awareness layer: seven questions, one assembly, no stored state.

The owner asked for a layer that answers, at any moment::

    what is happening now / why / what is changing / what is likely next /
    what is connected to it / what deserves attention / what action is safest

Those seven lines are a rendering spec, not an architecture. Underneath they
demand three different machines: *now* and *connected* are mechanical reads;
*changing* and *likely next* are impossible without history, because you cannot
diff against nothing; *deserves attention* and *safest action* are judgement.

**This module is the first two and the evidence for the third. It does not
conclude.** The answers are delivered inside a Claude session -- the session
start catch-up, and on demand -- so the reasoner is already present and free.
A tool that also guessed at *why* would be a second opinion nobody asked for,
and an untestable one. So the core assembles evidence and refuses to editorialise
over it, in the same way ``spend_watch`` refuses a burn rate from one snapshot.

**Nothing here stores current state**, which is the rule this repository already
has in writing and the reason a dashboard was retired on 2026-08-29
(``docs/decisions/2026-08-29-retiring-the-live-work-dashboard.md``). What it
records instead is *observations*: "at 14:03, the run log's newest record for
``journal`` was a failure" is true forever. "The current branch is X" is false
in an hour. Deltas come from diffing a fresh derivation against that log, so no
line in this system claims to be current except at the moment it is asked.

Usage::

    python tools/awareness.py brief          # the seven answers, for a session
    python tools/awareness.py brief --short  # the catch-up form, <= 6 lines
    python tools/awareness.py brief --json
    python tools/awareness.py record         # append this moment to the log
    python tools/awareness.py sources        # what it can see, and what it cannot

The first domain wired was the agent fleet and this repository -- chosen because
it was the only one whose live sources a cloud session could reach and verify end
to end. ``desk`` and ``content`` followed, and neither is read from this process:
the desk's feeds are on the owner's machine and content's credentials are MCP
connectors only a session can call, so both arrive as redacted signals committed
to git by whoever *can* reach them. See ``tools/desk_signal.py`` and
``tools/content_signal.py``. The promise held -- both adapters dropped in beside
``observe_jobs`` and the assembly did not change. ``business`` is still unwired
and is still named out loud as a blind spot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Iterable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools import content_signal, desk_signal  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "awareness"
DEFAULT_LOG = LOG_DIR / "observations.jsonl"

# The three things the owner chose to be interrupted for, 2026-09-02. A
# thresholds trigger was offered and deliberately declined -- false alarms are
# how alerting dies, and none of these three needs a number picked out of the
# air to fire.
TRIGGERS = ("stopped", "money", "blocking")

# Ranked worst-first. `act` means something is not working and will not start
# working on its own; `watch` is real but not yet costing anything.
SEVERITIES = ("act", "watch", "info")

# What clears a failure streak. `partial` is in here deliberately: runlog's
# vocabulary is (failed, partial, skipped, ok), and a run that did some of its
# job is not a run that did nothing. Counting `partial` as failure turned
# premarket's real streak of 3 into a reported 8 on the first pass -- a layer
# that overstates is as useless as one that misses, and faster to stop reading.
STREAK_BREAKS = {"ok", "partial"}

# `skipped` is transparent *between* failures -- a holiday in the middle of a
# broken week is neither a failure nor a recovery, and runlog.py makes that
# distinction load-bearing. It is NOT transparent at the head: a job whose
# newest record is a skip is dormant, and reporting it as failing describes a
# run that happened weeks ago as if it were this morning's.
STREAK_TRANSPARENT = {"skipped"}

# A blocker seen in this many separate runs and never cleared has stopped being
# a bad day and started being a decision nobody has made.
BLOCKING_AFTER_RUNS = 3

# Two consecutive failures is the point at which "yesterday was bad" becomes
# "this is not going to fix itself". One is noise; the desk agent's own history
# shows a job reaching seven.
STOPPED_AFTER_FAILURES = 2


# ---------------------------------------------------------------------------
# The unit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One fact, from one source, true at one moment.

    An observation is never revised. If the world changes, a later observation
    disagrees with this one and the disagreement *is* the answer to "what is
    changing". That is the whole reason this is not a state file.

    ``entity`` is a stable id (``job:journal``, ``blocker:desk-levels-...``) so
    two observations taken a day apart can be matched up. ``depends_on`` names
    other entities and is what makes "what is connected to it" answerable
    without a hand-maintained graph.
    """

    domain: str
    entity: str
    summary: str
    at: str  # ISO 8601; when the *observed thing* happened, not when we looked
    severity: str = "info"
    trigger: str = ""
    evidence: str = ""
    detail: str = ""
    depends_on: tuple[str, ...] = ()
    metrics: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity {self.severity!r} not in {SEVERITIES}")
        if self.trigger and self.trigger not in TRIGGERS:
            raise ValueError(f"trigger {self.trigger!r} not in {TRIGGERS}")

    def as_dict(self) -> dict:
        out = asdict(self)
        out["depends_on"] = list(self.depends_on)
        out["metrics"] = {k: v for k, v in self.metrics}
        return out

    @staticmethod
    def from_dict(raw: dict) -> "Observation":
        metrics = raw.get("metrics") or {}
        if isinstance(metrics, dict):
            metrics = tuple(sorted((str(k), float(v)) for k, v in metrics.items()))
        else:
            metrics = tuple((str(k), float(v)) for k, v in metrics)
        return Observation(
            domain=str(raw.get("domain", "")),
            entity=str(raw.get("entity", "")),
            summary=str(raw.get("summary", "")),
            at=str(raw.get("at", "")),
            severity=str(raw.get("severity", "info")),
            trigger=str(raw.get("trigger", "")),
            evidence=str(raw.get("evidence", "")),
            detail=str(raw.get("detail", "")),
            depends_on=tuple(str(d) for d in raw.get("depends_on", ()) or ()),
            metrics=metrics,
        )


@dataclass(frozen=True)
class Change:
    """One entity that reads differently than it did last time we looked."""

    entity: str
    was: str
    now: str
    since: str


@dataclass(frozen=True)
class Projection:
    """Something that follows from a rule, not from a hunch.

    Every projection names the rule that produced it. There is deliberately no
    path in this module that produces one without a rule -- see
    ``project`` for what it refuses.
    """

    entity: str
    expectation: str
    rule: str
    when: str = ""


@dataclass(frozen=True)
class Action:
    """A next step, and whether this layer is allowed to propose it.

    ``safe`` is not a comment. An action is safe only if it is reversible, moves
    no money, and needs no judgement the layer does not have. Everything else is
    routed to the owner with the reason attached, rather than recommended.
    """

    entity: str
    step: str
    safe: bool
    why: str


@dataclass
class Picture:
    """The seven answers, assembled. Rendered, never stored."""

    taken: str
    now: list[Observation] = field(default_factory=list)
    changing: list[Change] = field(default_factory=list)
    no_history: bool = False
    projections: list[Projection] = field(default_factory=list)
    connections: dict = field(default_factory=dict)
    attention: list[Observation] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    blind: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sources -- pure. Everything they need is passed in.
# ---------------------------------------------------------------------------


def _iso(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def failure_streak(outcomes: Sequence[str]) -> tuple[int, str]:
    """``(streak, head_state)`` from a newest-first outcome list.

    ``head_state`` is one of ``failing``, ``dormant`` or ``clean``. The head is
    read before the streak because a job whose newest record is ``skipped`` is
    dormant however bad its history: the alerts job was switched off after 25
    runs, and calling its last real failure -- a fortnight old -- "its last run"
    was the first false alarm this tool produced.
    """
    if not outcomes:
        return 0, "clean"
    if outcomes[0] in STREAK_TRANSPARENT:
        return 0, "dormant"
    streak = 0
    for outcome in outcomes:
        if outcome in STREAK_BREAKS:
            break
        if outcome in STREAK_TRANSPARENT:
            continue
        streak += 1
    return streak, ("failing" if streak else "clean")


def live_blockers(
    records: Sequence[dict], enabled: set[str] | None = None
) -> dict[str, dict]:
    """Blocker slugs still present in the newest run of a job that still runs.

    Two filters, and both were added after the tool convicted something that
    was already fixed. A blocker is *live* only if it appears in some job's
    **latest** record -- ``journal-path-outside-session-working-directory``
    killed five runs and was explicitly closed on 08-31, and counting its
    history reported it as blocking today. And a blocker on a job that is
    switched off blocks nothing: ``no-alerts-configured-on-agent-login`` is
    real, and the job it stops has been disabled since 08-29 on purpose.

    Recurrence is still counted across the whole log, because "how long has
    this been going on" is the part worth knowing. Only the *liveness* gate
    reads the head.
    """
    latest: dict[str, dict] = {}
    for raw in records:
        job = str(raw.get("job", "")).strip()
        if not job:
            continue
        stamp = str(raw.get("finished", "")) or str(raw.get("started", ""))
        if job not in latest or stamp >= (
            str(latest[job].get("finished", "")) or str(latest[job].get("started", ""))
        ):
            latest[job] = raw

    def runs_here(job: str) -> bool:
        return enabled is None or job in enabled

    live: dict[str, dict] = {}
    for job, raw in latest.items():
        if not runs_here(job):
            continue
        for b in raw.get("blockers") or []:
            key = str(b.get("key", ""))
            if not key:
                continue
            entry = live.setdefault(
                key,
                {
                    "detail": str(b.get("detail", "")),
                    "jobs": set(),
                    "runs": 0,
                    "at": "",
                },
            )
            entry["jobs"].add(f"job:{job}")

    for raw in records:
        stamp = str(raw.get("finished", "")) or str(raw.get("started", ""))
        for b in raw.get("blockers") or []:
            key = str(b.get("key", ""))
            entry = live.get(key)
            if entry is None:
                continue
            entry["runs"] += 1
            if stamp > entry["at"]:
                entry["at"] = stamp
            if not entry["detail"]:
                entry["detail"] = str(b.get("detail", ""))
    return live


def observe_jobs(
    records: Sequence[dict],
    now: dt.datetime,
    jobs: Sequence["ScheduledJob"] = (),
) -> list[Observation]:
    """Job health from the desk agent's run log.

    ``records`` are raw ``runs.jsonl`` dicts, oldest first, exactly as
    ``runlog.read_records`` yields them. ``jobs`` is the scheduler table, used
    only to know which jobs still run -- pass nothing and every job is treated
    as live. Nothing here reads a file or a clock it was not handed.

    Two things are derived and they fail independently: a job's failure streak,
    and a blocker that is still live and keeps coming back.
    """
    if not records:
        return []

    enabled = {j.job for j in jobs if j.enabled} if jobs else None

    by_job: dict[str, list[dict]] = {}
    for raw in records:
        job = str(raw.get("job", "")).strip()
        if job:
            by_job.setdefault(job, []).append(raw)

    observations: list[Observation] = []
    live = live_blockers(records, enabled)

    for job, runs in sorted(by_job.items()):
        if enabled is not None and job not in enabled:
            # A switched-off job is reported by observe_schedule, once, as
            # information. Its history is not news.
            continue
        newest_first = sorted(
            runs,
            key=lambda r: str(r.get("finished", "")) or str(r.get("started", "")),
            reverse=True,
        )
        latest = newest_first[0]
        streak, head = failure_streak([str(r.get("outcome", "")) for r in newest_first])
        finished = str(latest.get("finished", ""))
        entity = f"job:{job}"
        depends = tuple(
            sorted(f"blocker:{k}" for k, v in live.items() if entity in v["jobs"])
        )

        if head == "dormant":
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=f"{job} skipped its last run rather than failing",
                    at=finished,
                    severity="info",
                    evidence="tools/desk_agent/runs.jsonl",
                )
            )
        elif streak >= STOPPED_AFTER_FAILURES:
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=f"{job} has failed {streak} runs in a row",
                    at=finished,
                    severity="act",
                    trigger="stopped",
                    evidence="tools/desk_agent/runs.jsonl",
                    detail=str(latest.get("summary", ""))[:400],
                    depends_on=depends,
                    metrics=(("consecutive_failures", float(streak)),),
                )
            )
        elif streak:
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=f"{job} failed its last run",
                    at=finished,
                    severity="watch",
                    evidence="tools/desk_agent/runs.jsonl",
                    depends_on=depends,
                    metrics=(("consecutive_failures", float(streak)),),
                )
            )
        else:
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=f"{job} last ran cleanly",
                    at=finished,
                    severity="info",
                    evidence="tools/desk_agent/runs.jsonl",
                )
            )

    for key, entry in sorted(live.items()):
        if entry["runs"] < BLOCKING_AFTER_RUNS:
            continue
        jobs_hit = sorted(entry["jobs"])
        # Live, and it has survived this many runs. Nothing in an unattended
        # run can clear it, or it would have -- so it is waiting on a person,
        # which is one of the three things the owner asked to be woken for.
        observations.append(
            Observation(
                domain="fleet",
                entity=f"blocker:{key}",
                summary=(
                    f"{entry['detail'] or key} -- still live, seen in "
                    f"{entry['runs']} runs, blocking {len(jobs_hit)} job(s)"
                ),
                at=entry["at"],
                severity="act",
                trigger="blocking",
                evidence="tools/desk_agent/runs.jsonl",
                depends_on=tuple(jobs_hit),
                metrics=(
                    ("runs_seen", float(entry["runs"])),
                    ("jobs_blocked", float(len(jobs_hit))),
                ),
            )
        )

    return observations


def _latest_stamp(records: Sequence[dict]) -> str:
    stamps = [
        str(r.get("finished", "")) or str(r.get("started", ""))
        for r in records
        if r.get("finished") or r.get("started")
    ]
    return max(stamps) if stamps else ""


@dataclass(frozen=True)
class ScheduledJob:
    """One row of ``register_desk_agent.ps1``'s ``$jobs`` table."""

    job: str
    hours: tuple[int, ...]
    minute: int
    enabled: bool
    needs_desktop: bool = False


def observe_schedule(
    jobs: Sequence[ScheduledJob],
    records: Sequence[dict],
    now: dt.datetime,
) -> list[Observation]:
    """Jobs that should have reported by now and have not.

    This is the silence detector, and silence is the failure mode this
    repository keeps meeting: three desk scans produced no record and the gap
    was found by hand four days later. A job that fails loudly is already
    covered by ``observe_jobs``; this catches the one that says nothing at all.

    A disabled job is not overdue. It is reported once, as information, because
    a job everyone forgot was switched off looks exactly like a healthy one.
    """
    last_seen: dict[str, dt.datetime] = {}
    for raw in records:
        job = str(raw.get("job", "")).strip()
        stamp = _iso(str(raw.get("finished", "")) or str(raw.get("started", "")))
        if job and stamp and (job not in last_seen or stamp > last_seen[job]):
            last_seen[job] = stamp

    observations: list[Observation] = []
    for spec in sorted(jobs, key=lambda j: j.job):
        entity = f"job:{spec.job}"
        if not spec.enabled:
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=f"{spec.job} is switched off in the scheduler",
                    at=now.isoformat(),
                    severity="info",
                    evidence="tools/register_desk_agent.ps1",
                )
            )
            continue
        seen = last_seen.get(spec.job)
        if seen is None:
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=f"{spec.job} is scheduled but has never reported",
                    at=now.isoformat(),
                    severity="act",
                    trigger="stopped",
                    evidence="tools/register_desk_agent.ps1 + runs.jsonl",
                )
            )
            continue
        # Grace is two full cycles. One missed run is a machine that was
        # asleep; two is a job that is not coming back on its own.
        gap_hours = (now - seen).total_seconds() / 3600.0
        cycle = _cycle_hours(spec.hours)
        if gap_hours > cycle * 2:
            observations.append(
                Observation(
                    domain="fleet",
                    entity=entity,
                    summary=(
                        f"{spec.job} has not reported for {gap_hours:.0f}h "
                        f"(runs every ~{cycle:.0f}h)"
                    ),
                    at=seen.isoformat(),
                    severity="act",
                    trigger="stopped",
                    evidence="tools/register_desk_agent.ps1 + runs.jsonl",
                    metrics=(("silent_hours", round(gap_hours, 1)),),
                )
            )
    return observations


def _cycle_hours(hours: Sequence[int]) -> float:
    """How often a job with these run-hours comes round, in hours."""
    if len(hours) <= 1:
        return 24.0
    ordered = sorted(hours)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    gaps.append(24 - ordered[-1] + ordered[0])
    return float(min(gaps))


@dataclass(frozen=True)
class GitFacts:
    """What the edge read out of git. Plain data so the core stays pure."""

    branch: str = ""
    dirty_paths: tuple[str, ...] = ()
    ahead: int = 0
    behind: int = 0
    unpushed_tracked: tuple[str, ...] = ()
    head_stamp: str = ""


def observe_git(facts: GitFacts, now: dt.datetime) -> list[Observation]:
    """Work that exists and has not reached anywhere anyone else can see it.

    The specific failure pinned here is committed-and-never-pushed: four days of
    desk agent records sat on a local ``main`` while GitHub's copy stopped, and
    nothing said so. Committed is not pushed.
    """
    at = facts.head_stamp or now.isoformat()
    observations: list[Observation] = []

    if facts.unpushed_tracked:
        observations.append(
            Observation(
                domain="fleet",
                entity="repo:unpushed",
                summary=(
                    f"{len(facts.unpushed_tracked)} commit(s) on {facts.branch} "
                    "have not been pushed"
                ),
                at=at,
                severity="act",
                trigger="stopped",
                evidence="git log @{u}..HEAD",
                detail="; ".join(facts.unpushed_tracked[:5]),
                metrics=(("unpushed_commits", float(len(facts.unpushed_tracked))),),
            )
        )

    if facts.dirty_paths:
        observations.append(
            Observation(
                domain="fleet",
                entity="repo:worktree",
                summary=f"{len(facts.dirty_paths)} uncommitted path(s) in the tree",
                at=now.isoformat(),
                severity="watch",
                evidence="git status --porcelain",
                detail="; ".join(facts.dirty_paths[:5]),
                metrics=(("dirty_paths", float(len(facts.dirty_paths))),),
            )
        )

    if facts.behind:
        observations.append(
            Observation(
                domain="fleet",
                entity="repo:branch",
                summary=f"{facts.branch} is {facts.behind} commit(s) behind its base",
                at=now.isoformat(),
                severity="watch",
                evidence="git rev-list --count",
                metrics=(("behind", float(facts.behind)),),
            )
        )

    if not observations:
        observations.append(
            Observation(
                domain="fleet",
                entity="repo:worktree",
                summary=f"{facts.branch or 'the branch'} is clean and pushed",
                at=now.isoformat(),
                severity="info",
                evidence="git status --porcelain",
            )
        )
    return observations


# ---------------------------------------------------------------------------
# The desk and content adapters
#
# Both domains live somewhere this process is not: the desk's feeds are on the
# owner's Windows machine, and content's credentials are in MCP connectors that
# only a Claude session can call. Neither is reached from here -- each is carried
# here, as a redacted signal committed to git, by whoever *can* reach it. See
# `tools/desk_signal.py` and `tools/content_signal.py` for the emitters and for
# the schema that makes publishing them to a public fork safe.
#
# These two functions are the whole of the wiring, which is what the original
# design promised: an adapter drops in beside `observe_jobs` and the assembly
# does not change.
# ---------------------------------------------------------------------------


def observe_desk(signal, now: dt.datetime) -> list[Observation]:
    """The desk, as its bridge last reported it.

    **The bridge is observed before the desk is.** A signal that stopped being
    written reads exactly like a desk with nothing wrong, and that confusion is
    the entire reason this layer exists -- so a stale bridge is reported as the
    thing that stopped, and the desk facts underneath it are still reported but
    are no longer evidence about today.

    Staleness is the trading calendar, never a number of hours: a signal is stale
    once a full session has elapsed without one being written. A weekend is not a
    failure and does not need an exception, because it is not a session.
    """
    if signal is None:
        return []

    observations: list[Observation] = []
    hours, missed = desk_signal.signal_age(signal, now)
    at = signal.taken or now.isoformat()

    if desk_signal.reads_nothing(signal):
        # Fresh and empty. The stamp says the emitter ran; every field says it
        # found nothing, which on a machine that has a desk means it was pointed
        # at the wrong one. Reported as the emitter stopping, because reading a
        # current stamp as a healthy desk is the mistake this layer exists for.
        observations.append(
            Observation(
                domain="desk",
                entity="desk:bridge",
                summary=(
                    "the desk signal was written but read nothing -- the emitter "
                    "is running somewhere the desk is not"
                ),
                at=at,
                severity="act",
                trigger="stopped",
                evidence="signals/desk.json, every field empty",
                detail=(
                    "check the paths `desk_signal.py emit` was given: the reports "
                    "directory, the trade-journal folder, the paper book"
                ),
                metrics=(("signal_age_hours", float(hours or 0.0)),),
            )
        )
    elif missed:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:bridge",
                summary=(
                    f"the desk signal has not been written for {missed} trading "
                    "session(s) -- what follows describes then, not now"
                ),
                at=at,
                severity="act",
                trigger="stopped",
                evidence="signals/desk.json stamp vs the NYSE calendar",
                detail="run `python tools/desk_signal.py emit` on the machine, and push it",
                metrics=(
                    ("sessions_since_signal", float(missed)),
                    ("signal_age_hours", float(hours or 0.0)),
                ),
            )
        )
    else:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:bridge",
                summary="the desk signal is current",
                at=at,
                severity="info",
                evidence="signals/desk.json stamp vs the NYSE calendar",
                metrics=(("signal_age_hours", float(hours or 0.0)),),
            )
        )

    missing = signal.sessions_missing
    if missing:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:reports",
                summary=(
                    f"{missing} of the last {signal.sessions_checked} trading "
                    "session(s) left no desk report"
                ),
                at=at,
                severity="act",
                trigger="stopped",
                evidence="tools/desk_watch.py audit, carried in the signal",
                detail=(
                    f"longest unbroken run: {signal.worst_missing_run}; "
                    f"last report {signal.last_report_day or 'never'}"
                ),
                depends_on=("desk:bridge",),
                metrics=(
                    ("sessions_missing", float(missing)),
                    ("worst_missing_run", float(signal.worst_missing_run or 0)),
                ),
            )
        )
    elif signal.sessions_empty:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:reports",
                summary=f"{signal.sessions_empty} desk report(s) exist but are empty",
                at=at,
                severity="watch",
                evidence="tools/desk_watch.py audit, carried in the signal",
                depends_on=("desk:bridge",),
                metrics=(("sessions_empty", float(signal.sessions_empty)),),
            )
        )
    elif signal.sessions_checked:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:reports",
                summary=(
                    f"every one of the last {signal.sessions_checked} trading "
                    "session(s) left a desk report"
                ),
                at=at,
                severity="info",
                evidence="tools/desk_watch.py audit, carried in the signal",
                depends_on=("desk:bridge",),
            )
        )

    # The paper book, against the same calendar. `last_bar` is the last session
    # it ingested, so sessions elapsed since is exactly how far behind it is.
    if signal.paper_book_last_bar:
        try:
            bar_day = dt.date.fromisoformat(signal.paper_book_last_bar)
        except ValueError:
            bar_day = None
        if bar_day is not None:
            behind = desk_signal.sessions_missed(bar_day, now.date())
            if behind:
                observations.append(
                    Observation(
                        domain="desk",
                        entity="desk:paper-book",
                        summary=f"the paper book has not ingested {behind} session(s)",
                        at=at,
                        severity="watch",
                        evidence="paper-book last_bar vs the NYSE calendar",
                        depends_on=("desk:bridge",),
                        metrics=(("sessions_behind", float(behind)),),
                    )
                )

    # The 2026-09-01 finding, as a rule rather than a threshold: closed trades
    # exist in a machine-readable register, and there is no export by which they
    # can reach the journal. Nothing automatic clears that -- it is a decision.
    closed = signal.paper_book_closed
    if closed and signal.journal_export_present is False:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:journal-gap",
                summary=(
                    f"{closed} closed paper trade(s) have no export route into "
                    "the journal, which is browser-only"
                ),
                at=at,
                severity="act",
                trigger="blocking",
                evidence="paper-book closed count, and no exported register beside it",
                detail=(
                    "either the journal grows an export, or the desk keeps its own "
                    "record -- a person picks, and nothing moves until they do"
                ),
                depends_on=("desk:paper-book",),
                metrics=(("closed_unexported", float(closed)),),
            )
        )

    # The journal's own age is reported and never judged. It is a document the
    # owner writes when they have something to write, so no elapsed-time rule
    # could tell a quiet fortnight from a broken one -- and inventing one is how
    # a channel earns its first false alarm.
    if signal.journal_age_hours is not None:
        observations.append(
            Observation(
                domain="desk",
                entity="desk:journal",
                summary=(
                    "the trade journal was last written "
                    f"{signal.journal_age_hours / 24:.0f} day(s) ago"
                ),
                at=at,
                severity="info",
                evidence="trade-journal.html mtime (the file itself is never read)",
                metrics=(("journal_age_hours", float(signal.journal_age_hours)),),
            )
        )

    if signal.broker == "disconnected":
        observations.append(
            Observation(
                domain="desk",
                entity="desk:broker",
                summary="the broker heartbeat has been stale for a full session",
                at=at,
                severity="act",
                trigger="stopped",
                evidence="IB heartbeat mtime vs the NYSE calendar",
                depends_on=("desk:bridge",),
            )
        )
    elif signal.broker == "connected":
        observations.append(
            Observation(
                domain="desk",
                entity="desk:broker",
                summary="the broker heartbeat is current",
                at=at,
                severity="info",
                evidence="IB heartbeat mtime vs the NYSE calendar",
                depends_on=("desk:bridge",),
            )
        )
    # `unknown` produces no observation at all. It is a blind spot, and
    # `collect` reports it as one -- saying nothing about the broker is honest,
    # saying it is fine because nobody looked is not.

    return observations


def observe_content(signal, now: dt.datetime) -> list[Observation]:
    """The content pipeline, in the two halves it is actually captured in.

    The halves are never merged into one verdict. The render half is machine
    facts, the platform half is an MCP read, and each is only as current as its
    own stamp -- so a fresh capture of one is never allowed to vouch for the
    other.
    """
    if signal is None:
        return []

    observations: list[Observation] = []
    at = signal.taken or now.isoformat()

    if signal.render_seen:
        stale = 0
        if signal.render_age_hours is not None:
            made = now - dt.timedelta(hours=signal.render_age_hours)
            stale = desk_signal.sessions_missed(made.date(), now.date())
        if signal.render_segments == 0 or stale:
            observations.append(
                Observation(
                    domain="content",
                    entity="content:render",
                    summary=(
                        "no market-close render produced for "
                        f"{max(stale, 1)} session(s)"
                    ),
                    at=at,
                    severity="act",
                    trigger="stopped",
                    evidence="render/ segment count and newest mtime",
                    detail="nothing to post, one step before the platform notices",
                    metrics=(("sessions_without_render", float(max(stale, 1))),),
                )
            )
        else:
            observations.append(
                Observation(
                    domain="content",
                    entity="content:render",
                    summary=f"{signal.render_segments} render segment(s) for this session",
                    at=at,
                    severity="info",
                    evidence="render/ segment count and newest mtime",
                    metrics=(("render_segments", float(signal.render_segments)),),
                )
            )

    if signal.platform_seen:
        # The half's own stamp, on the same calendar rule the desk bridge uses.
        # A capture from a fortnight ago reporting no posts is a fact about a
        # fortnight ago, and reading it as today is the mistake this layer is
        # for. Said once, in front of the facts it qualifies.
        try:
            captured = dt.datetime.fromisoformat(
                signal.platform_taken.replace("Z", "+00:00")
            )
        except ValueError:
            captured = None
        if captured is not None:
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=dt.timezone.utc)
            behind = desk_signal.sessions_missed(captured.date(), now.date())
            if behind:
                observations.append(
                    Observation(
                        domain="content",
                        entity="content:capture",
                        summary=(
                            f"the platform half was last captured {behind} session(s) "
                            "ago -- what follows describes then, not now"
                        ),
                        at=at,
                        severity="act",
                        trigger="stopped",
                        evidence="signals/content.json platform_taken vs the NYSE calendar",
                        detail=(
                            "a session holding the connectors must run "
                            "`content_signal.py capture --platform-json -`"
                        ),
                        metrics=(("sessions_since_capture", float(behind)),),
                    )
                )

        # Said before anything else in this half, because it qualifies all of
        # it: a post count and a follower count that describe different channels
        # are two true numbers and one false picture. Nothing automatic can fix
        # it -- reconnecting an account is an OAuth flow in a browser -- so it
        # is `blocking` rather than `stopped`.
        if signal.channel_match == "different":
            observations.append(
                Observation(
                    domain="content",
                    entity="content:channel",
                    summary=(
                        "publishing and analytics are pointed at different "
                        "channels -- posts go one way, numbers come back about "
                        "the other"
                    ),
                    at=at,
                    severity="act",
                    trigger="blocking",
                    evidence="the capturing session compared both connectors' accounts",
                    detail=(
                        "reconnect the publishing side to the measured channel; "
                        "until then every figure below describes one or the other, "
                        "never both"
                    ),
                    depends_on=("content:publish", "content:analytics"),
                )
            )

        if signal.publish_subscription == "inactive":
            observations.append(
                Observation(
                    domain="content",
                    entity="content:publish",
                    summary="the publishing subscription is not active -- nothing can post",
                    at=at,
                    severity="act",
                    trigger="stopped",
                    evidence="Blotato subscription status, captured by a session",
                    depends_on=("content:render",),
                )
            )
        elif signal.publish_accounts == 0:
            observations.append(
                Observation(
                    domain="content",
                    entity="content:publish",
                    summary="no publishing account is connected -- nothing can post",
                    at=at,
                    severity="act",
                    trigger="stopped",
                    evidence="Blotato connected-account count",
                    depends_on=("content:render",),
                )
            )
        elif signal.publish_posts_7d == 0 and (signal.render_segments or 0) > 0:
            observations.append(
                Observation(
                    domain="content",
                    entity="content:publish",
                    summary="renders exist and nothing was posted in the captured window",
                    at=at,
                    severity="act",
                    trigger="stopped",
                    evidence="Blotato post count for the window the capture asked for",
                    detail="made but not posted -- the pipeline stops at the last step",
                    depends_on=("content:render",),
                )
            )
        elif signal.publish_posts_7d is not None:
            observations.append(
                Observation(
                    domain="content",
                    entity="content:publish",
                    summary=(
                        f"{signal.publish_posts_7d} post(s) in the captured window "
                        f"across {signal.publish_accounts} account(s)"
                    ),
                    at=at,
                    severity="info",
                    evidence="Blotato post count for the window the capture asked for",
                    depends_on=("content:render",),
                    metrics=(("posts_in_window", float(signal.publish_posts_7d)),),
                )
            )

        # An unpaid analytics plan is carried because a lapsed trial does not
        # announce itself: the reads simply stop, and a channel nobody watched
        # looks identical to a channel with nothing happening.
        if signal.analytics_plan == "trial":
            observations.append(
                Observation(
                    domain="content",
                    entity="content:analytics",
                    summary=(
                        "the analytics connector is on an unpaid trial -- it will "
                        "go quiet rather than fail loudly when it lapses"
                    ),
                    at=at,
                    severity="watch",
                    evidence="Windsor.ai is_paid, captured by a session",
                )
            )
        elif signal.analytics_accounts == 0:
            observations.append(
                Observation(
                    domain="content",
                    entity="content:analytics",
                    summary="the analytics connector has no account connected",
                    at=at,
                    severity="watch",
                    evidence="Windsor.ai connected-account count",
                )
            )

    return observations


# ---------------------------------------------------------------------------
# Assembly -- the four derived answers
# ---------------------------------------------------------------------------


def connections(observations: Sequence[Observation]) -> dict[str, list[str]]:
    """The undirected graph of what touches what.

    Edges are declared by ``depends_on`` and nothing else. There is no
    similarity heuristic here on purpose: an invented edge is a shortcut across
    the whole graph, which is exactly the over-linking defect
    ``tools/graph_audit.py`` exists to convict in somebody else's resolver.
    """
    graph: dict[str, set[str]] = {}
    for obs in observations:
        graph.setdefault(obs.entity, set())
        for other in obs.depends_on:
            graph.setdefault(other, set())
            graph[obs.entity].add(other)
            graph[other].add(obs.entity)
    return {k: sorted(v) for k, v in sorted(graph.items())}


def changes(
    current: Sequence[Observation],
    prior: Sequence[Observation],
) -> tuple[list[Change], bool]:
    """What reads differently than last time. Returns ``(changes, no_history)``.

    **It refuses to call an empty log "nothing changed".** With no prior
    observations the honest answer is that this question cannot be answered
    yet, and the flag says so. A layer that reported calm because it had never
    looked before would be worse than one that reported nothing.
    """
    if not prior:
        return [], True

    latest_prior: dict[str, Observation] = {}
    for obs in prior:
        seen = latest_prior.get(obs.entity)
        if seen is None or obs.at >= seen.at:
            latest_prior[obs.entity] = obs

    out: list[Change] = []
    for obs in current:
        before = latest_prior.get(obs.entity)
        if before is None:
            out.append(Change(obs.entity, "not seen before", obs.summary, obs.at))
        elif before.summary != obs.summary:
            out.append(Change(obs.entity, before.summary, obs.summary, before.at))
    for entity, before in latest_prior.items():
        if entity not in {o.entity for o in current}:
            out.append(Change(entity, before.summary, "no longer observed", before.at))
    return sorted(out, key=lambda c: c.entity), False


def project(
    observations: Sequence[Observation],
    jobs: Sequence[ScheduledJob],
    now: dt.datetime,
) -> list[Projection]:
    """What follows from a rule.

    Two rules, and no third. A failure streak with an uncleared blocker
    projects the next run failing the same way, because nothing in an
    unattended run can clear it. A scheduled job projects its next run time.

    **Anything without a rule gets no projection at all.** This is the same
    refusal as ``night_lab`` dropping model output it cannot check: a forecast
    the tool cannot justify is indistinguishable from one it invented.
    """
    out: list[Projection] = []
    blocked = {o.entity for o in observations if o.entity.startswith("blocker:")}

    for obs in observations:
        if obs.trigger != "stopped" or not obs.entity.startswith("job:"):
            continue
        live = [d for d in obs.depends_on if d in blocked]
        if live:
            out.append(
                Projection(
                    entity=obs.entity,
                    expectation="the next scheduled run fails the same way",
                    rule=(
                        "a failure streak whose blocker is still unresolved -- "
                        f"{', '.join(live)} -- has nothing in an unattended run "
                        "that can clear it"
                    ),
                )
            )

    for spec in sorted(jobs, key=lambda j: j.job):
        if not spec.enabled or not spec.hours:
            continue
        nxt = _next_run(spec, now)
        if nxt:
            out.append(
                Projection(
                    entity=f"job:{spec.job}",
                    expectation="next scheduled run",
                    rule="tools/register_desk_agent.ps1 $jobs",
                    when=f"{nxt:%Y-%m-%d %H:%M} machine local time",
                )
            )
    return out


def _next_run(spec: ScheduledJob, now: dt.datetime) -> dt.datetime | None:
    """The next wall-clock time this job is due. Naive on purpose.

    The scheduler runs in the machine's local time and this module is not told
    which zone that is, so the answer is expressed in the zone it was given.
    Saying so beats silently converting into a zone nobody chose -- the
    timezone trap in ``docs/backtesting.md`` cost eight years of a backtest.
    """
    if not spec.hours:
        return None
    bare = now.replace(tzinfo=None)
    for offset in (0, 1):
        day = bare.date() + dt.timedelta(days=offset)
        for hour in sorted(spec.hours):
            candidate = dt.datetime(day.year, day.month, day.day, hour, spec.minute)
            if candidate > bare:
                return candidate
    return None


def attention(observations: Sequence[Observation]) -> list[Observation]:
    """What the owner asked to be interrupted for, worst first.

    The filter is his three triggers and nothing else. An observation with no
    trigger is never promoted here however interesting it looks, because a
    layer that decides on its own what is worth an interruption is how the
    interruptions stop being read.
    """
    ranked = [o for o in observations if o.trigger]
    return sorted(
        ranked,
        key=lambda o: (
            SEVERITIES.index(o.severity),
            TRIGGERS.index(o.trigger),
            o.entity,
        ),
    )


def safest_actions(observations: Sequence[Observation]) -> list[Action]:
    """The next step for each thing demanding attention, and who may take it.

    The gate is the point of this function. An action is proposed as safe only
    when it is reversible and moves no money; everything touching money, or
    needing a judgement this layer does not have, is returned with ``safe=False``
    and a reason, for a person to decide.

    Same doctrine as ``tools/ai_company.py``: agents move information, people
    move money. The gate is asserted by a test rather than trusted.
    """
    out: list[Action] = []
    for obs in attention(observations):
        if obs.trigger == "money":
            out.append(
                Action(
                    entity=obs.entity,
                    step="raise it -- do not act",
                    safe=False,
                    why="this commits money, which is a person's decision",
                )
            )
        elif obs.trigger == "blocking":
            out.append(
                Action(
                    entity=obs.entity,
                    step="put the decision to the owner with the evidence attached",
                    safe=False,
                    why=(
                        "unresolved across many runs, so nothing automatic will "
                        "clear it -- it is waiting on a judgement"
                    ),
                )
            )
        elif obs.entity == "repo:unpushed":
            out.append(
                Action(
                    entity=obs.entity,
                    step="push the branch",
                    safe=True,
                    why="reversible, moves no money, and the work is invisible until it lands",
                )
            )
        elif obs.entity == "desk:bridge":
            out.append(
                Action(
                    entity=obs.entity,
                    step="re-run `python tools/desk_signal.py emit` on the machine, and push it",
                    safe=True,
                    why=(
                        "the bridge stopped, not necessarily the desk -- until it is "
                        "written again nothing here describes today"
                    ),
                )
            )
        elif obs.entity.startswith("content:"):
            out.append(
                Action(
                    entity=obs.entity,
                    step="re-capture the content signal and compare it with this one",
                    safe=True,
                    why="a read of the connectors; posts nothing and changes nothing",
                )
            )
        elif obs.trigger == "stopped":
            out.append(
                Action(
                    entity=obs.entity,
                    step="read the newest run record and reproduce the failure here",
                    safe=True,
                    why="a read; changes nothing and settles whether the cause is still live",
                )
            )
    return out


def assemble(
    observations: Sequence[Observation],
    prior: Sequence[Observation],
    jobs: Sequence[ScheduledJob],
    now: dt.datetime,
    blind: Sequence[str] = (),
) -> Picture:
    """The seven answers. ``why`` is not a field: it is each observation's
    ``evidence`` plus its edges in ``connections``, handed to the session that
    is already reading this."""
    delta, no_history = changes(observations, prior)
    return Picture(
        taken=now.isoformat(),
        now=sorted(
            observations, key=lambda o: (SEVERITIES.index(o.severity), o.entity)
        ),
        changing=delta,
        no_history=no_history,
        projections=project(observations, jobs, now),
        connections=connections(observations),
        attention=attention(observations),
        actions=safest_actions(observations),
        blind=list(blind),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_short(picture: Picture) -> str:
    """The catch-up form: six lines at most, worst first, or one line if calm."""
    lines: list[str] = []
    for obs in picture.attention[:4]:
        lines.append(f"  [{obs.trigger}] {obs.summary}")
    if not lines:
        lines.append("  Nothing demanding attention: no stalled job, nothing unpushed.")
    if picture.no_history:
        lines.append(
            "  (no prior observation on file, so 'what changed' is unanswerable)"
        )
    elif picture.changing:
        lines.append(f"  {len(picture.changing)} thing(s) changed since the last look.")
    if picture.blind:
        lines.append(f"  Cannot see: {', '.join(picture.blind)}.")
    return "\n".join(lines[:6])


def render(picture: Picture) -> str:
    out: list[str] = [f"Situational picture, taken {picture.taken}", ""]

    out.append("WHAT IS HAPPENING NOW")
    for obs in picture.now:
        mark = {"act": "!!", "watch": " ~", "info": "  "}[obs.severity]
        out.append(f"  {mark} {obs.summary}")
        if obs.evidence:
            out.append(f"       why: {obs.evidence}")
    out.append("")

    out.append("WHAT IS CHANGING")
    if picture.no_history:
        out.append(
            "  Unanswerable: no prior observation on file. Run `record` to start "
            "the log; this is not a report that nothing changed."
        )
    elif not picture.changing:
        out.append("  Nothing, against the last recorded observation.")
    else:
        for change in picture.changing:
            out.append(f"  {change.entity}: {change.was}  ->  {change.now}")
    out.append("")

    out.append("WHAT IS LIKELY NEXT")
    if not picture.projections:
        out.append("  No rule applies. Nothing is projected rather than guessed.")
    for proj in picture.projections:
        when = f" at {proj.when}" if proj.when else ""
        out.append(f"  {proj.entity}: {proj.expectation}{when}")
        out.append(f"       rule: {proj.rule}")
    out.append("")

    out.append("WHAT IS CONNECTED")
    shared = {k: v for k, v in picture.connections.items() if len(v) > 1}
    if not shared:
        out.append("  Nothing links to more than one other thing.")
    for entity, linked in shared.items():
        out.append(f"  {entity} -> {', '.join(linked)}")
    out.append("")

    out.append("WHAT DESERVES ATTENTION")
    if not picture.attention:
        out.append("  Nothing matching the triggers: stopped, money, blocking.")
    for obs in picture.attention:
        out.append(f"  [{obs.severity}/{obs.trigger}] {obs.summary}")
    out.append("")

    out.append("WHAT ACTION IS SAFEST")
    if not picture.actions:
        out.append("  Nothing to do.")
    for action in picture.actions:
        tag = "safe" if action.safe else "NEEDS A PERSON"
        out.append(f"  [{tag}] {action.entity}: {action.step}")
        out.append(f"       {action.why}")

    if picture.blind:
        out.append("")
        out.append("NOT VISIBLE FROM HERE")
        for item in picture.blind:
            out.append(f"  {item}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The dirty edge
# ---------------------------------------------------------------------------


PS_JOB = re.compile(
    r"@\{\s*Name\s*=\s*'(?P<name>[^']+)'\s*;\s*Job\s*=\s*'(?P<job>[^']+)'\s*;"
    r"\s*Hours\s*=\s*@\((?P<hours>[^)]*)\)\s*;?\s*Minute\s*=\s*(?P<minute>\d+)"
    r"(?P<rest>.*?)\}",
    re.DOTALL,
)


def parse_scheduled_jobs(text: str) -> list[ScheduledJob]:
    """Read the ``$jobs`` table out of ``register_desk_agent.ps1``.

    Parsed rather than copied because a second list beside the first stops
    agreeing on the first edit -- the same reason ``front_door.py`` scans for
    its inventory instead of keeping one.
    """
    jobs: list[ScheduledJob] = []
    for match in PS_JOB.finditer(text):
        hours = tuple(int(h) for h in re.findall(r"\d+", match.group("hours")))
        rest = match.group("rest")
        enabled = "Enabled = $true" in rest or "Enabled = $True" in rest
        jobs.append(
            ScheduledJob(
                job=match.group("job"),
                hours=hours,
                minute=int(match.group("minute")),
                enabled=enabled,
                needs_desktop="NeedsDesktop = $true" in rest,
            )
        )
    return jobs


def _git(args: Sequence[str], root: pathlib.Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_git_facts(root: pathlib.Path) -> GitFacts:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    dirty = tuple(
        line[3:] for line in _git(["status", "--porcelain"], root).splitlines() if line
    )
    unpushed = tuple(
        line
        for line in _git(["log", "--oneline", "@{u}..HEAD"], root).splitlines()
        if line
    )
    behind = _git(["rev-list", "--count", "HEAD..@{u}"], root)
    return GitFacts(
        branch=branch,
        dirty_paths=dirty,
        unpushed_tracked=unpushed,
        behind=int(behind) if behind.isdigit() else 0,
        head_stamp=_git(["log", "-1", "--format=%cI"], root),
    )


def read_run_records(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Refuse rather than repair: an unparseable record is reported by
            # the caller as a blind spot, never guessed into shape.
            continue
    return out


def load_log(path: pathlib.Path) -> list[Observation]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Observation.from_dict(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def append_log(observations: Sequence[Observation], path: pathlib.Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for obs in observations:
            handle.write(json.dumps(obs.as_dict(), sort_keys=True) + "\n")
    return len(observations)


def collect(
    root: pathlib.Path, now: dt.datetime
) -> tuple[list[Observation], list[ScheduledJob], list[str]]:
    """Every source, read at this moment. Returns observations, schedule, blind spots."""
    blind: list[str] = []

    runs_path = root / "tools" / "desk_agent" / "runs.jsonl"
    records = read_run_records(runs_path)
    if not records:
        blind.append(
            "the desk agent run log (tools/desk_agent/runs.jsonl is missing or empty)"
        )

    ps1 = root / "tools" / "register_desk_agent.ps1"
    jobs = parse_scheduled_jobs(ps1.read_text(encoding="utf-8")) if ps1.exists() else []
    if not jobs:
        blind.append("the scheduled-job table (tools/register_desk_agent.ps1)")

    facts = read_git_facts(root)
    if not facts.branch:
        blind.append("git (not a checkout, or git is unavailable)")

    # The desk and content signals. Neither domain is reached from this process
    # -- both are carried here by whoever can reach them (a local job for the
    # desk, a session holding the connectors for content), which is why the
    # thing checked first is whether the bridge was written at all.
    desk = desk_signal.load_signal(root / "signals" / "desk.json")
    if desk is None:
        blind.append(
            "the desk (no signals/desk.json -- run `python tools/desk_signal.py "
            "emit` on the machine and push it)"
        )
    elif desk.broker == "unknown":
        # Tri-state on purpose: nobody looked is not the same as unplugged.
        blind.append(
            "the broker connection (no heartbeat file; unknown, not disconnected)"
        )

    content = content_signal.load_signal(root / "signals" / "content.json")
    if content is None:
        blind.append("content (no signals/content.json -- nothing has captured it yet)")
    else:
        if not content.render_seen:
            blind.append(
                "the market-close render (render/ is on the machine and gitignored)"
            )
        if content.platform_seen and content.channel_match == "unknown":
            blind.append(
                "whether publishing and analytics point at the same channel "
                "(the capture did not say; it is never guessed from handles)"
            )
        if not content.platform_seen:
            blind.append(
                "content publishing and analytics (the Blotato and Windsor.ai "
                "credentials are live, but they are MCP connectors -- a session "
                "holding them must run `content_signal.py capture --platform-json -`)"
            )

    # Named rather than left implicit: a domain the owner asked for and this
    # slice does not yet cover reads exactly like a domain with nothing wrong.
    blind.append("the businesses -- adapter not built yet")

    observations = [
        *observe_jobs(records, now, jobs),
        *observe_schedule(jobs, records, now),
        *observe_git(facts, now),
        *observe_desk(desk, now),
        *observe_content(content, now),
    ]
    return observations, jobs, blind


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")

    brief = sub.add_parser("brief", help="the seven answers")
    brief.add_argument("--short", action="store_true", help="catch-up form, <= 6 lines")
    brief.add_argument("--json", action="store_true", dest="as_json")
    brief.add_argument("--log", default=str(DEFAULT_LOG))
    brief.add_argument("--root", default=str(REPO_ROOT))

    record = sub.add_parser("record", help="append this moment to the observation log")
    record.add_argument("--log", default=str(DEFAULT_LOG))
    record.add_argument("--root", default=str(REPO_ROOT))

    sources = sub.add_parser("sources", help="what it can see, and what it cannot")
    sources.add_argument("--root", default=str(REPO_ROOT))

    args = parser.parse_args(argv)
    command = args.command or "brief"
    root = pathlib.Path(getattr(args, "root", REPO_ROOT))
    now = dt.datetime.now(dt.timezone.utc)

    observations, jobs, blind = collect(root, now)

    if command == "sources":
        print("SEEING")
        for entity in sorted({o.entity for o in observations}):
            print(f"  {entity}")
        print("\nBLIND")
        for item in blind:
            print(f"  {item}")
        return 0

    if command == "record":
        count = append_log(observations, pathlib.Path(args.log))
        print(f"Recorded {count} observation(s) to {args.log}")
        return 0

    picture = assemble(observations, load_log(pathlib.Path(args.log)), jobs, now, blind)
    if args.as_json:
        payload = {
            "taken": picture.taken,
            "now": [o.as_dict() for o in picture.now],
            "changing": [asdict(c) for c in picture.changing],
            "no_history": picture.no_history,
            "projections": [asdict(p) for p in picture.projections],
            "connections": picture.connections,
            "attention": [o.as_dict() for o in picture.attention],
            "actions": [asdict(a) for a in picture.actions],
            "blind": picture.blind,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.short:
        print(render_short(picture))
    else:
        print(render(picture))
    # Exit 1 when something wants a person, so a wrapper can react without
    # having to read the text.
    return 1 if any(o.severity == "act" for o in picture.attention) else 0


if __name__ == "__main__":
    sys.exit(main())

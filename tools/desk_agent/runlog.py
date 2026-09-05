"""The desk agent's memory: one record per unattended run, and the questions
worth asking of them.

An agent that runs while you sleep has no memory of yesterday unless something
writes it down. This module is that something. Every scheduled run appends one
record here, and the weekly review reads them back to decide what in the
playbook should change. The log is committed to git on purpose: it is the only
part of the system a cloud session can see, and `git log` over it is the
agent's learning history in a form you can audit and revert.

    python -m tools.desk_agent.runlog append --job premarket --outcome ok \
        --summary "3 candidates, 2 backtested" --action "deployed OB-FVG" \
        --metric candidates=3
    python -m tools.desk_agent.runlog review --last 40

Three distinctions are load-bearing, and each one exists because the obvious
version of this tool lies to you:

**Skipped is not failed.** A pre-market run at 07:00 on a holiday has nothing
to do; a run that could not reach the CDP port tried and broke. Collapsing the
two produces a system that reports itself healthy while doing nothing for a
week, which is the specific failure this whole design is meant to catch.

**No actions is not automatically a bad run.** A scan that honestly finds no
setup is a correct `ok` with an empty action list, and a playbook tuned to
avoid that outcome is a playbook tuned to invent trades. What *is* a finding is
a job that has never produced an action across many runs -- that one is dead
weight, and `dead_jobs` exists to say so out loud rather than letting it be
maintained in silence forever.

**Blockers are counted by key, not by message.** "connection refused on 9222 at
07:01" and the same thing at 07:02 are one recurring problem, but as free text
they are two singletons and neither crosses a threshold. Every blocker
therefore carries a slug, derived from the message when the caller does not
supply one, and the counting is done on the slug. A count that only one method
confirms is not a count.

**Committed is not pushed.** The log is committed so that a cloud session can
read it, and a cloud session reads GitHub, not the owner's disk. From
2026-08-31 to 09-01 four run records were committed to the OneDrive checkout's
``main`` and never left it; GitHub's copy stopped at 08-28 and nothing said so
for four days. ``unpushed`` compares this file with the copy on the fork's
``main`` and exits non-zero when this machine knows runs that GitHub does not::

    python -m tools.desk_agent.runlog unpushed            # against jay/main
    python -m tools.desk_agent.runlog unpushed --fetch    # refresh jay/main first
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

DEFAULT_LOG = pathlib.Path("tools/desk_agent/runs.jsonl")

#: The four outcomes a run may report. Ordered worst to best for reporting.
OUTCOMES = ("failed", "partial", "skipped", "ok")

#: Substrings replaced when deriving a blocker key from free text, so that two
#: reports of the same problem collapse onto one slug.
_VOLATILE = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(:\d{2})?", re.I), " "),
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b"), " "),
    (re.compile(r"[a-z]:\\[^\s'\"]+", re.I), " path "),
    (re.compile(r"/[^\s'\"]{4,}"), " path "),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), " "),
    (re.compile(r"\b\d+\b"), " "),
)


class LogError(Exception):
    """A record could not be written or read back."""


@dataclass(frozen=True)
class Blocker:
    """Something that stopped a run doing its job.

    ``key`` is what gets counted; ``detail`` is what a human reads.
    """

    key: str
    detail: str = ""

    @staticmethod
    def of(value: Any) -> "Blocker":
        if isinstance(value, Blocker):
            return value
        if isinstance(value, dict):
            detail = str(value.get("detail", ""))
            key = str(value.get("key") or "") or blocker_key(detail)
            return Blocker(key=key, detail=detail)
        detail = str(value)
        return Blocker(key=blocker_key(detail), detail=detail)

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "detail": self.detail}


@dataclass
class RunRecord:
    """One unattended run."""

    job: str
    outcome: str
    summary: str = ""
    started: str = ""
    finished: str = ""
    actions: list[str] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    playbook_rev: str = ""

    def __post_init__(self) -> None:
        if not self.job:
            raise LogError("a run record needs a job name")
        if self.outcome not in OUTCOMES:
            raise LogError(
                f"outcome {self.outcome!r} is not one of {', '.join(OUTCOMES)}"
            )
        self.blockers = [Blocker.of(b) for b in self.blockers]
        if not self.finished:
            self.finished = _now()
        if not self.started:
            self.started = self.finished

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["blockers"] = [Blocker.of(b).as_dict() for b in self.blockers]
        return out

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "RunRecord":
        return RunRecord(
            job=str(raw.get("job", "")),
            outcome=str(raw.get("outcome", "")),
            summary=str(raw.get("summary", "")),
            started=str(raw.get("started", "")),
            finished=str(raw.get("finished", "")),
            actions=[str(a) for a in raw.get("actions", []) or []],
            blockers=[Blocker.of(b) for b in raw.get("blockers", []) or []],
            metrics={
                str(k): float(v)
                for k, v in (raw.get("metrics", {}) or {}).items()
                if _is_number(v)
            },
            playbook_rev=str(raw.get("playbook_rev", "")),
        )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def blocker_key(text: str) -> str:
    """Slug a blocker message so two reports of one problem count as one.

    Timestamps, paths and bare numbers are stripped first -- they are what make
    otherwise-identical messages look distinct. The result is capped at six
    words so a long traceback line and its shorter retelling still collapse
    together.
    """
    s = str(text).lower()
    for pattern, replacement in _VOLATILE:
        s = pattern.sub(replacement, s)
    words = re.findall(r"[a-z]+", s)
    if not words:
        return "unknown"
    return "-".join(words[:6])


# ------------------------------------------------------------------ writing --


def append_record(record: RunRecord, path: pathlib.Path = DEFAULT_LOG) -> RunRecord:
    """Append one record as a JSON line, creating the log if needed."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.as_dict(), sort_keys=True)
    if "\n" in line:  # defensive: a newline here would split one record in two
        raise LogError("record serialised to more than one line")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return record


# ------------------------------------------------------------------ reading --


def read_records(
    path: pathlib.Path = DEFAULT_LOG,
    job: str | None = None,
    last: int | None = None,
) -> list[RunRecord]:
    """Read records oldest-first, skipping any line that will not parse.

    A corrupt line is skipped rather than fatal: a half-written record from a
    machine that slept mid-run should cost you that run's history, not the
    whole log.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return []
    records: list[RunRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(RunRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, LogError, TypeError, AttributeError):
            continue
    if job:
        records = [r for r in records if r.job == job]
    if last is not None and last > 0:
        records = records[-last:]
    return records


# ---------------------------------------------------------------- questions --


@dataclass
class Summary:
    total: int
    by_outcome: dict[str, int]
    by_job: dict[str, int]
    actions: int
    jobs_seen: list[str]

    @property
    def healthy(self) -> bool:
        """True when nothing failed and at least one run did something.

        Deliberately not "no failures": a week of clean skips is not health,
        it is a scheduler firing into a job that never has anything to do.
        """
        return self.by_outcome.get("failed", 0) == 0 and self.actions > 0


def summarize(records: Sequence[RunRecord]) -> Summary:
    by_outcome = Counter(r.outcome for r in records)
    by_job = Counter(r.job for r in records)
    return Summary(
        total=len(records),
        by_outcome={k: by_outcome.get(k, 0) for k in OUTCOMES if by_outcome.get(k)},
        by_job=dict(sorted(by_job.items())),
        actions=sum(len(r.actions) for r in records),
        jobs_seen=sorted(by_job),
    )


@dataclass
class RecurringBlocker:
    key: str
    count: int
    jobs: list[str]
    latest_detail: str


def recurring_blockers(
    records: Sequence[RunRecord], min_count: int = 3
) -> list[RecurringBlocker]:
    """Blockers seen at least ``min_count`` times, worst first.

    This is the primary input to self-improvement: something that has stopped
    the agent three times will stop it a fourth unless the playbook changes.
    """
    counts: Counter[str] = Counter()
    jobs: defaultdict[str, set[str]] = defaultdict(set)
    latest: dict[str, str] = {}
    for record in records:
        for blocker in record.blockers:
            counts[blocker.key] += 1
            jobs[blocker.key].add(record.job)
            if blocker.detail:
                latest[blocker.key] = blocker.detail
    out = [
        RecurringBlocker(
            key=key,
            count=count,
            jobs=sorted(jobs[key]),
            latest_detail=latest.get(key, ""),
        )
        for key, count in counts.items()
        if count >= min_count
    ]
    out.sort(key=lambda b: (-b.count, b.key))
    return out


def dead_jobs(records: Sequence[RunRecord], min_runs: int = 5) -> list[str]:
    """Jobs that have run at least ``min_runs`` times and never done anything.

    A job with no actions across many runs is either mis-scoped or no longer
    wanted, and either way it should be raised rather than maintained in
    silence. Runs that were skipped do not count towards ``min_runs`` -- a job
    that never got the chance to act has not been given one.
    """
    attempts: Counter[str] = Counter()
    acted: Counter[str] = Counter()
    for record in records:
        if record.outcome == "skipped":
            continue
        attempts[record.job] += 1
        acted[record.job] += len(record.actions)
    return sorted(
        job
        for job, count in attempts.items()
        if count >= min_runs and acted.get(job, 0) == 0
    )


def outcome_trend(records: Sequence[RunRecord]) -> str:
    """Compare the older half of the window against the newer half.

    Answers "is this getting better" without pretending to more precision than
    a handful of runs supports: it reports a direction, not a rate.
    """
    scored = [r for r in records if r.outcome != "skipped"]
    if len(scored) < 4:
        return "not enough runs to say"
    midpoint = len(scored) // 2
    older, newer = scored[:midpoint], scored[midpoint:]

    def rate(chunk: Sequence[RunRecord]) -> float:
        good = sum(1 for r in chunk if r.outcome == "ok")
        return good / len(chunk)

    before, after = rate(older), rate(newer)
    delta = after - before
    if abs(delta) < 0.1:
        return f"flat ({before:.0%} then {after:.0%})"
    direction = "improving" if delta > 0 else "regressing"
    return f"{direction} ({before:.0%} then {after:.0%})"


def review(records: Sequence[RunRecord], min_count: int = 3) -> str:
    """The whole picture, as the text the weekly review job reads."""
    summary = summarize(records)
    if not summary.total:
        return "No runs logged yet. Nothing to review."

    lines = [
        f"{summary.total} runs across {len(summary.jobs_seen)} jobs: "
        + ", ".join(f"{k} {v}" for k, v in summary.by_outcome.items()),
        f"actions taken: {summary.actions}",
        f"trend: {outcome_trend(records)}",
    ]

    blockers = recurring_blockers(records, min_count=min_count)
    if blockers:
        lines.append("")
        lines.append(f"Recurring blockers (>= {min_count} runs) -- fix these first:")
        for blocker in blockers:
            jobs = ", ".join(blocker.jobs)
            lines.append(f"  {blocker.count}x  {blocker.key}  [{jobs}]")
            if blocker.latest_detail:
                lines.append(f"        latest: {blocker.latest_detail}")
    else:
        lines.append("")
        lines.append("No blocker has recurred enough to act on.")

    dead = dead_jobs(records)
    if dead:
        lines.append("")
        lines.append("Jobs that have never produced an action -- propose removing:")
        for job in dead:
            lines.append(f"  {job}")

    return "\n".join(lines)


# ------------------------------------------------------------------ pushed? --
#
# The whole reason the log is tracked is that a cloud session can read it, and
# a cloud session reads the fork on GitHub. A record that is committed and never
# pushed is therefore exactly as invisible there as one that was never written,
# while looking, from the machine, like the job did everything asked of it.


def git_output(args: Sequence[str], cwd: pathlib.Path | str | None = None) -> str:
    """Run one git command and return its stdout. Raises LogError on failure.

    The only function here that touches git, so a test can hand ``unpushed`` a
    stand-in and never need a repository or a network.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )
    except OSError as exc:  # git not on PATH
        raise LogError(f"git could not be run: {exc}") from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip() or f"git {' '.join(args)} failed"
        raise LogError(message)
    return proc.stdout


@dataclass
class PushReport:
    """What the fork's copy of the log knows, compared with this one."""

    remote_ref: str
    log_path: str
    #: Commits on HEAD touching the log that the remote ref does not have.
    commits_ahead: int = 0
    #: Records in this file that the remote copy does not carry, oldest first.
    unseen: list[RunRecord] = field(default_factory=list)
    #: Lines in this file that the remote copy lacks -- includes any that would
    #: not parse, so the two counts can differ.
    unseen_lines: int = 0
    #: Of those, lines that HEAD does not carry either: written, not committed.
    uncommitted_lines: int = 0
    #: Records the remote copy has that this file lacks: behind, not ahead.
    remote_only_lines: int = 0
    #: The remote ref could not be read at all -- no such remote, never fetched.
    ref_missing: bool = False
    error: str = ""

    @property
    def pushed(self) -> bool:
        """True only when the remote copy carries every line here.

        Deliberately not "commits_ahead == 0": a commit count says nothing
        about a record that was appended and never committed at all.
        """
        return not self.ref_missing and self.unseen_lines == 0


def _log_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_lines(lines: Iterable[str]) -> list[RunRecord]:
    out: list[RunRecord] = []
    for line in lines:
        try:
            out.append(RunRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, LogError, TypeError, AttributeError):
            continue
    return out


def unpushed(
    remote: str = "jay",
    branch: str = "main",
    path: pathlib.Path = DEFAULT_LOG,
    run=None,
    fetch: bool = False,
) -> PushReport:
    """Compare this file with the copy on ``remote/branch``.

    ``remote/branch`` is the remote-tracking ref *as of the last fetch*, so by
    default this answers "did the last push reach the ref git last saw", which
    is the question the launcher asks straight after pushing. Pass ``fetch``
    to ask GitHub first; that is the network call, and it is opt-in.

    The remote is ``jay`` by name and not ``origin``: ``origin`` is upstream in
    the OneDrive checkout and the fork in the other, so a bare ``origin`` would
    compare against the wrong project in one of them and report it pushed.
    """
    # Resolved at call time so a test can replace ``git_output`` on the module.
    run = run or git_output
    path = pathlib.Path(path)
    ref = f"{remote}/{branch}"
    report = PushReport(remote_ref=ref, log_path=str(path))

    if fetch:
        try:
            run(["fetch", remote, branch])
        except LogError as exc:
            report.error = f"fetch failed: {exc}"
            # Carry on: the ref as last seen is still worth comparing against.

    # git names a blob by its repository-relative posix path, whatever the
    # platform spells the working path as.
    if path.is_absolute():
        try:
            top = pathlib.Path(run(["rev-parse", "--show-toplevel"]).strip())
            rel = path.resolve().relative_to(top.resolve()).as_posix()
        except (LogError, ValueError) as exc:
            report.ref_missing = True
            report.error = f"{path} is not inside a git checkout: {exc}"
            return report
    else:
        rel = path.as_posix()

    try:
        remote_text = run(["show", f"{ref}:{rel}"])
    except LogError as exc:
        message = str(exc)
        if "does not exist in" in message or "exists on disk, but not in" in message:
            # The ref is there and the log is not: everything here is unseen.
            remote_text = ""
        else:
            report.ref_missing = True
            report.error = message
            return report

    try:
        head_text = run(["show", f"HEAD:{rel}"])
    except LogError:
        head_text = ""

    try:
        report.commits_ahead = int(
            run(["rev-list", "--count", f"{ref}..HEAD", "--", rel]).strip() or 0
        )
    except (LogError, ValueError) as exc:
        report.error = f"could not count commits: {exc}"

    local = _log_lines(path.read_text(encoding="utf-8")) if path.exists() else []
    remote_lines = set(_log_lines(remote_text))
    head_lines = set(_log_lines(head_text))

    unseen = [line for line in local if line not in remote_lines]
    report.unseen_lines = len(unseen)
    report.uncommitted_lines = sum(1 for line in unseen if line not in head_lines)
    report.unseen = _parse_lines(unseen)
    report.remote_only_lines = sum(1 for line in remote_lines if line not in set(local))
    return report


def render_push_report(report: PushReport) -> str:
    lines = [f"Run log here vs {report.remote_ref}  ({report.log_path})"]
    if report.ref_missing:
        lines.append(f"  could not read {report.remote_ref}: {report.error}")
        lines.append(
            "  No such remote, or never fetched. From a clone whose fork remote is"
            " 'origin' pass --remote origin; on the owner's machine the remote is"
            " 'jay'."
        )
        return "\n".join(lines)

    lines.append(
        f"  commits touching the log that {report.remote_ref} lacks: "
        f"{report.commits_ahead}"
    )
    lines.append(
        f"  records here that {report.remote_ref} cannot see: {report.unseen_lines}"
    )
    if report.unseen:
        oldest, newest = report.unseen[0], report.unseen[-1]
        lines.append(f"    oldest: {oldest.finished}  {oldest.job} {oldest.outcome}")
        if newest is not oldest:
            lines.append(
                f"    newest: {newest.finished}  {newest.job} {newest.outcome}"
            )
    if report.uncommitted_lines:
        lines.append(f"    of which not yet committed: {report.uncommitted_lines}")
    if report.remote_only_lines:
        lines.append(
            f"  records on {report.remote_ref} this file lacks: "
            f"{report.remote_only_lines}  (behind, not ahead -- merge, do not worry)"
        )
    if report.error:
        lines.append(f"  note: {report.error}")
    lines.append(
        f"  {report.remote_ref} is as git last fetched it; --fetch asks GitHub first."
    )
    if report.pushed:
        lines.append(f"  pushed: {report.remote_ref} carries every record here.")
    else:
        lines.append(
            "  NOT PUSHED. A cloud session reads GitHub and will not see these runs."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------- cli --


def _parse_metric(text: str) -> tuple[str, float]:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"metric {text!r} must look like name=number")
    name, _, value = text.partition("=")
    try:
        return name.strip(), float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"metric {text!r} value is not a number")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.desk_agent.runlog",
        description="Append to and question the desk agent's run log.",
    )
    parser.add_argument("--log", type=pathlib.Path, default=DEFAULT_LOG)
    sub = parser.add_subparsers(dest="command", required=True)

    ap = sub.add_parser("append", help="record one run")
    ap.add_argument("--job", required=True)
    ap.add_argument("--outcome", required=True, choices=OUTCOMES)
    ap.add_argument("--summary", default="")
    ap.add_argument("--action", action="append", default=[], dest="actions")
    ap.add_argument("--blocker", action="append", default=[], dest="blockers")
    ap.add_argument("--metric", action="append", default=[], type=_parse_metric)
    ap.add_argument("--started", default="")
    ap.add_argument("--playbook-rev", default="")

    sp = sub.add_parser("summary", help="counts by outcome and job")
    sp.add_argument("--last", type=int, default=None)
    sp.add_argument("--job", default=None)

    rp = sub.add_parser("review", help="what the weekly review reads")
    rp.add_argument("--last", type=int, default=40)
    rp.add_argument("--min-count", type=int, default=3)

    up = sub.add_parser("unpushed", help="did the committed log reach the fork?")
    up.add_argument("--remote", default="jay", help="never a bare origin: see docs")
    up.add_argument("--branch", default="main")
    up.add_argument("--fetch", action="store_true", help="fetch the ref first")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "append":
        record = RunRecord(
            job=args.job,
            outcome=args.outcome,
            summary=args.summary,
            started=args.started,
            actions=list(args.actions),
            blockers=[Blocker.of(b) for b in args.blockers],
            metrics=dict(args.metric),
            playbook_rev=args.playbook_rev,
        )
        append_record(record, args.log)
        print(f"logged {record.job} {record.outcome} at {record.finished}")
        return 0

    if args.command == "unpushed":
        report = unpushed(
            remote=args.remote, branch=args.branch, path=args.log, fetch=args.fetch
        )
        print(render_push_report(report))
        # 0 pushed, 1 not pushed, 2 could not tell -- a wrapper must be able to
        # separate "behind" from "no idea", or a missing remote reads as clean.
        if report.ref_missing:
            return 2
        return 0 if report.pushed else 1

    records = read_records(args.log, job=getattr(args, "job", None), last=args.last)

    if args.command == "summary":
        summary = summarize(records)
        print(f"runs: {summary.total}")
        for outcome, count in summary.by_outcome.items():
            print(f"  {outcome}: {count}")
        print(f"actions: {summary.actions}")
        print(f"healthy: {'yes' if summary.healthy else 'no'}")
        return 0

    print(review(records, min_count=args.min_count))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

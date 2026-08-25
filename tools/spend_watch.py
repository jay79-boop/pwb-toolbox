"""Audit Claude session and Routine state for the patterns that drain a window.

Written after the 2026-08-24 drain (``docs/token-drain-2026-08-24.md``), which
was invisible until it hit 100%. Nothing warned at 50% or 80%, so the first
signal was a wall.

**What this tool will not do is invent a burn rate.** A single snapshot reports
each session's *lifetime* metered total, not what it spent recently, so a rate
cannot be derived from one file no matter how tempting the arithmetic looks --
that error is what made a $290 lifetime figure read as an hourly one. Rate
findings therefore appear only when a ``--baseline`` snapshot is supplied to
diff against. Structural findings need no baseline and are the ones that would
have caught this incident.

Input is the JSON from the ``list_sessions`` and ``list_triggers`` MCP tools::

    {"sessions": [...], "triggers": [...]}

Either key may be omitted. Usage::

    python tools/spend_watch.py audit snapshot.json
    python tools/spend_watch.py audit now.json --baseline an-hour-ago.json

The ``session`` command answers a different question -- "is the session I am in
right now getting expensive?" -- from the transcript the harness already writes
to disk. It needs no API call, so the warning never consumes the thing it is
warning about::

    python tools/spend_watch.py session ~/.claude/projects/<proj>/<id>.jsonl --quiet
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional

# A Routine prompt telling itself to schedule its own successor. This is the
# pattern that has no visible cadence in the Routine list and no single place
# to switch off.
_REARM = re.compile(
    r"re-?arm(?:s|ed|ing)?"
    r"|schedule (?:another|the next)"
    r"|check.{0,12}in .{0,20}(?:hours?|hour) out",
    re.I,
)

# Text immediately before a re-arm mention that makes it a *prohibition* or a
# *history lesson* rather than an instruction.
#
# This is not decoration. On 2026-08-24 every Routine prompt on the account was
# edited to end with "do NOT re-arm yourself", and several also explain that
# "an earlier version re-armed itself every ~3 hours". A bare substring search
# therefore fires hardest on exactly the Routines that were fixed -- the check
# would cry wolf on its own cure, and a check that always fires is one nobody
# reads.
_NOT_A_DIRECTIVE = re.compile(
    r"\b(?:not|never|n't|no|nor|stop|avoid|without|instead of"
    r"|previous|previously|earlier|prior|former|old|original"
    r"|was|were|had|used to|removed|deprecated|replaced)\b[^.;\n]{0,60}$",
    re.I,
)

# How far back to look for that negation. Long enough to span "Do NOT create a
# follow-up check-in and do NOT re-arm yourself", short enough not to reach
# into an unrelated preceding sentence -- which the trailing [^.;\n] guard
# also prevents.
_NEGATION_LOOKBACK = 80


def _rearms(prompt: str) -> bool:
    """True only for a prompt that *tells* a session to schedule its successor.

    Mentions that are forbidden ("do NOT re-arm yourself") or historical ("an
    earlier version re-armed itself") do not count. Getting this wrong in the
    permissive direction is worse than missing one: a detector that flags the
    remediation as the disease trains its reader to ignore it.
    """

    for match in _REARM.finditer(prompt):
        before = prompt[max(0, match.start() - _NEGATION_LOOKBACK) : match.start()]
        if _NOT_A_DIRECTIVE.search(before):
            continue
        return True
    return False


# Defaults chosen from the incident: twelve cloud sessions were live at once,
# and the two most expensive carried tens of millions of cached tokens.
CONCURRENCY_ALERT = 6
FAT_SESSION_CACHE_READS = 10_000_000

# Two Routine prompts this similar, on the same cron, are the same job.
# Set from the real pair: the spec-desk watch and its replacement differed
# only in a few sentences of preamble.
DUPLICATE_PROMPT_RATIO = 0.75

FIVE_HOURS = timedelta(hours=5)

# The account is not always limited by the same clock. ``rate_limit_info``
# names which one is binding, and assuming the five-hour window when a
# seven-day one is in force puts the inferred window start *in the future* --
# after which every check filtered by it silently measures an empty set.
WINDOW_SPANS = {
    "five_hour": timedelta(hours=5),
    "seven_day": timedelta(days=7),
}

# "How many sessions are awake at once" is a question about recency, not about
# whichever billing clock happens to be binding. Widening it to seven days
# would count a week of finished work as concurrency.
CONCURRENCY_HORIZON = timedelta(hours=5)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    title: str
    detail: str
    subjects: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "subjects": list(self.subjects),
        }


def parse_time(value: Optional[str]) -> Optional[datetime]:
    """Parse an RFC3339 timestamp, tolerating ``Z`` and fractional seconds."""

    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # Python rejects more than six fractional digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _usage(session: Dict[str, Any]) -> Dict[str, Any]:
    return (session.get("external_metadata") or {}).get("usage") or {}


def metered(session: Dict[str, Any]) -> float:
    """Lifetime metered total for a session, in API-equivalent dollars.

    Not a charge. On a subscription this is a gauge of window consumption.
    """

    try:
        return float(_usage(session).get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cache_reads(session: Dict[str, Any]) -> int:
    try:
        return int(_usage(session).get("cache_read_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def active_since(
    sessions: Iterable[Dict[str, Any]], cutoff: datetime
) -> List[Dict[str, Any]]:
    """Sessions whose last activity is at or after ``cutoff``."""

    out = []
    for s in sessions:
        updated = parse_time(s.get("updated_at"))
        if updated is not None and updated >= cutoff:
            out.append(s)
    return out


def find_self_rearming(triggers: Iterable[Dict[str, Any]]) -> List[Finding]:
    """Routines bound to a persistent session whose prompt re-arms itself.

    Both halves are needed for the expensive version: the re-arm gives it no
    stopping point, and the persistent binding makes every wake re-read that
    session's whole accumulated history.
    """

    findings = []
    for t in triggers:
        prompt = _prompt_of(t)
        persistent = t.get("persistent_session_id")
        rearms = _rearms(prompt)
        if not (persistent and rearms):
            continue
        findings.append(
            Finding(
                severity="high",
                code="self-rearming-persistent",
                title=f"Routine {t.get('name') or t.get('id')!r} re-arms itself into a persistent session",
                detail=(
                    "Its prompt schedules its own successor, so it has no visible "
                    "cadence and no single place to switch off, and it fires into "
                    f"session {persistent} whose entire history is re-read on every "
                    "wake. Replace with a cron plus create_new_session_on_fire."
                ),
                subjects=[str(t.get("id"))],
            )
        )
    return findings


def find_persistent_triggers(triggers: Iterable[Dict[str, Any]]) -> List[Finding]:
    """Routines firing into a persistent session but not re-arming."""

    findings = []
    for t in triggers:
        if not t.get("persistent_session_id"):
            continue
        if _rearms(_prompt_of(t)):
            continue  # already reported at high severity
        findings.append(
            Finding(
                severity="medium",
                code="persistent-session-trigger",
                title=f"Routine {t.get('name') or t.get('id')!r} fires into a persistent session",
                detail=(
                    "Every firing re-reads the bound session's accumulated context, "
                    "so the cost grows with the session's age rather than staying "
                    "flat. Prefer a fresh session per fire unless it genuinely needs "
                    "the prior conversation."
                ),
                subjects=[str(t.get("id"))],
            )
        )
    return findings


def _prompt_of(trigger: Dict[str, Any]) -> str:
    """Best-effort extraction of a Routine's prompt text."""

    events = ((trigger.get("job_config") or {}).get("ccr") or {}).get("events") or []
    parts = []
    for event in events:
        message = ((event.get("data") or {}).get("message") or {}).get("content")
        if isinstance(message, str):
            parts.append(message)
    return "\n".join(parts)


def _normalised(prompt: str) -> str:
    """Prompt text reduced to what a duplicate check should compare."""

    return " ".join(prompt.lower().split())


def find_duplicate_triggers(
    triggers: Iterable[Dict[str, Any]],
    similarity: float = DUPLICATE_PROMPT_RATIO,
) -> List[Finding]:
    """Two enabled Routines on the same cron doing near-identical work.

    "Delete the old Routine in the same breath as creating its replacement" was
    already a written rule and had nothing behind it. On 2026-08-24 a
    superseded spec-desk watch and its own replacement were both live on
    ``0 2,14 * * *``, firing sixty-two seconds apart -- one of them bound to a
    persistent session.

    Only Routines *explicitly* marked enabled are compared. A missing flag is
    treated as disabled rather than assumed live, so this reports double-fires
    that are really happening instead of pairs that merely could.
    """

    live = [
        t
        for t in triggers
        if t.get("enabled") and (t.get("cron_expression") or "").strip()
    ]
    findings = []
    for i, first in enumerate(live):
        for second in live[i + 1 :]:
            if first.get("cron_expression") != second.get("cron_expression"):
                continue
            ratio = SequenceMatcher(
                None, _normalised(_prompt_of(first)), _normalised(_prompt_of(second))
            ).ratio()
            if ratio < similarity:
                continue
            findings.append(
                Finding(
                    severity="high",
                    code="duplicate-trigger",
                    title=(
                        f"Routines {first.get('name') or first.get('id')!r} and "
                        f"{second.get('name') or second.get('id')!r} are the same job"
                    ),
                    detail=(
                        f"Both are enabled on {first.get('cron_expression')!r} and "
                        f"their prompts are {ratio:.0%} identical, so every firing "
                        "happens twice. A superseded Routine left live alongside "
                        "its replacement is indistinguishable from an intended one. "
                        "Delete whichever is obsolete."
                    ),
                    subjects=[str(first.get("id")), str(second.get("id"))],
                )
            )
    return findings


def find_fat_sessions(
    sessions: Iterable[Dict[str, Any]], threshold: int = FAT_SESSION_CACHE_READS
) -> List[Finding]:
    """Sessions carrying enough context that waking them is expensive."""

    findings = []
    for s in sessions:
        reads = cache_reads(s)
        if reads < threshold:
            continue
        findings.append(
            Finding(
                severity="medium",
                code="expensive-to-wake",
                title=f"Session {s.get('title') or s.get('id')!r} carries {reads / 1e6:.1f}M cache reads",
                detail=(
                    "Any wake re-reads that context before doing a single useful "
                    "thing. Archive it if its work is finished, and never bind a "
                    "scheduled check-in to it."
                ),
                subjects=[str(s.get("id"))],
            )
        )
    return findings


def find_concurrency(
    sessions: Iterable[Dict[str, Any]],
    cutoff: datetime,
    alert_at: int = CONCURRENCY_ALERT,
) -> List[Finding]:
    """Too many sessions active at once — the first-order term in a drain."""

    live = active_since(sessions, cutoff)
    if len(live) < alert_at:
        return []
    total = sum(metered(s) for s in live)
    return [
        Finding(
            severity="high",
            code="concurrency",
            title=f"{len(live)} sessions active in the same {int(CONCURRENCY_HORIZON.total_seconds() // 3600)}-hour span",
            detail=(
                f"They carry {total:,.2f} in lifetime metered total (not a charge, "
                "and not all of it in this window). Concurrency, not any single "
                "runaway, is what exhausts a window."
            ),
            subjects=[str(s.get("id")) for s in live],
        )
    ]


def limit_type(sessions: Iterable[Dict[str, Any]]) -> Optional[str]:
    """Which rate-limit clock the account is currently being held to."""

    for s in sessions:
        info = (s.get("external_metadata") or {}).get("rate_limit_info") or {}
        kind = info.get("rateLimitType")
        if isinstance(kind, str) and kind:
            return kind
    return None


def window_span(sessions: Iterable[Dict[str, Any]]) -> timedelta:
    """The length of the window currently in force.

    Defaults to five hours when the payload does not say, because that is the
    shorter and therefore safer assumption: it under-reports how much history
    belongs to the window rather than sweeping in a week of finished work.
    """

    return WINDOW_SPANS.get(limit_type(sessions) or "", FIVE_HOURS)


def latest_activity(sessions: Iterable[Dict[str, Any]]) -> Optional[datetime]:
    """The most recent ``updated_at`` in the snapshot.

    Stands in for "now" so the module needs no clock, which is what lets every
    check be tested on a fixed synthetic payload.
    """

    latest = None
    for s in sessions:
        seen = parse_time(s.get("updated_at"))
        if seen is not None and (latest is None or seen > latest):
            latest = seen
    return latest


def window_start(sessions: Iterable[Dict[str, Any]]) -> Optional[datetime]:
    """Infer the current window's start from any session's reset time.

    Reads ``rateLimitType`` rather than assuming five hours. On 2026-08-24 the
    binding limit was ``seven_day``; subtracting five hours from that reset
    returned a cutoff four days in the *future*, so the concurrency check found
    zero live sessions and passed. A check that passes because it measured
    nothing is worse than no check.
    """

    latest = None
    for s in sessions:
        info = (s.get("external_metadata") or {}).get("rate_limit_info") or {}
        resets = info.get("resetsAt")
        if not isinstance(resets, (int, float)):
            continue
        end = datetime.fromtimestamp(resets, tz=timezone.utc)
        if latest is None or end > latest:
            latest = end
    if latest is None:
        return None
    return latest.replace(microsecond=0) - window_span(sessions)


def find_rate(
    sessions: Iterable[Dict[str, Any]],
    baseline: Iterable[Dict[str, Any]],
) -> List[Finding]:
    """Metered growth between two snapshots. Requires a baseline by design."""

    before = {s.get("id"): metered(s) for s in baseline}
    grew = []
    delta_total = 0.0
    for s in sessions:
        delta = metered(s) - before.get(s.get("id"), 0.0)
        if delta > 0:
            delta_total += delta
            grew.append((s, delta))
    if not grew:
        return []
    grew.sort(key=lambda pair: pair[1], reverse=True)
    top = ", ".join(f"{s.get('title') or s.get('id')} (+{d:,.2f})" for s, d in grew[:3])
    return [
        Finding(
            severity="low",
            code="growth",
            title=f"{len(grew)} sessions grew by {delta_total:,.2f} between snapshots",
            detail=f"Largest movers: {top}.",
            subjects=[str(s.get("id")) for s, _ in grew],
        )
    ]


# Cache reads are what a session re-pays on every single turn, so they -- not
# output -- are what makes one expensive to keep going. The first tier matches
# FAT_SESSION_CACHE_READS deliberately: the point at which this tool already
# calls a session expensive to wake is the point at which its owner should be
# told it is getting big.
SESSION_SIZE_TIERS = (
    (50_000_000, "high", "very large -- start a fresh session for the next task"),
    (25_000_000, "medium", "large -- finish the current thread, then start fresh"),
    (10_000_000, "low", "getting big -- worth splitting the next task out"),
)


def transcript_usage(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Total the per-turn usage a session transcript already records.

    Costs nothing to compute: the numbers are written to disk by the harness as
    the session runs, so a size check needs no API call and no tokens. That is
    the whole reason this reads a transcript rather than calling
    ``list_sessions`` -- a warning that itself consumes the window is not a
    warning worth having.
    """

    totals = {
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "turns": 0,
    }
    for record in records:
        usage = (record.get("message") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        totals["turns"] += 1
        for src, dst in (
            ("cache_read_input_tokens", "cache_read_tokens"),
            ("cache_creation_input_tokens", "cache_write_tokens"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
        ):
            value = usage.get(src)
            if isinstance(value, int):
                totals[dst] += value
    return totals


def find_session_size(usage: Dict[str, int]) -> List[Finding]:
    """Warn once the session's own accumulated context is worth acting on.

    Silent below the first tier, by design. A warning that prints on every
    session is wallpaper; this one exists to interrupt exactly when the answer
    to "should I keep going in here?" has changed.
    """

    reads = usage.get("cache_read_tokens", 0)
    for threshold, severity, advice in SESSION_SIZE_TIERS:
        if reads < threshold:
            continue
        out = usage.get("output_tokens", 0)
        ratio = f"{reads // out}:1" if out else "n/a"
        return [
            Finding(
                severity=severity,
                code="session-size",
                title=f"This session has re-read {reads / 1e6:.1f}M tokens of context",
                detail=(
                    f"Across {usage.get('turns', 0)} turns it has produced "
                    f"{out:,} tokens of output, a read-to-write ratio of {ratio}. "
                    "Every further turn re-reads the whole conversation before "
                    f"doing anything, and that price only goes up -- {advice}."
                ),
            )
        ]
    return []


def read_transcript(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL session transcript, skipping anything unparseable.

    A malformed or half-written line is dropped rather than repaired: this runs
    from a hook on every prompt, and a size check that crashes the session it
    was meant to protect is a worse outcome than one that under-counts.
    """

    records = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


def audit(
    payload: Dict[str, Any],
    baseline: Optional[Dict[str, Any]] = None,
) -> List[Finding]:
    """Run every check and return findings, most severe first."""

    sessions = payload.get("sessions") or []
    triggers = payload.get("triggers") or []

    findings: List[Finding] = []
    findings += find_self_rearming(triggers)
    findings += find_persistent_triggers(triggers)
    findings += find_duplicate_triggers(triggers)
    findings += find_fat_sessions(sessions)

    # Anchored to the newest activity in the snapshot rather than to the rate
    # limit's reset, so the answer means "awake at the same time" whichever
    # clock is currently binding.
    seen = latest_activity(sessions)
    if seen is not None:
        findings += find_concurrency(sessions, seen - CONCURRENCY_HORIZON)

    if baseline is not None:
        findings += find_rate(sessions, baseline.get("sessions") or [])

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.code))
    return findings


def render(findings: List[Finding]) -> str:
    if not findings:
        return "No findings. Nothing here matches a known drain pattern."
    lines = []
    for f in findings:
        lines.append(f"[{f.severity.upper()}] {f.title}")
        lines.append(f"    {f.detail}")
        if len(f.subjects) <= 4:
            for s in f.subjects:
                lines.append(f"    - {s}")
        else:
            for s in f.subjects[:4]:
                lines.append(f"    - {s}")
            lines.append(f"    - ... and {len(f.subjects) - 4} more")
        lines.append("")
    return "\n".join(lines).rstrip()


def _load(path: str) -> Dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    audit_cmd = sub.add_parser("audit", help="audit a session/trigger snapshot")
    audit_cmd.add_argument("snapshot", help="JSON file, or - for stdin")
    audit_cmd.add_argument(
        "--baseline",
        help="earlier snapshot to diff against; required for any rate finding",
    )
    audit_cmd.add_argument("--json", action="store_true", help="emit JSON")

    size_cmd = sub.add_parser(
        "session", help="warn if this session's own context has grown expensive"
    )
    size_cmd.add_argument("transcript", help="path to the session's .jsonl transcript")
    size_cmd.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing below the first tier (for hook use)",
    )
    size_cmd.add_argument("--json", action="store_true", help="emit JSON")

    args = parser.parse_args(argv)

    if args.command == "session":
        usage = transcript_usage(read_transcript(args.transcript))
        findings = find_session_size(usage)
        if args.json:
            print(
                json.dumps(
                    {"usage": usage, "findings": [f.as_dict() for f in findings]},
                    indent=2,
                )
            )
        elif findings:
            print(render(findings))
        elif not args.quiet:
            print(
                f"{usage['cache_read_tokens'] / 1e6:.1f}M cache reads over "
                f"{usage['turns']} turns. Nothing worth acting on yet."
            )
        return 0

    payload = _load(args.snapshot)
    baseline = _load(args.baseline) if args.baseline else None
    findings = audit(payload, baseline)

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        print(render(findings))
    return 1 if any(f.severity == "high" for f in findings) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

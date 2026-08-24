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
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional

# A Routine prompt telling itself to schedule its own successor. This is the
# pattern that has no visible cadence in the Routine list and no single place
# to switch off.
_REARM = re.compile(
    r"re-?arm|schedule (?:another|the next)|check.{0,12}in .{0,20}(?:hours?|hour) out",
    re.I,
)

# Defaults chosen from the incident: twelve cloud sessions were live at once,
# and the two most expensive carried tens of millions of cached tokens.
CONCURRENCY_ALERT = 6
FAT_SESSION_CACHE_READS = 10_000_000

FIVE_HOURS = timedelta(hours=5)

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
        rearms = bool(_REARM.search(prompt))
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
        if _REARM.search(_prompt_of(t)):
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
            title=f"{len(live)} sessions active in the current window",
            detail=(
                f"They carry {total:,.2f} in lifetime metered total (not a charge, "
                "and not all of it in this window). Concurrency, not any single "
                "runaway, is what exhausts a window."
            ),
            subjects=[str(s.get("id")) for s in live],
        )
    ]


def window_start(sessions: Iterable[Dict[str, Any]]) -> Optional[datetime]:
    """Infer the current five-hour window's start from any session's reset time."""

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
    return latest.replace(microsecond=0) - FIVE_HOURS


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
    findings += find_fat_sessions(sessions)

    start = window_start(sessions)
    if start is not None:
        findings += find_concurrency(sessions, start)

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

    args = parser.parse_args(argv)
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

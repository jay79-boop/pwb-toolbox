#!/usr/bin/env python
"""Walk a business through the AI & automation readiness framework.

The framework is a fixed sequence of twelve phases — audit the tools, map the
process, find the bottlenecks, score AI/automation readiness, prioritize,
ship the quick wins, design the target process, present to stakeholders,
revise, get approval, plan the rollout, go live. The order is the point:
nothing gets implemented before a stakeholder has seen and approved it, and
nothing gets presented before the analysis behind it exists on disk.

This tool is the tracker, not the brain. The analysis in each phase is done
by whoever is running the engagement — usually a Claude session driving the
``engagement-flow`` skill — and lands as a markdown deliverable in the
engagement's folder. ``advance`` refuses to move past a phase whose
deliverable is missing or empty, ``approval`` refuses to pass without a named
approver, and ``present`` refuses until the stakeholder deck has actually
been built. The gates are the contract; everything else is bookkeeping.

Engagement data lives under ``engagements/`` which is gitignored: this fork
is public, and a client's process map is nobody's business but theirs.

Commands:

    new        Open an engagement for a business.
    list       Every engagement and where it stands.
    status     One engagement: phases done, current phase, what unblocks it.
    note       Record a lesson against the current phase (or --phase KEY).
    advance    Complete the current phase, if its gate passes.
    deck       Render the stakeholder deck (deck.html) from the deliverables.
    retro      Aggregate lessons across engagements, per phase — the raw
               material for improving how the next engagement runs.
    export-flow  Write the engagement as a Flow Canvas JSON (flow.json) so
               static/flow-canvas.html can show it as a visual map.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "engagements"

DECK_FILENAME = "deck.html"
STATE_FILENAME = "engagement.json"


@dataclass(frozen=True)
class Phase:
    key: str
    title: str
    deliverable: str | None
    done_when: str
    optional: bool = False


PHASES: tuple[Phase, ...] = (
    Phase(
        "audit",
        "Audit tools & systems",
        "01-audit.md",
        "every tool, system, data store and subscription is inventoried, "
        "with who uses it, what it costs, and what it talks to",
    ),
    Phase(
        "map",
        "Map current process",
        "02-process-map.md",
        "the end-to-end process is written down as it actually runs: steps, "
        "actors, handoffs, inputs/outputs, and rough time per step",
    ),
    Phase(
        "bottlenecks",
        "Identify bottlenecks",
        "03-bottlenecks.md",
        "constraints are ranked by cost with evidence from the map, not vibes",
    ),
    Phase(
        "readiness",
        "Assess AI & automation readiness",
        "04-readiness.md",
        "each process step is scored for automatability: data availability, "
        "repetitiveness, error tolerance, and where a human must stay in "
        "the loop",
    ),
    Phase(
        "prioritize",
        "Prioritize improvements",
        "05-priorities.md",
        "improvements are ranked impact-versus-effort and the quick wins " "are named",
    ),
    Phase(
        "quick_wins",
        "Implement quick wins",
        "06-quick-wins.md",
        "the low-risk items shipped, with a before/after measurement for each",
    ),
    Phase(
        "design",
        "Design target process",
        "07-target-design.md",
        "the future process is specified: what runs automatically, what an "
        "AI agent does, what stays human, and the controls around each",
    ),
    Phase(
        "present",
        "Stakeholder presentation",
        "08-feedback.md",
        "the deck was presented and the stakeholders' feedback is captured " "verbatim",
    ),
    Phase(
        "revise",
        "Revise proposal",
        "09-revision.md",
        "the proposal is amended to answer the feedback (skippable when "
        "there was none)",
        optional=True,
    ),
    Phase(
        "approval",
        "Stakeholder approval",
        None,
        "a named stakeholder has approved the proposal",
    ),
    Phase(
        "plan",
        "Plan & schedule implementation",
        "10-implementation-plan.md",
        "milestones, owners, dates, and a rollback path exist for everything "
        "approved",
    ),
    Phase(
        "live",
        "Go live & update workspace",
        "11-golive.md",
        "the new process is running, the workspace/docs reflect the live "
        "state, and a metrics baseline is recorded",
    ),
)

PHASE_BY_KEY = {p.key: p for p in PHASES}


class EngagementError(ValueError):
    """A gate refused, or the request does not make sense. The message is
    written to be shown to the operator as-is."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise EngagementError(f"cannot make a slug out of {name!r}")
    return slug


def _state_path(root: Path, slug: str) -> Path:
    return root / slug / STATE_FILENAME


def load(root: Path, slug: str) -> dict:
    path = _state_path(root, slug)
    if not path.exists():
        known = ", ".join(sorted(e["slug"] for e in list_engagements(root))) or "none"
        raise EngagementError(f"no engagement {slug!r} under {root} (known: {known})")
    return json.loads(path.read_text(encoding="utf-8"))


def save(root: Path, data: dict) -> None:
    path = _state_path(root, data["slug"])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def new_engagement(root: Path, name: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    slug = slugify(name)
    folder = root / slug
    if _state_path(root, slug).exists():
        raise EngagementError(f"engagement {slug!r} already exists")
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "slug": slug,
        "created": today.isoformat(),
        "completed": None,
        "approval": None,
        "phases": {
            p.key: {"status": "pending", "completed": None, "notes": []} for p in PHASES
        },
    }
    save(root, data)
    return data


def list_engagements(root: Path) -> list[dict]:
    out = []
    if root.exists():
        for state in sorted(root.glob(f"*/{STATE_FILENAME}")):
            out.append(json.loads(state.read_text(encoding="utf-8")))
    return out


def current_phase(data: dict) -> Phase | None:
    """The first phase that is neither done nor skipped, or None when the
    engagement is finished."""
    for p in PHASES:
        if data["phases"][p.key]["status"] == "pending":
            return p
    return None


def _deliverable_ready(root: Path, slug: str, phase: Phase) -> Path:
    path = root / slug / phase.deliverable
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise EngagementError(
            f"phase {phase.key!r} is gated on its deliverable: write "
            f"{path} first. Done when {phase.done_when}."
        )
    return path


def advance(
    root: Path,
    slug: str,
    skip: bool = False,
    approved_by: str | None = None,
    today: dt.date | None = None,
) -> Phase:
    """Complete (or skip) the current phase and return it."""
    today = today or dt.date.today()
    data = load(root, slug)
    phase = current_phase(data)
    if phase is None:
        raise EngagementError(f"engagement {slug!r} is already complete")

    if skip:
        if not phase.optional:
            raise EngagementError(
                f"phase {phase.key!r} cannot be skipped — done when "
                f"{phase.done_when}"
            )
        data["phases"][phase.key]["status"] = "skipped"
    else:
        if phase.deliverable:
            _deliverable_ready(root, slug, phase)
        if phase.key == "present" and not (root / slug / DECK_FILENAME).exists():
            raise EngagementError(
                "present is gated on the deck: build it first with "
                f"`python tools/engagement.py deck {slug}`"
            )
        if phase.key == "approval":
            if not approved_by:
                raise EngagementError(
                    "approval needs a named approver: pass --approved-by"
                )
            data["approval"] = {"approved_by": approved_by, "date": today.isoformat()}
        data["phases"][phase.key]["status"] = "done"
        data["phases"][phase.key]["completed"] = today.isoformat()

    if current_phase(data) is None:
        data["completed"] = today.isoformat()
    save(root, data)
    return phase


def add_note(root: Path, slug: str, text: str, phase_key: str | None = None) -> str:
    """Record a lesson against a phase (default: the current one). Returns
    the key it landed on."""
    text = text.strip()
    if not text:
        raise EngagementError("an empty note records nothing")
    data = load(root, slug)
    if phase_key is None:
        phase = current_phase(data)
        if phase is None:
            raise EngagementError(
                f"engagement {slug!r} is complete — name a phase with --phase"
            )
        phase_key = phase.key
    if phase_key not in PHASE_BY_KEY:
        known = ", ".join(p.key for p in PHASES)
        raise EngagementError(f"unknown phase {phase_key!r} (one of: {known})")
    data["phases"][phase_key]["notes"].append(text)
    save(root, data)
    return phase_key


def retro(root: Path, phase_key: str | None = None) -> dict[str, list[tuple[str, str]]]:
    """Lessons across every engagement, grouped by phase key. Each entry is
    (engagement slug, note). This is what the next engagement reads before
    starting a phase."""
    if phase_key is not None and phase_key not in PHASE_BY_KEY:
        known = ", ".join(p.key for p in PHASES)
        raise EngagementError(f"unknown phase {phase_key!r} (one of: {known})")
    grouped: dict[str, list[tuple[str, str]]] = {}
    for data in list_engagements(root):
        for p in PHASES:
            if phase_key is not None and p.key != phase_key:
                continue
            for note in data["phases"][p.key]["notes"]:
                grouped.setdefault(p.key, []).append((data["slug"], note))
    return grouped


def status_text(root: Path, slug: str) -> str:
    data = load(root, slug)
    lines = [f"{data['name']}  ({slug}, opened {data['created']})"]
    for p in PHASES:
        state = data["phases"][p.key]
        mark = {"done": "x", "skipped": "-", "pending": " "}[state["status"]]
        when = f"  {state['completed']}" if state["completed"] else ""
        lines.append(f"  [{mark}] {p.title}{when}")
    phase = current_phase(data)
    if phase is None:
        approval = data.get("approval") or {}
        lines.append(
            f"Complete {data['completed']}"
            + (
                f" — approved by {approval['approved_by']} {approval['date']}"
                if approval
                else ""
            )
        )
    else:
        lines.append(f"Current phase: {phase.key} — {phase.title}")
        if phase.deliverable:
            lines.append(f"  Deliverable: engagements/{slug}/{phase.deliverable}")
        if phase.key == "present":
            lines.append(
                f"  Also needs the deck: python tools/engagement.py deck {slug}"
            )
        if phase.key == "approval":
            lines.append("  Advance with --approved-by NAME")
        lines.append(f"  Done when {phase.done_when}.")
    return "\n".join(lines)


# --- deck rendering ---------------------------------------------------------
#
# The deck is a single self-contained HTML file so it opens from file:// with
# no server, no build step, and no network — same contract as the journal and
# docs/index.html. Deliverables are markdown-ish; the renderer below covers
# what the framework actually writes (headings, bullets, bold, fenced code)
# and escapes everything else rather than guessing.


def _md_inline(text: str) -> str:
    text = escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _md_html(text: str) -> str:
    out: list[str] = []
    in_list = False
    in_code = False
    paragraph: list[str] = []

    def flush_paragraph():
        if paragraph:
            out.append(f"<p>{_md_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in text.splitlines():
        if line.strip().startswith("```"):
            flush_paragraph()
            close_list()
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(escape(line))
            continue
        heading = re.match(r"(#{1,4})\s+(.*)", line)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)) + 2, 5)
            out.append(f"<h{level}>{_md_inline(heading.group(2))}</h{level}>")
            continue
        bullet = re.match(r"\s*[-*]\s+(.*)", line)
        if bullet:
            flush_paragraph()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_md_inline(bullet.group(1))}</li>")
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        close_list()
        paragraph.append(line.strip())
    if in_code:
        out.append("</pre>")
    flush_paragraph()
    close_list()
    return "\n".join(out)


_DECK_CSS = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: "Segoe UI", -apple-system, "Helvetica Neue", Arial,
         sans-serif; background: #f8fafc; color: #0f172a; line-height: 1.6; }
  main { max-width: 860px; margin: 0 auto; padding: 3rem 1.5rem 5rem; }
  header { border-bottom: 3px solid #0f172a; padding-bottom: 1.5rem;
           margin-bottom: 2rem; }
  header h1 { font-size: 2rem; line-height: 1.2; }
  header .kicker { text-transform: uppercase; letter-spacing: 0.12em;
                   font-size: 0.8rem; color: #475569; margin-bottom: 0.5rem; }
  header .date { color: #64748b; margin-top: 0.5rem; }
  .phases { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 1.5rem 0; }
  .phase { font-size: 0.75rem; padding: 0.25rem 0.6rem; border-radius: 999px;
           border: 1px solid #cbd5e1; color: #64748b; background: #fff; }
  .phase.done { border-color: #16a34a; color: #166534; background: #f0fdf4; }
  .phase.current { border-color: #0f172a; color: #0f172a; font-weight: 600; }
  section { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
            padding: 1.5rem 2rem; margin-bottom: 1.5rem; }
  section > h2 { font-size: 1.25rem; margin-bottom: 0.75rem;
                 padding-bottom: 0.5rem; border-bottom: 1px solid #e2e8f0; }
  section h3 { font-size: 1.05rem; margin: 1rem 0 0.4rem; }
  section h4, section h5 { margin: 0.8rem 0 0.3rem; }
  section p { margin: 0.5rem 0; }
  section ul { margin: 0.5rem 0 0.5rem 1.4rem; }
  section pre { background: #0f172a; color: #f8fafc; padding: 1rem;
                border-radius: 6px; overflow-x: auto; font-size: 0.85rem;
                margin: 0.75rem 0; }
  .approval { border-left: 4px solid #16a34a; }
  footer { color: #94a3b8; font-size: 0.8rem; text-align: center;
           margin-top: 3rem; }
"""


def build_deck(root: Path, slug: str, today: dt.date | None = None) -> Path:
    today = today or dt.date.today()
    data = load(root, slug)
    folder = root / slug
    active = current_phase(data)

    chips = []
    for p in PHASES:
        status = data["phases"][p.key]["status"]
        cls = "phase done" if status == "done" else "phase"
        if active and p.key == active.key:
            cls = "phase current"
        chips.append(f'<span class="{cls}">{escape(p.title)}</span>')

    sections = []
    for p in PHASES:
        if not p.deliverable:
            continue
        path = folder / p.deliverable
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        sections.append(
            f"<section><h2>{escape(p.title)}</h2>{_md_html(body)}</section>"
        )

    approval = data.get("approval")
    if approval:
        sections.append(
            '<section class="approval"><h2>Approval</h2><p>Approved by '
            f"<strong>{escape(approval['approved_by'])}</strong> on "
            f"{escape(approval['date'])}.</p></section>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(data['name'])} — AI &amp; Automation Readiness</title>
<style>{_DECK_CSS}</style>
</head>
<body>
<main>
<header>
  <div class="kicker">AI &amp; Automation Readiness — Findings &amp; Proposal</div>
  <h1>{escape(data['name'])}</h1>
  <div class="date">Prepared {today.isoformat()}</div>
  <div class="phases">{''.join(chips)}</div>
</header>
{''.join(sections)}
<footer>Generated by tools/engagement.py — engagements/{escape(slug)}</footer>
</main>
</body>
</html>
"""
    out = folder / DECK_FILENAME
    out.write_text(html, encoding="utf-8")
    return out


# --- Flow Canvas export -----------------------------------------------------
#
# static/flow-canvas.html imports {theme?, nodes, edges}; this writes the
# twelve phases as a left-to-right chain in that shape, so an engagement can
# be looked at instead of read. Statuses map done->live, the current
# phase->working, everything still ahead->draft; a skipped phase stays draft
# with the skip recorded in its notes.

FLOW_FILENAME = "flow.json"

_FLOW_OWNERS = {
    "present": "person",
    "approval": "person",
    "quick_wins": "auto",
    "live": "auto",
}


def export_flow(root: Path, slug: str) -> Path:
    data = load(root, slug)
    active = current_phase(data)
    nodes = []
    edges = []
    for i, p in enumerate(PHASES):
        state = data["phases"][p.key]
        if state["status"] == "done":
            status = "live"
        elif active is not None and p.key == active.key:
            status = "working"
        else:
            status = "draft"
        notes = "skipped" if state["status"] == "skipped" else ""
        nodes.append(
            {
                "id": p.key,
                "title": p.title,
                "status": status,
                "owner": _FLOW_OWNERS.get(p.key, "ai"),
                "dur": "",
                "decision": p.key == "approval",
                "notes": notes,
                "x": 60 + i * 240,
                "y": 300,
            }
        )
        if i:
            edges.append(
                {
                    "id": f"e-{PHASES[i - 1].key}-{p.key}",
                    "from": PHASES[i - 1].key,
                    "to": p.key,
                    "label": "",
                }
            )
    flow = {"theme": "slate", "nodes": nodes, "edges": edges}
    out = root / slug / FLOW_FILENAME
    out.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")
    return out


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engagement.py",
        description="Track a business through the AI & automation readiness "
        "framework (docs/ai-readiness-framework.md).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="engagements directory (default: engagements/ in the repo)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="open an engagement for a business")
    p.add_argument("name", help='business name, e.g. "Acme Logistics"')

    sub.add_parser("list", help="every engagement and where it stands")

    p = sub.add_parser("status", help="phases done, current phase, next gate")
    p.add_argument("slug")

    p = sub.add_parser("note", help="record a lesson against a phase")
    p.add_argument("slug")
    p.add_argument("text")
    p.add_argument("--phase", help="phase key (default: the current phase)")

    p = sub.add_parser("advance", help="complete the current phase")
    p.add_argument("slug")
    p.add_argument("--skip", action="store_true", help="skip an optional phase")
    p.add_argument("--approved-by", help="approver name (approval phase only)")

    p = sub.add_parser("deck", help="render the stakeholder deck")
    p.add_argument("slug")

    p = sub.add_parser("retro", help="lessons across engagements, per phase")
    p.add_argument("--phase", help="only this phase key")

    p = sub.add_parser(
        "export-flow",
        help="write flow.json for static/flow-canvas.html (Import button)",
    )
    p.add_argument("slug")

    args = parser.parse_args(argv)
    try:
        if args.command == "new":
            data = new_engagement(args.root, args.name)
            print(f"Opened {data['slug']!r} under {args.root / data['slug']}")
            print(status_text(args.root, data["slug"]))
        elif args.command == "list":
            rows = list_engagements(args.root)
            if not rows:
                print(f"No engagements under {args.root}")
            for data in rows:
                phase = current_phase(data)
                where = f"phase: {phase.key}" if phase else "complete"
                print(f"{data['slug']:30} {data['name']:30} {where}")
        elif args.command == "status":
            print(status_text(args.root, args.slug))
        elif args.command == "note":
            key = add_note(args.root, args.slug, args.text, args.phase)
            print(f"Noted on {key!r}")
        elif args.command == "advance":
            phase = advance(
                args.root, args.slug, skip=args.skip, approved_by=args.approved_by
            )
            done = "Skipped" if args.skip else "Completed"
            print(f"{done} {phase.key!r}")
            print(status_text(args.root, args.slug))
        elif args.command == "deck":
            out = build_deck(args.root, args.slug)
            print(f"Deck written to {out}")
        elif args.command == "export-flow":
            out = export_flow(args.root, args.slug)
            print(f"Flow written to {out} — open static/flow-canvas.html and Import it")
        elif args.command == "retro":
            grouped = retro(args.root, args.phase)
            if not grouped:
                print("No lessons recorded yet")
            for key, entries in grouped.items():
                print(f"{key} — {PHASE_BY_KEY[key].title}")
                for slug, note in entries:
                    print(f"  [{slug}] {note}")
    except EngagementError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # Downstream (e.g. `... | head`) closed the pipe: that is their say,
        # not our failure. Point stdout at devnull so interpreter shutdown
        # does not raise a second time trying to flush it.
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 0
    raise SystemExit(code)

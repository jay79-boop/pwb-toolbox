"""The framework's value is the order of its gates, so the gates are what
these tests pin.

An engagement that reaches implementation without a stakeholder having
approved it, or presents a deck that was never built, has silently become a
different framework — one that ships first and asks later. Every gate
refusal here is therefore an assertion, not an afterthought: no advancing
past a phase whose deliverable is missing or empty, no presenting without
the deck, no approval without a named approver, no skipping a mandatory
phase. The phase order itself is pinned too, because the sequence *is* the
framework.

Everything runs against a tmp_path root; nothing touches the repo's real
``engagements/`` folder (which is gitignored precisely because its contents
are client data).
"""

import datetime as dt

import pytest

from tools.engagement import (
    DECK_FILENAME,
    PHASES,
    EngagementError,
    add_note,
    advance,
    build_deck,
    current_phase,
    list_engagements,
    load,
    main,
    new_engagement,
    retro,
    slugify,
    status_text,
)

TODAY = dt.date(2026, 8, 22)


def _write_deliverable(root, slug, phase, text="## Findings\n\n- one real line\n"):
    (root / slug / phase.deliverable).write_text(text, encoding="utf-8")


def _advance_through(root, slug, upto_key):
    """Advance phase by phase, writing a stub deliverable for each, stopping
    just before `upto_key` becomes current work."""
    while True:
        data = load(root, slug)
        phase = current_phase(data)
        if phase is None or phase.key == upto_key:
            return phase
        if phase.deliverable:
            _write_deliverable(root, slug, phase)
        if phase.key == "present":
            build_deck(root, slug, today=TODAY)
        advance(
            root,
            slug,
            approved_by="Jay" if phase.key == "approval" else None,
            today=TODAY,
        )


def test_phase_order_is_the_framework():
    assert [p.key for p in PHASES] == [
        "audit",
        "map",
        "bottlenecks",
        "readiness",
        "prioritize",
        "quick_wins",
        "design",
        "present",
        "revise",
        "approval",
        "plan",
        "live",
    ]
    # Only revision is skippable; every analysis, gate, and go-live is not.
    assert [p.key for p in PHASES if p.optional] == ["revise"]


def test_slugify():
    assert slugify("Acme Logistics, Inc.") == "acme-logistics-inc"
    with pytest.raises(EngagementError):
        slugify("!!!")


def test_new_engagement_starts_at_audit(tmp_path):
    data = new_engagement(tmp_path, "Acme Logistics", today=TODAY)
    assert data["created"] == "2026-08-22"
    assert current_phase(data).key == "audit"
    assert all(p["status"] == "pending" for p in data["phases"].values())
    with pytest.raises(EngagementError, match="already exists"):
        new_engagement(tmp_path, "Acme Logistics", today=TODAY)


def test_advance_refuses_without_deliverable(tmp_path):
    new_engagement(tmp_path, "Acme", today=TODAY)
    with pytest.raises(EngagementError, match="01-audit.md"):
        advance(tmp_path, "acme", today=TODAY)
    # An empty file is not a deliverable either.
    (tmp_path / "acme" / "01-audit.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(EngagementError, match="01-audit.md"):
        advance(tmp_path, "acme", today=TODAY)
    _write_deliverable(tmp_path, "acme", PHASES[0])
    assert advance(tmp_path, "acme", today=TODAY).key == "audit"
    assert current_phase(load(tmp_path, "acme")).key == "map"


def test_mandatory_phase_cannot_be_skipped(tmp_path):
    new_engagement(tmp_path, "Acme", today=TODAY)
    with pytest.raises(EngagementError, match="cannot be skipped"):
        advance(tmp_path, "acme", skip=True, today=TODAY)


def test_present_is_gated_on_the_deck(tmp_path):
    new_engagement(tmp_path, "Acme", today=TODAY)
    phase = _advance_through(tmp_path, "acme", "present")
    assert phase.key == "present"
    _write_deliverable(tmp_path, "acme", phase, "Stakeholders asked about cost.")
    with pytest.raises(EngagementError, match="deck"):
        advance(tmp_path, "acme", today=TODAY)
    build_deck(tmp_path, "acme", today=TODAY)
    assert advance(tmp_path, "acme", today=TODAY).key == "present"


def test_revise_is_skippable_and_approval_needs_a_name(tmp_path):
    new_engagement(tmp_path, "Acme", today=TODAY)
    assert _advance_through(tmp_path, "acme", "revise").key == "revise"
    assert advance(tmp_path, "acme", skip=True, today=TODAY).key == "revise"
    with pytest.raises(EngagementError, match="approved-by"):
        advance(tmp_path, "acme", today=TODAY)
    advance(tmp_path, "acme", approved_by="Jay", today=TODAY)
    data = load(tmp_path, "acme")
    assert data["approval"] == {"approved_by": "Jay", "date": "2026-08-22"}
    assert data["phases"]["revise"]["status"] == "skipped"


def test_full_flow_completes_and_then_refuses(tmp_path):
    new_engagement(tmp_path, "Acme", today=TODAY)
    assert _advance_through(tmp_path, "acme", "nonexistent") is None
    data = load(tmp_path, "acme")
    assert data["completed"] == "2026-08-22"
    with pytest.raises(EngagementError, match="already complete"):
        advance(tmp_path, "acme", today=TODAY)
    assert "Complete 2026-08-22" in status_text(tmp_path, "acme")


def test_notes_and_retro_feed_the_next_engagement(tmp_path):
    new_engagement(tmp_path, "Acme", today=TODAY)
    new_engagement(tmp_path, "Bravo Corp", today=TODAY)
    add_note(tmp_path, "acme", "ask for the vendor invoices up front")
    add_note(tmp_path, "bravo-corp", "the org chart lies; shadow the work")
    add_note(tmp_path, "bravo-corp", "score data quality early", phase_key="readiness")

    grouped = retro(tmp_path)
    assert grouped["audit"] == [
        ("acme", "ask for the vendor invoices up front"),
        ("bravo-corp", "the org chart lies; shadow the work"),
    ]
    assert grouped["readiness"] == [("bravo-corp", "score data quality early")]
    # Filtered retro is what the skill reads before starting one phase.
    assert set(retro(tmp_path, "readiness")) == {"readiness"}
    with pytest.raises(EngagementError, match="unknown phase"):
        retro(tmp_path, "sprint")
    with pytest.raises(EngagementError, match="empty note"):
        add_note(tmp_path, "acme", "   ")


def test_deck_renders_deliverables_and_escapes_them(tmp_path):
    new_engagement(tmp_path, "Acme <Widgets> & Co", today=TODAY)
    slug = "acme-widgets-co"
    _write_deliverable(
        tmp_path,
        slug,
        PHASES[0],
        "# Inventory\n\n- CRM: **HubSpot**\n- <script>alert(1)</script>\n",
    )
    out = build_deck(tmp_path, slug, today=TODAY)
    assert out == tmp_path / slug / DECK_FILENAME
    html = out.read_text(encoding="utf-8")
    assert "Acme &lt;Widgets&gt; &amp; Co" in html
    assert "<li>CRM: <strong>HubSpot</strong></li>" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # Phases without a deliverable on disk stay out of the deck.
    assert "Identify bottlenecks" not in html.split("</header>")[1]


def test_cli_round_trip(tmp_path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "new", "Acme"]) == 0
    assert main(["--root", root, "status", "acme"]) == 0
    assert "Current phase: audit" in capsys.readouterr().out
    # A refused gate is an exit code and a message, not a traceback.
    assert main(["--root", root, "advance", "acme"]) == 1
    assert "01-audit.md" in capsys.readouterr().err
    _write_deliverable(tmp_path, "acme", PHASES[0])
    assert main(["--root", root, "advance", "acme"]) == 0
    assert main(["--root", root, "note", "acme", "start with the invoices"]) == 0
    assert main(["--root", root, "retro"]) == 0
    assert "start with the invoices" in capsys.readouterr().out
    assert main(["--root", root, "list"]) == 0
    assert "phase: map" in capsys.readouterr().out
    assert len(list_engagements(tmp_path)) == 1


def test_export_flow_maps_statuses_and_chains_the_phases(tmp_path):
    data = new_engagement(tmp_path, "Acme Logistics", today=TODAY)
    slug = data["slug"]
    _advance_through(tmp_path, slug, "bottlenecks")

    import json

    from tools.engagement import export_flow

    out = export_flow(tmp_path, slug)
    assert out == tmp_path / slug / "flow.json"
    flow = json.loads(out.read_text(encoding="utf-8"))

    by_id = {n["id"]: n for n in flow["nodes"]}
    assert [n["id"] for n in flow["nodes"]] == [p.key for p in PHASES]
    # done phases read live, the current one working, the future draft
    assert by_id["audit"]["status"] == "live"
    assert by_id["map"]["status"] == "live"
    assert by_id["bottlenecks"]["status"] == "working"
    assert by_id["live"]["status"] == "draft"
    # the hard gate is the decision point, and stakeholders own it
    assert by_id["approval"]["decision"] is True
    assert by_id["approval"]["owner"] == "person"
    # a strict left-to-right chain: each phase feeds exactly the next
    assert [(e["from"], e["to"]) for e in flow["edges"]] == [
        (PHASES[i].key, PHASES[i + 1].key) for i in range(len(PHASES) - 1)
    ]
    # every edge endpoint resolves to a node the canvas can find
    assert all(e["from"] in by_id and e["to"] in by_id for e in flow["edges"])


def test_export_flow_records_a_skipped_phase_without_calling_it_done(tmp_path):
    import json

    from tools.engagement import export_flow

    data = new_engagement(tmp_path, "Acme Logistics", today=TODAY)
    slug = data["slug"]
    _advance_through(tmp_path, slug, "revise")
    advance(tmp_path, slug, skip=True, today=TODAY)

    flow = json.loads(export_flow(tmp_path, slug).read_text(encoding="utf-8"))
    revise = next(n for n in flow["nodes"] if n["id"] == "revise")
    assert revise["status"] == "draft"
    assert revise["notes"] == "skipped"

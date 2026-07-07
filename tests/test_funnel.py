"""Acquisitie-funnel — tests voor de meetlus (input → output).

Dekt de kern van de conversieformule zonder LLM of Graph aan te roepen:
  - advance_lead: status + eenmalige tijdstempels
  - mark_replied_if_lead: reply-detectie op hoofdemail én contacts-JSON
  - select_batch_leads: de juiste leads voor de dagelijkse batch
  - funnel_stats / input_stats: de formule-berekening
  - Actiecentrum + ochtendrapport tonen de outreach-gate en de formule
"""
import json
import uuid
from datetime import datetime, timezone


def _make_lead(**overrides):
    """Insert via een eigen connectie zodat de commit direct zichtbaar is
    voor de productie-code (die ook eigen connecties opent)."""
    from backend.shared.database import get_conn
    lead_id = overrides.pop("id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "org_name": "Testorganisatie", "website": "https://example.nl",
        "contacts": "[]", "summary": "Doet dingen.", "relevance": "hoog",
        "status": "enriched", "email": "info@example.nl", "score": 70,
        "created_at": now, "updated_at": now,
    }
    fields.update(overrides)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO leads (id, {cols}) VALUES (?, {marks})",
            [lead_id, *fields.values()],
        )
    return lead_id


def test_advance_lead_stamps_timestamp_once(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead
    lead_id = _make_lead()

    updated = advance_lead(lead_id, "contacted")
    assert updated["status"] == "contacted"
    first_stamp = updated["contacted_at"]
    assert first_stamp

    # Terug en weer vooruit: de tijdstempel blijft de eerste (conversie-integriteit)
    advance_lead(lead_id, "enriched")
    again = advance_lead(lead_id, "contacted")
    assert again["contacted_at"] == first_stamp


def test_advance_lead_rejects_unknown_stage(clean_tables):
    import pytest
    from backend.domains.prospecting.funnel import advance_lead
    lead_id = _make_lead()
    with pytest.raises(ValueError):
        advance_lead(lead_id, "vip")


def test_mark_replied_matches_main_email(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead, mark_replied_if_lead
    lead_id = _make_lead(email="reply@example.nl")
    advance_lead(lead_id, "contacted")

    updated = mark_replied_if_lead("Reply@Example.nl")
    assert updated is not None and updated["status"] == "replied"
    assert updated["replied_at"]


def test_mark_replied_matches_contact_email(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead, mark_replied_if_lead
    contacts = json.dumps([{"naam": "Anna", "email": "anna@bedrijf.nl"}])
    lead_id = _make_lead(email="", contacts=contacts)
    advance_lead(lead_id, "contacted")

    updated = mark_replied_if_lead("anna@bedrijf.nl")
    assert updated is not None and updated["status"] == "replied"


def test_mark_replied_ignores_uncontacted_and_old_mail(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead, mark_replied_if_lead
    # Nooit benaderd → geen reply
    _make_lead(email="koud@example.nl")
    assert mark_replied_if_lead("koud@example.nl") is None

    # Mail van vóór het contactmoment → geen reply
    lead_id = _make_lead(email="oud@example.nl")
    advance_lead(lead_id, "contacted")
    assert mark_replied_if_lead("oud@example.nl", received_at="2020-01-01T00:00:00Z") is None


def test_select_batch_leads_picks_best_unmailed(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead
    from backend.domains.prospecting.outreach import select_batch_leads

    top = _make_lead(org_name="Top", score=95)
    _make_lead(org_name="Middel", score=60)
    _make_lead(org_name="GeenMail", score=99, email="", contacts="[]")
    contacted = _make_lead(org_name="AlBenaderd", score=98)
    advance_lead(contacted, "contacted")
    _make_lead(org_name="HeeftConcept", score=97, status="outreach_review",
               outreach_draft="Hoi", outreach_subject="x")
    # 'valid' (deliverable geverifieerd) gaat vóór hogere score zonder verificatie
    valid = _make_lead(org_name="Valid", score=50, status="valid")

    batch = select_batch_leads(2)
    assert [b["id"] for b in batch] == [valid, top]


def test_funnel_stats_formula(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead, funnel_stats

    for i in range(4):
        lead_id = _make_lead(org_name=f"Org{i}")
        advance_lead(lead_id, "contacted")
        if i == 0:
            advance_lead(lead_id, "replied")

    stats = funnel_stats()
    assert stats["reached"]["contacted"] == 4
    assert stats["reached"]["replied"] == 1
    assert stats["conversions"]["contacted_to_replied"] == 25.0
    assert "4 verstuurde mails" in stats["formula"]


def test_input_stats_counts_recent_sends(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead, input_stats

    lead_id = _make_lead()
    advance_lead(lead_id, "contacted")
    _make_lead(org_name="Concept", status="outreach_review",
               outreach_draft="Hoi", outreach_subject="x")

    inp = input_stats(days=7)
    assert inp["outreach_sent"] == 1
    assert inp["outreach_drafts_ready"] == 1
    assert inp["outreach_target"] > 0


def test_action_center_shows_outreach_review(clean_tables):
    from backend.domains.action_center.service import build_inbox
    _make_lead(org_name="Reviewbedrijf", status="outreach_review",
               outreach_subject="Kennismaken?", outreach_draft="Hoi, korte vraag...",
               outreach_drafted_at=datetime.now(timezone.utc).isoformat())

    items = build_inbox()["items"]
    outreach = [i for i in items if i["kind"] == "outreach_review"]
    assert len(outreach) == 1
    labels = {a["type"] for a in outreach[0]["actions"]}
    assert {"outreach_send", "outreach_dismiss"} <= labels


def test_digest_contains_formula_section(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead
    from backend.domains.action_center.digest import build_digest

    lead_id = _make_lead()
    advance_lead(lead_id, "contacted")

    md = build_digest()["markdown"]
    assert "De formule" in md
    assert "benaderd" in md

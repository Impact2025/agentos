"""Lead-opvolging — een 'contacted' lead die stilbleef krijgt hooguit twee
zachte herinneringen, nooit automatisch verstuurd, nooit een derde keer."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _make_lead(**overrides):
    from backend.shared.database import get_conn
    lead_id = overrides.pop("id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "org_name": "Stiltebedrijf", "website": "https://example.nl",
        "contacts": "[]", "summary": "Doet dingen.", "relevance": "hoog",
        "status": "contacted", "email": "jan@stiltebedrijf.nl", "score": 70,
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


def _oud(dagen):
    return (datetime.now(timezone.utc) - timedelta(days=dagen)).isoformat()


def test_leads_needing_followup_respects_silence_window(clean_tables):
    from backend.domains.prospecting import followup

    te_vroeg = _make_lead(org_name="TeVroeg", contacted_at=_oud(1))
    op_tijd = _make_lead(org_name="OpTijd", contacted_at=_oud(6))

    ids = {l["id"] for l in followup.leads_needing_followup()}
    assert op_tijd in ids
    assert te_vroeg not in ids


def test_leads_needing_followup_excludes_replied_and_maxed_out(clean_tables):
    from backend.domains.prospecting import followup

    gereageerd = _make_lead(org_name="Gereageerd", contacted_at=_oud(6), replied_at=_oud(3))
    max_bereikt = _make_lead(org_name="MaxBereikt", contacted_at=_oud(6), followup_count=2)

    ids = {l["id"] for l in followup.leads_needing_followup()}
    assert gereageerd not in ids
    assert max_bereikt not in ids


def test_leads_needing_followup_skips_lead_with_pending_draft(clean_tables):
    from backend.domains.prospecting import followup

    lead_id = _make_lead(contacted_at=_oud(6), followup_draft="Hoi nogmaals",
                          followup_subject="Nog interesse?")
    ids = {l["id"] for l in followup.leads_needing_followup()}
    assert lead_id not in ids


def test_sla_followup_over_bumps_count_and_resets_cooldown(clean_tables):
    from backend.domains.prospecting import followup
    from backend.shared.database import get_conn

    lead_id = _make_lead(contacted_at=_oud(6), followup_draft="Hoi nogmaals",
                          followup_subject="Nog interesse?")
    followup.sla_followup_over(lead_id)

    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())
    assert row["followup_count"] == 1
    assert row["followup_draft"] == ""
    assert row["followup_sent_at"] != ""

    # Meteen erna is de stilteperiode weer vers gestart, dus geen nieuwe kandidaat.
    ids = {l["id"] for l in followup.leads_needing_followup()}
    assert lead_id not in ids


def test_na_verzending_bumps_count_and_logs_outcome(clean_tables):
    from backend.domains.prospecting import followup
    from backend.shared.database import get_conn

    lead_id = _make_lead(contacted_at=_oud(6), followup_draft="Hoi nogmaals",
                          followup_subject="Nog interesse?")
    followup.na_verzending(lead_id)

    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())
        log = conn.execute(
            "SELECT * FROM activity_log WHERE action = 'lead_followup_verstuurd'"
        ).fetchone()
    assert row["followup_count"] == 1
    assert log is not None


@pytest.mark.asyncio
async def test_genereer_followups_skips_when_llm_unavailable(clean_tables, monkeypatch):
    """Zonder werkende LLM (zoals in de testomgeving) mag genereer_followups
    nooit crashen — draft_followup geeft None terug en er gebeurt niets."""
    from backend.domains.prospecting import followup

    async def _geen_llm(system, prompt, max_tokens=400, purpose=""):
        return ""

    import backend.domains.publish.content_pipeline as content_pipeline
    monkeypatch.setattr(content_pipeline, "_llm", _geen_llm)

    _make_lead(contacted_at=_oud(6))
    gemaakt = await followup.genereer_followups()
    assert gemaakt == []


def test_action_center_shows_followup_review(clean_tables):
    from backend.domains.action_center.service import build_inbox

    _make_lead(followup_draft="Hoi nogmaals, nog interesse?", followup_subject="Nog interesse?",
               followup_drafted_at=datetime.now(timezone.utc).isoformat())

    items = build_inbox()["items"]
    followup_items = [i for i in items if i["kind"] == "followup_review"]
    assert len(followup_items) == 1
    action_types = {a["type"] for a in followup_items[0]["actions"]}
    assert {"followup_send", "followup_skip"} <= action_types

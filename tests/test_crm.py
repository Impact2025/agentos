"""CRM — tests voor bedrijven/contacten/deals/taken en de brug met de
acquisitie-funnel (een gewonnen lead moet hier automatisch een deal worden,
idempotent, zonder de bestaande leads-tabel te vervangen)."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _make_lead(**overrides):
    from backend.shared.database import get_conn
    lead_id = overrides.pop("id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "org_name": "Testorganisatie CRM", "website": "https://example.nl",
        "contacts": json.dumps([{"name": "Anna Janssen", "email": "anna@testorganisatie.nl"}]),
        "summary": "Doet dingen.", "relevance": "hoog",
        "status": "enriched", "email": "info@testorganisatie.nl", "score": 70,
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


def test_create_company_dedupes_on_squashed_name(clean_tables):
    from backend.domains.crm import service as crm

    a = crm.create_company("Bedrijf B.V.")
    b = crm.create_company("bedrijf bv")
    assert a["id"] == b["id"]
    assert len(crm.list_companies()) == 1


def test_deal_uit_lead_creates_company_contact_and_deal(clean_tables):
    from backend.domains.crm import service as crm

    lead_id = _make_lead()
    deal = crm.deal_uit_lead(lead_id)
    assert deal is not None
    assert deal["stage"] == "gewonnen"
    assert deal["lead_id"] == lead_id

    company = crm.get_company(deal["company_id"])
    assert company["name"] == "Testorganisatie CRM"

    contacts = crm.list_contacts(company["id"])
    assert len(contacts) == 1
    assert contacts[0]["first_name"] == "Anna"
    assert contacts[0]["is_primary"] == 1


def test_deal_uit_lead_is_idempotent(clean_tables):
    from backend.domains.crm import service as crm

    lead_id = _make_lead()
    first = crm.deal_uit_lead(lead_id)
    second = crm.deal_uit_lead(lead_id)
    assert first["id"] == second["id"]
    assert len(crm.list_deals()) == 1


def test_advance_lead_won_triggers_crm_deal(clean_tables):
    from backend.domains.prospecting.funnel import advance_lead
    from backend.domains.crm import service as crm

    lead_id = _make_lead(status="replied")
    advance_lead(lead_id, "won")

    deals = crm.list_deals()
    assert len(deals) == 1
    assert deals[0]["lead_id"] == lead_id


def test_update_deal_stage_sets_closed_at(clean_tables):
    from backend.domains.crm import service as crm

    company = crm.create_company("StageBedrijf")
    deal = crm.create_deal(company["id"], "Testdeal", value_cents=100000)
    assert deal["closed_at"] == ""

    won = crm.update_deal_stage(deal["id"], "gewonnen")
    assert won["closed_at"] != ""
    assert won["stage"] == "gewonnen"


def test_update_deal_stage_rejects_unknown_stage(clean_tables):
    from backend.domains.crm import service as crm
    company = crm.create_company("FoutBedrijf")
    deal = crm.create_deal(company["id"], "Testdeal")
    with pytest.raises(ValueError):
        crm.update_deal_stage(deal["id"], "geen-bestaande-stage")


def test_pipeline_summary_groups_by_stage(clean_tables):
    from backend.domains.crm import service as crm

    company = crm.create_company("PipelineBedrijf")
    crm.create_deal(company["id"], "Deal A", value_cents=100000, stage="gesprek", probability=20)
    crm.create_deal(company["id"], "Deal B", value_cents=200000, stage="gewonnen", probability=100)

    summary = crm.pipeline_summary()
    assert summary["by_stage"]["gesprek"]["count"] == 1
    assert summary["by_stage"]["gewonnen"]["count"] == 1
    assert summary["open_count"] == 1
    assert summary["won_count"] == 1
    assert summary["weighted_value_cents"] == 20000  # alleen open deals wegen mee


def test_overdue_tasks_only_returns_open_past_due(clean_tables):
    from backend.domains.crm import service as crm

    gisteren = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    morgen = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    te_laat = crm.create_task("Bel terug", due_date=gisteren)
    crm.create_task("Nog op tijd", due_date=morgen)
    afgevinkt = crm.create_task("Al gedaan", due_date=gisteren)
    crm.complete_task(afgevinkt["id"])

    overdue = crm.overdue_tasks()
    assert [t["id"] for t in overdue] == [te_laat["id"]]


def test_action_center_shows_overdue_crm_tasks(clean_tables):
    from backend.domains.crm import service as crm
    from backend.domains.action_center.service import build_inbox

    gisteren = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    crm.create_task("Bel terug", due_date=gisteren)

    items = build_inbox()["items"]
    overdue_items = [i for i in items if i["kind"] == "crm_tasks_overdue"]
    assert len(overdue_items) == 1

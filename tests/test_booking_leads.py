"""Boekingsaanvragen via weareimpact.nl (26 aug 2026): een boeking heeft een
levenscyclus (pending -> approved/rejected), anders dan de eenmalige
workshop-/impact-calculator-leads. Deze tests dekken de kern: dedupe op
e-mail, funnel-status alleen omhoog bij een écht bevestigd gesprek, en een
afwijzing die de lead zelf met rust laat.
"""
import pytest


@pytest.fixture()
def _schone_leads(clean_tables):
    yield


def _booking(**overrides):
    base = {
        "customer_name": "Marleen de Vries",
        "customer_email": "marleen@voorbeeldstichting.nl",
        "customer_phone": "+31612345678",
        "customer_organization": "Voorbeeldstichting",
        "booking_type": "AI Strategiesessie",
        "start_time": "2026-09-07T13:00:00+02:00",
        "notes": "Vooral benieuwd naar AI in de intake.",
        "booking_status": "pending",
    }
    base.update(overrides)
    return base


# ── capture_booking_lead ─────────────────────────────────────────────────────

def test_nieuwe_boeking_wordt_lead_op_valid(_schone_leads):
    from backend.domains.prospecting.service import LeadsService
    from backend.shared.database import get_conn

    result = LeadsService().capture_booking_lead(_booking(), "Verslag van Iris.", {})
    assert result["is_new"] is True
    assert result["status"] == "valid"

    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (result["id"],)).fetchone())
    assert row["org_name"] == "Voorbeeldstichting"
    assert row["email"] == "marleen@voorbeeldstichting.nl"
    assert row["lead_type"] == "booking"
    assert "booking" in row["tags"]


def test_zonder_email_wordt_niets_vastgelegd(_schone_leads):
    from backend.domains.prospecting.service import LeadsService

    result = LeadsService().capture_booking_lead(_booking(customer_email=""), "x", {})
    assert result["id"] is None


def test_dedupe_op_email_zet_verder_gevorderde_lead_niet_terug(_schone_leads):
    from backend.domains.prospecting.service import LeadsService
    from backend.domains.prospecting import funnel
    from backend.shared.database import get_conn

    first = LeadsService().capture_booking_lead(_booking(), "Eerste verslag.", {})
    funnel.advance_lead(first["id"], "call")

    again = LeadsService().capture_booking_lead(_booking(), "Tweede verslag (herhaalde push).", {})
    assert again["id"] == first["id"]
    assert again["is_new"] is False

    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (first["id"],)).fetchone())
    assert row["status"] == "call"  # niet teruggezet naar 'valid'
    assert row["summary"] == "Tweede verslag (herhaalde push)."  # wel bijgewerkt


# ── booking_leads._process_one ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_doet_volledige_verrijking_en_verslag(_schone_leads, monkeypatch):
    from backend.domains.bridge import booking_leads as bl
    from backend.shared.database import get_conn

    monkeypatch.setattr(bl, "_enrich_sync", lambda email, org: {"website": "", "scraped": {}, "search_results": []})

    async def fake_verslag(booking, enrichment):
        return "Iris' verslag: relevante inbound lead."
    monkeypatch.setattr(bl, "_write_verslag", fake_verslag)

    ok, err = await bl._process_one(_booking())
    assert ok is True
    assert err == ""

    with get_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM leads WHERE lower(email)=?", ("marleen@voorbeeldstichting.nl",)
        ).fetchone())
    assert row["status"] == "valid"
    assert "Iris' verslag" in row["summary"]


@pytest.mark.asyncio
async def test_pending_llm_fout_valt_terug_op_ruwe_feiten(_schone_leads, monkeypatch):
    from backend.domains.bridge import booking_leads as bl
    from backend.shared.database import get_conn

    monkeypatch.setattr(bl, "_enrich_sync", lambda email, org: {"website": "", "scraped": {}, "search_results": []})

    async def falende_verslag(booking, enrichment):
        raise RuntimeError("gateway plat")
    monkeypatch.setattr(bl, "_write_verslag", falende_verslag)

    ok, err = await bl._process_one(_booking())
    # De lead wordt nog steeds vastgelegd (deterministisch pad) — alleen het
    # LLM-verslag ontbreekt. process_one meldt dit als 'gelukt' (True) met de
    # fout in err, zodat de bridge het niet blijft herhalen.
    assert ok is True
    assert "gateway plat" in err

    with get_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM leads WHERE lower(email)=?", ("marleen@voorbeeldstichting.nl",)
        ).fetchone())
    assert row["status"] == "valid"
    assert "LLM-fout" in row["summary"]


@pytest.mark.asyncio
async def test_approved_zet_funnel_op_call_zonder_nieuwe_llm_call(_schone_leads, monkeypatch):
    from backend.domains.bridge import booking_leads as bl
    from backend.domains.prospecting.service import LeadsService
    from backend.shared.database import get_conn

    # Eerst de oorspronkelijke aanvraag vastleggen (zoals de pending-ronde dat
    # zou doen), zodat er een lead bestaat om bij te werken.
    LeadsService().capture_booking_lead(_booking(), "Eerste verslag.", {})

    def fail_if_called(*a, **kw):
        raise AssertionError("_enrich_sync had niet aangeroepen mogen worden bij approved")
    monkeypatch.setattr(bl, "_enrich_sync", fail_if_called)

    ok, err = await bl._process_one(_booking(booking_status="approved"))
    assert ok is True
    assert err == ""

    with get_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM leads WHERE lower(email)=?", ("marleen@voorbeeldstichting.nl",)
        ).fetchone())
    assert row["status"] == "call"
    assert row["call_at"] != ""


@pytest.mark.asyncio
async def test_rejected_laat_funnel_status_met_rust(_schone_leads, monkeypatch):
    from backend.domains.bridge import booking_leads as bl
    from backend.domains.prospecting.service import LeadsService
    from backend.shared.database import get_conn

    LeadsService().capture_booking_lead(_booking(), "Eerste verslag.", {})

    def fail_if_called(*a, **kw):
        raise AssertionError("_enrich_sync had niet aangeroepen mogen worden bij rejected")
    monkeypatch.setattr(bl, "_enrich_sync", fail_if_called)

    ok, err = await bl._process_one(_booking(booking_status="rejected"))
    assert ok is True

    with get_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM leads WHERE lower(email)=?", ("marleen@voorbeeldstichting.nl",)
        ).fetchone())
    # Status blijft 'valid' — een afgewezen tijdstip is geen oordeel over de lead.
    assert row["status"] == "valid"

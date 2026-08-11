"""De outreach-selectie: zeven mailbare leads mogen niet 'funnel-invoer is op' heten.

Incident 2 aug 2026. De dagelijkse batch meldde weken achtereen "geen bruikbare
leads — funnel-invoer is op" terwijl er zeven direct mailbare leads in voorraad
stonden, en Iris' briefing zette daardoor elke ochtend de verkeerde knop bovenaan
(nóg meer leads zoeken, in plaats van versturen).

De oorzaak was één regel volgorde: `select_batch_leads` kapte in SQL af op
`count` en liet Python daarná pas de adres-zeef draaien. De eerste acht rijen in
de sortering waren generieke `info@`-adressen die de zeef weigert, de eerste
bruikbare stond op plek negen, en met count=5 kwam er nooit één concept uit.
Omdat álle leads dezelfde score hadden (50) was de tie-break `created_at` en dus
de volgorde iedere dag identiek: dezelfde acht blokkeerden het venster permanent.
"""
import uuid
from datetime import datetime, timezone

import pytest


def _lead(conn, email, *, score=50, status="enriched", created="2026-07-01T00:00:00+00:00"):
    lead_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO leads (id, org_name, website, email, status, score, "
        "contacted_at, lost_at, outreach_draft, contacts, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, '', '', '', '[]', ?, ?)",
        (lead_id, f"Org {email}", "https://voorbeeld.nl", email, status, score,
         created, created),
    )
    return lead_id


@pytest.fixture()
def lege_voorraad(conn):
    conn.execute("DELETE FROM leads")
    conn.commit()
    yield conn
    conn.execute("DELETE FROM leads")
    conn.commit()


def test_onbruikbare_adressen_verdringen_de_bruikbare_niet(lege_voorraad):
    """De reconstructie van het incident: acht info@-adressen vóór de eerste
    bruikbare, en een batch die er vijf vraagt."""
    from backend.domains.prospecting.outreach import select_batch_leads

    conn = lege_voorraad
    for i in range(8):
        _lead(conn, f"info@organisatie{i}.nl", created=f"2026-07-0{i + 1}T00:00:00+00:00")
    for i in range(3):
        _lead(conn, f"marieke.jansen{i}@organisatie.nl",
              created=f"2026-07-1{i}T00:00:00+00:00")
    conn.commit()

    gekozen = select_batch_leads(5)

    # Drie mailbaar in voorraad: die moeten er alle drie uit komen, ook al staan
    # ze achter acht adressen die de zeef weigert.
    assert len(gekozen) == 3
    assert all("info@" not in l["email"] for l in gekozen)


def test_count_blijft_een_bovengrens(lege_voorraad):
    """De zeef vóór het afkappen mag de batchgrootte niet oprekken."""
    from backend.domains.prospecting.outreach import select_batch_leads

    conn = lege_voorraad
    for i in range(10):
        _lead(conn, f"marieke.jansen{i}@organisatie.nl")
    conn.commit()

    assert len(select_batch_leads(4)) == 4


def test_mailbare_telling_gebruikt_dezelfde_zeef(lege_voorraad):
    """Voorraad en 'waar de batch mee vooruit kan' moeten één getal zijn.

    Zolang Iris' bottleneck op `new + enriched` telde, las ze 47 waar er 7
    mailbaar waren — en dan wijst ze de verkeerde knop aan.
    """
    from backend.domains.prospecting.outreach import count_mailable_leads

    conn = lege_voorraad
    for i in range(6):
        _lead(conn, f"info@organisatie{i}.nl")
    for i in range(2):
        _lead(conn, f"pieter.devries{i}@organisatie.nl")
    conn.commit()

    assert count_mailable_leads() == 2


def test_lead_zonder_adres_telt_niet_mee(lege_voorraad):
    from backend.domains.prospecting.outreach import count_mailable_leads

    conn = lege_voorraad
    _lead(conn, "")
    conn.commit()

    assert count_mailable_leads() == 0


def _batch_draaide(conn, dagen_geleden=1):
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO activity_log (id, project, action, detail, status, created_at) "
        "VALUES (?, 'Leads', 'outreach_batch', 'Outreach-batch gestart', 'ok', "
        "datetime('now', ?))",
        (uuid.uuid4().hex, f"-{dagen_geleden} day"))
    conn.commit()
    return ts


@pytest.fixture()
def schone_log(conn):
    conn.execute("DELETE FROM activity_log WHERE project = 'Leads'")
    conn.commit()
    yield conn
    conn.execute("DELETE FROM activity_log WHERE project = 'Leads'")
    conn.commit()


def test_invariant_ziet_onbenutte_voorraad(lege_voorraad, schone_log):
    """De waarheidsaudit hoort dit gat te vinden ongeacht de oorzaak: mailbare
    voorraad ja, concepten in review nee, batch draaide wél."""
    from backend.domains.iris import integrity as ig

    conn = lege_voorraad
    for i in range(3):
        _lead(conn, f"sanne.bakker{i}@organisatie.nl")
    _batch_draaide(conn)

    b = ig._check_outreach_voorraad_onbenut()
    assert len(b) == 1
    assert "3 mailbare lead(s)" in b[0].detail


def test_invariant_zwijgt_als_er_concepten_in_review_staan(lege_voorraad, schone_log):
    from backend.domains.iris import integrity as ig

    conn = lege_voorraad
    for i in range(3):
        _lead(conn, f"sanne.bakker{i}@organisatie.nl")
    _lead(conn, "in.review@organisatie.nl", status="outreach_review")
    _batch_draaide(conn)

    assert ig._check_outreach_voorraad_onbenut() == []


def test_invariant_zwijgt_als_de_batch_niet_draaide(lege_voorraad, schone_log):
    """Dan is de funnel niet kapot maar stil — dat is het domein van de
    stilstand-detectie, niet van deze invariant."""
    from backend.domains.iris import integrity as ig

    conn = lege_voorraad
    for i in range(3):
        _lead(conn, f"sanne.bakker{i}@organisatie.nl")
    conn.commit()

    assert ig._check_outreach_voorraad_onbenut() == []


def test_bottleneck_kiest_versturen_boven_zoeken_bij_mailbare_voorraad():
    """De knop onder de diagnose hoort bij de diagnose te passen.

    Met mailbare leads in voorraad is 'concepten klaarzetten' de actie; is de
    voorraad wél gevuld maar niets ervan mailbaar, dan is zoeken het antwoord.
    """
    from backend.domains.iris import metrics

    def snap(mailable):
        return {
            "projects": [],
            "global": {
                "pending_review_total": 0,
                "scheduler_failures": [],
                "funnel": {"by_status": {"new": 40, "enriched": 7}, "mailable": mailable},
                "inputs_7d": {"outreach_target": 50, "outreach_sent": 0,
                              "outreach_drafts_ready": 0},
            },
        }

    met_voorraad = [b for b in metrics.bottlenecks(snap(7))
                    if b["issue"] == "funnel_droog"][0]
    assert met_voorraad["suggestion"]["type"] == "outreach_run"
    assert met_voorraad["suggestion"]["payload"]["aantal"] == 7
    # De ruwe voorraad staat erbij, zodat "47 leads maar 7 mailbaar" zichtbaar is.
    assert "47" in met_voorraad["waarom"] and "7 mailbare" in met_voorraad["waarom"]

    zonder = [b for b in metrics.bottlenecks(snap(0)) if b["issue"] == "funnel_droog"][0]
    assert zonder["suggestion"]["type"] == "lead_search_run"

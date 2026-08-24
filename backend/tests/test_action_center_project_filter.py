"""Project-gescopeerde inbox: site→project-normalisatie.

Valideert dat build_inbox(project=P) alleen items teruggeeft die écht bij P
horen — inclusief content-wachtrijen die onder een site-naam hangen (bv.
"DatingAssistent 40+" → project "DatingAssistent"), en dat cross-cutting items
(Agenda, Leads, Scheduler, Systeem, …) eruit vallen.
"""
import os
import re
import sys

import pytest

# Zorg dat `backend` als top-level package importeerbaar is (de server draait
# ook als backend.domains... zodat de relatieve `...shared`-imports naar
# backend.shared resolven).
_IMPACTROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _IMPACTROOT not in sys.path:
    sys.path.insert(0, _IMPACTROOT)

# LET OP: dit bestand wees hier vroeger standaard naar data/impactos.db — de
# ÉCHTE productiedatabase — als `IMPACTOS_DB_PATH` nog niet gezet was. Draai je
# alleen dit bestand (of alleen backend/tests/), dan geldt tests/conftest.py
# (dat de wegwerp-DB zet) niet: die conftest ligt in een zusterdirectory, geen
# voorouder van backend/tests/, dus pytest laadt hem dan niet. Het gevolg was
# dat elke standalone run van dit bestand tegen de live productie-database
# draaide (19 aug 2026, ontdekt bij codereview — niet gemeten als incident,
# maar wel een reëel risico: lock-contentie met de draaiende server, en een
# toekomstige schrijvende test tegen prod). Nu: nooit stil terugvallen op
# productie — als er geen expliciete testdatabase is opgegeven, slaat de hele
# module zichzelf over in plaats van te gokken.
if not os.environ.get("IMPACTOS_DB_PATH"):
    pytest.skip(
        "IMPACTOS_DB_PATH niet gezet — dit bestand draait alleen zinvol via "
        "de wegwerp-DB uit tests/conftest.py (bv. `pytest tests/ backend/tests/`), "
        "nooit standalone tegen de productiedatabase.",
        allow_module_level=True,
    )
os.environ.setdefault("OBSIDIAN_VAULT_PATH", "")

from backend.domains.action_center import service as ac


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@pytest.fixture(autouse=True)
def _site_tagged_content():
    """Zaai precies de data die de site→project-normalisatie test:

    een site met een naam die NIET letterlijk gelijk is aan het projectwoord
    ("DatingAssistent 40+" vs. project "DatingAssistent") plus een
    goedkeurbare content_jobs-rij eronder. Zonder dit is de test afhankelijk
    van wat er toevallig in de productiedatabase staat — precies het risico
    dat de vroegere fallback naar data/impactos.db camoufleerde (zie de
    waarschuwing hierboven).
    """
    from backend.shared.database import get_conn
    from backend.shared.config import CONTENT_MIN_SCORE

    site_id = "test-datingassistent"
    job_id = "test-job-datingassistent-1"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sites (id, name, base_url, created_at) "
            "VALUES (?, ?, '', datetime('now'))",
            (site_id, "DatingAssistent 40+"),
        )
        conn.execute(
            "INSERT INTO content_jobs (id, site_id, title, status, seo_score, created_at) "
            "VALUES (?, ?, 'Testartikel voor de projectfilter', 'pending_review', ?, datetime('now'))",
            (job_id, site_id, CONTENT_MIN_SCORE),
        )
    # ac._PROJECT_INDEX is een module-global cache die maar één keer per
    # proces gebouwd wordt (zie _build_project_index) — zonder reset ziet
    # een eerder in dit proces gebouwde index de zojuist geïnserte site nooit.
    ac._PROJECT_INDEX = None
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM content_jobs WHERE id = ?", (job_id,))
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    ac._PROJECT_INDEX = None


def test_no_filter_returns_full_inbox():
    data = ac.build_inbox()
    assert "items" in data and "counts" in data
    assert data["counts"]["total"] == len(data["items"])


def test_project_filter_keeps_only_that_project():
    for project in ("DatingAssistent", "Bewaard voor Jou", "daarwebsite", "Daar"):
        data = ac.build_inbox(project=project)
        assert data.get("project") == project
        assert data["counts"]["total"] == len(data["items"])
        target = _norm(project)
        for it in data["items"]:
            assert ac._resolve_item_project(it.get("project")) == target


def test_site_content_maps_to_project():
    data = ac.build_inbox(project="DatingAssistent")
    assert data["counts"]["total"] > 0
    site_tagged = [
        it for it in data["items"]
        if _norm(it.get("project") or "") != _norm("DatingAssistent")
    ]
    assert site_tagged, "Geen site-naam items toegewezen aan het DatingAssistent-project"


def test_cross_cutting_excluded_from_client_project():
    """Cross-cutting items (Agenda, Leads, Scheduler, ...) horen bij geen
    enkel klantproject — die zijn van Vincent zelf, niet van bv. DatingAssistent."""
    data = ac.build_inbox(project="DatingAssistent")
    banned = {"agenda", "leads", "scheduler", "opdrachten", "linkbuilding", "postvak"}
    for it in data["items"]:
        assert _norm(it.get("project") or "") not in banned


def test_cross_cutting_visible_on_weareimpact():
    """WeAreImpact is Vincents eigen bedrijf, geen klantproject — een
    cross-cutting item zonder resolveerbaar project (Agenda-voorstel, Leads,
    Scheduler-fout) hoort daarom wél zichtbaar te zijn op zíjn eigen
    dashboard, niet alleen in de globale Control Room-inbox.

    23 aug 2026: vóór deze fix stond een WhatsApp-afspraakvoorstel keurig in
    de globale inbox maar toonde 'Wacht op jou (0)' op het
    WeAreImpact-dashboard zelf — precies waar Vincent 'm verwachtte af te
    handelen. Zie action_center/service.py:_item_belongs_to_project."""
    from backend.shared.database import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO calendar_proposals (mailbox_id, inbox_id, from_addr, subject, "
            "title, proposed_start, proposed_end, status, created_at) "
            "VALUES ('test', 0, 'klant@voorbeeld.nl', 'Testafspraak', 'Testafspraak', "
            "'2026-08-24T14:00:00+02:00', '2026-08-24T14:30:00+02:00', "
            "'pending_review', datetime('now'))"
        )
        proposal_id = cur.lastrowid
    try:
        data = ac.build_inbox(project="WeAreImpact")
        assert data.get("project") == "WeAreImpact"
        assert data["counts"]["total"] == len(data["items"])
        for it in data["items"]:
            resolved = ac._resolve_item_project(it.get("project"))
            assert resolved in (None, "weareimpact")
        calendar_items = [it for it in data["items"] if it.get("dismiss_kind") == "calendar"]
        assert calendar_items, "Agenda-voorstel niet zichtbaar op het WeAreImpact-dashboard"
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM calendar_proposals WHERE id = ?", (proposal_id,))

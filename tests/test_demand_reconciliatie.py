"""Kansen mogen niet voorgoed verbruikt raken.

Achtergrond (27 juli 2026): er stonden 62 kansen op 'in_progress' tegen 11 op
'published'. `select_topic` zet een kans op 'in_progress' zodra hij hem uitdeelt,
maar niets zette hem ooit terug. Liep het artikel daarna vast op de
kwaliteitsgate, werd het afgewezen, of struikelde de publicatie — dan was dat
zoekwoord voorgoed verbruikt zonder dat er iets live stond.

`list_opportunities_truth` corrigeerde de status al bij het lézen, maar
`select_topic` leest de tabel zelf en zag die correctie nooit. Gevolg: de
contentmotor droogde op met een volle tabel. De reconciliatie draaide bij
invoering 36 zoekwoorden vrij en vond 25 kansen die allang live stonden.
"""
import json
import uuid

import pytest

from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import engine
from backend.domains.seo import sites as sites_service
from backend.shared.database import get_conn


@pytest.fixture
def site():
    s = sites_service.create_site(
        {"name": "ReconcileTest", "base_url": "https://voorbeeld.nl"})
    yield sites_service.get_site(s["id"])
    with get_conn() as conn:
        conn.execute("DELETE FROM opportunities WHERE site_id = ?", (s["id"],))
        conn.execute("DELETE FROM content_jobs WHERE site_id = ?", (s["id"],))
    sites_service.delete_site(s["id"])


def _kans(site_id: str, query: str, status: str = "in_progress") -> str:
    oid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO opportunities (id, site_id, query, clicks, impressions, "
            "ctr, position, opportunity_score, status, scanned_at) "
            "VALUES (?, ?, ?, 0, 100, 0.0, 15.0, 50.0, ?, '2026-07-20')",
            (oid, site_id, query, status),
        )
    return oid


def _status(opp_id: str) -> str:
    with get_conn() as conn:
        return conn.execute(
            "SELECT status FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()["status"]


class TestReconciliatie:
    def test_afgewezen_artikel_geeft_het_zoekwoord_vrij(self, site):
        """Het kerngeval: 36 zoekwoorden zaten hierdoor vast."""
        oid = _kans(site["id"], "digitale erfenis")
        cp.create_job(site["id"], "Titel", "digitale erfenis", "waarom",
                      "<p>x</p>", 70, {}, None, "titel", status="rejected")

        engine.reconcile_opportunities(site_id=site["id"])
        assert _status(oid) == "new"

    def test_kans_zonder_enig_artikel_komt_ook_vrij(self, site):
        """Een run die halverwege afbrak laat een kans achter zonder job."""
        oid = _kans(site["id"], "levensverhaal vastleggen")
        engine.reconcile_opportunities(site_id=site["id"])
        assert _status(oid) == "new"

    def test_lopend_werk_blijft_in_progress(self, site):
        """Een artikel in de wachtrij is geen vrij zoekwoord."""
        oid = _kans(site["id"], "digitale erfenis")
        cp.create_job(site["id"], "Titel", "digitale erfenis", "waarom",
                      "<p>x</p>", 85, {}, None, "titel", status="pending_review")

        engine.reconcile_opportunities(site_id=site["id"])
        assert _status(oid) == "in_progress"

    def test_artikel_dat_verbeterd_wordt_blijft_in_progress(self, site):
        oid = _kans(site["id"], "digitale erfenis")
        cp.create_job(site["id"], "Titel", "digitale erfenis", "waarom",
                      "<p>x</p>", 70, {}, None, "titel", status="needs_work")

        engine.reconcile_opportunities(site_id=site["id"])
        assert _status(oid) == "in_progress"

    def test_live_artikel_zet_de_kans_op_published(self, site):
        """25 kansen stonden allang live zonder dat de vlag ooit gezet was."""
        oid = _kans(site["id"], "digitale erfenis")
        job_id = cp.create_job(site["id"], "Titel", "digitale erfenis", "waarom",
                               "<p>x</p>", 85, {}, None, "titel", status="published")
        with get_conn() as conn:
            conn.execute(
                "UPDATE content_jobs SET publish_result = ? WHERE id = ?",
                (json.dumps({"site": {"url": "https://voorbeeld.nl/blog/titel"}}),
                 job_id),
            )

        telling = engine.reconcile_opportunities(site_id=site["id"])
        assert _status(oid) == "published"
        assert telling["published"] == 1

        with get_conn() as conn:
            url = conn.execute(
                "SELECT live_url FROM opportunities WHERE id = ?", (oid,)
            ).fetchone()["live_url"]
        assert url == "https://voorbeeld.nl/blog/titel"

    def test_reconciliatie_raakt_new_en_dismissed_niet_aan(self, site):
        """Alleen 'in_progress' is een gok; de rest is een besluit."""
        nieuw = _kans(site["id"], "kans een", status="new")
        weg = _kans(site["id"], "kans twee", status="dismissed")

        engine.reconcile_opportunities(site_id=site["id"])
        assert _status(nieuw) == "new"
        assert _status(weg) == "dismissed"

    def test_telling_klopt(self, site):
        _kans(site["id"], "vrij een")
        _kans(site["id"], "vrij twee")
        telling = engine.reconcile_opportunities(site_id=site["id"])
        assert telling["vrijgegeven"] == 2
        assert telling["published"] == 0

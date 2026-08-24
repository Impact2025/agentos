"""Twee waarheidsbugs in de Leads-tab (20 aug 2026), gevonden bij een
handmatige review van het WeAreImpact-dashboard.

1. `get_stats()` retourneerde nooit `valid`/`contacted` — de KPI-tegels
   "Geverifieerd"/"Gecontacteerd" stonden daardoor altijd op 0 (frontend-
   default `|| 0`), ongeacht hoeveel leads er echt geverifieerd/benaderd
   waren. Gemeten op dat moment: 1 geverifieerd, 20 ooit gecontacteerd.
2. `run_quality_gate()` schreef `quality_label` alleen weg voor leads die
   naar 'lost' (D) of 'valid' (A) verhuisden — een B- of C-fit die bleef
   staan waar hij stond, kreeg zijn label nooit gepersisteerd. Elke scan
   toonde daardoor permanent 0×B, 0×C in de Kansen-verdeling, ongeacht de
   werkelijke scoreverdeling (gemeten: 75 leads met score 70-89 in de
   tabel, quality_label overal leeg).
"""
import uuid

from backend.domains.prospecting import quality_gate
from backend.domains.prospecting.service import LeadsService
from backend.shared.database import get_conn


def _maak_lead(**overrides):
    lead_id = uuid.uuid4().hex[:12]
    data = {
        "id": lead_id,
        "org_name": f"Testorganisatie {lead_id}",
        "website": f"https://{lead_id}.example.nl",
        "status": "new",
        "lead_type": "overig",
        "score": 50,
        "enriched_at": "",
        "hunter_verified": 0,
        "contacted_at": "",
    }
    data.update(overrides)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO leads (id, org_name, website, status, lead_type, score, "
            "enriched_at, hunter_verified, contacted_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (data["id"], data["org_name"], data["website"], data["status"],
             data["lead_type"], data["score"], data["enriched_at"],
             data["hunter_verified"], data["contacted_at"]),
        )
    return lead_id


class TestGetStatsToontEchteAantallen:
    def test_valid_en_contacted_staan_niet_meer_vast_op_nul(self):
        _maak_lead(hunter_verified=1, status="valid", score=80)
        _maak_lead(contacted_at="2026-08-20T10:00:00", status="contacted", score=75)
        stats = LeadsService().get_stats()
        assert stats["valid"] >= 1
        assert stats["contacted"] >= 1

    def test_contacted_blijft_meetellen_na_replied_of_won(self):
        # Een tijdstempel is eenmalig — een lead die doorschuift naar 'replied'
        # is nog steeds ooit gecontacteerd; de teller mag niet terugzakken.
        _maak_lead(contacted_at="2026-08-01T09:00:00", status="replied", score=70)
        stats = LeadsService().get_stats()
        assert stats["contacted"] >= 1


class TestQualityGatePersisteertElkLabel:
    def test_bc_fit_krijgt_label_ook_zonder_statuswijziging(self):
        # Score 65 = C-fit (40-69): geen promotie (dat is alleen voor A) en
        # geen discard (dat is alleen < 40) — dus blijft 'new' staan, maar
        # het label moet wél geschreven worden.
        lead_id = _maak_lead(status="new", score=65)
        quality_gate.run_quality_gate(status_filter=("new",), dry_run=False)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT quality_label, quality_score FROM leads WHERE id=?", (lead_id,)
            ).fetchone()
        assert row["quality_label"] == "C"
        assert row["quality_score"] == 65

    def test_b_fit_krijgt_label(self):
        lead_id = _maak_lead(status="enriched", score=80)
        quality_gate.run_quality_gate(status_filter=("enriched",), dry_run=False)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT quality_label FROM leads WHERE id=?", (lead_id,)
            ).fetchone()
        assert row["quality_label"] == "B"

    def test_dry_run_schrijft_niets(self):
        lead_id = _maak_lead(status="new", score=65)
        quality_gate.run_quality_gate(status_filter=("new",), dry_run=True)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT quality_label FROM leads WHERE id=?", (lead_id,)
            ).fetchone()
        assert row["quality_label"] == ""

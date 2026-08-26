"""Tests voor Besluiten: openstaande keuzes die om een besluit vragen."""
import pytest

from backend.domains.decisions import service as decisions_service
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def clean_decisions():
    decisions_service.ensure_schema()
    with get_conn() as c:
        c.execute("DELETE FROM decisions")
    yield
    with get_conn() as c:
        c.execute("DELETE FROM decisions")


def test_add_and_list_open_decision():
    d = decisions_service.add_decision("WeAreImpact", "Wel of niet doorgaan met X", "Context hier",
                                        ["Doorgaan", "Stoppen"], "2026-09-01")
    assert d["status"] == "open"
    assert d["options"] == ["Doorgaan", "Stoppen"]

    open_list = decisions_service.list_decisions("WeAreImpact", status="open")
    assert len(open_list) == 1
    assert open_list[0]["id"] == d["id"]


def test_resolve_decision_moves_it_out_of_open():
    d = decisions_service.add_decision("WeAreImpact", "Nieuwe hire aannemen?")
    resolved = decisions_service.resolve_decision(d["id"], "Ja, aannemen", "Team heeft capaciteit nodig")
    assert resolved["status"] == "besloten"
    assert resolved["decision"] == "Ja, aannemen"
    assert resolved["decided_at"]

    assert decisions_service.list_decisions("WeAreImpact", status="open") == []
    resolved_list = decisions_service.list_decisions("WeAreImpact", status="besloten")
    assert len(resolved_list) == 1


def test_resolve_onbekend_besluit_geeft_none():
    assert decisions_service.resolve_decision(99999, "iets") is None


def test_reopen_decision():
    d = decisions_service.add_decision("WeAreImpact", "Prijs verhogen?")
    decisions_service.resolve_decision(d["id"], "Nee, nog niet")
    reopened = decisions_service.reopen_decision(d["id"])
    assert reopened["status"] == "open"
    assert reopened["decided_at"] == ""
    # De vorige keuze blijft zichtbaar in de rij, alleen de status verandert.
    assert reopened["decision"] == "Nee, nog niet"


def test_delete_decision():
    d = decisions_service.add_decision("WeAreImpact", "Weg te gooien besluit")
    decisions_service.delete_decision(d["id"])
    assert decisions_service.get_decision(d["id"]) is None


def test_projecten_zijn_gescheiden():
    decisions_service.add_decision("WeAreImpact", "Besluit A")
    decisions_service.add_decision("Bewaard voor Jou", "Besluit B")
    assert len(decisions_service.list_decisions("WeAreImpact")) == 1
    assert len(decisions_service.list_decisions("Bewaard voor Jou")) == 1


def test_router_gemonteerd():
    from backend.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/decisions" in paths
    assert "/api/decisions/{decision_id}/resolve" in paths


def test_api_leeg_titel_geeft_400():
    from fastapi.testclient import TestClient
    from backend.main import app
    r = TestClient(app).post("/api/decisions", json={"project": "WeAreImpact", "title": "   "})
    assert r.status_code == 400

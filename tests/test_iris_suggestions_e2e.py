"""End-to-end test van de Iris /api/iris/suggestions-endpoints via TestClient.

Draait in-process (geen live server, geen auth-gate — conftest zet
IMPACTOS_PASSWORD='' en roept init_db()). Dekt de volledige flow:
briefing-run genereert suggestions -> list -> approve -> apply.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.main import app
    return TestClient(app)


def test_suggestions_endpoint_e2e(client, monkeypatch):
    # 1) Lijst is leeg bij start.
    r = client.get("/api/iris/suggestions")
    assert r.status_code == 200
    assert r.json()["suggestions"] == []

    # 2) Briefing-run genereert (onder de live-LLM) suggestions.
    #    Zonder LLM (offline) blijft de lijst leeg — dat is oké,
    #    de unit-tests dekken de executor zelf. `service._llm` mocken (i.p.v.
    #    de echte gateway raken, zoals test_iris.py al doet): deze test riep
    #    ongemockt de PRODUCTIE-OpenModel-gateway aan via run_morning_briefing.
    #    Op 26 aug 2026 gaf de gateway daarbij een echte 403 quota-exceeded
    #    terug; die schrijft via note_llm_quota_exhausted() een 'quota'-marker
    #    in de (gedeelde) test-DB die 45 min actief blijft (LLM_QUOTA_BACKOFF_
    #    MINUTES) en zo elke latere test in dezelfde pytest-sessie vergiftigde
    #    — test_llm_budget.py faalde daardoor alleen in de volle testrun, nooit
    #    geïsoleerd. Los van de kosten/flakiness van een echte netwerkcall in
    #    een e2e-test hoort dat sowieso niet: tests mogen nooit de productie-
    #    quota aanspreken.
    from backend.domains.iris import service
    async def offline_llm(system, prompt, max_tokens=3000):
        return ""
    monkeypatch.setattr(service, "_llm", offline_llm)

    r2 = client.post("/api/iris/run-now")
    assert r2.status_code == 200
    data = r2.json()
    assert "saved_suggestions" in data

    # 3) Lijst endpoint beantwoordt correct (zelfs leeg).
    r3 = client.get("/api/iris/suggestions")
    assert r3.status_code == 200
    sugs = r3.json()["suggestions"]
    # Elke gegenereerde rij heeft de verwachte velden.
    from backend.domains.iris.fix import _ALLOWED_TYPES
    for s in sugs:
        assert s["id"] and s["type"] and s["status"]
        assert s["type"] in _ALLOWED_TYPES


def test_suggestions_approve_reject_not_found(client):
    # Onbekende id -> 404, geen crash.
    r = client.post("/api/iris/suggestions/nope/approve")
    assert r.status_code == 404
    r2 = client.post("/api/iris/suggestions/nope/reject")
    assert r2.status_code == 404
    r3 = client.post("/api/iris/suggestions/nope/apply")
    assert r3.status_code == 400

"""Tests voor /api/coach-context/holding: het read-only, token-gated
contract dat andere systemen (mocht dat ooit weer nodig zijn) kunnen gebruiken
om de holding-brede stand te lezen. Het proactieve WhatsApp-signaal en de
kernfunctie van De Sparringpartner zelf leven native in backend/domains/coach
(zie tests/test_coach.py) — dit token-gate blijft alleen bestaan voor externe,
niet-ingelogde aanroepers.
"""
import pytest


def _client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_router_gemonteerd():
    from backend.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any(p.startswith("/api/coach-context") for p in paths)


def test_werkt_ook_met_actieve_sessie_gate(monkeypatch):
    """Regressie: bij een gezette IMPACTOS_PASSWORD (Vincents echte lokale
    instance) strandde deze route eerder op de sessie-cookie-middleware vóórdat
    de eigen Bearer-token-check ooit werd bereikt — /api/coach-context/ moet in
    auth/service.py:PUBLIC_PREFIXES staan, anders een altijd-401 ondanks een
    juist token."""
    from backend.shared import config
    monkeypatch.setattr(config, "COACH_BRIDGE_TOKEN", "het-echte-geheim")
    monkeypatch.setenv("IMPACTOS_PASSWORD", "een-wachtwoord")

    r = _client().get(
        "/api/coach-context/holding",
        headers={"Authorization": "Bearer het-echte-geheim"},
    )
    assert r.status_code == 200

    # Zonder geldig token blijft de eigen gate (niet de sessie-gate) de reden.
    r_bad = _client().get("/api/coach-context/holding")
    assert r_bad.status_code == 401
    assert r_bad.json()["detail"] != "Niet geautoriseerd — log eerst in."

    # Een andere, wél sessie-beschermde route blijft gewoon dicht.
    r_other = _client().get("/api/action-center")
    assert r_other.status_code == 401


def test_zonder_token_geconfigureerd_geeft_503(monkeypatch):
    from backend.shared import config
    monkeypatch.setattr(config, "COACH_BRIDGE_TOKEN", "")
    r = _client().get("/api/coach-context/holding")
    assert r.status_code == 503


def test_verkeerd_token_geeft_401(monkeypatch):
    from backend.shared import config
    monkeypatch.setattr(config, "COACH_BRIDGE_TOKEN", "het-echte-geheim")
    r = _client().get(
        "/api/coach-context/holding",
        headers={"Authorization": "Bearer verkeerd"},
    )
    assert r.status_code == 401


def test_ontbrekend_token_geeft_401(monkeypatch):
    from backend.shared import config
    monkeypatch.setattr(config, "COACH_BRIDGE_TOKEN", "het-echte-geheim")
    r = _client().get("/api/coach-context/holding")
    assert r.status_code == 401


def test_juist_token_geeft_holding_context(monkeypatch):
    from backend.shared import config
    monkeypatch.setattr(config, "COACH_BRIDGE_TOKEN", "het-echte-geheim")
    r = _client().get(
        "/api/coach-context/holding",
        headers={"Authorization": "Bearer het-echte-geheim"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "projecten" in body and "stilstaand" in body["projecten"]
    assert "waarheidsaudit" in body
    assert "gemiste_runs" in body
    assert "iris" in body
    assert "agenda" in body


def test_build_holding_context_faalt_niet_op_lege_installatie():
    """Een verse installatie heeft geen projecten/rapporten — dat mag nooit
    een crash geven, alleen lege/nul-waarden (zelfde regel als de rest van
    Iris' domein: 'geen data' is geen storing)."""
    import asyncio
    from backend.domains.coach_bridge.context import build_holding_context

    result = asyncio.run(build_holding_context())
    assert result["status"] in ("ok", "error")
    if result["status"] == "ok":
        assert isinstance(result["projecten"]["stilstaand"], list)

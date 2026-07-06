"""Applicatie-smoke: routers monteren, kernendpoints antwoorden, debug is weg."""


def _client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_status_endpoint():
    r = _client().get("/api/status")
    assert r.status_code == 200
    assert r.json()["status"] == "online"


def test_action_center_endpoints():
    c = _client()
    r = c.get("/api/action-center")
    assert r.status_code == 200
    assert {"counts", "items"} <= set(r.json().keys())
    assert c.get("/api/action-center/feed").status_code == 200


def test_debug_keys_endpoint_bestaat_niet_meer():
    assert _client().get("/api/debug/keys").status_code == 404


def test_alle_domein_routers_gemonteerd():
    from backend.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for verwacht in ("/api/goals", "/api/action-center", "/api/content-queue",
                     "/api/vacancies", "/api/leads", "/api/strategist/control-room"):
        assert any(p.startswith(verwacht) for p in paths), f"router ontbreekt: {verwacht}"

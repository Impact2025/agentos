"""Tests voor het live LLM-verbruiksoverzicht (OpenModel-credits op het dashboard)."""
import pytest


@pytest.fixture()
def usage_clean(conn):
    conn.execute("DELETE FROM llm_usage")
    conn.commit()
    yield
    conn.execute("DELETE FROM llm_usage")
    conn.commit()


def _seed(conn):
    from backend.shared.outcomes import log_llm_usage
    log_llm_usage(backend="openmodel", model="claude-sonnet-4-6", route="iris",
                  prompt_tokens=10000, completion_tokens=2000, total_tokens=12000)
    log_llm_usage(backend="openmodel", model="deepseek-v4-flash", route="content",
                  prompt_tokens=50000, completion_tokens=8000, total_tokens=58000)
    log_llm_usage(backend="openmodel", model="deepseek-v4-flash", route="content",
                  prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
                  status="error", error="boom")
    conn.execute(
        "INSERT INTO llm_usage (backend, model, route, prompt_tokens, completion_tokens, "
        "total_tokens, status, created_at) "
        "VALUES ('openmodel','deepseek-v4-flash','mail', 300, 50, 350, 'ok', "
        "datetime('now','-1 day'))"
    )
    conn.commit()


def test_llm_usage_summary(conn, usage_clean):
    from backend.shared.outcomes import llm_usage_summary
    _seed(conn)
    d = llm_usage_summary(days=7)

    assert d["today"]["total_tokens"] == 71100
    assert d["today"]["calls"] == 3
    assert d["today"]["errors"] == 1
    assert d["budget"] > 0

    # Grootverbruikers aflopend gesorteerd, gegroepeerd op route+model
    assert d["by_route"][0]["route"] == "content"
    assert d["by_route"][0]["total_tokens"] == 59100
    assert d["by_route"][0]["errors"] == 1
    assert d["by_route"][1]["route"] == "iris"

    # Dagreeks: altijd `days` punten, lege dagen op 0, gisteren en vandaag gevuld
    assert len(d["days"]) == 7
    assert d["days"][-1]["total_tokens"] == 71100
    assert d["days"][-2]["total_tokens"] == 350
    assert d["days"][0]["total_tokens"] == 0


def test_llm_usage_summary_leeg(usage_clean):
    from backend.shared.outcomes import llm_usage_summary
    d = llm_usage_summary(days=3)
    assert d["today"]["total_tokens"] == 0
    assert d["by_route"] == []
    assert len(d["days"]) == 3


def test_quota_backoff(conn, usage_clean, clean_tables, monkeypatch):
    """Zelf-uitlijnende rem: een verse 403-quota-marker pauzeert autonome runs,
    een verlopen marker niet meer.

    `llm_budget_exceeded()` slaat de pauze bewust over als de actieve backend
    lokaal/Ollama is (die kost geen cloud-quota, zie de docstring in
    outcomes.py). Deze test gaat over de budget-/quota-logica zelf, niet over
    welke backend toevallig in de lokale .env staat — zonder deze patch
    faalde de test op elke machine met HERMES_LOCAL_URL/OLLAMA_BASE_URL
    ingevuld, wat 'ambient config bepaalt of de test slaagt' als bug heeft
    (gemeten 11 aug 2026)."""
    from backend.shared import config as cfg, outcomes
    monkeypatch.setattr(cfg, "hermes_backend", lambda: "openmodel")

    assert not outcomes.llm_quota_backoff_active()
    assert not outcomes.llm_budget_exceeded()

    outcomes.note_llm_quota_exhausted(backend="openmodel",
                                      model="claude-sonnet-4-6", route="iris")
    assert outcomes.llm_quota_backoff_active()
    assert outcomes.llm_budget_exceeded()
    with pytest.raises(outcomes.BudgetExceeded):
        outcomes.require_llm_budget("test")
    # De blokkade-kaart wordt maar één keer per dag gelogd
    with pytest.raises(outcomes.BudgetExceeded):
        outcomes.require_llm_budget("test")
    n = conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE action='llm-budget-op'"
    ).fetchone()[0]
    assert n == 1

    # Verlopen marker → rem eraf
    conn.execute("UPDATE llm_usage SET created_at = datetime('now','-2 hours') "
                 "WHERE status='quota'")
    conn.commit()
    assert not outcomes.llm_quota_backoff_active()
    assert not outcomes.llm_budget_exceeded()


def test_llm_usage_endpoint(conn, usage_clean):
    from fastapi.testclient import TestClient
    from backend.main import app
    _seed(conn)
    r = TestClient(app).get("/api/action-center/llm-usage?days=7")
    assert r.status_code == 200
    d = r.json()
    assert d["today"]["total_tokens"] == 71100
    assert d["by_route"][0]["route"] == "content"

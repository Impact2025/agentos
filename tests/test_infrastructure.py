"""Tests voor DB-schema/migraties en de llm_usage-telemetrie.

Deze tests garanderen dat de fundering niet meer stil naar de klote kan gaan:
als een migratie faalt of een kolom ontbreekt, faalt de test vóórdat de server
draait.
"""
from backend.shared.database import get_conn
from backend.shared.outcomes import log_llm_usage, daily_llm_tokens


def test_init_db_creates_core_tables():
    with get_conn() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for required in ("content_jobs", "scheduler_runs", "activity_log",
                     "journeys", "llm_usage", "content_jobs"):
        assert required in names, f"ontbrekende tabel: {required}"


def test_content_jobs_has_improve_attempts_column():
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(content_jobs)").fetchall()}
    assert "improve_attempts" in cols


def test_llm_usage_log_and_daily_total():
    # Schone lei: andere tests mogen usage hebben gelogd zonder deze test te breken.
    with get_conn() as conn:
        conn.execute("DELETE FROM llm_usage")
    before = daily_llm_tokens()
    log_llm_usage(backend="openmodel", model="claude-sonnet-4-6", route="test",
                  prompt_tokens=100, completion_tokens=50, total_tokens=150)
    log_llm_usage(backend="openmodel", model="claude-sonnet-4-6", route="test",
                  prompt_tokens=200, completion_tokens=100, total_tokens=300)
    assert daily_llm_tokens() == before + 450
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT backend, total_tokens FROM llm_usage ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["backend"] == "openmodel"
    assert rows[1]["total_tokens"] == 300


def test_log_llm_usage_never_raises_on_broken_conn(monkeypatch):
    # Telemetrie mag nooit een agent-run laten crashen, ook niet bij een
    # beschadigde DB. We simuleren een exception in de insert.
    from backend.shared import outcomes
    import sqlite3
    real = get_conn

    def boom():
        raise sqlite3.OperationalError("simulated corruption")
    monkeypatch.setattr(outcomes, "get_conn", boom)
    # Mag geen exception gooien:
    outcomes.log_llm_usage(backend="openmodel", model="x", total_tokens=10)

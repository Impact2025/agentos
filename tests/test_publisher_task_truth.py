"""Een publisher-taak die niets publiceert mag niet als 'voltooid' tellen.

Aanleiding (25 jul 2026): het doel "Optimaliseer de bestaande content van
Steentjebijsteentje" sloot af met 12/12 taken voltooid, terwijl beide
publisher-taken letterlijk "CONCEPT — geen echte actie uitgevoerd" als
resultaat hadden. Er was niets aan de site veranderd. Het doel gold daarna als
afgerond én dempte in die hoedanigheid 14 dagen lang de positie-alert op het
dashboard. Activiteit werd zo verward met effect.

Nu: kan de échte actie (stagen naar de Wachtrij) niet, dan faalt de taak
direct — zonder retry en zonder 'alternatieve aanpak', want die zouden de taak
alsnog met LLM-tekst op 'completed' zetten. Het doel eindigt als 'partial' en
`_goal_addresses` dempt daar niet op.
"""
import asyncio

import pytest

from backend.shared.database import get_conn


def _seed_task(goal_id="gp1", task_id="tp1", skill="publisher"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goals (id, title, objective, status, project, created_at, updated_at) "
            "VALUES (?, 'T', 'O', 'running', 'Testsite', datetime('now'), datetime('now'))",
            (goal_id,),
        )
        conn.execute(
            "INSERT INTO goal_phases (id, goal_id, title, description, ord, status, created_at, updated_at) "
            "VALUES ('php', ?, 'F', '', 1, 'running', datetime('now'), datetime('now'))",
            (goal_id,),
        )
        conn.execute(
            "INSERT INTO goal_tasks (id, goal_id, phase_id, title, description, skill, "
            "status, dependencies, created_at, updated_at, ord) "
            "VALUES (?, ?, 'php', 'Publiceer geupdatete pagina A', '', ?, 'ready', "
            "'[]', datetime('now'), datetime('now'), 1)",
            (task_id, goal_id, skill),
        )
        conn.commit()
    return {"id": task_id, "title": "Publiceer geupdatete pagina A",
            "description": "", "skill": skill, "retry_count": 0, "max_retries": 3}


def _task_row(task_id="tp1"):
    with get_conn() as conn:
        return dict(conn.execute(
            "SELECT status, result, error FROM goal_tasks WHERE id = ?",
            (task_id,)).fetchone())


def test_publisher_zonder_publiceerbaar_artikel_faalt(clean_tables, monkeypatch):
    from backend.domains.goal import service

    async def _niets(goal_id, task_title, project, task_id=""):
        return None, "geen publiceerbaar artikel in dit doel"

    monkeypatch.setattr(service, "_stage_to_wachtrij", _niets)
    task = _seed_task()

    asyncio.run(service._execute_task("gp1", task))

    row = _task_row()
    assert row["status"] == "failed"
    assert "publiceren niet uitgevoerd" in (row["error"] or "")
    assert "geen publiceerbaar artikel" in (row["error"] or "")


def test_publisher_faalt_zonder_retry_of_alternatief(clean_tables, monkeypatch):
    """Retries en het 'alternatief' zijn LLM-paden: die zouden de taak alsnog
    op 'completed' zetten met tekst die niemand heeft gepubliceerd."""
    from backend.domains.goal import service

    geroepen = {"alt": 0}

    async def _niets(goal_id, task_title, project, task_id=""):
        return None, "kwaliteitsgate niet gehaald (78/100)"

    async def _alt(*a, **kw):
        geroepen["alt"] += 1
        return "Hier is alsnog een mooi verhaal."

    monkeypatch.setattr(service, "_stage_to_wachtrij", _niets)
    monkeypatch.setattr(service, "_find_alternative", _alt)
    task = _seed_task()

    asyncio.run(service._execute_task("gp1", task))

    row = _task_row()
    assert row["status"] == "failed"
    assert geroepen["alt"] == 0
    assert not (row["result"] or "").strip()


def test_publisher_met_gestaged_artikel_slaagt(clean_tables, monkeypatch):
    """De echte actie lukt wel → taak voltooid, met verwijzing naar de job.
    De review-gate blijft staan: er is niets live gezet."""
    from backend.domains.goal import service

    async def _gestaged(goal_id, task_title, project, task_id=""):
        return ("job-123", "Vier microgewoontes", 86), ""

    monkeypatch.setattr(service, "_stage_to_wachtrij", _gestaged)
    task = _seed_task()

    asyncio.run(service._execute_task("gp1", task))

    row = _task_row()
    assert row["status"] == "completed"
    assert "job-123" in (row["result"] or "")
    assert "Wachtrij" in (row["result"] or "")

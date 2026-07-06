"""Uitkomst-kaarten — één standaard voor 'wat heeft een agent gedaan'.

Elke agent-run (goal-taak, scheduler-job, strategist-actie, publish) sluit af
met een uitkomst: wat is er gedaan, waar staat het resultaat (artifact), en
wat moet de mens nog doen (next_step). Fouten krijgen status='error' zodat
het Actiecentrum ze als inbox-item toont.
"""
import uuid

from .database import get_conn


def log_outcome(
    project: str,
    action: str,
    detail: str,
    *,
    artifact: str = "",
    next_step: str = "",
    status: str = "ok",
) -> str:
    """Schrijf een uitkomst-kaart naar activity_log. Retourneert het id.

    artifact: URL of pad naar het concrete resultaat (leeg = geen artefact,
              wat voor een 'echte actie' een smell is).
    next_step: wat Vincent moet doen, in één zin. Leeg = niets.
    status: 'ok' | 'error'.
    """
    outcome_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (id, project, action, detail, artifact, next_step, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (outcome_id, project, action, detail, artifact, next_step, status),
        )
    return outcome_id

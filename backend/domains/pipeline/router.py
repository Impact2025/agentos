import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from ...shared.models import TaskCreate, TaskUpdate, TaskOut
from ...shared.database import get_conn
from ...domains.pipeline.service import create_triage, get_next_ready_task, set_task_status, TRIAGE_STATUSES

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return dict(row)


@router.get("", response_model=List[TaskOut])
def list_tasks():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY status, position, created_at ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(body: TaskCreate):
    task_id = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        # Positie is het huidige aantal taken in die kolom
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = ?", (body.status,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO tasks (id, title, description, status, agent, assigned_agent_id, position, workspace_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, body.title, body.description or "", body.status,
             body.agent, body.assigned_agent_id, count, body.workspace_path or "", now, now),
        )
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: str, body: TaskUpdate):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Taak niet gevonden")
        task = _row_to_dict(row)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return task

    updates["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]

    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {fields} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Taak niet gevonden")


# ── Triage endpoints ──────────────────────────────────────────────────

class TriageRequest(BaseModel):
    prompt: str
    workspace_path: Optional[str] = ""


class TriageOut(BaseModel):
    session_id: str
    tasks: List[TaskOut]


@router.post("/triage", response_model=TriageOut, status_code=201)
def triage_prompt(body: TriageRequest):
    tasks = create_triage(body.prompt, body.workspace_path or None)
    session_id = f"triage-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    return TriageOut(session_id=session_id, tasks=tasks)


@router.get("/triage/next-ready", response_model=TaskOut | None)
def triage_next_ready():
    task = get_next_ready_task()
    if not task:
        return None
    return task


@router.patch("/{task_id}/status", response_model=TaskOut)
def triage_update_status(task_id: str, body: dict):
    status = body.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="'status' is verplicht in de request body")
    if status not in TRIAGE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Ongeldige status '{status}'. Toegestaan: {TRIAGE_STATUSES}")
    task = set_task_status(task_id, status)
    if not task:
        raise HTTPException(status_code=404, detail="Taak niet gevonden")
    return task

"""Goal Mode router — REST + SSE endpoints voor langetermijndoelen."""

import asyncio
import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import service as goal_service
from ..delegate import event_bus
from .schema import GoalCreateRequest, TaskUpdateRequest

router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalPlanConfirm(BaseModel):
    goal_id: str


class GoalAction(BaseModel):
    goal_id: str


@router.post("/plan", status_code=201)
async def plan_goal(body: GoalCreateRequest):
    """Stap 1: Maak een goal en laat Hermes een plan genereren (decompositie)."""
    try:
        result = await goal_service.create_and_plan(
            title=body.title,
            objective=body.objective,
            project=body.project,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, detail=str(e)[:300])
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/confirm")
def confirm_goal(body: GoalPlanConfirm):
    """Stap 2: Keur het plan goed → schrijf fasen/taken naar DB."""
    try:
        return goal_service.confirm_plan(body.goal_id)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/start")
async def start_goal(body: GoalAction):
    """Stap 3: Start de executie-loop (achtergrond)."""
    try:
        return await goal_service.start_goal_async(body.goal_id)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/pause")
def pause_goal(body: GoalAction):
    """Pauzeer een goal."""
    try:
        return goal_service.pause_goal(body.goal_id)
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/resume")
def resume_goal(body: GoalAction):
    """Hervat een gepauzeerde goal."""
    try:
        return goal_service.resume_goal(body.goal_id)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.get("")
def list_goals(limit: int = Query(10, ge=1, le=50), project: Optional[str] = None):
    return goal_service.list_goals(limit=limit, project=project)


@router.post("/retry-failed")
async def retry_failed_goal(body: GoalAction):
    """Reset een failed goal naar ready en start opnieuw."""
    try:
        return await goal_service.retry_failed_goal(body.goal_id)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/resume-stalled")
def resume_stalled_goal(body: GoalAction):
    """Herstart de achtergrond-loop van een goal die op 'running' staat maar
    is blijven hangen (bv. na een server-restart)."""
    try:
        return goal_service.resume_stalled_goal(body.goal_id)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.delete("/{goal_id}")
def delete_goal(goal_id: str):
    """Verwijder een goal (draft/completed/failed) inclusief fasen/taken."""
    try:
        goal_service.delete_goal(goal_id)
        return {"deleted": goal_id}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


@router.get("/{goal_id}")
def get_goal(goal_id: str):
    goal = goal_service.get_goal(goal_id)
    if not goal:
        raise HTTPException(404, detail="Goal niet gevonden")
    return goal


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdateRequest):
    """Handmatige status-override voor een taak."""
    task = goal_service.get_task(task_id)
    if not task:
        raise HTTPException(404, detail="Taak niet gevonden")
    allowed = {"pending", "ready", "running", "completed", "failed", "skipped"}
    if body.status not in allowed:
        raise HTTPException(400, detail=f"Ongeldige status: {body.status}. Toegestaan: {sorted(allowed)}")
    from .service import _update_task, _log_activity
    _update_task(task_id, status=body.status)
    _log_activity(task["goal_id"], "task_manual", f"Taak '{task['title']}' handmatig op '{body.status}' gezet")
    return goal_service.get_task(task_id)


@router.get("/stream")
async def goal_stream():
    """SSE: live goal events (start, progress, task_done, phase_done, done)."""
    async def event_gen():
        q = event_bus.subscribe()
        try:
            for ev in event_bus.recent(limit=10):
                etype = str(ev.get("type", ""))
                if etype.startswith("goal_"):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    etype = str(ev.get("type", ""))
                    if etype.startswith("goal_"):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(q)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

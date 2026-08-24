"""Agent Control router — live stal-overzicht + deploy voor Iris.

  GET  /api/agentctl/agents        → 13 profielen met live occupancy (idle/busy)
  GET  /api/agentctl/roster        → marketing-cast (gezichten) + crew + orchestrator
  POST /api/agentctl/deploy        → zet één agent op een taak (echte Gauntlet-run)
  POST /api/agentctl/recover       → forceer orphan-recovery (running→stopped)
  GET  /api/agentctl/stream        → SSE met agentctl_*-events (deploy, recovery)
"""
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import service as agentctl_service
from ..delegate import event_bus
from . import suggest as suggest_service
from ...agents import get_faces, CREW, ORCHESTRATOR

router = APIRouter(prefix="/api/agentctl", tags=["agentctl"])


class DeployRequest(BaseModel):
    agent_id: int
    task: str
    project: Optional[str] = None
    benchmark: Optional[str] = None


@router.get("/agents")
def agents():
    """Live stal: wie is idle, wie is bezig, waarop."""
    return agentctl_service.list_agents()


@router.get("/roster")
def roster():
    """Single source of truth voor de agent-identiteit (code/UI/marketing).

    Geeft de marketing-gezichten (Iris/Mara/Bram/Noor), de interne crew
    (Toby/AI Diary) en — alleen wanneer daadwerkelijk live — de orchestrator
    (Simon). Voorkomt phantom-agents in de marketing.
    """
    import backend.shared.config as c
    import sqlite3
    faces = []
    with sqlite3.connect(str(c.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        for f in get_faces():
            covered = conn.execute(
                "SELECT GROUP_CONCAT(name, ', ') AS names FROM agent_profiles WHERE face_key = ?",
                (f.key,),
            ).fetchone()["names"] or ""
            faces.append({
                "key": f.key, "name": f.name, "title": f.title,
                "tagline": f.tagline, "bio": f.bio, "layer": f.layer,
                "reports_to": f.reports_to,
                "covers_profiles": covered.split(", ") if covered else [],
            })
    crew = [{"key": k, "name": v["name"], "title": v["title"],
             "tagline": v["tagline"], "layer": v["layer"]} for k, v in CREW.items()]
    # Iris' team: alle faces die aan haar rapporteren (haar "staf").
    iris_team = [f["key"] for f in faces if f.get("reports_to") == "iris"]
    simon_visible = False
    try:
        with sqlite3.connect(str(c.DB_PATH)) as conn:
            simon_visible = bool(conn.execute(
                "SELECT 1 FROM agent_profiles WHERE name LIKE '%Simon%' OR face_key='simon' LIMIT 1"
            ).fetchone())
    except Exception:
        pass
    out = {"faces": faces, "crew": crew, "manager": "iris", "iris_team": iris_team}
    if "orchestrator" not in [f["layer"] for f in faces] and simon_visible:
        out["orchestrator"] = {k: ORCHESTRATOR[k] for k in
                               ("key", "name", "title", "layer", "tagline")}
    return out


@router.post("/deploy", status_code=201)
async def deploy(body: DeployRequest):
    """Zet een expert-agent op een taak. Start een ECHTE Gauntlet-run."""
    try:
        return agentctl_service.deploy_agent(
            agent_id=body.agent_id,
            task=body.task,
            project=body.project,
            benchmark=body.benchmark,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:300])


@router.get("/recover")
def recover():
    """Forceer orphan-recovery: 'running' runs/loops zonder taak -> 'stopped'."""
    return agentctl_service.recover_orphans()


@router.post("/cleanup-goals")
def cleanup_goals():
    """Markeer verweesde 'partial' goals (>7 dagen, geen actieve run) als completed."""
    return agentctl_service.cleanup_stale_goals(older_than_days=7)


@router.get("/suggest")
def suggest():
    """Iris' autonome inzet-voorstellen: per project de grootste hefboom-agent."""
    return suggest_service.suggest()


@router.post("/suggest/execute")
async def suggest_execute():
    """Voer alle suggesties uit — elke pijler via zijn eigen echte mechanisme
    (zie agentctl/suggest.py). Response bevat per suggestie het concrete
    resultaat (status/artifact), niet alleen een run_id."""
    try:
        return await suggest_service.execute_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:300])


@router.get("/stream")
async def stream():
    """SSE: pusht agentctl_deploy / agentctl_recover events naar de UI."""
    async def event_gen():
        q = event_bus.subscribe()
        try:
            for ev in event_bus.recent(limit=10):
                if str(ev.get("type", "")).startswith("agentctl_"):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    if str(ev.get("type", "")).startswith("agentctl_"):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

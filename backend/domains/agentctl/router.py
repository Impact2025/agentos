"""Agent Control router — live stal-overzicht + deploy voor Iris.

  GET  /api/agentctl/agents        → 13 profielen met live occupancy (idle/busy)
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
    """Voer alle suggesties uit als echte agent-deploys (via de Gauntlet-pijplijn)."""
    try:
        return suggest_service.execute_all()
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

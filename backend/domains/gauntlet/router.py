"""
Gauntlet router — REST + SSE voor de Gauntlet Loop (matcher + parallelle blinde critici).

  POST /api/gauntlet              → start een Gauntlet Loop (objective + benchmark verplicht).
  GET  /api/gauntlet              → lijst recente runs.
  GET  /api/gauntlet/stream       → SSE; pusht elke gebeurtenis live (gauntlet_*-events).
  GET  /api/gauntlet/{id}         → één run + deeltaken + iteraties.
  POST /api/gauntlet/{id}/stop    → menselijke STOP-knop (breekt de lussen bij volgende ronde af).
  POST /api/gauntlet/{id}/verdict → menselijke eindjurat (laatste jury-oordeel).

Deelt de globale event_bus met /api/loops/stream en /api/delegate/stream; de frontend
filtert op 'gauntlet_'-events.
"""
import json
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import service as gauntlet_service
from ...domains.delegate import event_bus

router = APIRouter(prefix="/api/gauntlet", tags=["gauntlet"])


class GauntletRequest(BaseModel):
    objective: str
    benchmark: str
    threshold: int = 85
    max_iterations: int = 3
    session_id: Optional[str] = None
    model: Optional[str] = None  # optioneel: bv. deepseek-v4-pro, qwen3.6-flash


class GauntletVerdict(BaseModel):
    verdict: str          # bv. "goedgekeurd" | "afgekeurd" | "aangepast"
    note: Optional[str] = None


@router.post("", status_code=201)
async def start_gauntlet(body: GauntletRequest):
    try:
        return gauntlet_service.spawn_gauntlet(
            objective=body.objective,
            benchmark=body.benchmark,
            threshold=body.threshold,
            max_iterations=body.max_iterations,
            session_id=body.session_id,
            model_override=body.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_gauntlets():
    return gauntlet_service.list_runs()


@router.get("/stream")
async def gauntlet_stream():
    """SSE: stuurt elk gauntlet-event (start/plan/deeltaak/ronde/afronding) live naar de UI."""
    async def event_gen():
        q = event_bus.subscribe()
        try:
            for ev in event_bus.recent(limit=10):
                if str(ev.get("type", "")).startswith("gauntlet_"):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    if str(ev.get("type", "")).startswith("gauntlet_"):
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


@router.get("/{run_id}")
def get_gauntlet(run_id: str):
    run = gauntlet_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Gauntlet run niet gevonden")
    return run


@router.post("/{run_id}/stop")
def stop_gauntlet(run_id: str):
    ok = gauntlet_service.stop_gauntlet(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Geen lopende run om te stoppen")
    return {"run_id": run_id, "stopped": True}


@router.post("/{run_id}/verdict")
def record_verdict(run_id: str, body: GauntletVerdict):
    ok = gauntlet_service.record_verdict(run_id, body.verdict, body.note)
    if not ok:
        raise HTTPException(status_code=404, detail="Gauntlet run niet gevonden")
    return {"run_id": run_id, "verdict": body.verdict, "recorded": True}


class GauntletPublish(BaseModel):
    site_id: Optional[str] = None
    site_name: Optional[str] = None   # Orchestrator stuurt de project-naam; wordt naar site_id vertaald
    title: Optional[str] = None       # echte titel i.p.v. de vage objective
    keyword: Optional[str] = None
    slug: Optional[str] = None


@router.post("/{run_id}/publish")
def publish_gauntlet(run_id: str, body: Optional[GauntletPublish] = None):
    """Publish-gate: zet een PASSED/partial Gauntlet-run om in een content_job.

    Blokkeert als de blinde criticus de benchmark niet haalde (zie service.publish_run_to_wachtrij).
    """
    try:
        site_id = site_name = title = keyword = slug = None
        if body:
            site_id = body.site_id
            site_name = body.site_name
            title = body.title
            keyword = body.keyword
            slug = body.slug
        return gauntlet_service.publish_run_to_wachtrij(
            run_id, site_id=site_id, site_name=site_name,
            title=title, keyword=keyword, slug=slug,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

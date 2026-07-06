"""
Loops router — REST + SSE voor Loop Engineering (maker/beoordelaar-lus).

  POST /api/loops          → start een kwaliteitslus (non-blocking, keert direct terug).
  GET  /api/loops          → lijst recente lussen.
  GET  /api/loops/stream   → globale SSE-stream; pusht elke ronde (concept + score) live.
  GET  /api/loops/{id}     → één lus + alle iteraties (concept/score/feedback).

De stream deelt de globale event_bus met /api/delegate/stream; de frontend
filtert op 'loop_*'-events.
"""
import json
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import service as loop_service
from ...domains.delegate import event_bus

router = APIRouter(prefix="/api/loops", tags=["loops"])


class LoopRequest(BaseModel):
    objective: str
    maker_profile_id: Optional[int] = None
    reviewer_profile_id: Optional[int] = None
    threshold: int = 85
    max_iterations: int = 4
    session_id: Optional[str] = None


@router.post("", status_code=201)
async def start_loop(body: LoopRequest):
    try:
        return loop_service.spawn_loop(
            objective=body.objective,
            maker_profile_id=body.maker_profile_id,
            reviewer_profile_id=body.reviewer_profile_id,
            threshold=body.threshold,
            max_iterations=body.max_iterations,
            session_id=body.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_loops():
    return loop_service.list_loops()


@router.get("/stream")
async def loop_stream():
    """SSE: stuurt elk loop-event (start/ronde/afronding) live naar de UI."""
    async def event_gen():
        q = event_bus.subscribe()
        try:
            for ev in event_bus.recent(limit=10):
                if str(ev.get("type", "")).startswith("loop_"):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    if str(ev.get("type", "")).startswith("loop_"):
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


@router.get("/{loop_id}")
def get_loop(loop_id: str):
    loop = loop_service.get_loop(loop_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Lus niet gevonden")
    return loop

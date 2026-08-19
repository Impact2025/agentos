"""
Delegate router — REST + SSE voor de parallelle subagent-laag.

  GET  /api/delegate/stream     → globale SSE-stream; pusht worker-resultaten live
                                   naar de UI als zelfstandige berichten.
  GET  /api/delegate            → lijst recente delegatie-batches.
  GET  /api/delegate/{id}       → één batch + zijn workers (incl. resultaten).
  POST /api/delegate            → handmatig een delegatie starten (plain English /
                                   expliciete workerlijst), buiten de chat om.
"""
import json
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

from . import service as delegate_service
from . import event_bus

router = APIRouter(prefix="/api/delegate", tags=["delegate"])


class WorkerSpec(BaseModel):
    role: str
    goal: str
    profile: Optional[str] = None
    use_tools: bool = False


class DelegateRequest(BaseModel):
    objective: str
    workers: List[WorkerSpec]
    cta: Optional[str] = None
    session_id: Optional[str] = None


@router.post("", status_code=201)
async def start_delegation(body: DelegateRequest):
    if not body.workers:
        raise HTTPException(status_code=400, detail="Minstens één worker vereist.")
    # Prompt-injectie-scan: worker-goals en het hoofd-objective kunnen uit een
    # externe bron komen (chat-tool, mail, webhook). Blokkeer instructies die
    # het model dwingen zijn systeem-prompt te negeren of een andere rol aan
    # te nemen. Zie backend/shared/prompt_safety.py.
    from ...shared.prompt_safety import scan_structured
    fields = {"objective": body.objective}
    for i, w in enumerate(body.workers):
        fields[f"worker[{i}].goal"] = w.goal
        if w.role:
            fields[f"worker[{i}].role"] = w.role
    scan = scan_structured(**fields)
    if scan.blocked:
        raise HTTPException(status_code=400, detail=scan.reason())
    return delegate_service.spawn_delegation(
        objective=body.objective,
        workers=[w.model_dump() for w in body.workers],
        session_id=body.session_id,
        cta=body.cta,
    )


@router.get("")
def list_delegations():
    return delegate_service.list_delegations()


@router.get("/stream")
async def delegate_stream():
    """SSE: stuurt elk subagent-event (start/voortgang/resultaat) live naar de UI.

    De frontend abonneert zich hier één keer en rendert 'worker_done'-events als
    zelfstandige chat-bubbles / dashboard-kaarten.
    """
    async def event_gen():
        q = event_bus.subscribe()
        # Loop Engineering deelt dezelfde event_bus maar heeft een eigen stream
        # (/api/loops/stream); filter die events hier weg.
        def _is_delegate(ev) -> bool:
            return not str(ev.get("type", "")).startswith("loop_")
        try:
            # Stuur eerst de recente buffer mee, zodat een late verbinding niets mist.
            for ev in event_bus.recent(limit=10):
                if _is_delegate(ev):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    if _is_delegate(ev):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive-comment houdt de verbinding (en proxies) levend.
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{delegation_id}")
def get_delegation(delegation_id: str):
    d = delegate_service.get_delegation(delegation_id)
    if not d:
        raise HTTPException(status_code=404, detail="Delegatie niet gevonden")
    return d

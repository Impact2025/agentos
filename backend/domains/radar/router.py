"""
Mission Radar API router — de Competitor Radar / Sky Scanner.

Endpoints:
  GET    /api/radar/sky                      Signalen (filter: project, status, min_score)
  POST   /api/radar/scan                     SSE-scan over de watchlist (nu draaien)
  GET    /api/radar/stats                    KPI's voor de UI
  GET    /api/radar/watch-list               Watchlist (filter: project)
  POST   /api/radar/watch-list               Watch-item toevoegen (keyword/competitor/rss)
  PATCH  /api/radar/watch-list/{id}          Actief aan/uit
  DELETE /api/radar/watch-list/{id}          Verwijderen
  PATCH  /api/radar/signals/{id}             Status (new/targeted/converted/dismissed)
  DELETE /api/radar/signals/{id}             Verwijderen
  POST   /api/radar/signals/{id}/scrape      Volledige brontekst ophalen
  POST   /api/radar/signals/{id}/aeo-attack  AEO Domination Journey → conveyor-taken
  POST   /api/radar/signals/{id}/notebooklm  NotebookLM-bronpakket genereren → vault
  POST   /api/radar/signals/{id}/queue-listicle  Afgeronde listicle → publicatie-wachtrij
  POST   /api/radar/signals/{id}/infographic Infographic-PNG genereren → vault + download
  POST   /api/radar/signals/{id}/obsidian    Trend-note (opnieuw) naar de vault schrijven
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .service import get_service

router = APIRouter(prefix="/api/radar", tags=["radar"])


class WatchCreate(BaseModel):
    project: str = ""
    label: str = ""
    type: str = "keyword"   # keyword | competitor | rss
    value: str


class WatchUpdate(BaseModel):
    active: bool


class SignalUpdate(BaseModel):
    status: str


class ScanRequest(BaseModel):
    project: Optional[str] = None
    enrich: bool = True


class AeoRequest(BaseModel):
    channels: Optional[List[str]] = None   # subset van listicle | video | reddit


class QueueListicleRequest(BaseModel):
    site_id: Optional[str] = None   # leeg = auto-match op project / enige site


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Sky / signalen ───────────────────────────────────────────────────────────

@router.get("/sky")
def get_sky(
    project: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    limit: int = Query(100, le=500),
):
    return get_service().list_signals(project=project, status=status, source=source,
                                      min_score=min_score, limit=limit)


@router.get("/stats")
def get_stats(project: Optional[str] = Query(None)):
    return get_service().get_stats(project=project)


@router.post("/scan")
async def run_scan(body: ScanRequest):
    svc = get_service()

    async def generate():
        async for ev in svc.run_scan(project=body.project, enrich=body.enrich):
            yield _sse(ev)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.patch("/signals/{signal_id}")
def update_signal(signal_id: str, body: SignalUpdate):
    try:
        updated = get_service().update_signal_status(signal_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")
    return updated


@router.delete("/signals/{signal_id}", status_code=204)
def delete_signal(signal_id: str):
    if not get_service().delete_signal(signal_id):
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")


@router.post("/signals/{signal_id}/scrape")
def scrape_signal(signal_id: str):
    sig = get_service().scrape_source(signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")
    return sig


@router.post("/signals/{signal_id}/aeo-attack")
def aeo_attack(signal_id: str, body: AeoRequest):
    try:
        return get_service().aeo_attack(signal_id, channels=body.channels)
    except LookupError:
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/signals/{signal_id}/queue-listicle")
async def queue_listicle(signal_id: str, body: QueueListicleRequest):
    """Zet de afgeronde AEO-listicle in de content_jobs-wachtrij (pending_review).
    Publiceren gebeurt pas na menselijke goedkeuring in de Wachtrij-tab."""
    try:
        return await get_service().queue_listicle(signal_id, site_id=body.site_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/signals/{signal_id}/infographic")
async def build_infographic(signal_id: str):
    """Genereer een infographic-PNG (1080x1350) — vault + base64 voor download."""
    try:
        return await get_service().build_infographic(signal_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/signals/{signal_id}/notebooklm")
async def notebooklm_package(signal_id: str):
    try:
        return await get_service().build_notebooklm_package(signal_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/signals/{signal_id}/obsidian")
def push_to_obsidian(signal_id: str):
    svc = get_service()
    sig = svc.get_signal(signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")
    path = svc.write_trend_note(sig)
    if not path:
        raise HTTPException(status_code=503, detail="Obsidian-vault niet geconfigureerd")
    return {"obsidian_path": path}


# ── Watchlist ────────────────────────────────────────────────────────────────

@router.get("/watch-list")
def list_watch(project: Optional[str] = Query(None)):
    return get_service().list_watch(project=project)


@router.post("/watch-list", status_code=201)
def add_watch(body: WatchCreate):
    if not body.value.strip():
        raise HTTPException(status_code=422, detail="value is verplicht")
    try:
        return get_service().add_watch(body.project, body.label, body.type, body.value)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.patch("/watch-list/{watch_id}")
def update_watch(watch_id: str, body: WatchUpdate):
    if not get_service().set_watch_active(watch_id, body.active):
        raise HTTPException(status_code=404, detail="Watch-item niet gevonden")
    return {"id": watch_id, "active": body.active}


@router.delete("/watch-list/{watch_id}", status_code=204)
def delete_watch(watch_id: str):
    if not get_service().delete_watch(watch_id):
        raise HTTPException(status_code=404, detail="Watch-item niet gevonden")

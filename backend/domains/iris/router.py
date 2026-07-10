"""Iris API — de manager-agent.

  GET  /api/iris/briefing   → laatste dagbriefing (of live cijfer-snapshot)
  GET  /api/iris/history    → briefing-geschiedenis (cijfers/lessen/advies per dag)
  GET  /api/iris/scores     → actuele deterministische cijfers per project
  GET  /api/iris/lessons    → actieve lessen uit haar geheugen
  POST /api/iris/run-now    → dagbriefing nu draaien (analyse + bijsturing)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import metrics, service

router = APIRouter(prefix="/api/iris", tags=["iris"])


@router.get("/briefing")
def briefing():
    from . import predictions
    track_record = predictions.track_record()
    report = service.latest_report()
    if report:
        report["track_record"] = track_record
        return report
    # Nog geen briefing gedraaid: geef alvast het cijferbeeld zodat de UI
    # nooit leeg is.
    return {"report_date": None, "markdown": "", "grades": {},
            "track_record": track_record, "metrics": metrics.snapshot()}


@router.get("/history")
def history(limit: int = Query(14, ge=1, le=60)):
    return {"reports": service.report_history(limit)}


@router.get("/scores")
def scores():
    return metrics.snapshot()


@router.get("/lessons")
def lessons():
    return {"lessons": service.active_lessons(limit=50)}


@router.get("/trends")
def trends():
    """Week-over-week GSC-delta's per project (site-trend + pagina-bewegers)."""
    from ..seo import history as history_service
    from ..seo import sites as sites_service
    out = []
    for s in sites_service.list_sites():
        if not (s.get("gsc_property") or "").strip():
            continue
        out.append({
            "site_id": s["id"],
            "name": s["name"],
            "trend": history_service.site_trend(s["id"]),
            "movers": history_service.page_movers(s["id"], limit=5),
        })
    return {"projects": out}


@router.get("/gsc-series/{site_id}")
def gsc_series(site_id: str, days: int = Query(28, ge=7, le=90)):
    """Dagreeks (clicks/impressies/CTR/positie) voor een trendgrafiek."""
    from ..seo import history as history_service
    return {"site_id": site_id, "series": history_service.site_series(site_id, days=days)}


@router.get("/predictions")
def predictions_view():
    """Iris' gesloten leer-lus: haar eigen trefkans + de openstaande
    voorspellingen die nog afgerekend worden."""
    from . import predictions
    return {"track_record": predictions.track_record(),
            "open": predictions.open_predictions()}


# ── Kennisbank: Vincent voedt Iris met onderzoek ────────────────────────────

class ManualNote(BaseModel):
    title: str = ""
    text: str


@router.get("/knowledge")
def knowledge_list():
    """Actieve kennisitems + het pad van de vault-map om onderzoek in te droppen."""
    from . import knowledge
    return {"folder": knowledge.ensure_folder(), "items": knowledge.list_knowledge()}


@router.post("/knowledge/sync")
async def knowledge_sync():
    """Scan de vault-map opnieuw en distilleer nieuwe/gewijzigde onderzoeksdocs."""
    from . import knowledge
    return await knowledge.sync_knowledge()


@router.post("/knowledge")
async def knowledge_add(body: ManualNote):
    """Voeg kennis direct toe (geplakt), zonder een vault-bestand aan te maken."""
    from . import knowledge
    kid = await knowledge.add_manual_note(body.title, body.text)
    if not kid:
        raise HTTPException(status_code=400, detail="Te weinig tekst om iets van te leren")
    return {"id": kid, "items": knowledge.list_knowledge()}


@router.delete("/knowledge/{kid}")
def knowledge_delete(kid: str):
    from . import knowledge
    if not knowledge.delete_knowledge(kid):
        raise HTTPException(status_code=404, detail="Kennisitem niet gevonden")
    return {"success": True}


@router.post("/run-now")
async def run_now():
    """Draai de dagbriefing direct (zelfde flow als de 06:45-job)."""
    return await service.run_morning_briefing()

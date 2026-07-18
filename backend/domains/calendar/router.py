"""
Google Agenda API-router voor Agent OS.

Endpoints:
  GET  /api/calendar/status      Config-status + verbonden calendar
  GET  /api/calendar/events      Week-events (weekStart=YYYY-MM-DD optioneel)
  POST /api/calendar/block       Tijd blokkeren {title, start, end, description?}
  GET  /api/calendar/today       Korte samenvatting voor Iris-briefing
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service as calendar_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class BlockRequest(BaseModel):
    title: str
    start: str  # ISO 8601
    end: str    # ISO 8601
    description: str = ""


class ProposalAction(BaseModel):
    proposal_id: int


@router.get("/proposals")
async def proposals():
    """Lijst open afspraak-voorstellen (uit mail gedetecteerd)."""
    from . import agent as agenda_agent
    return {"proposals": agenda_agent.pending_proposals()}


@router.post("/proposals/approve")
async def approve(body: ProposalAction):
    """Mens keurt goed → schrijf naar Google Agenda via block_time.

    Let op het onderscheid in de teruggeef-waarde:
      - ok=True            → 200, afspraak staat in de agenda.
      - ok=False, blocked  → 200 met ok:false + `error` (de deel-instructie).
                            Dit is GEEN serverfout: de agent weigert bewust te
                            boeken omdat het slot niet tegen de agenda's kon
                            worden getoetst (conflict_checked != 'ok'). We gooien
                            hier géén 502 — anders leest de SPA dat als "de server
                            is kapot" en verdwijnt de zorgvuldig geformuleerde
                            instructie in de console i.p.v. bij de gebruiker.
      - ok=False, booking_error → 502 (echt mislukt: Google-gave een fout).
    """
    from . import agent as agenda_agent
    res = agenda_agent.approve_proposal(body.proposal_id)
    if res.get("ok"):
        return res
    if res.get("code") == "booking_error":
        raise HTTPException(502, res.get("error", "boeken mislukt"))
    # Geblokkeerd / geweigerd: nette 200 zodat de client de instructie toont.
    return res


@router.post("/proposals/reject")
async def reject(body: ProposalAction):
    """Mens wijst af (blijft gesloten)."""
    from . import agent as agenda_agent
    agenda_agent.reject_proposal(body.proposal_id)
    return {"ok": True}


@router.get("/status")
async def status():
    # `configured` = credentials aanwezig; `reachable` = de agenda is ook echt
    # te lezen. Alleen dat tweede zegt of conflict-detectie iets waard is.
    access = await calendar_service.verify_access()
    return {
        "configured": calendar_service.is_configured(),
        "calendar_id": access["calendar_id"],
        "busy_calendar_ids": access["busy_calendar_ids"],
        "reachable": access["reachable"],
        "error": access["error"],
        "sub": bool(__import__("os").environ.get("CALENDAR_SUB")),
    }


@router.get("/events")
async def events(week_start: Optional[str] = None):
    if not calendar_service.is_configured():
        raise HTTPException(503, "Google Agenda niet geconfigureerd (CALENDAR_SERVICE_ACCOUNT_PATH)")
    try:
        items = await calendar_service.get_week_events(week_start=week_start)
        return {"events": items, "week_start": week_start}
    except Exception as e:
        log.exception("Calendar events ophalen mislukt")
        raise HTTPException(502, f"Google Agenda-fout: {e}")


@router.post("/block")
async def block(req: BlockRequest):
    if not calendar_service.is_configured():
        raise HTTPException(503, "Google Agenda niet geconfigureerd")
    try:
        start = datetime.fromisoformat(req.start)
        end = datetime.fromisoformat(req.end)
    except ValueError:
        raise HTTPException(400, "start/end moeten geldige ISO-timestamps zijn")
    try:
        result = await calendar_service.block_time(req.title, start, end, req.description)
        return result
    except Exception as e:
        log.exception("Tijd blokkeren mislukt")
        raise HTTPException(502, f"Google Agenda-fout: {e}")


@router.get("/today")
async def today():
    if not calendar_service.is_configured():
        return {"configured": False, "summary": ""}
    try:
        summary = await calendar_service.get_today_summary()
        return {"configured": True, "summary": summary}
    except Exception as e:
        log.exception("Today-summary mislukt")
        return {"configured": True, "summary": "", "error": str(e)}

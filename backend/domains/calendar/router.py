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


@router.get("/status")
async def status():
    return {
        "configured": calendar_service.is_configured(),
        "calendar_id": calendar_service._cal_id() if calendar_service.is_configured() else None,
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

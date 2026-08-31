"""
Google Agenda API-router voor Impact OS.

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
from ...shared.config import CALENDAR_BACKEND
from ...shared.settings_store import get_setting, set_setting

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class BlockRequest(BaseModel):
    title: str
    start: str  # ISO 8601
    end: str    # ISO 8601
    description: str = ""


class ProposalAction(BaseModel):
    proposal_id: int


class CalendarSettings(BaseModel):
    calendar_id: Optional[str] = None
    busy_calendar_ids: Optional[str] = None  # komma-gescheiden


class AttendeeInfoRequest(BaseModel):
    name: str
    email: str = ""
    event_title: str = ""
    event_description: str = ""
    force: bool = False


class CommandRequest(BaseModel):
    text: str


@router.post("/command")
async def command(body: CommandRequest):
    """Vrije tekst -> agenda-voorstel (review-gate), voor de 'snel iets
    toevoegen'-invoer in de Agenda-tab. Zelfde functie als de WhatsApp-
    bridge/klant-Iris gebruiken (calendar/agent.py:propose_from_text) — één
    parse-conflict-boek-keten voor alle drie de ingangen."""
    from . import agent as agenda_agent
    ok, message, proposal_id = await agenda_agent.propose_from_text(body.text)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "proposal_id": proposal_id}


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
        await agenda_agent.notify_customer_outcome(body.proposal_id, "booked")
        return res
    if res.get("code") == "booking_error":
        raise HTTPException(502, res.get("error", "boeken mislukt"))
    # Geblokkeerd / geweigerd: nette 200 zodat de client de instructie toont.
    return res


@router.post("/proposals/propose-alternative")
async def propose_alternative(body: ProposalAction):
    """Mens klikt "Stuur alternatief voorstel" op een conflict-kaart: Iris'
    gevonden vrije slot gaat per mail naar de afzender, het originele
    voorstel sluit als 'rejected' (achterhaald door de nieuwe zet)."""
    from . import agent as agenda_agent
    res = await agenda_agent.propose_alternative_by_mail(body.proposal_id)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "kon geen alternatief voorstellen"))
    return res


@router.post("/proposals/reject")
async def reject(body: ProposalAction):
    """Mens wijst af (blijft gesloten)."""
    from . import agent as agenda_agent
    agenda_agent.reject_proposal(body.proposal_id)
    await agenda_agent.notify_customer_outcome(body.proposal_id, "rejected")
    return {"ok": True}


@router.get("/status")
async def status():
    # `configured` = credentials aanwezig; `reachable` = de agenda is ook echt
    # te lezen. Alleen dat tweede zegt of conflict-detectie iets waard is.
    access = await calendar_service.verify_access()
    return {
        "backend": CALENDAR_BACKEND,
        "configured": calendar_service.is_configured(),
        "calendar_id": access["calendar_id"],
        "busy_calendar_ids": access["busy_calendar_ids"],
        "reachable": access["reachable"],
        "error": access["error"],
        "sub": bool(__import__("os").environ.get("CALENDAR_SUB")),
        # Adres om de agenda mee te delen (alleen bij de Google-serviceaccount-
        # backend — leeg bij Outlook, daar is de koppeling de eigen OAuth-login).
        "client_email": calendar_service.client_email(),
    }


@router.put("/settings")
async def update_settings(body: CalendarSettings):
    """Agenda-ID/busy-agenda's zelf koppelen vanuit de Instellingen-hub —
    werkt direct (DB-override, zie shared/settings_store.py), geen herstart
    nodig. Alleen zinvol bij de Google-serviceaccount-backend; bij Outlook
    volgt de agenda dezelfde koppeling als mail."""
    if body.calendar_id is not None:
        set_setting("calendar_calendar_id", body.calendar_id.strip())
    if body.busy_calendar_ids is not None:
        set_setting("calendar_busy_ids", body.busy_calendar_ids.strip())
    access = await calendar_service.verify_access()
    return {
        "configured": calendar_service.is_configured(),
        "calendar_id": access["calendar_id"],
        "busy_calendar_ids": access["busy_calendar_ids"],
        "reachable": access["reachable"],
        "error": access["error"],
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


@router.post("/attendee-info")
async def attendee_info(body: AttendeeInfoRequest):
    """Korte briefing over een deelnemer (websearch + LLM), voor de info-knop
    naast een naam op de Agenda-tab. Luid falen i.p.v. een lege briefing —
    de knop toont dan de echte reden (zoekproviders uitgeput, LLM onbereikbaar)."""
    from . import attendee_info as info_service
    try:
        return await info_service.build_briefing(
            body.name, body.email, body.event_title, body.event_description,
            force=body.force,
        )
    except Exception as e:
        log.warning("Attendee-info mislukt voor %s: %s", body.name, e)
        raise HTTPException(502, str(e))


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

"""Meeting-notulen-API.

Endpoints:
  POST /api/notes         plak een transcript -> samenvatting + actiepunten als CRM-taken
  GET  /api/notes         lijst (optioneel ?status=nieuw|samengevat|mislukt)
  GET  /api/notes/{id}    detail
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteBody(BaseModel):
    title: str
    transcript: str
    company_id: str = ""
    deal_id: str = ""
    meeting_date: str = ""


@router.post("", status_code=201)
async def api_create_note(body: NoteBody):
    try:
        return await service.maak_notitie(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def api_list_notes(status: str = ""):
    return service.list_notes(status)


@router.get("/{note_id}")
def api_get_note(note_id: str):
    row = service.get_note(note_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notitie niet gevonden")
    return row

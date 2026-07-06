"""
Mission Control journey-API (read-only).

  GET /api/journeys?session_id=...   — lijst van runs voor een sessie
  GET /api/journeys/{journey_id}     — één run met alle stappen op volgorde
"""
from fastapi import APIRouter, HTTPException, Query

from ...domains.chat import journey as journey_service

router = APIRouter(prefix="/api/journeys", tags=["journeys"])


@router.get("")
def list_journeys(session_id: str = Query(...)):
    return journey_service.list_journeys(session_id)


@router.get("/{journey_id}")
def get_journey(journey_id: str):
    journey = journey_service.get_journey(journey_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey niet gevonden")
    return journey

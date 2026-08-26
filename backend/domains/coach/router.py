"""API voor De Sparringpartner — achter de gewone ImpactOS-sessiegate (Vincent
is al ingelogd in de Control Room), geen apart token nodig: dit draait
allemaal in hetzelfde proces als de rituelen die het leest."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/coach", tags=["coach"])


class EnergyEntry(BaseModel):
    activity: str
    category: str = ""
    direction: str  # 'gain' | 'cost'


class EnergyLogPayload(BaseModel):
    date: str
    entries: List[EnergyEntry]


@router.post("/analyse")
async def analyse() -> Dict[str, Any]:
    result = await service.run_analysis()
    return result


@router.get("/lessons")
def lessons() -> Dict[str, Any]:
    return {"lessons": service.list_lessons()}


@router.post("/energy-log")
def add_energy_log(payload: EnergyLogPayload) -> Dict[str, Any]:
    created = service.save_energy_log(payload.date, [e.model_dump() for e in payload.entries])
    return {"ok": True, "created": created}


@router.get("/energy-log")
def get_energy_log(days: int = 30) -> Dict[str, Any]:
    return {"entries": service.list_energy_log(days=max(7, min(90, days)))}


@router.post("/whatsapp-check")
async def whatsapp_check() -> Dict[str, Any]:
    """Handmatige trigger voor de proactieve signaalcheck — zelfde functie als
    de 2-uurlijkse scheduler-job, maar op verzoek (bv. om te testen)."""
    sent = await service.check_and_send_whatsapp()
    return {"sent": sent}

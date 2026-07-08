"""Strategist & Control Room — API endpoints."""
import logging
from typing import Any, Dict

from fastapi import APIRouter

from . import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategist", tags=["strategist"])


@router.get("/control-room")
def control_room() -> Dict[str, Any]:
    """Centraal dashboard — status van alle projecten, doelen, systemen."""
    try:
        return service.control_room_status()
    except Exception as e:
        logger.exception("Control Room fout")
        return {"error": str(e)[:300]}


@router.post("/analyse")
async def strategist_analyse() -> Dict[str, Any]:
    """Strategist Agent — AI-analyse van de volledige status."""
    return await service.strategist_analyse()


@router.post("/execute")
async def strategist_execute(body: Dict[str, str]) -> Dict[str, Any]:
    """Voer de Strategist-prioriteiten uit — maak doelen/taken aan."""
    analysis = body.get("analysis", "")
    if not analysis:
        return {"error": "Geen analyse tekst ontvangen", "actions": []}
    return await service.strategist_execute(analysis)


@router.get("/health")
def strategist_health(project: str = None) -> Dict[str, Any]:
    """Compact systeemgezondheid-overzicht: verweesde/mislukte doelen,
    scheduler-fouten, laatste autoheal-run. Met `project` beperk je de
    publish-status tot één project (zodat een Bijeen-fout niet op het
    Bewaardvoorjou-dashboard verschijnt)."""
    try:
        return service.system_health(project=project)
    except Exception as e:
        logger.exception("Health-check fout")
        return {"error": str(e)[:300]}


# Per-project health — het project-dashboard roept dit aan i.p.v. de globale
# check, zodat de Aandachtspunten alleen meldingen voor dát project tonen.


@router.get("/projects/{name}/health")
def strategist_project_health(name: str) -> Dict[str, Any]:
    """Systeemgezondheid gefilterd op één project."""
    try:
        return service.system_health(project=name)
    except Exception as e:
        logger.exception("Project health-check fout")
        return {"error": str(e)[:300]}


@router.post("/autoheal")
async def strategist_autoheal() -> Dict[str, Any]:
    """Deterministische zelf-reparatie: ruimt duplicate/artefact draft-doelen
    op en hervat verweesde 'running'-doelen. Geen LLM, direct uitgevoerd.

    Async omdat het herstarten van een 'running'-doel een asyncio-achtergrond-
    taak aanmaakt — dat vereist een actieve event loop op dit thread, wat een
    synchrone route (die FastAPI in een threadpool draait) niet heeft."""
    try:
        return service.autoheal_goals()
    except Exception as e:
        logger.exception("Autoheal fout")
        return {"error": str(e)[:300]}

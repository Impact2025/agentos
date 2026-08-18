"""GEO-API — Generative Engine Optimization endpoints.

  GET  /api/geo/scan/{site_id}        — voer een deterministische GEO-scan uit
  GET  /api/geo/latest/{site_id}      — laatste scan-resultaat
  GET  /api/geo/personas/{site_id}    — lijst ICP-persona's
  POST /api/geo/personas              — voeg/werk een ICP-persona bij
  GET  /api/geo/summary               — GEO-score per site (dashboard + Iris)
  POST /api/geo/entity-block          — genereer entity-block + negations
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/geo", tags=["geo"])


class PersonaRequest(BaseModel):
    site_id: str
    name: str
    description: str = ""
    pain_points: str = ""
    queries: Optional[List[str]] = None


class EntityRequest(BaseModel):
    site_name: str
    what_it_is: str
    what_it_is_not: List[str]


@router.get("/scan/{site_id}")
def api_scan(site_id: str):
    try:
        return service.scan_site(site_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/latest/{site_id}")
def api_latest(site_id: str):
    s = service.get_latest_scan(site_id)
    if not s:
        raise HTTPException(404, "nog geen scan — draai eerst /scan/{site_id}")
    return s


@router.get("/personas/{site_id}")
def api_personas(site_id: str):
    return {"personas": service.list_personas(site_id)}


@router.post("/personas")
def api_persona_save(req: PersonaRequest):
    pid = service.upsert_persona(
        req.site_id, req.name, req.description, req.pain_points, req.queries
    )
    return {"id": pid, "ok": True}


@router.get("/summary")
def api_summary():
    return {"sites": service.all_sites_summary()}


@router.post("/entity-block")
def api_entity_block(req: EntityRequest):
    return {
        "block": service.generate_entity_block(
            req.site_name, req.what_it_is, req.what_it_is_not
        )
    }


@router.post("/citation-check")
def api_citation_check():
    """Draai de wekelijkse citatie-check (het echte GEO-KPI: wordt je merk
    genoemd als bron in ChatGPT/Perplexity?). Kan lang duren — meerdere LLM-calls."""
    from . import citation as citation_service
    citation_service.ensure_schema()
    return citation_service.run_citation_check()


@router.get("/citation/latest")
def api_citation_latest():
    from . import citation as citation_service
    return citation_service.latest_week_summary()

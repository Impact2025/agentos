"""
Demand Engine-API — scan een site op zoekwoordkansen en beheer de kansen-lijst.

  POST /api/demand/scan          — draai een GSC-scan voor een site (achtergrond)
  GET  /api/demand/opportunities — lijst kansen (filter op site_id / status)
  PATCH /api/demand/opportunities/{id} — status bijwerken
  GET  /api/demand/status        — configuratiestatus
"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from . import engine as demand_engine
from . import sites as sites_service
from .gsc import is_configured as gsc_ok, submit_sitemap as gsc_submit_sitemap
from ...shared.config import GSC_SERVICE_ACCOUNT_PATH

router = APIRouter(prefix="/api/demand", tags=["demand"])


class ScanRequest(BaseModel):
    site_id: str
    days: int = 90
    min_impressions: int = demand_engine.DEFAULT_MIN_IMPRESSIONS
    limit: int = demand_engine.DEFAULT_LIMIT


class OpportunityUpdate(BaseModel):
    status: str


class OpportunityCreate(BaseModel):
    site_id: str
    query: str
    angle: str
    rationale: str
    action: str = "nieuwe-content"
    opportunity_score: float = 100.0


class SitemapSubmitRequest(BaseModel):
    site_url: str
    sitemap_url: str


def _run_scan(site: dict, days: int, min_impressions: int, limit: int) -> None:
    try:
        result = demand_engine.scan_site(
            site, days=days, min_impressions=min_impressions, limit=limit
        )
        print(f"[demand] Scan '{site['name']}': {result['new']} nieuwe kansen "
              f"(van {result['found']} gevonden, {result['analysed']} zoekwoorden geanalyseerd)")
    except Exception as e:  # noqa: BLE001
        print(f"[demand] Scan mislukt voor '{site.get('name')}': {e}")


@router.post("/scan")
def scan(body: ScanRequest, background_tasks: BackgroundTasks):
    if not gsc_ok():
        raise HTTPException(
            status_code=503,
            detail="Search Console niet geconfigureerd — stel GSC_SERVICE_ACCOUNT_PATH "
                   "(of GA_SERVICE_ACCOUNT_PATH) in .env in.",
        )
    site = sites_service.get_site(body.site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site niet gevonden")
    if not (site.get("gsc_property") or "").strip():
        raise HTTPException(status_code=400, detail="Site heeft geen gsc_property ingesteld")

    background_tasks.add_task(_run_scan, site, body.days, body.min_impressions, body.limit)
    return {"status": "gestart", "site": site["name"],
            "message": "Demand-Engine-scan draait op de achtergrond"}


@router.get("/opportunities")
def opportunities(site_id: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    return demand_engine.list_opportunities(site_id=site_id, status=status)


@router.post("/opportunities")
def create_opportunity(body: OpportunityCreate):
    site = sites_service.get_site(body.site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site niet gevonden")
    return demand_engine.create_manual_opportunity(
        site_id=body.site_id, query=body.query, angle=body.angle,
        rationale=body.rationale, action=body.action,
        opportunity_score=body.opportunity_score,
    )


@router.patch("/opportunities/{opp_id}")
def update_opportunity(opp_id: str, body: OpportunityUpdate):
    try:
        updated = demand_engine.update_opportunity_status(opp_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Kans niet gevonden")
    return updated


@router.post("/submit-sitemap")
def submit_sitemap_endpoint(body: SitemapSubmitRequest):
    if not gsc_ok():
        raise HTTPException(status_code=503, detail="Search Console niet geconfigureerd")
    ok, detail = gsc_submit_sitemap(body.site_url, body.sitemap_url)
    if ok:
        return {"status": "ingediend", "site": body.site_url, "sitemap": body.sitemap_url}
    return {"status": "fout", "detail": detail or "Sitemap indienen mislukt — controleer of de site_url klopt en het service-account toegang heeft"}


@router.get("/status")
def status():
    return {
        "gsc_configured": gsc_ok(),
        "service_account_set": bool(GSC_SERVICE_ACCOUNT_PATH),
        "site_count": len(sites_service.list_sites()),
    }

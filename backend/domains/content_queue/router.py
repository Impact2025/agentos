"""
Content-wachtrij API — 2x/week auto-gegenereerde blog + social-copy, wacht op
menselijke goedkeuring voordat er iets live/gepost wordt.

  GET  /api/content-queue                  → lijst (filter op site_id/status)
  GET  /api/content-queue/{id}             → één job (incl. blog_html + social_copy)
  POST /api/content-queue/{id}/approve     → publiceer + post naar alle geconfigureerde platformen
  POST /api/content-queue/{id}/reject      → afwijzen, geen actie
  POST /api/content-queue/{id}/regenerate  → herschrijf hetzelfde onderwerp opnieuw
  POST /api/content-queue/run-now          → (handmatig) draai de auto-content-generatie nu voor 1 site
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..publish import content_pipeline
from ..seo import sites as sites_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/content-queue", tags=["content-queue"])


def _with_parsed_social_copy(job: dict) -> dict:
    job = dict(job)
    try:
        job["social_copy"] = json.loads(job.get("social_copy") or "{}")
    except Exception:
        job["social_copy"] = {}
    try:
        job["publish_result"] = json.loads(job.get("publish_result") or "{}")
    except Exception:
        job["publish_result"] = {}
    try:
        job["qc_report"] = json.loads(job.get("qc_report") or "{}")
    except Exception:
        job["qc_report"] = {}
    return job


@router.get("")
def list_content_jobs(site_id: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    jobs = content_pipeline.list_jobs(site_id=site_id, status=status)
    return [_with_parsed_social_copy(j) for j in jobs]


@router.get("/{job_id}")
def get_content_job(job_id: str):
    job = content_pipeline.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Content-job niet gevonden")
    return _with_parsed_social_copy(job)


@router.post("/{job_id}/approve")
async def approve_content_job(job_id: str):
    try:
        result = await content_pipeline.approve_and_publish(job_id)
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Goedkeuren/publiceren mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/{job_id}/reject")
def reject_content_job(job_id: str):
    try:
        content_pipeline.reject_job(job_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/{job_id}/regenerate")
async def regenerate_content_job(job_id: str):
    try:
        new_id = await content_pipeline.regenerate_job(job_id)
        return _with_parsed_social_copy(content_pipeline.get_job(new_id))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Regenereren mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/run-now")
async def run_now(site_id: str = Query(...), count: Optional[int] = Query(None, ge=1, le=10)):
    """Handmatig een content-batch draaien voor 1 site (buiten het 2x/week-schema
    om). `count` overschrijft de site-instelling content_batch_size."""
    site = sites_service.get_site(site_id)
    if not site:
        raise HTTPException(404, detail="Site niet gevonden")
    try:
        job_ids = await content_pipeline.run_content_batch(site, count=count)
        if not job_ids:
            return {"success": False, "detail": "Geen nieuwe kansen — voer eerst een Demand Engine-scan uit."}
        return {"success": True, "job_ids": job_ids, "job_id": job_ids[0]}
    except Exception as e:
        logger.exception("run-now mislukt voor site %s", site_id)
        raise HTTPException(500, detail=str(e)[:300])

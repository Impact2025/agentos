"""Iris Orchestrator — REST-endpoints voor de meta-agent."""
from __future__ import annotations

from fastapi import APIRouter, Body
from typing import Dict, Optional

from . import service as orchestrator_service

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/under-threshold")
def list_under_threshold(threshold: int = 80):
    """Welke content_queue-stukken staan onder de grens en zijn verbeterbaar?"""
    jobs = orchestrator_service._find_under_threshold_jobs(threshold)
    return {
        "count": len(jobs),
        "jobs": [
            {
                "id": j.get("id"),
                "title": j.get("title"),
                "project": orchestrator_service._project_for_job(j),
                "seo_score": j.get("seo_score"),
                "status": j.get("status"),
            }
            for j in jobs
        ],
    }


@router.post("/process-one")
async def process_one(body: Optional[Dict] = Body(None)):
    """Verwerk ÉÉN stuk onder de grens via de Gauntlet Loop.

    Optioneel: {"threshold": 80}. Roept de Gauntlet-service direct aan (in-process,
    geen auth nodig). Iris kent de toolbox: content_queue → Gauntlet → publish-gate.
    """
    threshold = (body or {}).get("threshold", 80)
    result = await orchestrator_service.process_one_under_threshold(threshold=threshold)
    return result

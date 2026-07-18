"""Researcher API-router — de NotebookLM-onderzoek-agent.

Endpoints:
  GET    /api/researcher/jobs          Onderzoeksjobs (filter: project, status)
  POST   /api/researcher/run           Eén onderzoeksvraag uitvoeren (achtergrond)
  GET    /api/researcher/jobs/{id}      Één job met rapport + citations
  POST   /api/researcher/push-signal   Radar-signaal -> NotebookLM-bron
"""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from .service import get_service

router = APIRouter(prefix="/api/researcher", tags=["researcher"])


@router.get("/jobs")
def list_jobs(project: Optional[str] = Query(None),
             status: Optional[str] = Query(None)):
    return get_service().list_jobs(project=project, status=status)


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = get_service().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job niet gevonden")
    return job


@router.post("/run")
async def run_research(background: BackgroundTasks,
                      project: str = "",
                      question: str = "",
                      notebook_id: str = ""):
    if not question.strip():
        raise HTTPException(status_code=422, detail="question is verplicht")
    svc = get_service()

    async def _bg():
        try:
            await svc.run_research(project, question,
                                   notebook_id=notebook_id or None)
        except Exception as e:
            # De service schrijft de error al naar de job-row; log hier
            # alleen ter diagnostics.
            from ...shared import outcomes
            outcomes.log_outcome(
                project=project or "Researcher",
                action="notebooklm_research",
                detail=f"Onderzoek mislukt: {e}",
                next_step="Controleer of notebooklm-mcp is ingelogd (re_auth).",
                status="error",
            )

    background.add_task(lambda: __import__("asyncio").run(_bg()))
    return {"accepted": True, "question": question,
            "notebook": notebook_id or "(standaard)"}


@router.post("/ground")
async def ground_opportunities(background: BackgroundTasks,
                              site_id: str = "",
                              count: int = 3):
    """Grond de open Demand Engine-kansen van een site in NotebookLM
    (achtergrond). Zelfde brug als de wekelijkse demand-scan gebruikt,
    maar handmatig te triggeren."""
    if not site_id.strip():
        raise HTTPException(status_code=422, detail="site_id is verplicht")
    from ...domains.seo import sites as sites_service
    site = sites_service.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site niet gevonden")
    svc = get_service()

    async def _bg():
        try:
            n = await svc.ground_new_opportunities(site, max_questions=max(1, min(5, count)))
            from ...shared import outcomes
            outcomes.log_outcome(
                project=site.get("name") or "Researcher",
                action="notebooklm_grounding",
                detail=f"{n} kans(en) gegrond in NotebookLM-onderzoek.",
                artifact="/api/researcher/jobs",
                next_step="Niets — de contentmotor gebruikt de rapporten automatisch.",
            )
        except Exception as e:
            from ...shared import outcomes
            outcomes.log_outcome(
                project=site.get("name") or "Researcher",
                action="notebooklm_grounding",
                detail=f"Gronden mislukt: {e}",
                next_step="Controleer of notebooklm-mcp is ingelogd (re_auth).",
                status="error",
            )

    background.add_task(lambda: __import__("asyncio").run(_bg()))
    return {"accepted": True, "site": site.get("name"), "count": count}


@router.post("/push-signal")
async def push_signal(signal_id: str, notebook_id: str = ""):
    """Duw een Radar-signaal als bron naar NotebookLM (achtergrond)."""
    from ...domains.radar.service import get_service as radar_svc
    sig = radar_svc().get_signal(signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Signaal niet gevonden")
    svc = get_service()

    async def _bg():
        try:
            await svc.push_signal_as_source(sig,
                                            notebook_id=notebook_id or None)
        except Exception as e:
            from ...shared import outcomes
            outcomes.log_outcome(
                project=sig.get("project", "Researcher"),
                action="notebooklm_push_source",
                detail=f"Bron-push mislukt: {e}",
                next_step="Controleer notebooklm-mcp auth.",
                status="error",
            )

    background.add_task(lambda: __import__("asyncio").run(_bg()))
    return {"accepted": True, "signal_id": signal_id}

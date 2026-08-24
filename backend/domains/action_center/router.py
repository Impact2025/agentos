"""Actiecentrum API — de inbox van alles wat op Vincent wacht.

  GET  /api/action-center           → inbox (items + tellingen)
  POST /api/action-center/dismiss   → item verbergen (kind + ref_id)
  POST /api/action-center/content/reset-stuck → stuck content-job resetten
  GET  /api/action-center/feed      → uitkomst-feed (wat gedaan → waar → wat nu)
"""
from fastapi import APIRouter, Query, Body
from pydantic import BaseModel
from typing import Optional

from . import service

router = APIRouter(prefix="/api/action-center", tags=["action-center"])


class DismissBody(BaseModel):
    kind: str
    ref_id: str


@router.get("")
def inbox(project: Optional[str] = Query(None, description="Vault-projectnaam — filtert de inbox op één project (incl. diens site-content).")):
    return service.build_inbox(project=project)


@router.get("/by-project")
def by_project():
    """Telling per project van open actie-items (voor Control-Room-badges)."""
    return service.inbox_counts_by_project()


@router.post("/dismiss")
def dismiss(body: DismissBody):
    service.dismiss(body.kind, body.ref_id)
    return {"success": True}


@router.post("/content/reset-stuck/{job_id}")
def reset_stuck(job_id: str):
    """Reset een vastgelopen content-job: pogingentellers → 0, status → needs_work.

    Handmatige overbruggingsactie voor artikelen waar de content_improver EN
    de Orchestrator beiden hun max_attempts bereikten zonder de grens te halen.
    Zonder deze knop zit zo'n artikel permanent in 'stuck' en moet Vincent het
    handmatig in de shell herstellen.
    """
    from ..publish import content_pipeline as cp
    result = cp.reset_stuck_attempts(job_id)
    if result.get("ok"):
        # Log outcome zodat het terugkomt in de activiteiten-feed
        from ...shared.outcomes import log_outcome
        log_outcome("content", "reset_stuck",
                    f"{result['old_title'] or job_id[:12]}: pogingen terug op nul, "
                    f"status needs_work (was {result['old_status']}).",
                    artifact="/wachtrij", next_step="Content-verbeteraar of Orchestrator pakt hem op.")
    return result


@router.get("/feed")
def feed(limit: int = Query(25, ge=1, le=100)):
    return service.outcome_feed(limit)


@router.get("/llm-usage")
def llm_usage(days: int = Query(7, ge=1, le=31)):
    """Live LLM-verbruik (OpenModel-credits): vandaag per route/model + dagreeks."""
    from ...shared.outcomes import llm_usage_summary
    return llm_usage_summary(days)


@router.get("/digest")
def digest():
    """Het ochtendrapport, on demand (zelfde inhoud als de 07:00-mail)."""
    from . import digest as digest_service
    return digest_service.build_digest()


@router.get("/pulse")
async def pulse(project: Optional[str] = Query(None, description="Vault-projectnaam — filtert content/activiteit/hero-cijfers op één project.")):
    """Iris Pulse: mail, agenda, content, leads en traffic in één samenvatting
    voor de Control-Room-hero — wat deed Iris deze week, en werkt het."""
    from . import pulse as pulse_service
    return await pulse_service.build_home_pulse(project=project)

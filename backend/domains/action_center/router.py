"""Actiecentrum API — de inbox van alles wat op Vincent wacht.

  GET  /api/action-center           → inbox (items + tellingen)
  POST /api/action-center/dismiss   → item verbergen (kind + ref_id)
  GET  /api/action-center/feed      → uitkomst-feed (wat gedaan → waar → wat nu)
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/action-center", tags=["action-center"])


class DismissBody(BaseModel):
    kind: str
    ref_id: str


@router.get("")
def inbox():
    return service.build_inbox()


@router.post("/dismiss")
def dismiss(body: DismissBody):
    service.dismiss(body.kind, body.ref_id)
    return {"success": True}


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

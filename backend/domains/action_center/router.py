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

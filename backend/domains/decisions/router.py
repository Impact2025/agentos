from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class DecisionCreate(BaseModel):
    project: str
    title: str
    context: str = ""
    options: List[str] = []
    deadline: str = ""


class DecisionResolve(BaseModel):
    decision: str
    reasoning: str = ""


@router.get("")
def list_decisions(project: str, status: Optional[str] = None) -> Dict[str, Any]:
    return {"decisions": service.list_decisions(project, status)}


@router.post("")
def add_decision(payload: DecisionCreate) -> Dict[str, Any]:
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Titel mag niet leeg zijn")
    return service.add_decision(payload.project, payload.title.strip(), payload.context,
                                 payload.options, payload.deadline)


@router.post("/{decision_id}/resolve")
def resolve_decision(decision_id: int, payload: DecisionResolve) -> Dict[str, Any]:
    if not payload.decision.strip():
        raise HTTPException(status_code=400, detail="Vul in wat er besloten is")
    result = service.resolve_decision(decision_id, payload.decision.strip(), payload.reasoning)
    if not result:
        raise HTTPException(status_code=404, detail="Besluit niet gevonden")
    return result


@router.post("/{decision_id}/reopen")
def reopen_decision(decision_id: int) -> Dict[str, Any]:
    result = service.reopen_decision(decision_id)
    if not result:
        raise HTTPException(status_code=404, detail="Besluit niet gevonden")
    return result


@router.delete("/{decision_id}")
def delete_decision(decision_id: int) -> Dict[str, Any]:
    service.delete_decision(decision_id)
    return {"ok": True}

"""
Opdrachten API router — zoeken naar interim-vacatures + CRUD.

Endpoints:
  POST   /api/vacancies/search   SSE-zoekactie over rol x bron (LinkedIn/Freelance.nl/Indeed/BMC/overig)
  GET    /api/vacancies          Lijst (filter: status, min_score), gesorteerd op fit_score
  PATCH  /api/vacancies/{id}     Status updaten (new/interesting/rejected/applied)
  DELETE /api/vacancies/{id}     Verwijderen
  GET    /api/vacancies/stats    Statistieken
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .service import get_service, DEFAULT_ROLES
from ...shared.models import VacancyUpdate

router = APIRouter(prefix="/api/vacancies", tags=["vacancies"])


class VacancySearchRequest(BaseModel):
    roles: List[str] = DEFAULT_ROLES
    max_per_source: int = 3


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/search")
async def search_vacancies(body: VacancySearchRequest):
    svc = get_service()

    async def generate():
        async for ev in svc.run_scan(roles=body.roles, max_per_source=body.max_per_source):
            yield _sse(ev)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("")
def list_vacancies(
    status: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
):
    return get_service().list_vacancies(status=status, min_score=min_score)


@router.get("/stats")
def get_stats():
    return get_service().get_stats()


@router.patch("/{vacancy_id}")
def update_vacancy(vacancy_id: str, body: VacancyUpdate):
    updated = get_service().update_status(vacancy_id, body.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Vacature niet gevonden")
    return updated


@router.delete("/{vacancy_id}", status_code=204)
def delete_vacancy(vacancy_id: str):
    if not get_service().delete_vacancy(vacancy_id):
        raise HTTPException(status_code=404, detail="Vacature niet gevonden")

"""CRM-API — bedrijven, contacten, deals, activiteiten, taken.

Endpoints:
  GET  /api/crm/companies              lijst (optioneel ?q=zoekterm)
  POST /api/crm/companies               nieuw bedrijf
  GET  /api/crm/companies/{id}          detail
  PATCH /api/crm/companies/{id}         bijwerken
  GET  /api/crm/companies/{id}/contacts contacten van dit bedrijf
  POST /api/crm/contacts                nieuw contact
  GET  /api/crm/deals                   pipeline (optioneel ?stage=&company_id=)
  POST /api/crm/deals                   nieuwe deal
  PATCH /api/crm/deals/{id}/stage       stage wijzigen
  GET  /api/crm/pipeline                pipeline-samenvatting (per stage)
  GET  /api/crm/activities              tijdlijn (optioneel ?company_id=&deal_id=)
  POST /api/crm/activities              activiteit loggen
  GET  /api/crm/tasks                   taken (optioneel ?status=open|done|'')
  POST /api/crm/tasks                   nieuwe taak
  POST /api/crm/tasks/{id}/complete     taak afvinken
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/crm", tags=["crm"])


class CompanyBody(BaseModel):
    name: str
    website: str = ""
    industry: str = ""
    city: str = ""
    phone: str = ""
    email: str = ""
    kvk_number: str = ""
    notes: str = ""


class ContactBody(BaseModel):
    first_name: str
    company_id: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    job_title: str = ""
    is_primary: bool = False
    notes: str = ""


class DealBody(BaseModel):
    company_id: str
    title: str
    contact_id: str = ""
    value_cents: int = 0
    stage: str = "gesprek"
    probability: int = 20
    expected_close_date: str = ""
    description: str = ""
    source: str = ""


class StageBody(BaseModel):
    stage: str


class ActivityBody(BaseModel):
    company_id: str = ""
    contact_id: str = ""
    deal_id: str = ""
    type: str
    subject: str
    description: str = ""


class TaskBody(BaseModel):
    title: str
    company_id: str = ""
    contact_id: str = ""
    deal_id: str = ""
    description: str = ""
    priority: str = "normal"
    due_date: str = ""


@router.get("/companies")
def api_list_companies(q: str = ""):
    return service.list_companies(q)


@router.post("/companies", status_code=201)
def api_create_company(body: CompanyBody):
    try:
        return service.create_company(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/companies/{company_id}")
def api_get_company(company_id: str):
    row = service.get_company(company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    return row


@router.patch("/companies/{company_id}")
def api_update_company(company_id: str, body: dict):
    row = service.update_company(company_id, **body)
    if not row:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    return row


@router.get("/companies/{company_id}/contacts")
def api_company_contacts(company_id: str):
    return service.list_contacts(company_id)


@router.post("/contacts", status_code=201)
def api_create_contact(body: ContactBody):
    try:
        return service.create_contact(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/deals")
def api_list_deals(stage: str = "", company_id: str = ""):
    return service.list_deals(stage=stage, company_id=company_id)


@router.post("/deals", status_code=201)
def api_create_deal(body: DealBody):
    try:
        d = body.model_dump()
        title = d.pop("title")
        company_id = d.pop("company_id")
        return service.create_deal(company_id, title, **d)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/deals/{deal_id}/stage")
def api_update_deal_stage(deal_id: str, body: StageBody):
    try:
        row = service.update_deal_stage(deal_id, body.stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not row:
        raise HTTPException(status_code=404, detail="Deal niet gevonden")
    return row


@router.get("/pipeline")
def api_pipeline():
    return service.pipeline_summary()


@router.get("/activities")
def api_list_activities(company_id: str = "", deal_id: str = "", limit: int = 50):
    return service.list_activities(company_id=company_id, deal_id=deal_id, limit=limit)


@router.post("/activities", status_code=201)
def api_create_activity(body: ActivityBody):
    d = body.model_dump()
    type_ = d.pop("type")
    return service.log_activity(type_=type_, **d)


@router.get("/tasks")
def api_list_tasks(status: Optional[str] = "open"):
    return service.list_tasks(status=status or "")


@router.get("/tasks/overdue")
def api_overdue_tasks():
    return service.overdue_tasks()


@router.post("/tasks", status_code=201)
def api_create_task(body: TaskBody):
    try:
        return service.create_task(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/complete")
def api_complete_task(task_id: str):
    row = service.complete_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Taak niet gevonden")
    return row

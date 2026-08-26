"""Offertes-API.

Endpoints:
  POST /api/quotes                 nieuwe conceptofferte
  GET  /api/quotes                 lijst (optioneel ?status=)
  GET  /api/quotes/{id}            detail (incl. berekende totalen)
  GET  /api/quotes/{id}/html       downloadbaar/printbaar HTML-document
  POST /api/quotes/{id}/send       verstuur per mail
  POST /api/quotes/{id}/decision   {status: geaccepteerd|afgewezen} — handmatig, geen e-sign
  DELETE /api/quotes/{id}          verwijder conceptofferte
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


class ItemBody(BaseModel):
    description: str
    quantity: float
    unit_price_cents: int


class QuoteBody(BaseModel):
    client_name: str
    title: str
    items: List[ItemBody]
    company_id: str = ""
    deal_id: str = ""
    client_email: str = ""
    intro: str = ""
    vat_percent: int = 21
    valid_days: int = 30


class SendBody(BaseModel):
    to_email: str = ""


class DecisionBody(BaseModel):
    status: str


@router.post("", status_code=201)
def api_create_quote(body: QuoteBody):
    try:
        d = body.model_dump()
        items = d.pop("items")
        client_name = d.pop("client_name")
        title = d.pop("title")
        return service.create_quote(client_name, title, items, **d)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def api_list_quotes(status: str = ""):
    return service.list_quotes(status)


@router.get("/{quote_id}")
def api_get_quote(quote_id: str):
    row = service.get_quote(quote_id)
    if not row:
        raise HTTPException(status_code=404, detail="Offerte niet gevonden")
    return row


@router.get("/{quote_id}/html", response_class=HTMLResponse)
def api_quote_html(quote_id: str):
    quote = service.get_quote(quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="Offerte niet gevonden")
    return service.render_quote_html(quote)


@router.post("/{quote_id}/send")
def api_send_quote(quote_id: str, body: SendBody = SendBody()):
    try:
        return service.send_quote(quote_id, body.to_email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{quote_id}/decision")
def api_decide_quote(quote_id: str, body: DecisionBody):
    try:
        return service.markeer_beslissing(quote_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{quote_id}")
def api_delete_quote(quote_id: str):
    try:
        service.delete_quote(quote_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "verwijderd"}

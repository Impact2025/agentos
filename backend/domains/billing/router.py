"""Facturatie-API.

Endpoints:
  POST /api/billing/receipts                bonnetje uploaden -> auto-forward naar DigiBoox
  GET  /api/billing/receipts                 lijst (optioneel ?status=)
  POST /api/billing/receipts/{id}/retry      opnieuw doorsturen na een mislukte poging

  POST /api/billing/invoices/generate        genereer een uren-conceptfactuur uit de agenda
  GET  /api/billing/invoices                 lijst conceptfacturen (optioneel ?status=)
  GET  /api/billing/invoices/{id}            detail + regels
  PATCH /api/billing/invoices/{id}/lines/{line_id}   regel uitsluiten/insluiten
  POST /api/billing/invoices/{id}/approve    keur goed -> genereert CSV-export
  DELETE /api/billing/invoices/{id}          verwerp conceptfactuur

  POST /api/billing/debtors/import           debiteuren-snapshot importeren (CSV)
  GET  /api/billing/debtors                  laatste snapshot + status

  POST /api/billing/reminders/generate       genereer herinneringsconcepten
  GET  /api/billing/reminders                lijst (optioneel ?status=review|verstuurd|overgeslagen)
  POST /api/billing/reminders/{id}/send      verstuur herinnering
  POST /api/billing/reminders/{id}/skip      sla over
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ── Bonnetjes ────────────────────────────────────────────────────────────

@router.post("/receipts", status_code=201)
async def api_upload_receipt(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leeg bestand")
    return service.ontvang_bonnetje(file.filename or "bonnetje", content)


@router.get("/receipts")
def api_list_receipts(status: str = ""):
    return service.list_receipts(status)


@router.post("/receipts/{receipt_id}/retry")
def api_retry_receipt(receipt_id: str):
    try:
        return service.forward_bonnetje(receipt_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Uren -> factuur ──────────────────────────────────────────────────────

class GenerateInvoiceBody(BaseModel):
    client_name: str
    period_start: str
    period_end: str
    hourly_rate_cents: int = 0
    vat_percent: int = 21


@router.post("/invoices/generate", status_code=201)
async def api_generate_invoice(body: GenerateInvoiceBody):
    return await service.genereer_uren_factuur_concept(**body.model_dump())


@router.get("/invoices")
def api_list_invoices(status: str = ""):
    return service.list_invoice_drafts(status)


@router.get("/invoices/{draft_id}")
def api_get_invoice(draft_id: str):
    row = service.get_invoice_draft(draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Niet gevonden")
    return row


class LineBody(BaseModel):
    excluded: bool


@router.patch("/invoices/{draft_id}/lines/{line_id}")
def api_set_line(draft_id: str, line_id: str, body: LineBody):
    service.set_line_excluded(line_id, body.excluded)
    return service.get_invoice_draft(draft_id)


@router.post("/invoices/{draft_id}/approve")
def api_approve_invoice(draft_id: str):
    try:
        return service.keur_factuur_goed(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/invoices/{draft_id}/export")
def api_download_invoice_export(draft_id: str):
    row = service.get_invoice_draft(draft_id)
    if not row or not row.get("export_path"):
        raise HTTPException(status_code=404, detail="Nog geen export beschikbaar")
    path = Path(row["export_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Exportbestand ontbreekt op schijf")
    return FileResponse(path, filename=path.name, media_type="text/csv")


@router.delete("/invoices/{draft_id}")
def api_delete_invoice(draft_id: str):
    service.verwerp_factuur(draft_id)
    return {"status": "verwijderd"}


# ── Debiteuren ───────────────────────────────────────────────────────────

@router.post("/debtors/import", status_code=201)
async def api_import_debtors(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leeg bestand")
    try:
        return service.importeer_debiteuren_snapshot(file.filename or "export.csv", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/debtors")
def api_get_debtors():
    snap = service.get_latest_snapshot()
    stale_days = service.snapshot_stale_days()
    return {
        "snapshot": snap,
        "stale_days": stale_days,
        "is_stale": service.snapshot_is_stale(),
    }


# ── Herinneringen ────────────────────────────────────────────────────────

@router.post("/reminders/generate", status_code=201)
def api_generate_reminders():
    try:
        return service.genereer_herinneringen()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/reminders")
def api_list_reminders(status: str = "review"):
    return service.list_reminders(status)


@router.post("/reminders/{reminder_id}/send")
def api_send_reminder(reminder_id: str):
    try:
        return service.keur_herinnering_goed(reminder_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reminders/{reminder_id}/skip")
def api_skip_reminder(reminder_id: str):
    service.sla_herinnering_over(reminder_id)
    return {"status": "overgeslagen"}

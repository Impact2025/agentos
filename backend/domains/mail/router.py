"""Mail helpdesk API — mailbox-beheer + review-gate acties.

  GET  /api/mail/mailboxes          → alle mailboxen
  POST /api/mail/mailboxes          → mailbox aanmaken (per project)
  GET  /api/mail/pending            → concept-antwoorden wachtend op goedkeuring
  POST /api/mail/reply/{id}/send    → verstuur goedgekeurd concept (zelfde gate als content)
  POST /api/mail/reply/{id}/reject  → afwijzen
  POST /api/mail/reply/{id}/edit    → bewerken + markeren als edited
  POST /api/mail/run                → poll alle mailboxen nu (handmatig)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/mail", tags=["mail"])


class MailboxBody(BaseModel):
    project: str
    label: str = ""
    address: str
    pop_host: str
    pop_port: int = 110
    pop_user: str
    pop_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    brand_context: str = ""
    knowledge_scope: str = "all"
    poll_minutes: int = 30
    enabled: int = 1
    from_display: str = ""


class EditBody(BaseModel):
    text: str


@router.get("/mailboxes")
def get_mailboxes():
    return {"mailboxes": service.list_mailboxes()}


@router.post("/mailboxes")
def post_mailbox(body: MailboxBody):
    mid = service.create_mailbox(body.model_dump())
    return {"ok": True, "id": mid}


@router.get("/pending")
def get_pending():
    return {"replies": service.pending_replies()}


@router.post("/run")
def run_now():
    results = service.run_all_mailboxes()
    return {"ok": True, "results": results}


@router.post("/reply/{reply_id}/send")
@router.post("/replies/{reply_id}/send")  # alias — frontend stuurt soms meervoud
def send_reply(reply_id: int):
    if not service.send_reply(reply_id):
        raise HTTPException(502, "Versturen mislukt (SMTP of reeds verzonden)")
    return {"ok": True}


@router.post("/reply/{reply_id}/reject")
@router.post("/replies/{reply_id}/reject")  # alias
def reject_reply(reply_id: int):
    service.reject_reply(reply_id)
    return {"ok": True}


@router.post("/reply/{reply_id}/edit")
@router.post("/replies/{reply_id}/edit")  # alias
def edit_reply(reply_id: int, body: EditBody):
    service.edit_reply(reply_id, body.text)
    return {"ok": True}

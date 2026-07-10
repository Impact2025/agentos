"""Mail helpdesk API — mailbox-beheer + review-gate acties.

  GET    /api/mail/mailboxes?project=   → mailboxen (optioneel per project)
  POST   /api/mail/mailboxes            → mailbox aanmaken (per project)
  PATCH  /api/mail/mailboxes/{id}       → mailbox bijwerken (bv. enabled aan/uit)
  DELETE /api/mail/mailboxes/{id}       → mailbox verwijderen (incl. inbox/concepten)
  GET    /api/mail/pending?project=     → concept-antwoorden wachtend op goedkeuring
  POST   /api/mail/reply/{id}/send      → verstuur goedgekeurd concept (zelfde gate als content)
  POST   /api/mail/reply/{id}/reject    → afwijzen
  POST   /api/mail/reply/{id}/edit      → bewerken + markeren als edited
  POST   /api/mail/run                  → poll alle mailboxen (of body {mailbox_id} voor één)
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

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
    signature: str = ""


class EditBody(BaseModel):
    # Accepteert zowel `text` als `draft_body` — de frontend stuurde historisch
    # beide vormen; een 422 op "Opslaan" mag nooit meer voorkomen.
    text: str = ""
    draft_body: str = ""

    @model_validator(mode="after")
    def _one_of(self):
        if not self.text and self.draft_body:
            self.text = self.draft_body
        if not self.text.strip():
            raise ValueError("Lege tekst — stuur 'text' of 'draft_body' mee.")
        return self


class MailboxPatch(BaseModel):
    project: Optional[str] = None
    label: Optional[str] = None
    address: Optional[str] = None
    pop_host: Optional[str] = None
    pop_port: Optional[int] = None
    pop_user: Optional[str] = None
    pop_password: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    brand_context: Optional[str] = None
    knowledge_scope: Optional[str] = None
    poll_minutes: Optional[int] = None
    enabled: Optional[int] = None
    from_display: Optional[str] = None
    signature: Optional[str] = None


class RunBody(BaseModel):
    mailbox_id: Optional[str] = None


@router.get("/mailboxes")
def get_mailboxes(project: Optional[str] = None):
    return {"mailboxes": service.list_mailboxes(project=project)}


@router.post("/mailboxes")
def post_mailbox(body: MailboxBody):
    mid = service.create_mailbox(body.model_dump())
    return {"ok": True, "id": mid}


@router.patch("/mailboxes/{mailbox_id}")
def patch_mailbox(mailbox_id: str, body: MailboxPatch):
    if not service.update_mailbox(mailbox_id, body.model_dump(exclude_none=True)):
        raise HTTPException(404, "Mailbox niet gevonden of niets te wijzigen")
    return {"ok": True}


@router.delete("/mailboxes/{mailbox_id}")
def remove_mailbox(mailbox_id: str):
    if not service.delete_mailbox(mailbox_id):
        raise HTTPException(404, "Mailbox niet gevonden")
    return {"ok": True}


@router.get("/mailboxes/{mailbox_id}/knowledge")
def mailbox_knowledge(mailbox_id: str):
    """Wat weet de helpdesk over dit project? Coverage per kennislaag + hints."""
    from ...shared.database import get_conn
    from . import knowledge as knowledge_mod
    with get_conn() as conn:
        mb = conn.execute("SELECT * FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()
        if not mb:
            raise HTTPException(404, "Mailbox niet gevonden")
        mb = dict(mb)
        return knowledge_mod.coverage(conn, mb.get("project", ""), mb)


@router.get("/pending")
def get_pending(project: Optional[str] = None):
    return {"replies": service.pending_replies(project=project)}


@router.post("/run")
def run_now(body: Optional[RunBody] = None):
    results = service.run_all_mailboxes(
        mailbox_id=body.mailbox_id if body else None
    )
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

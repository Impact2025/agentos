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
from typing import Optional, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from . import service
from ..bridge import actions as bridge_actions

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
    # Microsoft Graph (OAuth2 client_credentials) — voor Office365/Exchange
    auth_method: str = "pop"
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_user_upn: str = ""


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


class BulkRejectBody(BaseModel):
    ids: list[int] = []


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
    auth_method: Optional[str] = None
    graph_tenant_id: Optional[str] = None
    graph_client_id: Optional[str] = None
    graph_client_secret: Optional[str] = None
    graph_user_upn: Optional[str] = None


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


# ── Persoonlijke mail (Vincents eigen postvak, via Graph) ──────────────────
# De Actiecentrum-kaarten voor persoonlijke mail sturen `personal_mail_send` /
# `personal_mail_reject`; die lopen via de bridge-handlers (Outlook/Graph),
# niet via de project-mailbox-service hierboven.

@router.post("/personal/{item_id}/send")
async def personal_mail_send(item_id: str, body: Optional[Dict] = None):
    ok, msg = await bridge_actions._personal_mail_send(
        item_id, (body or {}))
    if not ok:
        raise HTTPException(502, msg)
    return {"ok": True, "message": msg}


@router.post("/personal/{item_id}/reject")
async def personal_mail_reject(item_id: str):
    ok, msg = await bridge_actions._personal_mail_reject(item_id, {})
    if not ok:
        raise HTTPException(502, msg)
    return {"ok": True, "message": msg}


@router.post("/replies/reject-bulk")
def reject_replies_bulk(body: BulkRejectBody):
    n = service.reject_replies_bulk(body.ids)
    return {"ok": True, "rejected": n}


@router.post("/replies/delete-bulk")
def delete_replies_bulk(body: BulkRejectBody):
    n = service.delete_replies_bulk(body.ids)
    return {"ok": True, "deleted": n}


@router.post("/reply/{reply_id}/edit")
@router.post("/replies/{reply_id}/edit")  # alias
def edit_reply(reply_id: int, body: EditBody):
    service.edit_reply(reply_id, body.text)
    return {"ok": True}


@router.post("/reply/{reply_id}/gsc-fix")
@router.post("/replies/{reply_id}/gsc-fix")  # alias
def gsc_fix_reply(reply_id: int, body: Optional[Dict] = None):
    """GSC-expert-agent: analyseer een Search Console-notificatiemail en schrijf
    een concrete fix-gids terug in het concept.

    body.auto (default true): de agent handelt veilig af — verzenden alleen
    naar een écht mens bij hoge confidence, anders oplossen/ter review.
    body.auto=false: alleen analyseren + ter review zetten (handmatige knop).

    Retourneert {'ok', 'reply_id', 'domain', 'reason', 'used_live_gsc',
    'confidence', 'disposition', 'auto_sent', 'analysis'} of 404.
    """
    auto = True
    if isinstance(body, dict):
        auto = bool(body.get("auto", True))
    result = service.gsc_fix_reply(reply_id, auto=auto)
    if not result:
        raise HTTPException(404, "Concept niet gevonden of geen Search Console-mail")
    return {"ok": True, **result}


@router.post("/gsc-fix-all")
def gsc_fix_all(body: Optional[Dict] = None):
    """Verwerk alle wachtende GSC-concepten in één keer via de expert-agent.
    VUUR-EN-VERGEET: komt meteen terug met een job_id; de verwerking loopt
    op de achtergrond. body.auto (default true) regelt autonoom verzenden/
    oplossen. Ververs het Actiecentrum na enkele seconden voor de resultaten."""
    auto = True
    if isinstance(body, dict):
        auto = bool(body.get("auto", True))
    return service.gsc_fix_all_pending(auto=auto)


@router.post("/gsc-feedback")
def gsc_feedback(body: Dict):
    """Sla Vincents feedback op een GSC-analyse op (leer-laag).
    body: {analysis_id, domain, reason, score (1-5), corrected_text?, note?}"""
    try:
        return service.gsc_record_feedback(
            analysis_id=body.get("analysis_id", ""),
            domain=body.get("domain", ""),
            reason=body.get("reason", ""),
            score=int(body.get("score", 0) or 0),
            corrected_text=body.get("corrected_text", "") or "",
            note=body.get("note", "") or "",
        )
    except Exception as e:
        raise HTTPException(400, f"Feedback mislukt: {e}")


@router.post("/reply/{reply_id}/ignore-sender")
@router.post("/replies/{reply_id}/ignore-sender")  # alias
def ignore_sender(reply_id: int):
    """'Niet meer reageren': afzender op de negeerlijst + alle openstaande
    concepten van deze afzender afwijzen. Toekomstige mails van dit adres
    (bij zakelijke domeinen: het hele domein) krijgen nooit meer een concept."""
    result = service.ignore_sender(reply_id)
    if not result:
        raise HTTPException(404, "Concept niet gevonden of afzender onbekend")
    return {"ok": True, **result}


@router.post("/reply/{reply_id}/mark-known")
@router.post("/replies/{reply_id}/mark-known")  # alias
def mark_sender_known(reply_id: int):
    """'Markeer als bekend': zet de afzender van dit concept in het
    bekende-afzenders-register, zodat hij voortaan géén 'Nieuwe afzender'
    meer is in het Actiecentrum (ook al staat hij niet in de CRM/leads-tafel).
    Idempotent — een tweede keer markeren verandert niets."""
    result = service.mark_sender_known(reply_id)
    if not result:
        raise HTTPException(404, "Concept niet gevonden")
    return {"ok": True, **result}


@router.post("/inbox/{inbox_id}/archive")
def archive_inbox_mail(inbox_id: int):
    """Markeer een project-mailbox-bericht als verwerkt (classified='ignored').

    Comes uit het Postvak (project-modus), dat lezen + archiveren is — geen
    review-gate. De rij blijft in de DB staan voor de Helpdesk-tab; hij
    verdwijnt alleen uit de Postvak-weergave. Idempotent.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE mail_inbox SET classified='ignored' WHERE id=? AND classified!='ignored'",
            (inbox_id,),
        )
    return {"ok": True, "archived": cur.rowcount}


@router.get("/ignored")
def get_ignored():
    return {"ignored": service.list_ignored_senders()}


@router.delete("/ignored/{ignored_id}")
def delete_ignored(ignored_id: int):
    if not service.unignore_sender(ignored_id):
        raise HTTPException(404, "Niet gevonden")
    return {"ok": True}


@router.get("/known")
def get_known():
    return {"known": service.list_known_senders()}


@router.delete("/known/{known_id}")
def delete_known(known_id: int):
    if not service.unmark_sender_known(known_id):
        raise HTTPException(404, "Niet gevonden")
    return {"ok": True}


class BulkTriageBody(BaseModel):
    ids: list[str] = []
    label: str = "actie"
    priority: Optional[int] = None


@router.post("/inbox/bulk-triage")
def bulk_triage(body: BulkTriageBody):
    """Bulk-triage meerdere Outlook-mails in één database-call.

    De inbox groeit sneller dan handmatige triage hem kan wegwerken
    (incident 23 aug 2026: 73 onbeantwoorde mails). Deze bulk-actie
    markeert één of meerdere emails tegelijk als getrieerd.
    """
    result = service.bulk_triage(body.ids, body.label, body.priority)
    return result

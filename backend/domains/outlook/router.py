"""
Outlook API router — volledig e-mailbeheer via Microsoft Graph.

Endpoints:
  GET  /api/outlook/status           Auth-status + mailbox info
  POST /api/outlook/auth/start       Start device code flow
  GET  /api/outlook/auth/status      Poll of auth geslaagd is
  DELETE /api/outlook/auth           Uitloggen

  POST /api/outlook/sync             Inbox sync (haalt verse e-mails op)
  GET  /api/outlook/emails           Lijst (filter: label, unread, search)
  GET  /api/outlook/sorted           Gegroepeerd: needs_reply / fyi / waiting
  GET  /api/outlook/emails/{id}      E-mail detail + body + lead-link
  POST /api/outlook/emails/{id}/read Mark as read
  POST /api/outlook/emails/{id}/triage  AI-triage (SSE)
  POST /api/outlook/emails/{id}/draft   AI concept-antwoord (SSE)
  POST /api/outlook/emails/{id}/reply   Verstuur antwoord

  POST /api/outlook/compose          Nieuwe e-mail versturen
  POST /api/outlook/triage/batch     Batch AI-triage (SSE)
  GET  /api/outlook/stats            Statistieken per label

  GET    /api/outlook/rules              Afzenderregels + wat ze weghielden
  POST   /api/outlook/rules              Regel toevoegen (past meteen toe)
  DELETE /api/outlook/rules/{id}         Regel intrekken (geeft mail terug)
  GET    /api/outlook/filtered           Weggehouden mail mét bewijs
  POST   /api/outlook/emails/{id}/spam       Nooit meer van deze afzender
  POST   /api/outlook/emails/{id}/archive    Deze mail hoeft niets van je
  POST   /api/outlook/emails/{id}/restore    Terug in het postvak
"""
import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import service as outlook_service

router = APIRouter(prefix="/api/outlook", tags=["outlook"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ComposeRequest(BaseModel):
    to: str
    subject: str
    body_html: str


class ReplyRequest(BaseModel):
    body_html: str


class DraftRequest(BaseModel):
    instructions: str = ""


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/status")
def outlook_status():
    configured = outlook_service.is_configured()
    authenticated = outlook_service.is_authenticated() if configured else False
    account = outlook_service.get_account_info() if authenticated else None
    stats = outlook_service.get_stats() if authenticated else None
    return {
        "configured": configured,
        "authenticated": authenticated,
        # token_valid = er is daadwerkelijk een bruikbaar (verversbaar) token.
        # 'authenticated' kan True zijn op een verlopen cache-account, dus de UI
        # moet op token_valid vertrouwen om te weten of versturen gaat lukken.
        "token_valid": bool(outlook_service.get_valid_token()),
        "account": account,
        "stats": stats,
    }


@router.post("/auth/start")
async def auth_start(background_tasks: BackgroundTasks):
    if not outlook_service.is_configured():
        raise HTTPException(400, "OUTLOOK_CLIENT_ID niet ingesteld in .env")

    flow = outlook_service.prepare_device_flow()

    # Start polling op de achtergrond
    background_tasks.add_task(_run_bg_auth)

    return flow


async def _run_bg_auth():
    await outlook_service.bg_acquire_token()


@router.get("/auth/status")
def auth_status():
    state = outlook_service.get_auth_state()
    return state


@router.delete("/auth")
def auth_logout():
    outlook_service.clear_auth()
    return {"success": True}


# ── Mail sync ─────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_inbox(limit: int = Query(50, ge=1, le=100)):
    # `is_authenticated()` kent alleen het gecachete account; of er een bruikbaar
    # token is, weet alleen `get_valid_token()`. Op 11 aug 2026 was het grant
    # ingetrokken (AADSTS50173) en gaf deze route een kale 500 — een verlopen
    # login hoort een 401 met een instructie te zijn, niet een serverfout.
    if not outlook_service.is_authenticated() or not outlook_service.get_valid_token():
        raise HTTPException(401, "Outlook-sessie verlopen of ingetrokken — log opnieuw in "
                                 "via de Postvak-tab (Koppel Outlook-account).")
    emails = await outlook_service.sync_inbox(limit=limit)
    return {"synced": len(emails), "emails": emails}


# ── Email lijst ───────────────────────────────────────────────────────────────

@router.get("/emails")
def list_emails(
    label: str = Query(None),
    folder: str = Query(None),
    unread: bool = Query(False),
    search: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    emails = outlook_service.list_emails_db(
        folder=folder,
        label=label,
        unread_only=unread,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {"emails": emails, "total": len(emails)}


@router.get("/sorted")
def sorted_inbox():
    """Inbox gegroepeerd: needs_reply / fyi / waiting (+ untriaged-teller)."""
    return outlook_service.list_sorted_db()


# ── Email detail ──────────────────────────────────────────────────────────────

@router.get("/emails/{email_id}")
async def get_email(email_id: str):
    # Probeer eerst uit DB; haal via Graph als body ontbreekt
    local = outlook_service.get_email_db(email_id)
    if local and local.get("body_html"):
        from_email = local.get("from_email", "")
        lead = outlook_service.find_lead_by_email(from_email) if from_email else None
        return {**local, "linked_lead": lead}

    if not outlook_service.is_authenticated():
        if not local:
            raise HTTPException(404, "E-mail niet gevonden")
        return {**local, "linked_lead": None}

    try:
        return await outlook_service.get_email_detail(email_id)
    except Exception as e:
        if local:
            return {**local, "linked_lead": None}
        raise HTTPException(404, str(e))


@router.post("/emails/{email_id}/read")
async def mark_read(email_id: str):
    await outlook_service.mark_as_read(email_id)
    return {"success": True}


# ── AI endpoints (SSE) ────────────────────────────────────────────────────────

@router.post("/emails/{email_id}/triage")
def triage_email(email_id: str):
    async def generate():
        async for event in outlook_service.triage_single(email_id):
            yield _sse(event)
        yield _sse({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/emails/{email_id}/draft")
def draft_reply(email_id: str, body: DraftRequest):
    async def generate():
        async for event in outlook_service.draft_reply(email_id, body.instructions):
            yield _sse(event)
        yield _sse({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Versturen ─────────────────────────────────────────────────────────────────

@router.post("/emails/{email_id}/reply")
async def reply_email(email_id: str, body: ReplyRequest):
    if not outlook_service.is_authenticated():
        raise HTTPException(401, "Niet geauthenticeerd bij Microsoft")
    try:
        result = await outlook_service.send_reply(email_id, body.body_html)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/compose")
async def compose_email(body: ComposeRequest):
    if not outlook_service.is_authenticated():
        raise HTTPException(401, "Niet geauthenticeerd bij Microsoft")
    try:
        result = await outlook_service.send_new_email(body.to, body.subject, body.body_html)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Batch triage (SSE) ────────────────────────────────────────────────────────

@router.post("/triage/batch")
def batch_triage(limit: int = Query(30, ge=1, le=100)):
    async def generate():
        async for event in outlook_service.batch_triage(limit=limit):
            yield _sse(event)
        yield _sse({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats():
    return outlook_service.get_stats()


# ── Afzenderregels ────────────────────────────────────────────────────────────
#
# De spam-knop en alles eromheen. Twee dingen die deze endpoints anders maken
# dan een gewone CRUD: aanmaken past de regel meteen toe op wat er al ligt (een
# filter met terugwerkende kracht), en intrekken geeft élke mail terug die eraan
# gesneuveld is. Zonder dat tweede is strenger filteren onverantwoord.

class RuleRequest(BaseModel):
    pattern: str
    scope: str = "adres"          # adres | domein
    action: str = "spam"          # spam | geen-klant | altijd-tonen
    reason: str = ""


class BlockRequest(BaseModel):
    scope: str = "adres"
    action: str = "spam"
    reason: str = ""


@router.get("/rules")
def list_rules(include_inactive: bool = Query(False)):
    from . import rules as mail_rules
    return {
        "rules": mail_rules.list_rules(include_inactive=include_inactive),
        "stats": mail_rules.filtered_stats(),
    }


@router.post("/rules")
def add_rule(body: RuleRequest):
    from . import rules as mail_rules
    try:
        rule = mail_rules.add_rule(body.pattern, scope=body.scope, action=body.action,
                                   reason=body.reason, source="mens")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rule": rule, "applied": rule.get("applied", 0)}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    from . import rules as mail_rules
    try:
        return mail_rules.deactivate_rule(rule_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/filtered")
def filtered(limit: int = Query(50, ge=1, le=200)):
    """Wat de regels hebben weggehouden, mét het bewijs en een weg terug."""
    from . import rules as mail_rules
    return {"emails": mail_rules.filtered_mails(limit=limit),
            "stats": mail_rules.filtered_stats()}


@router.post("/emails/{email_id}/spam")
def block_sender(email_id: str, body: BlockRequest = BlockRequest()):
    try:
        return outlook_service.block_sender(
            email_id, scope=body.scope, action=body.action, reason=body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/emails/{email_id}/archive")
def archive_email(email_id: str):
    try:
        return outlook_service.archive_email(email_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/emails/{email_id}/restore")
def restore_email(email_id: str):
    try:
        return outlook_service.restore_email(email_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

"""
Outlook / Microsoft Graph — wereldklasse e-mailbeheer.

Auth: MSAL Device Code Flow (geen client secret, werkt op localhost).
Setup in Azure portal:
  1. App registrations → New registration → naam "Agent OS"
  2. Supported account types: "Accounts in any organizational directory and personal Microsoft accounts"
  3. Redirect URI: Public client/native → https://login.microsoftonline.com/common/oauth2/nativeclient
  4. API permissions (Delegated): Mail.Read, Mail.ReadWrite, Mail.Send, User.Read
  5. Kopieer Application (client) ID → OUTLOOK_CLIENT_ID in .env

Flow:
  POST /api/outlook/auth/start   → {user_code, verification_uri}
  [User opent URL, voert code in]
  GET  /api/outlook/auth/status  → {status: "done", email, name}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from ...shared.config import OUTLOOK_CLIENT_ID, OUTLOOK_TENANT_ID
from ...shared.database import get_conn

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
]

# Module-level auth state
_pending_flow: Optional[dict] = None
_pending_app = None
_pending_cache = None
_auth_state: dict = {"status": "unauthenticated", "email": "", "name": ""}

# ── AI prompts ────────────────────────────────────────────────────────────────

_TRIAGE_SYSTEM = (
    "Je bent een e-mail-triagespecialist. Analyseer elke e-mail en kies PRECIES één label:\n"
    "- urgent: directe actie vereist, deadline <24u, escalatie, klacht\n"
    "- actie: actie nodig, maar niet direct\n"
    "- wacht: wacht op iemand anders / follow-up nodig\n"
    "- info: ter kennisgeving, geen actie vereist\n"
    "- archief: nieuwsbrief, marketing, notificatie, automatisch bericht\n\n"
    "ANTWOORD UITSLUITEND met één geldig JSON-object zonder markdown-fences:\n"
    '{"label":"urgent|actie|wacht|info|archief","priority":0-100,'
    '"summary":"<20 woorden max>","action":"concrete stap of leeg",'
    '"reply_hint":"kort antwoord-hint of leeg"}'
)

_DRAFT_SYSTEM = (
    "Je bent een expert zakelijke schrijver. Schrijf een professioneel, concreet en warm antwoord.\n"
    "Richtlijnen:\n"
    "- Direct en bondig (max 3 alinea's)\n"
    "- Zakelijk maar menselijk — geen stijf jargon\n"
    "- Sluit passend af, geen overdreven formules\n"
    "- Schrijf in dezelfde taal als de ontvangen e-mail\n"
    "- Geen placeholders zoals [Naam] — laat weg als onbekend\n"
    "- Geen aanhef-regel toevoegen, begin direct met de tekst"
)


# ── Token cache (SQLite) ──────────────────────────────────────────────────────

def _save_token_cache(cache_str: str, email: str, name: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO outlook_tokens (account_id, email, name, token_cache, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                token_cache = excluded.token_cache,
                email       = excluded.email,
                name        = excluded.name,
                updated_at  = excluded.updated_at
        """, (email, email, name, cache_str, now, now))


def _load_token_cache() -> Optional[tuple]:
    """Returns (cache_str, email, name) or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT token_cache, email, name FROM outlook_tokens ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return (row["token_cache"], row["email"], row["name"]) if row else None


def _clear_token_cache() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM outlook_tokens")


# ── MSAL helpers ──────────────────────────────────────────────────────────────

def _get_msal_app():
    """Create MSAL PublicClientApplication, loading any cached tokens from DB."""
    try:
        from msal import PublicClientApplication, SerializableTokenCache
    except ImportError:
        raise RuntimeError(
            "msal niet geïnstalleerd. Voer uit: pip install msal  (of herstart via start.ps1)"
        )

    cache = SerializableTokenCache()
    stored = _load_token_cache()
    if stored:
        cache.deserialize(stored[0])

    app = PublicClientApplication(
        client_id=OUTLOOK_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{OUTLOOK_TENANT_ID}",
        token_cache=cache,
    )
    return app, cache


# ── Public auth API ───────────────────────────────────────────────────────────

def is_configured() -> bool:
    return bool(OUTLOOK_CLIENT_ID)


def is_authenticated() -> bool:
    if not is_configured():
        return False
    try:
        app, _ = _get_msal_app()
        return bool(app.get_accounts())
    except Exception:
        return False


def get_account_info() -> Optional[dict]:
    stored = _load_token_cache()
    if not stored:
        return None
    _, email, name = stored
    return {"email": email, "name": name or email}


def get_auth_state() -> dict:
    if is_authenticated():
        info = get_account_info()
        return {"status": "done", "email": info["email"], "name": info["name"]}
    return _auth_state.copy()


def get_valid_token() -> Optional[str]:
    """Return a valid access token, auto-refreshing if needed."""
    if not is_configured():
        return None
    try:
        app, cache = _get_msal_app()
        accounts = app.get_accounts()
        if not accounts:
            return None
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            if cache.has_state_changed:
                stored = _load_token_cache()
                email = stored[1] if stored else ""
                name = stored[2] if stored else ""
                _save_token_cache(cache.serialize(), email, name)
            return result["access_token"]
    except Exception as e:
        log.warning(f"Token ophalen mislukt: {e}")
    return None


def prepare_device_flow() -> dict:
    """Initiate device code flow. Returns {user_code, verification_uri, expires_in}."""
    global _pending_flow, _pending_app, _pending_cache, _auth_state

    if not OUTLOOK_CLIENT_ID:
        raise ValueError("OUTLOOK_CLIENT_ID niet ingesteld in .env")

    app, cache = _get_msal_app()
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)

    if "error" in flow:
        raise RuntimeError(
            f"Device flow starten mislukt: {flow.get('error_description', flow['error'])}"
        )

    _pending_flow = flow
    _pending_app = app
    _pending_cache = cache
    _auth_state = {"status": "pending", "email": "", "name": ""}

    return {
        "user_code": flow["user_code"],
        "verification_uri": flow["verification_uri"],
        "expires_in": flow.get("expires_in", 900),
        "message": flow.get("message", ""),
    }


async def bg_acquire_token() -> None:
    """Background asyncio task: blocks in thread until user completes auth."""
    global _pending_flow, _pending_app, _pending_cache, _auth_state

    if not _pending_flow or not _pending_app:
        _auth_state = {"status": "error", "error": "Geen device flow actief"}
        return

    app = _pending_app
    flow = _pending_flow
    cache = _pending_cache

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: app.acquire_token_by_device_flow(flow)
        )

        if "error" in result:
            err = result.get("error_description") or result.get("error", "onbekende fout")
            _auth_state = {"status": "error", "error": err}
            return

        accounts = app.get_accounts()
        account = accounts[0] if accounts else {}
        email = account.get("username", "")
        name = account.get("name", email)

        if cache.has_state_changed:
            _save_token_cache(cache.serialize(), email, name)

        _auth_state = {"status": "done", "email": email, "name": name}
        log.info(f"[Outlook] Auth geslaagd voor {email}")

    except asyncio.CancelledError:
        _auth_state = {"status": "cancelled", "email": "", "name": ""}
        raise
    except Exception as e:
        log.error(f"[Outlook] Auth fout: {e}")
        _auth_state = {"status": "error", "error": str(e)}
    finally:
        _pending_flow = None
        _pending_app = None
        _pending_cache = None


def clear_auth() -> None:
    global _pending_flow, _pending_app, _pending_cache, _auth_state
    _pending_flow = None
    _pending_app = None
    _pending_cache = None
    _auth_state = {"status": "unauthenticated", "email": "", "name": ""}
    _clear_token_cache()


# ── Graph API helpers ─────────────────────────────────────────────────────────

async def _graph(method: str, path: str, token: str, **kwargs) -> dict:
    url = f"{GRAPH_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
        if resp.status_code in (204, 202):
            return {}
        resp.raise_for_status()
        # Graph kan bij succes een lege body teruggeven (bijv. sendMail →
        # 202 Accepted zonder JSON). Forceer geen json()-parse op lege bodies.
        if not resp.content or not resp.content.strip():
            return {}
        try:
            return resp.json()
        except Exception:
            return {}


# ── Mail sync ─────────────────────────────────────────────────────────────────

async def sync_inbox(limit: int = 50) -> List[dict]:
    """Fetch latest emails from Graph and upsert into local DB."""
    token = get_valid_token()
    if not token:
        raise RuntimeError("Niet geauthenticeerd bij Microsoft")

    params = {
        "$orderby": "receivedDateTime desc",
        "$top": str(min(limit, 100)),
        "$select": (
            "id,subject,from,toRecipients,receivedDateTime,"
            "bodyPreview,isRead,importance,hasAttachments,conversationId"
        ),
    }
    data = await _graph("GET", "/me/messages", token, params=params)
    messages = data.get("value", [])

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for m in messages:
            from_addr = (m.get("from") or {}).get("emailAddress", {})
            to_list = m.get("toRecipients") or []
            to_email = ", ".join(
                r.get("emailAddress", {}).get("address", "") for r in to_list[:3]
            )
            conn.execute("""
                INSERT INTO outlook_emails
                    (id, subject, from_email, from_name, to_email,
                     received_at, body_preview, is_read, folder,
                     importance, has_attachments, thread_id, synced_at)
                VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    is_read  = excluded.is_read,
                    synced_at = excluded.synced_at
            """, (
                m["id"],
                m.get("subject") or "(geen onderwerp)",
                from_addr.get("address", ""),
                from_addr.get("name", ""),
                to_email,
                m.get("receivedDateTime", now),
                m.get("bodyPreview", ""),
                1 if m.get("isRead") else 0,
                "inbox",
                m.get("importance", "normal"),
                1 if m.get("hasAttachments") else 0,
                m.get("conversationId", ""),
                now,
            ))

    # Reply-detectie voor de acquisitieformule: inkomende mail van een lead
    # die we benaderd hebben → status 'replied' (+ uitkomst-kaart voor Vincent).
    try:
        from ..prospecting.funnel import mark_replied_if_lead
        from ..linkbuilding.service import mark_replied_if_prospect
        for m in messages:
            from_addr = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
            if from_addr:
                mark_replied_if_lead(from_addr, m.get("receivedDateTime", ""))
                # Zelfde detectie voor linkbuilding-partners.
                mark_replied_if_prospect(from_addr, m.get("receivedDateTime", ""))
    except Exception as e:
        log.warning(f"Reply-detectie op leads mislukt: {e}")

    return list_emails_db(limit=limit)


async def get_email_detail(email_id: str) -> dict:
    """Fetch full email body from Graph, save to DB, mark as read."""
    token = get_valid_token()
    if not token:
        raise RuntimeError("Niet geauthenticeerd")

    params = {
        "$select": (
            "id,subject,from,toRecipients,ccRecipients,"
            "receivedDateTime,body,isRead,importance,hasAttachments,conversationId"
        )
    }
    data = await _graph("GET", f"/me/messages/{email_id}", token, params=params)

    body = data.get("body") or {}
    content = body.get("content", "")
    content_type = body.get("contentType", "text")
    body_html = content if content_type == "html" else f"<pre>{content}</pre>"

    with get_conn() as conn:
        conn.execute(
            "UPDATE outlook_emails SET body_html = ?, is_read = 1 WHERE id = ?",
            (body_html, email_id),
        )

    # Mark as read in Graph (fire and forget)
    try:
        await _graph("PATCH", f"/me/messages/{email_id}", token, json={"isRead": True})
    except Exception:
        pass

    # Detect linked lead
    from_email = (data.get("from") or {}).get("emailAddress", {}).get("address", "")
    lead = _find_lead_by_email(from_email) if from_email else None

    return {**data, "body_html": body_html, "linked_lead": lead}


def _find_lead_by_email(email: str) -> Optional[dict]:
    if not email:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, org_name, website, status, score FROM leads WHERE email = ? LIMIT 1",
            (email.lower(),),
        ).fetchone()
    return dict(row) if row else None


async def mark_as_read(email_id: str) -> None:
    token = get_valid_token()
    if not token:
        return
    try:
        await _graph("PATCH", f"/me/messages/{email_id}", token, json={"isRead": True})
        with get_conn() as conn:
            conn.execute("UPDATE outlook_emails SET is_read = 1 WHERE id = ?", (email_id,))
    except Exception as e:
        log.warning(f"Mark-as-read mislukt: {e}")


async def send_new_email(to: str, subject: str, body_html: str) -> dict:
    """Send a new email via Graph.

    Returns {"success": True} on send, or {"success": False, "error": ...}
    when the mail cannot be dispatched (no valid token, dead session, Graph
    error). Callers MUST check `success` — this NEVER raises a raw exception,
    so a stale/expired Outlook session degrades to a clean 422/502 instead of
    crashing the endpoint with an uncaught 500.
    """
    token = get_valid_token()
    if not token:
        return {
            "success": False,
            "error": "Geen geldig Outlook-token (sessie verlopen of netwerkfout). "
                     "Log opnieuw in via Instellingen -> Outlook en probeer het daarna opnieuw.",
        }

    recipients = [
        {"emailAddress": {"address": addr.strip()}}
        for addr in to.split(",")
        if addr.strip()
    ]
    if not recipients:
        return {"success": False, "error": "Geen geldige ontvanger (leeg adres)."}

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": recipients,
        }
    }
    try:
        await _graph("POST", "/me/sendMail", token, json=payload)
    except Exception as e:
        return {"success": False, "error": f"Versturen via Graph mislukt: {e}"}
    return {"success": True}


async def send_reply(email_id: str, body_html: str) -> dict:
    """Reply to an existing email via Graph."""
    token = get_valid_token()
    if not token:
        raise RuntimeError("Niet geauthenticeerd")

    # Create draft reply → patch body → send
    draft = await _graph("POST", f"/me/messages/{email_id}/createReply", token, json={})
    draft_id = draft["id"]
    await _graph(
        "PATCH", f"/me/messages/{draft_id}", token,
        json={"body": {"contentType": "HTML", "content": body_html}},
    )
    await _graph("POST", f"/me/messages/{draft_id}/send", token, json={})

    with get_conn() as conn:
        conn.execute("UPDATE outlook_emails SET is_replied = 1, is_read = 1 WHERE id = ?", (email_id,))

    return {"success": True}


# ── Local DB queries ──────────────────────────────────────────────────────────

def list_emails_db(
    folder: str = None,
    label: str = None,
    unread_only: bool = False,
    search: str = None,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    clauses, params = [], []

    if folder:
        clauses.append("folder = ?")
        params.append(folder)
    if label and label != "all":
        clauses.append("triage_label = ?")
        params.append(label)
    if unread_only:
        clauses.append("is_read = 0")
    if search:
        clauses.append(
            "(subject LIKE ? OR from_email LIKE ? OR from_name LIKE ? OR body_preview LIKE ?)"
        )
        q = f"%{search}%"
        params.extend([q, q, q, q])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM outlook_emails {where} ORDER BY received_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return [dict(r) for r in rows]


def get_email_db(email_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM outlook_emails WHERE id = ?", (email_id,)
        ).fetchone()
    return dict(row) if row else None


def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM outlook_emails").fetchone()["c"]
        unread = conn.execute("SELECT COUNT(*) c FROM outlook_emails WHERE is_read=0").fetchone()["c"]
        untriaged = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails WHERE triage_label=''"
        ).fetchone()["c"]
        by_label = conn.execute(
            "SELECT triage_label, COUNT(*) c FROM outlook_emails "
            "WHERE triage_label!='' GROUP BY triage_label"
        ).fetchall()

    return {
        "total": total,
        "unread": unread,
        "untriaged": untriaged,
        "by_label": {r["triage_label"]: r["c"] for r in by_label},
    }


# ── AI triage ─────────────────────────────────────────────────────────────────

async def triage_single(email_id: str) -> AsyncGenerator[dict, None]:
    """AI-triageert één e-mail via SSE-events."""
    email = get_email_db(email_id)
    if not email:
        yield {"type": "error", "message": f"E-mail {email_id} niet gevonden in DB"}
        return

    content = (
        f"Van: {email['from_name']} <{email['from_email']}>\n"
        f"Aan: {email.get('to_email', '')}\n"
        f"Onderwerp: {email['subject']}\n"
        f"Ontvangen: {email['received_at'][:10]}\n"
        f"Belang: {email.get('importance','normal')}\n\n"
        f"Inhoud:\n{email['body_preview']}"
    )

    messages = [{"role": "user", "content": f"Triageer deze e-mail:\n\n{content}"}]

    full_text = ""
    async for event in agent_service.run_agent(messages, _TRIAGE_SYSTEM, use_tools=False):
        if event["type"] == "text":
            full_text += event["text"]
        yield event

    # Parse + opslaan
    try:
        clean = re.sub(r"^```[a-z]*\n?", "", full_text.strip())
        clean = re.sub(r"\n?```$", "", clean).strip()
        result = json.loads(clean)

        label = result.get("label", "info")
        priority = max(0, min(100, int(result.get("priority", 50))))
        summary = result.get("summary", "")
        action = result.get("action", "")
        reply_hint = result.get("reply_hint", "")

        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            conn.execute(
                """UPDATE outlook_emails
                   SET triage_label=?, priority=?, ai_summary=?, ai_action=?,
                       reply_hint=?, triaged_at=?
                   WHERE id=?""",
                (label, priority, summary, action, reply_hint, now, email_id),
            )

        yield {
            "type": "triage_done",
            "email_id": email_id,
            "label": label,
            "priority": priority,
            "summary": summary,
            "action": action,
            "reply_hint": reply_hint,
        }
    except (json.JSONDecodeError, ValueError) as e:
        log.warning(f"Triage JSON parse mislukt ({email_id}): {e} | raw={full_text[:300]}")
        yield {"type": "triage_error", "email_id": email_id}


async def batch_triage(limit: int = 30) -> AsyncGenerator[dict, None]:
    """Triageert alle ongeprioriteerde e-mails."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, subject FROM outlook_emails "
            "WHERE triage_label='' ORDER BY received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    total = len(rows)
    if total == 0:
        yield {"type": "done", "message": "Alle e-mails zijn al getriageerd"}
        return

    yield {"type": "start", "total": total}

    for i, row in enumerate(rows):
        yield {
            "type": "progress",
            "current": i + 1,
            "total": total,
            "email_id": row["id"],
            "subject": row["subject"],
        }
        async for event in triage_single(row["id"]):
            yield event
        await asyncio.sleep(0.3)

    yield {"type": "batch_done", "total": total}


async def draft_reply(email_id: str, instructions: str = "") -> AsyncGenerator[dict, None]:
    """AI schrijft een concept-antwoord via SSE."""
    email = get_email_db(email_id)
    if not email:
        yield {"type": "error", "message": "E-mail niet gevonden"}
        return

    # Gebruik HTML-body als beschikbaar, anders preview
    body_raw = email.get("body_html") or email.get("body_preview") or ""
    clean_body = re.sub(r"<[^>]+>", " ", body_raw)
    clean_body = re.sub(r"\s+", " ", clean_body).strip()[:2000]

    context = (
        f"ONTVANGEN E-MAIL:\n"
        f"Van: {email['from_name']} <{email['from_email']}>\n"
        f"Onderwerp: {email['subject']}\n"
        f"Datum: {email['received_at'][:10]}\n\n"
        f"Inhoud:\n{clean_body}"
    )
    if instructions:
        context += f"\n\nINSTRUCTIES VOOR HET ANTWOORD:\n{instructions}"

    messages = [{"role": "user", "content": f"Schrijf een antwoord op deze e-mail:\n\n{context}"}]

    async for event in agent_service.run_agent(messages, _DRAFT_SYSTEM, use_tools=False):
        yield event

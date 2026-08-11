"""
Outlook / Microsoft Graph — wereldklasse e-mailbeheer.

Auth: MSAL Device Code Flow (geen client secret, werkt op localhost).
Setup in Azure portal:
  1. App registrations → New registration → naam "Agent OS"
  2. Supported account types: "Accounts in any organizational directory and personal Microsoft accounts"
  3. Redirect URI: Public client/native → https://login.microsoftonline.com/common/oauth2/nativeclient
  4. API permissions (Delegated): Mail.Read, Mail.ReadWrite, Mail.Send, User.Read,
     Calendars.ReadWrite (nodig zodra CALENDAR_BACKEND=outlook — zie
     domains/calendar/service_outlook.py, dat hergebruikt dezelfde login)
  5. Kopieer Application (client) ID → OUTLOOK_CLIENT_ID in .env

Flow:
  POST /api/outlook/auth/start   → {user_code, verification_uri}
  [User opent URL, voert code in]
  GET  /api/outlook/auth/status  → {status: "done", email, name}

Een token dat vóór het toevoegen van Calendars.ReadWrite is aangevraagd dekt
die scope niet met terugwerkende kracht — opnieuw device-code inloggen is dan
nodig (dezelfde `_clear_token_cache`/opnieuw-inloggen-route als elke
scope-uitbreiding).
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
# `agent_service.run_agent(...)` hieronder verwees naar een module die nergens
# werd geïmporteerd — triage_single()/draft_reply() gaven dus een NameError
# zodra iemand ze écht aanriep. Onopgemerkt gebleven omdat niets in de
# frontend deze endpoints aanroept (zie bridge/context.py's nieuwe
# ensure_suggested_replies, die dit pad als eerste echt gebruikt).
from ...shared import agent_runner as agent_service

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
    # Alleen nodig voor CALENDAR_BACKEND=outlook, maar altijd aanvragen: één
    # device-code-login moet zowel mail als agenda dekken (zie service_outlook.py) —
    # apart per functie inloggen is precies het soort dubbele login-vraag die
    # een "eigen omgeving"-gevoel ondermijnt.
    "https://graph.microsoft.com/Calendars.ReadWrite",
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

def own_addresses() -> set:
    """De adressen die 'ik' zijn. Alles wat hiervandaan komt is verzonden post,
    geen binnengekomen mail — en dat onderscheid was precies wat ontbrak."""
    adressen = set()
    cached = _load_token_cache()
    if cached and cached[1]:
        adressen.add(str(cached[1]).lower().strip())
    return {a for a in adressen if a}


async def sync_inbox(limit: int = 50) -> List[dict]:
    """Haal het postvak IN op en werk de lokale kopie bij.

    Bewust `/me/mailFolders/inbox/messages` en niet `/me/messages`: dat laatste
    is de héle mailbox — Verzonden items, Concepten, Archief, Ongewenst. Tot
    11 aug 2026 stond dat er, mét een hardgecodeerde `folder='inbox'` erachter,
    en daardoor stonden Vincents eigen outreach-mails als binnengekomen post in
    'wacht op jouw antwoord' (gemeten: 5 van de 7 items op de telefoon). De
    `folder` komt nu uit de opgehaalde map in plaats van uit een aanname.

    Twee dingen gebeuren er ná het ophalen, allebei omdat "wat het systeem denkt"
    en "wat er in de wereld is" uit elkaar liepen: de afzenderregels ruimen de
    verse mail op (rules.apply_all) en Verzonden items levert het bewijs dat er
    geantwoord is (_sync_sent_items) — dat laatste stond eerder alleen aan onze
    eigen verstuurknop, dus alles wat Vincent gewoon in Outlook beantwoordde
    bleef voor altijd open staan.
    """
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
    data = await _graph("GET", "/me/mailFolders/inbox/messages", token, params=params)
    messages = data.get("value", [])

    eigen = own_addresses()
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for m in messages:
            from_addr = (m.get("from") or {}).get("emailAddress", {})
            to_list = m.get("toRecipients") or []
            to_email = ", ".join(
                r.get("emailAddress", {}).get("address", "") for r in to_list[:3]
            )
            afzender = (from_addr.get("address") or "").lower()
            # Eigen mail die in het postvak IN belandt (cc/bcc naar jezelf) is
            # geen binnengekomen post: die hoort nooit om een antwoord te vragen.
            folder = "sent" if afzender and afzender in eigen and afzender not in to_email.lower() else "inbox"
            conn.execute("""
                INSERT INTO outlook_emails
                    (id, subject, from_email, from_name, to_email,
                     received_at, body_preview, is_read, folder,
                     importance, has_attachments, thread_id, synced_at)
                VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    is_read  = excluded.is_read,
                    folder   = excluded.folder,
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
                folder,
                m.get("importance", "normal"),
                1 if m.get("hasAttachments") else 0,
                m.get("conversationId", ""),
                now,
            ))

    # Afzenderregels vóór de triage: wat Vincent nooit meer wil zien hoeft geen
    # LLM-beoordeling te krijgen (zelfde volgorde-argument als de signaalpoort
    # in radar/quality.py — het budget ging op aan spul dat toch wegvalt).
    # Bewust over het héle postvak en niet alleen over verse mail: een regel
    # geldt met terugwerkende kracht, en dat moet ook waar zijn voor de
    # standaardregels en voor een regel die is toegevoegd terwijl de sync liep.
    # Een mail die een mens expliciet terugzette blijft staan (HANDMATIG_TERUG).
    try:
        from . import rules
        rules.apply_all()
    except Exception:  # noqa: BLE001
        log.warning("Afzenderregels toepassen na sync mislukt", exc_info=True)

    try:
        await _sync_sent_items(token)
    except Exception:  # noqa: BLE001
        log.warning("Verzonden items ophalen mislukt", exc_info=True)

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

    return list_emails_db(folder="inbox", limit=limit)


async def _sync_sent_items(token: str, limit: int = 100) -> int:
    """Beantwoord = waargenomen in Verzonden items, niet geclaimd door onszelf.

    `is_replied` werd op precies één plek gezet: `send_reply`, oftewel als
    Vincent vanuit Agent OS antwoordde. Alles wat hij gewoon in Outlook
    beantwoordde telde nooit mee, dus kon de achterstand alleen groeien en stond
    er "0% beantwoord (7d)" op de telefoon — een cijfer dat per constructie
    nooit iets anders kón worden. Een mail geldt als beantwoord zodra er in
    dezelfde conversatie iets is verstuurd ná binnenkomst.
    """
    params = {
        "$orderby": "sentDateTime desc",
        "$top": str(min(limit, 100)),
        "$select": "id,conversationId,sentDateTime",
    }
    data = await _graph("GET", "/me/mailFolders/sentitems/messages", token, params=params)
    verstuurd = data.get("value", [])
    if not verstuurd:
        return 0

    geraakt = 0
    with get_conn() as conn:
        for m in verstuurd:
            conv = m.get("conversationId") or ""
            sent_at = m.get("sentDateTime") or ""
            if not conv or not sent_at:
                continue
            cur = conn.execute(
                "UPDATE outlook_emails SET is_replied = 1, replied_at = ? "
                "WHERE thread_id = ? AND folder = 'inbox' AND is_replied = 0 "
                "  AND received_at <= ?",
                (sent_at, conv, sent_at),
            )
            geraakt += cur.rowcount or 0
    return geraakt


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
    lead = find_lead_by_email(from_email) if from_email else None

    return {**data, "body_html": body_html, "linked_lead": lead}


def find_lead_by_email(email: str) -> Optional[dict]:
    if not email:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, org_name, website, status, score FROM leads WHERE email = ? LIMIT 1",
            (email.lower(),),
        ).fetchone()
    return dict(row) if row else None


def lookup_contact(email: str) -> dict:
    """Wat weten we deterministisch (geen LLM) over dit adres — voor de
    'waar moet je op letten'-regel bij een agenda-afspraak of urgente mail.

    Drie deterministische signalen, zelfde queryidioom als build_mail() in
    bridge/context.py (from_email/is_replied/received_at): is dit een lead
    (en welke funnel-status), ligt er nog een onbeantwoorde mail van hen, en
    wanneer hoorden we voor het laatst iets. Puur data — geen oordeel — zodat
    de aanroeper (bridge/context.py) bepaalt wat de moeite van het melden waard is.
    """
    email = (email or "").lower().strip()
    if not email:
        return {"lead": None, "open_email": None, "last_heard_from": None}

    lead = find_lead_by_email(email)

    with get_conn() as conn:
        open_row = conn.execute(
            "SELECT subject, received_at FROM outlook_emails "
            "WHERE folder='inbox' AND from_email = ? AND is_replied = 0 "
            "ORDER BY received_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        last_row = conn.execute(
            "SELECT received_at FROM outlook_emails "
            "WHERE folder='inbox' AND from_email = ? "
            "ORDER BY received_at DESC LIMIT 1",
            (email,),
        ).fetchone()

    def _days_ago(iso: Optional[str]) -> Optional[int]:
        if not iso:
            return None
        try:
            dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).days
        except (ValueError, TypeError):
            return None

    open_email = None
    if open_row:
        open_email = {"subject": open_row["subject"], "days": _days_ago(open_row["received_at"])}

    return {
        "lead": lead,
        "open_email": open_email,
        "last_heard_from": _days_ago(last_row["received_at"]) if last_row else None,
    }


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


def text_to_html(text: str) -> str:
    """Platte tekst (zoals een LLM-concept, `\\n\\n`-alinea's) naar minimale
    HTML. Graph's send/reply verwacht `contentType: HTML` — zonder deze stap
    verdwijnen alle regel- en alinea-einden in één aaneengeplakte alinea bij
    de ontvanger, want een browser negeert kale `\\n`'s in HTML."""
    import html as html_lib
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "".join(
        f"<p>{html_lib.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
    )


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


# ── Handelingen op één mailregel ──────────────────────────────────────────────
#
# Een regel in het postvak kon tot 11 aug 2026 precies één ding: opengaan als er
# toevallig een conceptantwoord onder lag. Zeven regels, één handeling. Dit zijn
# de andere twee, en ze zijn allebei lokaal én omkeerbaar: er wordt niets in de
# échte mailbox verplaatst of verwijderd. Dat is een bewuste grens — het scherm
# beslist wat jóu bereikt, niet wat Microsoft met je post doet.

def block_sender(email_id: str, *, scope: str = "adres", action: str = "spam",
                 reason: str = "") -> dict:
    """"Nooit meer van deze afzender" — de knop achter de spam-actie.

    Maakt een afzenderregel van de mail waarop getikt is en past die meteen toe
    op alles wat er al ligt; de teruggegeven `applied` is wat de knop moet
    melden ("14 mails opgeruimd"). Een regel die pas werkt bij de volgende
    binnenkomende mail is geen filter maar een belofte.
    """
    from . import rules

    email = get_email_db(email_id)
    if not email:
        raise ValueError("E-mail niet gevonden")
    afzender = (email.get("from_email") or "").strip()
    if not afzender:
        raise ValueError("Deze mail heeft geen afzenderadres om op te filteren")

    rule = rules.add_rule(
        afzender, scope=scope, action=action, source="mens",
        reason=reason or (f"geblokkeerd door jou op {datetime.now(timezone.utc).date()} "
                          f"vanaf '{(email.get('subject') or '')[:60]}'"),
    )
    return {"rule": rule, "applied": rule.get("applied", 0),
            "pattern": rule["pattern"], "scope": rule["scope"]}


def archive_email(email_id: str) -> dict:
    """Deze mail hoeft niets van je — maar de afzender blijft welkom.

    Bewust géén regel: 'ik ben klaar met dit bericht' en 'ik wil deze afzender
    nooit meer' zijn twee verschillende besluiten, en ze op één knop leggen is
    hoe je per ongeluk een klant blokkeert.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE outlook_emails SET triage_label='archief', priority=0, is_read=1, "
            "       filter_reason='handmatig gearchiveerd', triaged_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), email_id),
        )
    if not cur.rowcount:
        raise ValueError("E-mail niet gevonden")
    return {"success": True}


def restore_email(email_id: str) -> dict:
    """Terug in het postvak: label leeg, dus de triage beoordeelt hem opnieuw.

    Een gegokt label zou een mail van vier weken geleden vandaag als urgent
    kunnen terugzetten; opnieuw laten beoordelen is het enige eerlijke antwoord.
    De afzenderregel die hem wegnam blijft staan — die trek je apart in, zodat
    "deze ene mail toch" niet stilzwijgend het hele filter opent.
    """
    from . import rules

    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE outlook_emails SET triage_label='', priority=50, "
            "       filter_reason=?, filter_rule_id=NULL, triaged_at='' WHERE id=?",
            (rules.HANDMATIG_TERUG, email_id),
        )
    if not cur.rowcount:
        raise ValueError("E-mail niet gevonden")
    return {"success": True}


def dismiss_suggested_reply(email_id: str) -> None:
    """Vincent wees het voorgestelde antwoord af — de mail blijft in Postvak/
    Besluiten staan (urgent, onbeantwoord), maar `ensure_suggested_replies`
    genereert er niet stilzwijgend opnieuw een; dat zou een afwijzing zonder
    reden negeren en telkens hetzelfde concept terugbrengen."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE outlook_emails SET suggested_reply_dismissed = 1 WHERE id = ?",
            (email_id,),
        )


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


# De triage kent al vijf labels (urgent/actie/wacht/info/archief, zie
# _TRIAGE_SYSTEM); dit is puur een presentatie-groepering erbovenop, geen
# nieuwe classificatie. 'archief' (nieuwsbrief/spam/notificatie) hoort hier
# nooit in — dat is precies het spul dat een gesorteerde inbox moet wegfilteren.
_SORT_BUCKETS = {
    "needs_reply": ("urgent", "actie"),
    "waiting": ("wacht",),
    "fyi": ("info",),
}


def list_sorted_db(limit_per_bucket: int = 20) -> dict:
    """Inbox gegroepeerd naar wat hij van jou nodig heeft — needs_reply/fyi/waiting.

    Alleen `folder='inbox'`; 'needs_reply' toont enkel wat nog niet beantwoord
    is (anders blijft een afgehandelde mail voor altijd in de lijst staan).
    Ongetrieerde mail (triage_label='') zit in geen van de buckets — die telt
    apart mee zodat "leeg" niet als "niets te doen" leest terwijl er nog een
    triage-achterstand is (zelfde les als scheduler_runs: stilte ≠ rust).
    """
    with get_conn() as conn:
        buckets = {}
        for name, labels in _SORT_BUCKETS.items():
            placeholders = ",".join("?" for _ in labels)
            clause = f"folder='inbox' AND triage_label IN ({placeholders})"
            params = list(labels)
            if name != "fyi":
                clause += " AND is_replied=0"
            rows = conn.execute(
                f"SELECT id, subject, from_name, from_email, received_at, priority, "
                f"       triage_label, ai_summary, ai_action, suggested_reply, is_read "
                f"FROM outlook_emails WHERE {clause} "
                f"ORDER BY priority DESC, received_at DESC LIMIT ?",
                params + [limit_per_bucket],
            ).fetchall()
            buckets[name] = [dict(r) for r in rows]

        untriaged = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails WHERE folder='inbox' AND triage_label=''"
        ).fetchone()["c"]
        # Weggehouden door een afzenderregel. Staat als eigen getal in de payload
        # en telt nergens in mee: een filter dat je niet kunt zien werken is niet
        # te beoordelen, maar het hoort ook niet in het cijfer dat om een
        # handeling vraagt.
        gefilterd = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails "
            "WHERE folder='inbox' AND filter_rule_id IS NOT NULL"
        ).fetchone()["c"]

    return {**buckets, "untriaged": untriaged, "filtered": gefilterd}


def get_email_db(email_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM outlook_emails WHERE id = ?", (email_id,)
        ).fetchone()
    return dict(row) if row else None


def get_stats() -> dict:
    """Tellingen over het postvak IN — en alleen daarover.

    Alle drie de tellers stonden op de volledige tabel: verzonden post, spam en
    weggefilterde ruis telden mee. Zo werd "121 open" het grootste getal op het
    scherm terwijl geen enkele handeling in dat scherm het kleiner maakte, en
    stond er "106 nog niet getrieerd" over mail die nooit getrieerd hóéfde te
    worden. Een getal dat om een handeling vraagt telt alleen wat een handeling
    verandert; de rest staat er apart naast (`filtered`, `sent`).
    """
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails WHERE folder='inbox'"
        ).fetchone()["c"]
        unread = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails "
            "WHERE folder='inbox' AND is_read=0 AND filter_rule_id IS NULL "
            "  AND triage_label NOT IN ('spam','archief')"
        ).fetchone()["c"]
        untriaged = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails "
            "WHERE folder='inbox' AND triage_label='' AND filter_rule_id IS NULL"
        ).fetchone()["c"]
        filtered = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails "
            "WHERE folder='inbox' AND filter_rule_id IS NOT NULL"
        ).fetchone()["c"]
        sent = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails WHERE folder='sent'"
        ).fetchone()["c"]
        by_label = conn.execute(
            "SELECT triage_label, COUNT(*) c FROM outlook_emails "
            "WHERE folder='inbox' AND triage_label!='' GROUP BY triage_label"
        ).fetchall()

    return {
        "total": total,
        "unread": unread,
        "untriaged": untriaged,
        "filtered": filtered,
        "sent": sent,
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

    # Verzonden post triëren we niet: er is niemand die op ons antwoord wacht.
    if (email.get("folder") or "inbox") != "inbox":
        yield {"type": "triage_skipped", "email_id": email_id, "reason": "geen binnengekomen mail"}
        return

    # Deterministische afzenderregels vóór de LLM-triage — webshops,
    # vacaturesites, digests, systeemmeldingen en alles wat Vincent zelf heeft
    # geblokkeerd. Bewust vóór het model: het budget ging eerder op aan spul dat
    # toch wegvalt (666 van 1.676 signalen kregen nooit een oordeel, zelfde
    # patroon als de signaalpoort in radar/quality.py). De regels leven in
    # `mail_sender_rules` en niet in code, zodat "nooit meer van deze afzender"
    # een knop is en geen commit — zie rules.py.
    from . import rules as _rules
    oordeel = _rules.verdict(email.get("from_email", ""))
    if oordeel:
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            conn.execute(
                "UPDATE outlook_emails SET triage_label=?, priority=0, "
                "ai_summary=?, filter_reason=?, filter_rule_id=?, triaged_at=? WHERE id=?",
                (oordeel["label"], oordeel["reason"], oordeel["reason"],
                 oordeel["rule_id"], now, email_id),
            )
            conn.execute(
                "UPDATE mail_sender_rules SET hits = hits + 1, last_hit_at = ? WHERE id = ?",
                (now, oordeel["rule_id"]),
            )
        yield {"type": "triage_done", "email_id": email_id, "label": oordeel["label"],
               "priority": 0, "summary": oordeel["reason"], "action": "",
               "reply_hint": "", "auto_archived": True}
        return

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
            "WHERE folder='inbox' AND triage_label='' AND filter_rule_id IS NULL "
            "ORDER BY received_at DESC LIMIT ?",
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


async def run_postvak_sync(triage_limit: int = 40) -> dict:
    """De scheduler-ronde: ophalen, regels toepassen, achterstand wegtriëren.

    Hier stond niets. Er was géén JobSpec voor Vincents eigen postvak — alleen
    de helpdesk-mailboxen hadden er een — dus werd er alleen gesynchroniseerd
    als iemand op "Ophalen" tikte of vanaf de telefoon een commando gaf, en dat
    laatste triageerde er 15 per keer. Gemeten op 11 aug 2026: de laatste sync
    was van de dag ervóór, en 106 mails stonden ongetrieerd. De gele balk "106
    mails nog niet getrieerd" was daarmee geen waarschuwing maar een toestand —
    en een waarschuwing die altijd aan staat leert een mens hem te negeren.

    De triage-lus stopt bij een lege achterstand óf bij een uitgeput LLM-budget;
    dat laatste is geen fout maar de rem die overal in dit systeem geldt. De
    sync zelf (Graph) draait daar los van, want ophalen kost geen tokens.
    """
    from ...shared.failures import describe_exception, note_success, should_escalate
    from ...shared.outcomes import log_outcome, llm_budget_exceeded

    faalsleutel = "outlook_sync"
    # `is_authenticated()` kijkt naar het gecachete account, niet naar een
    # bruikbaar token. Op 11 aug 2026 stond het op True terwijl Microsoft het
    # grant al had ingetrokken (AADSTS50173, uitgegeven 9 juli): de sync gooide
    # dan een RuntimeError die pas na drie ronden zou escaleren, en tot die tijd
    # zag een stilstaand postvak eruit als een rustige dag. Een ingetrokken
    # sessie is een mens-alleen oorzaak — daar helpt wachten niet, dus meldt hij
    # zich meteen mét de stap die hem oplost.
    if not is_authenticated() or not get_valid_token():
        if should_escalate(faalsleutel, PermissionError("Outlook niet ingelogd")):
            log_outcome(
                "Postvak", "postvak-sync",
                "Het postvak wordt niet meer opgehaald: de Outlook-sessie is verlopen of "
                "ingetrokken. Er komt geen nieuwe mail binnen en niets wordt getrieerd.",
                next_step="Log opnieuw in via de Postvak-tab → 'Koppel Outlook-account' "
                          "(device-code). Daarna loopt de sync vanzelf weer mee.",
                status="error",
            )
        return {"success": False, "error": "geen geldige Outlook-sessie"}

    try:
        mails = await sync_inbox(limit=50)
    except Exception as e:  # noqa: BLE001
        msg = f"Postvak ophalen mislukt: {describe_exception(e)}"
        log.warning(msg)
        if should_escalate(faalsleutel, e):
            log_outcome("Postvak", "postvak-sync", msg,
                        next_step="Controleer de Outlook-koppeling (Postvak-tab → opnieuw "
                                  "inloggen) en draai POST /api/outlook/sync.",
                        status="error")
        return {"success": False, "error": msg}
    note_success(faalsleutel)

    getrieerd = 0
    if not llm_budget_exceeded():
        async for event in batch_triage(limit=triage_limit):
            if event.get("type") == "batch_done":
                getrieerd = event.get("total", 0)

    return {"success": True, "synced": len(mails), "triaged": getrieerd,
            "backlog": get_stats().get("untriaged", 0)}


def _draft_messages(email: dict, instructions: str = "") -> List[dict]:
    """Bouwt de promptcontext voor een conceptantwoord — gedeeld door de
    streamende route (draft_reply, mens wacht erop) en de batchroute
    (ensure_suggested_replies, draait vooraf zonder toeschouwer)."""
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
    return [{"role": "user", "content": f"Schrijf een antwoord op deze e-mail:\n\n{context}"}]


async def draft_reply(email_id: str, instructions: str = "") -> AsyncGenerator[dict, None]:
    """AI schrijft een concept-antwoord via SSE."""
    email = get_email_db(email_id)
    if not email:
        yield {"type": "error", "message": "E-mail niet gevonden"}
        return

    messages = _draft_messages(email, instructions)
    async for event in agent_service.run_agent(messages, _DRAFT_SYSTEM, use_tools=False):
        yield event


async def _generate_draft_text(email_id: str, instructions: str = "") -> Optional[str]:
    """Niet-streamende variant voor batchgebruik (ensure_suggested_replies):
    zelfde prompt als draft_reply(), maar geeft het volledige antwoord in één
    keer terug in plaats van SSE-events — er is hier niemand die meekijkt."""
    email = get_email_db(email_id)
    if not email:
        return None
    messages = _draft_messages(email, instructions)
    full_text = ""
    async for event in agent_service.run_agent(
        messages, _DRAFT_SYSTEM, use_tools=False, purpose="mail-suggested-reply",
    ):
        if event.get("type") == "text":
            full_text += event["text"]
    return full_text.strip() or None


async def ensure_suggested_replies(limit: int = 3) -> int:
    """Genereert vast een conceptantwoord voor de top-`limit` urgente mails
    die er nog geen hebben — zodat de telefoon (bridge/context.py build_mail)
    het al klaar kan tonen i.p.v. pas na een tik-en-wacht-3-minuten (de bridge
    is een pull-model, er is geen synchroon 'genereer nu'-pad naar de phone).

    Eenmalig per mail (WHERE suggested_reply='' AND is_replied=0): budget kost
    geld, dus geen concept opnieuw maken voor een mail die al beantwoord is of
    er al een heeft. Budget-/quota-bewaakt zoals elke autonome LLM-route in dit
    systeem (content_improver, biweekly-content) — stil overslaan, geen crash,
    geen kaart: dit is geen taak die een mens hoeft te zien mislukken, de
    e-mail blijft gewoon zonder concept staan tot de volgende sync.
    """
    from ...shared.outcomes import require_llm_budget, BudgetExceeded

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM outlook_emails "
            "WHERE folder='inbox' AND is_replied=0 AND priority>=70 "
            "AND filter_rule_id IS NULL "
            "AND (suggested_reply IS NULL OR suggested_reply='') "
            "AND suggested_reply_dismissed=0 "
            "ORDER BY priority DESC, received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    made = 0
    for row in rows:
        try:
            require_llm_budget("mail-suggested-reply")
        except BudgetExceeded:
            break
        try:
            text = await _generate_draft_text(row["id"])
        except Exception:  # noqa: BLE001
            log.warning("ensure_suggested_replies: genereren mislukt voor %s", row["id"], exc_info=True)
            continue
        if not text:
            continue
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            conn.execute(
                "UPDATE outlook_emails SET suggested_reply=?, suggested_reply_at=? WHERE id=?",
                (text, now, row["id"]),
            )
        made += 1
    return made

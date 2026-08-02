"""Microsoft Graph connector voor Office365/Exchange-mailboxen.

Waarom dit bestand bestaat
──────────────────────────
Microsoft heeft basic authentication (POP3/IMAP/SMTP met gewoon wachtwoord)
voor Exchange Online sinds 1 okt 2022 uitgezet. De oude `inbox.py` (POP3) en
`service.py` (SMTP) authenticeren daarmee niet meer tegen
outlook.office365.com. Deze module praat via de Microsoft Graph API met
OAuth2 **client_credentials** (daemon-flow): de Entra-app "Hermes" krijgt
applicatie-machtigingen `Mail.ReadWrite` + `Mail.Send` en mag namens een
specifieke mailbox (UPN) mail lezen/versturen zónder dat er een
accountwachtwoord of interactieve login nodig is. Dat is de stabielste route
voor een 24/7 server-agent.

C credentials (tenant_id, client_id, client_secret, mailbox UPN) horen in de
`mailboxes`-rij (kolommen hieronder), NIET in .env met een accountwachtwoord.
De mail-service detecteert `auth_method='graph'` en roept deze module aan in
plaats van de POP3/SMTP-paden.

Scope-vereisten in Entra (app "Hermes"):
  - Toepassingsmachtigingen: Mail.ReadWrite, Mail.Send
  - Beheerdersinstemming (paarse knop) verleend voor de tenant
  - Het mailbox-account moet een gelicenseerde Exchange Online-licentie hebben.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from email.header import decode_header as _decode_header
from email.utils import parseaddr
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# De spam/auto-submitted filters horen thuis in inbox.py; we hergebruiken ze
# hier zodat de Graph-flow exact dezelfde classificatie-voorfilters heeft als
# de POP3-flow (geen reply-loops op out-of-office/bounces, geen nieuwsbrieven).
from .inbox import _should_ignore, _looks_like_newsletter  # noqa: E402,F401


def _is_auto_submitted(msg: Dict) -> bool:
    """Graph kent geen Auto-Submitted-header zoals POP3; we deduceren het uit
    de aanwezigheid van internetMessageHeaders of bekende markers."""
    headers = msg.get("internetMessageHeaders") or msg.get("headers") or []
    for h in headers:
        naam = (h.get("name") or "").lower()
        if naam == "auto-submitted":
            return True
        if naam == "precedence" and str(h.get("value") or "").strip().lower() in (
                "bulk", "list", "junk"):
            return True
    subj = (msg.get("subject") or "").lower()
    if any(k in subj for k in ("out of office", "automatisch antwoord", "niet op kantoor")):
        return True
    return False

_TOKEN_URL_TMPL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


def _decode(s: Optional[str]) -> str:
    if not s:
        return ""
    out = []
    for part, enc in _decode_header(s):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _post_form(url: str, data: Dict[str, str], timeout: int = 25) -> Dict:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Graph token HTTP {e.code}: {body}") from e


def get_access_token(tenant_id: str, client_id: str, client_secret: str,
                     timeout: int = 25) -> str:
    """Haal een OAuth2 access-token op via client_credentials."""
    token = _post_form(
        _TOKEN_URL_TMPL.format(tenant=tenant_id),
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": _SCOPE,
        },
        timeout=timeout,
    )
    if "access_token" not in token:
        raise RuntimeError(f"Geen access_token ontvangen: {token}")
    return token["access_token"]


def _graph_get(url: str, access_token: str, timeout: int = 25) -> Dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Graph GET {url} HTTP {e.code}: {body}") from e


def _graph_post(url: str, access_token: str, payload: Dict, timeout: int = 25) -> Dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Graph POST {url} HTTP {e.code}: {body}") from e


def fetch_messages(mailbox: Dict, top: int = 25, timeout: int = 30) -> List[Dict]:
    """Lees de laatste `top` berichten uit de inbox van de mailbox-UPN.

    Geeft dezelfde dict-structuur terug als `inbox.fetch_new` nodig heeft
    (id/uidl, from_addr, from_name, subject, body_text, message_id,
    in_reply_to, references) zodat de bestaande classificatie/drafter
    ongewijzigd kan blijven.
    """
    token = get_access_token(
        mailbox["graph_tenant_id"], mailbox["graph_client_id"],
        mailbox["graph_client_secret"], timeout=timeout,
    )
    upn = mailbox["graph_user_upn"] or mailbox["address"]
    # internetMessageHeaders MOET expliciet opgevraagd worden: Graph levert het
    # niet in de standaard-selectie. Zonder dit veld zag `_is_auto_submitted`
    # altijd een lege lijst en kon de Graph-flow bulk/auto-mail per definitie
    # niet herkennen — de POP3-flow deed dat wél, dus hetzelfde bericht kreeg
    # een ander oordeel afhankelijk van het transportpad (1 aug 2026).
    select = ("id,subject,from,bodyPreview,body,receivedDateTime,"
              "internetMessageId,replyTo,conversationId,isRead,"
              "internetMessageHeaders")
    url = (f"{_GRAPH_BASE}/users/{urllib.parse.quote(upn)}/mailFolders('inbox')/messages"
           f"?$top={top}&$orderby=receivedDateTime%20desc&$select={urllib.parse.quote(select)}")
    data = _graph_get(url, token, timeout=timeout)
    out: List[Dict] = []
    for m in data.get("value", []):
        sender = (m.get("from") or {}).get("emailAddress") or {}
        raw_from = sender.get("address") or ""
        from_name = sender.get("name") or ""
        subject = m.get("subject") or ""
        body = _body_from_graph(m)
        out.append({
            # Graph `id` is een stabiele, unieke bericht-ID — dat is onze UIDL
            # voor dedupe (vervangt de POP3-UIDL).
            "uidl": m.get("id"),
            "from_addr": raw_from,
            "from_name": from_name,
            "subject": subject,
            "body_text": body,
            "message_id": m.get("internetMessageId"),
            "in_reply_to": (m.get("inReplyTo") or {}).get("id") if isinstance(m.get("inReplyTo"), dict) else m.get("inReplyTo"),
            "references": "",
            "received_at": m.get("receivedDateTime"),
            "is_read": bool(m.get("isRead")),
            "graph_id": m.get("id"),
            # Ruwe headers meegeven zodat de classificatie hetzelfde bewijs
            # heeft als de POP3-flow (List-Unsubscribe, Precedence, ...).
            "headers": m.get("internetMessageHeaders") or [],
        })
    return out


def _body_from_graph(msg: Dict) -> str:
    body = msg.get("body") or {}
    if body.get("contentType", "").lower() == "html":
        from .inbox import _strip_html
        return _strip_html(body.get("content", ""))
    return body.get("content", "") or msg.get("bodyPreview") or ""


def send_message(mailbox: Dict, to_addr: str, subject: str, text: str,
                 from_display: str = "", in_reply_to: str = "",
                 references: str = "", timeout: int = 30) -> bool:
    """Verstuur een mail namens de mailbox-UPN via Graph /sendMail."""
    token = get_access_token(
        mailbox["graph_tenant_id"], mailbox["graph_client_id"],
        mailbox["graph_client_secret"], timeout=timeout,
    )
    upn = mailbox["graph_user_upn"] or mailbox["address"]
    # from_display optioneel; Graph stuurt altijd vanuit de geautoriseerde UPN.
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": to_addr}}],
        },
        "saveToSentItems": True,
    }
    if in_reply_to or references:
        payload["message"]["internetMessageHeaders"] = []
        if in_reply_to:
            payload["message"]["internetMessageHeaders"].append(
                {"name": "In-Reply-To", "value": in_reply_to})
        if references:
            payload["message"]["internetMessageHeaders"].append(
                {"name": "References", "value": references})
    url = f"{_GRAPH_BASE}/users/{urllib.parse.quote(upn)}/sendMail"
    _graph_post(url, token, payload, timeout=timeout)
    return True


def is_configured(mailbox: Dict) -> bool:
    """True als deze mailbox voldoende Graph-credentials heeft."""
    return bool(
        mailbox.get("auth_method") == "graph"
        and mailbox.get("graph_tenant_id")
        and mailbox.get("graph_client_id")
        and mailbox.get("graph_client_secret")
    )

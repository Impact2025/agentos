"""
Bridge-acties — de whitelist van besluiten die de cloud-companion mag laten
uitvoeren. Elk besluit loopt door exact dezelfde servicefuncties als de knoppen
in de lokale UI; de cloud kan dus nooit een gate omzeilen of een willekeurig
endpoint aanroepen. Onbekende (kind, action)-combinaties worden hard geweigerd.

Een decision uit Neon ziet er zo uit:
    {"id": 7, "item_kind": "mail", "item_id": "12", "action": "send",
     "payload": {...}}

Retour: (ok: bool, message: str) — gaat terug naar de cloud zodat de telefoon
toont wat er met het besluit gebeurde ("verstuurd" / "geweigerd: ...").
"""
import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


async def _content_approve(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..publish import content_pipeline
    channels = None
    if payload.get("social") is False:
        channels = []
    elif "channels" in payload:
        channels = [str(c).strip().lower() for c in (payload.get("channels") or [])]
    result = await content_pipeline.approve_and_publish(item_id, social_channels=channels)
    return True, f"Gepubliceerd: {str(result)[:200]}"


async def _content_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..publish import content_pipeline
    content_pipeline.reject_job(item_id)
    return True, "Afgewezen"


async def _mail_send(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..mail import service as mail
    ok = mail.send_reply(int(item_id))
    return (True, "Verstuurd") if ok else (False, "Versturen mislukt (zie logboek)")


async def _mail_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..mail import service as mail
    mail.reject_reply(int(item_id))
    return True, "Afgewezen"


async def _mail_edit(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..mail import service as mail
    text = (payload.get("text") or "").strip()
    if not text:
        return False, "Lege tekst — bewerking genegeerd"
    mail.edit_reply(int(item_id), text)
    return True, "Bewerking opgeslagen (blijft ter review staan)"


async def _outreach_approve(item_id: str, payload: Dict) -> Tuple[bool, str]:
    # Bewust via de routerfunctie: die bevat de volledige verzendketen
    # (adres-validatie, Outlook-check, funnel-tijdstempel, uitkomst-kaart).
    from fastapi import HTTPException
    from ..prospecting.router import OutreachApproveRequest, approve_outreach
    try:
        result = await approve_outreach(item_id, OutreachApproveRequest(
            subject=payload.get("subject") or "", body=payload.get("body") or ""))
        return True, f"Verstuurd aan {result.get('to', '?')}"
    except HTTPException as e:
        return False, str(e.detail)[:300]


async def _outreach_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..prospecting.router import dismiss_outreach
    dismiss_outreach(item_id)
    return True, "Lead afgewezen (→ lost)"


async def _calendar_approve(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..calendar import agent as calendar_agent
    result = calendar_agent.approve_proposal(int(item_id))
    if result.get("ok"):
        return True, f"Geboekt: {result.get('link') or result.get('event_id') or 'ok'}"
    return False, result.get("error") or "Boeken mislukt"


async def _calendar_reject(item_id: str, payload: Dict) -> Tuple[bool, str]:
    from ..calendar import agent as calendar_agent
    calendar_agent.reject_proposal(int(item_id))
    return True, "Voorstel afgewezen"


async def _dismiss(kind: str, item_id: str) -> Tuple[bool, str]:
    from ..action_center import service as ac
    ac.dismiss(kind, item_id)
    return True, "Weggeklikt"


# (item_kind = dismiss_kind uit het Actiecentrum, action) → uitvoerder.
_HANDLERS = {
    ("content", "approve"): _content_approve,
    ("content", "reject"): _content_reject,
    ("mail", "send"): _mail_send,
    ("mail", "reject"): _mail_reject,
    ("mail", "edit"): _mail_edit,
    ("outreach", "approve"): _outreach_approve,
    ("outreach", "reject"): _outreach_reject,
    ("calendar", "approve"): _calendar_approve,
    ("calendar", "reject"): _calendar_reject,
}

# Weggeklikken mag voor elk item-type dat het Actiecentrum kent.
_DISMISSABLE = {"content", "mail", "outreach", "calendar", "goal", "task",
                "error", "vacancies", "leads", "linkbuilding"}


async def apply_decision(decision: Dict[str, Any]) -> Tuple[bool, str]:
    kind = str(decision.get("item_kind") or "")
    action = str(decision.get("action") or "")
    item_id = str(decision.get("item_id") or "")
    payload = decision.get("payload") or {}
    if not kind or not action or not item_id:
        return False, "Onvolledig besluit (kind/action/id ontbreekt)"

    if action == "dismiss":
        if kind not in _DISMISSABLE:
            return False, f"Onbekend item-type '{kind}' voor dismiss"
        return await _dismiss(kind, item_id)

    handler = _HANDLERS.get((kind, action))
    if not handler:
        return False, f"Actie '{action}' op '{kind}' staat niet op de whitelist"
    try:
        return await handler(item_id, payload)
    except ValueError as e:
        return False, str(e)[:300]
    except Exception as e:
        logger.exception("Bridge-besluit mislukt: %s/%s op %s", kind, action, item_id)
        return False, f"Fout bij uitvoeren: {str(e)[:250]}"

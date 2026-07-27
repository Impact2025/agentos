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
    # Een besluit van onderweg kan de lokale machine inhalen: Vincent keurt
    # op zijn telefoon iets goed dat hij (of een andere sync) intussen al
    # lokaal publiceerde. De job staat dan al op 'published' — dat is het
    # bedoelde eindresultaat, geen fout. Zonder deze check gooit
    # approve_and_publish een ValueError ('niet pending_review/publish_failed')
    # en meldt de Bridge dat elke keer als 'remote_decision_failed', terwijl er
    # niets stuk is.
    job = content_pipeline.get_job(item_id)
    if job and job.get("status") == "published":
        return True, "Was al gepubliceerd (geen actie nodig)"
    # Opt-in, net als in de UI: zonder expliciete channels-lijst alleen website.
    channels = []
    if payload.get("social") is not False and "channels" in payload:
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


# ── Commando's: werk aanzwengelen vanaf de telefoon ─────────────────────────
#
# Tot nu toe was de bridge puur reactief: je kon afhandelen wat de machine al
# had klaargezet, maar niets in gang zetten. Dat is het verschil tussen een
# afstandsbediening en een assistent. Deze commando's starten agents — en zijn
# veilig om exact dezelfde reden als Iris' eigen hendels: het resultaat landt
# ALTIJD in een review-gate (Wachtrij / outreach_review / voorstel), nooit
# extern. Ze hergebruiken bewust `iris/actions.py`, inclusief de klemmen en de
# dedupe van max één run per dag per doelwit — anders kan een telefoon in een
# broekzak de LLM-rekening leegtrekken.

async def _cmd_content_run(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    site = str(payload.get("site") or "").strip()
    if not site:
        return False, "Geef een site op (bv. 'weareimpact')"
    result = await iris_actions.content_run(site, payload.get("count") or 1,
                                            "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, f"Contentmotor voor '{site}' mislukt of site onbekend")


async def _cmd_outreach_run(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    result = await iris_actions.outreach_run(payload.get("count") or 5,
                                             "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, "Outreach-batch mislukt (zie logboek)")


async def _cmd_seo_refresh(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    site = str(payload.get("site") or "").strip()
    if not site:
        return False, "Geef een site op"
    result = await iris_actions.seo_refresh(site, payload.get("count") or 1,
                                            "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, f"SEO-refresh voor '{site}' mislukt")


async def _cmd_lead_search(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    result = await iris_actions.lead_search_run(
        payload.get("queries") or [], "Aangevraagd via Iris Remote",
        template=str(payload.get("template") or ""))
    return (True, result) if result else (False, "Lead-zoekactie mislukt (zie logboek)")


async def _cmd_linkbuilding_run(payload: Dict) -> Tuple[bool, str]:
    from ..iris import actions as iris_actions
    result = await iris_actions.linkbuilding_run(payload.get("count") or 5,
                                                 "Aangevraagd via Iris Remote")
    return (True, result) if result else (False, "Linkbuilding-batch mislukt (zie logboek)")


async def _cmd_mail_sync(payload: Dict) -> Tuple[bool, str]:
    """Postvak ophalen + triëren. Leest en beoordeelt; verstuurt niets."""
    from ..outlook import service as outlook
    if not outlook.is_authenticated():
        return False, "Outlook niet ingelogd — log opnieuw in via de Mail-tab"
    mails = await outlook.sync_inbox(limit=50)
    triaged = 0
    if payload.get("triage") is not False:
        async for event in outlook.batch_triage(limit=15):
            if event.get("type") == "batch_done":
                triaged = event.get("total", 0)
    return True, f"{len(mails)} mail(s) opgehaald, {triaged} getrieerd"


async def _cmd_helpdesk_run(payload: Dict) -> Tuple[bool, str]:
    """Helpdesk-mailboxen langsgaan: concepten schrijven, niets versturen."""
    from ..mail import service as mail
    import asyncio
    result = await asyncio.to_thread(mail.run_all_mailboxes)
    total = sum(v for v in result.values() if isinstance(v, int))
    return True, f"{total} nieuw(e) concept(en) klaargezet ter review"


async def _cmd_iris_briefing(payload: Dict) -> Tuple[bool, str]:
    from ..iris import service as iris
    report = await iris.run_morning_briefing()
    if not report:
        return False, "Briefing leverde niets op (zie logboek)"
    return True, f"Briefing van {report.get('report_date') or 'vandaag'} klaar"


async def _cmd_context_refresh(payload: Dict) -> Tuple[bool, str]:
    """Gooi de contextcache leeg zodat de eerstvolgende sync verse cijfers
    ophaalt — voor als je onderweg niet wilt wachten op de TTL."""
    from ...shared.database import get_conn
    keys = payload.get("sections") or ["mail", "agenda", "analytics", "seo"]
    with get_conn() as conn:
        for key in keys:
            conn.execute("DELETE FROM bridge_context_cache WHERE key = ?", (str(key),))
    return True, f"Context ververst bij de volgende sync ({', '.join(map(str, keys))})"


async def _cmd_digest(payload: Dict) -> Tuple[bool, str]:
    from ..action_center import digest
    await digest.run_daily_digest()
    return True, "Ochtendrapport gedraaid (gemaild als SMTP is ingesteld)"


# Commando's staan bewust in een eigen tabel: ze horen niet bij één item, en
# een tikfout mag nooit per ongeluk in de item-whitelist vallen.
_COMMANDS = {
    "content_run": _cmd_content_run,
    "outreach_run": _cmd_outreach_run,
    "seo_refresh": _cmd_seo_refresh,
    "lead_search": _cmd_lead_search,
    "linkbuilding_run": _cmd_linkbuilding_run,
    "mail_sync": _cmd_mail_sync,
    "helpdesk_run": _cmd_helpdesk_run,
    "iris_briefing": _cmd_iris_briefing,
    "context_refresh": _cmd_context_refresh,
    "digest": _cmd_digest,
}


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

# Weggeklikken mag voor elk item-type dat het Actiecentrum kent. `scheduler`
# hoorde er vanaf het begin bij te staan (build_inbox produceert die kaarten)
# maar ontbrak — een scheduler-fout was daardoor het enige item op de telefoon
# waarvan zelfs 'Wegklikken' een fout gaf.
_DISMISSABLE = {"content", "mail", "outreach", "calendar", "goal", "task",
                "error", "vacancies", "leads", "linkbuilding", "scheduler"}


async def apply_decision(decision: Dict[str, Any]) -> Tuple[bool, str]:
    kind = str(decision.get("item_kind") or "")
    action = str(decision.get("action") or "")
    item_id = str(decision.get("item_id") or "")
    payload = decision.get("payload") or {}

    # Commando's horen niet bij een item: kind='command', action=de opdracht.
    if kind == "command":
        handler = _COMMANDS.get(action)
        if not handler:
            return False, f"Onbekend commando '{action}'"
        try:
            return await handler(payload)
        except ValueError as e:
            return False, str(e)[:300]
        except Exception as e:  # noqa: BLE001
            logger.exception("Bridge-commando mislukt: %s", action)
            return False, f"Fout bij uitvoeren: {str(e)[:250]}"

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

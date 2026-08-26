"""
AI Leadership Lab-leads (weareimpact.nl/lab, 27 aug 2026, CIC Rotterdam).

Zelfde vorm als impact_leads.py hiernaast: de website pusht elke inschrijving
rechtstreeks naar de bridge (remote/api/bridge.js, op=workshop-lead) — dit
ontstaat buiten ImpactOS om, dus is er geen lokale rij om op te reageren
totdat een sync 'm ophaalt. De bezoeker krijgt zijn hand-outs al automatisch
van de website zelf (Next.js mailt dat direct); wat hier gebeurt is Iris'
toevoeging voor Vincent: wie is dit bedrijf/deze persoon, wat deed hij op de
site, en wat is een goed vervolg.

Draait mee in bridge/service.sync_once (dezelfde 3-minuten-cadans als de rest
van de bridge) — geen eigen scheduler-job. Publiceert of verstuurt nooit iets
naar de bezoeker: dit domein mailt uitsluitend Vincent.
"""
import asyncio
import logging
from typing import Any, Dict, List, Tuple

import httpx

from ...shared.config import BRIDGE_REMOTE_URL, BRIDGE_TOKEN
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0

# Zelfde overweging als impact_leads._GENERIC_MAIL_DOMAINS: op deze
# providers is het maildomein de aanbieder, niet het bedrijf.
_GENERIC_MAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "hotmail.nl", "outlook.com", "live.nl",
    "live.com", "icloud.com", "yahoo.com", "yahoo.nl", "ziggo.nl",
    "kpnmail.nl", "protonmail.com", "me.com", "outlook.nl",
}


def _base() -> str:
    return BRIDGE_REMOTE_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {BRIDGE_TOKEN}"}


def _company_domain(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    return "" if not domain or domain in _GENERIC_MAIL_DOMAINS else domain


def _enrich_sync(email: str, organisatie: str) -> Dict[str, Any]:
    """Synchrone netwerk-verrijking — via asyncio.to_thread aangeroepen, zie
    CLAUDE.md-les bij impact_leads.py. Faalt nooit hard."""
    from ..prospecting.service import LeadsService
    svc = LeadsService()
    domain = _company_domain(email)
    try:
        if domain:
            scraped = svc.scrape_and_enrich(f"https://{domain}", organisatie or domain)
            return {"website": f"https://{domain}", "scraped": scraped, "search_results": []}
        if organisatie:
            results = svc.search_web(organisatie, max_results=3)
            return {"website": "", "scraped": {}, "search_results": results}
    except Exception:
        logger.exception("Workshop-lead verrijking mislukt")
    return {"website": "", "scraped": {}, "search_results": []}


def _feiten_block(lead: Dict[str, Any]) -> str:
    """De harde feiten die de lead zelf invulde/deed — nooit
    LLM-afhankelijk, dus ook bruikbaar als Iris' analyse mislukt."""
    page_views = lead.get("page_views") or []
    bezocht = ", ".join(p.get("path", "?") for p in page_views[:10]) or "onbekend"
    return (
        f"- Naam: {lead.get('naam') or 'onbekend'}\n"
        f"- Organisatie: {lead.get('organisatie') or 'onbekend'}\n"
        f"- Rol: {lead.get('rol') or 'onbekend'}\n"
        f"- E-mail: {lead.get('email')}\n"
        f"- Bron: AI Leadership Lab (weareimpact.nl/lab), 27 augustus 2026, CIC Rotterdam\n"
        f"- Recent bezochte pagina's op weareimpact.nl: {bezocht}"
    )


async def _write_verslag(lead: Dict[str, Any], enrichment: Dict[str, Any]) -> str:
    """Iris' analyse: wie is dit, wat vertelt zijn gedrag op de site, en wat
    is het beste vervolg? Nooit verzinnen — ontbrekende feiten heten
    'onbekend'."""
    from ..chat import claude as claude_chat

    scraped = enrichment.get("scraped") or {}
    search = enrichment.get("search_results") or []

    context_lines: List[str] = []
    if enrichment.get("website"):
        context_lines.append(f"Website: {enrichment['website']}")
    if scraped.get("page_text"):
        context_lines.append(f"Paginainhoud (verkort): {scraped['page_text'][:2000]}")
    adres = " ".join(x for x in (scraped.get("address"), scraped.get("city")) if x)
    if adres:
        context_lines.append(f"Adres: {adres}")
    if scraped.get("kvk_number"):
        context_lines.append(f"KvK: {scraped['kvk_number']}")
    for r in search[:3]:
        context_lines.append(
            f"- {r.get('title', '')}: {(r.get('snippet') or '')[:300]} ({r.get('url', '')})"
        )
    context = "\n".join(context_lines) or "Geen aanvullende informatie gevonden."

    prompt = f"""Er heeft zich zojuist een lead aangemeld via de /lab-pagina van weareimpact.nl,
gekoppeld aan het AI Leadership Lab (WeAreImpact x Grantmaster, 27 augustus 2026, CIC Rotterdam).
WeAreImpact is Vincent van Munster's AI-adviesbureau voor het sociaal domein.

Wat de lead zelf invulde en deed op de site:
{_feiten_block(lead)}

Wat er over het bedrijf/de persoon op internet is gevonden:
{context}

Schrijf een kort verslag voor Vincent (Nederlands, markdown, max 200 woorden) met:
1. Wie dit is en wat het bedrijf doet (schrijf letterlijk "onbekend" als er niets is
   gevonden — verzin nooit een branche, grootte of functie die niet in de gevonden
   informatie staat)
2. Waarom deze lead relevant is, ook gezien welke pagina's hij bezocht
3. Een concrete aanbeveling voor het vervolg (bijv. bellen, mailen met welke insteek,
   of negeren als het duidelijk geen fit is)
Gebruik geen liggende streepjes of emoji's."""

    return await claude_chat.get_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="Je bent Iris, de manager-agent van WeAreImpact. Je analyseert "
                      "inbound leads voor Vincent: kort, concreet, nooit verzonnen feiten.",
        purpose="workshop_lead",
    )


async def _process_one(lead: Dict[str, Any]) -> Tuple[bool, str]:
    """Zelfde tweedeling als impact_leads._process_one: vastleggen in de
    funnel is deterministisch en moet altijd lukken; Iris' verslag + de mail
    zijn verrijking erbovenop en mogen de lead nooit kwijtraken bij een
    LLM-storing."""
    email = lead.get("email") or ""
    naam_of_org = lead.get("organisatie") or lead.get("naam") or email
    from ..prospecting.service import LeadsService

    enrichment: Dict[str, Any] = {}
    verslag_error = ""
    try:
        enrichment = await asyncio.to_thread(_enrich_sync, email, lead.get("organisatie") or "")
        verslag = await _write_verslag(lead, enrichment)
    except Exception as e:  # noqa: BLE001
        logger.exception("Iris-verslag voor workshop-lead %s mislukt", email)
        verslag_error = str(e)[:300]
        verslag = (
            "Iris' analyse is niet gelukt (LLM-fout). Dit zijn de ruwe gegevens die de "
            f"lead zelf invulde:\n\n{_feiten_block(lead)}\n\nFout: {verslag_error}"
        )

    try:
        captured = await asyncio.to_thread(
            LeadsService().capture_workshop_lead, lead, verslag, enrichment)
    except Exception as e:  # noqa: BLE001
        logger.exception("Workshop-lead vastleggen in de funnel mislukt voor %s", email)
        log_outcome(
            "WeAreImpact", "workshop_lead_niet_vastgelegd",
            f"De AI Leadership Lab-lead van {naam_of_org} kon niet in de Leads-tab "
            f"worden gezet ({e}).",
            next_step=f"Leg de lead handmatig vast: {email}, rol {lead.get('rol') or '?'}.",
            status="error",
        )
        return False, str(e)[:300]

    if verslag_error:
        log_outcome(
            "WeAreImpact", "workshop_lead_verslag_mislukt",
            f"De lead van {naam_of_org} staat als 'Geverifieerd' in de Leads-tab, maar "
            f"Iris kon er geen analyse van maken en er ging geen mail uit ({verslag_error}).",
            next_step="Bekijk de lead in de Leads-tab en beoordeel hem handmatig.",
            status="error",
        )
        return True, verslag_error

    from ...shared import email_service
    subject = f"Iris: nieuwe AI Leadership Lab-lead — {naam_of_org}"
    ok = await asyncio.to_thread(email_service.send_report, subject, verslag)
    if not ok:
        log_outcome(
            "WeAreImpact", "workshop_lead_verslag_niet_verstuurd",
            f"Iris' verslag over de lead van {naam_of_org} is geschreven maar niet "
            "verstuurd (mailversturen faalde).",
            next_step="Controleer SMTP/Resend-configuratie in .env.",
            status="error",
        )
        return True, "mail versturen mislukt"

    lead_note = (
        f" Staat als nieuwe lead ({captured['org_name']}) op 'Geverifieerd' in de Leads-tab."
        if captured.get("is_new")
        else f" Al bekend als lead ({captured.get('org_name', naam_of_org)}); verslag bijgewerkt."
    )
    log_outcome(
        "WeAreImpact", "workshop_lead_verslag_verstuurd",
        f"Iris' verslag over de AI Leadership Lab-lead van {naam_of_org} is gemaild."
        + lead_note,
        next_step="Bekijk de lead in de Leads-tab en bepaal het vervolg — streef naar "
                  "opvolging binnen 48 uur.",
    )
    return True, ""


async def process_pending() -> Dict[str, Any]:
    """Ophalen bij de bridge, verrijken, laten analyseren, mailen, ack'en.
    Eigen try/except zodat een mislukking hier de rest van de bridge-sync
    (die net geslaagd is) niet alsnog als 'failed' laat boeken."""
    summary: Dict[str, Any] = {"pulled": 0, "processed": 0, "failed": 0}
    if not (BRIDGE_REMOTE_URL and BRIDGE_TOKEN):
        return summary
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.get(f"{_base()}/api/bridge?op=workshop-leads")
            r.raise_for_status()
            leads = r.json().get("leads", [])
            summary["pulled"] = len(leads)
            if not leads:
                return summary

            acks = []
            for lead in leads:
                ok, err = await _process_one(lead)
                acks.append({"id": lead["id"], "status": "processed" if ok else "failed",
                             "error": err})
                summary["processed" if ok else "failed"] += 1

            r = await client.post(f"{_base()}/api/bridge?op=workshop-leads-ack",
                                  json={"acks": acks})
            r.raise_for_status()
    except Exception:
        logger.exception("Workshop-leads ophalen/verwerken mislukt")
    return summary

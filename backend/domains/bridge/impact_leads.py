"""
Impact Calculator-leads (weareimpact.nl/impact-calculator).

De website pusht elke ontgrendeling rechtstreeks naar de bridge
(remote/api/bridge.js, op=impact-lead) — dit ontstaat buiten AgentOS om, dus
is er geen lokale rij om op te reageren totdat een sync 'm ophaalt. De klant
krijgt zijn cijferrapport al automatisch van de website zelf (Next.js stuurt
dat direct); wat hier gebeurt is Iris' toevoeging voor Vincent: wie is dit
bedrijf/deze persoon, en wat is een goed vervolg.

Draait mee in bridge/service.sync_once (dezelfde 3-minuten-cadans als de rest
van de bridge) — geen eigen scheduler-job, want dit hoeft nooit vaker te
draaien dan de bridge zelf al doet. Publiceert of verstuurt nooit iets naar de
klant: dit domein mailt uitsluitend Vincent.
"""
import asyncio
import logging
from typing import Any, Dict, List, Tuple

import httpx

from ...shared.config import BRIDGE_REMOTE_URL, BRIDGE_TOKEN
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0

# Op deze providers is het maildomein de aanbieder, niet het bedrijf — daar
# zoeken naar een "bedrijfswebsite" levert gmail.com/hotmail.com op.
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
    """Synchrone netwerk-verrijking (scrapen/zoeken) — wordt via
    asyncio.to_thread aangeroepen, nooit rechtstreeks vanuit async code (zie
    CLAUDE.md-les: een sync HTTP-call in een async job legt de hele server
    plat). Faalt nooit hard: een mislukte verrijking betekent minder feiten
    voor Iris, geen geblokkeerd verslag."""
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
        logger.exception("Impact-lead verrijking mislukt")
    return {"website": "", "scraped": {}, "search_results": []}


def _feiten_block(lead: Dict[str, Any]) -> str:
    """De harde feiten die de lead zelf invulde — nooit LLM-afhankelijk, dus
    ook bruikbaar als Iris' analyse mislukt (zie _process_one)."""
    inputs = lead.get("inputs") or {}
    results = lead.get("results") or {}
    return (
        f"- Naam: {lead.get('naam') or 'onbekend'}\n"
        f"- Organisatie: {lead.get('organisatie') or 'onbekend'}\n"
        f"- E-mail: {lead.get('email')}\n"
        f"- Teamomvang: {inputs.get('fte', 'onbekend')} FTE\n"
        f"- Administratiedruk: {inputs.get('adminPct', 'onbekend')}% van de werkdag\n"
        f"- Huidige AI-adoptie: {inputs.get('aiPct', 'onbekend')}%\n"
        f"- Berekende tijdwinst: {results.get('weeklyHoursSaved', 'onbekend')} uur/week\n"
        f"- Berekende besparing: EUR {results.get('grossSavingsPerYear', 'onbekend')}/jaar\n"
        f"- SROI: {results.get('sroiRatio', 'onbekend')} : 1"
    )


async def _write_verslag(lead: Dict[str, Any], enrichment: Dict[str, Any]) -> str:
    """Iris' analyse: wie is dit, wat vertellen de sliders, en wat is het
    beste vervolg? Nooit verzinnen — ontbrekende feiten heten 'onbekend'."""
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

    prompt = f"""Er heeft zich zojuist een lead aangemeld via de Impact Calculator op weareimpact.nl.
WeAreImpact is Vincent van Munster's AI-adviesbureau voor het sociaal domein.

Wat de lead zelf invulde:
{_feiten_block(lead)}

Wat er over het bedrijf/de persoon op internet is gevonden:
{context}

Schrijf een kort verslag voor Vincent (Nederlands, markdown, max 200 woorden) met:
1. Wie dit is en wat het bedrijf doet (schrijf letterlijk "onbekend" als er niets is
   gevonden — verzin nooit een branche, grootte of functie die niet in de gevonden
   informatie staat)
2. Waarom deze lead relevant is, gezien de ingevulde cijfers
3. Een concrete aanbeveling voor het vervolg (bijv. bellen, mailen met welke insteek,
   of negeren als het duidelijk geen fit is)
Gebruik geen liggende streepjes of emoji's."""

    return await claude_chat.get_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="Je bent Iris, de manager-agent van WeAreImpact. Je analyseert "
                      "inbound leads voor Vincent: kort, concreet, nooit verzonnen feiten.",
        purpose="impact_lead",
    )


async def _process_one(lead: Dict[str, Any]) -> Tuple[bool, str]:
    """Twee stappen die bewust niet in elkaars lot delen: vastleggen in de
    funnel is deterministisch (geen LLM) en moet altijd lukken — dat is het
    effect dat telt (de lead niet kwijtraken). Iris' verslag + de mail zijn
    verrijking erbovenop; als de LLM-quota op is (zie CLAUDE.md, de
    zelf-uitlijnende quota-rem) mag de lead daar niet het slachtoffer van
    worden — hij staat dan met de kale feiten in de Leads-tab i.p.v. helemaal
    nergens."""
    email = lead.get("email") or ""
    naam_of_org = lead.get("organisatie") or lead.get("naam") or email
    from ..prospecting.service import LeadsService

    enrichment: Dict[str, Any] = {}
    verslag_error = ""
    try:
        enrichment = await asyncio.to_thread(_enrich_sync, email, lead.get("organisatie") or "")
        verslag = await _write_verslag(lead, enrichment)
    except Exception as e:  # noqa: BLE001
        logger.exception("Iris-verslag voor impact-lead %s mislukt", email)
        verslag_error = str(e)[:300]
        verslag = (
            "Iris' analyse is niet gelukt (LLM-fout). Dit zijn de ruwe cijfers die de "
            f"lead zelf invulde:\n\n{_feiten_block(lead)}\n\nFout: {verslag_error}"
        )

    try:
        captured = await asyncio.to_thread(
            LeadsService().capture_impact_calculator_lead, lead, verslag, enrichment)
    except Exception as e:  # noqa: BLE001
        logger.exception("Impact-lead vastleggen in de funnel mislukt voor %s", email)
        # project="WeAreImpact" (exact match, geen squash) i.p.v. een verzonnen
        # 'ImpactLead'-project: activity_log wordt per project.activity-endpoint
        # met een exacte WHERE project = ? opgehaald, dus een niet-bestaand
        # project logt wél maar is in de UI nergens te zien (20 aug 2026: Vincent
        # zag het verslag in Outlook, maar niets in AgentOS zelf).
        log_outcome(
            "WeAreImpact", "impact_lead_niet_vastgelegd",
            f"De Impact Calculator-lead van {naam_of_org} kon niet in de Leads-tab "
            f"worden gezet ({e}).",
            next_step=f"Leg de lead handmatig vast: {email}, "
                      f"{(lead.get('inputs') or {}).get('fte', '?')} FTE, "
                      f"{(lead.get('results') or {}).get('grossSavingsPerYear', '?')} EUR/jaar.",
            status="error",
        )
        return False, str(e)[:300]

    if verslag_error:
        log_outcome(
            "WeAreImpact", "impact_lead_verslag_mislukt",
            f"De lead van {naam_of_org} staat als 'Geverifieerd' in de Leads-tab, maar "
            f"Iris kon er geen analyse van maken en er ging geen mail uit ({verslag_error}).",
            next_step="Bekijk de lead in de Leads-tab en beoordeel hem handmatig.",
            status="error",
        )
        return True, verslag_error

    from ...shared import email_service
    subject = f"Iris: nieuwe Impact Calculator-lead — {naam_of_org}"
    ok = await asyncio.to_thread(email_service.send_report, subject, verslag)
    if not ok:
        log_outcome(
            "WeAreImpact", "impact_lead_verslag_niet_verstuurd",
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
        "WeAreImpact", "impact_lead_verslag_verstuurd",
        f"Iris' verslag over de Impact Calculator-lead van {naam_of_org} is gemaild."
        + lead_note,
        next_step="Bekijk de lead in de Leads-tab en bepaal het vervolg.",
    )
    return True, ""


async def process_pending() -> Dict[str, Any]:
    """Ophalen bij de bridge, verrijken, laten analyseren, mailen, ack'en.
    Geen enkele stap mag de rest van de bridge-sync meeslepen — vandaar een
    eigen try/except in plaats van laten doorborrelen naar sync_once()."""
    summary: Dict[str, Any] = {"pulled": 0, "processed": 0, "failed": 0}
    if not (BRIDGE_REMOTE_URL and BRIDGE_TOKEN):
        return summary
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.get(f"{_base()}/api/bridge?op=impact-leads")
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

            r = await client.post(f"{_base()}/api/bridge?op=impact-leads-ack",
                                  json={"acks": acks})
            r.raise_for_status()
    except Exception:
        logger.exception("Impact-leads ophalen/verwerken mislukt")
    return summary

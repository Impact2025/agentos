"""
Boekingsaanvragen via weareimpact.nl (26 aug 2026).

Zelfde vorm als workshop_leads.py hiernaast, met één verschil: een boeking
kent een levenscyclus (pending -> approved/rejected) terwijl een workshop-
inschrijving een eenmalig feit is. De website pusht daarom niet één keer maar
bij elke statuswijziging (remote/api/bridge.js, op=booking-lead, upsert op
booking_request_id) — deze module verwerkt élke binnengekomen versie opnieuw:

- pending:  volledige verrijking + Iris-verslag, lead komt op 'valid' te staan
  (of blijft ongewijzigd als hij al verder is) in de Leads-tab.
- approved: geen nieuwe LLM-analyse (die deed de pending-ronde al) — alleen de
  funnel naar 'call' zetten, want er staat nu écht een gesprek gepland.
- rejected: geen analyse, alleen een melding — het voorgestelde moment paste
  niet, dat is geen oordeel over de lead zelf, dus de funnel-status blijft
  met rust (advance_lead zou dat ook fout interpreteren als vooruitgang).

Vincent krijgt de boekingsaanvraag zelf al rechtstreeks per mail van
weareimpact.nl (met de goedkeur-/afwijslink) — deze module mailt daarom
NOOIT een tweede keer over hetzelfde feit (zie CLAUDE.md,
stilstand_dubbel_gemeld: twee meldwegen voor hetzelfde ding is de storing,
niet de dekking). log_outcome zorgt uitsluitend voor zichtbaarheid in het
Actiecentrum en de Leads-tab.

Draait mee in bridge/service.sync_once, geen eigen scheduler-job.
"""
import asyncio
import logging
import re
from typing import Any, Dict, List, Tuple

import httpx

from ...shared.config import BRIDGE_REMOTE_URL, BRIDGE_TOKEN
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0

# Zelfde overweging als workshop_leads._GENERIC_MAIL_DOMAINS.
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


_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.[a-z]{2,}$")


def _looks_like_domain(text: str) -> bool:
    t = text.strip().lower()
    return "@" not in t and bool(_DOMAIN_RE.match(t))


def _normalize_website(text: str) -> str:
    """Wat de bezoeker zelf typt in het Website-veld kan met of zonder
    schema/pad komen ('https://Lspacademy.nl/over-ons') — dit strookt het tot
    kaal domein of geeft '' terug als het geen domein is."""
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = re.sub(r"^https?://", "", t).split("/", 1)[0]
    if t.startswith("www."):
        t = t[4:]
    return t if _looks_like_domain(t) else ""


def _enrich_sync(email: str, organisatie: str, website: str = "") -> Dict[str, Any]:
    """Synchrone netwerk-verrijking — via asyncio.to_thread aangeroepen, zie
    CLAUDE.md-les bij impact_leads.py. Faalt nooit hard.

    Prioriteit: het expliciet ingevulde Website-veld (26 aug 2026) is de
    hardste ankertekst — de bezoeker heeft die zelf en bewust getypt — en
    wint daarom altijd als primaire bron. Ontbreekt die, dan is het
    e-maildomein de primaire bron (zoals vóór 26 aug 2026). De opgegeven
    organisatienaam wordt daarnaast ALTIJD apart nagegaan zodra die kan
    afwijken van de primaire bron: het is vrije tekst die niets met een
    maildomein te maken hoeft te hebben (privé-adres, ander label voor
    hetzelfde bedrijf, tikfout). Wie hier maar één bron raadpleegt kiest die
    soms verkeerd zonder dat ooit te merken: een aanvraag met organisatie
    'Lspacademy.nl' en mailadres op 'fellow-travellers.com' werd vóór 26 aug
    2026 alleen op het maildomein onderzocht, en het verslag meldde slechts
    een 'mismatch' in plaats van uit te zoeken wie het daadwerkelijk is."""
    from ..prospecting.service import LeadsService
    svc = LeadsService()
    result: Dict[str, Any] = {
        "website": "", "scraped": {}, "search_results": [],
        "org_website": "", "org_scraped": {}, "org_search_results": [],
    }
    org = (organisatie or "").strip()
    explicit_domain = _normalize_website(website)
    mail_domain = _company_domain(email)
    primary_domain = explicit_domain or mail_domain

    try:
        if primary_domain:
            result["website"] = f"https://{primary_domain}"
            result["scraped"] = svc.scrape_and_enrich(result["website"], org or primary_domain)
    except Exception:
        logger.exception("Booking-lead verrijking (website/maildomein) mislukt")

    org_domain = org.lower() if org and _looks_like_domain(org) else ""
    try:
        if org_domain and org_domain != primary_domain:
            result["org_website"] = f"https://{org_domain}"
            result["org_scraped"] = svc.scrape_and_enrich(result["org_website"], org)
        elif org and not org_domain and org.lower() != primary_domain:
            result["org_search_results"] = svc.search_web(org, max_results=3)
    except Exception:
        logger.exception("Booking-lead verrijking (organisatienaam) mislukt")

    if not primary_domain and not org_domain and org and not result["org_search_results"]:
        try:
            result["search_results"] = svc.search_web(org, max_results=3)
        except Exception:
            logger.exception("Booking-lead verrijking (fallback zoekopdracht) mislukt")

    return result


def _feiten_block(booking: Dict[str, Any]) -> str:
    """De harde feiten die de bezoeker zelf invulde — nooit LLM-afhankelijk,
    dus ook bruikbaar als Iris' analyse mislukt."""
    return (
        f"- Naam: {booking.get('customer_name') or 'onbekend'}\n"
        f"- Organisatie: {booking.get('customer_organization') or 'onbekend'}\n"
        f"- Website (door bezoeker zelf ingevuld): {booking.get('customer_website') or 'onbekend'}\n"
        f"- E-mail: {booking.get('customer_email')}\n"
        f"- Telefoon: {booking.get('customer_phone') or 'onbekend'}\n"
        f"- Type gesprek: {booking.get('booking_type') or 'onbekend'}\n"
        f"- Aangevraagd moment: {booking.get('start_time') or 'onbekend'}\n"
        f"- Bericht van de bezoeker: {booking.get('notes') or '(geen)'}\n"
        f"- Bron: boekingswidget weareimpact.nl"
    )


def _bron_context(website: str, scraped: Dict[str, Any], search: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    if website:
        lines.append(f"Website: {website}")
    if scraped.get("page_text"):
        lines.append(f"Paginainhoud (verkort): {scraped['page_text'][:2000]}")
    adres = " ".join(x for x in (scraped.get("address"), scraped.get("city")) if x)
    if adres:
        lines.append(f"Adres: {adres}")
    if scraped.get("kvk_number"):
        lines.append(f"KvK: {scraped['kvk_number']}")
    for r in (search or [])[:3]:
        lines.append(f"- {r.get('title', '')}: {(r.get('snippet') or '')[:300]} ({r.get('url', '')})")
    return "\n".join(lines) if lines else "Niets gevonden."


async def _write_verslag(booking: Dict[str, Any], enrichment: Dict[str, Any]) -> str:
    """Iris' analyse: wie is dit, en wat is het beste vervolg? Nooit
    verzinnen — ontbrekende feiten heten 'onbekend'."""
    from ..chat import claude as claude_chat

    maildomein_context = _bron_context(
        enrichment.get("website", ""), enrichment.get("scraped") or {},
        enrichment.get("search_results") or [])
    org_website = enrichment.get("org_website", "")
    org_search = enrichment.get("org_search_results") or []
    heeft_organisatiebron = bool(org_website or org_search)
    organisatie_context = _bron_context(
        org_website, enrichment.get("org_scraped") or {}, org_search)

    organisatie_block = (
        f"""
Apart gevonden op basis van de opgegeven organisatienaam (dit hoeft niet
hetzelfde bedrijf te zijn als het e-maildomein hierboven):
{organisatie_context}
"""
        if heeft_organisatiebron else ""
    )

    prompt = f"""Er is zojuist een gesprek aangevraagd via de boekingswidget op weareimpact.nl.
WeAreImpact is Vincent van Munster's AI-adviesbureau voor het sociaal domein.

Wat de bezoeker zelf invulde:
{_feiten_block(booking)}

Gevonden op basis van het e-maildomein:
{maildomein_context}
{organisatie_block}
Schrijf een kort verslag voor Vincent (Nederlands, markdown, max 200 woorden) met:
1. Wie dit is en wat het bedrijf doet. Wijzen beide bronnen hierboven naar hetzelfde
   bedrijf, gebruik die informatie dan gewoon samengevoegd. Wijzen ze naar iets anders
   (bijvoorbeeld een privé-mailadres bij een ander bedrijf dan de opgegeven organisatie),
   maak dan een keuze welke bron je gebruikt en zeg waarom (bijvoorbeeld: de opgegeven
   organisatienaam is leidend, het mailadres is vermoedelijk privé) in plaats van alleen
   te melden dát ze verschillen. Schrijf letterlijk "onbekend" als geen van beide bronnen
   iets opleverde — verzin nooit een branche, grootte of functie die niet in de gevonden
   informatie staat.
2. Waarom deze aanvraag relevant is
3. Waar Vincent op moet letten in het gesprek zelf (gezien het type gesprek en het
   eventuele bericht van de bezoeker)
Gebruik geen liggende streepjes of emoji's."""

    return await claude_chat.get_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="Je bent Iris, de manager-agent van WeAreImpact. Je analyseert "
                      "inbound leads voor Vincent: kort, concreet, nooit verzonnen feiten.",
        purpose="booking_lead",
    )


async def _process_one(booking: Dict[str, Any]) -> Tuple[bool, str]:
    email = booking.get("customer_email") or ""
    naam_of_org = booking.get("customer_organization") or booking.get("customer_name") or email
    booking_status = booking.get("booking_status") or "pending"
    from ..prospecting.service import LeadsService

    if booking_status == "rejected":
        # Geen LLM-analyse nodig: er is niets nieuws te ontdekken over de
        # lead, alleen het voorgestelde moment ging niet door. Dat is geen
        # oordeel over de lead — de funnel-status blijft met rust.
        try:
            await asyncio.to_thread(
                LeadsService().capture_booking_lead, booking,
                f"Aangevraagd moment ({booking.get('start_time')}) is afgewezen door Vincent.",
                {},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Afgewezen booking-lead vastleggen mislukt voor %s", email)
            return False, str(e)[:300]
        log_outcome(
            "WeAreImpact", "booking_lead_afgewezen",
            f"De boekingsaanvraag van {naam_of_org} is afgewezen — het voorgestelde moment paste niet.",
            next_step="Stuur zelf een alternatief moment als er nog interesse is.",
        )
        return True, ""

    if booking_status == "approved":
        # De pending-ronde deed al de verrijking en het verslag; hier hoeft
        # alleen de funnel bijgewerkt — er staat nu écht een gesprek gepland.
        try:
            captured = await asyncio.to_thread(
                LeadsService().capture_booking_lead, booking,
                f"Gesprek bevestigd voor {booking.get('start_time')}.", {},
            )
            if captured.get("id"):
                from ..prospecting import funnel
                await asyncio.to_thread(funnel.advance_lead, captured["id"], "call")
        except Exception as e:  # noqa: BLE001
            logger.exception("Goedgekeurde booking-lead bijwerken mislukt voor %s", email)
            log_outcome(
                "WeAreImpact", "booking_lead_niet_vastgelegd",
                f"De goedgekeurde afspraak met {naam_of_org} kon niet worden bijgewerkt in de "
                f"Leads-tab ({e}).",
                next_step=f"Zet de lead handmatig op 'Gesprek': {email}.",
                status="error",
            )
            return False, str(e)[:300]
        log_outcome(
            "WeAreImpact", "booking_lead_bevestigd",
            f"Het gesprek met {naam_of_org} staat vast voor {booking.get('start_time')}.",
            next_step="Bereid het gesprek voor — bekijk de lead in de Leads-tab.",
        )
        return True, ""

    # booking_status == 'pending' (de oorspronkelijke aanvraag).
    enrichment: Dict[str, Any] = {}
    verslag_error = ""
    try:
        enrichment = await asyncio.to_thread(
            _enrich_sync, email, booking.get("customer_organization") or "",
            booking.get("customer_website") or "")
        verslag = await _write_verslag(booking, enrichment)
    except Exception as e:  # noqa: BLE001
        logger.exception("Iris-verslag voor booking-lead %s mislukt", email)
        verslag_error = str(e)[:300]
        verslag = (
            "Iris' analyse is niet gelukt (LLM-fout). Dit zijn de ruwe gegevens van de "
            f"aanvraag:\n\n{_feiten_block(booking)}\n\nFout: {verslag_error}"
        )

    try:
        captured = await asyncio.to_thread(
            LeadsService().capture_booking_lead, booking, verslag, enrichment)
    except Exception as e:  # noqa: BLE001
        logger.exception("Booking-lead vastleggen mislukt voor %s", email)
        log_outcome(
            "WeAreImpact", "booking_lead_niet_vastgelegd",
            f"De boekingsaanvraag van {naam_of_org} kon niet in de Leads-tab worden gezet ({e}).",
            next_step=f"Leg de lead handmatig vast: {email}.",
            status="error",
        )
        return False, str(e)[:300]

    if verslag_error:
        log_outcome(
            "WeAreImpact", "booking_lead_verslag_mislukt",
            f"De boekingsaanvraag van {naam_of_org} staat als 'Geverifieerd' in de Leads-tab, "
            f"maar Iris kon er geen analyse van maken ({verslag_error}).",
            next_step="Bekijk de lead in de Leads-tab en beoordeel hem handmatig.",
            status="error",
        )
        return True, verslag_error

    lead_note = (
        f" Nieuwe lead ({captured['org_name']}) op 'Geverifieerd' in de Leads-tab."
        if captured.get("is_new")
        else f" Al bekend als lead ({captured.get('org_name', naam_of_org)}); verslag bijgewerkt."
    )
    log_outcome(
        "WeAreImpact", "booking_lead_ontvangen",
        f"Boekingsaanvraag van {naam_of_org} voor {booking.get('booking_type') or 'een gesprek'} "
        f"op {booking.get('start_time')}." + lead_note,
        next_step="Je hebt hierover al een mail met goedkeur-/afwijslink ontvangen — de lead "
                  "staat nu ook in de Leads-tab.",
    )
    return True, ""


async def process_pending() -> Dict[str, Any]:
    """Ophalen bij de bridge, verwerken (verrijken bij een nieuwe aanvraag,
    funnel bijwerken bij een besluit), ack'en. Eigen try/except zodat een
    mislukking hier de rest van de bridge-sync niet alsnog als 'failed' laat
    boeken."""
    summary: Dict[str, Any] = {"pulled": 0, "processed": 0, "failed": 0}
    if not (BRIDGE_REMOTE_URL and BRIDGE_TOKEN):
        return summary
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.get(f"{_base()}/api/bridge?op=booking-leads")
            r.raise_for_status()
            bookings = r.json().get("leads", [])
            summary["pulled"] = len(bookings)
            if not bookings:
                return summary

            acks = []
            for booking in bookings:
                ok, err = await _process_one(booking)
                acks.append({"id": booking["id"], "status": "processed" if ok else "failed",
                             "error": err})
                summary["processed" if ok else "failed"] += 1

            r = await client.post(f"{_base()}/api/bridge?op=booking-leads-ack",
                                  json={"acks": acks})
            r.raise_for_status()
    except Exception:
        logger.exception("Booking-leads ophalen/verwerken mislukt")
    return summary

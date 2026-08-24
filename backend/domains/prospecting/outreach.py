"""Dagelijkse outreach-batch — het input-volume van de acquisitieformule.

Elke werkdag zet de agent voor de beste onbenaderde leads een gepersonaliseerd
outreach-concept klaar (onderwerp + mail). De lead krijgt status
'outreach_review' en verschijnt in het Actiecentrum, waar Vincent per concept
"Verstuur" of "Wijs af" klikt.

Er wordt hier NOOIT verstuurd — dat gebeurt uitsluitend via de
outreach-approve-endpoint na menselijke goedkeuring (de Wachtrij-gate-regel).
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from ...shared.config import OUTREACH_DAILY_TARGET
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

# Eigen zakelijke domeinen — een lead op een van deze domeinen is géén
# externe prospect (self-lead) en mag nooit in de outreach-review komen.
_OWN_DOMAINS = {"weareimpact.nl", "bewaardvooraltijd.nl"}
# Algemene/functie-adressen die (bijna) nooit de inkoper zijn. Acquisitie naar
# info@ / pers@ / redactie@ is zinloos en beschadigt de verzender-reputatie.
_GENERIC_LOCAL = {
    "info", "pers", "redactie", "admin", "noreply", "no-reply", "contact",
    "algemeen", "mail", "office", "sales", "pr", "marketing",
}
# Domeinen die duidelijk geen echt zakelijk adres zijn (placeholder/scrape-rest).
_INVALID_DOMAINS = {"voorbeeld.nl", "example.com", "example.nl", "test.nl", "localhost"}


def _email_is_valid(addr: str) -> tuple[bool, str]:
    """Valideer een e-mailadres op 'serieus prospect-adres'.

    Returns (ok, reden). reden is leeg bij ok. Een adres is pas ok als:
    - het een syntactisch geldig adres is (local@domein.tld),
    - het domein niet een eigen domein is (geen self-lead),
    - het domein niet in de expliciete ongeldige-lijst staat,
    - de local-part geen bekend algemeen/functie-adres is,
    - het domein een écht TLD heeft (≥2 tekens na de laatste punt).
    """
    if not addr or "@" not in addr:
        return False, "geen geldig adres (geen @)"
    # Sommige leads hebben een URL-encoding of proto-rest (bijv.
    # 'http://geaddresseerd%40voorbeeld.nl') — schoon dat eerst.
    addr = unquote(addr).split("://")[-1].split("?")[0].strip().lower()
    local, _, dom = addr.partition("@")
    if not local or not dom:
        return False, "geen geldig adres (local/domein ontbreekt)"
    if "." not in dom:
        return False, f"geen geldig domein ('{dom}')"
    sld, _, tld = dom.rpartition(".")
    if len(tld) < 2 or not re.fullmatch(r"[a-z0-9-]+", tld):
        return False, f"geen geldig TLD ('{dom}')"
    # Second-level domain moet ook minimaal 3 tekens zijn — 'b.ys' e.d.
    # zijn vrijwel altijd scrape-rest/placeholder, geen echte organisatie.
    if len(sld) < 3:
        return False, f"geen geldig domein ('{dom}')"
    if dom in _INVALID_DOMAINS:
        return False, f"placeholder/scrape-rest domein ('{dom}')"
    if dom in _OWN_DOMAINS:
        return False, f"eigen domein (self-lead: '{dom}')"
    if local in _GENERIC_LOCAL:
        return False, f"algemeen adres ('{local}@{dom}' — geen inkoper)"
    return True, ""


def valid_target(lead: Dict[str, Any]) -> tuple[bool, str]:
    """Bepaal of de lead een serieus outreach-doel heeft.

    Kijkt naar hetzelfde adres dat bij verzending gebruikt zou worden
    (hoofdmail, anders eerste contactpersoon). Returns (ok, reden)."""
    target = target_email_for(lead)
    if not target:
        return False, "geen e-mailadres bekend"
    return _email_is_valid(target)

# Waardepropositie per lead_type: WeAreImpact (AI in zorg/welzijn) of
# Bewaard voor altijd (keepsake, B2B-partners). Bepaalt de invalshoek van
# het concept; AVG-regel geldt overal: alleen zakelijke gegevens.
_PITCH_BY_TYPE = {
    "ai-consultancy": (
        "WeAreImpact helpt zorg- en welzijnsorganisaties met warme zorg door slimme tech: "
        "AI-strategie, implementatietrajecten en AI-assistenten. Vincent is beschikbaar "
        "voor interim AI-opdrachten en consultancy."
    ),
    "ai-opdracht": (
        "Vincent van Munster (WeAreImpact) is beschikbaar als interim AI-projectleider/"
        "programmamanager voor zorg, welzijn en gemeenten — van AI-strategie tot implementatie."
    ),
    "zorg": (
        "WeAreImpact helpt zorgorganisaties met warme zorg door slimme tech: praktische "
        "AI-toepassingen die zorgprofessionals tijd teruggeven."
    ),
    "notarissen": (
        "Bewaard voor altijd maakt tastbare herinnerings-keepsakes voor nabestaanden — "
        "een waardevolle aanvulling op nalatenschaps- en levenstestamentgesprekken."
    ),
    "uitvaart": (
        "Bewaard voor altijd maakt tastbare herinnerings-keepsakes die uitvaartondernemers "
        "als blijvend aandenken aan families kunnen aanbieden."
    ),
}
_DEFAULT_PITCH = _PITCH_BY_TYPE["ai-consultancy"]

_SIGNATURE = (
    "Hartelijke groet,\n"
    "  WeAreImpact\n"
    "Innovatie met een sociaal hart.\n"
    "\n"
    "Vincent van Munster - Strategic Innovation Partner\n"
    "  T  06 - 144 709 77\n"
    "  E  v.munster@weareimpact.nl\n"
    "  W  weareimpact.nl\n"
    "  L  in/vincent-van-münster"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Hoeveel kandidaten we maximaal door `valid_target` halen om er `count` uit te
# kiezen. Ruim genoeg dat een sliert onbruikbare adressen de batch nooit leeg
# maakt, begrensd genoeg dat een voorraad van duizenden niet elke ochtend
# volledig door de zeef gaat.
_KANDIDAAT_SCAN_MAX = 500


def select_batch_leads(count: int) -> List[Dict[str, Any]]:
    """Kies de beste onbenaderde leads met een bruikbaar e-mailadres.

    Selectie: nog nooit benaderd of afgeschreven, nog geen concept klaar,
    e-mail bekend (hoofdemail of contactpersoon), hoogste score eerst.
    Deliverable-geverifieerde adressen ('valid') gaan vóór alleen-gescrapede.

    De zeef van `valid_target` draait vóór het afkappen op `count`, niet erna.
    Andersom kiest de database eerst `count` rijen en houdt Python daar
    misschien niets van over — dan meldt de batch "geen bruikbare leads" terwijl
    de voorraad vol staat. Precies dat gebeurde op 2 aug 2026: de eerste acht
    rijen in deze sortering waren allemaal `info@`-adressen die de zeef weigert,
    de eerste bruikbare stond op plek negen, en met count=5 kwam er nooit één
    concept uit. Alle leads hadden bovendien dezelfde score (50), dus de
    tie-break was `created_at` en de volgorde iedere dag identiek: dezelfde acht
    onbruikbare adressen blokkeerden het venster permanent. De funnel stond
    weken op "0 verstuurd" met zeven direct mailbare leads in voorraad.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads "
            "WHERE status IN ('new', 'enriched', 'valid') "
            "AND contacted_at = '' AND lost_at = '' AND outreach_draft = '' "
            "AND (email != '' OR contacts LIKE '%@%') "
            "ORDER BY CASE status WHEN 'valid' THEN 0 ELSE 1 END, score DESC, created_at ASC "
            "LIMIT ?",
            (_KANDIDAAT_SCAN_MAX,),
        ).fetchall()
    # Sorteer de leads eruit zonder serieus prospect-adres — die mogen nooit in
    # de outreach-review belanden (zie valid_target()).
    bruikbaar = [dict(r) for r in rows if valid_target(dict(r))[0]]
    return bruikbaar[:count]


def count_mailable_leads() -> int:
    """Hoeveel leads kunnen er vandáág een concept krijgen.

    Bewust dezelfde selectie + zeef als `select_batch_leads`: "voorraad" en
    "waar de batch mee vooruit kan" moeten één getal zijn. Zolang Iris' bottleneck
    op `new + enriched` telde, las ze 47 waar er 7 mailbaar waren — en dan wijst
    ze de verkeerde knop aan (nog meer leads zoeken in plaats van versturen).
    """
    return len(select_batch_leads(_KANDIDAAT_SCAN_MAX))


def target_email_for(lead: Dict[str, Any]) -> str:
    """Het adres waar het concept naartoe gaat: hoofdemail, anders eerste contact."""
    if lead.get("email"):
        return lead["email"]
    try:
        contacts = json.loads(lead.get("contacts") or "[]")
    except Exception:
        contacts = []
    for c in contacts:
        if c.get("email"):
            return c["email"]
    return ""


def _draft_prompt(lead: Dict[str, Any], variant: Optional[Dict[str, str]] = None) -> str:
    from ...shared.learning import lessons_block
    from .learning import variant_instructions

    try:
        contacts = json.loads(lead.get("contacts") or "[]")
    except Exception:
        contacts = []
    contact_line = ""
    if contacts:
        c = contacts[0]
        naam, rol = c.get("naam", ""), c.get("rol", "")
        contact_line = f"Contactpersoon: {naam}{(' (' + rol + ')') if rol else ''}\n"
    pitch = _PITCH_BY_TYPE.get(lead.get("lead_type", ""), _DEFAULT_PITCH)
    # Stijl-eisen: met variant (leerlus) vervangen de variant-instructies de
    # vaste opening/lengte/toon-regels; zonder variant het oude basisconcept.
    if variant:
        style = variant_instructions(variant)
    else:
        style = [
            "Maximaal 130 woorden, toon: direct, oprecht, geen jargon of superlatieven",
            "Open met iets specifieks over HUN organisatie (uit 'wat we over hen weten')",
        ]
    eisen = style + [
        "Eén laagdrempelige call-to-action (kort kennismakingsgesprek)",
        "AVG-veilig: alleen zakelijke context, geen aannames over personen",
        # Wetgeving (Telecommunicatiewet art. 11.7 + e-Privacy): elke ongevraagde
        # B2B-commercial mail MOET de afzender identificeren én een werkende
        # afmeldmogelijkheid bevatten. De LLM schrijft dit blok daarom altijd,
        # ook als de gebruiker er niet om vraagt — anders is de mail illegaal.
        "VERPLICICHT (wetgeving): eindig de mail met een werkende afmeldregel, "
        "bijv. 'Geen interesse meer in deze mails? Antwoord met 'STOP' en je "
        "staat uit ons bestand. — Vincent van Munster, WeAreImpact, "
        "v.munster@weareimpact.nl, 06-14470977'.",
        f"Onderteken met:\n{_SIGNATURE}",
    ]
    # Geleerde lessen (gemeten reply-rates) — leeg blok zolang er niets is.
    geleerd = lessons_block("outreach")
    return (
        "Schrijf een korte, persoonlijke B2B-outreachmail in het Nederlands.\n\n"
        f"Aan: {lead.get('org_name', '')}"
        f"{' in ' + lead['city'] if lead.get('city') else ''}\n"
        f"{contact_line}"
        f"Wat we over hen weten: {(lead.get('summary') or '—')[:500]}\n\n"
        f"Ons aanbod: {pitch}\n\n"
        + (geleerd + "\n\n" if geleerd else "")
        + "Eisen:\n"
        + "\n".join(f"- {e}" for e in eisen) + "\n\n"
        "Antwoord UITSLUITEND met JSON (geen markdown):\n"
        '{"subject": "onderwerpregel van max 60 tekens", "body": "de volledige mailtekst '
        '(inclusief de verplichte afmeldregel aan het eind)"}'
    )


async def draft_outreach(lead: Dict[str, Any],
                         variant: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """Genereer één concept (subject + body) via Claude, Hermes als terugval."""
    from ..publish.content_pipeline import _llm, _extract_json

    system = (
        "Je bent een nuchtere Nederlandse B2B-copywriter. Je schrijft outreach die "
        "gelezen wordt omdat hij specifiek en kort is, niet omdat hij schreeuwt."
    )
    raw = await _llm(system, _draft_prompt(lead, variant), max_tokens=700, purpose="outreach")
    if not raw:
        return None
    try:
        data = json.loads(_extract_json(raw))
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
    except Exception:
        logger.warning("[outreach] Onleesbaar concept voor %s — overslaan", lead.get("org_name"))
        return None
    if not subject or not body or len(body) < 80:
        return None
    return {"subject": subject[:120], "body": body}


async def prepare_outreach_batch(count: int = 0) -> Dict[str, Any]:
    """Zet voor de beste leads een outreach-concept klaar ter review.

    Retourneert een batchrapport; logt één uitkomst-kaart met de next_step
    voor Vincent. Verstuurt niets."""
    count = count or OUTREACH_DAILY_TARGET
    leads = select_batch_leads(count)
    if not leads:
        log_outcome(
            "Leads", "outreach_batch",
            "Geen onbenaderde leads met e-mailadres beschikbaar voor de dagelijkse outreach-batch",
            next_step="Draai een lead-zoekactie (Leads-tab) om de funnel-invoer aan te vullen.",
        )
        return {"drafted": 0, "skipped": 0, "leads": []}

    drafted, skipped, done = 0, 0, []
    now = _now()
    for lead in leads:
        # Harde guard: alleen een serieus prospect-adres mag in review.
        # Een lead zonder geldig adres gaat naar 'lost' met reden, zodat hij
        # niet de volgende ochtend weer in de batch opduikt (en nooit per
        # ongeluk verstuurd wordt).
        ok, why = valid_target(lead)
        if not ok:
            logger.info("[outreach] Lead %s overgeslagen (%s) — niet in review",
                        lead.get("org_name"), why)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE leads SET status = 'lost', outreach_draft = '', "
                    "outreach_drafted_at = '', lost_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, lead["id"]),
                )
            skipped += 1
            continue
        # Leerlus: kies deterministisch een stijl-variant en label het concept
        # ermee, zodat de wekelijkse evaluatie aanpak aan reply kan koppelen.
        from .learning import choose_variant
        variant = choose_variant(lead["id"])
        draft = await draft_outreach(lead, variant)
        if not draft:
            skipped += 1
            continue
        with get_conn() as conn:
            conn.execute(
                "UPDATE leads SET status = 'outreach_review', outreach_subject = ?, "
                "outreach_draft = ?, outreach_drafted_at = ?, outreach_variant = ?, "
                "updated_at = ? WHERE id = ?",
                (draft["subject"], draft["body"], now,
                 json.dumps(variant, ensure_ascii=False), now, lead["id"]),
            )
        drafted += 1
        done.append({"id": lead["id"], "org_name": lead["org_name"], "subject": draft["subject"]})

    log_outcome(
        "Leads", "outreach_batch",
        f"{drafted} outreach-concept(en) klaargezet ter review"
        + (f" ({skipped} overgeslagen: geen bruikbaar concept)" if skipped else ""),
        next_step=(
            f"Keur de {drafted} concepten goed of wijs ze af in het Actiecentrum — "
            "pas na jouw klik wordt er verstuurd." if drafted else
            "Geen concepten gelukt — controleer de LLM-configuratie."
        ),
        status="ok" if drafted else "error",
    )
    logger.info("[outreach] Batch klaar: %d concepten, %d overgeslagen", drafted, skipped)
    return {"drafted": drafted, "skipped": skipped, "leads": done}


def cleanup_unmailable_leads() -> Dict[str, Any]:
    """Schoon de funnel-invoer op: leads (new/enriched) zonder bruikbaar
    e-mailadres gaan naar 'lost' met tijdstempel.

    Zonder deze opschoning liegt de funnel: '62 enriched' klinkt als voorraad,
    maar als de outreach-batch er dagelijks maar 1 door de kwaliteitsguard
    krijgt, is de rest dood gewicht dat elke ochtend opnieuw geselecteerd en
    overgeslagen wordt. Verstuurt niets; verwijdert niets."""
    now = _now()
    with get_conn() as conn:
        candidates = [dict(r) for r in conn.execute(
            "SELECT * FROM leads WHERE status IN ('new', 'enriched')"
        ).fetchall()]
    removed: Dict[str, int] = {}
    kept = 0
    for lead in candidates:
        ok, why = valid_target(lead)
        if ok:
            kept += 1
            continue
        removed[why] = removed.get(why, 0) + 1
        with get_conn() as conn:
            conn.execute(
                "UPDATE leads SET status = 'lost', lost_at = ?, updated_at = ? WHERE id = ?",
                (now, now, lead["id"]),
            )
    total_removed = sum(removed.values())
    reasons = "; ".join(f"{n}× {why}" for why, n in
                        sorted(removed.items(), key=lambda kv: -kv[1]))
    log_outcome(
        "Leads", "funnel_opschoning",
        f"Funnel-invoer opgeschoond: {total_removed} onbruikbare lead(s) → lost "
        f"({reasons or 'geen'}), {kept} bruikbare blijven staan",
        artifact="/api/leads/funnel",
        next_step=(f"Draai een lead-zoekactie: nog maar {kept} bruikbare lead(s) in voorraad."
                   if kept < 20 else
                   f"Niets — de outreach-batch kan weer vooruit met {kept} bruikbare leads."),
    )
    logger.info("[outreach] Opschoning: %d verwijderd, %d bruikbaar", total_removed, kept)
    return {"removed": total_removed, "kept": kept, "reasons": removed}


async def run_daily_outreach_batch() -> None:
    """Scheduler entry-point (ma-vr): bereid de dagelijkse batch voor."""
    try:
        await prepare_outreach_batch()
    except Exception as e:
        logger.exception("Dagelijkse outreach-batch gefaald")
        log_outcome(
            "Leads", "outreach_batch", f"Dagelijkse outreach-batch gefaald: {e}",
            next_step="Bekijk impactos.err en draai de batch handmatig (POST /api/leads/outreach-batch).",
            status="error",
        )

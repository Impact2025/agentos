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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.config import OUTREACH_DAILY_TARGET
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

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

_SIGNATURE = "Vincent van Munster\nWeAreImpact\nv.munster@weareimpact.nl\n06 14 47 09 77"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_batch_leads(count: int) -> List[Dict[str, Any]]:
    """Kies de beste onbenaderde leads met een bruikbaar e-mailadres.

    Selectie: nog nooit benaderd of afgeschreven, nog geen concept klaar,
    e-mail bekend (hoofdemail of contactpersoon), hoogste score eerst.
    Deliverable-geverifieerde adressen ('valid') gaan vóór alleen-gescrapede."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads "
            "WHERE status IN ('new', 'enriched', 'valid') "
            "AND contacted_at = '' AND lost_at = '' AND outreach_draft = '' "
            "AND (email != '' OR contacts LIKE '%@%') "
            "ORDER BY CASE status WHEN 'valid' THEN 0 ELSE 1 END, score DESC, created_at ASC "
            "LIMIT ?",
            (count,),
        ).fetchall()
    return [dict(r) for r in rows]


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


def _draft_prompt(lead: Dict[str, Any]) -> str:
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
    return (
        "Schrijf een korte, persoonlijke B2B-outreachmail in het Nederlands.\n\n"
        f"Aan: {lead.get('org_name', '')}"
        f"{' in ' + lead['city'] if lead.get('city') else ''}\n"
        f"{contact_line}"
        f"Wat we over hen weten: {(lead.get('summary') or '—')[:500]}\n\n"
        f"Ons aanbod: {pitch}\n\n"
        "Eisen:\n"
        "- Maximaal 130 woorden, toon: direct, oprecht, geen jargon of superlatieven\n"
        "- Open met iets specifieks over HUN organisatie (uit 'wat we over hen weten')\n"
        "- Eén laagdrempelige call-to-action (kort kennismakingsgesprek)\n"
        "- AVG-veilig: alleen zakelijke context, geen aannames over personen\n"
        f"- Onderteken met:\n{_SIGNATURE}\n\n"
        "Antwoord UITSLUITEND met JSON (geen markdown):\n"
        '{"subject": "onderwerpregel van max 60 tekens", "body": "de volledige mailtekst"}'
    )


async def draft_outreach(lead: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Genereer één concept (subject + body) via Claude, Hermes als terugval."""
    from ..publish.content_pipeline import _llm, _extract_json

    system = (
        "Je bent een nuchtere Nederlandse B2B-copywriter. Je schrijft outreach die "
        "gelezen wordt omdat hij specifiek en kort is, niet omdat hij schreeuwt."
    )
    raw = await _llm(system, _draft_prompt(lead), max_tokens=700)
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
        draft = await draft_outreach(lead)
        if not draft:
            skipped += 1
            continue
        with get_conn() as conn:
            conn.execute(
                "UPDATE leads SET status = 'outreach_review', outreach_subject = ?, "
                "outreach_draft = ?, outreach_drafted_at = ?, updated_at = ? WHERE id = ?",
                (draft["subject"], draft["body"], now, now, lead["id"]),
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


async def run_daily_outreach_batch() -> None:
    """Scheduler entry-point (ma-vr): bereid de dagelijkse batch voor."""
    try:
        await prepare_outreach_batch()
    except Exception as e:
        logger.exception("Dagelijkse outreach-batch gefaald")
        log_outcome(
            "Leads", "outreach_batch", f"Dagelijkse outreach-batch gefaald: {e}",
            next_step="Bekijk agentos.err en draai de batch handmatig (POST /api/leads/outreach-batch).",
            status="error",
        )

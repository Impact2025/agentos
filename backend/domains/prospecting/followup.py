"""
Lead-opvolging — de zachte herinnering na stilte.

De acquisitieformule (funnel.py) meet contacted -> replied, maar deed tot nu
toe niets als het antwoord uitbleef: een lead die niet reageerde bleef voor
altijd op 'contacted' staan zonder dat er ooit een tweede poging kwam. Dit
bestand zet dat om in één zachte, hooguit tweemalige herinnering — nooit een
derde keer (dat is achtervolgen, geen opvolgen) en nooit zonder Vincents klik.

`followup_count`/`followup_sent_at` tellen ELKE afgehandelde ronde (verstuurd
én bewust overgeslagen): zonder dat zou een afgewezen concept de eerstvolgende
scheduler-ronde gewoon opnieuw verschijnen, want de stilteperiode zelf is dan
nog niet verstreken — hetzelfde concept twee keer aanbieden is geen tweede
kans, het is ruis.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

FOLLOWUP_AFTER_DAYS = 5     # stilte na 'contacted' (of de vorige follow-up) vóór een herinnering
FOLLOWUP_MAX_ATTEMPTS = 2   # hooguit twee herinneringen — een derde is achtervolgen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r) -> Dict[str, Any]:
    return dict(r) if r is not None else {}


def leads_needing_followup() -> List[Dict[str, Any]]:
    """'contacted', nog geen reactie, stil lang genoeg, nog niet aan het
    maximum, en nog geen openstaand concept."""
    grens = (datetime.now(timezone.utc) - timedelta(days=FOLLOWUP_AFTER_DAYS)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE status = 'contacted' AND replied_at = '' "
            "AND followup_draft = '' AND followup_count < ? "
            "AND MAX(contacted_at, followup_sent_at) < ? "
            "ORDER BY score DESC",
            (FOLLOWUP_MAX_ATTEMPTS, grens),
        ).fetchall()
    return [_row(r) for r in rows]


def _followup_prompt(lead: Dict[str, Any]) -> str:
    try:
        contacts = json.loads(lead.get("contacts") or "[]")
    except (TypeError, ValueError):
        contacts = []
    contact_line = ""
    if contacts and isinstance(contacts[0], dict):
        naam = contacts[0].get("naam") or contacts[0].get("name") or ""
        if naam:
            contact_line = f"Contactpersoon: {naam}\n"
    eerdere_mail = (lead.get("outreach_draft") or "")[:600]
    return (
        "Schrijf een korte opvolgmail (follow-up) in het Nederlands op een eerdere "
        "outreach-mail die geen reactie kreeg. Dit is opvolging nummer "
        f"{(lead.get('followup_count') or 0) + 1} van maximaal {FOLLOWUP_MAX_ATTEMPTS}.\n\n"
        f"Aan: {lead.get('org_name', '')}\n{contact_line}"
        f"De eerdere mail was:\n{eerdere_mail}\n\n"
        "Eisen:\n"
        "- Maximaal 60 woorden — korter dan de eerste mail, geen herhaling van de hele pitch\n"
        "- Erken luchtig dat je nog niets hoorde, zonder verwijt of druk\n"
        "- Eén concrete, laagdrempelige vraag of voorstel\n"
        "- Geen jargon of superlatieven\n"
        "VERPLICHT (wetgeving): eindig met een werkende afmeldregel, bijv. "
        "'Geen interesse meer? Antwoord met STOP.' — Vincent van Munster, WeAreImpact, "
        "v.munster@weareimpact.nl, 06-14470977\n\n"
        "Antwoord UITSLUITEND met JSON (geen markdown):\n"
        '{"subject": "onderwerpregel van max 60 tekens", "body": "de volledige mailtekst"}'
    )


async def draft_followup(lead: Dict[str, Any]) -> Optional[Dict[str, str]]:
    from ..publish.content_pipeline import _llm, _extract_json

    system = (
        "Je bent een nuchtere Nederlandse B2B-copywriter die korte, oprechte "
        "opvolgmails schrijft — geen 'even opvolgen ivm onderstaand' clichés."
    )
    raw = await _llm(system, _followup_prompt(lead), max_tokens=400, purpose="outreach")
    if not raw:
        return None
    try:
        data = json.loads(_extract_json(raw))
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
    except (ValueError, TypeError):
        logger.warning("[followup] Onleesbaar concept voor %s — overslaan", lead.get("org_name"))
        return None
    if not subject or not body or len(body) < 40:
        return None
    return {"subject": subject[:120], "body": body}


async def genereer_followups(limit: int = 10) -> List[Dict[str, Any]]:
    """Maakt follow-up-concepten voor leads die er klaar voor zijn. Draait
    veilig herhaald: een lead met al een openstaand concept wordt overgeslagen
    (zie leads_needing_followup)."""
    gemaakt: List[Dict[str, Any]] = []
    for lead in leads_needing_followup()[:limit]:
        draft = await draft_followup(lead)
        if not draft:
            continue
        with get_conn() as conn:
            conn.execute(
                "UPDATE leads SET followup_subject = ?, followup_draft = ?, "
                "followup_drafted_at = ? WHERE id = ?",
                (draft["subject"], draft["body"], _now(), lead["id"]),
            )
        gemaakt.append(draft | {"lead_id": lead["id"], "org_name": lead["org_name"]})
    if gemaakt:
        logger.info("[followup] %d opvolgconcept(en) klaar voor review", len(gemaakt))
    return gemaakt


def sla_followup_over(lead_id: str) -> None:
    """Bewust geen tweede poging deze ronde — telt wél mee als afgehandelde
    poging (zie moduledocstring: anders herverschijnt hetzelfde concept
    morgen al weer)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET followup_count = followup_count + 1, followup_sent_at = ?, "
            "followup_subject = '', followup_draft = '', followup_drafted_at = '' WHERE id = ?",
            (_now(), lead_id),
        )


def na_verzending(lead_id: str) -> None:
    """Boekhouding na een echt verstuurde follow-up (het versturen zelf
    gebeurt in router.py, náást de bestaande outreach-approve-route, zodat
    dezelfde Outlook/opt-out/validatie-guards gelden)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET followup_count = followup_count + 1, followup_sent_at = ?, "
            "followup_subject = '', followup_draft = '', followup_drafted_at = '' WHERE id = ?",
            (_now(), lead_id),
        )
    log_outcome(
        "Leads", "lead_followup_verstuurd",
        f"Opvolgmail verstuurd naar lead {lead_id}.",
    )

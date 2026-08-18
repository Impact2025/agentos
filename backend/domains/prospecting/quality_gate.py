"""Quality Gate — de 'gooi de mismatches weg'-stap uit de Hermes Lead Machine.

In de video ('Build a 24/7 lead generation agent') is de SCORE-stap de enige
echte slimme zet: het systeem rangschikt elke lead 0-100 op fit en verwijdert
daarna stil degenen die niet passen. "Fewer is better" — je besteedt je tijd
aan de mensen die wél gaan reageren. Dit is de geautomatiseerde versie daarvan,
ingebakken in de bestaande acquisitie-funnel (new → enriched → valid → ...).

De gate is DETERMINISTISCH (geen LLM):
  - harde ondergrens (QUALITY_MIN_SCORE): leads daaronder gaan naar 'lost'
    met reden, zodat ze de funnel-invoer niet meer vervuilen.
  - zachte drempel (QUALITY_TARGET_SCORE): leads daaronder blijven staan als
    'review_laag' (je ziet ze, maar de batch prefereert de hoge).
  - een fit-label (A/B/C) voor snelle triage in de UI.

Alles GDPR-safe: de gate verstuurt niets, verwijdert niets definitief (lead
blijft in de DB met status 'lost'), en logt één outcome-kaart met next_step.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

# Drempels (0-100). Onder MIN = geen serieuze fit → lost.
# Tussen MIN en TARGET = matig; boven TARGET = scherpe fit (A).
QUALITY_MIN_SCORE = 40
QUALITY_TARGET_SCORE = 70

# Fit-labels op basis van score (voor de UI-pill).
FIT_LABELS = {90: "A", 70: "B", 40: "C"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fit_label(score: int) -> str:
    """A/B/C-label voor snelle triage."""
    try:
        s = int(score)
    except (TypeError, ValueError):
        s = 0
    if s >= 90:
        return "A"
    if s >= 70:
        return "B"
    if s >= 40:
        return "C"
    return "D"


def _serialize_score(lead: Dict[str, Any]) -> int:
    """Lees de score veilig (kan None zijn als analyse mislukte)."""
    s = lead.get("score")
    if s is None:
        return 0
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def run_quality_gate(status_filter: Tuple[str, ...] = ("new", "enriched", "valid"),
                     dry_run: bool = False) -> Dict[str, Any]:
    """Beoordeel elke onbenaderde lead op fit en zet mismatches naar 'lost'.

    De gate kijkt naar leads die nog niet in de outreach-fase zitten (dus
    new/enriched/valid). Sub-drempel → status 'lost' + reason 'quality_gate'.
    Dit is het automatische 'we gooien de mismatches weg' uit de video.

    Returns een rapport met aantallen per actie. Bij dry_run worden er GEEN
    wijzigingen geschreven (je ziet eerst wat er zou gebeuren).
    """
    now = _now()
    with get_conn() as conn:
        placeholders = ",".join("?" for _ in status_filter)
        rows = conn.execute(
            f"SELECT * FROM leads WHERE status IN ({placeholders})",
            status_filter,
        ).fetchall()

    promoted, discarded, kept = [], [], []
    for r in rows:
        lead = dict(r)
        score = _serialize_score(lead)
        label = fit_label(score)
        new_status = None
        reason = ""
        if score < QUALITY_MIN_SCORE:
            new_status = "lost"
            reason = f"quality_gate: score {score} < {QUALITY_MIN_SCORE} (geen fit)"
            discarded.append({"id": lead["id"], "org_name": lead["org_name"], "score": score})
        elif label == "A":
            # Scherpe fit: bevorder naar 'valid' als 'ie nog lager in de funnel staat,
            # zodat de outreach-batch 'm als eerste pakt. B2C-achtige afzijdigheid
            # wordt hier niet beoordeeld — dat doet valid_target() bij verzending.
            if lead.get("status") in ("new", "enriched"):
                new_status = "valid"
                reason = f"quality_gate: scherpe fit (score {score})"
                promoted.append({"id": lead["id"], "org_name": lead["org_name"], "score": score})

        if new_status and not dry_run:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE leads SET status = ?, quality_score = ?, quality_label = ?, "
                    "quality_reason = ?, lost_at = ?, updated_at = ? WHERE id = ?",
                    (new_status, score, label, reason, now if new_status == "lost" else "",
                     now, lead["id"]),
                )
        elif new_status:
            # dry-run: tel wel mee in het rapport
            if new_status == "lost":
                discarded.append({"id": lead["id"], "org_name": lead["org_name"], "score": score})
            else:
                promoted.append({"id": lead["id"], "org_name": lead["org_name"], "score": score})
        else:
            kept.append({"id": lead["id"], "org_name": lead["org_name"], "score": score,
                         "label": label})

    if not dry_run:
        log_outcome(
            "Leads", "quality_gate",
            f"Quality Gate: {promoted and len(promoted) or 0} bevorderd naar valid, "
            f"{len(discarded)} naar lost (geen fit), {len(kept)} behouden.",
            next_step=(
                "De funnel-invoer is eerlijk: mismatches liggen in 'lost'. "
                "Draai de outreach-batch voor de scherpe fits."
                if discarded else
                "Geen mismatches — de voorraad is al scherp."
            ),
            status="ok",
        )

    return {
        "promoted": promoted,
        "discarded": discarded,
        "kept": kept,
        "dry_run": dry_run,
        "thresholds": {
            "min": QUALITY_MIN_SCORE,
            "target": QUALITY_TARGET_SCORE,
        },
    }


def score_lead(lead_id: str) -> Optional[Dict[str, Any]]:
    """Reken het fit-label + quality_score voor één lead bij (zonder status-wijziging)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        return None
    lead = dict(row)
    score = _serialize_score(lead)
    label = fit_label(score)
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET quality_score = ?, quality_label = ? WHERE id = ?",
            (score, label, lead_id),
        )
    return {**lead, "quality_score": score, "quality_label": label}


def quality_summary() -> Dict[str, Any]:
    """Snelle stand voor de UI: verdeling over fit-labels + drempels."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_label = dict(conn.execute(
            "SELECT quality_label, COUNT(*) FROM leads WHERE quality_label != '' "
            "GROUP BY quality_label"
        ).fetchall())
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM leads GROUP BY status"
        ).fetchall())
    return {
        "total": total,
        "by_fit_label": by_label,
        "by_status": by_status,
        "thresholds": {"min": QUALITY_MIN_SCORE, "target": QUALITY_TARGET_SCORE},
    }

"""Outreach-leerlus — welke concept-stijl levert aantoonbaar replies op.

De funnel meet al óf een lead reageert (`contacted_at`/`replied_at`); deze
module legt vast met wélke aanpak elk concept geschreven is en rekent dat
wekelijks af. Drie stijl-dimensies met elk twee waarden worden deterministisch
over de leads gespreid (hash van het lead-id — geen loterij, wel spreiding),
zodat er vanzelf een eerlijk A/B-beeld ontstaat.

De evaluatie is puur regelgebaseerd (reply-rates uit de leads-tabel, met een
rijpingstijd en een minimum-steekproef) en schrijft lessen + falsifieerbare
voorspellingen naar het generieke leer-raamwerk (`shared/learning.py`). De
lessen stromen als promptblok terug in `_draft_prompt` — de schrijver leert,
maar verstuurt nog steeds niets: de review-gate blijft onaangeraakt.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...shared import learning
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

AGENT = "outreach"

# Stijl-dimensies. Waarden bewust beperkt tot 2 per dimensie: met de huidige
# batch-omvang duurt het anders maanden voor een cel genoeg steekproef heeft.
VARIANT_DIMENSIONS: Dict[str, tuple[str, str]] = {
    "opening": ("observatie", "vraag"),
    "toon": ("direct", "warm"),
    "lengte": ("kort", "middel"),
}

_INSTRUCTIONS: Dict[tuple[str, str], str] = {
    ("opening", "observatie"): (
        "Open met een concrete observatie over HUN organisatie of website "
        "(uit 'wat we over hen weten') — geen vraag."
    ),
    ("opening", "vraag"): (
        "Open met één specifieke, oprechte vraag over hun werk of organisatie "
        "(geen retorische verkoopvraag)."
    ),
    ("toon", "direct"): "Toon: zakelijk en direct — kom binnen twee zinnen ter zake.",
    ("toon", "warm"): "Toon: warm en betrokken — toon oprechte interesse in hun missie.",
    ("lengte", "kort"): "Lengte: maximaal 90 woorden.",
    ("lengte", "middel"): "Lengte: 110 tot 130 woorden.",
}

# Evaluatie-drempels. Replies hebben tijd nodig: een mail van gisteren zonder
# antwoord zegt niets, dus een concept telt pas mee na de rijpingstijd.
RIPEN_DAYS = 10
MIN_PER_VALUE = 8          # minimum verstuurde mails per variantwaarde
MIN_GAP_PP = 5.0           # minimaal verschil in procentpunten voor een les
PREDICTION_HORIZON_DAYS = 14
PREDICTION_KEEP_GAP_PP = 1.0  # voorspelling: de kloof blijft ≥ 1 pp


def choose_variant(lead_id: str) -> Dict[str, str]:
    """Deterministische variant-keuze per lead: zelfde lead → zelfde stijl,
    en over veel leads een gelijkmatige spreiding per dimensie."""
    variant = {}
    for dim, values in VARIANT_DIMENSIONS.items():
        digest = hashlib.md5(f"{lead_id}:{dim}".encode()).digest()
        variant[dim] = values[digest[0] % len(values)]
    return variant


def variant_instructions(variant: Dict[str, str]) -> List[str]:
    """De prompt-eisen die bij deze variant horen (vervangen de vaste
    opening/lengte-eisen uit het basisconcept)."""
    lines = []
    for dim, values in VARIANT_DIMENSIONS.items():
        value = variant.get(dim, values[0])
        lines.append(_INSTRUCTIONS.get((dim, value), ""))
    return [l for l in lines if l]


# ── Meting ─────────────────────────────────────────────────────────────────

def _ripe_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=RIPEN_DAYS)).isoformat()


def variant_stats() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per dimensie per waarde: verstuurd / replies / reply-rate (%), over
    alle gerijpte, daadwerkelijk verstuurde concepten met een variant-label."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT outreach_variant, replied_at FROM leads "
            "WHERE outreach_variant != '' AND contacted_at != '' AND contacted_at <= ?",
            (_ripe_cutoff(),),
        ).fetchall()
    stats: Dict[str, Dict[str, Dict[str, Any]]] = {
        dim: {v: {"sent": 0, "replied": 0} for v in values}
        for dim, values in VARIANT_DIMENSIONS.items()
    }
    for row in rows:
        try:
            variant = json.loads(row["outreach_variant"])
        except Exception:
            continue
        for dim, values in VARIANT_DIMENSIONS.items():
            value = variant.get(dim)
            if value in values:
                stats[dim][value]["sent"] += 1
                if row["replied_at"]:
                    stats[dim][value]["replied"] += 1
    for dim in stats:
        for value, s in stats[dim].items():
            s["rate"] = round(s["replied"] / s["sent"] * 100, 1) if s["sent"] else None
    return stats


def _gap_pp(dim: str, winner: str, loser: str) -> Optional[float]:
    """Huidige reply-rate-kloof (procentpunten) tussen twee variantwaarden.
    None zolang een van beide onder de minimum-steekproef zit."""
    stats = variant_stats().get(dim, {})
    a, b = stats.get(winner), stats.get(loser)
    if not a or not b or a["sent"] < MIN_PER_VALUE or b["sent"] < MIN_PER_VALUE:
        return None
    return round(a["rate"] - b["rate"], 1)


def _resolver(metric: str, context: str) -> Optional[float]:
    """Resolver voor het leer-raamwerk: 'reply_rate_gap' met context
    'dim:winnaar>verliezer' → de actuele kloof in procentpunten."""
    if metric != "reply_rate_gap" or ">" not in context or ":" not in context:
        return None
    dim, _, pair = context.partition(":")
    winner, _, loser = pair.partition(">")
    if dim not in VARIANT_DIMENSIONS:
        return None
    return _gap_pp(dim, winner, loser)


# ── Evaluatie (wekelijkse job) ─────────────────────────────────────────────

def run_outreach_learning_eval() -> Dict[str, Any]:
    """Wekelijks: (1) reken vervallen voorspellingen af tegen de echte
    reply-cijfers, (2) destilleer nieuwe/bevestigde lessen uit de huidige
    variant-statistieken, elk met een verse toetsbare voorspelling.

    Meldt zich alleen in het Actiecentrum als er echt iets gebeurd is —
    een wekelijkse "nog niets te leren"-kaart is ruis."""
    verdict = learning.evaluate_due(AGENT, _resolver)
    stats = variant_stats()
    new_lessons: List[str] = []

    for dim, values in VARIANT_DIMENSIONS.items():
        v1, v2 = values
        s1, s2 = stats[dim][v1], stats[dim][v2]
        if s1["sent"] < MIN_PER_VALUE or s2["sent"] < MIN_PER_VALUE:
            continue
        gap = s1["rate"] - s2["rate"]
        if abs(gap) < MIN_GAP_PP:
            continue
        winner, loser = (v1, v2) if gap > 0 else (v2, v1)
        w, l = stats[dim][winner], stats[dim][loser]
        # Stabiele les-tekst (dedupe!); de wisselende cijfers gaan in evidence.
        lesson_text = (f"Outreach met {dim} '{winner}' levert meer replies op "
                       f"dan '{loser}'.")
        lesson_id = learning.upsert_lesson(
            AGENT, lesson_text, category="outreach-stijl",
            evidence={
                "dimensie": dim,
                winner: {"verstuurd": w["sent"], "replies": w["replied"], "rate_pct": w["rate"]},
                loser: {"verstuurd": l["sent"], "replies": l["replied"], "rate_pct": l["rate"]},
                "kloof_pp": round(abs(gap), 1),
            },
        )
        if lesson_id:
            new_lessons.append(lesson_text)
            learning.record_prediction(
                AGENT,
                metric="reply_rate_gap",
                context=f"{dim}:{winner}>{loser}",
                direction="up",
                comparison="threshold",
                baseline=round(abs(gap), 1),
                target=PREDICTION_KEEP_GAP_PP,
                horizon_days=PREDICTION_HORIZON_DAYS,
                lesson_id=lesson_id,
                statement=(f"Over {PREDICTION_HORIZON_DAYS} dagen is de reply-rate van "
                           f"{dim}='{winner}' nog ≥ {PREDICTION_KEEP_GAP_PP:g} procentpunt "
                           f"hoger dan die van '{loser}'."),
            )

    happened = bool(new_lessons or verdict["evaluated"])
    if happened:
        parts = []
        if verdict["evaluated"]:
            parts.append(f"{verdict['correct']} voorspelling(en) correct, "
                         f"{verdict['wrong']} fout, {verdict['unclear']} onduidelijk")
        if new_lessons:
            parts.append(f"{len(new_lessons)} les(sen) vastgelegd/bevestigd")
        log_outcome(
            "Leads", "outreach_leerlus",
            "Outreach-leerlus: " + " · ".join(parts),
            artifact="/api/learning/outreach",
            next_step="Niets — de geleerde stijl stroomt automatisch in de volgende concepten.",
        )
    logger.info("[outreach-leerlus] evaluatie klaar: %d afgerekend, %d les(sen)",
                len(verdict["evaluated"]), len(new_lessons))
    return {"verdict": verdict, "lessons": new_lessons, "stats": stats}

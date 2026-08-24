"""
Gauntlet brand brief — leest WeAreImpact's Schrijf-DNA (single source of truth in
de Obsidian-vault) en voedt die als gedeelde merk-brief aan elke builder in de
Gauntlet Loop.

Zonder deze brief schrijft elke builder generieke AI-kopij. Met deze brief
schrijft elke builder in Vincent's eigen stem (eerste persoon, Ervarings-Carrousel,
E-E-A-T, geen AI-clichés, geen em-dashes, geen verzonnen cijfers).

**Project-scoping (19 aug 2026)**: `get_brand_brief()` werd zonder project
aangeroepen en dus voor ELKE Gauntlet-run gebruikt, ook voor Bijeen,
Pootgelukkig, LiefdeVoorIedereen en TeambuildingMetImpact. De brief zegt
letterlijk "SCHRIJF ALS VINCENT VAN MUNSTER (WeAreImpact), eerste persoon" —
een instructie om als een specifieke, echt bestaande persoon te schrijven, mét
diens bedrijfsidentiteit, op de site van een ánder project. Gemeten gevolg op
Bijeen: een artikel opende met "in mijn jaren als directeur van Stichting de
Baan draaide ik meer dan veertig van die dagen, met 180+ vrijwilligers... " —
een volledig verzonnen naam, functie en trackrecord, want het model kreeg de
opdracht in eerste persoon met ervaring te schrijven zonder dat er voor Bijeen
een echte identiteit of biografie beschikbaar was. `get_brand_brief(project)`
geeft de Vincent/WeAreImpact-brief nu alleen aan WeAreImpact-runs; elk ander
project krijgt `_GENERIC_BRIEF` — wél de feitenregels (geen AI-clichés, geen
em-dashes, geen verzonnen cijfers), nooit de opdracht om als een specifieke
naam te schrijven. Invariant: `merkbrief_verkeerd_project`.

De brief wordt één keer per run opgehaald (en gecached per project) zodat alle
parallelle deeltaken dezelfde merk-context delen.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from ...shared.projects import squash_project

logger = logging.getLogger(__name__)

_WEAREIMPACT_SQUASH = "weareimpact"

# Neutrale brief voor elk project dat niet WeAreImpact is: dezelfde harde
# feitenregels, maar zonder Vincent's persoonlijke identiteit op te leggen aan
# een merk waar hij niet de stem van is.
_GENERIC_BRIEF = (
    "SCHRIJF-BRIEF (generiek, geen specifiek merk-DNA beschikbaar voor dit "
    "project). Schrijf in de derde persoon of als 'wij' namens het bedrijf — "
    "NOOIT als een specifieke, met naam genoemde persoon, tenzij die naam en "
    "diens achtergrond letterlijk in de meegeleverde context staan. Verzin "
    "GEEN functietitel, dienstverband, jarenlange ervaring of trackrecord voor "
    "wie dan ook — een onbekende auteur zonder geclaimde autoriteit is beter "
    "dan een geloofwaardig klinkende, verzonnen auteur. Toon: direct, nuchter, "
    "geen corporate jargon, geen wollige beleidstaal, geen AI-hype. Geen "
    "em-dashes, geen verzonnen statistieken — alleen harde, traceerbare data "
    "uit de meegeleverde context. Eindig met een scherpe, relevante CTA."
)

# Pad naar de vault-note met Vincent's Schrijf-DNA (harde SSoT voor WeAreImpact).
_SCHRIJF_DNA_PATH = (
    "D:/APPS/Hermes Brein/Hermes Breind/"
    "10_Projects/Pootgelukkig/SCHRIJF-DNA-Vincent.md"
)

# Fallback-brief als de vault-note niet leesbaar is (zodat de Gauntlet nooit crasht).
_FALLBACK_BRIEF = (
    "SCHRIJF ALS VINCENT VAN MUNSTER (WeAreImpact), eerste persoon ('ik'/'mijn'). "
    "Toon: direct, nuchter, droog, ondernemend, tech-realistisch. Spreek op "
    "ooghoogte met wethouders en bestuurders in het sociaal domein. Geen "
    "corporate jargon, geen wollige beleidstaal, geen AI-hype. Technologie is "
    "achtergrond; de menselijke professional staat centraal. Geen em-dashes, "
    "geen bullet points in de hoofdtekst, geen verzonnen statistieken — alleen "
    "harde, traceerbare data. Eindig met een scherpe CTA naar een gesprek."
)

_cached_weareimpact_brief: Optional[str] = None
_cache_loaded = False


def _load_weareimpact_brief() -> str:
    global _cached_weareimpact_brief, _cache_loaded
    if _cache_loaded:
        return _cached_weareimpact_brief or _FALLBACK_BRIEF
    _cache_loaded = True
    try:
        with open(_SCHRIJF_DNA_PATH, "r", encoding="utf-8") as fh:
            raw = fh.read()
        # Knip de YAML-frontmatter (--- ... ---) weg; alleen de inhoud telt.
        body = raw
        if body.startswith("---"):
            end = body.find("\n---", 3)
            if end != -1:
                body = body[end + 4:].strip()
        if not body.strip():
            _cached_weareimpact_brief = _FALLBACK_BRIEF
        else:
            _cached_weareimpact_brief = (
                "SCHRIJF-BRIEF (WeAreImpact / Vincent van Munster) — volg deze "
                "stijl en identiteit STRIKT voor je deeltaak:\n\n" + body
            )
        logger.info("Gauntlet brand brief geladen uit vault (%d tekens).", len(_cached_weareimpact_brief))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon Schrijf-DNA niet laden (%s); val terug op fallback-brief.", exc)
        _cached_weareimpact_brief = _FALLBACK_BRIEF
    return _cached_weareimpact_brief


def get_brand_brief(project: Optional[str] = None) -> str:
    """Merk-brief voor de Gauntlet-builders, gescoped op project.

    Alleen WeAreImpact krijgt Vincent's persoonlijke Schrijf-DNA (eerste
    persoon, zijn identiteit) — elk ander project krijgt `_GENERIC_BRIEF`.
    `project=None` (herkomst niet te bepalen uit de benchmark) valt ook terug
    op de generieke brief: NOOIT als Vincent schrijven is de veilige default,
    andersom niet.
    """
    if project and squash_project(project) == _WEAREIMPACT_SQUASH:
        return _load_weareimpact_brief()
    return _GENERIC_BRIEF


def reload_brand_brief() -> None:
    """Forceer herlezen bij de volgende run (bv. na een vault-wijziging)."""
    global _cached_weareimpact_brief, _cache_loaded
    _cached_weareimpact_brief = None
    _cache_loaded = False

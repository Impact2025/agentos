"""
Gauntlet brand brief — leest WeAreImpact's Schrijf-DNA (single source of truth in
de Obsidian-vault) en voedt die als gedeelde merk-brief aan elke builder in de
Gauntlet Loop.

Zonder deze brief schrijft elke builder generieke AI-kopij. Met deze brief
schrijft elke builder in Vincent's eigen stem (eerste persoon, Ervarings-Carrousel,
E-E-A-T, geen AI-clichés, geen em-dashes, geen verzonnen cijfers).

De brief wordt één keer per run opgehaald (en gecached) zodat alle parallelle
deeltaken dezelfde merk-context delen.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

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

_cached_brief: Optional[str] = None
_cache_loaded = False


def get_brand_brief() -> str:
    """Leest (en cached) de WeAreImpact-merkbrief voor de Gauntlet-builders."""
    global _cached_brief, _cache_loaded
    if _cache_loaded:
        return _cached_brief or _FALLBACK_BRIEF
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
            _cached_brief = _FALLBACK_BRIEF
        else:
            _cached_brief = (
                "SCHRIJF-BRIEF (WeAreImpact / Vincent van Munster) — volg deze "
                "stijl en identiteit STRIKT voor je deeltaak:\n\n" + body
            )
        logger.info("Gauntlet brand brief geladen uit vault (%d tekens).", len(_cached_brief))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon Schrijf-DNA niet laden (%s); val terug op fallback-brief.", exc)
        _cached_brief = _FALLBACK_BRIEF
    return _cached_brief


def reload_brand_brief() -> None:
    """Forceer herlezen bij de volgende run (bv. na een vault-wijziging)."""
    global _cached_brief, _cache_loaded
    _cached_brief = None
    _cache_loaded = False

"""Iris' uitvoer-hendels — van advies naar agents aan het werk.

Iris adviseerde eerst alleen ("start een publicatie-run", "doe outreach");
deze module laat haar dat zélf oppakken. Drie acties, allemaal veilig omdat
het resultaat ALTIJD achter een bestaande review-gate landt:

1. content_run   — start de contentmotor voor een site; artikelen worden
                   `pending_review` in de Wachtrij (publiceert niets).
2. outreach_run  — zet outreach-concepten klaar als `outreach_review`;
                   versturen kan alleen via de Actiecentrum-knop.
3. seo_refresh   — verrijkt de sterkst wegzakkende pagina's (open
                   refresh-suggesties van de Optimizer) → review-job in
                   de Wachtrij.

Elke actie logt een uitkomst-kaart (`iris_actie`) met artefact en next_step,
en draait maximaal één keer per dag per doelwit — een herrun van de briefing
("Analyseer nu") mag niet dezelfde batch nog eens aanzwengelen.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

ACTION = "iris_actie"

# Klemmen per actie: Iris mag aanzwengelen, niet leegtrekken (LLM-kosten,
# rate-limits en een Wachtrij die Vincent nog moet kunnen bijbenen).
_CONTENT_RUN_MAX = 3
_OUTREACH_MAX = 15
_SEO_REFRESH_MAX = 2


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _already_done_today(project: str, prefix: str) -> bool:
    """Zelfde actie voor hetzelfde doelwit al gedraaid vandaag? Dan overslaan."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM activity_log WHERE action = ? AND project = ? "
            "AND detail LIKE ? AND date(created_at) = date('now', 'localtime') LIMIT 1",
            (ACTION, project, prefix + "%"),
        ).fetchone()
    return row is not None


def _resolve_site(ref: str) -> Optional[dict]:
    from ..seo import optimizer
    return optimizer.resolve_site((ref or "").strip())


async def content_run(site_ref: str, count: Any, reason: str) -> Optional[str]:
    """Start de contentmotor voor één site; jobs landen in de Wachtrij."""
    site = _resolve_site(site_ref)
    if not site:
        logger.warning("[iris] content_run: site '%s' niet gevonden", site_ref)
        return None
    n = _clamp(count, 1, _CONTENT_RUN_MAX, 1)
    if _already_done_today(site["name"], "Contentmotor gestart"):
        logger.info("[iris] content_run voor %s vandaag al gedraaid — overgeslagen", site["name"])
        return None
    try:
        import asyncio
        from ..publish import content_pipeline
        job_ids = await content_pipeline.run_content_batch(site, count=n)
        if not job_ids:
            # Geen kansen: draai éérst een Demand-scan (die voor sites zonder
            # rankings een cold-start uit het site-profiel doet) en probeer
            # dan nog één keer — zo blijft een verse site niet op 0 hangen.
            from ..seo import engine as demand_engine
            scan = await asyncio.to_thread(demand_engine.scan_site, site)
            logger.info("[iris] content_run: demand-scan voor %s → %d kansen (%d cold-start)",
                        site["name"], scan.get("new", 0), scan.get("cold_start", 0))
            if scan.get("new"):
                job_ids = await content_pipeline.run_content_batch(site, count=n)
    except Exception as e:
        logger.exception("[iris] content_run mislukt voor %s", site["name"])
        log_outcome(
            site["name"], ACTION,
            f"Contentmotor gestart door Iris maar gefaald: {str(e)[:200]}",
            next_step="Bekijk logs/agentos.log en draai de batch handmatig (Wachtrij → run-now).",
            status="error",
        )
        return None
    if not job_ids:
        detail = (f"Contentmotor gestart voor {site['name']}, maar geen kansen — "
                  "ook de Demand-scan (incl. cold-start) leverde niets op")
        log_outcome(site["name"], ACTION, detail,
                    next_step=f"Vul het site-profiel van {site['name']} in de kennisbank aan; "
                              "zonder profiel kan de cold-start geen zoekwoorden bedenken.")
        return f"{detail}. Reden: {reason}"
    detail = (f"Contentmotor gestart voor {site['name']}: {len(job_ids)} artikel(en) "
              "geschreven, klaar in de Wachtrij")
    log_outcome(site["name"], ACTION, detail, artifact=f"/api/content-queue/{job_ids[0]}",
                next_step=f"Keur de {len(job_ids)} nieuwe artikel(en) goed in de Wachtrij — "
                          "pas na jouw klik gaat er iets live.")
    return f"{detail}. Reden: {reason}"


async def outreach_run(count: Any, reason: str) -> Optional[str]:
    """Zet een extra outreach-batch klaar ter review; verstuurt niets."""
    n = _clamp(count, 1, _OUTREACH_MAX, 5)
    if _already_done_today("Leads", "Outreach-batch gestart"):
        logger.info("[iris] outreach_run vandaag al gedraaid — overgeslagen")
        return None
    try:
        from ..prospecting import outreach
        result = await outreach.prepare_outreach_batch(count=n)
    except Exception as e:
        logger.exception("[iris] outreach_run mislukt")
        log_outcome(
            "Leads", ACTION, f"Outreach-batch gestart door Iris maar gefaald: {str(e)[:200]}",
            next_step="Controleer de LLM-configuratie en draai de batch handmatig "
                      "(POST /api/leads/outreach-batch).",
            status="error",
        )
        return None
    drafted = result.get("drafted", 0)
    detail = (f"Outreach-batch gestart: {drafted} concept(en) klaargezet ter review"
              if drafted else
              "Outreach-batch gestart, maar geen bruikbare leads — funnel-invoer is op")
    # prepare_outreach_batch logt zelf al de batch-uitkomst met next_step;
    # deze kaart maakt zichtbaar dat Iris de opdrachtgever was.
    log_outcome("Leads", ACTION, detail, artifact="/api/leads/funnel",
                next_step=("Keur de concepten goed in het Actiecentrum." if drafted else
                           "Draai een lead-zoekactie (Leads-tab) om de funnel te vullen."))
    return f"{detail}. Reden: {reason}"


async def seo_refresh(site_ref: str, count: Any, reason: str) -> Optional[str]:
    """Verrijk de sterkst wegzakkende pagina's van een site (open
    refresh-suggesties) — resultaat als review-job in de Wachtrij."""
    site = _resolve_site(site_ref)
    if not site:
        logger.warning("[iris] seo_refresh: site '%s' niet gevonden", site_ref)
        return None
    if _already_done_today(site["name"], "SEO-refresh gestart"):
        logger.info("[iris] seo_refresh voor %s vandaag al gedraaid — overgeslagen", site["name"])
        return None
    from ..seo import optimizer
    suggestions = optimizer.list_suggestions(site["id"], stype="refresh", status="new")
    if not suggestions:
        logger.info("[iris] seo_refresh: geen open refresh-suggesties voor %s", site["name"])
        return None
    n = _clamp(count, 1, _SEO_REFRESH_MAX, 1)
    job_ids, failed = [], 0
    for sug in suggestions[:n]:
        try:
            job_ids.append(await optimizer.refresh_article(sug, site))
        except Exception as e:
            failed += 1
            logger.warning("[iris] refresh van %s mislukt: %s", sug.get("page"), e)
    if not job_ids:
        log_outcome(
            site["name"], ACTION,
            f"SEO-refresh gestart door Iris maar alle {failed} poging(en) faalden",
            next_step="Bekijk de Optimalisatie-tab en probeer de refresh handmatig.",
            status="error",
        )
        return None
    detail = (f"SEO-refresh gestart voor {site['name']}: {len(job_ids)} wegzakkende "
              "pagina('s) verrijkt, klaar in de Wachtrij")
    log_outcome(site["name"], ACTION, detail, artifact=f"/api/content-queue/{job_ids[0]}",
                next_step="Keur de verrijkte artikel(en) goed in de Wachtrij.")
    return f"{detail}. Reden: {reason}"

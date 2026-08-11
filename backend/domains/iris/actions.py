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
4. linkbuilding_run — zet link-outreach-concepten klaar als
                   `outreach_review` in de linkbuilding-funnel;
                   versturen kan alleen via de Actiecentrum-knop.
5. lead_search_run — vult de acquisitie-funnel met verse leads
                   (zoeken → verrijken → opslaan als `new`); er wordt
                   niets gemaild — outreach heeft zijn eigen gate.

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
_LINKBUILD_MAX = 10
_LEAD_SEARCH_MAX_QUERIES = 6
_LEAD_SEARCH_PER_QUERY = 4


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _autonomy_max(project: str, column: str, fallback: int) -> int:
    """Iris-onboarding (project_autonomy) laat een klant zelf de bovengrens
    kiezen — ontbreekt er een rij (of die kolom), dan geldt de globale klem
    hierboven. Alleen content_run/seo_refresh zijn vandaag per site aan te
    roepen; outreach_run/linkbuilding_run werken op de héle funnel (geen
    site/project), dus die blijven op de globale constante."""
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {column} FROM project_autonomy WHERE project = ?", (project,),
        ).fetchone()
    if row and row[column] is not None:
        return int(row[column])
    return fallback


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


# Boven deze stapel is schrijven geen productie meer maar verstopping. Bewust
# per site: één project met een volle Wachtrij mag de contentmotor van een
# ander project niet stilzetten.
_QUEUE_JAM = 10


def _pending_review_count(site_id: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'pending_review'",
            (site_id,),
        ).fetchone()[0]


async def content_run(site_ref: str, count: Any, reason: str) -> Optional[str]:
    """Start de contentmotor voor één site; jobs landen in de Wachtrij."""
    site = _resolve_site(site_ref)
    if not site:
        logger.warning("[iris] content_run: site '%s' niet gevonden", site_ref)
        return None
    n = _clamp(count, 1, _autonomy_max(site["name"], "content_run_max", _CONTENT_RUN_MAX), 1)
    if _already_done_today(site["name"], "Contentmotor gestart"):
        logger.info("[iris] content_run voor %s vandaag al gedraaid — overgeslagen", site["name"])
        # Benigne skip, geen fout: een kale None zou de fix-knop onterecht op
        # 'Mislukt' (HTTP 400) zetten (zie seo_refresh). Meld het als uitkomst.
        return (f"Contentmotor voor {site['name']} draaide vandaag al — geen tweede "
                f"run (dedup houdt de Wachtrij en LLM-kosten in toom). Reden: {reason}")
    # Doorvoer-rem: schrijven terwijl de Wachtrij vastloopt levert per definitie
    # niets op. Het artikel komt op dezelfde stapel, kost LLM-budget, en maakt
    # de review-berg waar de opbrengst vandaan moet komen alleen hoger. Een
    # actie die niets kan opleveren hoort niet te draaien — ook niet als de
    # cijfers "te weinig content" zeggen.
    wachtrij = _pending_review_count(site["id"])
    if wachtrij >= _QUEUE_JAM:
        logger.info("[iris] content_run voor %s overgeslagen — %d concepten in de Wachtrij",
                    site["name"], wachtrij)
        return (f"Contentmotor voor {site['name']} NIET gestart: er wachten al {wachtrij} "
                "concepten op goedkeuring. Nog een artikel schrijven maakt die stapel "
                "groter zonder één klik op te leveren — beoordeel eerst de Wachtrij.")
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
        # Benigne skip, geen fout (zie content_run/seo_refresh).
        return (f"Outreach-batch draaide vandaag al — geen tweede run (dedup). "
                f"Reden: {reason}")
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


async def lead_search_run(queries: Any, reason: str, template: str = "",
                          lead_type: str = "") -> Optional[str]:
    """Vul de acquisitie-funnel: zoek, verrijk en bewaar nieuwe leads.

    Veilig zonder extra gate: leads landen als `new`/`enriched` in de database
    en de Leads-tab — er wordt niets gemaild (outreach kent zijn eigen
    review-gate). Iris geeft bij voorkeur eigen zoekopdrachten mee; zonder
    queries pakt ze een greep uit de sector-templates."""
    from ..prospecting.service import BATCH_TEMPLATES, TEMPLATE_LEAD_TYPE, LeadsService
    if _already_done_today("Leads", "Lead-zoekactie gestart"):
        logger.info("[iris] lead_search_run vandaag al gedraaid — overgeslagen")
        return (f"Lead-zoekactie draaide vandaag al — geen tweede run (dedup houdt "
                f"de zoek-quota in toom). Reden: {reason}")
    qlist = [str(q).strip() for q in (queries or []) if str(q).strip()][:_LEAD_SEARCH_MAX_QUERIES]
    tmpl = (template or "").strip().lower()
    if not qlist:
        import random
        pool = BATCH_TEMPLATES.get(tmpl) or BATCH_TEMPLATES["weareimpact_ai"]
        qlist = random.sample(pool, min(5, len(pool)))
    ltype = (lead_type or "").strip() or TEMPLATE_LEAD_TYPE.get(tmpl, "overig")
    try:
        import asyncio
        result = await asyncio.to_thread(
            LeadsService().run_search_batch, qlist, ltype, _LEAD_SEARCH_PER_QUERY)
    except Exception as e:
        logger.exception("[iris] lead_search_run mislukt")
        log_outcome(
            "Leads", ACTION,
            f"Lead-zoekactie gestart door Iris maar gefaald: {str(e)[:200]}",
            next_step="Controleer de zoekprovider-quota (Tavily/Brave in .env) en "
                      "draai de zoekactie handmatig via de Leads-tab.",
            status="error",
        )
        return None
    saved = result.get("saved", 0)
    detail = (f"Lead-zoekactie gestart: {saved} nieuwe lead(s) gevonden en bewaard "
              f"({result.get('queries', 0)} zoekopdrachten)"
              if saved else
              f"Lead-zoekactie gestart ({result.get('queries', 0)} zoekopdrachten), "
              "maar geen nieuwe leads — alles was al bekend of niet bruikbaar")
    log_outcome("Leads", ACTION, detail, artifact="/api/leads/funnel",
                next_step=("Bekijk de nieuwe leads in de Leads-tab; bruikbare leads "
                           "gaan via een outreach-batch (review-gate) de funnel in."
                           if saved else
                           "Geef Iris in de volgende briefing scherpere zoekopdrachten "
                           "of kies een andere sector-template."))
    return f"{detail}. Reden: {reason}"


async def linkbuilding_run(count: Any, reason: str) -> Optional[str]:
    """Zet een extra linkbuilding-outreach-batch klaar ter review; verstuurt niets."""
    n = _clamp(count, 1, _LINKBUILD_MAX, 5)
    if _already_done_today("Linkbuilding", "Linkbuilding-batch gestart"):
        logger.info("[iris] linkbuilding_run vandaag al gedraaid — overgeslagen")
        # Benigne skip, geen fout (zie content_run/seo_refresh).
        return (f"Linkbuilding-batch draaide vandaag al — geen tweede run (dedup). "
                f"Reden: {reason}")
    try:
        from ..linkbuilding import outreach as lb_outreach
        result = await lb_outreach.prepare_linkbuilding_batch(count=n)
    except Exception as e:
        logger.exception("[iris] linkbuilding_run mislukt")
        log_outcome(
            "Linkbuilding", ACTION,
            f"Linkbuilding-batch gestart door Iris maar gefaald: {str(e)[:200]}",
            next_step="Controleer de LLM-configuratie en draai de batch handmatig "
                      "(POST /api/linkbuilding/outreach-batch).",
            status="error",
        )
        return None
    drafted = result.get("drafted", 0)
    detail = (f"Linkbuilding-batch gestart: {drafted} concept(en) klaargezet ter review"
              if drafted else
              "Linkbuilding-batch gestart, maar geen gekwalificeerde linkkansen met "
              "contactadres — de prospect-voorraad is op")
    log_outcome("Linkbuilding", ACTION, detail, artifact="/api/linkbuilding/funnel",
                next_step=("Keur de concepten goed in het Actiecentrum." if drafted else
                           "Draai een prospect-run (POST /api/linkbuilding/prospect-run) "
                           "om de linkkans-voorraad te vullen."))
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
        # Benigne skip, geen fout (zie de SPA-shell-tak verderop).
        return (f"SEO-refresh voor {site['name']} draaide vandaag al — geen tweede "
                f"run (dedup). Reden: {reason}")
    from ..seo import optimizer
    suggestions = optimizer.list_suggestions(site["id"], stype="refresh", status="new")
    if not suggestions:
        logger.info("[iris] seo_refresh: geen open refresh-suggesties voor %s", site["name"])
        # Benigne: niks te verrijken (een eerdere refresh heeft de open
        # suggesties al verwerkt). Geen None → geen valse HTTP 400.
        return (f"SEO-refresh voor {site['name']}: geen open refresh-kandidaten meer "
                f"— de wegzakkende pagina's zijn al verrijkt. Reden: {reason}")
    n = _clamp(count, 1, _autonomy_max(site["name"], "seo_refresh_max", _SEO_REFRESH_MAX), 1)
    job_ids, failed, skipped = [], 0, 0
    # Loop door álle open suggesties tot er n échte refreshes gelukt zijn:
    # een SPA-shell (homepage) mag de poging niet opbranden terwijl er
    # verrijkbare pagina's in de rij staan.
    for sug in suggestions:
        if len(job_ids) >= n:
            break
        try:
            job_ids.append(await optimizer.refresh_article(sug, site))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            # Een SPA-shell (bv. de homepage) heeft geen server-rendered
            # body-tekst → refresh_article kan de pagina niet ophalen. Dat is
            # géén bug om dagelijks op te blijven proberen: markeer de
            # suggestie als afgehandeld en log een heldere notitie i.p.v. een
            # rode foutkaart die elke Iris-run terugkomt.
            if "pagina-inhoud niet ophalen" in msg or "te kort" in msg:
                skipped += 1
                optimizer._update_suggestion(sug["id"], status="done")
                log_outcome(
                    site["name"], ACTION,
                    f"SEO-refresh overgeslagen voor {sug.get('page')}: pagina is een "
                    f"SPA-shell zonder extracteerbare body-tekst — niet automatisch "
                    f"verwerkbaar.",
                    next_step="Ververs de pagina handmatig in het CMS (de homepage "
                              "is een Next.js-SPA zonder server-rendered tekst).",
                    status="ok",
                )
            else:
                failed += 1
                logger.warning("[iris] refresh van %s mislukt: %s", sug.get("page"), e)
    if not job_ids:
        if skipped and not failed:
            # Alles overgeslagen wegens SPA-shell: netjes afgehandeld (de
            # suggesties zijn gesloten), dus meld dat als uitkomst — een None
            # zou de fix-knop onterecht op 'Mislukt' (HTTP 400) zetten.
            return (f"SEO-refresh voor {site['name']}: {skipped} kandidaat/kandidaten "
                    "overgeslagen (SPA-shell zonder leesbare tekst) — niets te verrijken. "
                    f"Reden: {reason}")
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

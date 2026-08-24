"""Content Multiplier — de format-waaier uit één goedgekeurd artikel.

Goldie's "Infinite Content Engine"-schakel: één blogartikel dat de mens heeft
goedgekeurd (en dus live staat) wordt automatisch vermenigvuldigd naar de
andere formats die het OS al kán maken:

  1. Social-pack (`social_content.generate_content_pack`): per-platform copy
     (LinkedIn / Facebook / Instagram / TikTok / X), Canva-beeld-brief en een
     TikTok-scriptpack — landt als `pending_review` in Social Creatie.
  2. Verticale 9:16-video (`blog_video.make_blog_video`): eigen spreekbaar
     script uit het blog + Pexels-beeld + voice-over — verschijnt als
     SocialPack met Bekijk/Render-knoppen.

De infographic en quote-card bestaan al op de content_job zelf (aangemaakt
tijdens het schrijven), dus die doet deze module niet nog eens.

Hard principe: hier wordt NIETS gepost of gepubliceerd. De waaier landt
volledig achter de bestaande review-gates; posten kan alleen via de
approve/publish-endpoints die een mens aanklikt.

Triggers: (a) automatisch als achtergrondtaak na `approve_and_publish`
(vlag CONTENT_MULTIPLIER_ENABLED), (b) handmatig via
POST /api/content-queue/{id}/multiply.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from ...shared.database import get_conn

logger = logging.getLogger(__name__)


def _project_for_job(job: Dict) -> str:
    """Zelfde afleiding als content_queue.router: site.name == project-naam."""
    from ..seo import sites as sites_service
    site = sites_service.get_site(job.get("site_id")) or {}
    return (site.get("name") or job.get("site_id") or "").lower()


def _existing_pack_for_job(project: str, title: str) -> Optional[str]:
    """Vind een eerder gemaakte (niet-afgewezen) social-pack voor dit artikel.

    Dedupe op (project, theme=titel): een herrun van de multiplier — of een
    handmatige klik na de automatische run — mag de Social Creatie-tab niet
    volstorten met duplicaten van hetzelfde artikel."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM social_posts WHERE project = ? AND theme = ? "
            "AND status != 'rejected' ORDER BY created_at DESC LIMIT 1",
            (project, title),
        ).fetchone()
    return row["id"] if row else None


async def multiply_job(job_id: str, with_video: bool = True) -> Dict:
    """Genereer de format-waaier voor één gepubliceerde content-job.

    Vereist status 'published': we vermenigvuldigen alleen wat een mens al
    heeft goedgekeurd — nooit concepten. Retourneert een verslag-dict; elke
    deelstap faalt zacht zodat één kapot format de rest niet blokkeert."""
    from . import content_pipeline

    job = content_pipeline.get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    if job.get("status") != "published":
        raise ValueError(
            f"Job heeft status '{job.get('status')}' — de multiplier draait alleen "
            "op gepubliceerde (door een mens goedgekeurde) artikelen."
        )

    project = _project_for_job(job)
    title = (job.get("title") or "").strip()
    keyword = (job.get("keyword") or "").strip()
    result: Dict = {"job_id": job_id, "project": project,
                    "social_pack": None, "video": None}
    artifacts: List[str] = []

    # ── 1. Social-pack (alle platforms, achter de review-gate) ──────────────
    from ...shared import social_content as sc
    existing = _existing_pack_for_job(project, title)
    if existing:
        result["social_pack"] = {"skipped": True, "pack_id": existing,
                                 "reason": "bestaat al"}
    else:
        try:
            # Sync LLM-calls (httpx sync) — in een thread zodat de event-loop
            # vrij blijft voor de rest van het OS.
            pack = await asyncio.to_thread(
                sc.generate_content_pack,
                project=project,
                theme=title,
                angle=(f"Vermenigvuldigd uit het blogartikel over '{keyword}'"
                       if keyword else "Vermenigvuldigd uit een blogartikel"),
                with_image=True,
                with_video=True,
            )
            result["social_pack"] = {"pack_id": pack.id, "concept": pack.concept}
            artifacts.append(f"/api/social-content/packs/{pack.id}/export")
        except Exception as e:  # noqa: BLE001
            logger.warning("[multiplier] Social-pack mislukt voor %s: %s",
                           job_id, str(e)[:200])
            result["social_pack"] = {"error": str(e)[:200]}

    # ── 1b. DatingAssistent: doelgroep-varianten (30+/40+/50+) ──────────────
    # DatingAssistent heeft 3 leeftijdsgebonden FB-pagina's náást de hoofd-
    # pagina (`shared/facebook.py:check_age_targeting`, zie CLAUDE.md §7i) —
    # een blog dat bv. over "daten na je scheiding" gaat, leest voor een
    # 30-jarige anders dan voor een 55-jarige. We genereren hier alléén de
    # tekst-varianten (geen video, geen automatische post): de leeftijds-
    # pagina's hebben géén eigen `sites`-rij en dus geen page-token via de
    # gewone route, en `check_age_targeting` weigert bewust élke leeftijds-
    # geframede tekst op de hoofdpagina — posten kan alleen via het bestaande
    # doelgroep-mechanisme (`scripts/da_post_engine.py`), niet via de
    # standaard Plaats-knop. De varianten landen daarom als concept in Social
    # Creatie mét een next_step die dat expliciet maakt, i.p.v. een knop te
    # tonen die altijd zal falen.
    result["doelgroep_varianten"] = []
    if project == "datingassistent":
        _DOELGROEPEN = (
            ("30+", "Schrijf de invalshoek voor twintigers/dertigers: net op de datingmarkt, "
                    "drukke agenda, wil het slim aanpakken."),
            ("40+", "Schrijf de invalshoek voor veertigers: vaak een eerdere relatie/gezin achter "
                    "de rug, weet wat ze niet meer willen, zoekt oprechte verbinding."),
            ("50+", "Schrijf de invalshoek voor vijftigplussers: nieuw hoofdstuk na een lange "
                    "relatie of pensioen in zicht, zoekt gelijkwaardigheid en gezelschap."),
        )
        for label, angle_hint in _DOELGROEPEN:
            variant_theme = f"{title} ({label})"
            if _existing_pack_for_job(project, variant_theme):
                result["doelgroep_varianten"].append({"doelgroep": label, "skipped": True})
                continue
            try:
                vpack = await asyncio.to_thread(
                    sc.generate_content_pack,
                    project=project,
                    theme=variant_theme,
                    angle=(f"Vermenigvuldigd uit het blogartikel over '{keyword}' — {angle_hint}"
                           if keyword else angle_hint),
                    platforms=["facebook", "instagram"],
                    with_image=False,
                    with_video=False,
                )
                result["doelgroep_varianten"].append(
                    {"doelgroep": label, "pack_id": vpack.id, "concept": vpack.concept})
                artifacts.append(f"/api/social-content/packs/{vpack.id}/export")
            except Exception as e:  # noqa: BLE001
                logger.warning("[multiplier] DA-doelgroepvariant %s mislukt voor %s: %s",
                               label, job_id, str(e)[:200])
                result["doelgroep_varianten"].append({"doelgroep": label, "error": str(e)[:200]})

    # ── 2. Blog → 9:16-video ────────────────────────────────────────────────
    # make_blog_video dedupet zelf (pack-id 'blog_{job_id}', video_path op de
    # job); een tweede run overschrijft hooguit het videobestand.
    if with_video and not (job.get("video_path") or "").strip():
        from ...shared import blog_video
        try:
            vres = await asyncio.to_thread(
                blog_video.make_blog_video,
                job_id, project, title, job.get("blog_html") or "",
            )
            result["video"] = {k: vres.get(k) for k in
                               ("success", "video_url", "video_path", "error")}
            if vres.get("success"):
                artifacts.append(vres.get("video_url") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("[multiplier] Blog-video mislukt voor %s: %s",
                           job_id, str(e)[:200])
            result["video"] = {"success": False, "error": str(e)[:200]}
    elif with_video:
        result["video"] = {"skipped": True, "reason": "video bestaat al"}

    # ── Uitkomstkaart ────────────────────────────────────────────────────────
    made = []
    if (result["social_pack"] or {}).get("pack_id") and not (result["social_pack"] or {}).get("skipped"):
        made.append("social-pack")
    if (result["video"] or {}).get("success"):
        made.append("video")
    variant_hits = [v for v in result.get("doelgroep_varianten", [])
                    if v.get("pack_id") and not v.get("skipped")]
    if variant_hits:
        made.append(f"{len(variant_hits)} doelgroepvariant(en)")
    failed = []
    if (result["social_pack"] or {}).get("error"):
        failed.append("social-pack")
    if (result["video"] or {}).get("error"):
        failed.append("video")
    if any(v.get("error") for v in result.get("doelgroep_varianten", [])):
        failed.append("doelgroepvariant")
    try:
        from ...shared.outcomes import log_outcome
        if made or failed:
            next_step = ("Bekijk en keur de posts/video in Social Creatie."
                         if made else "Bekijk de fout en probeer /multiply opnieuw.")
            if variant_hits:
                next_step += (" De doelgroepvarianten (30+/40+/50+) posten NIET via de "
                              "gewone Plaats-knop — die pagina's hebben geen eigen "
                              "site-token en de leeftijds-check op de hoofdpagina "
                              "weigert leeftijdsgeframede tekst bewust. Gebruik het "
                              "bestaande DA-doelgroepmechanisme (da_post_engine.py) "
                              "om ze op de juiste pagina te plaatsen.")
            log_outcome(
                project or "Multiplier", "content_multiplier",
                f"Format-waaier voor '{title}': "
                + (f"gemaakt: {', '.join(made)}" if made else "niets nieuws")
                + (f"; mislukt: {', '.join(failed)}" if failed else ""),
                artifact="; ".join(a for a in artifacts if a),
                next_step=next_step,
                status="error" if failed and not made else "ok",
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("[multiplier] log_outcome mislukt: %s", e)

    return result


async def multiply_job_safe(job_id: str) -> None:
    """Achtergrond-wrapper: vangt álles af zodat een multiplier-fout de
    approve-flow (die al geslaagd is) nooit alsnog een exception geeft."""
    try:
        await multiply_job(job_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[multiplier] Achtergrond-run mislukt voor %s: %s",
                       job_id, str(e)[:300])
        try:
            from ...shared.outcomes import log_outcome
            log_outcome(
                "Multiplier", "content_multiplier",
                f"Format-waaier mislukt voor job {job_id}: {str(e)[:200]}",
                next_step="Trigger handmatig opnieuw via POST /api/content-queue/"
                          f"{job_id}/multiply.",
                status="error",
            )
        except Exception:
            pass

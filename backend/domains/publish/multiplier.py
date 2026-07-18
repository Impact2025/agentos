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
    failed = []
    if (result["social_pack"] or {}).get("error"):
        failed.append("social-pack")
    if (result["video"] or {}).get("error"):
        failed.append("video")
    try:
        from ...shared.outcomes import log_outcome
        if made or failed:
            log_outcome(
                project or "Multiplier", "content_multiplier",
                f"Format-waaier voor '{title}': "
                + (f"gemaakt: {', '.join(made)}" if made else "niets nieuws")
                + (f"; mislukt: {', '.join(failed)}" if failed else ""),
                artifact="; ".join(a for a in artifacts if a),
                next_step=("Bekijk en keur de posts/video in Social Creatie."
                           if made else "Bekijk de fout en probeer /multiply opnieuw."),
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

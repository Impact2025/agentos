"""
Content-wachtrij API — 2x/week auto-gegenereerde blog + social-copy, wacht op
menselijke goedkeuring voordat er iets live/gepost wordt.

  GET  /api/content-queue                  → lijst (filter op site_id/status)
  GET  /api/content-queue/{id}             → één job (incl. blog_html + social_copy)
  POST /api/content-queue/{id}/approve     → publiceer + post naar alle geconfigureerde platformen
  POST /api/content-queue/{id}/reject      → afwijzen, geen actie
  POST /api/content-queue/{id}/regenerate  → herschrijf hetzelfde onderwerp opnieuw
  POST /api/content-queue/{id}/save-manual-edit → sla handmatig (Claude/Gemini) bewerkte body op + herscore
  POST /api/content-queue/run-now          → (handmatig) draai de auto-content-generatie nu voor 1 site
"""
import json
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from ..publish import content_pipeline
from ..seo import sites as sites_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/content-queue", tags=["content-queue"])


def _with_parsed_social_copy(job: dict) -> dict:
    job = dict(job)
    try:
        job["social_copy"] = json.loads(job.get("social_copy") or "{}")
    except Exception:
        job["social_copy"] = {}
    try:
        job["publish_result"] = json.loads(job.get("publish_result") or "{}")
    except Exception:
        job["publish_result"] = {}
    try:
        job["qc_report"] = json.loads(job.get("qc_report") or "{}")
    except Exception:
        job["qc_report"] = {}
    # SEO-wereldklasse-badge: deterministische AEO/E-E-A-T-analyse van het
    # artikel (geen LLM). Toont in de Wachtrij-UI hoe dicht het stuk bij
    # 'publish-klaar volgens wereldklasse' zit, vóórdat je op publiceer klikt.
    try:
        from ..seo.enhancements import assess_seo_worldclass
        html = job.get("blog_html") or ""
        kw = job.get("keyword") or ""
        site = {}
        if job.get("site_id"):
            try:
                site = sites_service.get_site(job["site_id"]) or {}
            except Exception:
                site = {}
        job["seo_worldclass"] = assess_seo_worldclass(html, kw, site)
    except Exception:
        job["seo_worldclass"] = None
    # Publiceerbaarheids-vlag voor de UI: als dit een interne werkbon/opdracht is,
    # verberg dan de groene "Publiceer"-knop en toon een waarschuwing. Dit is de
    #zelfde check als in approve_and_publish(), hier al vóóraf uitgerekend zodat de
    # frontend niet eerst hoeft te falen bij de klik.
    try:
        from .publish.content_pipeline import is_internal_document
        blocked = is_internal_document(job.get("title") or "", job.get("blog_html") or "")
        if blocked:
            job["publish_blocked"] = True
            job["publish_block_reason"] = blocked
        else:
            job["publish_blocked"] = False
            job["publish_block_reason"] = None
    except Exception:
        job["publish_blocked"] = False
        job["publish_block_reason"] = None
    return job


@router.get("")
def list_content_jobs(site_id: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    jobs = content_pipeline.list_jobs(site_id=site_id, status=status)
    return [_with_parsed_social_copy(j) for j in jobs]


@router.get("/{job_id}")
def get_content_job(job_id: str):
    job = content_pipeline.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Content-job niet gevonden")
    return _with_parsed_social_copy(job)


def _channels_from_body(body: Optional[Dict]) -> list:
    """Per-artikel social-keuze uit de request-body — opt-in: geen body, geen
    sleutel of `social: false` = lege lijst (alleen de website). Social gaat dus
    nooit vanzelf mee; alleen een expliciete `channels`-lijst post."""
    if not body or body.get("social") is False:
        return []
    if "channels" in body:
        return [str(c).strip().lower() for c in (body.get("channels") or [])]
    return []


@router.post("/{job_id}/approve")
async def approve_content_job(job_id: str, body: Optional[Dict] = Body(None)):
    try:
        result = await content_pipeline.approve_and_publish(
            job_id,
            social_channels=_channels_from_body(body),
            publish_date=(body or {}).get("publish_date") or None,
        )
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Goedkeuren/publiceren mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/{job_id}/ready-linkedin")
def mark_ready_for_linkedin(job_id: str):
    """Markeer een LinkedIn-outreach job als 'klaar voor LinkedIn' (geen site-publish).

    Dit is de expliciete menselijke bevestiging dat de berichten op LinkedIn mogen —
    door de gebruiker, niet automatisch. Blokkeert elke Netlify-deploy.
    """
    try:
        content_pipeline.mark_ready_for_linkedin(job_id)
        return {"success": True, "status": "ready_for_linkedin"}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))



@router.post("/{job_id}/reject")
def reject_content_job(job_id: str):
    try:
        content_pipeline.reject_job(job_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/{job_id}/confirm-depublished")
async def confirm_content_job_depublished(job_id: str):
    """Haal een 'afgekeurd maar live'-artikel écht offline en sluit de kaart."""
    try:
        result = await content_pipeline.confirm_depublished(job_id)
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/{job_id}/regenerate")
async def regenerate_content_job(job_id: str):
    try:
        new_id = await content_pipeline.regenerate_job(job_id)
        return _with_parsed_social_copy(content_pipeline.get_job(new_id))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Regenereren mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/{job_id}/save-manual-edit")
async def save_manual_edit_job(job_id: str, body: Dict = Body(...)):
    """Sla een handmatig (in Claude/Gemini of inline) bewerkte body terug op en
    laat 'm opnieuw scoren. Bij >= grens wordt de job 'pending_review'.
    body.force=True slaat de score over en zet de job direct op 'pending_review'
    (handmatig vrijgegeven door de mens, bv. als de LLM-quota in backoff zit)."""
    try:
        result = await content_pipeline.save_manual_edit(
            job_id, body.get("html_body", ""), force=bool(body.get("force", False)))
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Handmatig-edit opslaan mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/run-now")
async def run_now(site_id: str = Query(...), count: Optional[int] = Query(None, ge=1, le=10)):
    """Handmatig een content-batch draaien voor 1 site (buiten het 2x/week-schema
    om). `count` overschrijft de site-instelling content_batch_size."""
    site = sites_service.get_site(site_id)
    if not site:
        raise HTTPException(404, detail="Site niet gevonden")
    try:
        job_ids = await content_pipeline.run_content_batch(site, count=count)
        if not job_ids:
            return {"success": False, "detail": "Geen nieuwe kansen — voer eerst een Demand Engine-scan uit."}
        return {"success": True, "job_ids": job_ids, "job_id": job_ids[0]}
    except Exception as e:
        logger.exception("run-now mislukt voor site %s", site_id)
        raise HTTPException(500, detail=str(e)[:300])


def _project_for_job(job: dict) -> str:
    """Project-naam achterhalen: site.name == project-naam (bv. 'bewaardvoorjou')."""
    site = sites_service.get_site(job.get("site_id")) or {}
    return (site.get("name") or job.get("site_id") or "").lower()


@router.post("/{job_id}/upgrade")
async def upgrade_content_job(job_id: str, target: int = Query(85, ge=80, le=100)):
    """Til één artikel naar de wereldklasse-lat (default 85) — met bevestigde
    hermeting, want één meting boven de lat is bij deze reviewer geen bewijs.
    Publiceert niets; het artikel blijft achter de Wachtrij-gate."""
    from ..publish import upgrade
    try:
        return {"success": True, "result": await upgrade.upgrade_job(job_id, target=target)}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Opschoonronde mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/upgrade-all")
async def upgrade_all_content_jobs(target: int = Query(85, ge=80, le=100),
                                   site_id: Optional[str] = Query(None),
                                   limit: Optional[int] = Query(None, ge=1, le=200)):
    """Opschoonronde over alle artikelen die de lat nog niet halen.

    Kan lang duren (meerdere LLM-rondes per artikel) — hervatbaar: wat al
    bevestigd boven de lat staat valt bij een volgende run buiten de selectie.
    """
    from ..publish import upgrade
    try:
        return {"success": True, **await upgrade.upgrade_batch(
            target=target, site_id=site_id, limit=limit)}
    except Exception as e:
        logger.exception("Opschoonronde (batch) mislukt")
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/{job_id}/multiply")
async def multiply_content_job(job_id: str):
    """Draai de Content Multiplier voor een gepubliceerd artikel: social-pack
    (alle platforms) + 9:16-video, alles achter de review-gates. Synchroon —
    de video-render kan even duren, maar je krijgt het volledige verslag terug."""
    from ..publish import multiplier
    try:
        result = await multiplier.multiply_job(job_id)
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Multiplier mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])


@router.post("/{job_id}/make-video")
def make_job_video(job_id: str):
    """Maak een korte verticale video vánuit dit blogartikel.

    De agent schrijft een eigen spreekbaar script op basis van het blog, Pexels
    levert het beeld, en de video wordt opgeslagen + teruggekoppeld als
    SocialPack (verschijnt in Social Creatie).
    """
    job = content_pipeline.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Content-job niet gevonden")
    project = _project_for_job(job)
    if not project:
        raise HTTPException(400, detail="Kan geen project koppelen aan deze job")
    from ...shared import blog_video
    try:
        result = blog_video.make_blog_video(
            job_id, project, job.get("title", ""), job.get("blog_html") or ""
        )
    except Exception as e:
        logger.exception("Blog-video mislukt voor job %s", job_id)
        raise HTTPException(500, detail=str(e)[:300])
    if not result.get("success"):
        raise HTTPException(500, detail=result.get("error", "onbekende fout"))
    return result


@router.get("/{job_id}/video")
def get_job_video(job_id: str):
    """Stream de gegenereerde video voor dit blog (404 als er nog geen is)."""
    from ...shared import blog_video as bv
    from fastapi.responses import FileResponse
    job = content_pipeline.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Content-job niet gevonden")
    rel = job.get("video_path") or ""
    if not rel:
        raise HTTPException(404, detail="Nog geen video voor dit blog")
    path = bv._REPO / rel
    if not path.exists():
        raise HTTPException(404, detail="Videobestand niet gevonden")
    return FileResponse(str(path), media_type="video/mp4",
                        filename=f"blog_{job_id}.mp4")

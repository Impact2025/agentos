"""Social Content Creatie — API router.

  POST /api/social-content/generate   → maak een content-pack (posts + beeld/TikTok)
  GET  /api/social-content/packs      → lijst packs (filter project/status)
  GET  /api/social-content/packs/{id} → één pack
  POST /api/social-content/packs/{id}/approve → keur goed
  POST /api/social-content/packs/{id}/reject  → wijs af
  GET  /api/social-content/packs/{id}/export   → plak-klare bundel (Canva/MJ/TT/posting)
  POST /api/social-content/packs/{id}/publish  → plaats op één platform (na goedkeuring)
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ...shared import social_content as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social-content", tags=["social-content"])


@router.post("/generate")
def generate(body: dict):
    project = (body.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is verplicht")
    theme = (body.get("theme") or "").strip()
    if not theme:
        raise HTTPException(400, "theme is verplicht")
    platforms = body.get("platforms") or None
    if isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",") if p.strip()]
    pack = svc.generate_content_pack(
        project=project,
        theme=theme,
        angle=(body.get("angle") or "").strip(),
        platforms=platforms,
        with_image=bool(body.get("with_image", True)),
        with_video=bool(body.get("with_video", True)),
        brand_context=(body.get("brand_context") or "").strip(),
    )
    return {"success": True, "pack": svc.export_pack(pack.id)}


@router.get("/packs")
def list_packs(project: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    packs = svc.list_packs(project=project or None, status=status or None)
    return [svc.export_pack(p.id) for p in packs]


@router.get("/packs/{pack_id}")
def get_pack(pack_id: str):
    p = svc.get_pack(pack_id)
    if not p:
        raise HTTPException(404, "Pack niet gevonden")
    return svc.export_pack(pack_id)


@router.post("/packs/{pack_id}/approve")
def approve(pack_id: str):
    if not svc.approve_pack(pack_id):
        raise HTTPException(404, "Pack niet gevonden")
    return {"success": True}


@router.post("/packs/{pack_id}/reject")
def reject(pack_id: str):
    if not svc.reject_pack(pack_id):
        raise HTTPException(404, "Pack niet gevonden")
    return {"success": True}


@router.get("/packs/{pack_id}/export")
def export(pack_id: str):
    out = svc.export_pack(pack_id)
    if not out:
        raise HTTPException(404, "Pack niet gevonden")
    return out


@router.post("/packs/{pack_id}/render-video")
def render_video(pack_id: str):
    """Render een 9:16 short uit het scriptpack. Synchroon (def) zodat FastAPI
    dit in de threadpool draait — edge-tts gebruikt intern asyncio.run, wat in
    een aparte thread wél mag maar in een async endpoint zou botsen."""
    result = svc.render_pack_video(pack_id)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Renderen mislukt"))
    return result


@router.get("/packs/{pack_id}/video")
def get_video(pack_id: str):
    """Stream de gerenderde video (ondersteunt range-requests voor <video>)."""
    path = svc.video_file_path(pack_id)
    if not path:
        raise HTTPException(404, "Geen video voor dit pack")
    return FileResponse(str(path), media_type="video/mp4", filename=f"{pack_id}.mp4")


@router.post("/packs/{pack_id}/publish")
async def publish(pack_id: str, body: dict):
    platform = (body.get("platform") or "").strip().lower()
    try:
        result = await svc.publish_pack(pack_id, platform)
    except Exception as e:
        raise HTTPException(400, str(e)[:300])
    if result.get("success") or result.get("manual"):
        return {"success": True, **result}
    raise HTTPException(400, result.get("error", "Onbekende fout"))

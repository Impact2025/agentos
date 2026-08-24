"""Social Content Creatie — API router.

  POST /api/social-content/generate   → maak een content-pack (posts + beeld/TikTok)
  GET  /api/social-content/packs      → lijst packs (filter project/status)
  GET  /api/social-content/packs/{id} → één pack
  POST /api/social-content/packs/{id}/approve → keur goed
  POST /api/social-content/packs/{id}/reject  → wijs af
  GET  /api/social-content/packs/{id}/export   → plak-klare bundel (Canva/MJ/TT/posting)
  POST /api/social-content/packs/{id}/publish  → plaats op één platform (na goedkeuring)
  POST /api/social-content/campaign/import     → zet een uitgeschreven socialplan om in packs
  GET  /api/social-content/campaign            → de campagne-agenda (wat staat wanneer klaar)
  GET  /api/social-content/style               → het huisstijl-profiel van een project
  GET  /api/social-content/photos              → fotobibliotheek van een project (projects/<p>/photos/)
  POST /api/social-content/packs/{id}/photos/{filename} → koppel een bibliotheekfoto aan een pack
"""
import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ...shared import social_campaign as campaign_svc
from ...shared import social_content as svc
from ...shared import social_style

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


@router.post("/packs/{pack_id}/image")
async def upload_image(pack_id: str, file: UploadFile):
    """Vervang het beeld van een pack door een eigen render (Midjourney/foto).

    De headline/subtext uit de bestaande beeld-brief blijven leidend voor de
    overlay — de brief is de instructie ('gouden serif-titel + wit onderschrift
    op een donker vlak'), dit endpoint levert alleen het beeld waar die
    instructie op wordt toegepast. Alle 18 prompts uit het huisstijl-profiel
    zijn al klaar via GET /api/social-content/style; dit is de andere helft:
    de render terugbrengen in het pack.
    """
    p = svc.get_pack(pack_id)
    if not p:
        raise HTTPException(404, "Pack niet gevonden")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Leeg bestand")
    from ...shared import social_image as img_svc
    brief = p.image_brief or {}
    res = img_svc.brand_uploaded_image(
        raw, p.project,
        headline=brief.get("headline", "") or p.theme,
        subtext=brief.get("subtext", ""),
    )
    if not res.get("success"):
        raise HTTPException(400, res.get("error", "Afbeelding verwerken mislukt"))
    svc.set_pack_image(pack_id, image_url=res["url"], image_path=res["path"],
                       image_source=res["source"], image_raw_path=res.get("raw_path", ""))
    return {"success": True, **res}


@router.get("/photos")
def list_photos(project: str = Query(...)):
    """Foto's die Vincent zelf in projects/<project>/photos/ heeft gezet
    (bijv. zelf gegenereerde Midjourney-renders). Zelfde map als de
    video-B-roll-bibliotheek, hier gebruikt voor losse post-beelden."""
    return {"photos": svc.list_project_photos(project)}


@router.get("/photos/{project}/{filename}")
def get_photo(project: str, filename: str):
    path = svc.project_photo_path(project, filename)
    if not path:
        raise HTTPException(404, "Foto niet gevonden")
    return FileResponse(path)


@router.post("/packs/{pack_id}/photos/{filename}")
def assign_photo(pack_id: str, filename: str, project: str = Query(...)):
    """Koppel een bestaande foto uit de projectbibliotheek aan dit pack,
    i.p.v. een nieuwe upload — zelfde huisstijl-crop+overlay als bij upload."""
    res = svc.assign_library_photo(pack_id, project, filename)
    if not res.get("success"):
        raise HTTPException(400, res.get("error", "Koppelen mislukt"))
    return res


@router.post("/campaign/import")
def import_campaign(body: dict):
    """Zet een uitgeschreven socialplan om in review-klare packs.

    `start` (ISO-datum) is optioneel; standaard de eerstvolgende maandag. Er
    wordt niets gepost — elk pack landt op 'pending_review', net als alle andere
    content die dit systeem maakt.
    """
    project = (body.get("project") or "").strip()
    if not project:
        raise HTTPException(400, "project is verplicht")
    start = None
    if body.get("start"):
        try:
            start = date.fromisoformat(str(body["start"]))
        except ValueError:
            raise HTTPException(400, "start moet een ISO-datum zijn (JJJJ-MM-DD)")
    result = campaign_svc.importeer_campagne(project, start=start)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Import mislukt"))
    return result


@router.post("/packs/{pack_id}/posted")
def mark_posted(pack_id: str, body: dict = None):
    """Leg vast dat je dit pack zélf op de kanalen hebt gezet.

    Voor alles wat Impact OS niet kan plaatsen (LinkedIn vanaf een persoonlijk
    profiel, een project zonder eigen social-token). Zonder deze knop blijft het
    pack 'pending_review' en meldt de waarheidsaudit een gemiste post die
    gewoon live staat.
    """
    platforms = (body or {}).get("platforms")
    if isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",") if p.strip()]
    res = svc.mark_posted_manually(pack_id, platforms)
    if not res.get("success"):
        raise HTTPException(404, res.get("error", "Pack niet gevonden"))
    return res


@router.get("/campaign")
def campaign_agenda(project: str = Query(...), campagne: str = Query("")):
    """De campagne op volgorde van plaatsdatum, met status en 'over datum'-vlag."""
    return campaign_svc.agenda(project, campagne)


@router.get("/style")
def get_style(project: str = Query(...)):
    """Het huisstijl-profiel zoals de generator het leest.

    Handig om te zien wélke stem/hashtags/beeldstijl een project heeft — en of
    er überhaupt een profiel gevonden is (`bron`) in plaats van de generieke
    terugval.
    """
    s = social_style.load_style(project, refresh=True)
    return {
        "project": project,
        "bron": s.bron,
        "stem": s.voice,
        "toon": s.tone,
        "hashtag_sets": s.hashtag_sets,
        "platform_set": s.platform_set,
        "post_types": s.type_set,
        "beeld": {"stijlblok": s.stijlblok, "aspect": s.aspect,
                  "overlay": s.overlay.__dict__},
        "ritme": [r.__dict__ for r in s.ritme],
        "utm": s.utm,
        "site_url": s.site_url,
    }


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

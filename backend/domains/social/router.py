"""
Social API router — post content naar Facebook, Instagram en X vanuit ImpactOS.
Zelfde vorm als `backend/domains/linkedin/router.py` (die apart blijft staan).

  GET  /api/social/{platform}/status          → is dit platform geconfigureerd?
  POST /api/social/{platform}/test            → verbinding testen
  POST /api/social/facebook/post               → tekst-update posten
  POST /api/social/instagram/post              → afbeelding + caption posten
  POST /api/social/twitter/post                → tweet posten
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ...shared import facebook as facebook_service
from ...shared import instagram as instagram_service
from ...shared import twitter as twitter_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social", tags=["social"])


# ── Facebook ─────────────────────────────────────────────────────────────

@router.get("/facebook/status")
def facebook_status(site_name: Optional[str] = Query(None)):
    return {"configured": facebook_service.is_configured(site_name), "site_name": site_name}


@router.post("/facebook/test")
async def facebook_test(body: dict = {"site_name": None}):
    site_name = body.get("site_name") if isinstance(body, dict) else None
    try:
        info = await facebook_service.get_page_info(site_name)
        return {"success": True, "page": info, "site_name": site_name}
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])


@router.post("/facebook/post")
async def facebook_post(body: dict):
    text = body.get("text", "")
    if not text:
        raise HTTPException(400, detail="Geen tekst opgegeven")
    result = await facebook_service.post_update(text, body.get("article_url"), body.get("site_name"))
    if result.get("success"):
        return result
    raise HTTPException(400, detail=result.get("error", "Onbekende fout"))


# ── Instagram ────────────────────────────────────────────────────────────

@router.get("/instagram/status")
def instagram_status(site_name: Optional[str] = Query(None)):
    return {"configured": instagram_service.is_configured(site_name), "site_name": site_name}


@router.post("/instagram/test")
async def instagram_test(body: dict = {"site_name": None}):
    site_name = body.get("site_name") if isinstance(body, dict) else None
    try:
        info = await instagram_service.get_account_info(site_name)
        return {"success": True, "account": info, "site_name": site_name}
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])


@router.post("/instagram/post")
async def instagram_post(body: dict):
    image_url = body.get("image_url", "")
    caption = body.get("caption", "")
    if not image_url or not caption:
        raise HTTPException(400, detail="image_url en caption zijn verplicht")
    result = await instagram_service.post_image(image_url, caption, body.get("site_name"))
    if result.get("success"):
        return result
    raise HTTPException(400, detail=result.get("error", "Onbekende fout"))


# ── X / Twitter ──────────────────────────────────────────────────────────

@router.get("/twitter/status")
def twitter_status(site_name: Optional[str] = Query(None)):
    return {"configured": twitter_service.is_configured(site_name), "site_name": site_name}


@router.post("/twitter/test")
async def twitter_test(body: dict = {"site_name": None}):
    site_name = body.get("site_name") if isinstance(body, dict) else None
    try:
        info = await twitter_service.get_account_info(site_name)
        return {"success": True, "account": info, "site_name": site_name}
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])


@router.post("/twitter/post")
async def twitter_post(body: dict):
    text = body.get("text", "")
    if not text:
        raise HTTPException(400, detail="Geen tekst opgegeven")
    result = await twitter_service.post_update(text, body.get("article_url"), body.get("site_name"))
    if result.get("success"):
        return result
    raise HTTPException(400, detail=result.get("error", "Onbekende fout"))

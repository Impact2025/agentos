"""
LinkedIn API router — post content naar LinkedIn vanuit AgentOS.
Ondersteunt per-project tokens via `site_name` parameter.
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ...shared import linkedin as linkedin_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/status")
def linkedin_status(site_name: Optional[str] = Query(None)):
    """Check of LinkedIn is geconfigureerd (optioneel per site)."""
    return {
        "configured": linkedin_service.is_configured(site_name),
        "site_name": site_name,
    }


@router.post("/test")
async def linkedin_test(body: dict = {"site_name": None}):
    """Test de LinkedIn verbinding voor een specifieke site."""
    site_name = body.get("site_name") if isinstance(body, dict) else None
    try:
        member_id = await linkedin_service.get_member_id(site_name)
        token, _ = linkedin_service._get_site_data(site_name)
        return {
            "success": True,
            "member_id": member_id,
            "author_urn": f"urn:li:member:{member_id}",
            "site_name": site_name,
            "token_prefix": token[:8] + "..." if token else "geen",
        }
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])


@router.post("/post")
async def linkedin_post(body: dict):
    """Post een tekst-update naar LinkedIn (optioneel per site)."""
    text = body.get("text", "")
    if not text:
        raise HTTPException(400, detail="Geen tekst opgegeven")
    article_url = body.get("article_url", None)
    site_name = body.get("site_name", None)

    try:
        result = await linkedin_service.post_update(text, article_url, site_name)
        if result.get("success"):
            return result
        raise HTTPException(400, detail=result.get("error", "Onbekende fout"))
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])

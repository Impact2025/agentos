"""
Instagram service — post een afbeelding + caption naar een Instagram
Business/Creator-account via de Graph API (Content Publishing).

Instagram posten kan alleen via een Business/Creator-account dat gekoppeld is
aan een Facebook-pagina, en gebruikt hetzelfde Page Access Token als Facebook
(zie facebook.py). Vereist per site: `instagram_business_id` (het IG-user-ID,
te vinden via Graph API Explorer: GET /{page-id}?fields=instagram_business_account).

Twee stappen (Meta Content Publishing API):
  1. POST /{ig-user-id}/media       (image_url + caption) → creation_id
  2. POST /{ig-user-id}/media_publish (creation_id)        → media_id

Instagram accepteert geen tekst-only posts — er is altijd een publiek
bereikbare image_url nodig (zie shared/image_gen.py + de Netlify-deploy die de
gegenereerde afbeelding meepubliceert).
"""

import asyncio
import httpx
import logging
from typing import Optional, Dict, Any

from .config import FACEBOOK_PAGE_TOKEN, INSTAGRAM_BUSINESS_ID

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v19.0"


def _get_site_data(site_name: Optional[str] = None) -> tuple:
    """Haal (ig_user_id, token) op voor een site, of globaal als fallback.

    Instagram gebruikt hetzelfde page-token als Facebook voor die site.
    """
    ig_id = INSTAGRAM_BUSINESS_ID
    token = FACEBOOK_PAGE_TOKEN

    if site_name:
        try:
            from ..domains.seo import sites as sites_service
            for s in sites_service.list_sites():
                norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
                if norm(s.get("name", "")) == norm(site_name):
                    site_full = sites_service.get_site(s["id"])
                    if site_full:
                        sid = (site_full.get("instagram_business_id") or "").strip()
                        st = (site_full.get("facebook_page_token") or "").strip()
                        if sid:
                            ig_id = sid
                        if st:
                            token = st
                    break
        except Exception:
            pass

    return ig_id, token


def is_configured(site_name: Optional[str] = None) -> bool:
    ig_id, token = _get_site_data(site_name)
    return bool(ig_id) and bool(token)


async def get_account_info(site_name: Optional[str] = None) -> Dict[str, Any]:
    """Haal accountgegevens op — gebruikt om de verbinding te testen."""
    ig_id, token = _get_site_data(site_name)
    if not ig_id or not token:
        raise ValueError("Geen Instagram business-id/token geconfigureerd.")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_API}/{ig_id}", params={"fields": "username", "access_token": token}, timeout=15
        )
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def post_image(image_url: str, caption: str,
                     site_name: Optional[str] = None) -> Dict[str, Any]:
    """Publiceer een afbeelding met caption naar Instagram voor een specifieke site.

    `image_url` moet publiek bereikbaar zijn (Instagram haalt 'm zelf op).
    """
    ig_id, token = _get_site_data(site_name)
    if not ig_id or not token:
        return {"success": False, "error": f"Geen Instagram business-id/token voor {site_name or 'globale config'}"}

    async with httpx.AsyncClient(timeout=60) as client:
        create_resp = await client.post(
            f"{GRAPH_API}/{ig_id}/media",
            data={"image_url": image_url, "caption": caption[:2200], "access_token": token},
        )
        if create_resp.status_code != 200:
            error_body = create_resp.text[:500]
            logger.error(f"❌ Instagram media create failed ({site_name}): {create_resp.status_code}")
            return {"success": False, "error": f"HTTP {create_resp.status_code}: {error_body}"}

        creation_id = create_resp.json().get("id", "")
        if not creation_id:
            return {"success": False, "error": "Geen creation_id ontvangen van Instagram"}

        # Meta's container kan even nodig hebben om de afbeelding te verwerken.
        await asyncio.sleep(2)

        publish_resp = await client.post(
            f"{GRAPH_API}/{ig_id}/media_publish",
            data={"creation_id": creation_id, "access_token": token},
        )

    if publish_resp.status_code == 200:
        media_id = publish_resp.json().get("id", "")
        logger.info(f"✅ Instagram post OK — site={site_name}, media_id={media_id}")
        return {"success": True, "post_id": media_id, "site": site_name}
    else:
        error_body = publish_resp.text[:500]
        logger.error(f"❌ Instagram publish failed ({site_name}): {publish_resp.status_code}")
        return {"success": False, "error": f"HTTP {publish_resp.status_code}: {error_body}"}

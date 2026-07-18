"""
Facebook service — post content naar een Facebook-pagina via de Graph API.

Ondersteunt per-site page-id/token opgeslagen in de `sites` tabel.
Valt terug op FACEBOOK_PAGE_ID/FACEBOOK_PAGE_TOKEN in .env als globale fallback.

Token aanmaken: developers.facebook.com > je app > Graph API Explorer
Nodig: een Page Access Token (geen user token) met scope `pages_manage_posts`
+ `pages_read_engagement`. Voor langdurig gebruik: wissel om naar een
long-lived page token (verloopt niet, tenzij de app-review verloopt).
"""

import httpx
import logging
from typing import Optional, Dict, Any

from .config import FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v19.0"


def _get_site_data(site_name: Optional[str] = None) -> tuple:
    """Haal (page_id, token) op voor een site, of globaal als fallback."""
    page_id = FACEBOOK_PAGE_ID
    token = FACEBOOK_PAGE_TOKEN

    if site_name:
        try:
            from ..domains.seo import sites as sites_service
            for s in sites_service.list_sites():
                norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
                if norm(s.get("name", "")) == norm(site_name):
                    site_full = sites_service.get_site(s["id"])
                    if site_full:
                        sp = (site_full.get("facebook_page_id") or "").strip()
                        st = (site_full.get("facebook_page_token") or "").strip()
                        if sp:
                            page_id = sp
                        if st:
                            token = st
                    break
        except Exception:
            pass

    return page_id, token


def is_configured(site_name: Optional[str] = None) -> bool:
    page_id, token = _get_site_data(site_name)
    return bool(page_id) and bool(token)


async def post_update(text: str, article_url: Optional[str] = None,
                      site_name: Optional[str] = None) -> Dict[str, Any]:
    """Post een update naar de Facebook-pagina van een specifieke site."""
    page_id, token = _get_site_data(site_name)
    if not page_id or not token:
        return {"success": False, "error": f"Geen Facebook page-id/token voor {site_name or 'globale config'}"}

    payload: Dict[str, Any] = {"message": text[:63000], "access_token": token}
    if article_url:
        payload["link"] = article_url

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{GRAPH_API}/{page_id}/feed", data=payload, timeout=30)

    if resp.status_code == 200:
        result = resp.json()
        post_id = result.get("id", "")
        logger.info(f"✅ Facebook post OK — site={site_name}, post_id={post_id}")
        return {
            "success": True,
            "post_id": post_id,
            "url": f"https://www.facebook.com/{post_id}",
            "site": site_name,
        }
    else:
        # Lees de FB-fout netjes uit zodat we een actiegerichte melding kunnen
        # geven. Een verlopen/on­geldige access-token (OAuthException 190) is de
        # meest voorkomende oorzaak van een 400 — zeg dát expliciet, zodat er
        # niet naar de payload gezocht wordt maar het token vernieuwd wordt.
        try:
            err_json = resp.json()
            fb_msg = err_json.get("error", {}).get("message", resp.text[:300])
            fb_code = err_json.get("error", {}).get("code")
        except Exception:
            fb_msg, fb_code = resp.text[:300], None
        if fb_code == 190 or "session has expired" in fb_msg.lower() \
                or "error validating access token" in fb_msg.lower():
            logger.error(
                "❌ Facebook post mislukt (%s): access token verlopen/on­geldig "
                "(FB code %s). Verleng via developers.facebook.com → Graph API "
                "Explorer (Page Access Token, scope pages_manage_posts + "
                "pages_read_engagement), zet 'm in .env (FACEBOOK_PAGE_TOKEN) en "
                "herstart Agent OS.",
                site_name, fb_code,
            )
        else:
            logger.error(
                "❌ Facebook post mislukt (%s): HTTP %s — %s",
                site_name, resp.status_code, fb_msg[:300],
            )
        return {
            "success": False,
            "error": f"HTTP {resp.status_code}: {fb_msg[:500]}",
        }
    """Haal paginanaam op — gebruikt om de verbinding te testen."""
    page_id, token = _get_site_data(site_name)
    if not page_id or not token:
        raise ValueError("Geen Facebook page-id/token geconfigureerd.")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_API}/{page_id}", params={"fields": "name", "access_token": token}, timeout=15
        )
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def share_article(url: str, title: str, description: str = "",
                        site_name: Optional[str] = None) -> Dict[str, Any]:
    """Deel een artikel op de Facebook-pagina voor een specifieke site."""
    text = title
    if description:
        text += f"\n\n{description}"
    return await post_update(text, article_url=url, site_name=site_name)

"""
LinkedIn service — post content naar LinkedIn via de User Generated Content API.

Ondersteunt per-site tokens opgeslagen in de `sites` tabel.
Valt terug op LINKEDIN_ACCESS_TOKEN in .env als globale fallback.

Token aanmaken: https://www.linkedin.com/developers/tools/oauth/token-generator
Scopes nodig: w_member_social (voor posten), openid profile email (voor URN detectie)
"""

import httpx
import logging
from typing import Optional, Dict, Any

from ..shared.config import LINKEDIN_ACCESS_TOKEN, LINKEDIN_USER_URN

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com/v2"

# Cached numeric member IDs per token (keyed by token[:16])
_member_id_cache: Dict[str, str] = {}


# ── Site-specific token lookup ─────────────────────────────────────

def _get_site_data(site_name: Optional[str] = None) -> tuple:
    """Haal (token, user_urn) op voor een site, of globaal als fallback."""
    token = LINKEDIN_ACCESS_TOKEN
    user_urn = LINKEDIN_USER_URN

    if site_name:
        try:
            from ..domains.seo import sites as sites_service
            for s in sites_service.list_sites():
                norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
                if norm(s.get("name", "")) == norm(site_name):
                    site_full = sites_service.get_site(s["id"])
                    if site_full:
                        st = (site_full.get("linkedin_token") or "").strip()
                        su = (site_full.get("linkedin_user_urn") or "").strip()
                        if st:
                            token = st
                        if su:
                            user_urn = su
                    break
        except Exception:
            pass

    return token, user_urn


def _make_headers(token: str) -> Dict[str, str]:
    if not token:
        raise ValueError("Geen LinkedIn access token.")
    return {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def is_configured(site_name: Optional[str] = None) -> bool:
    """Check of er een LinkedIn token is voor deze site (of globaal)."""
    token, _ = _get_site_data(site_name)
    return bool(token) and token.strip() != ""


async def get_member_id(site_name: Optional[str] = None) -> str:
    """Haal het numerieke LinkedIn member ID op (cached per token).
    
    De UGC API verwacht 'urn:li:member:{numeric_id}' als author.
    Het member ID is een numerieke string, NIET de vanity name uit de profiel-URL.
    
    Resolutievolgorde:
    1. Opgeslagen 'linkedin_user_urn' in sites DB of .env
    2. /userinfo endpoint (openid scope → sub field)
    3. /me endpoint (r_liteprofile scope → id field)
    """
    token, stored_urn = _get_site_data(site_name)

    if not token:
        raise ValueError("Geen LinkedIn access token.")

    cache_key = token[:16]
    if cache_key in _member_id_cache:
        return _member_id_cache[cache_key]

    # 1. Opgeslagen URN — accepteer urn:li:member:{id} of numeriek ID
    if stored_urn:
        if stored_urn.startswith("urn:li:member:"):
            mid = stored_urn.replace("urn:li:member:", "")
            _member_id_cache[cache_key] = mid
            return mid
        if stored_urn.isdigit():
            _member_id_cache[cache_key] = stored_urn
            return stored_urn
        if stored_urn.startswith("urn:li:person:"):
            # Vanity name — niet bruikbaar voor UGC API, probeer API
            logger.info(f"Opgeslagen URN is vanity name, probeer API-resolutie: {stored_urn}")

    # 2. Auto-detect via LinkedIn API
    headers = _make_headers(token)
    endpoints = [
        (f"{API_BASE}/userinfo", "sub"),       # OpenID → numeric sub
        (f"{API_BASE}/me", "id"),               # v2 API → numeric id
    ]
    for url, field in endpoints:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                mid = data.get(field, "")
                if mid:
                    _member_id_cache[cache_key] = mid
                    logger.info(f"LinkedIn member ID resolved: {mid} (via {url[:50]})")
                    return mid
        except Exception as e:
            logger.debug(f"LinkedIn {url} failed: {e}")

    raise ValueError(
        "Kan LinkedIn member ID niet ophalen via API. "
        "Zorg dat het token scopes heeft: openid, profile, email "
        "of geef het numerieke member ID op.\n\n"
        "Vind je member ID:\n"
        "1. Ga naar linkedin.com/in/jouw-profiel\n"
        "2. View page source (Ctrl+U)\n"
        "3. Zoek naar 'memberId' of een 10+ cijferig nummer\n"
        "4. Zet LINKEDIN_USER_URN=dat_nummer in .env"
    )


async def post_update(text: str, article_url: Optional[str] = None,
                      site_name: Optional[str] = None) -> Dict[str, Any]:
    """Post een LinkedIn update voor een specifieke site."""
    token, _ = _get_site_data(site_name)
    if not token:
        return {"success": False, "error": f"Geen LinkedIn token voor {site_name or 'globale config'}"}

    member_id = await get_member_id(site_name)
    author = f"urn:li:member:{member_id}"
    commentary = text[:3000]
    headers = _make_headers(token)

    content = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": commentary},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }

    if article_url:
        content["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "ARTICLE"
        content["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
            {
                "status": "READY",
                "description": {"text": commentary[:256]},
                "originalUrl": article_url,
            }
        ]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_BASE}/ugcPosts",
            headers=headers,
            json=content,
            timeout=30,
        )

    if resp.status_code == 201:
        result = resp.json()
        post_id = result.get("id", "")
        logger.info(f"✅ LinkedIn post OK — site={site_name}, post_id={post_id}")
        return {
            "success": True,
            "post_id": post_id,
            "url": f"https://www.linkedin.com/feed/update/{post_id}",
            "site": site_name,
        }
    else:
        error_body = resp.text[:500]
        logger.error(f"❌ LinkedIn post failed ({site_name}): {resp.status_code}")
        return {"success": False, "error": f"HTTP {resp.status_code}: {error_body}"}


async def share_article(url: str, title: str, description: str = "",
                        site_name: Optional[str] = None) -> Dict[str, Any]:
    """Deel een artikel naar LinkedIn voor een specifieke site."""
    text = title
    if description:
        text += f"\n\n{description}"
    text += f"\n\n{url}"
    return await post_update(text, article_url=url, site_name=site_name)

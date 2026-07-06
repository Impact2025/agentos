"""
X (Twitter) service — post een tweet via API v2.

X vereist OAuth 1.0a User Context voor het posten van tweets (geen simpele
bearer-token zoals de andere platformen). `httpx` ondersteunt geen OAuth1
signing, dus dit gebruikt `requests` + `requests_oauthlib.OAuth1` (sync),
uitgevoerd in een thread zodat de rest van de codebase async kan blijven.

Vereist 4 waarden uit een X Developer-app (developer.x.com) met
"Read and Write" permissions:
  - API Key + API Key Secret (consumer key/secret)
  - Access Token + Access Token Secret (user-context, gegenereerd voor je eigen account)

Let op: het gratis/Free-tier van de X API staat schrijftoegang toe met een
laag maandelijks quotum; controleer je tier voordat je dit op productie-schaal
inzet.
"""

import asyncio
import logging
from typing import Optional, Dict, Any

import requests
from requests_oauthlib import OAuth1

from .config import TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET

logger = logging.getLogger(__name__)

API_BASE = "https://api.twitter.com/2"


def _get_site_data(site_name: Optional[str] = None) -> tuple:
    """Haal (api_key, api_secret, access_token, access_secret) op voor een site."""
    creds = [TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]

    if site_name:
        try:
            from ..domains.seo import sites as sites_service
            for s in sites_service.list_sites():
                norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
                if norm(s.get("name", "")) == norm(site_name):
                    site_full = sites_service.get_site(s["id"])
                    if site_full:
                        site_creds = [
                            (site_full.get("twitter_api_key") or "").strip(),
                            (site_full.get("twitter_api_secret") or "").strip(),
                            (site_full.get("twitter_access_token") or "").strip(),
                            (site_full.get("twitter_access_secret") or "").strip(),
                        ]
                        if all(site_creds):
                            creds = site_creds
                    break
        except Exception:
            pass

    return tuple(creds)


def is_configured(site_name: Optional[str] = None) -> bool:
    return all(_get_site_data(site_name))


def _post_sync(text: str, creds: tuple) -> Dict[str, Any]:
    api_key, api_secret, access_token, access_secret = creds
    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    resp = requests.post(f"{API_BASE}/tweets", auth=auth, json={"text": text}, timeout=30)
    return {"status_code": resp.status_code, "body": resp.text}


def _get_me_sync(creds: tuple) -> Dict[str, Any]:
    api_key, api_secret, access_token, access_secret = creds
    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    resp = requests.get(f"{API_BASE}/users/me", auth=auth, timeout=15)
    return {"status_code": resp.status_code, "body": resp.text}


async def get_account_info(site_name: Optional[str] = None) -> Dict[str, Any]:
    """Haal accountgegevens op — gebruikt om de verbinding te testen."""
    creds = _get_site_data(site_name)
    if not all(creds):
        raise ValueError("Geen volledige X-credentials geconfigureerd.")
    result = await asyncio.to_thread(_get_me_sync, creds)
    if result["status_code"] != 200:
        raise ValueError(f"HTTP {result['status_code']}: {result['body'][:300]}")
    import json as _json
    return _json.loads(result["body"]).get("data", {})


async def post_update(text: str, article_url: Optional[str] = None,
                      site_name: Optional[str] = None) -> Dict[str, Any]:
    """Post een tweet voor een specifieke site."""
    creds = _get_site_data(site_name)
    if not all(creds):
        return {"success": False, "error": f"Geen volledige X-credentials voor {site_name or 'globale config'}"}

    tweet_text = text
    if article_url and article_url not in text:
        # X telt links altijd als 23 tekens ongeacht de echte lengte (t.co-shortening).
        tweet_text = f"{text}\n\n{article_url}"
    tweet_text = tweet_text[:280]

    result = await asyncio.to_thread(_post_sync, tweet_text, creds)

    if result["status_code"] == 201:
        import json as _json
        data = _json.loads(result["body"]).get("data", {})
        tweet_id = data.get("id", "")
        logger.info(f"✅ X post OK — site={site_name}, tweet_id={tweet_id}")
        return {
            "success": True,
            "post_id": tweet_id,
            "url": f"https://x.com/i/web/status/{tweet_id}" if tweet_id else "",
            "site": site_name,
        }
    else:
        logger.error(f"❌ X post failed ({site_name}): {result['status_code']}")
        return {"success": False, "error": f"HTTP {result['status_code']}: {result['body'][:500]}"}

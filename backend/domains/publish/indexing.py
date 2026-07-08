"""
Directe indexering — Goldie's pijler 4: zodra een artikel live staat, de URL
actief bij zoekmachines aanmelden in plaats van wachten op een crawl.

Drie routes, allemaal uitsluitend aangeroepen vanuit `approve_and_publish`
(dus na menselijke goedkeuring in de Wachtrij):

  1. GSC-sitemap-submit  — bestond al (`seo/gsc.py`), blijft de hoofdroute
     naar Google.
  2. IndexNow             — dekt Bing/Yandex/Seznam/Naver, gratis en direct.
     Vereist een key-bestand `{key}.txt` op de site-root; voor Netlify-sites
     wordt dat automatisch meegedeployed (zie `service.build_site_files`),
     voor elders gehoste sites moet de eigenaar het bestand zelf plaatsen.
  3. Google Indexing API  — achter GOOGLE_INDEXING_ENABLED (default uit):
     officieel alleen bedoeld voor JobPosting/Livestream-content en vereist
     Owner-rechten voor het service-account in Search Console. Gebruik op
     eigen risico; de sitemap-submit blijft de nette route.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List
from urllib.parse import urlparse

import httpx

from ...shared.config import GOOGLE_INDEXING_ENABLED
from ..seo import sites as sites_service

logger = logging.getLogger(__name__)

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def ensure_indexnow_key(site: Dict) -> str:
    """Bestaande IndexNow-key van de site, of genereer + persisteer er één.
    De key is niet geheim in cryptografische zin (hij staat publiek op de
    site), maar we behandelen 'm als secret-veld zodat hij niet rondslingert."""
    key = (site.get("indexnow_key") or "").strip()
    if key:
        return key
    key = uuid.uuid4().hex
    sites_service.update_site(site["id"], {"indexnow_key": key})
    site["indexnow_key"] = key
    return key


async def verify_indexnow(site: Dict) -> Dict:
    """Controleer of het IndexNow-keybestand écht live staat op de site-root.

    Voor Netlify-sites deployt Agent OS het bestand zelf mee, maar extern
    gehoste sites (Vercel/eigen CMS) moeten het handmatig plaatsen — zonder
    dat bestand negeren Bing/Yandex/Naver elke IndexNow-submit stilletjes."""
    base_url = (site.get("base_url") or "").strip().rstrip("/")
    key = (site.get("indexnow_key") or "").strip()
    if not base_url:
        return {"status": "geen-base-url",
                "detail": "Site heeft geen base_url — IndexNow niet controleerbaar."}
    if not key:
        return {"status": "geen-key",
                "detail": "Nog geen IndexNow-key — wordt bij de eerste publicatie aangemaakt."}
    key_url = f"{base_url}/{key}.txt"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(key_url)
        if resp.status_code == 200 and resp.text.strip() == key:
            return {"status": "ok", "key_url": key_url}
        return {"status": "keyfile-ontbreekt", "key_url": key_url,
                "status_code": resp.status_code,
                "detail": f"Verwachtte de key als bestandsinhoud op {key_url} — "
                          "plaats het bestand op de site-root (extern gehoste site) "
                          "of publiceer één artikel (Netlify deployt het mee)."}
    except Exception as e:
        return {"status": "fout", "key_url": key_url, "detail": str(e)[:200]}


async def submit_indexnow(site: Dict, urls: List[str]) -> Dict:
    """Meld URL's aan via IndexNow. Faalt zacht — indexering mag een
    geslaagde publicatie nooit laten mislukken."""
    urls = [u for u in urls if u.startswith("http")]
    if not urls:
        return {"status": "overgeslagen", "detail": "geen absolute URL's"}
    key = (site.get("indexnow_key") or "").strip()
    if not key:
        return {"status": "overgeslagen", "detail": "geen IndexNow-key voor deze site"}

    host = urlparse(urls[0]).netloc
    body = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls[:100],
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(INDEXNOW_ENDPOINT, json=body)
        # 200 = verwerkt, 202 = geaccepteerd; al het andere is een fout.
        ok = resp.status_code in (200, 202)
        if not ok:
            logger.warning("[indexing] IndexNow gaf %s: %s", resp.status_code, resp.text[:200])
        return {"status": "ingediend" if ok else "fout",
                "status_code": resp.status_code, "urls": len(urls)}
    except Exception as e:
        logger.warning("[indexing] IndexNow-aanroep mislukt: %s", e)
        return {"status": "fout", "detail": str(e)[:200]}


async def submit_google_indexing(url: str) -> Dict:
    """Google Indexing API (urlNotifications.publish) — alleen met
    GOOGLE_INDEXING_ENABLED=1. Zie de module-docstring voor de caveats."""
    if not GOOGLE_INDEXING_ENABLED:
        return {"status": "uitgeschakeld", "detail": "zet GOOGLE_INDEXING_ENABLED=1 in .env"}
    try:
        import asyncio

        def _publish() -> Dict:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from ..seo.gsc import _resolve_credentials_path

            creds = service_account.Credentials.from_service_account_file(
                _resolve_credentials_path(),
                scopes=["https://www.googleapis.com/auth/indexing"],
            )
            service = build("indexing", "v3", credentials=creds, cache_discovery=False)
            return service.urlNotifications().publish(
                body={"url": url, "type": "URL_UPDATED"}
            ).execute()

        result = await asyncio.to_thread(_publish)
        return {"status": "ingediend", "notify_time": (result.get("urlNotificationMetadata") or {}).get("latestUpdate", {}).get("notifyTime", "")}
    except Exception as e:
        # Meestal: 403 (service-account is geen Owner) of API niet geactiveerd.
        logger.warning("[indexing] Google Indexing API mislukt voor %s: %s", url, e)
        return {"status": "fout", "detail": str(e)[:250]}

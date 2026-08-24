"""
External content check — voorkomt dat de contentpijplijn een onderwerp kiest
dat op de site zelf al bestaat.

Sommige sites (Bijeen, Steentjebijsteentje) hebben een eigen CMS/blog buiten
Impact OS om (los Next.js/Neon-project), dus de eigen `published_pages`-tabel
weet niets van wat daar al live staat. Als `sites.external_db_url` is
ingevuld (een read-only/gewone Postgres-connectiestring naar die site's eigen
database), halen we hier live de bestaande titels + slugs op zodat de
schrijf-/topic-keuze daar tegenaan kan checken. Faalt stil (lege lijst) als
er geen URL is, de tabel niet bestaat, of de connectie mislukt — dit mag de
contentpijplijn nooit blokkeren."""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def fetch_external_titles(site: Dict) -> List[Dict[str, str]]:
    url = (site.get("external_db_url") or "").strip()
    if not url:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=10)
        try:
            cur = conn.cursor()
            cur.execute("SELECT title, slug FROM blog_posts")
            return [{"title": t or "", "slug": s or ""} for t, s in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[external-content] Kon bestaande content niet ophalen voor %s: %s",
                        site.get("name"), str(e)[:200])
        return []


# ── Zero-config fallback: live sitemap van de site zelf ─────────────────────
# Werkt zonder credentials voor élke live site — vangt content die buiten
# Impact OS om is gepubliceerd, ook als er geen external_db_url is ingevuld
# (precies het gat waardoor Bijeen een duplicaat-artikel kreeg).

_sitemap_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
_SITEMAP_TTL_SECONDS = 900
_MAX_SITEMAP_FILES = 5  # sitemap-index + max 4 sub-sitemaps


def fetch_live_sitemap_slugs(site: Dict) -> List[Dict[str, str]]:
    """Haal pagina-slugs uit de live sitemap.xml van de site (met TTL-cache).

    Alleen slugs die op een artikel lijken (≥2 woorddelen of ≥8 tekens) doen
    mee, zodat korte sectiepaden als /blog of /ai geen valse dedup-hits geven.
    Faalt stil met een lege lijst — mag de pijplijn nooit blokkeren."""
    base = (site.get("base_url") or "").strip().rstrip("/")
    if not base:
        return []
    now = time.time()
    cached = _sitemap_cache.get(base)
    if cached and cached[0] > now:
        return cached[1]

    slugs: List[Dict[str, str]] = []
    try:
        import httpx
        fetched: set = set()
        queue = [f"{base}/sitemap.xml"]
        while queue and len(fetched) < _MAX_SITEMAP_FILES:
            sm_url = queue.pop(0)
            if sm_url in fetched:
                continue
            fetched.add(sm_url)
            resp = httpx.get(sm_url, timeout=10, follow_redirects=True)
            if resp.status_code != 200:
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text):
                if loc.rstrip("/").endswith(".xml"):
                    queue.append(loc)  # sitemap-index → sub-sitemap
                    continue
                last = loc.rstrip("/").rsplit("/", 1)[-1]
                last = re.sub(r"\.(html?|php)$", "", last)
                if last and ("-" in last or len(last) >= 8):
                    slugs.append({"title": "", "slug": last})
    except Exception as e:
        logger.debug("[external-content] Sitemap ophalen mislukt voor %s: %s", base, str(e)[:150])

    _sitemap_cache[base] = (now + _SITEMAP_TTL_SECONDS, slugs)
    return slugs


def fetch_all_known_content(site: Dict) -> List[Dict[str, str]]:
    """Alle bekende bestaande content van een site: externe CMS-database
    (indien geconfigureerd) + live sitemap (altijd, zero-config)."""
    return fetch_external_titles(site) + fetch_live_sitemap_slugs(site)


_sitemap_url_cache: Dict[str, Tuple[float, List[str]]] = {}


def fetch_live_sitemap_urls(site: Dict) -> List[str]:
    """Volledige pagina-URL's uit de live sitemap.xml — de linkstap van de
    artikel-generator heeft absolute URL's nodig (slugs alleen zijn niet
    genoeg om een geldige interne link te bouwen). Zelfde stille-fallback- en
    TTL-cache-aanpak als `fetch_live_sitemap_slugs`."""
    base = (site.get("base_url") or "").strip().rstrip("/")
    if not base:
        return []
    now = time.time()
    cached = _sitemap_url_cache.get(base)
    if cached and cached[0] > now:
        return cached[1]

    urls: List[str] = []
    try:
        import httpx
        fetched: set = set()
        queue = [f"{base}/sitemap.xml"]
        while queue and len(fetched) < _MAX_SITEMAP_FILES:
            sm_url = queue.pop(0)
            if sm_url in fetched:
                continue
            fetched.add(sm_url)
            resp = httpx.get(sm_url, timeout=10, follow_redirects=True)
            if resp.status_code != 200:
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text):
                if loc.rstrip("/").endswith(".xml"):
                    queue.append(loc)
                else:
                    urls.append(loc)
    except Exception as e:
        logger.debug("[external-content] Sitemap-URL's ophalen mislukt voor %s: %s", base, str(e)[:150])

    _sitemap_url_cache[base] = (now + _SITEMAP_TTL_SECONDS, urls)
    return urls

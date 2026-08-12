"""SERP-Omni engine — reverse-engineering van de zoekresultatenpagina (SERP).

Dit is het "brein" achter de omnipresentie-strategie uit de Goldie/Floate-
analyse: in plaats van blind je eigen site te optimaliseren, kijken we eerst
WIE er bovenaan staat en OP WELK PLATFORM. Staat Reddit bovenaan voor een
zoekwoord -> we genereren een Reddit-concept. Staat er een videobox ->
een YouTube-script. LinkedIn/X-artikelen ranken beter dan losse tweets, dus
die wegen zwaarder mee in het profiel.

De extractie gebruikt de gedeelde websearch-laag (keyless DDG/Bing als
Tavily/Brave niet aanstaan) en classificeert elke hit naar platform. Alles is
deterministisch + graceful-degrade: als de SERP niet op te halen is, geeft de
functie een leeg profiel terug met een duidelijke status i.p.v. te crashen of
stil een lege lijst te retourneren (zie de websearch-les van 17 jul).

Resultaatvorm van `analyze_serp(query)`:
    {
      "query": str,
      "status": "ok" | "degraded" | "empty",
      "platforms": {"reddit": int, "youtube": int, "linkedin": int,
                    "x": int, "owned": int, "other": int},
      "dominant": ["reddit"] | ["youtube", "linkedin"] | ... (gesorteerd),
      "has_video_box": bool,
      "has_reddit_thread": bool,
      "top_results": [{"title","url","snippet","platform"}],
      "recommended_assets": ["reddit_post", "youtube_script", ...],
      "note": str,
    }
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional

from ...shared.websearch import search as web_search, WebSearchError

logger = logging.getLogger(__name__)

# Hoe lang een SERP-profiel in de cache blijft (de SERP verandert niet per uur).
_SERP_CACHE_TTL_SECONDS = 6 * 3600
_serp_cache: Dict[str, tuple[float, dict]] = {}

# Platform-herkenning op URL + titel. Volgorde = prioriteit bij overlap.
_PLATFORM_PATTERNS = [
    ("reddit", re.compile(r"reddit\.com|redd\.it", re.IGNORECASE)),
    ("youtube", re.compile(r"youtube\.com|youtu\.be", re.IGNORECASE)),
    ("linkedin", re.compile(r"linkedin\.com", re.IGNORECASE)),
    ("x", re.compile(r"x\.com|twitter\.com", re.IGNORECASE)),
]


def _classify(url: str, title: str = "") -> str:
    text = f"{url} {title}"
    for name, pat in _PLATFORM_PATTERNS:
        if pat.search(text):
            return name
    return ""


def _is_owned(url: str, owned_domains: List[str]) -> bool:
    u = url.lower()
    return any(d.lower() in u for d in owned_domains)


def _recommend(platforms: Dict[str, int], has_video_box: bool,
               has_reddit: bool, query: str = "") -> List[str]:
    """Zet een SERP-profiel om in de asset-types die we moeten genereren.

    Verbeterd model (afgeleid van de Goldie/Floate-analyse + de pro-run van
    12 aug op NL-institutionele zoekwoorden):

      - Reddit-thread of -dominantie -> reddit_post (hoogste AIO-hefboom).
      - 'other' domineert (kennis/overheid/VNG/Rijksoverheid) -> Reddit is JUIST
        de onderbenutte hefboom (Floate: weinig concurrentie op Reddit = makkelijker
        ranken in AIO). Dus bij weinig platform-signaal wél Reddit aanbevelen.
      - videobox of YouTube of video-intentie in snippets -> youtube_script.
      - LinkedIn aanwezig -> linkedin_article (rankt beter dan X volgens video).
      - X aanwezig -> x_post (laagste prioriteit, bereik beperkt).
      - Zwak signaal (alleen 'other') -> genereer een brede omnipresence-set
        (reddit + linkedin + x + aeo) in plaats van te kiezen: je wilt overal staan.
    """
    assets: List[str] = []
    if has_reddit or platforms.get("reddit", 0) >= 1:
        assets.append("reddit_post")
    if has_video_box or platforms.get("youtube", 0) >= 1:
        assets.append("youtube_script")
    if platforms.get("linkedin", 0) >= 1:
        assets.append("linkedin_article")
    if platforms.get("x", 0) >= 1:
        assets.append("x_post")

    other = platforms.get("other", 0)
    # Zwak signaal: SERP zit vol 'other' (kennis/overheid). Dan is Reddit de
    # open hefboom en willen we omnipresence — genereer de volle set.
    if not assets:
        assets = ["reddit_post", "linkedin_article", "x_post", "aeo_snippet"]
    elif other >= 8 and "reddit_post" not in assets:
        # Veel 'other', weinig platformen: Reddit er expliciet bij voor de
        # onderbenutte positie in AIO.
        assets.append("reddit_post")
    # Altijd een AEO-snippet als anker (eigen site blijft de basis).
    if "aeo_snippet" not in assets:
        assets.append("aeo_snippet")
    return assets


def analyze_serp(query: str, owned_domains: Optional[List[str]] = None,
                 use_cache: bool = True) -> Dict:
    """Reverse-engineer de SERP voor `query` en geef een platform-profiel.

    `owned_domains`: lijst van domeinen die van de gebruiker zijn (worden als
    'owned' geteld, niet als concurrentie-signaal). `use_cache`: gebruik/
    schrijf de TTL-cache.
    """
    key = (query or "").strip().lower()
    if not key:
        return _empty_profile(query, "lege query")
    if use_cache:
        cached = _serp_cache.get(key)
        if cached and (time.time() - cached[0]) < _SERP_CACHE_TTL_SECONDS:
            prof = dict(cached[1])
            prof["cached"] = True
            return prof

    owned = owned_domains or []
    try:
        hits = web_search(query, max_results=12)
    except WebSearchError as e:
        logger.warning("[serp] search faalde voor '%s': %s", query, str(e)[:160])
        return _empty_profile(query, f"SERP niet op te halen: {str(e)[:120]}")

    if not hits:
        return _empty_profile(query, "SERP leverde 0 resultaten op")

    platforms: Dict[str, int] = {k: 0 for k, _ in _PLATFORM_PATTERNS}
    platforms["owned"] = 0
    platforms["other"] = 0
    top_results: List[Dict] = []
    has_video_box = False
    has_reddit = False

    for h in hits:
        url = h.get("url", "")
        title = h.get("title", "")
        plat = _classify(url, title)
        if plat:
            platforms[plat] += 1
            if plat == "reddit":
                has_reddit = True
        elif _is_owned(url, owned):
            platforms["owned"] += 1
        else:
            platforms["other"] += 1
        # Video-intentie: youtube-url OF 'video/uitleg/how-to' in titel+snippet.
        # Op DDG (na Tavily-quota) staan er zelden youtube-url's bovenaan, maar
        # de intentie is er wel — dan willen we alsnog een YouTube-script.
        blob = (title + " " + h.get("snippet", "")).lower()
        if plat == "youtube" or any(k in blob for k in
                                     ("video", "uitleg", "how-to", "how to", "tutorial", "youtube")):
            has_video_box = True
        top_results.append({
            "title": title, "url": url,
            "snippet": (h.get("snippet", "") or "")[:300],
            "platform": plat or ("owned" if _is_owned(url, owned) else "other"),
        })

    # Dominant = platformen gesorteerd op aantal hits (boven de 0).
    dominant = [p for p, n in sorted(platforms.items(), key=lambda kv: kv[1],
                                     reverse=True) if n > 0 and p not in ("other", "owned")]

    prof = {
        "query": query,
        "status": "ok",
        "cached": False,
        "platforms": platforms,
        "dominant": dominant,
        "has_video_box": has_video_box,
        "has_reddit_thread": has_reddit,
        "top_results": top_results,
        "recommended_assets": _recommend(platforms, has_video_box, has_reddit, query),
        "note": "",
    }
    _serp_cache[key] = (time.time(), prof)
    return prof


def _empty_profile(query: str, note: str) -> Dict:
    platforms = {k: 0 for k, _ in _PLATFORM_PATTERNS}
    platforms["owned"] = 0
    platforms["other"] = 0
    return {
        "query": query,
        "status": "empty",
        "cached": False,
        "platforms": platforms,
        "dominant": [],
        "has_video_box": False,
        "has_reddit_thread": False,
        "top_results": [],
        "recommended_assets": ["reddit_post", "linkedin_article"],
        "note": note,
    }


def reset_serp_cache() -> None:
    """Lokale cache wissen (voor tests / handmatige verversing)."""
    _serp_cache.clear()

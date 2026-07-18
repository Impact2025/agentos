"""Gedeelde web-zoeklaag met provider-fallback: Tavily → Brave.

Waarom: op 2026-07-18 legde één uitgeputte Tavily-quota zowel de radar-scan
als de lead-zoekacties plat (zie ook de linkbuilding-les van 17 jul). Iris'
remedie was "koppel een tweede LLM", maar het probleem is de zóekprovider,
niet het taalmodel. Deze module lost de echte oorzaak op: elke zoek-flow
(leads, tools/web_search, radar) praat tegen `search()`, die providers in
volgorde probeert en pas faalt als álle geconfigureerde providers falen —
en dan luid (WebSearchError), nooit stil met een lege lijst.

Resultaatvorm is overal gelijk: [{"title", "url", "snippet"}].
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .config import BRAVE_SEARCH_API_KEY, TAVILY_API_KEY

logger = logging.getLogger(__name__)

_QUOTA_MARKERS = ("quota", "usage", "limit", "credits", "429", "432", "payment")


class WebSearchError(RuntimeError):
    """Alle geconfigureerde zoekproviders faalden (of geen enkele is ingesteld)."""


def _looks_like_quota(err: Exception) -> bool:
    msg = str(err).lower()
    return any(m in msg for m in _QUOTA_MARKERS)


def _tavily_search(query: str, max_results: int,
                   exclude_domains: Optional[List[str]]) -> List[Dict]:
    from tavily import TavilyClient
    client = TavilyClient(api_key=TAVILY_API_KEY)
    resp = client.search(
        query=query,
        max_results=max_results + 4,
        search_depth="advanced",
        include_answer=False,
        exclude_domains=exclude_domains or [],
    )
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("content", "")}
        for r in resp.get("results", [])
    ][:max_results]


def _brave_search(query: str, max_results: int,
                  exclude_domains: Optional[List[str]]) -> List[Dict]:
    import httpx
    resp = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(max_results + 6, 20)},
        headers={"X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                 "Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    results = ((resp.json().get("web") or {}).get("results")) or []
    excl = [d.lower() for d in (exclude_domains or [])]

    def _excluded(url: str) -> bool:
        u = url.lower()
        return any(d in u for d in excl)

    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("description", "")}
        for r in results if r.get("url") and not _excluded(r["url"])
    ][:max_results]


def brave_search(query: str, max_results: int = 6,
                 exclude_domains: Optional[List[str]] = None) -> List[Dict]:
    """Alleen de Brave-provider — voor flows met een Tavily-specifieke primaire
    route (bv. de radar met news-topic/freshness) die enkel een vangnet willen."""
    if not BRAVE_SEARCH_API_KEY:
        raise WebSearchError("BRAVE_SEARCH_API_KEY niet ingesteld")
    return _brave_search(query, max_results, exclude_domains)


def providers_configured() -> List[str]:
    out = []
    if TAVILY_API_KEY:
        out.append("tavily")
    if BRAVE_SEARCH_API_KEY:
        out.append("brave")
    return out


def search(query: str, max_results: int = 6,
           exclude_domains: Optional[List[str]] = None) -> List[Dict]:
    """Zoek op het web met provider-fallback. Geeft [{"title","url","snippet"}].

    Gooit WebSearchError als álle geconfigureerde providers falen of als er
    geen enkele provider is ingesteld — stil terugvallen op [] maskeerde
    eerder een uitgeputte quota als "geen resultaten".
    """
    errors: List[str] = []
    if TAVILY_API_KEY:
        try:
            return _tavily_search(query, max_results, exclude_domains)
        except Exception as e:  # noqa: BLE001
            level = logging.WARNING if BRAVE_SEARCH_API_KEY else logging.ERROR
            logger.log(level, "[websearch] Tavily faalde%s: %s — %s",
                       " (quota?)" if _looks_like_quota(e) else "", str(e)[:200],
                       "probeer Brave" if BRAVE_SEARCH_API_KEY else "geen fallback ingesteld")
            errors.append(f"tavily: {str(e)[:200]}")
    if BRAVE_SEARCH_API_KEY:
        try:
            return _brave_search(query, max_results, exclude_domains)
        except Exception as e:  # noqa: BLE001
            logger.error("[websearch] Brave-fallback faalde ook: %s", str(e)[:200])
            errors.append(f"brave: {str(e)[:200]}")
    if not errors:
        raise WebSearchError(
            "Geen zoekprovider geconfigureerd — zet TAVILY_API_KEY en/of "
            "BRAVE_SEARCH_API_KEY in .env")
    raise WebSearchError("Alle zoekproviders faalden: " + " | ".join(errors))

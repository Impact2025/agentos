"""Gedeelde web-zoeklaag met provider-fallback: Tavily → Brave → DuckDuckGo → Bing.

Waarom: op 2026-07-18 legde één uitgeputte Tavily-quota zowel de radar-scan
als de lead-zoekacties plat (zie ook de linkbuilding-les van 17 jul). Iris'
remedie was "koppel een tweede LLM", maar het probleem is de zóekprovider,
niet het taalmodel. Deze module lost de echte oorzaak op: elke zoek-flow
(leads, tools/web_search, radar) praat tegen `search()`, die providers in
volgorde probeert en pas faalt als álle geconfigureerde providers falen —
en dan luid (WebSearchError), nooit stil met een lege lijst.

Twee dingen die de keten op 2026-07-20 alsnog plat legden, en hier zijn opgelost:

(a) Zonder BRAVE_SEARCH_API_KEY was er ná Tavily niets. Een tweede *betaalde*
    provider als enige vangnet betekent dat een lege of niet-ingevulde key de
    hele keten alsnog laat vallen. DuckDuckGo (`ddgs`) sluit de rij als
    keyless, quota-loze laatste redmiddel — precies wat je nodig hebt op het
    moment dat het betaalde abonnement op is. Dezelfde terugval draaide al in
    `linkbuilding/prospector.py`; hij hoort in de gedeelde laag, niet in één domein.

(b) Een uitgeputte quota is een *toestand*, geen incident: elke query van een
    batch liep opnieuw tegen dezelfde 432 aan (5 zoekopdrachten = 5 zinloze
    calls, 5× latency, en pas daarna de terugval). `_QUOTA_BACKOFF_SECONDS`
    zet de provider proces-breed even opzij zodra hij quota-uitputting meldt,
    zodat volgende calls meteen doorschuiven. Alleen quota-fouten blokkeren —
    een incidentele timeout mag een provider niet 6 uur uitschakelen.

Resultaatvorm is overal gelijk: [{"title", "url", "snippet"}].
"""
from __future__ import annotations

import logging
import time
import re
from typing import Dict, List, Optional

from .config import BRAVE_SEARCH_API_KEY, GOOGLE_PSE_API_KEY, GOOGLE_PSE_CX, TAVILY_API_KEY

logger = logging.getLogger(__name__)

_QUOTA_MARKERS = ("quota", "usage", "limit", "credits", "429", "432", "payment",
                  "upgrade your plan")


class EmptiesError(RuntimeError):
    """Een provider gaf een 200 OK maar 0 resultaten terug.

    Zonder deze fout valt de keten stil met `[]`: een lege lijst wordt als
    'succes' gezien en de volgende provider in de fallback-keten wordt nooit
    geprobeerd. Eén provider die wél resultaten hád, wordt dan nooit bereikt —
    precies wat er gebeurde bij de ~10 linkbuilding-sites die 'alle providers
    faalden' terwijl Bing/DDG wél hits hadden. We gooien dus ook bij 0 hits,
    zodat de keten naar de volgende, onafhankelijke provider doorschuift."""

# Hoe lang een provider die quota-uitputting meldt wordt overgeslagen.
_QUOTA_BACKOFF_SECONDS = 6 * 3600
_quota_block: Dict[str, float] = {}


class WebSearchError(RuntimeError):
    """Alle geconfigureerde zoekproviders faalden (of geen enkele is ingesteld)."""


def _looks_like_quota(err: Exception) -> bool:
    msg = str(err).lower()
    return any(m in msg for m in _QUOTA_MARKERS)


def _blocked(provider: str) -> bool:
    return time.time() < _quota_block.get(provider, 0.0)


def _note_error(provider: str, err: Exception) -> None:
    """Zet een quota-uitgeputte provider tijdelijk opzij. Andere fouten niet:
    een timeout is geen reden om een werkende provider 6 uur te negeren."""
    if _looks_like_quota(err):
        first = not _blocked(provider)
        _quota_block[provider] = time.time() + _QUOTA_BACKOFF_SECONDS
        if first:
            logger.error("[websearch] %s meldt quota-uitputting — %d uur "
                         "overgeslagen, terugval neemt het over: %s",
                         provider, _QUOTA_BACKOFF_SECONDS // 3600, str(err)[:200])


def reset_quota_blocks() -> None:
    """Hef alle backoffs op (voor tests en na het bijwerken van een abonnement)."""
    _quota_block.clear()


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


def _ddg_search(query: str, max_results: int,
                exclude_domains: Optional[List[str]]) -> List[Dict]:
    """Keyless laatste redmiddel. Geen key, geen quota — draait dus ook als
    élk betaald abonnement op is."""
    from ddgs import DDGS
    hits = DDGS().text(query, max_results=max_results + 6, region="nl-nl") or []
    excl = [d.lower() for d in (exclude_domains or [])]
    out = []
    for h in hits:
        url = h.get("href") or h.get("url") or ""
        if not url or any(d in url.lower() for d in excl):
            continue
        out.append({"title": h.get("title") or "", "url": url,
                    "snippet": (h.get("body") or "")[:300]})
    return out[:max_results]


def _ddg_html_search(query: str, max_results: int,
                      exclude_domains: Optional[List[str]]) -> List[Dict]:
    """Keyless achtervang achter de ddgs-library.

    `ddgs` praat tegen DuckDuckGo's JSON-API, die bij geautomatiseerd
    verkeer regelmatig rate-limited raakt (429/'Ratelimited'). Het
    HTML-endpoint (html.duckduckgo.com/html/) is een ándere code-path en
    wordt zelden tegelijk geblokkeerd — precies wat je nodig hebt als de
    primaire DDG-aanroep dichtgaat. Geen key, geen quota."""
    import httpx
    from bs4 import BeautifulSoup  # type: ignore
    resp = httpx.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0 Safari/537.36")},
        timeout=15, follow_redirects=True,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    excl = [d.lower() for d in (exclude_domains or [])]
    out = []
    for a in soup.select("a.result__a"):
        url = a.get("href", "")
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http") or any(d in url.lower() for d in excl):
            continue
        snippet_el = a.find_parent("div", class_="result")
        snippet = ""
        if snippet_el:
            sn = snippet_el.select_one(".result__snippet")
            snippet = (sn.get_text(strip=True) if sn else "")[:300]
        out.append({"title": a.get_text(strip=True), "url": url, "snippet": snippet})
        if len(out) >= max_results:
            break
    if not out:
        raise WebSearchError("DDG HTML-endpoint leverde 0 resultaten op")
    return out


def _bing_search(query: str, max_results: int,
                 exclude_domains: Optional[List[str]]) -> List[Dict]:
    """Keyless vierde aanbod, op ándere infra dan DuckDuckGo.

    Zowel `ddg` als `ddg_html` praten tegen DuckDuckGo — als DDG ons
    rate-limiteert (429) vallen ze tegelijk uit, en blijft er na Tavily-quota
    niets over. Bing (bing.com/search) is onafhankelijke infrastructuur: wanneer
    DDG dichtzit blijft Bing bereikbaar. Geen key, geen quota — precies het
    keyless vangnet dat de keten nodig heeft nu het betaalde abonnement op is.
    """
    import httpx
    from bs4 import BeautifulSoup  # type: ignore
    resp = httpx.get(
        "https://www.bing.com/search",
        params={"q": query, "count": max_results + 6, "setlang": "nl-NL"},
        headers={"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")},
        timeout=15, follow_redirects=True,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    excl = [d.lower() for d in (exclude_domains or [])]
    out: List[Dict] = []
    for li in soup.select("li.b_algo"):
        h2 = li.find("h2")
        if not h2:
            continue
        a = h2.find("a")
        url = a.get("href", "") if a else ""
        if not url.startswith("http") or any(d in url.lower() for d in excl):
            continue
        sn = li.select_one("p")
        snippet = (sn.get_text(strip=True) if sn else "")[:300]
        out.append({"title": (a.get_text(strip=True) if a else ""),
                    "url": url, "snippet": snippet})
        if len(out) >= max_results:
            break
    if not out:
        raise WebSearchError("Bing leverde 0 resultaten op")
    return out


def _google_pse_search(query: str, max_results: int,
                       exclude_domains: Optional[List[str]]) -> List[Dict]:
    """Keyed backbone op éigen index (Programmable Search Engine) — onafhankelijk
    van Bing/DDG/Tavily. GRATIS 100 queries/dag, gecontracteerd (geen bot-block).

    Waarom dit de ontbrekende pijler is: DDG + DDG_HTML zijn dezelfde
    infrastructuur, dus 'onafhankelijk' was tot nu toe Tavily + DDG + Bing.
    Als Bing ons IP blokkeert én DDG rate-limiteert (beide gebeuren bij
    geautomatiseerd verkeer), valt de hele keten terug op 0 poten. Google PSE
    heeft een eigen crawl-index en een contractuele SLA → een échte derde
    onafhankelijke pijler. Wordt door `providers_configured()` alleen in de
    keten gezet als zowel GOOGLE_PSE_API_KEY als GOOGLE_PSE_CX ingesteld zijn.
    """
    if not (GOOGLE_PSE_API_KEY and GOOGLE_PSE_CX):
        raise WebSearchError("GOOGLE_PSE_API_KEY/GOOGLE_PSE_CX niet ingesteld")
    import httpx
    params = {"key": GOOGLE_PSE_API_KEY, "cx": GOOGLE_PSE_CX,
              "q": query, "num": min(max_results + 4, 10), "lr": "lang_nl",
              "gl": "nl"}
    if exclude_domains:
        params["siteSearch"] = " OR ".join(exclude_domains)
        params["siteSearchFilter"] = "e"
    resp = httpx.get("https://www.googleapis.com/customsearch/v1",
                     params=params, timeout=15)
    resp.raise_for_status()
    items = (resp.json() or {}).get("items") or []
    out = [{"title": r.get("title", ""), "url": r.get("link", ""),
            "snippet": (r.get("snippet") or "")[:300]} for r in items
           if r.get("link")]
    if not out:
        raise EmptiesError("Google PSE leverde 0 resultaten op")
    return out[:max_results]


def brave_search(query: str, max_results: int = 6,
                 exclude_domains: Optional[List[str]] = None) -> List[Dict]:
    """Alleen de Brave-provider — voor flows met een Tavily-specifieke primaire
    route (bv. de radar met news-topic/freshness) die enkel een vangnet willen."""
    if not BRAVE_SEARCH_API_KEY:
        raise WebSearchError("BRAVE_SEARCH_API_KEY niet ingesteld")
    return _brave_search(query, max_results, exclude_domains)


def keyless_search(query: str, max_results: int = 6,
                   exclude_domains: Optional[List[str]] = None) -> List[Dict]:
    """Alleen de keyless provider (DuckDuckGo) — voor flows die hun eigen
    Tavily-route hebben en enkel een vangnet zoeken dat nooit op quota stukloopt."""
    return _ddg_search(query, max_results, exclude_domains)


def providers_configured() -> List[str]:
    """Providers in probeervolgorde. DuckDuckGo én Bing staan er altijd bij:
    allebei keyless én op ándere infrastructuur, dus de keten is nooit leeg en
    als DDG ons rate-limiteert (waardoor ddg én ddg_html tegelijk dichtgaan)
    blijft Bing overeind."""
    out = []
    if TAVILY_API_KEY:
        out.append("tavily")
    if BRAVE_SEARCH_API_KEY:
        out.append("brave")
    if GOOGLE_PSE_API_KEY and GOOGLE_PSE_CX:
        out.append("google_pse")
    out.append("ddg")
    out.append("ddg_html")
    out.append("bing")
    return out


# Naam → functienaam, niet → functieobject: het object wordt pas op aanroepmoment
# opgezocht, zodat monkeypatchen in tests (en een latere provider-swap) werkt.
_PROVIDERS = {"tavily": "_tavily_search", "brave": "_brave_search",
              "google_pse": "_google_pse_search",
              "ddg": "_ddg_search", "ddg_html": "_ddg_html_search",
              "bing": "_bing_search"}


def provider_health() -> Dict[str, str]:
    """Status per provider voor dashboards/Iris: 'ok' | 'no_key' | 'quota'.
    Keyed providers met een lege key melden 'no_key' (niet geconfigureerd);
    providers in de actieve quota-backoff melden 'quota'. Keyless providers
    (ddg/ddg_html/bing) rapporteren altijd 'ok' — die kunnen niet op quota
    stuklopen, alleen tijdelijk rate-limited raken (en dat herstelt vanzelf)."""
    out: Dict[str, str] = {}
    for name in ("tavily", "brave", "google_pse"):
        key = {"tavily": TAVILY_API_KEY, "brave": BRAVE_SEARCH_API_KEY,
               "google_pse": (GOOGLE_PSE_API_KEY and GOOGLE_PSE_CX)}.get(name)
        if not key:
            out[name] = "no_key"
        elif _blocked(name):
            out[name] = "quota"
        else:
            out[name] = "ok"
    for name in ("ddg", "ddg_html", "bing"):
        out[name] = "quota" if _blocked(name) else "ok"
    return out


def probe_health(ref_query: str = "vrijwilligerswerk nederland") -> Dict[str, str]:
    """Meet de échte bereikbaarheid van elke geconfigureerde provider met één
    live call (niet alleen de key/quota-status). Nuttig voor de selfheal-
    watchdog: die kan daarmee zien of een 'quota'-blokkade inmiddels is
    opgeheven en de backoff veilig geheven mag worden.

    Geeft per provider 'ok' | 'down' | 'no_key'. Een provider die 0 resultaten
    of een exception geeft telt als 'down'; een keyless provider zonder key
    bestaat niet ('no_key')."""
    import httpx  # noqa: F401  (zekerheid dat httpx beschikbaar is)
    out: Dict[str, str] = {}
    for name in ("tavily", "brave", "google_pse", "ddg", "ddg_html", "bing"):
        if name in ("tavily", "brave", "google_pse"):
            key = {"tavily": TAVILY_API_KEY, "brave": BRAVE_SEARCH_API_KEY,
                   "google_pse": (GOOGLE_PSE_API_KEY and GOOGLE_PSE_CX)}.get(name)
            if not key:
                out[name] = "no_key"
                continue
        try:
            hits = globals()[_PROVIDERS[name]](ref_query, 3, None)
            out[name] = "ok" if hits else "down"
        except Exception:
            out[name] = "down"
    return out


def clear_recovered_blocks(ref_query: str = "vrijwilligerswerk nederland") -> List[str]:
    """Selfheal-Watchdog: meet elke provider live en hef de quota-backoff op
    voor providers die inmiddels wél resultaten geven. Zo herstelt de keten
    zichzelf zodra een abonnement is bijgevuld of een rate-limit is gezakt —
    zonder dat een mens de server hoeft te herstarten. Geeft de lijst met
    providers terug waarvan de blokkade is opgeheven."""
    health = probe_health(ref_query)
    lifted: List[str] = []
    for name, status in health.items():
        if status == "ok" and _blocked(name):
            _quota_block.pop(name, None)
            lifted.append(name)
    return lifted


def simplify_query(query: str) -> str:
    """Zet een operator-rijke zoekopdracht om in gewone trefwoorden.

    Tavily/DuckDuckGo/Bing begrijpen niet allemaal dezelfde syntax. Een
    opdracht vol `"aanhalingstekens"`, `OR`-ketens en `-site:` levert bij
    Bing en DDG vaak 0 resultaten op — die lezen als "niets gevonden" terwijl
    de vraag alleen verkeerd gesteld was. De gedeelde laag probeert zélf de
    vereenvoudigde vorm als de operator-rijke faalt, zodat één zoek-flow de
    ander niet hoeft te kennen. (De linkbuilding-prospector heeft zijn eigen
    kopie voor compatibiliteit met bestaande logs.)
    """
    q = re.sub(r"-\w+:\S+", " ", query or " ")   # -site:example.nl
    q = q.split(" OR ")[0]                         # alleen de eerste variant
    q = q.replace('"', " ")
    return re.sub(r"\s+", " ", q).strip()


def search(query: str, max_results: int = 6,
           exclude_domains: Optional[List[str]] = None) -> List[Dict]:
    """Zoek op het web met provider-fallback. Geeft [{"title","url","snippet"}].

    Gooit WebSearchError als álle providers falen — stil terugvallen op []
    maskeerde eerder een uitgeputte quota als "geen resultaten". Providers die
    kort geleden quota-uitputting meldden worden overgeslagen (zie kop).
    Levert de operator-rijke opdracht nergens iets op, dan volgt één keer de
    vereenvoudigde vorm (zonder OR/quotes/site:-) — een provider die de syntax
    niet snapt mag geen 'geen resultaten' opleveren.
    """
    plain = simplify_query(query)
    try:
        return _run_chain(query, max_results, exclude_domains)
    except (WebSearchError, EmptiesError) as e:
        if plain == query:
            raise
        logger.info("[websearch] '%s' leverde niets op — opnieuw als '%s'",
                    query, plain)
        return _run_chain(plain, max_results, exclude_domains)


def _run_chain(query: str, max_results: int,
               exclude_domains: Optional[List[str]]) -> List[Dict]:
    errors: List[str] = []
    chain = providers_configured()
    for name in chain:
        if _blocked(name):
            errors.append(f"{name}: quota-backoff actief, overgeslagen")
            continue
        try:
            res = globals()[_PROVIDERS[name]](query, max_results, exclude_domains)
        except Exception as e:  # noqa: BLE001
            _note_error(name, e)
            remaining = [p for p in chain[chain.index(name) + 1:] if not _blocked(p)]
            logger.log(logging.WARNING if remaining else logging.ERROR,
                       "[websearch] %s faalde%s: %s — %s", name,
                       " (quota)" if _looks_like_quota(e) else "", str(e)[:200],
                       f"probeer {remaining[0]}" if remaining else "geen terugval meer")
            errors.append(f"{name}: {str(e)[:200]}")
            continue
        if not res:
            # Een lege lijst van één provider is géén succes: de volgende,
            # onafhankelijke provider krijgt alsnog een kans. `ddgs` geeft bv.
            # [] terug bij rate-limit (niet bij een echte 0-hits) — zonder
            # deze check stopte de keten stil op [] en vielen DDG_HTML/Bing/
            # Google PSE nooit in. Pas ná alle providers pas geven we [] terug.
            remaining = [p for p in chain[chain.index(name) + 1:] if not _blocked(p)]
            errors.append(f"{name}: 0 resultaten")
            logger.log(logging.WARNING if remaining else logging.ERROR,
                       "[websearch] %s gaf 0 resultaten — %s", name,
                       f"probeer {remaining[0]}" if remaining else "geen terugval meer")
            continue
        return res
    raise WebSearchError("Alle zoekproviders faalden: " + " | ".join(errors))

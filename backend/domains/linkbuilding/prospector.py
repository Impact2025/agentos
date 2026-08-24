"""Linkbuilding-prospector — zoekt en kwalificeert linkkansen per site.

Bronnen: Tavily-websearch met zoekrecepten per kanstype (gastblog,
resource-pagina's, branchegidsen, merkvermeldingen), gevoed door het
site-profiel (kennisbank) en de top-zoekwoorden uit de Demand Engine.
Een LLM (Claude-pad) kwalificeert de kandidaten: relevantiescore 0-100,
type, motivatie én een concrete linksuggestie (welke pagina van ons, welke
ankertekst). Alleen kandidaten boven LINKBUILD_MIN_SCORE komen als
'qualified' in de funnel. Contact-e-mail via de bestaande scraper.

Hier wordt niets verstuurd — dat doet de outreach-module, achter de gate.
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List

from ...shared.config import LINKBUILD_MIN_SCORE, TAVILY_API_KEY
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome, llm_budget_exceeded
from . import service
from .service import norm_domain

logger = logging.getLogger(__name__)

# Platformen die nooit een bruikbare linkpartner zijn (social, marktplaatsen,
# zoekmachines, encyclopedieën met nofollow-beleid).
_BLOCKED_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com", "wikipedia.org",
    "google.com", "google.nl", "bing.com", "marktplaats.nl", "reddit.com",
    "medium.com", "amazon.com", "amazon.nl", "bol.com", "indeed.com",
    "werkzoeken.nl", "trustpilot.com",
}

# Signaalwoorden voor linkfarm/spam-niches — die kansen wil je niet eens zien.
_SPAM_HINTS = ("casino", "gokken", "betting", "cbd", "viagra", "essay writing",
               "linkbuilding pakket", "backlinks kopen")


class SearchUnavailable(RuntimeError):
    """De zoek-API zélf is stuk: quota op, key ongeldig of ontbrekend.

    Dit is géén 'niets gevonden'. Zonder websearch kan de prospector per
    definitie niets vinden, en dan mag de funnel niet stilletjes op 0 blijven
    staan — dat leest als 'geen kansen' terwijl het 'geen zoekmachine' is.
    """


def simplify_query(query: str) -> str:
    """Zet een operator-rijke zoekopdracht om in gewone trefwoorden.

    De zoekrecepten hieronder zijn geschreven voor Tavily, dat aanhalingstekens,
    `OR`-ketens en `-site:` begrijpt. DuckDuckGo — de keyless achtervang die het
    overneemt zodra het Tavily-abonnement op is — geeft op zo'n opdracht nul
    resultaten terug. Dat leest als "geen linkkansen gevonden" terwijl de vraag
    alleen verkeerd gesteld was (25 jul 2026: de weekrun was nog nooit gelukt).
    """
    q = re.sub(r"-\w+:\S+", " ", query or "")   # -site:example.nl
    q = q.split(" OR ")[0]                       # alleen de eerste variant
    q = q.replace('"', " ")
    return re.sub(r"\s+", " ", q).strip()


def _search_web(query: str, max_results: int = 8) -> List[Dict[str, str]]:
    """Zoekopdracht via de gedeelde websearch-laag in `backend/shared/websearch.py`
    (Tavily → Brave → DuckDuckGo → DDG-HTML). Eén zoek-flow voor het hele
    systeem — geen tweede, domein-eigen fallback die uit de pas gaat lopen.

    Levert de operator-rijke opdracht niets op, dan volgt één poging met de
    vereenvoudigde variant: een provider die de syntax niet snapt mag geen
    'geen kansen' opleveren. Raises SearchUnavailable pas als álle providers
    op béíde vormen falen; een lege lijst betekent dan écht 'niets gevonden'.
    """
    from ...shared.websearch import search as web_search, WebSearchError

    def _run(q: str) -> List[Dict[str, str]]:
        hits = web_search(q, max_results=max_results)
        return [
            {"title": h.get("title") or "", "url": h.get("url") or "",
             "snippet": (h.get("snippet") or "")[:300]}
            for h in hits
        ]

    plain = simplify_query(query)
    try:
        results = _run(query)
    except WebSearchError as e:
        if plain == query:
            raise SearchUnavailable(f"Alle zoekproviders faalden voor '{query}': {e}") from e
        logger.info("[linkbuilding] '%s' leverde niets op — opnieuw als '%s'", query, plain)
        try:
            return _run(plain)
        except WebSearchError as e2:
            raise SearchUnavailable(
                f"Alle zoekproviders faalden voor '{query}' en '{plain}': {e2}") from e2
    if not results and plain != query:
        logger.info("[linkbuilding] '%s' gaf 0 resultaten — opnieuw als '%s'", query, plain)
        try:
            return _run(plain)
        except WebSearchError:
            return []
    return results


def _url_key(url: str) -> str:
    """Vergelijkingssleutel voor URL's: schema, www, query en slash eraf.

    De kwalificatie-LLM geeft URL's zelden karakter-voor-karakter terug zoals
    ze binnenkwamen (https/http, trailing slash). Op de ruwe string koppelen
    laat prospects stil verdwijnen.
    """
    s = (url or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    s = s.split("?")[0].split("#")[0]
    return s.rstrip("/")


def _top_terms(site: Dict[str, Any], limit: int = 3) -> List[str]:
    """Niche-termen: top-zoekwoorden uit de Demand Engine, terugval op sitenaam."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT query FROM opportunities WHERE site_id = ? "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (site["id"], limit),
        ).fetchall()
    terms = [r["query"] for r in rows if (r["query"] or "").strip()]
    return terms or [site.get("name") or ""]


def _candidate_queries(site: Dict[str, Any]) -> List[str]:
    """Zoekrecepten per kanstype — bewust maximaal ~6 zoekopdrachten per site."""
    terms = _top_terms(site)
    queries: List[str] = []
    for term in terms[:2]:
        queries.append(f'{term} "gastblog" OR "schrijf voor ons" OR "gastartikel"')
        queries.append(f'{term} "handige links" OR "bronnen" OR "interessante websites"')
    name = (site.get("name") or "").strip()
    if name:
        queries.append(f'"{name}" -site:{norm_domain(site.get("base_url") or "")}')
    return queries[:6]


def _target_pages(site: Dict[str, Any], limit: int = 8) -> List[Dict[str, str]]:
    """Onze linkdoelen: best presterende pagina's + de homepage.

    Uit de GSC-dagsnapshots, niet uit `published_pages` (leeg — zie top_pages).
    Zonder dit is de homepage het enige doel en wordt élke backlink een
    homepage-link; diepe links naar pagina's die al bijna scoren zijn juist
    het waardevolst. Waar de pagina op rankt gaat mee als omschrijving: dat
    is precies wat de kwalificatie nodig heeft voor een natuurlijke ankertekst.
    """
    from ..seo.history import top_pages

    targets: List[Dict[str, str]] = []
    seen: set = set()
    for page in top_pages(site["id"], limit=limit):
        url = (page.get("page_url") or "").strip()
        key = _url_key(url)
        if not url or key in seen:
            continue
        seen.add(key)
        query = (page.get("top_query") or "").strip()
        pos = page.get("position") or 0
        desc = f"rankt op '{query}'" if query else "gepubliceerde pagina"
        if pos:
            desc += f", positie {pos:.0f}"
        targets.append({"url": url, "title": desc})

    home = (site.get("base_url") or "").strip()
    if home and _url_key(home) not in seen:
        targets.append({"url": home, "title": f"homepage — {site.get('name') or ''}"})
    return targets


def _collect_candidates(site: Dict[str, Any]) -> List[Dict[str, str]]:
    """Zoek en ontdubbel kandidaten; filter platformen, spam en bekenden weg."""
    own = norm_domain(site.get("base_url") or "")
    with get_conn() as conn:
        known = {r["domain"] for r in conn.execute(
            "SELECT domain FROM link_prospects WHERE site_id = ?", (site["id"],)
        ).fetchall()}
    seen: set = set()
    candidates: List[Dict[str, str]] = []
    for q in _candidate_queries(site):
        for hit in _search_web(q):
            dom = norm_domain(hit["url"])
            if not dom or dom in seen or dom in known or dom == own:
                continue
            if any(dom == b or dom.endswith("." + b) for b in _BLOCKED_DOMAINS):
                continue
            text = f"{hit['title']} {hit['snippet']}".lower()
            if any(h in text for h in _SPAM_HINTS):
                continue
            seen.add(dom)
            candidates.append({**hit, "domain": dom})
    return candidates


async def _qualify(site: Dict[str, Any], candidates: List[Dict[str, str]],
                   targets: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """LLM-kwalificatie: score, type, motivatie + concrete linksuggestie."""
    from ..publish.content_pipeline import _llm, _extract_json
    from ..seo.knowledge import get_site_knowledge

    profile = (get_site_knowledge(site).get("profile") or "")[:800]
    if not profile.strip():
        # Zonder profiel beoordeelt de LLM relevantie blind op naam + URL. De
        # zoekwoorden waar de site op staat zijn dan de beste omschrijving die
        # we hebben — beter een magere niche-schets dan een streepje.
        terms = [t for t in _top_terms(site, limit=6) if t]
        profile = ("Geen profiel ingevuld. De site scoort in Google op: "
                   + ", ".join(terms)) if terms else ""
        logger.warning("[linkbuilding] %s heeft geen site-profiel — kwalificatie "
                       "valt terug op zoekwoorden", site.get("name"))
    cand_lines = "\n".join(
        f"- url: {c['url']}\n  titel: {c['title']}\n  snippet: {c['snippet']}"
        for c in candidates
    )
    target_lines = "\n".join(f"- {t['url']} ({t['title']})" for t in targets)
    prompt = (
        "Je beoordeelt kandidaat-websites als linkbuilding-prospect voor deze site.\n\n"
        f"Onze site: {site.get('name')} ({site.get('base_url')})\n"
        f"Profiel: {profile or '—'}\n\n"
        f"Onze pagina's die een backlink kunnen krijgen:\n{target_lines}\n\n"
        f"Kandidaten:\n{cand_lines}\n\n"
        "Beoordeel per kandidaat:\n"
        "- score 0-100: hoe waardevol en haalbaar is een backlink hiervandaan? "
        "Thematische relevantie weegt het zwaarst; duidelijke redactie/blogsectie is een plus; "
        "directories en linkfarms scoren laag.\n"
        "- type: gastblog | resource | partner | gids | mention | overig\n"
        "- reason: één concrete zin waarom (of waarom niet)\n"
        "- target_url: de best passende pagina van ons (kies uit de lijst)\n"
        "- anchor_text: natuurlijke Nederlandse ankertekst (2-5 woorden, geen merknaam-spam)\n\n"
        "Antwoord UITSLUITEND met JSON (geen markdown):\n"
        '{"prospects": [{"url": "...", "score": 0, "type": "...", "reason": "...", '
        '"target_url": "...", "anchor_text": "..."}]}'
    )
    system = (
        "Je bent een nuchtere Nederlandse SEO-specialist die linkkansen beoordeelt. "
        "Je bent streng: liever 3 goede prospects dan 10 matige."
    )
    raw = await _llm(system, prompt, max_tokens=6000, purpose="linkbuilding")
    if not raw:
        return []
    try:
        data = json.loads(_extract_json(raw))
        return [p for p in data.get("prospects", []) if isinstance(p, dict)]
    except Exception:
        # Afgekapt antwoord (max_tokens) levert halve JSON. In plaats van de hele
        # batch weg te gooien redden we de prospect-objecten die WEL compleet zijn.
        salvaged = []
        for m in re.finditer(r"\{[^{}]*\"url\"\s*:\s*\"[^\"]+\"[^{}]*\}", raw):
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict) and obj.get("url"):
                    salvaged.append(obj)
            except Exception:
                continue
        if salvaged:
            logger.warning("[linkbuilding] JSON afgekapt — %d complete prospect(s) gered",
                           len(salvaged))
            return salvaged
        logger.warning("[linkbuilding] Onleesbare kwalificatie-JSON — batch overgeslagen")
        return []


async def _scrape_contact_email(url: str) -> str:
    """Contact-e-mail van de kandidaat-site via de bestaande scraper (homepage)."""
    from ..prospecting.scraper import ScraperService
    from .outreach import email_ok

    dom = norm_domain(url)
    if not dom:
        return ""
    try:
        result = await asyncio.to_thread(ScraperService().scrape, f"https://{dom}")
    except Exception:
        return ""
    email = (result.get("email") or "").lower()
    return email if email and email_ok(email)[0] else ""


async def run_prospecting_for_site(site: Dict[str, Any], max_new: int = 10) -> Dict[str, Any]:
    """Zoek, kwalificeer en sla linkkansen op voor één site. Verstuurt niets."""
    try:
        candidates = _collect_candidates(site)
    except SearchUnavailable as e:
        log_outcome(
            "Linkbuilding", "linkbuilding_prospectie",
            f"Linkbuilding kon niet zoeken voor {site.get('name')}: {e}",
            next_step="Zowel Tavily als de DuckDuckGo-terugval faalden — zonder "
                      "websearch vindt de agent geen linkkansen. Check je "
                      "Tavily-abonnement/quota op tavily.com of de TAVILY_API_KEY in "
                      ".env; is DuckDuckGo geblokkeerd (rate-limit/netwerk), probeer later.",
            status="error",
        )
        logger.error("[linkbuilding] Websearch onbruikbaar voor %s: %s", site.get("name"), e)
        return {"site": site["id"], "found": 0, "qualified": 0, "error": str(e)}
    if not candidates:
        return {"site": site["id"], "found": 0, "qualified": 0}
    targets = _target_pages(site)
    judged = await _qualify(site, candidates[:20], targets)
    by_url = {_url_key(c["url"]): c for c in candidates}
    by_domain = {c["domain"]: c for c in candidates}

    qualified = 0
    for p in sorted(judged, key=lambda x: -int(x.get("score") or 0)):
        if qualified >= max_new:
            break
        url = p.get("url") or ""
        cand = by_url.get(_url_key(url)) or by_domain.get(norm_domain(url))
        score = int(p.get("score") or 0)
        if not cand:
            logger.warning("[linkbuilding] Kwalificatie noemt onbekende URL %r "
                           "— overgeslagen", url)
            continue
        if score < LINKBUILD_MIN_SCORE:
            continue
        email = await _scrape_contact_email(cand["url"])
        row = service.create_prospect(site["id"], {
            "url": cand["url"],
            "page_title": cand["title"],
            "prospect_type": p.get("type") or "overig",
            "relevance_score": score,
            "rationale": p.get("reason") or "",
            "contact_email": email,
            "target_url": p.get("target_url") or (site.get("base_url") or ""),
            "anchor_text": p.get("anchor_text") or "",
            "status": "qualified",
        })
        if row:
            qualified += 1
    # Bewijs dat de zoeklaag voor dit project wél werkt — een geslaagde run
    # (candidates gevonden) is precies wat de resolver nodig heeft om de
    # 'search failed'-kaarten van de Tavily/DDG-uitval van 24-25 jul alsnog
    # te laten verdwijnen. Zonder deze ok-rij blijven die kaarten eeuwig
    # staan terwijl zoeken allang weer draait.
    log_outcome(
        "Linkbuilding", "linkbuilding_prospectie",
        f"Prospectie voor {site.get('name')}: {len(candidates)} kandidaat(en), "
        f"{qualified} gekwalificeerd.",
        status="ok",
    )
    return {"site": site["id"], "found": len(candidates), "qualified": qualified}


async def run_weekly_linkbuilding() -> None:
    """Scheduler entry-point (wekelijks): prospectie + outreach-concepten per site.

    Alles landt in de funnel resp. het Actiecentrum — er vertrekt geen mail."""
    from ..seo.sites import list_sites
    from .outreach import prepare_linkbuilding_batch

    if llm_budget_exceeded():
        logger.info("[linkbuilding] LLM-budget/quota op — weekrun overgeslagen")
        return
    try:
        total_q, ran, failed = 0, 0, 0
        for site_summary in list_sites():
            from ..seo.sites import get_site
            site = get_site(site_summary["id"])
            if not site or not (site.get("base_url") or "").strip():
                continue
            report = await run_prospecting_for_site(site)
            total_q += report["qualified"]
            ran += 1
            if report.get("error"):
                failed += 1
                continue
            logger.info("[linkbuilding] %s: %d kandidaten, %d gekwalificeerd",
                        site["name"], report["found"], report["qualified"])
        # Viel de websearch voor élke site om, dan is de weekrun niet "klaar met
        # 0 kansen" maar simpelweg mislukt. run_prospecting_for_site logde de
        # oorzaak al per site; hier telt alleen dat er niets gezocht is.
        if ran and failed == ran:
            logger.error("[linkbuilding] Weekrun afgebroken: websearch onbruikbaar "
                         "voor alle %d sites", ran)
            return
        batch = await prepare_linkbuilding_batch()
        log_outcome(
            "Linkbuilding", "linkbuilding_weekrun",
            f"Weekrun klaar: {total_q} nieuwe linkkans(en) gekwalificeerd, "
            f"{batch['drafted']} outreach-concept(en) klaargezet ter review"
            + (f" ({failed} site(s) zonder werkende websearch)" if failed else ""),
            artifact="/api/linkbuilding/funnel",
            next_step=(
                f"Keur de {batch['drafted']} concepten goed of wijs ze af in het "
                "Actiecentrum — pas na jouw klik wordt er verstuurd."
                if batch["drafted"] else
                "Geen concepten deze week — check of prospects een contact-e-mail hebben "
                "(GET /api/linkbuilding/prospects?status=qualified)."
            ),
            status="error" if failed else "ok",
        )
        # Goldie-modus: als LINKBUILD_AUTO_APPROVE aan staat, verstuurt de batch
        # de goedgekeurde concepten meteen (zelfde checks als de handmatige knop).
        if batch["drafted"]:
            from .outreach import auto_approve_review_queue
            auto = await auto_approve_review_queue()
            if auto.get("sent"):
                log_outcome(
                    "Linkbuilding", "linkbuilding_auto_approve",
                    f"Auto-approve: {auto['sent']} link-outreach verstuurd "
                    f"({auto.get('skipped', 0)} overgeslagen).",
                    artifact="/api/linkbuilding/funnel",
                    next_step="Monitor checkt dagelijks of de links live komen.",
                    status="ok",
                )
    except Exception as e:
        logger.exception("Linkbuilding-weekrun gefaald")
        log_outcome(
            "Linkbuilding", "linkbuilding_weekrun",
            f"Linkbuilding-weekrun gefaald: {e}",
            next_step="Bekijk logs/impactos.log en draai handmatig: POST /api/linkbuilding/prospect-run.",
            status="error",
        )

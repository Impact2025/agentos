"""
Facebook-content uit echte data — geen willekeurige posts.

Deze module voedt de Facebook-content voor een project met de databronnen die
ImpactOS al analyseert, in plaats van een thema uit de vault te gokken:

  1. GSC-topqueries      — zoekwoorden die al verkeer trekken op de site → schrijf
                          een FB-post die diezelfde vraag beantwoordt (hergebruik
                          van bewezen interesse, niet giswerk).
  2. Demand-kansen        — opportuniteiten op positie 4-15 met volume (laaghangend
                          fruit uit de Demand Engine): een FB-post over dat onderwerp
                          versterkt hetzelfde zoekwoord dat de site aan het klimmen is.
  3. FB-post-engagement   — uit de opgeslagen snapshot (fb_insights): welke eerdere
                          posts scoorden → soortgelijk thema/hoek herhalen.

De brug naar SEO: elke GSC-topquery / Demand-kans wordt gekoppeld aan het live
artikel op de site (content_jobs.slug + sites.base_url, of opportunities.live_url).
De FB-post linkt naar dat artikel — zo wordt FB-verkeer een hefboom voor de
GSC-positie van precies dat zoekwoord. FB en SEO trekken elkaar omhoog in plaats
van losse silo's te blijven.

Output: een lijst van gerichte post-ideeën (hoek + werktitel + bron + bewijs),
en op verzoek een geschreven post-tekst via de Hermes/DeepSeek-backend.

Als GSC niet is geconfigureerd voor de site, valt de bron weg (geen fake-data):
de module rapporteert expliciet welke bronnen ontbraken. Net als de rest van
ImpactOS: stilte is de duurste leugen, dus "geen GSC" is een statusregel, geen
lege post.
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional

from ...shared.database import get_conn
from ..seo import sites as sites_service
from ..seo import gsc as gsc_service
from ..seo import engine as demand_engine
from .facebook_store import get_snapshot

logger = logging.getLogger(__name__)


def _site_row(site_name: str) -> Optional[Dict]:
    for s in sites_service.list_sites():
        if (s.get("name", "").lower().replace(" ", "").replace("-", "").replace("_", "")) == \
           (site_name or "").lower().replace(" ", "").replace("-", "").replace("_", ""):
            return sites_service.get_site(s["id"]) or s
    return None


def resolve_query_to_url(site_name: str, query: str) -> Optional[str]:
    """Koppel een zoekwoord aan het live artikel op de site (de FB→SEO-brug).

    Kanonieke bron: een gepubliceerd content_job met keyword == query
    (URL = {base_url}/blog/{slug}). Valt terug op opportunities.live_url als die
    gevuld is. Geen match → None (geen fake-link, de post wordt dan een
    algemene vraag-post zonder diepe link).
    """
    site = _site_row(site_name)
    if not site:
        return None
    site_id = site.get("id")
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        return None
    with get_conn() as conn:
        # 1. Gepubliceerd artikel via content_jobs (exacte keyword-match).
        row = conn.execute(
            "SELECT slug FROM content_jobs WHERE site_id = ? AND status = 'published' "
            "AND LOWER(keyword) = LOWER(?) LIMIT 1",
            (site_id, query),
        ).fetchone()
        if row and row["slug"]:
            return f"{base}/blog/{row['slug']}"
        # 2. Opportunities met een live_url.
        orow = conn.execute(
            "SELECT live_url FROM opportunities WHERE site_id = ? AND LOWER(query) = LOWER(?) "
            "AND live_url IS NOT NULL AND live_url != '' LIMIT 1",
            (site_id, query),
        ).fetchone()
        if orow and orow["live_url"]:
            return orow["live_url"]
    return None


def gather_signals(site_name: str, days: int = 28) -> Dict[str, Any]:
    """Verzamel de echte databronnen voor FB-content. Faalveilig per bron."""
    site = _site_row(site_name)
    if not site:
        return {"error": f"Site '{site_name}' niet gevonden"}

    gsc_property = (site.get("gsc_property") or "").strip()
    signals: Dict[str, Any] = {
        "site_name": site_name,
        "gsc_configured": bool(gsc_property) and gsc_service.is_configured(),
        "top_queries": [],
        "demand_kansen": [],
        "fb_top_posts": [],
        "sources_used": [],
        "sources_missing": [],
    }

    # 1. GSC-topqueries (verkeer dat er al is)
    if gsc_property and gsc_service.is_configured():
        try:
            rows = gsc_service.fetch_query_performance(gsc_property, days=days)
            # Sorteer op impressies (bereik) en neem de top die ook enige positie hebben.
            rows.sort(key=lambda r: r.get("impressions", 0), reverse=True)
            signals["top_queries"] = [
                {"query": r["query"], "clicks": r.get("clicks", 0),
                 "impressions": r.get("impressions", 0), "position": r.get("position", 0)}
                for r in rows[:15]
            ]
            signals["sources_used"].append("gsc_top_queries")
        except Exception as e:
            logger.warning("[fb_content] GSC mislukt voor %s: %s", site_name, e)
            signals["sources_missing"].append("gsc_top_queries")
    else:
        signals["sources_missing"].append("gsc_top_queries")

    # 2. Demand-kansen (positie 4-15 met volume)
    try:
        opps = demand_engine.list_opportunities(site.get("id"))
        # 'open' kansen met een beetje score en volume
        kansen = [o for o in opps if o.get("status") in ("new", "in_progress")]
        kansen.sort(key=lambda o: (o.get("impressions", 0) or 0), reverse=True)
        signals["demand_kansen"] = [
            {"query": o.get("query"), "impressions": o.get("impressions", 0),
             "position": o.get("position", 0), "score": o.get("opportunity_score", 0)}
            for o in kansen[:10]
        ]
        if signals["demand_kansen"]:
            signals["sources_used"].append("demand_kansen")
    except Exception as e:
        logger.warning("[fb_content] Demand-kansen mislukt voor %s: %s", site_name, e)
        signals["sources_missing"].append("demand_kansen")

    # 3. FB-post-engagement uit snapshot
    snap = get_snapshot(site_name)
    if snap and snap.get("status") == "ok" and snap.get("snapshot"):
        tops = snap["snapshot"].get("top_posts", [])
        signals["fb_top_posts"] = [
            {"message": (p.get("message") or "")[:120], "engagement": p.get("engagement", 0)}
            for p in tops[:5]
        ]
        if signals["fb_top_posts"]:
            signals["sources_used"].append("fb_engagement")
    else:
        signals["sources_missing"].append("fb_engagement")

    return signals


def build_ideas(signals: Dict[str, Any], limit: int = 5,
                site_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Vertaal signalen naar gerichte post-ideeën (zonder LLM — puur data-gedreven).

    Elk idee met een `query` wordt gekoppeld aan het live artikel op de site
    (FB→SEO-brug): de post linkt naar dat artikel, zodat FB-verkeer de GSC-positie
    van precies dat zoekwoord optrekt.
    """
    ideas: List[Dict[str, Any]] = []

    # Idee uit GSC-topquery: beantwoord de vraag die mensen al stellen.
    for q in signals.get("top_queries", [])[:5]:
        idea = {
            "hoek": "beantwoord_zoekvraag",
            "werktitel": f"\"{q['query']}\" — wat je écht wil weten",
            "bron": "gsc_top_queries",
            "bewijs": f"{q['impressions']} impressies / {q['clicks']} klikken op de site (pos {q['position']})",
            "query": q["query"],
            "url": None,
        }
        if site_name:
            idea["url"] = resolve_query_to_url(site_name, q["query"])
        ideas.append(idea)

    # Idee uit Demand-kans: versterk het zoekwoord dat de site aan het klimmen is.
    for k in signals.get("demand_kansen", [])[:5]:
        idea = {
            "hoek": "versterk_kans",
            "werktitel": f"Waarom '{k['query']}' steeds vaker wordt gezocht",
            "bron": "demand_kansen",
            "bewijs": f"Demand-kans, pos {k['position']}, {k['impressions']} impressies",
            "query": k["query"],
            "url": None,
        }
        if site_name:
            idea["url"] = resolve_query_to_url(site_name, k["query"])
        ideas.append(idea)

    # Idee uit FB-engagement: herhaal wat eerder scoorde.
    for p in signals.get("fb_top_posts", [])[:3]:
        ideas.append({
            "hoek": "herhaal_winnend",
            "werktitel": f"Soortgelijk als: {p['message'][:60]}",
            "bron": "fb_engagement",
            "bewijs": f"Eerdere post: {p['engagement']} interacties",
            "query": None,
            "url": None,
        })

    if not ideas:
        return [{
            "hoek": "geen_data",
            "werktitel": f"Deel een verhaal van {signals.get('site_name')}",
            "bron": None,
            "bewijs": "Geen GSC/Demand/FB-data beschikbaar — gebruik vault-context",
            "query": None,
            "url": None,
        }]

    return ideas[:limit]


async def generate_post_text(idea: Dict[str, Any], site_name: str) -> Dict[str, Any]:
    """Schrijf een FB-post-tekst via de Hermes/DeepSeek-backend, gebaseerd op de
    echte data in `idea` (geen verzonnen cijfers — de backend krijgt het bewijs mee)."""
    from ...shared.hermes_context import build_hermes_context
    try:
        from ..chat import hermes as hermes_service
    except Exception:
        hermes_service = None

    if not hermes_service:
        return {"success": False, "error": "Hermes-backend niet beschikbaar"}

    prompt = (
        f"Schrijf een Nederlandse Facebook-post voor het project '{site_name}'.\n"
        f"Hoek: {idea.get('hoek')}. Werktitel: {idea.get('werktitel')}.\n"
        f"Echte data als basis: {idea.get('bewijs')}.\n"
    )
    if idea.get("url"):
        prompt += (
            f"Link in de post naar dit artikel op de site: {idea['url']} "
            f"(het zoekwoord '{idea.get('query')}' scoort daar al op, FB-verkeer helpt de "
            f"Google-positie). Eindig de post met een zachte CTA: 'Lees meer op de site'.\n"
        )
    else:
        prompt += "Geen artikel-link beschikbaar — schrijf een losse vraag-post.\n"
    prompt += (
        f"Richtlijnen: warm en menselijk, max 280 tekens, geen hashtag-spam, "
        f"eindig met een vraag om interactie uit te lokken. Geen verzonnen cijfers "
        f"of bronnen. alleen de post-tekst, geen uitleg."
    )
    try:
        text = await hermes_service.ask(prompt, model="deepseek-v4-flash")
        return {"success": True, "text": text.strip(), "idea": idea}
    except Exception as e:
        return {"success": False, "error": str(e)[:200], "idea": idea}


async def suggest_facebook_content(site_name: str, days: int = 28,
                                    limit: int = 5, write: bool = False) -> Dict[str, Any]:
    """Volledige suggestie-loop: signalen → ideeën → (optioneel) geschreven posts."""
    signals = gather_signals(site_name, days=days)
    if signals.get("error"):
        return {"success": False, "error": signals["error"]}
    ideas = build_ideas(signals, limit=limit, site_name=site_name)
    out = {
        "success": True,
        "site_name": site_name,
        "sources_used": signals["sources_used"],
        "sources_missing": signals["sources_missing"],
        "ideas": ideas,
        "posts": [],
    }
    if write:
        posts = await asyncio.gather(*[generate_post_text(i, site_name) for i in ideas])
        out["posts"] = [p for p in posts]
    return out

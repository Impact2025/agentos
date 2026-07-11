"""
SEO Optimizer — verzilvert rankings die de site al hééft, voor alle projecten.

Drie gereedschappen, allemaal gevoed door echte data (GSC + de live site):

1. Interne Linkbuilder  — vindt ontbrekende contextuele links tussen eigen
   pagina's (puur algoritmisch: geen LLM, geen kosten).
2. CTR-optimizer        — vergelijkt de werkelijke CTR per pagina met de
   verwachte CTR voor die positie; grote afwijkers krijgen op verzoek
   title/meta-varianten (LLM).
3. Content-refresh      — detecteert pagina's die klikken/positie verliezen
   (decay) en kan ze via de agent laten verrijken → als review-job in de
   Wachtrij (nooit direct live).

Suggesties landen in de `seo_suggestions`-tabel; verworpen suggesties komen
bij een herscan niet terug (stabiele id-vingerafdruk + INSERT OR IGNORE).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...shared.database import get_conn
from ...shared import agent_runner
from ...shared.config import TAVILY_API_KEY
from . import gsc, sites as sites_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seo-optimizer", tags=["seo-optimizer"])

# ── Verwachte CTR per positie (organisch, samengevoegde industrie-benchmarks) ──
_EXPECTED_CTR = {
    1: 28.0, 2: 15.5, 3: 10.5, 4: 7.5, 5: 5.5, 6: 4.5, 7: 3.8,
    8: 3.2, 9: 2.8, 10: 2.5, 11: 1.8, 12: 1.6, 13: 1.4, 14: 1.3, 15: 1.2,
}

_MAX_PAGES_TO_FETCH = 40
_MIN_IMPRESSIONS_CTR = 20
_STOPWORDS = {
    "de", "het", "een", "en", "of", "in", "op", "voor", "van", "met", "bij",
    "naar", "je", "jouw", "uw", "wat", "hoe", "waarom", "is", "zijn", "the",
    "and", "for", "with", "your",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_site(name_or_id: str) -> Optional[Dict]:
    """Vind een site op id, sitenaam of projectnaam (genormaliseerd)."""
    norm = lambda x: (x or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    target = norm(name_or_id)
    for s in sites_service.list_sites():
        if s["id"] == name_or_id or norm(s["name"]) == target:
            return sites_service.get_site(s["id"]) or s
    return None


# ── Suggestie-opslag ─────────────────────────────────────────────────────────

def _fingerprint(site_id: str, stype: str, page: str, title: str) -> str:
    raw = f"{site_id}|{stype}|{page}|{title}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _store_suggestions(site_id: str, stype: str, suggestions: List[Dict]) -> int:
    """Vervang openstaande suggesties van dit type; verworpen ('dismissed')
    suggesties behouden hun rij en blokkeren her-insertie (zelfde id)."""
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM seo_suggestions WHERE site_id = ? AND type = ? AND status = 'new'",
            (site_id, stype),
        )
        inserted = 0
        for s in suggestions:
            sid = _fingerprint(site_id, stype, s.get("page", ""), s.get("title", ""))
            cur = conn.execute(
                """INSERT OR IGNORE INTO seo_suggestions
                   (id, site_id, type, page, query, title, data, score, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
                (sid, site_id, stype, s.get("page", ""), s.get("query", ""),
                 s.get("title", ""), json.dumps(s.get("data", {}), ensure_ascii=False),
                 float(s.get("score", 0)), now, now),
            )
            inserted += cur.rowcount
    return inserted


def list_suggestions(site_id: str, stype: Optional[str] = None,
                     status: Optional[str] = None) -> List[Dict]:
    clauses, params = ["site_id = ?"], [site_id]
    if stype:
        clauses.append("type = ?")
        params.append(stype)
    if status:
        clauses.append("status = ?")
        params.append(status)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM seo_suggestions WHERE {' AND '.join(clauses)} "
            "ORDER BY score DESC, created_at DESC LIMIT 200",
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["data"] = json.loads(d.get("data") or "{}")
        except json.JSONDecodeError:
            d["data"] = {}
        out.append(d)
    return out


def _get_suggestion(sid: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM seo_suggestions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["data"] = json.loads(d.get("data") or "{}")
    except json.JSONDecodeError:
        d["data"] = {}
    return d


def _update_suggestion(sid: str, **fields: Any) -> None:
    if "data" in fields and not isinstance(fields["data"], str):
        fields["data"] = json.dumps(fields["data"], ensure_ascii=False)
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE seo_suggestions SET {sets} WHERE id = ?", [*fields.values(), sid])


# ── Pagina's ophalen van de live site ────────────────────────────────────────

async def _fetch_pages(urls: List[str]) -> Dict[str, Dict]:
    """Haal pagina's parallel op en extraheer titel/meta/tekst/interne links."""
    import httpx
    from bs4 import BeautifulSoup

    sem = asyncio.Semaphore(8)
    results: Dict[str, Dict] = {}

    async def _one(client: httpx.AsyncClient, url: str) -> None:
        async with sem:
            try:
                resp = await client.get(url, timeout=12, follow_redirects=True)
                if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                    return
                soup = BeautifulSoup(resp.text, "html.parser")
                # Titel/meta eerst pakken, daarna <head> weggooien zodat
                # title-tekst niet als "lopende tekst" meetelt voor ankers.
                title = (soup.title.get_text(strip=True) if soup.title else "")
                meta = ""
                meta_tag = soup.find("meta", attrs={"name": "description"})
                if meta_tag:
                    meta = meta_tag.get("content", "")
                for tag in soup(["head", "script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                own_host = urlparse(url).netloc
                links = set()
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    parsed = urlparse(href)
                    if parsed.netloc and parsed.netloc != own_host:
                        continue
                    path = parsed.path.rstrip("/")
                    if path:
                        links.add(path)
                text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
                results[url] = {
                    "title": title, "meta": meta,
                    "text": text[:20000], "text_lower": text[:20000].lower(),
                    "links": links,
                }
            except Exception as e:
                logger.debug(f"[optimizer] Pagina ophalen mislukt {url}: {e}")

    async with httpx.AsyncClient(headers={"User-Agent": "AgentOS-SEO-Optimizer/1.0"}) as client:
        await asyncio.gather(*(_one(client, u) for u in urls))
    return results


def _top_query_per_page(page_queries: List[Dict]) -> Dict[str, Dict]:
    """Belangrijkste zoekwoord per pagina (hoogste impressies)."""
    best: Dict[str, Dict] = {}
    for pq in page_queries:
        cur = best.get(pq["page"])
        if not cur or pq["impressions"] > cur["impressions"]:
            best[pq["page"]] = pq
    return best


# ── Analyzer 1: Interne Linkbuilder ──────────────────────────────────────────

def _significant_tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-zà-ü0-9]+", (text or "").lower()) if len(t) > 2 and t not in _STOPWORDS]


def _canonical_key(url: str) -> str:
    """Zelfde pagina onder www/niet-www of met/zonder slash → zelfde sleutel.
    GSC rapporteert die varianten als aparte URL's; voor linkadvies zijn ze één."""
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}"


def _analyze_internal_links(pages: Dict[str, Dict], top_queries: Dict[str, Dict],
                            gsc_pages: List[Dict]) -> List[Dict]:
    imp_by_page = {p["page"]: p["impressions"] for p in gsc_pages}
    # Merknaam (host zonder tld) is geen bruikbaar anker — die matcht overal.
    brand_tokens = set()
    for url in pages:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        brand_tokens.add(host.split(".")[0])

    suggestions: List[Dict] = []
    seen_pairs: set = set()

    for target_url, target in pages.items():
        target_path = urlparse(target_url).path.rstrip("/")
        if not target_path:
            continue  # homepage hoeft geen extra interne links
        # Ankerkandidaat: het top-zoekwoord van de doelpagina, anders slug-tokens
        tq = top_queries.get(target_url, {})
        anchor = (tq.get("query") or "").strip()
        if not anchor:
            slug_tokens = _significant_tokens(target_path.rsplit("/", 1)[-1].replace("-", " "))
            if len(slug_tokens) < 2:
                continue
            anchor = " ".join(slug_tokens)
        if len(anchor) < 6:
            continue
        if anchor.lower().replace(" ", "") in brand_tokens:
            continue  # merknaam-anker: matcht overal, zegt niets

        target_key = _canonical_key(target_url)
        for source_url, source in pages.items():
            source_key = _canonical_key(source_url)
            if source_key == target_key:
                continue  # zelfde pagina (evt. www/niet-www variant)
            pair = (source_key, target_key)
            if pair in seen_pairs:
                continue
            if target_path in source["links"]:
                continue  # link bestaat al
            idx = source["text_lower"].find(anchor.lower())
            if idx < 0:
                continue
            seen_pairs.add(pair)
            snippet = source["text"][max(0, idx - 60): idx + len(anchor) + 60].strip()
            suggestions.append({
                "page": target_url,
                "query": anchor,
                "title": f"Link \"{anchor}\" van {urlparse(source_url).path or '/'} → {target_path}",
                "score": float(imp_by_page.get(target_url, 0)) + 10,
                "data": {
                    "from": source_url,
                    "to": target_url,
                    "anchor": anchor,
                    "context": snippet,
                    "target_impressions": imp_by_page.get(target_url, 0),
                },
            })

    suggestions.sort(key=lambda s: -s["score"])
    return suggestions[:30]


# ── Analyzer 2: CTR-audit ────────────────────────────────────────────────────

def _expected_ctr(position: float) -> float:
    p = max(1, int(round(position)))
    if p in _EXPECTED_CTR:
        return _EXPECTED_CTR[p]
    if p <= 20:
        return 1.0
    return 0.6


def _analyze_ctr(gsc_pages: List[Dict], top_queries: Dict[str, Dict]) -> List[Dict]:
    suggestions: List[Dict] = []
    for p in gsc_pages:
        if p["impressions"] < _MIN_IMPRESSIONS_CTR or p["position"] > 20:
            continue
        expected = _expected_ctr(p["position"])
        if p["ctr"] >= expected * 0.7:
            continue  # presteert redelijk conform benchmark
        missed = round(p["impressions"] * (expected - p["ctr"]) / 100, 1)
        if missed < 2:
            continue
        tq = top_queries.get(p["page"], {})
        suggestions.append({
            "page": p["page"],
            "query": tq.get("query", ""),
            "title": f"CTR {p['ctr']}% op positie {p['position']} — benchmark ~{expected}%",
            "score": missed,
            "data": {
                "position": p["position"], "ctr": p["ctr"], "expected_ctr": expected,
                "impressions": p["impressions"], "clicks": p["clicks"],
                "missed_clicks_per_period": missed,
            },
        })
    suggestions.sort(key=lambda s: -s["score"])
    return suggestions[:20]


# ── Analyzer 3: Content-decay ────────────────────────────────────────────────

def _analyze_decay(cur_pages: List[Dict], prev_pages: List[Dict],
                   top_queries: Dict[str, Dict]) -> List[Dict]:
    prev_by = {p["page"]: p for p in prev_pages}
    suggestions: List[Dict] = []
    for cur in cur_pages:
        prev = prev_by.get(cur["page"])
        if not prev:
            continue
        clicks_lost = prev["clicks"] - cur["clicks"]
        pos_drop = round(cur["position"] - prev["position"], 1)  # positief = gezakt
        click_decay = prev["clicks"] >= 5 and clicks_lost >= max(2, prev["clicks"] * 0.3)
        pos_decay = pos_drop >= 3 and cur["impressions"] >= 30
        if not click_decay and not pos_decay:
            continue
        tq = top_queries.get(cur["page"], {})
        reason = []
        if click_decay:
            reason.append(f"{clicks_lost} klikken verloren ({prev['clicks']}→{cur['clicks']})")
        if pos_decay:
            reason.append(f"{pos_drop} posities gezakt ({prev['position']}→{cur['position']})")
        suggestions.append({
            "page": cur["page"],
            "query": tq.get("query", ""),
            "title": " · ".join(reason),
            "score": max(clicks_lost, 0) * 2 + max(pos_drop, 0),
            "data": {
                "clicks_current": cur["clicks"], "clicks_prev": prev["clicks"],
                "position_current": cur["position"], "position_prev": prev["position"],
                "impressions": cur["impressions"],
            },
        })
    suggestions.sort(key=lambda s: -s["score"])
    return suggestions[:15]


# ── Scan-orchestratie ────────────────────────────────────────────────────────

async def run_scan(site: Dict, types: Optional[List[str]] = None) -> Dict[str, Any]:
    """Draai de gevraagde analyzers voor een site en sla suggesties op.
    Volledig LLM-vrij — kost alleen GSC-calls en pagina-fetches."""
    types = types or ["internal_link", "ctr", "refresh"]
    gsc_prop = (site.get("gsc_property") or "").strip()
    if not gsc_prop or not gsc.is_configured():
        raise ValueError("Geen GSC-koppeling voor deze site — de Optimizer heeft Search Console-data nodig.")

    try:
        cur_pages = await asyncio.to_thread(lambda: gsc.fetch_page_performance(gsc_prop, days=28, row_limit=500))
        page_queries = await asyncio.to_thread(lambda: gsc.fetch_page_query_performance(gsc_prop, days=28))
    except Exception as e:
        msg = str(e)
        if "permission" in msg.lower() or "403" in msg:
            raise ValueError(
                f"Het service-account heeft geen toegang tot '{gsc_prop}'. "
                "Voeg het GSC-service-account (zie google-credentials.json → client_email) toe als "
                "gebruiker met 'Volledig'-rechten in Search Console → Instellingen → Gebruikers."
            )
        raise ValueError(f"GSC-data ophalen mislukt: {msg[:200]}")
    top_queries = _top_query_per_page(page_queries)

    summary: Dict[str, Any] = {"site": site["name"], "counts": {}}

    if "internal_link" in types:
        urls = [p["page"] for p in sorted(cur_pages, key=lambda x: -x["impressions"])[:_MAX_PAGES_TO_FETCH]]
        pages = await _fetch_pages(urls)
        links = _analyze_internal_links(pages, top_queries, cur_pages)
        summary["counts"]["internal_link"] = _store_suggestions(site["id"], "internal_link", links)
        summary["pages_fetched"] = len(pages)

    if "ctr" in types:
        ctr = _analyze_ctr(cur_pages, top_queries)
        summary["counts"]["ctr"] = _store_suggestions(site["id"], "ctr", ctr)

    if "refresh" in types:
        prev_pages = await asyncio.to_thread(
            lambda: gsc.fetch_page_performance(gsc_prop, days=28, row_limit=500, end_offset=28)
        )
        decay = _analyze_decay(cur_pages, prev_pages, top_queries)
        summary["counts"]["refresh"] = _store_suggestions(site["id"], "refresh", decay)

    logger.info(f"[optimizer] Scan {site['name']}: {summary['counts']}")
    return summary


async def run_weekly_optimizer_job() -> None:
    """Scheduler: wekelijkse scan voor alle sites met een GSC-koppeling.
    LLM-vrij, dus gratis — vult alleen de Optimalisatie-tab met verse kansen."""
    for s in sites_service.list_sites():
        site = sites_service.get_site(s["id"]) or s
        if not (site.get("gsc_property") or "").strip():
            continue
        try:
            await run_scan(site)
        except Exception as e:
            logger.warning(f"[optimizer] Wekelijkse scan mislukt voor {site.get('name')}: {e}")


# ── LLM-helper ───────────────────────────────────────────────────────────────

async def _llm(system: str, prompt: str, max_tokens: int = 2000) -> str:
    full = ""
    async for chunk in agent_runner.run_agent(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system,
        agent="hermes",
        use_tools=False,
        purpose="seo-optimizer",
    ):
        if chunk.get("type") == "text":
            full += chunk["text"]
        elif chunk.get("type") == "error":
            raise RuntimeError(chunk.get("message", "LLM-fout"))
    return full.strip()


def _extract_json_block(raw: str) -> str:
    m = re.search(r"\{.*\}|\[.*\]", raw, re.DOTALL)
    return m.group(0) if m else raw


# ── Actie: CTR title/meta-varianten (LLM, op verzoek) ────────────────────────

async def generate_ctr_variants(sug: Dict, site: Dict) -> List[Dict]:
    pages = await _fetch_pages([sug["page"]])
    page = pages.get(sug["page"], {})
    current_title = page.get("title", "")
    current_meta = page.get("meta", "")

    system = (
        "Je bent een Nederlandse SEO-copywriter gespecialiseerd in title tags en meta descriptions "
        "die de CTR verhogen zonder clickbait. Regels: title ≤ 60 tekens, meta ≤ 155 tekens, "
        "zoekwoord vooraan in de title, concreet voordeel of cijfer benoemen, geen overdrijving. "
        "Antwoord UITSLUITEND met JSON: [{\"title\": \"...\", \"meta\": \"...\", \"waarom\": \"...\"}] — exact 3 varianten."
    )
    prompt = (
        f"Site: {site['name']} ({site.get('base_url', '')})\n"
        f"Pagina: {sug['page']}\n"
        f"Belangrijkste zoekwoord: {sug.get('query') or '(onbekend — leid af uit de URL)'}\n"
        f"Positie: {sug['data'].get('position')} · CTR nu: {sug['data'].get('ctr')}% · "
        f"benchmark: {sug['data'].get('expected_ctr')}%\n"
        f"Huidige title: {current_title or '(niet gevonden)'}\n"
        f"Huidige meta description: {current_meta or '(ontbreekt!)'}\n\n"
        "Schrijf 3 sterkere title+meta-combinaties."
    )
    raw = await _llm(system, prompt, max_tokens=1200)
    try:
        variants = json.loads(_extract_json_block(raw))
        assert isinstance(variants, list) and variants
    except Exception:
        raise RuntimeError(f"Kon geen geldige varianten parsen uit LLM-output: {raw[:200]}")

    data = dict(sug["data"])
    data["current_title"] = current_title
    data["current_meta"] = current_meta
    data["variants"] = variants[:3]
    _update_suggestion(sug["id"], data=data)
    return variants[:3]


# ── Actie: Content-refresh → Wachtrij (LLM + Tavily, op verzoek) ─────────────

async def refresh_article(sug: Dict, site: Dict) -> str:
    """Verrijk een wegzakkend artikel met echte SERP-inzichten en zet het als
    review-job in de Wachtrij. Retourneert het job-id."""
    from ..publish import content_pipeline

    pages = await _fetch_pages([sug["page"]])
    page = pages.get(sug["page"])
    if not page or len(page.get("text", "")) < 300:
        raise RuntimeError("Kon de huidige pagina-inhoud niet ophalen — refresh niet mogelijk.")

    keyword = sug.get("query") or urlparse(sug["page"]).path.rsplit("/", 1)[-1].replace("-", " ")

    # Echte SERP-context: wat behandelen concurrenten dat dit artikel mist?
    serp_context = ""
    if TAVILY_API_KEY:
        try:
            def _search():
                from tavily import TavilyClient
                return TavilyClient(api_key=TAVILY_API_KEY).search(
                    query=keyword, max_results=6, search_depth="advanced")
            resp = await asyncio.to_thread(_search)
            hits = [
                f"- {r.get('title', '')}: {(r.get('content') or '')[:250]}"
                for r in resp.get("results", [])
                if urlparse(r.get("url", "")).netloc != urlparse(sug["page"]).netloc
            ]
            if hits:
                serp_context = "## Wat de huidige top-resultaten behandelen\n" + "\n".join(hits[:5])
        except Exception as e:
            logger.debug(f"[optimizer] SERP-context mislukt: {e}")

    system = (
        "Je bent een senior Nederlandse SEO-editor. Je verrijkt een bestaand artikel dat "
        "posities verliest: behoud wat goed is, verbeter structuur en volledigheid, vul aan "
        "wat concurrenten wél behandelen, voeg waar logisch een FAQ-sectie toe. "
        "Verzin GEEN cijfers of feiten die niet in de bronnen staan. "
        "Lever ALLEEN de volledige verbeterde HTML-body (h1/h2/h3/p/ul/li), geen <html>/<head>."
    )
    prompt = (
        f"Zoekwoord: {keyword}\n"
        f"Prestatie: {sug['title']}\n\n"
        f"## Huidig artikel (platte tekst)\n{page['text'][:8000]}\n\n"
        f"{serp_context}\n\n"
        "Herschrijf en verrijk dit artikel."
    )
    html_body = await _llm(system, prompt, max_tokens=4000)
    if len(html_body) < 500:
        raise RuntimeError("Refresh-output te kort — niet in de Wachtrij gezet.")

    title = page.get("title") or keyword
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_body, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip() or title

    seo_score = 0
    try:
        review = await content_pipeline._review_article(site, keyword, html_body)
        seo_score = int(review.get("score", 0))
    except Exception:
        pass

    job_id = content_pipeline.create_job(
        site_id=site["id"], title=title, keyword=keyword,
        rationale=f"Content-refresh van {sug['page']} — {sug['title']}",
        blog_html=html_body, seo_score=seo_score, social_copy={},
        image_bytes=None, slug=content_pipeline.slugify_title(title),
    )
    _update_suggestion(sug["id"], status="done")
    return job_id


# ── API ──────────────────────────────────────────────────────────────────────

class StatusPatch(BaseModel):
    status: str


@router.post("/{project}/scan")
async def api_scan(project: str):
    site = resolve_site(project)
    if not site:
        raise HTTPException(404, f"Site/project '{project}' niet gevonden")
    try:
        return await run_scan(site)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{project}/suggestions")
def api_suggestions(project: str, type: Optional[str] = None, status: Optional[str] = "new"):
    site = resolve_site(project)
    if not site:
        raise HTTPException(404, f"Site/project '{project}' niet gevonden")
    return {
        "site_id": site["id"],
        "suggestions": list_suggestions(site["id"], stype=type, status=status or None),
    }


@router.patch("/suggestions/{sid}")
def api_patch_suggestion(sid: str, body: StatusPatch):
    if body.status not in ("new", "done", "dismissed"):
        raise HTTPException(400, "Ongeldige status")
    if not _get_suggestion(sid):
        raise HTTPException(404, "Suggestie niet gevonden")
    _update_suggestion(sid, status=body.status)
    return {"ok": True}


@router.post("/suggestions/{sid}/ctr-variants")
async def api_ctr_variants(sid: str):
    sug = _get_suggestion(sid)
    if not sug or sug["type"] != "ctr":
        raise HTTPException(404, "CTR-suggestie niet gevonden")
    site = resolve_site(sug["site_id"])
    if not site:
        raise HTTPException(404, "Site niet gevonden")
    try:
        variants = await generate_ctr_variants(sug, site)
        return {"variants": variants}
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.post("/suggestions/{sid}/refresh")
async def api_refresh(sid: str):
    sug = _get_suggestion(sid)
    if not sug or sug["type"] != "refresh":
        raise HTTPException(404, "Refresh-suggestie niet gevonden")
    site = resolve_site(sug["site_id"])
    if not site:
        raise HTTPException(404, "Site niet gevonden")
    try:
        job_id = await refresh_article(sug, site)
        return {"job_id": job_id, "note": "Verrijkt artikel staat in de Wachtrij ter review."}
    except RuntimeError as e:
        raise HTTPException(502, str(e))

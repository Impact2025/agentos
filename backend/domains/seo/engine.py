"""
Demand Engine — Goldie's pijler 1 (zoekwoordstrategie).

Haalt zoekwoorddata uit Google Search Console, filtert deterministisch de
'striking distance'-kansen eruit (zoekwoorden waar de site al half op scoort —
positie ~4-20 met veel impressies), en laat vervolgens Claude per kans een
actie en een concrete content-invalshoek bepalen.

Het zware denkwerk doet Claude (slim, duur); het bulk-schrijven doet later
Hermes via de conveyor (goedkoop). Dat is precies de taakverdeling uit de video.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import anthropic
import httpx

from ...shared.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, OPENROUTER_API_KEY, CLAUDE_VIA_OPENROUTER,
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_SMART_MODEL,
    anthropic_configured,
)
from ...shared.database import get_conn
from .gsc import fetch_query_performance

# 'Striking distance': nog niet in de top, maar wel binnen bereik.
MIN_POSITION = 4.0
MAX_POSITION = 20.0
DEFAULT_MIN_IMPRESSIONS = 20
DEFAULT_LIMIT = 25

_POSITION_SPAN = (MAX_POSITION + 1) - MIN_POSITION  # 17


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _opportunity_score(impressions: int, position: float) -> float:
    """Kansscore: veel impressies dichtbij pagina 1 = grootste hefboom.

    proximity = 1.0 bij positie 4, daalt naar ~0.06 bij positie 20. Buiten de
    striking-distance-band is de score 0 (geen kans of al goed/te ver weg).
    """
    if position < MIN_POSITION or position > MAX_POSITION:
        return 0.0
    proximity = (MAX_POSITION + 1 - position) / _POSITION_SPAN
    return round(impressions * proximity, 1)


def find_opportunities(
    rows: List[Dict], min_impressions: int = DEFAULT_MIN_IMPRESSIONS, limit: int = DEFAULT_LIMIT
) -> List[Dict]:
    scored: List[Dict] = []
    for r in rows:
        if r["impressions"] < min_impressions:
            continue
        score = _opportunity_score(r["impressions"], r["position"])
        if score <= 0:
            continue
        scored.append({**r, "opportunity_score": score})
    scored.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return scored[:limit]


def _llm_available() -> bool:
    return anthropic_configured() or bool(OPENMODEL_API_KEY) or bool(OPENROUTER_API_KEY)


_ANNOTATE_SYSTEM = (
    "Je bent een Nederlandse SEO-strateeg. Je krijgt 'striking distance'-zoekwoorden "
    "uit Google Search Console (zoekwoorden waar de site al half op scoort). Per zoekwoord "
    "bepaal je: (1) action = 're-optimaliseren' wanneer er waarschijnlijk al een pagina voor "
    "bestaat die je kunt aanscherpen, of 'nieuwe-content' wanneer je er beter nieuwe content "
    "omheen bouwt; (2) angle = één concrete, onderscheidende content-invalshoek (max 12 woorden); "
    "(3) rationale = in één zin waarom dit kansrijk is, onderbouwd met positie/impressies/CTR. "
    "Wees concreet en vermijd algemeenheden."
)


def _claude_complete(system: str, prompt: str, max_tokens: int = 2000) -> str:
    """Vraag een Claude-completion via de eerste werkende route.

    1. Directe Anthropic-API (als ANTHROPIC_API_KEY geldig is).
    2. Claude-model via de OpenModel-gateway (OPENMODEL_SMART_MODEL) — op deze
       machine de primaire route.
    3. Claude via OpenRouter (CLAUDE_VIA_OPENROUTER) als laatste terugval.
    """
    errors = []
    if anthropic_configured():
        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except Exception as e:  # noqa: BLE001
            errors.append(f"anthropic: {e}")

    if OPENMODEL_API_KEY:
        try:
            resp = httpx.post(
                OPENMODEL_BASE_URL.rstrip("/") + "/v1/messages",
                headers={
                    "Authorization": f"Bearer {OPENMODEL_API_KEY}",
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENMODEL_SMART_MODEL, "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            _data = resp.json()
            usage = _data.get("usage") or {}
            if usage:
                from ...shared.outcomes import log_llm_usage
                log_llm_usage(
                    backend="openmodel", model=OPENMODEL_SMART_MODEL, route="seo-engine",
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                )
            text = "".join(
                b.get("text", "") for b in _data.get("content", [])
                if isinstance(b, dict)
            )
            if text.strip():
                return text
            errors.append("openmodel: lege respons")
        except Exception as e:  # noqa: BLE001
            errors.append(f"openmodel: {e}")

    if OPENROUTER_API_KEY:
        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "http://localhost:1250",
                    "X-Title": "Agent OS Demand Engine",
                },
                json={
                    "model": CLAUDE_VIA_OPENROUTER,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            errors.append(f"openrouter: {e}")

    raise RuntimeError("Geen werkende Claude-route. " + " | ".join(errors))


def _annotate(opportunities: List[Dict], site_name: str) -> List[Dict]:
    """Laat Claude per kans action/angle/rationale bepalen. Index-gealigneerd."""
    base = [{"action": "", "angle": "", "rationale": ""} for _ in opportunities]
    if not opportunities or not _llm_available():
        return base

    table = "\n".join(
        f"{i + 1}. zoekwoord={o['query']!r} positie={o['position']} "
        f"impressies={o['impressions']} klikken={o['clicks']} ctr={o['ctr']}%"
        for i, o in enumerate(opportunities)
    )
    prompt = (
        f"Site: {site_name}\n\n"
        f"Hieronder {len(opportunities)} zoekwoorden uit Search Console, gesorteerd op kans:\n"
        f"{table}\n\n"
        f"Geef een JSON-array met exact {len(opportunities)} objecten, in DEZELFDE volgorde:\n"
        '[{"action": "re-optimaliseren of nieuwe-content", "angle": "...", "rationale": "..."}]\n'
        "Antwoord UITSLUITEND met de JSON-array, geen extra tekst."
    )

    try:
        raw = _claude_complete(_ANNOTATE_SYSTEM, prompt, max_tokens=2000)
        arr = json.loads(_strip_json_fences(raw))
        for i in range(min(len(arr), len(base))):
            item = arr[i] or {}
            action = (item.get("action") or "").strip().lower()
            if action not in ("re-optimaliseren", "nieuwe-content"):
                action = "nieuwe-content"
            base[i] = {
                "action": action,
                "angle": (item.get("angle") or "").strip(),
                "rationale": (item.get("rationale") or "").strip(),
            }
    except Exception as e:  # noqa: BLE001
        print(f"[demand] Claude annotatie mislukt: {e}")
    return base


def scan_site(
    site: Dict,
    days: int = 90,
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS,
    limit: int = DEFAULT_LIMIT,
) -> Dict:
    """Draai een volledige Demand-Engine-scan voor één site en persisteer de kansen.

    Bestaande kansen met status != 'new' (al in behandeling/gepubliceerd/genegeerd)
    blijven staan en worden niet opnieuw aangeboden.
    """
    gsc_property = (site.get("gsc_property") or "").strip()
    if not gsc_property:
        raise ValueError("Site heeft geen gsc_property ingesteld.")

    rows = fetch_query_performance(gsc_property, days=days)
    opportunities = find_opportunities(rows, min_impressions=min_impressions, limit=limit)
    annotations = _annotate(opportunities, site.get("name") or gsc_property)

    scanned_at = _now()
    saved: List[Dict] = []
    with get_conn() as conn:
        existing = {
            row["query"]
            for row in conn.execute(
                "SELECT query FROM opportunities WHERE site_id = ? AND status != 'new'",
                (site["id"],),
            ).fetchall()
        }
        conn.execute(
            "DELETE FROM opportunities WHERE site_id = ? AND status = 'new'", (site["id"],)
        )
        for opp, ann in zip(opportunities, annotations):
            if opp["query"] in existing:
                continue
            oid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO opportunities
                   (id, site_id, query, clicks, impressions, ctr, position,
                    opportunity_score, action, angle, rationale, status, scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                (
                    oid, site["id"], opp["query"], opp["clicks"], opp["impressions"],
                    opp["ctr"], opp["position"], opp["opportunity_score"],
                    ann["action"], ann["angle"], ann["rationale"], scanned_at,
                ),
            )
            saved.append({
                "id": oid, "site_id": site["id"], "status": "new",
                "scanned_at": scanned_at, **opp, **ann,
            })

    # Cold-start: leverde GSC niets op én staat er ook niets meer open, dan
    # zit deze site vast (nieuwe site zonder rankings). Genereer dan kansen
    # uit het site-profiel zodat de contentmotor kan blijven draaien.
    cold_started: List[Dict] = []
    if not saved and not list_opportunities(site_id=site["id"], status="new"):
        cold_started = cold_start_opportunities(site)
        saved.extend(cold_started)

    return {
        "site_id": site["id"],
        "scanned_at": scanned_at,
        "analysed": len(rows),
        "found": len(opportunities),
        "new": len(saved),
        "cold_start": len(cold_started),
        "opportunities": saved,
    }


_COLD_START_SYSTEM = (
    "Je bent een Nederlandse SEO-strateeg gespecialiseerd in nieuwe websites zonder "
    "rankinghistorie. Je bedenkt long-tail zoekwoorden waar een verse site realistisch "
    "op kan scoren: specifiek, vraaggedreven, lage concurrentie, aansluitend op het "
    "site-profiel. Geen generieke head-terms (daar wint een nieuwe site nooit). "
    "Antwoord UITSLUITEND met een JSON-array."
)

_COLD_START_SCORE = 60.0  # onder echte striking-distance-kansen, boven niets


def cold_start_opportunities(site: Dict, count: int = 8) -> List[Dict]:
    """Kansen genereren voor een site zonder bruikbare GSC-data.

    Striking-distance vereist bestaande posities mét impressies — een site
    zonder live content heeft die per definitie niet, dus zonder deze
    cold-start blijft zo'n site eeuwig op 0 artikelen hangen. De kansen komen
    uit het site-profiel (kennisbank) en worden als handmatige kans opgeslagen;
    de contentmotor pakt ze daarna gewoon op. Vereist een LLM."""
    if not _llm_available():
        return []
    from .knowledge import get_site_knowledge
    kb = get_site_knowledge(site)
    profile = kb.get("profile") or ""
    if len(profile) < 40:
        return []  # zonder profiel wordt keyword-onderzoek giswerk — niet doen

    with get_conn() as conn:
        existing = {
            r["query"].strip().lower()
            for r in conn.execute(
                "SELECT query FROM opportunities WHERE site_id = ?", (site["id"],)
            ).fetchall()
        }

    prompt = (
        f"Site: {site.get('name')} ({site.get('base_url', '')})\n\n"
        f"## Site-profiel\n{profile[:2000]}\n\n"
        + (f"## CTA's / diensten\n- " + "\n- ".join(kb.get("ctas", [])[:6]) + "\n\n"
           if kb.get("ctas") else "")
        + f"Deze site heeft nog geen rankings. Bedenk {count} long-tail "
        "content-kansen waarmee de site zijn eerste organische bezoekers kan "
        "winnen. Geef een JSON-array met exact dit formaat:\n"
        '[{"query": "het zoekwoord (3-6 woorden, zoals mensen echt zoeken)", '
        '"angle": "concrete onderscheidende invalshoek (max 12 woorden)", '
        '"rationale": "waarom een nieuwe site hier kan winnen, één zin"}]'
    )
    try:
        raw = _claude_complete(_COLD_START_SYSTEM, prompt, max_tokens=2500)
        items = json.loads(_strip_json_fences(raw))
        assert isinstance(items, list)
    except Exception as e:  # noqa: BLE001
        print(f"[demand] Cold-start keyword-onderzoek mislukt: {e}")
        return []

    created: List[Dict] = []
    for item in items[:count]:
        query = (item.get("query") or "").strip() if isinstance(item, dict) else ""
        if not query or query.lower() in existing:
            continue
        existing.add(query.lower())
        created.append(create_manual_opportunity(
            site_id=site["id"], query=query,
            angle=(item.get("angle") or "").strip(),
            rationale=(item.get("rationale") or "").strip(),
            action="nieuwe-content", opportunity_score=_COLD_START_SCORE,
        ))
    return created


async def run_weekly_demand_scan() -> None:
    """Scheduler (ma 06:15): kansen-scan voor alle sites met GSC, inclusief
    cold-start voor sites zonder rankings. Zonder deze job raakt de kansen-
    voorraad op en valt de di/vr-contentmotor stil zonder dat iemand het ziet."""
    import asyncio
    from ...shared.outcomes import log_outcome
    from . import sites as sites_service

    scanned, new_total, cold_total, failed = 0, 0, 0, []
    for s in sites_service.list_sites():
        site = sites_service.get_site(s["id"]) or s
        if not (site.get("gsc_property") or "").strip():
            continue
        try:
            res = await asyncio.to_thread(scan_site, site)
            scanned += 1
            new_total += res.get("new", 0)
            cold_total += res.get("cold_start", 0)
        except Exception as e:  # noqa: BLE001
            failed.append(site.get("name") or site["id"])
            print(f"[demand] Weekscan mislukt voor {site.get('name')}: {e}")
    log_outcome(
        "SEO", "demand_scan",
        f"Wekelijkse Demand-scan: {scanned} site(s), {new_total} nieuwe kans(en)"
        + (f" waarvan {cold_total} via cold-start" if cold_total else "")
        + (f"; mislukt: {', '.join(failed[:5])}" if failed else ""),
        artifact="/api/seo/opportunities",
        next_step=("Controleer de GSC-koppeling van de mislukte site(s)." if failed
                   else "Niets — de contentmotor pakt de kansen automatisch op (di/vr)."),
        status="error" if failed and not scanned else "ok",
    )


def create_manual_opportunity(
    site_id: str, query: str, angle: str, rationale: str,
    action: str = "nieuwe-content", opportunity_score: float = 100.0,
) -> Dict:
    """Voeg een kans handmatig toe (bv. uit keyword-onderzoek) i.p.v. via een GSC-scan.

    Voor jonge sites die nog niet ranken voor een zoekwoord levert GSC geen impressies,
    dus `scan_site` kan die kansen nooit vinden (striking-distance vereist al posities
    4-20 mét impressies). Dit is de ontsnappingsklep voor nog-niet-geschreven content.
    """
    oid = str(uuid.uuid4())
    scanned_at = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO opportunities
               (id, site_id, query, clicks, impressions, ctr, position,
                opportunity_score, action, angle, rationale, status, scanned_at)
               VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?, 'new', ?)""",
            (oid, site_id, query, opportunity_score, action, angle, rationale, scanned_at),
        )
    return {
        "id": oid, "site_id": site_id, "query": query, "clicks": 0, "impressions": 0,
        "ctr": 0, "position": 0, "opportunity_score": opportunity_score,
        "action": action, "angle": angle, "rationale": rationale,
        "status": "new", "scanned_at": scanned_at,
    }


def list_opportunities(site_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
    clauses, params = [], []
    if site_id:
        clauses.append("site_id = ?")
        params.append(site_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM opportunities{where} "
            "ORDER BY opportunity_score DESC, impressions DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def update_opportunity(opp_id: str, status: Optional[str] = None,
                       live_url: Optional[str] = None,
                       published_at: Optional[str] = None) -> Optional[Dict]:
    """Werk een kans bij. Status én/of live-URL/publicatietimestamp kunnen los worden gezet.

    `live_url` wordt door de write-and-publish pipeline teruggeschreven zodra een
    artikel écht live staat — zo kan de Kansen-card in de UI onderscheiden tussen
    "handmatig op Gepubliceerd gevinkt" en "staat daadwerkelijk live op de site".
    """
    sets, params = [], []
    allowed = {"new", "in_progress", "published", "dismissed"}
    if status is not None:
        if status not in allowed:
            raise ValueError(f"Ongeldige status '{status}'. Toegestaan: {sorted(allowed)}")
        sets.append("status = ?")
        params.append(status)
    if live_url is not None:
        sets.append("live_url = ?")
        params.append(live_url)
    if published_at is not None:
        sets.append("published_at = ?")
        params.append(published_at)
    if not sets:
        return None
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE opportunities SET {', '.join(sets)} WHERE id = ?",
            params + [opp_id],
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
    return dict(row)


# Alias zodat bestaande callers (frontend updateOppStatus) blijven werken.
def update_opportunity_status(opp_id: str, status: str) -> Optional[Dict]:
    return update_opportunity(opp_id, status=status)

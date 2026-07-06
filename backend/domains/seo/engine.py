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
    2. Claude via OpenRouter (CLAUDE_VIA_OPENROUTER) als terugval.

    Zo blijft de Demand Engine werken ook als één van beide sleutels ontbreekt of
    verlopen is — wat hier het geval was met de directe sleutel.
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
    if not opportunities or not (anthropic_configured() or OPENROUTER_API_KEY):
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

    return {
        "site_id": site["id"],
        "scanned_at": scanned_at,
        "analysed": len(rows),
        "found": len(opportunities),
        "new": len(saved),
        "opportunities": saved,
    }


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


def update_opportunity_status(opp_id: str, status: str) -> Optional[Dict]:
    allowed = {"new", "in_progress", "published", "dismissed"}
    if status not in allowed:
        raise ValueError(f"Ongeldige status '{status}'. Toegestaan: {sorted(allowed)}")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE opportunities SET status = ? WHERE id = ?", (status, opp_id)
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
    return dict(row)

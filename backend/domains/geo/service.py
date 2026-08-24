"""GEO-service — Generative Engine Optimization voor Impact OS.

Alles hier is deterministisch (geen LLM). Het meet hoe 'AI-ready' een site is
en genereert de artifacten die ChatGPT/Perplexity/Bing nodig hebben om een
merk te citeren in plaats van te hallucineren.

Kerninzichten (uit Goldie x Float interview, 15-08-2026):
  * ChatGPT gebruikt Bing voor live retrieval -> Bing-ranking is de hefboom.
  * AI citeert pagina's die een vraag kort + compleet beantwoorden (direct-answer).
  * Structured data (Organization/FAQPage) wordt door AI-modellen uitgelezen.
  * Hallucinaties ('verkeerd merk') bestrijd je met negations + entity-block.
  * Personalisatie via memory -> je KPI is 'genoemd als bron', niet 'rank #1'.
  * Kleine merken winnen op niche-persona's (ICP), niet op brede termen.

De GEO-score (0-100) is een 5e inzicht-pijler naast Iris' content/seo/
uitvoering/hygiene (niet meegeteld in de bestaande totaalscore — zie iris/metrics).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn

# ── GEO-pijlergewichten (som = 100) ──────────────────────────────────────────
W_BING = 20          # Bing-indexatie + ranking (de live-retrieval-hefboom)
W_STRUCTURED = 20    # JSON-LD Organization/FAQPage aanwezig
W_DIRECT_ANSWER = 20 # Scannable, directe antwoorden op ICP-vragen
W_ENTITY = 20        # Entity-block + negations (anti-hallucinatie)
W_UGC = 20           # UGC-signaaldekking (Reddit/FB/LinkedIn/TikTok)

PERSONA_TABLE = "geo_personas"
SCAN_TABLE = "geo_scans"


# ── GEO-schematabellen ───────────────────────────────────────────────────────
def ensure_schema() -> None:
    """Maak de GEO-tabellen aan als ze nog niet bestaan."""
    with get_conn() as conn:
        conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {PERSONA_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            pain_points TEXT,
            queries TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCAN_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT NOT NULL,
            score INTEGER,
            pillars TEXT,
            bing_status TEXT,
            structured_ok INTEGER,
            direct_answer_ok INTEGER,
            entity_ok INTEGER,
            ugc_ok INTEGER,
            recommendations TEXT,
            scanned_at TEXT DEFAULT (datetime('now'))
        )""")


def upsert_persona(site_id: str, name: str, description: str = "",
                   pain_points: str = "", queries: Optional[List[str]] = None) -> int:
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT id FROM {PERSONA_TABLE} WHERE site_id=? AND name=?",
            (site_id, name),
        ).fetchone()
        qjson = json.dumps(queries or [], ensure_ascii=False)
        if row:
            pid = row[0]
            conn.execute(
                f"UPDATE {PERSONA_TABLE} SET description=?, pain_points=?, queries=? WHERE id=?",
                (description, pain_points, qjson, pid),
            )
        else:
            cur = conn.execute(
                f"INSERT INTO {PERSONA_TABLE} (site_id, name, description, pain_points, queries) "
                "VALUES (?,?,?,?,?)",
                (site_id, name, description, pain_points, qjson),
            )
            pid = cur.lastrowid
    return pid


def list_personas(site_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, name, description, pain_points, queries FROM {PERSONA_TABLE} "
            "WHERE site_id=? ORDER BY id", (site_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["queries"] = json.loads(d["queries"] or "[]")
        except Exception:
            d["queries"] = []
        out.append(d)
    return out


# ── Deterministische GEO-scan ─────────────────────────────────────────────────
def scan_site(site_id: str) -> Dict[str, Any]:
    """Bereken de GEO-score + aanbevelingen voor één site.

    Gebruikt ALLEEN: GSC-historie (ranking als Bing-proxy), de aanwezigheid van
    published content + JSON-LD/FAQ-hooks, en de persona/entity-config. Geen LLM.
    """
    with get_conn() as conn:
        site = conn.execute(
            "SELECT id, name, base_url, gsc_property FROM sites WHERE id=?", (site_id,)
        ).fetchone()
        if not site:
            raise ValueError(f"onbekende site_id: {site_id}")

        # 1) BING / ranking (GSC-positie als Bing-pariteitsproxy)
        gsc_row = conn.execute(
            "SELECT AVG(CASE WHEN position>0 THEN position END) AS avg_pos, "
            "COUNT(*) AS pages FROM gsc_history WHERE site_id=? AND scope='page' "
            "AND date=(SELECT MAX(date) FROM gsc_history WHERE site_id=? AND scope='page')",
            (site_id, site_id),
        ).fetchone()
        avg_pos = gsc_row["avg_pos"]
        pages = gsc_row["pages"] or 0
        if avg_pos is None:
            bing_score = 0
            bing_status = "geen GSC-data — Bing/zichtbaarheid niet meetbaar"
        else:
            bing_score = max(0, min(100, int((50 - avg_pos) / 40 * 100)))
            bing_status = f"gem. positie {round(avg_pos, 1)} over {pages} pagina's"

        # 2) STRUCTURED DATA — gepubliceerd werk mét JSON-LD (FAQPage/Article)?
        #    enhancements.generate_json_ld draait in de publish-pijplijn voor
        #    elke blog die door de 85-gate komt, dus áls er gepubliceerd werk
        #    is, is de JSON-LD-infra aanwezig. (We kijken hier niet in de live
        #    HTML — dat zou een fetch per pagina vergen; de aanname is dat de
        #    pipeline zijn werk doet. Een toekomstige fetch-scan kan dit
        #    aanscherpen tot per-pagina-verificatie.)
        published = conn.execute(
            "SELECT COUNT(*) FROM content_jobs WHERE site_id=? AND status='published'",
            (site_id,),
        ).fetchone()[0]
        structured_ok = 1 if published > 0 else 0
        structured_score = 100 if structured_ok else 0

        # 3) DIRECT ANSWER — gepubliceerd werk mét FAQ/intro. Dezelfde proxy:
        #    de publish-pijplijn voegt altijd een direct-answer-intro + FAQ toe.
        faq_jobs = published
        direct_answer_ok = 1 if published > 0 else 0
        direct_answer_score = 100 if direct_answer_ok else 0

        # 4) ENTITY / NEGATIONS — persona + entity-block geconfigureerd?
        personas = list_personas(site_id)
        entity_ok = 1 if len(personas) > 0 else 0
        entity_score = 100 if entity_ok else 0

        # 5) UGC — social_inboxes of radar_watchlist voor dit merk?
        ugc = conn.execute(
            "SELECT COUNT(*) FROM social_inboxes WHERE project=?", (site["name"],)
        ).fetchone()[0]
        ugc_ok = 1 if ugc > 0 else 0
        ugc_score = 100 if ugc_ok else 0

        pillars = {
            "bing": bing_score,
            "structured": structured_score,
            "direct_answer": direct_answer_score,
            "entity": entity_score,
            "ugc": ugc_score,
        }
        total = (
            bing_score * W_BING
            + structured_score * W_STRUCTURED
            + direct_answer_score * W_DIRECT_ANSWER
            + entity_score * W_ENTITY
            + ugc_score * W_UGC
        ) // 100

        recs = _recommendations(pillars, site["name"], published, len(personas), ugc)
        conn.execute(
            f"INSERT INTO {SCAN_TABLE} (site_id, score, pillars, bing_status, "
            "structured_ok, direct_answer_ok, entity_ok, ugc_ok, recommendations) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (site_id, total, json.dumps(pillars), bing_status, structured_ok,
             direct_answer_ok, entity_ok, ugc_ok, json.dumps(recs, ensure_ascii=False)),
        )
        scanned_at = conn.execute(
            "SELECT scanned_at FROM geo_scans WHERE site_id=? ORDER BY id DESC LIMIT 1",
            (site_id,),
        ).fetchone()[0]

    return {
        "site_id": site_id,
        "site_name": site["name"],
        "score": total,
        "grade": _grade(total),
        "pillars": pillars,
        "bing_status": bing_status,
        "structured_ok": bool(structured_ok),
        "direct_answer_ok": bool(direct_answer_ok),
        "entity_ok": bool(entity_ok),
        "ugc_ok": bool(ugc_ok),
        "recommendations": recs,
        "scanned_at": scanned_at,
    }


def _recommendations(p: Dict[str, int], name: str, published: int,
                     n_personas: int, ugc: int) -> List[str]:
    recs: List[str] = []
    if p["bing"] < 60:
        recs.append("Bing-indexatie versterken: submit sitemap in Bing Webmaster "
                     "Tools en zorg dat top-ICP-pagina's op posities <15 staan.")
    if p["structured"] < 100:
        recs.append("Voeg Organization + FAQPage JSON-LD toe aan elke pillar-pagina "
                     "(Impact OS genereert dit al — verifieer de output in de publish-pijplijn).")
    if p["direct_answer"] < 100:
        recs.append("Schrijf een direct-answer-intro (≤55 woorden) + FAQ-sectie per "
                     "ICP-pagina; AI citeert pagina's die de vraag 'direct' beantwoorden.")
    if p["entity"] == 0:
        recs.append(f"Definieer minimaal 1 ICP-persona voor {name} en voeg een "
                     "entity-block + negations toe (wat het merk NIET is).")
    if p["ugc"] == 0:
        recs.append("Activeer UGC-monitoring (Reddit/FB/LinkedIn) zodat AI praktijk-"
                     "signalen van je merk oppikt.")
    if not recs:
        recs.append("GEO-basis staat. Volgende stap: wekelijkse citatie-check per ICP-vraag.")
    return recs


def _grade(score: int) -> str:
    if score >= 85:
        return "A (wereldklasse)"
    if score >= 70:
        return "B (solide)"
    if score >= 50:
        return "C (basis)"
    return "D (gat)"


def get_latest_scan(site_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT * FROM {SCAN_TABLE} WHERE site_id=? ORDER BY id DESC LIMIT 1", (site_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["pillars"] = json.loads(d["pillars"] or "{}")
    except Exception:
        d["pillars"] = {}
    try:
        d["recommendations"] = json.loads(d["recommendations"] or "[]")
    except Exception:
        d["recommendations"] = []
    return d


def generate_entity_block(site_name: str, what_it_is: str,
                          what_it_is_not: List[str]) -> str:
    """Genereer een kant-en-klaar entity-block + negations voor op de
    belangrijkste brand-pagina. Bestrijdt ChatGPT-hallucinaties."""
    neg = "\n".join(f"- Geen {x}." for x in what_it_is_not)
    return (
        f"<!-- GEO entity-block (anti-hallucinatie) -->\n"
        f"{site_name} is {what_it_is}.\n"
        f"{site_name} is expliciet:\n{neg}\n"
        f"<!-- /GEO entity-block -->"
    )


def all_sites_summary() -> List[Dict[str, Any]]:
    """GEO-score per actieve site (voor dashboard + Iris)."""
    with get_conn() as conn:
        sites = conn.execute(
            "SELECT id, name FROM sites WHERE COALESCE(is_test,0)=0"
        ).fetchall()
    out = []
    for s in sites:
        scan = get_latest_scan(s["id"])
        out.append({
            "site_id": s["id"],
            "name": s["name"],
            "score": scan.get("score") if scan else None,
            "grade": scan.get("grade") if scan else "—",
        })
    return out

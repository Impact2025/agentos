"""
Trend-brug — Mission Radar-signalen als tweede zoekwoordbron voor de Demand Engine.

De striking-distance-scan (engine.py) is reactief: hij vindt alleen zoekwoorden
waar de site al half op scoort. Trends zijn het complement — first-mover zijn op
een vers onderwerp is hoe je AI Overviews-citaties pakt vóórdat er concurrentie
is. De Mission Radar scant al elke 4 uur op trends per project; deze module zet
de topsignalen om in `opportunities`, zodat ze in dezelfde contentpijplijn
(select_topic → meertraps-generator → Wachtrij) meelopen als de GSC-kansen.

Koppeling site ↔ radar-project: op genormaliseerde naam (radar_signals.project
vs. sites.name). Elk geconverteerd signaal gaat naar status 'targeted' zodat een
volgende sync 'm niet opnieuw aanbiedt; dedupe gebeurt daarnaast op query-tekst
tegen álle bestaande kansen van de site.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn
from . import sites as sites_service

logger = logging.getLogger(__name__)

# Alleen echte topsignalen worden een content-kans — de Radar-tab blijft de
# plek voor alles daaronder.
TREND_MIN_SIGNAL_SCORE = 60.0
TREND_MAX_PER_SYNC = 3  # per site per sync, zodat trends de GSC-kansen niet verdringen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _signal_query(sig: Dict) -> str:
    """Het zoekwoord waarop we content bouwen: het gemonitorde keyword als dat
    er is, anders een opgeschoonde variant van de brontitel."""
    kw = (sig.get("keyword") or "").strip()
    if kw and not kw.startswith("site:"):
        return kw
    title = re.sub(r"\s+", " ", sig.get("title") or "").strip()
    return title[:80]


def _existing_queries(conn, site_id: str) -> set:
    rows = conn.execute(
        "SELECT query FROM opportunities WHERE site_id = ?", (site_id,)
    ).fetchall()
    return {(r["query"] or "").strip().lower() for r in rows}


def sync_trend_opportunities(site: Dict) -> Dict:
    """Zet verse Radar-topsignalen voor deze site om in Demand Engine-kansen.

    Idempotent: geconverteerde signalen krijgen status 'targeted' en queries
    die al als kans bestaan (welke status dan ook) worden overgeslagen."""
    try:
        from ..radar.models import ensure_schema
        ensure_schema()
    except Exception as e:  # radar-domein hoort te bestaan, maar faal zacht
        logger.warning("[trends] Radar-schema niet beschikbaar: %s", e)
        return {"site": site.get("name"), "created": 0, "skipped": 0}

    site_key = _norm_name(site.get("name"))
    created: List[Dict] = []
    skipped = 0
    with get_conn() as conn:
        signals = conn.execute(
            "SELECT * FROM radar_signals WHERE status = 'new' AND signal_score >= ? "
            "ORDER BY signal_score DESC",
            (TREND_MIN_SIGNAL_SCORE,),
        ).fetchall()
        existing = _existing_queries(conn, site["id"])
        for row in signals:
            sig = dict(row)
            if _norm_name(sig.get("project")) != site_key:
                continue
            if len(created) >= TREND_MAX_PER_SYNC:
                break
            query = _signal_query(sig)
            if not query or query.lower() in existing:
                skipped += 1
                continue
            oid = str(uuid.uuid4())
            angle = (sig.get("ai_angle") or "").strip() or f"Listicle rond '{query}'"
            hook = (sig.get("ai_hook") or "").strip()
            rationale = (
                f"Trending (Radar-score {round(sig.get('signal_score') or 0)}): "
                + (hook or f"vers signaal via {sig.get('source') or 'web'} — {sig.get('url', '')}")
            )[:400]
            conn.execute(
                """INSERT INTO opportunities
                   (id, site_id, query, clicks, impressions, ctr, position,
                    opportunity_score, action, angle, rationale, status, scanned_at)
                   VALUES (?, ?, ?, 0, 0, 0, 0, ?, 'nieuwe-content', ?, ?, 'new', ?)""",
                (oid, site["id"], query, float(sig.get("signal_score") or 0),
                 angle, rationale, _now()),
            )
            conn.execute(
                "UPDATE radar_signals SET status = 'targeted', updated_at = ? WHERE id = ?",
                (_now(), sig["id"]),
            )
            existing.add(query.lower())
            created.append({"id": oid, "query": query, "signal_id": sig["id"]})
    return {"site": site.get("name"), "created": len(created),
            "skipped": skipped, "opportunities": created}


def sync_all_trend_opportunities() -> Dict:
    """Trend-sync voor alle sites — draait automatisch na elke Radar sky-scan
    en is handmatig te triggeren via POST /api/demand/trend-sync."""
    results: Dict[str, Dict] = {}
    total = 0
    for s in sites_service.list_sites():
        full = sites_service.get_site(s["id"])
        if not full:
            continue
        try:
            result = sync_trend_opportunities(full)
        except Exception as e:
            logger.exception("[trends] Trend-sync mislukt voor %s", full.get("name"))
            result = {"site": full.get("name"), "created": 0, "error": str(e)[:200]}
        results[full["name"]] = result
        total += result.get("created", 0)
    if total:
        from ...shared.outcomes import log_outcome
        log_outcome("SEO", "trend-kansen",
                    f"{total} trending zoekwoord(en) uit de Mission Radar als content-kans klaargezet",
                    next_step="Ze lopen mee in de eerstvolgende content-run (di/vr) — of start een run-now")
    return {"total_created": total, "sites": results}

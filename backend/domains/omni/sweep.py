"""SERP-Omni scheduler-sweep.

Periodieke job die de hoogste open zoekwoord-kansen uit de `opportunities`-
tabel pakt en een Omni-run draait (SERP reverse-engineering + asset-generatie
naar de staged omni_queue). Vincent keurt daarna goed — niets wordt automatisch
gepost. Idempotent + quota-bewust: per run een gelimiteerde batch, en als de
websearch-laag faalt (quota), stopt de run in plaats van alle tokens te verbranden.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List

from ...shared.database import get_conn
from .generator import generate_for_keyword

logger = logging.getLogger(__name__)

# Max aantal keywords per run — houd de LLM-kosten beheersbaar.
MAX_KEYWORDS_PER_RUN = 8
# Alleen kansen met een opportunity_score boven deze drempel worden meegenomen.
MIN_OPPORTUNITY_SCORE = 70


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _top_open_keywords(site_id: str) -> List[str]:
    """Open kansen (nieuw/in_progress) met de hoogste score, zonder recente
    Omni-run (geen dubbele assets voor hetzelfde keyword binnen 7 dagen)."""
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            """SELECT o.query, o.opportunity_score
               FROM opportunities o
               WHERE o.site_id = ?
                 AND o.status IN ('new','in_progress')
                 AND o.opportunity_score >= ?
                 AND NOT EXISTS (
                   SELECT 1 FROM omni_queue q
                   WHERE q.site_id = o.site_id AND q.keyword = o.query
                     AND q.created_at >= datetime('now','-7 days')
                 )
               ORDER BY o.opportunity_score DESC
               LIMIT ?""",
            (site_id, MIN_OPPORTUNITY_SCORE, MAX_KEYWORDS_PER_RUN),
        ).fetchall()
    return [r["query"] for r in rows]


def _site_row(site_id: str) -> Dict:
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        return dict(conn.execute(
            "SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone() or {})


def _owned_domains(site: Dict) -> List[str]:
    dom = (site.get("base_url") or "").lower().replace("https://", "").replace("http://", "")
    return [dom.rstrip("/")] if dom else []


async def run_omni_sweep() -> Dict:
    """Sweep over alle sites met een omni_queue-bekwaamheid (elke site).

    Pakt per site de top-open kansen en genereert assets naar omni_queue.
    """
    from ...shared.outcomes import log_outcome

    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        sites = conn.execute(
            "SELECT id, name FROM sites WHERE status != 'archived' OR status IS NULL"
        ).fetchall()

    total_queued = 0
    considered = 0
    sites_touched = 0
    for s in sites:
        site = _site_row(s["id"])
        if not site:
            continue
        keywords = _top_open_keywords(s["id"])
        if not keywords:
            continue
        considered += len(keywords)
        sites_touched += 1
        with get_conn() as conn:
            conn.row_factory = __import__("sqlite3").Row
            for kw in keywords:
                try:
                    res = generate_for_keyword(kw, site, "",
                                               owned_domains=_owned_domains(site))
                except Exception as e:  # noqa: BLE001
                    logger.error("[omni-sweep] generatie faalde voor %s/%s: %s",
                                 s["name"], kw, e)
                    continue
                for a in res.get("assets", []):
                    qid = f"omni_{__import__('uuid').uuid4().hex[:12]}"
                    conn.execute(
                        "INSERT INTO omni_queue (id, site_id, keyword, asset_type, "
                        "platform, title, body, serp_profile, angle, status, score, "
                        "note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (qid, s["id"], kw, a["asset_type"], a["platform"],
                         a.get("title", ""), a.get("body", ""),
                         __import__("json").dumps(res.get("serp", {})),
                         "", a.get("status", "staged"), a.get("score", 0),
                         a.get("note", ""), _now_iso(), _now_iso()))
                    total_queued += 1

    summary = {
        "considered": considered,
        "sites_touched": sites_touched,
        "queued": total_queued,
    }
    if total_queued:
        try:
            log_outcome(project="AgentOS", action="omni-sweep",
                        detail=f"{total_queued} platform-assets klaargezet uit {considered} keywords",
                        status="ok")
        except Exception:  # noqa: BLE001
            pass
    logger.info("[omni-sweep] %s", summary)
    return summary

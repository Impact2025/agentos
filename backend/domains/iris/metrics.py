"""Iris-metrics — de harde cijfers waarop de manager-agent stuurt.

Alles hier is deterministisch (geen LLM): per project een rapportcijfer
opgebouwd uit vier pijlers, plus de globale funnel- en systeemcijfers.
De LLM-laag (service.py) krijgt deze cijfers als input en mag er een
oordeel over vellen, maar de cijfers zelf zijn altijd reproduceerbaar.

Pijlers per project (samen 100):
- content    (25): draait de contentmotor — live-artikelen laatste 30 dagen
                   t.o.v. het batch-doel, en blijft de Wachtrij niet liggen.
- seo        (35): meetbare vindbaarheid — GSC-clicks/positie/CTR van de
                   gepubliceerde pagina's. Geen GSC-data = laag, met reden.
- uitvoering (20): doelen afgerond vs. mislukt in de laatste 30 dagen.
- hygiene    (20): fouten in de uitkomst-feed (7 dagen) en needs_work-jobs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...shared.database import get_conn


def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def _site_projects(conn) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, base_url, gsc_property, auto_content_enabled, "
        "content_batch_size FROM sites"
    ).fetchall()]


def _content_pillar(conn, site_id: str, batch_size: int) -> Dict[str, Any]:
    live_30d = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'published' "
        "AND created_at > datetime('now', '-30 days')", (site_id,)
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'pending_review'",
        (site_id,)
    ).fetchone()[0]
    stale = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'pending_review' "
        "AND created_at < datetime('now', '-3 days')", (site_id,)
    ).fetchone()[0]
    needs_work = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'needs_work'",
        (site_id,)
    ).fetchone()[0]
    # Doel: 2 runs/week × batch_size ≈ 8×batch per maand; 100% = alles gehaald.
    target_30d = max(1, (batch_size or 1) * 8)
    score = _clamp(live_30d / target_30d * 25, 0, 25)
    # Wachtrij die blijft liggen kost punten: de motor draait dan voor niets.
    score = _clamp(score - stale * 2, 0, 25)
    return {
        "score": round(score, 1),
        "live_30d": live_30d,
        "target_30d": target_30d,
        "pending_review": pending,
        "stale_review": stale,
        "needs_work": needs_work,
    }


def _seo_pillar(conn, site_id: str, gsc_configured: bool) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS pages, "
        "SUM(gsc_clicks) AS clicks, SUM(gsc_impressions) AS impressions, "
        "SUM(CASE WHEN gsc_clicks > 0 THEN 1 ELSE 0 END) AS pages_with_clicks, "
        "AVG(CASE WHEN gsc_position > 0 THEN gsc_position END) AS avg_position "
        "FROM published_pages WHERE site_id = ?", (site_id,)
    ).fetchone()
    pages = row["pages"] or 0
    clicks = row["clicks"] or 0
    impressions = row["impressions"] or 0
    pages_with_clicks = row["pages_with_clicks"] or 0
    avg_position = round(row["avg_position"], 1) if row["avg_position"] else None

    if not gsc_configured:
        # Zonder meetdata kan SEO nooit 'wereldklasse' heten — max 10/35.
        score = _clamp(min(pages, 10), 0, 10)
        note = "geen GSC-koppeling — vindbaarheid is niet meetbaar"
    elif pages == 0:
        score, note = 0.0, "nog geen gepubliceerde pagina's"
    else:
        # 15 punten: aandeel pagina's dat daadwerkelijk clicks krijgt.
        score = pages_with_clicks / pages * 15
        # 10 punten: absolute clicks (100+/30d = vol).
        score += _clamp(clicks / 100 * 10, 0, 10)
        # 10 punten: gemiddelde positie (pos 1 = 10, pos 30+ = 0).
        if avg_position:
            score += _clamp((30 - avg_position) / 29 * 10, 0, 10)
        note = ""
        score = _clamp(score, 0, 35)

    ctr = round(clicks / impressions * 100, 2) if impressions else None
    open_suggestions = conn.execute(
        "SELECT COUNT(*) FROM seo_suggestions WHERE site_id = ? AND status = 'new'",
        (site_id,)
    ).fetchone()[0]
    return {
        "score": round(score, 1),
        "pages": pages,
        "clicks": clicks,
        "impressions": impressions,
        "pages_with_clicks": pages_with_clicks,
        "avg_position": avg_position,
        "ctr_pct": ctr,
        "open_suggestions": open_suggestions,
        "note": note,
    }


def _execution_pillar(conn, project_names: List[str]) -> Dict[str, Any]:
    ph = ",".join("?" for _ in project_names) or "''"
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM goals "
        f"WHERE lower(project) IN ({ph}) AND updated_at > datetime('now', '-30 days') "
        f"GROUP BY status",
        [p.lower() for p in project_names],
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in rows}
    completed = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    running = by_status.get("running", 0)
    finished = completed + failed
    if finished == 0:
        # Geen afgeronde doelen: half krediet als er tenminste iets loopt.
        score = 10.0 if running else 5.0
    else:
        score = _clamp(completed / finished * 20, 0, 20)
    return {
        "score": round(score, 1),
        "completed_30d": completed,
        "failed_30d": failed,
        "running": running,
        "by_status": by_status,
    }


def _hygiene_pillar(conn, project_names: List[str], needs_work: int) -> Dict[str, Any]:
    ph = ",".join("?" for _ in project_names) or "''"
    errors_7d = conn.execute(
        f"SELECT COUNT(*) FROM activity_log WHERE status = 'error' "
        f"AND lower(project) IN ({ph}) AND created_at > datetime('now', '-7 days')",
        [p.lower() for p in project_names],
    ).fetchone()[0]
    score = _clamp(20 - errors_7d * 3 - needs_work * 2, 0, 20)
    return {"score": round(score, 1), "errors_7d": errors_7d, "needs_work": needs_work}


def _trend_block(site_id: str) -> Optional[Dict[str, Any]]:
    """Week-over-week-delta's uit de GSC-historie; None zolang er geen reeks is."""
    from ..seo import history as history_service
    trend = history_service.site_trend(site_id)
    if trend is None:
        return None
    movers = history_service.page_movers(site_id, limit=3)
    return {
        "site": trend,
        "risers": [{"url": m["page_url"], "query": m["top_query"],
                    "delta_clicks": m["delta_clicks"], "delta_position": m["delta_position"]}
                   for m in movers.get("risers", [])],
        "fallers": [{"url": m["page_url"], "query": m["top_query"],
                     "delta_clicks": m["delta_clicks"], "delta_position": m["delta_position"]}
                    for m in movers.get("fallers", [])],
    }


def project_scores() -> List[Dict[str, Any]]:
    """Rapportcijfer per project (site), opgebouwd uit de vier pijlers."""
    out: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for site in _site_projects(conn):
            names = [site["id"], site["name"]]
            content = _content_pillar(conn, site["id"], site["content_batch_size"] or 1)
            seo = _seo_pillar(conn, site["id"], bool(site["gsc_property"]))
            execution = _execution_pillar(conn, names)
            hygiene = _hygiene_pillar(conn, names, content["needs_work"])
            total = round(content["score"] + seo["score"] + execution["score"] + hygiene["score"], 1)
            out.append({
                "project": site["name"],
                "site_id": site["id"],
                "score": total,
                "grade": round(total / 10, 1),  # rapportcijfer 0-10
                "auto_content": bool(site["auto_content_enabled"]),
                "pillars": {
                    "content": content,
                    "seo": seo,
                    "uitvoering": execution,
                    "hygiene": hygiene,
                },
            })
    # Trend-delta's per site apart ophalen (eigen read-connecties, buiten de
    # bovenstaande lus zodat we niet twee schrijf-connecties genest aanhouden).
    for p in out:
        p["trend"] = _trend_block(p["site_id"])
    out.sort(key=lambda p: p["score"])
    return out


def global_metrics() -> Dict[str, Any]:
    """Project-overstijgende cijfers: funnel, fouten, scheduler, wachtrij."""
    from ..prospecting import funnel as funnel_mod
    with get_conn() as conn:
        errors_24h = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE status = 'error' "
            "AND created_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        delivered_24h = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE status = 'ok' "
            "AND action IN ('task_done','goal_done','live','publicatie','wachtrij_staged') "
            "AND created_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        pending_review_total = conn.execute(
            "SELECT COUNT(*) FROM content_jobs WHERE status = 'pending_review'"
        ).fetchone()[0]

    scheduler_failures: List[Dict[str, Any]] = []
    try:
        from ...scheduler import get_scheduler_status
        for job in get_scheduler_status().get("jobs", []):
            last = job.get("last_run") or {}
            if last.get("status") == "error":
                scheduler_failures.append({"job": job["label"], "error": last.get("error", "")})
    except Exception:
        pass

    try:
        funnel = funnel_mod.funnel_stats()
        inputs = funnel_mod.input_stats(days=7)
    except Exception:
        funnel, inputs = {}, {}

    return {
        "errors_24h": errors_24h,
        "delivered_24h": delivered_24h,
        "pending_review_total": pending_review_total,
        "scheduler_failures": scheduler_failures,
        "funnel": funnel,
        "inputs_7d": inputs,
    }


def snapshot() -> Dict[str, Any]:
    """Het volledige cijferbeeld dat Iris elke ochtend analyseert."""
    return {"projects": project_scores(), "global": global_metrics()}

"""GSC-historie — dagreeksen en trend-delta's per site en per pagina.

`published_pages` bewaart alleen de láátste GSC-sync (elke sync overschrijft);
deze module bewaart de reeks. Twee scopes in één tabel (gsc_history):

- scope='site': echte dagcijfers per site (GSC date-dimensie). Idempotent
  ge-upsert; elke sync vult de laatste 28 dagen bij, de eerste sync 90 dagen.
- scope='page': dagelijkse snapshot per pagina van het trailing 28-dagen-
  aggregaat (zelfde data die published_pages in gaat), gedateerd op sync-dag.

Daaruit volgen de delta's: site-trend (laatste 7 GSC-dagen vs. de 7 ervoor)
en per-pagina-movers (huidige snapshot vs. de snapshot van ~7 dagen terug).
Dit is de meetlat waarmee Iris haar eigen adviezen kan toetsen.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn

# Eerste keer meteen 90 dagen terugvullen, daarna volstaat het 28-dagen-venster.
_FIRST_FILL_DAYS = 90
_DAILY_FILL_DAYS = 28


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_site_daily(site: Dict) -> Dict[str, Any]:
    """Haal de dagreeks van GSC op en upsert die in gsc_history (scope='site')."""
    from . import gsc

    prop = (site.get("gsc_property") or "").strip()
    if not prop or not gsc.is_configured():
        return {"ok": False, "reason": "geen GSC-property of niet geconfigureerd", "rows": 0}

    with get_conn() as conn:
        has_history = conn.execute(
            "SELECT 1 FROM gsc_history WHERE site_id = ? AND scope = 'site' LIMIT 1",
            (site["id"],),
        ).fetchone()
    days = _DAILY_FILL_DAYS if has_history else _FIRST_FILL_DAYS

    try:
        rows = gsc.fetch_daily_performance(prop, days=days)
    except Exception as e:
        return {"ok": False, "reason": f"GSC-fout: {str(e)[:160]}", "rows": 0}

    now = _now()
    with get_conn() as conn:
        for r in rows:
            conn.execute(
                """INSERT INTO gsc_history
                       (id, site_id, scope, page_url, date, clicks, impressions,
                        ctr, position, created_at)
                   VALUES (?, ?, 'site', '', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(site_id, scope, page_url, date) DO UPDATE SET
                       clicks = excluded.clicks, impressions = excluded.impressions,
                       ctr = excluded.ctr, position = excluded.position""",
                (str(uuid.uuid4()), site["id"], r["date"], r["clicks"],
                 r["impressions"], r["ctr"], r["position"], now),
            )
    return {"ok": True, "rows": len(rows), "backfill_days": days}


def record_page_snapshots(site_id: str, by_page: Dict[str, Dict]) -> int:
    """Sla de per-pagina-aggregaten van vandaag op (scope='page').

    `by_page` is dezelfde structuur die sync_page_performance in
    published_pages schrijft: {url: {clicks, impressions, ctr, position, query}}.
    """
    today = _today()
    now = _now()
    saved = 0
    with get_conn() as conn:
        for page_url, m in by_page.items():
            conn.execute(
                """INSERT INTO gsc_history
                       (id, site_id, scope, page_url, date, clicks, impressions,
                        ctr, position, top_query, created_at)
                   VALUES (?, ?, 'page', ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(site_id, scope, page_url, date) DO UPDATE SET
                       clicks = excluded.clicks, impressions = excluded.impressions,
                       ctr = excluded.ctr, position = excluded.position,
                       top_query = excluded.top_query""",
                (str(uuid.uuid4()), site_id, page_url, today,
                 m.get("clicks", 0), m.get("impressions", 0), m.get("ctr", 0.0),
                 m.get("position", 0.0), m.get("query", ""), now),
            )
            saved += 1
    return saved


def site_trend(site_id: str) -> Optional[Dict[str, Any]]:
    """Laatste 7 GSC-dagen vs. de 7 dagen ervoor (uit de site-dagreeks).

    Retourneert None zolang er geen historie is — 'geen data' mag nooit als
    'geen verandering' gelezen worden.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, clicks, impressions, position FROM gsc_history "
            "WHERE site_id = ? AND scope = 'site' ORDER BY date DESC LIMIT 14",
            (site_id,),
        ).fetchall()
    if not rows:
        return None
    recent, previous = [dict(r) for r in rows[:7]], [dict(r) for r in rows[7:14]]

    def _agg(chunk: List[Dict]) -> Dict[str, Any]:
        if not chunk:
            return {"clicks": 0, "impressions": 0, "avg_position": None, "days": 0}
        positions = [c["position"] for c in chunk if c["position"]]
        return {
            "clicks": sum(c["clicks"] for c in chunk),
            "impressions": sum(c["impressions"] for c in chunk),
            "avg_position": round(sum(positions) / len(positions), 1) if positions else None,
            "days": len(chunk),
        }

    cur, prev = _agg(recent), _agg(previous)
    out: Dict[str, Any] = {
        "last7": cur,
        "prev7": prev,
        "delta_clicks": cur["clicks"] - prev["clicks"] if prev["days"] else None,
        "delta_impressions": cur["impressions"] - prev["impressions"] if prev["days"] else None,
        # Positie: lager = beter, dus een negatieve delta is winst.
        "delta_position": (round(cur["avg_position"] - prev["avg_position"], 1)
                           if cur["avg_position"] is not None and prev["avg_position"] is not None
                           else None),
    }
    if prev["days"] and prev["clicks"]:
        out["clicks_pct"] = round((cur["clicks"] - prev["clicks"]) / prev["clicks"] * 100, 1)
    else:
        out["clicks_pct"] = None
    return out


def page_movers(site_id: str, limit: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """Grootste stijgers/dalers per pagina: nieuwste snapshot vs. de snapshot
    van ±7 dagen geleden (dichtstbijzijnde oudere sync-dag)."""
    with get_conn() as conn:
        latest_date = conn.execute(
            "SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page'",
            (site_id,),
        ).fetchone()[0]
        if not latest_date:
            return {"risers": [], "fallers": []}
        base_date = conn.execute(
            "SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page' "
            "AND date <= date(?, '-6 days')",
            (site_id, latest_date),
        ).fetchone()[0]
        if not base_date or base_date == latest_date:
            return {"risers": [], "fallers": []}
        rows = conn.execute(
            """SELECT cur.page_url, cur.top_query,
                      cur.clicks AS clicks, base.clicks AS clicks_prev,
                      cur.position AS position, base.position AS position_prev
               FROM gsc_history cur
               JOIN gsc_history base
                 ON base.site_id = cur.site_id AND base.scope = 'page'
                AND base.page_url = cur.page_url AND base.date = ?
               WHERE cur.site_id = ? AND cur.scope = 'page' AND cur.date = ?""",
            (base_date, site_id, latest_date),
        ).fetchall()

    movers = []
    for r in rows:
        d = dict(r)
        d["delta_clicks"] = d["clicks"] - d["clicks_prev"]
        d["delta_position"] = (round(d["position"] - d["position_prev"], 1)
                               if d["position"] and d["position_prev"] else None)
        if d["delta_clicks"] != 0 or (d["delta_position"] or 0) != 0:
            movers.append(d)
    risers = sorted((m for m in movers if m["delta_clicks"] > 0),
                    key=lambda m: m["delta_clicks"], reverse=True)
    fallers = sorted((m for m in movers if m["delta_clicks"] < 0),
                     key=lambda m: m["delta_clicks"])
    return {
        "risers": risers[:limit],
        "fallers": fallers[:limit],
        "compared": {"latest": latest_date, "base": base_date},
    }


def top_pages(site_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Onze best presterende pagina's uit de laatste dagsnapshot.

    De paginalijst hoort hier en niet uit `published_pages` te komen: die tabel
    bewaart alleen de laatste sync en is in de praktijk leeg. `top_query` gaat
    mee — dat vertelt de aanroeper waar de pagina op rankt.
    """
    with get_conn() as conn:
        latest = conn.execute(
            "SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page'",
            (site_id,),
        ).fetchone()[0]
        if not latest:
            return []
        rows = conn.execute(
            "SELECT page_url, clicks, impressions, position, top_query "
            "FROM gsc_history WHERE site_id = ? AND scope = 'page' AND date = ? "
            "ORDER BY clicks DESC, impressions DESC LIMIT ?",
            (site_id, latest, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def site_series(site_id: str, days: int = 28) -> List[Dict[str, Any]]:
    """Dagreeks voor grafieken: oudste eerst."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, clicks, impressions, ctr, position FROM gsc_history "
            "WHERE site_id = ? AND scope = 'site' ORDER BY date DESC LIMIT ?",
            (site_id, days),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]

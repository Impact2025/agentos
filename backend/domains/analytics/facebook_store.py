"""
Facebook-snapshot store — persisted analytics zodat de UI en Iris NOOIT live
de Graph API hoeven te raken (snel, rate-limit-vrij, offline-bestendig).

Waarom dit bestaat (expert-keuze, 15 aug 2026): de Facebook Agent "Deluxe"
(backend/domains/facebook/agent.py) haalt per call verse data van Meta. Als de
UI en Iris die calls bij elke render/elke prompt zouden doen, slaat drie dingen
stuk:
  1. Rate limits — Graph API is niet gemaakt voor per-request UI-verkeer.
  2. Latentie — een analyse over 28 dagen (posts + insights) duurt seconden.
  3. Stilte bij uitval — als Meta even niet antwoordt, ziet Iris "niets" en
     leest dat als "alles oké" (de duurste leugen, zie insights.py).

Oplossing: één geplande job (facebook_snapshot, in scheduler.py) trekt de
analyse voor alle FB-sites één keer per etmaal en schrijft hem naar
`fb_insights`. UI en Iris lezen uit die tabel — instant, deterministisch, en
met een expliciete `captured_at` zodat "verouderd" nooit op "rustig" lijkt.

De store is faalveilig: een pagina die niet leesbaar is (NO_SCOPE/TOKEN_EXPIRED)
krijgt `status='error'` met de foutmelding, niet een lege rij. Zo blijft de
rest van het portfolio gewoon rapporteren.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from ...shared.database import get_conn

logger = logging.getLogger(__name__)


def ensure_schema() -> None:
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fb_insights (
                site_name    TEXT NOT NULL,
                page_id      TEXT,
                captured_at  TEXT NOT NULL,
                status       TEXT NOT NULL,            -- 'ok' | 'error'
                error        TEXT,
                snapshot     TEXT,                      -- JSON: volledige analyse_page()-uitvoer
                PRIMARY KEY (site_name)
            )"""
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_snapshot(site_name: str, page_id: str, status: str,
                  snapshot: Optional[Dict[str, Any]] = None,
                  error: Optional[str] = None) -> None:
    """Upsert één site-snapshot. `snapshot` is de analyse_page()-dict (of None bij error)."""
    ensure_schema()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO fb_insights (site_name, page_id, captured_at, status, error, snapshot)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(site_name) DO UPDATE SET
                 page_id=excluded.page_id, captured_at=excluded.captured_at,
                 status=excluded.status, error=excluded.error, snapshot=excluded.snapshot""",
            (site_name, page_id, _now(), status, error,
             json.dumps(snapshot, default=str) if snapshot else None),
        )


def get_snapshot(site_name: str) -> Optional[Dict[str, Any]]:
    """Lees één site-snapshot uit de DB (geen Graph API)."""
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT site_name, page_id, captured_at, status, error, snapshot "
            "FROM fb_insights WHERE site_name = ?", (site_name,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("snapshot"):
        try:
            d["snapshot"] = json.loads(d["snapshot"])
        except (ValueError, TypeError):
            d["snapshot"] = None
    return d


def get_all_snapshots() -> List[Dict[str, Any]]:
    """Alle site-snapshots (voor de portfolio-weergave / Iris)."""
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT site_name, page_id, captured_at, status, error, snapshot "
            "FROM fb_insights ORDER BY site_name"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("snapshot"):
            try:
                d["snapshot"] = json.loads(d["snapshot"])
            except (ValueError, TypeError):
                d["snapshot"] = None
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# FB→SEO meetlus: elke geplaatste post loggen (query→artikel-koppeling)
# ─────────────────────────────────────────────────────────────────────────────

def log_fb_post(post_id: str, site_name: str, query: Optional[str] = None,
                article_url: Optional[str] = None, message: Optional[str] = None) -> None:
    """Log een geplaatste FB-post voor de impact-meting (fb_seo_impact.py).

    Best-effort: een logging-fout mag de post zelf nooit breken."""
    try:
        ensure_schema()
        with get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO fb_posts
                   (post_id, site_name, query, article_url, placed_at, message)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (post_id, site_name, query, article_url, _now(), message),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[fb_store] log_fb_post mislukt (niet fataal): %s", e)


def get_fb_posts(site_name: Optional[str] = None,
                 since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alle gelogde FB-posts (voor de impact-meting)."""
    ensure_schema()
    with get_conn() as conn:
        if site_name:
            rows = conn.execute(
                "SELECT * FROM fb_posts WHERE site_name = ? ORDER BY placed_at DESC",
                (site_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fb_posts ORDER BY placed_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Geplande snapshot-run (aangeroepen door scheduler.py)
# ─────────────────────────────────────────────────────────────────────────────

async def snapshot_all_facebook() -> Dict[str, Any]:
    """Analyseer elke site met een facebook_page_id en sla de uitkomst op.

    Eén call per site (parallel via asyncio.gather) — niet per request. De
    scheduler draait dit 1x/etmaal. Geeft een compact verslag terug voor logging.
    """
    from ..seo import sites as sites_service
    from ..facebook import agent as fb

    ensure_schema()
    sites = []
    for s in sites_service.list_sites():
        pid = (s.get("facebook_page_id") or "").strip()
        if pid:
            sites.append((s.get("name", ""), pid))

    if not sites:
        logger.info("[fb_snapshot] geen sites met facebook_page_id — niets te doen")
        return {"ok": True, "sites": 0, "note": "geen FB-sites"}

    async def _one(name: str, pid: str):
        if not fb.is_configured(name):
            save_snapshot(name, pid, "error",
                          error="GEEN_TOKEN: facebook_page_token ontbreekt voor deze pagina")
            return name, "no_token"
        try:
            r = await fb.analyse_page(name, days=28)
        except Exception as e:
            save_snapshot(name, pid, "error", error=f"EXCEPTION: {str(e)[:200]}")
            return name, "exception"
        if r.get("success"):
            save_snapshot(name, pid, "ok", snapshot=r)
            # Wereldklasse-uitbreiding: schrijf ook een historiepunt zodat
            # facebook_trends.py echte tijdreeksen/trends kan bouwen (gratis,
            # geen extra Meta-call — de data zit al in `r`).
            from .facebook_trends import append_history
            try:
                append_history(name, {"captured_at": _now(), "snapshot": r})
            except Exception as e:  # nooit de snapshot zélf laten breken om historie
                logger.warning("[fb_snapshot] historie-append mislukt (niet fataal): %s", e)
            return name, "ok"
        save_snapshot(name, pid, "error", error=r.get("error", "onbekend"))
        return name, "error"

    results = await asyncio.gather(*[_one(n, p) for n, p in sites])
    by_state = {}
    for _, state in results:
        by_state[state] = by_state.get(state, 0) + 1
    logger.info("[fb_snapshot] klaar: %s", by_state)
    return {"ok": True, "sites": len(sites), "states": by_state}

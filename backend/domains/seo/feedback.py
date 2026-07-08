"""SEO-feedback-loop: sluit de cirkel tussen publiceren en presteren.

Zonder feedback weet het systeem niet wat werkt. Deze module haalt
Google Search Console-data (per pagina + top-zoekwoord) op, slaat die op
per gepubliceerde pagina, en leidt daaruit "growth-signals" af:

  - striking_distance: pagina's op positie ~8-20 voor een zoekwoord met
    fatsoenlijke impressies — dichtbij de top 10, dus een interne link of
    een aanvullend artikel tilt ze erin.
  - low_ctr: pagina's met veel impressies maar bijna 0 klikken — de titel/
    snippet trekt niet; een herschrijf (of nieuwe hoek) loont.

Die signalen worden teruggevoed naar de Mission Radar als type 'growth',
zodat de auto-AEO-keten ze oppakt: de agent schrijft een versterkend
artikel of legt een cluster-link. Zo compoundeert de content-machine.

Alles is defensief: geen GSC-config = geen actie, geen crashes.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Positiebereik dat "striking distance" is: net buiten de top 10.
_STRIKING_MIN, _STRIKING_MAX = 8.0, 20.0


def sync_page_performance(site: Dict) -> Dict:
    """Haal GSC per-pagina+query data en sla de beste zoekwoord/positie per
    pagina op in published_pages. Retourneert een kort rapport."""
    from . import gsc

    prop = (site.get("gsc_property") or "").strip()
    if not prop or not gsc.is_configured():
        return {"ok": False, "reason": "geen GSC-property of niet geconfigureerd", "synced": 0}

    try:
        rows = gsc.fetch_page_query_performance(prop, days=28, row_limit=2000)
    except Exception as e:  # netwerk/API-fout mag de sync nooit laten crashen
        return {"ok": False, "reason": f"GSC-fout: {str(e)[:160]}", "synced": 0}

    # Groepeer per pagina: kies het zoekwoord met de meeste impressies.
    by_page: Dict[str, Dict] = {}
    for r in rows:
        page = (r.get("page") or "").rstrip("/")
        if not page:
            continue
        cur = by_page.get(page)
        if cur is None or r.get("impressions", 0) > cur["impressions"]:
            by_page[page] = {
                "query": r.get("query", ""),
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 0.0),
            }

    synced = 0
    now = _now()
    with get_conn() as conn:
        for page, m in by_page.items():
            # match op URL suffix: published_pages.url eindigt op /<slug>/
            slug = page.rsplit("/", 2)[-2] if page.count("/") >= 2 else page.rsplit("/", 1)[-1]
            conn.execute(
                """UPDATE published_pages SET
                       gsc_clicks = ?, gsc_impressions = ?, gsc_ctr = ?,
                       gsc_position = ?, gsc_top_query = ?, gsc_synced_at = ?
                   WHERE site_id = ? AND (url LIKE ? OR slug = ?)""",
                (m["clicks"], m["impressions"], m["ctr"], m["position"],
                 m["query"], now, site["id"], f"%{slug}/", slug),
            )
            synced += 1
    return {"ok": True, "synced": synced, "pages": len(by_page)}


def collect_growth_signals(site: Dict) -> List[Dict]:
    """Leid growth-kansen af uit de laatst gesyncte GSC-data per pagina."""
    out: List[Dict] = []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slug, title, url, gsc_clicks, gsc_impressions, gsc_ctr, "
            "gsc_position, gsc_top_query FROM published_pages "
            "WHERE site_id = ? AND gsc_synced_at != ''",
            (site["id"],),
        ).fetchall()
    for r in rows:
        r = dict(r)
        pos = r.get("gsc_position") or 0
        imps = r.get("gsc_impressions") or 0
        clicks = r.get("gsc_clicks") or 0
        q = (r.get("gsc_top_query") or "").strip()
        if not q or imps < 20:
            continue  # te weinig data om iets zinnigs uit af te leiden
        # Striking distance: net buiten top 10, maar wel gevonden.
        if _STRIKING_MIN <= pos <= _STRIKING_MAX:
            out.append({
                "kind": "striking_distance",
                "slug": r["slug"], "title": r["title"], "url": r["url"],
                "query": q, "position": pos, "impressions": imps,
                "clicks": clicks,
            })
        # Lage CTR ondanks veel impressies: snippet/titel trekt niet.
        elif imps >= 200 and clicks <= max(1, imps * 0.005):
            out.append({
                "kind": "low_ctr",
                "slug": r["slug"], "title": r["title"], "url": r["url"],
                "query": q, "position": pos, "impressions": imps,
                "clicks": clicks,
            })
    return out


def feed_radar(site: Dict, project: str = "") -> int:
    """Zet growth-signals terug in de Mission Radar als signalen van type
    'growth'. Retourneert het aantal nieuw ingevoerde signalen.

    Idempotent: een signaal met dezelfde (project, url) bestaat al → overslaan.
    """
    from ..radar import models as radar_models

    signals = collect_growth_signals(site)
    if not signals:
        return 0
    radar_models.ensure_schema()
    inserted = 0
    now = _now()
    with get_conn() as conn:
        for s in signals:
            url = f"gsc://growth/{s['slug']}"
            existing = conn.execute(
                "SELECT id FROM radar_signals WHERE project = ? AND url = ?",
                (project, url),
            ).fetchone()
            if existing:
                continue
            sig_id = f"growth-{s['slug']}-{abs(hash(s['query'])) % 10**8}"
            title = (f"Boost-kans: '{s['query']}' staat #{int(s['position'])}"
                     if s["kind"] == "striking_distance"
                     else f"CTR-kans: '{s['query']}' veel impressies, weinig klikken")
            angle = (f"Pagina rankt op #{int(s['position'])} voor '{s['query']}' "
                     f"({s['impressions']} impressies). Een interne link vanuit een "
                     f"sterke pagina of een aanvullend artikel duwt hem de top 10 in."
                     if s["kind"] == "striking_distance"
                     else f"Pagina krijgt {s['impressions']} impressies voor '{s['query']}' "
                     f"maar bijna 0 klikken. Herschrijf titel/snippet of bied een scherpere hoek.")
            conn.execute(
                """INSERT INTO radar_signals
                   (id, watch_id, project, keyword, title, url, source, snippet,
                    signal_score, ai_angle, status, scanned_at, created_at, updated_at)
                   VALUES (?, '', ?, ?, ?, ?, 'gsc', ?, ?, ?, 'new', ?, ?, ?)""",
                (sig_id, project, s["query"], title, url, angle,
                 min(95, 60 + int(s["impressions"] / 50)), angle, now, now, now),
            )
            inserted += 1
        conn.commit()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    return inserted


def run_daily_gsc_sync() -> Dict:
    """Scheduler-entry: sync GSC-performance voor elke site met een
    gsc_property en voer growth-signalen terug naar de Radar. Defensief:
    als er geen GSC is, doet het niets (geen crash, geen log-ruis)."""
    from ..seo import sites as sites_service

    report = {"sites": 0, "synced": 0, "growth_signals": 0, "errors": []}
    try:
        all_sites = sites_service.list_sites()
    except Exception as e:
        report["errors"].append(f"sites-lijst: {str(e)[:120]}")
        return report
    for s in all_sites:
        s = s if isinstance(s, dict) else dict(s)
        if not (s.get("gsc_property") or "").strip():
            continue
        try:
            res = sync_page_performance(s)
            report["sites"] += 1
            report["synced"] += res.get("synced", 0)
            # Voed de Radar met hetzelfde project als de site-naam (radar
            # matched op project; growth-signalen krijgen dat als project).
            gs = feed_radar(s, project=(s.get("name") or ""))
            report["growth_signals"] += gs
        except Exception as e:
            report["errors"].append(f"{s.get('name', '?')}: {str(e)[:120]}")
    if report["errors"]:
        logger.warning("[gsc-sync] fouten: %s", report["errors"])
    else:
        logger.info(
            "[gsc-sync] %d sites, %d pagina's gesynct, %d growth-signalen",
            report["sites"], report["synced"], report["growth_signals"],
        )
    return report

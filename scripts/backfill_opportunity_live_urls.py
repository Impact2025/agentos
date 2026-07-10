"""
Backfill: koppel bestaande 'published' Demand-Engine-kansen aan hun echte
live-URL op de site.

De write-and-publish pipeline schreef vóór de live_url-feature (2026-07-09)
de status wel op 'published', maar liet de live_url leeg. Daardoor toonde de
Kansen-card die kansen als "Gepubliceerd (geen live-link)" in plaats van 🟢 LIVE.

Dit script probeert per 'published' kans (met lege live_url) de meest
waarschijnlijke URL te achterhalen uit de site-base_url + de geslugifyde
query, controleert of die pagina écht HTTP 200 geeft, en schrijft de URL
(mét published_at) alleen terug als hij live is. Niets wordt aangeraakt als
de pagina niet gevonden wordt — zo krijg je geen valse LIVE-badges.

Gebruik:
    python scripts/backfill_opportunity_live_urls.py            # echte run
    python scripts/backfill_opportunity_live_urls.py --dry-run  # alleen tonen
"""
import argparse
import httpx
import os
import sys
from datetime import datetime, timezone

# Zorg dat het project-root op het pad staat (draait vanuit scripts/ of elders).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.domains.seo import engine as demand_engine
from backend.domains.seo import sites as sites_service
from backend.domains.publish import content_pipeline as content_pipeline


def _slug(q: str) -> str:
    return content_pipeline.slugify_title(q or "")


def _candidate_urls(base_url: str, query: str, action: str) -> list[str]:
    """Mogelijke live-paden, meest waarschijnlijk eerst.

    - nieuwe-content  -> /blog/<slug>  dan  /<slug>
    - re-optimaliseren -> /<slug>  dan  /blog/<slug>  (bestaande pagina's
      staan vaak op de root of onder /kennisbank)
    """
    slug = _slug(query)
    base = (base_url or "").rstrip("/")
    if action == "re-optimaliseren":
        paths = [f"/{slug}", f"/blog/{slug}", f"/kennisbank/{slug}"]
    else:
        paths = [f"/blog/{slug}", f"/{slug}", f"/kennisbank/{slug}"]
    return [base + p for p in paths if slug]


def _first_live(candidates: list[str], timeout: float = 8.0) -> str | None:
    for url in candidates:
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            if r.status_code < 400:
                return url
        except Exception:
            continue
    return None


def backfill(dry_run: bool = False) -> dict:
    published = [
        o for o in demand_engine.list_opportunities(status="published")
        if not (o.get("live_url") or "").strip()
    ]
    results = {"checked": 0, "linked": 0, "skipped": 0, "details": []}
    for opp in published:
        results["checked"] += 1
        site = sites_service.get_site(opp["site_id"])
        base_url = (site or {}).get("base_url", "") if site else ""
        candidates = _candidate_urls(base_url, opp.get("query", ""), opp.get("action", ""))
        live = _first_live(candidates)
        if live:
            results["linked"] += 1
            results["details"].append({
                "id": opp["id"], "query": opp.get("query"),
                "live_url": live, "action": "WOULD_LINK" if dry_run else "LINKED",
            })
            if not dry_run:
                demand_engine.update_opportunity(
                    opp["id"],
                    status="published",
                    live_url=live,
                    published_at=datetime.now(timezone.utc).isoformat(),
                )
        else:
            results["skipped"] += 1
            results["details"].append({
                "id": opp["id"], "query": opp.get("query"),
                "live_url": None, "action": "NO_LIVE_PAGE_FOUND",
            })
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Alleen tonen, niets schrijven")
    args = ap.parse_args()
    out = backfill(dry_run=args.dry_run)
    print(f"Dry-run: {args.dry_run}")
    print(f"Gecontroleerd: {out['checked']} | Gekoppeld: {out['linked']} | Overgeslagen: {out['skipped']}")
    for d in out["details"]:
        print(f"  [{d['action']}] {d['query']!r} -> {d['live_url']}")

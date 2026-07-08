#!/usr/bin/env python3
"""Bewaardvoorjou SEO-content-blitz: draai de content-pipeline direct aan
(voor de lokale Hermes nu de primaire backend is) en rapporteer de queue."""
import sys, time, asyncio
sys.path.insert(0, ".")
from backend.domains.publish import content_pipeline
from backend.domains.seo import sites as sites_service
from backend.shared.database import get_conn


async def main():
    site = sites_service.get_site("1e3e5bc6-982e-489f-bfb9-22313b33edb4")
    print(f"[blitz] start content-batch voor {site['name']} (count=3)...")
    try:
        job_ids = await content_pipeline.run_content_batch(site, count=3)
        print(f"[blitz] aangemaakte jobs: {job_ids}")
    except Exception as e:
        print(f"[blitz] fout: {e}")
        import traceback; traceback.print_exc()

    # wacht even en toon queue
    await asyncio.sleep(3)
    with get_conn() as c:
        rows = c.execute(
            "SELECT status, keyword, title FROM content_jobs "
            "WHERE site_id=? ORDER BY created_at DESC LIMIT 10",
            (site["id"],),
        ).fetchall()
    print(f"\n[blitz] content-queue ({len(rows)} recent):")
    for r in rows:
        print(f"  [{r[0]}] {r[1]} -> {str(r[2])[:50]}")


if __name__ == "__main__":
    asyncio.run(main())

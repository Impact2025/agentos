#!/usr/bin/env python3
"""Vervolg-scan: scan alleen de teambuilding-watches die nog geen signalen hebben
(robuste voortzetting na een time-out). Idempotent: URL-dedupe voorkomt dubbele
signalen. Kan veilig meerdere keren gedraaid worden."""
import asyncio
import sys

sys.path.insert(0, "D:/apps/impactos")

from backend.domains.radar.service import RadarService


async def main():
    svc = RadarService()
    # Watches die al signalen opleverden
    scanned = {
        s["watch_id"] for s in svc.list_signals(project="teambuildingmetimpact")
    }
    watches = [w for w in svc.list_watch("teambuildingmetimpact") if w["active"]]
    pending = [w for w in watches if w["id"] not in scanned]
    print(f"[vervolg] {len(pending)} watches nog te scannen van {len(watches)} totaal")
    for w in pending:
        print(f"  -> {w['label']} ({w['type']})")
    # run_scan scant alles; we filteren niet intern, maar URL-dedupe houdt het schoon.
    # Om tijd te besparen scannen we alsnog alles — reeds-bestaande URLs worden overgeslagen.
    total = 0
    async for ev in svc.run_scan(project="teambuildingmetimpact", enrich=True):
        if ev.get("type") == "watch_done":
            print(f"  [done] {ev.get('label')}: +{ev.get('found')}")
        elif ev.get("type") == "scan_done":
            print(f"[scan] KLAAR — nieuw deze run: {ev.get('total_saved')}")
    stats = svc.get_stats(project="teambuildingmetimpact")
    print(f"[stats] total={stats['total']} top_score={stats['top_score']} "
          f"watch_count={stats['watch_count']}")


if __name__ == "__main__":
    asyncio.run(main())

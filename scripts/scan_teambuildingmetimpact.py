#!/usr/bin/env python3
"""Voltooi de Mission Radar scan voor teambuildingmetimpact in-process (zonder
SSE-fragiliteit). Draait elke actieve watch die nog niet gescand is tot het eind,
zodat de signalen-geschiedenis compleet wordt. Schrijft een kort rapport naar stdout."""
import asyncio
import sys

sys.path.insert(0, "D:/apps/agentos")

from backend.domains.radar.service import RadarService
from backend.domains.radar import scorer


async def main():
    svc = RadarService()
    print("[scan] start volledige scan voor teambuildingmetimpact (enrich=True)...")
    total = 0
    async for ev in svc.run_scan(project="teambuildingmetimpact", enrich=True):
        t = ev.get("type")
        if t == "watch_done":
            print(f"  [done] {ev.get('label')}: +{ev.get('found')} signalen (top {ev.get('top_score')})")
            total += ev.get("found", 0)
        elif t == "auto_aeo":
            print(f"  [auto_aeo] {ev.get('count')} signaal(len) aangevallen")
        elif t == "scan_done":
            print(f"[scan] KLAAR — totaal nieuw deze run: {ev.get('total_saved')}")
    stats = svc.get_stats(project="teambuildingmetimpact")
    print(f"[stats] total={stats['total']} new={stats['new']} "
          f"targeted={stats['targeted']} converted={stats['converted']} "
          f"top_score={stats['top_score']} watch_count={stats['watch_count']}")


if __name__ == "__main__":
    asyncio.run(main())

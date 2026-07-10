#!/usr/bin/env python3
"""Rescan teambuildingmetimpact in-process met de VERBETERDE scorer + verbrede
concurrent-queries + nieuwe RSS-feeds. Draait standalone (laadt de nieuwe modules
direct, geen backend-restart nodig). Schrijft signalen naar dezelfde DB."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.domains.radar import service as radar_service


async def main():
    svc = radar_service.RadarService()
    total = 0
    async for ev in svc.run_scan(project="teambuildingmetimpact", enrich=True):
        t = ev.get("type")
        if t == "watch_done":
            print(f"  [ok] {ev['label']:30} found={ev['found']} top={ev['top_score']}")
        elif t == "watch_error":
            print(f"  [ERR] {ev['label']}: {ev['error']}")
        elif t == "scan_done":
            total = ev.get("total_saved", 0)
            print(f"[scan_done] total_saved={total}")
        elif t == "auto_aeo":
            print(f"[auto_aeo] {ev.get('count')} signalen aangevallen")
    # eindstatistiek
    stats = svc.get_stats("teambuildingmetimpact")
    print(f"\n[STATS] total={stats['total']} top={stats['top_score']} watches={stats['watch_count']}")
    # hoeveel boven Obsidiaan-drempel (70)?
    high = svc.list_signals(project="teambuildingmetimpact", min_score=70, limit=50)
    print(f"[STATS] signalen >=70: {len(high)}")
    for h in high[:10]:
        print(f"   {h['signal_score']:5} {h['source']:8} {h['title'][:55]}")


if __name__ == "__main__":
    asyncio.run(main())

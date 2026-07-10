#!/usr/bin/env python3
"""Mission Radar — eenmalige sky-scan voor EEN project (Pootgelukkig).

Robuuste in-process driver: roept svc.run_scan(project='pootgelukkig', enrich=True)
aan, print per-watch voortgang, en commit per watch (overleeft disconnects).
Idempotent op URL, dus een onderbroken run gewoon opnieuw starten.

Run (vanuit repo root, background + notify aanbevolen voor lange runs):
  .venv/Scripts/python scripts/scan_pootgelukkig_inprocess.py
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.domains.radar.service import RadarService


async def main():
    svc = RadarService()
    svc.get_service() if hasattr(svc, "get_service") else None
    total = 0
    async for ev in svc.run_scan(project="pootgelukkig", enrich=True):
        etype = ev.get("type")
        if etype == "watch_scan_done":
            print(f"  [watch] {ev.get('label','?')}: +{ev.get('saved',0)} signalen")
        elif etype == "scan_done":
            total = ev.get("total_saved", 0)
    print(f"\n[radar] Pootgelukkig scan klaar: {total} nieuwe signalen")


if __name__ == "__main__":
    asyncio.run(main())

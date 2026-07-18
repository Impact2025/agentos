"""Mission Radar — wekelijkse sky-scan (cron entry point).

Draait scan_the_skies() over ALLE projecten (bewaardvoorjou, bijeen,
WeAreImpact, ...) en logt het aantal nieuwe signalen. Stdout wordt door de
Hermes-cron als bericht afgeleverd. Idempotent: schrijft niets dubbel (de
service dedupe't op URL).

Run: .venv/Scripts/python scripts/radar_weekly_scan.py  (vanuit repo root)
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.domains.radar.service import scan_the_skies


async def main():
    saved = await scan_the_skies() or 0
    print(f"[radar] wekelijkse sky-scan klaar: {saved} nieuwe signalen (alle projecten)")


if __name__ == "__main__":
    asyncio.run(main())

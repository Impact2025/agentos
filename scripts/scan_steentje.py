"""Draai één Mission Radar-scan voor project 'steentjebijsteentje' en rapporteer."""
import asyncio, sys
from pathlib import Path
IMPACTOS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(IMPACTOS))
from backend.domains.radar import service as radar  # noqa


async def main():
    svc = radar.get_service()
    saved = 0
    async for ev in svc.run_scan(project="steentjebijsteentje", enrich=True):
        if ev.get("type") == "saved":
            saved += 1
        elif ev.get("type") == "done":
            print(f"Scan klaar: {ev.get('saved', 0)} nieuwe signalen, "
                  f"{ev.get('total', 0)} bekeken.")
    print(f"Totaal opgeslagen in deze run: {saved}")


if __name__ == "__main__":
    asyncio.run(main())

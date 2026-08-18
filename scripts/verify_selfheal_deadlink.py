"""Bewijs dat de selfheal-ronde én de triage-knop de publish_failed+link-dood
jobs nu wél oppakken (tegen de test-DB, geen live data geraakt).

  cd D:/apps/agentos/backend
  AGENTOS_DB_PATH=D:/apps/agentos/data/agentos_test.db PYTHONPATH=D:/apps/agentos \
    .venv/Scripts/python.exe D:/apps/agentos/scripts/verify_selfheal_deadlink.py
"""
from __future__ import annotations
import asyncio, os, sqlite3

DB = os.environ.get("AGENTOS_DB_PATH", "data/agentos_test.db")
os.environ["AGENTOS_DB_PATH"] = DB

from backend.shared.database import get_conn
from backend.domains.iris import selfheal, triage


def _count():
    with get_conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM content_jobs WHERE status='publish_failed' "
            "AND error LIKE '%link-dood%'").fetchone()[0]


async def main() -> None:
    print(f"Test-DB: {DB}")
    print(f"publish_failed+link-dood vóór: {_count()}")

    # 1) selfheal-ronde moet de jobs oppakken als 'content_job'-gevallen
    print("\n--- run_selfheal() ---")
    res = await selfheal.run_selfheal()
    for r in res.get("results", []):
        if r.get("action") == "publish_failed":
            print(f"  {r['id'][:12]} -> {r.get('result')} ({r.get('class')})")
    print(f"  healed={res['healed']} escalated={res['escalated']}")

    # 2) triage-knop op één content_job moet direct repareren
    print("\n--- triage.analyze_and_fix(kind=content_job) ---")
    with get_conn() as c:
        jid = c.execute(
            "SELECT id FROM content_jobs WHERE status='publish_failed' "
            "AND error LIKE '%link-dood%' LIMIT 1").fetchone()
    if jid:
        uit = await triage.analyze_and_fix(jid[0], kind="content_job")
        print(f"  {jid[0][:12]} -> {uit}")
    else:
        print("  (geen publish_failed meer — selfheal heeft ze al verwerkt)")

    print(f"\npublish_failed+link-dood ná: {_count()}")


if __name__ == "__main__":
    asyncio.run(main())

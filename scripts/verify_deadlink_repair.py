"""Verifieer de dode-link-reparatie tegen een aparte test-DB (Pitfall #0:
raak de live data niet). Roept repair.repareer_dode_link_in_job aan op de
3 publish_failed-jobs in de test-DB en toont het resultaat.

Gebruik:
  cd D:/apps/impactos
  IMPACTOS_DB_PATH=D:/apps/impactos/data/agentos_test.db \
    .venv/Scripts/python.exe scripts/verify_deadlink_repair.py
"""
from __future__ import annotations
import asyncio, os, sqlite3, sys

DB = os.environ.get("IMPACTOS_DB_PATH", "data/agentos_test.db")
os.environ["IMPACTOS_DB_PATH"] = DB  # vóór elke import die de DB bindt

from backend.shared.database import get_conn
from backend.domains.publish import repair


def _show(job_id: str) -> None:
    with get_conn() as c:
        r = c.execute(
            "SELECT status, error, blog_html FROM content_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not r:
            print("   (job verdwenen)")
            return
        dead = [h for h in __import__("re").findall(r'href="([^"]+)"', r["blog_html"] or "")
                if "rijksoverheid" in h or "zonmw" in h]
        print(f"   -> status={r['status']} | error={r['error'][:40]!r}")
        print(f"   -> gov-links nu: {dead}")


async def main() -> None:
    with get_conn() as c:
        jobs = [dict(r) for r in c.execute(
            "SELECT id,title FROM content_jobs WHERE status='publish_failed' "
            "AND error LIKE '%link-dood%'")]
    print(f"Test-DB: {DB}")
    print(f"publish_failed+link-dood jobs gevonden: {len(jobs)}\n")
    for j in jobs:
        print(f"=== {j['id'][:12]} | {j['title'][:50]}")
        _show(j["id"])
        uit = await repair.repareer_dode_link_in_job(j["id"])
        print(f"   reparatie: {uit}")
        if uit.get("ok"):
            _show(j["id"])
        print()


if __name__ == "__main__":
    asyncio.run(main())

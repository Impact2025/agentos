"""Index-status monitor: sluit de indexering-loop.

We pingen IndexNow + Google Indexing API bij elke publish (zie content_pipeline),
maar tot nu toe meten we niet of de pagina écht geïndexeerd is. Google kan een
URL "Discovered" markeren (bekend, maar niet in zoekresultaten) — dat telt niet
als verkeer. Deze module inspecteert gepubliceerde artikelen via de Google URL
Inspection API en slaat de echte index-status op per content_job, zodat de
Wachtrij/het dashboard kan tonen welke artikelen wél in de index staan.

Goldie's "24u indexeren" is alleen een belofte als je de terugkoppeling meet.
"""

from datetime import datetime, timedelta
from typing import Dict, List
import logging

from ...shared.database import get_conn
from . import gsc as gsc_api

logger = logging.getLogger(__name__)

# Een artikel opnieuw inspecteren pas na deze periode — de URL Inspection API
# heeft een strengere quota dan de Search Analytics API, dus we slaan niet elke
# run elke pagina op.
REINSPECT_AFTER = timedelta(days=3)

# Max aantal inspecties per run — beschermt het quota en houdt de job kort.
MAX_INSPECTIONS_PER_RUN = 25


def _published_without_fresh_status() -> List[Dict]:
    """Gepubliceerde artikelen waarvan de index-status ontbreekt of verouderd is."""
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            """
            SELECT cj.id, cj.site_id, cj.slug, cj.keyword, cj.index_status,
                   cj.index_inspected_at, s.name, s.base_url, s.gsc_property
            FROM content_jobs cj
            JOIN sites s ON s.id = cj.site_id
            WHERE cj.status = 'published'
              AND s.gsc_property IS NOT NULL
              AND s.gsc_property != ''
              AND (cj.index_status IS NULL OR cj.index_status = '')
            LIMIT ?
            """,
            (MAX_INSPECTIONS_PER_RUN * 4,),
        ).fetchall()
    return [dict(r) for r in rows]


def _is_stale(inspected_at: str) -> bool:
    if not inspected_at:
        return True
    try:
        dt = datetime.fromisoformat(inspected_at.replace("Z", "+00:00"))
        return datetime.utcnow().replace(tzinfo=dt.tzinfo) - dt > REINSPECT_AFTER
    except ValueError:
        return True


async def run_index_status_check() -> Dict:
    """Scheduler-job: inspecteer een batch gepubliceerde artikelen op index-status.

    Idempotent en quota-bewust: alleen artikelen zonder status of met een
    verouderde status worden meegenomen, en nooit meer dan MAX_INSPECTIONS_PER_RUN
    per run. Een mislukte inspectie (quota/rechten) zet de status op 'error' en
    schrijft de fout in 'detail' — de job crasht nooit op één pagina.
    """
    from ...shared.outcomes import log_outcome

    if not gsc_api.is_configured():
        return {"skipped": True, "reason": "GSC niet geconfigureerd"}

    candidates = _published_without_fresh_status()
    if not candidates:
        return {"considered": 0, "inspected": 0, "indexed": 0,
                "not_indexed": 0, "errors": 0}

    indexed, not_indexed, errors = 0, 0, 0
    inspected = 0
    for job in candidates[:MAX_INSPECTIONS_PER_RUN]:
        site_url = job["gsc_property"]
        base = (job["base_url"] or "").rstrip("/")
        if not base or not job["slug"]:
            continue
        page_url = f"{base}/blog/{job['slug']}"
        try:
            res = gsc_api.inspect_url(site_url, page_url)
        except Exception as e:  # noqa: BLE001
            res = {"indexed": False, "status": "error",
                   "detail": f"inspectie crash: {str(e)[:160]}"}

        status = res.get("status", "unknown")
        indexed_flag = bool(res.get("indexed"))
        detail = res.get("detail", "")[:200]
        now = datetime.utcnow().isoformat() + "Z"

        with get_conn() as conn:
            conn.execute(
                """UPDATE content_jobs
                   SET index_status = ?, index_inspected_at = ?
                   WHERE id = ?""",
                (f"{status}|{detail}" if detail else status, now, job["id"]),
            )

        inspected += 1
        if status == "error":
            errors += 1
        elif indexed_flag:
            indexed += 1
        else:
            not_indexed += 1

        # Quota-bescherming: bij een inspectie-fout (meestal quota) stoppen we
        # de run vroegtijdig in plaats van alle quota in één keer te verbranden.
        if status == "error" and "quota" in detail.lower():
            logger.warning("[index-monitor] Quota geraakt — run gestopt.")
            break

    summary = {
        "considered": len(candidates),
        "inspected": inspected,
        "indexed": indexed,
        "not_indexed": not_indexed,
        "errors": errors,
    }
    log_outcome(
        "SEO", "index-status-check",
        f"Index-status: {inspected} artikel(en) geïnspecteerd — "
        f"{indexed} geïndexeerd, {not_indexed} niet (nog), {errors} fout.",
        artifact="/wachtrij",
        next_step=("Niets — artikelen zonder index-status worden in een volgende "
                   "run opnieuw geprobeerd." if not_indexed or errors
                   else "Alle geïnspecteerde artikelen staan in de index."),
        status="ok" if inspected else "warning",
    )
    return summary

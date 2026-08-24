"""
SEO-loop router — REST voor de Loop Engineering SEO-use-case.

  GET  /api/seo-loops              → recente runs (voor de activiteiten-lijst).
  GET  /api/seo-loops/sites        → sites met GSC-data + hun huidige KPI.
  GET  /api/seo-loops/{site_id}/kpi        → objectieve meting (nu vs. vorig venster).
  GET  /api/seo-loops/{site_id}/opportunities → striking-distance targets (Build-input).
  GET  /api/seo-loops/{site_id}/history     → run-geschiedenis uit het leerbestand.
  POST /api/seo-loops/{site_id}/run         → start een run (live of dry_run).

De live-feed van de lopende run (concept/score/feedback) deelt de globale
event_bus met /api/loops/stream; de frontend filtert op 'loop_*'-events.
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import seo_loop as seo_service
from . import service as loop_service
from ...domains.delegate import event_bus

router = APIRouter(prefix="/api/seo-loops", tags=["seo-loops"])


class RunRequest(BaseModel):
    dry_run: bool = False
    window_days: int = 28
    focus_striking_distance: bool = True


def _site_rows():
    from ...shared.database import get_conn
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, base_url, gsc_property FROM sites "
            "WHERE gsc_property <> ''"
        ).fetchall()]


def _site_run_count(site_id: str) -> int:
    """Aantal runs voor deze site die échte verbeter-voorstellen opleverden
    (live-runs mét proposals). Dit bepaalt de default-site van de tab: die
    toont bij openen meteen gevulde geschiedenis + voorstellen in plaats van
    een lege site."""
    try:
        return sum(1 for r in seo_service.run_history(site_id) if r.get("proposals"))
    except Exception:  # noqa: BLE001
        return 0


@router.get("")
def list_runs():
    return seo_service.list_seo_loop_runs()


@router.get("/sites")
def sites_with_kpi():
    out = []
    for s in _site_rows():
        kpi = {}
        try:
            kpi = seo_service.get_site_kpi(s["id"])
        except Exception:  # noqa: BLE001
            pass
        out.append({
            "site_id": s["id"],
            "name": s["name"],
            "base_url": s["base_url"],
            "kpi": kpi,
            "runs": _site_run_count(s["id"]),
        })
    # Site met de meeste runs eerst (zodat de tab bij openen meteen gevulde
    # geschiedenis + voorstellen toont in plaats van een lege site).
    out.sort(key=lambda x: (x["runs"], x["name"]), reverse=True)
    return out


@router.get("/{site_id}/kpi")
def site_kpi(site_id: str, window_days: int = 28):
    try:
        return seo_service.get_site_kpi(site_id, window_days=window_days)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{site_id}/opportunities")
def opportunities(site_id: str, lo: float = 11.0, hi: float = 30.0, limit: int = 25):
    return seo_service.striking_distance_opportunities(site_id, lo=lo, hi=hi, limit=limit)


@router.get("/{site_id}/history")
def history(site_id: str):
    return seo_service.run_history(site_id)


@router.post("/{site_id}/run")
async def run(site_id: str, body: RunRequest):
    # Keer direct terug; de run draait als achtergrondtaak en logt naar
    # activity_log + schrijft het leerbestand. Publiceert loop_events.
    task = asyncio.create_task(
        seo_service.run_seo_loop(
            site_id,
            dry_run=body.dry_run,
            window_days=body.window_days,
            focus_striking_distance=body.focus_striking_distance,
        )
    )

    async def _done(t):
        try:
            res = t.result()
            event_bus.publish({
                "type": "seo_loop_done", "site_id": site_id,
                "kpi_score": res["kpi"]["kpi_score"],
                "passed": res["passed"],
                "dry_run": res["dry_run"],
            })
        except Exception as exc:  # noqa: BLE001
            event_bus.publish({
                "type": "seo_loop_error", "site_id": site_id, "error": str(exc),
            })

    task.add_done_callback(
        lambda t: asyncio.get_event_loop().create_task(_done(t))
    )
    return {"site_id": site_id, "started": True, "dry_run": body.dry_run}

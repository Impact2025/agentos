"""Social Auto-Poster — configuratie-API.

  GET  /api/social-auto/{project}/status   → auto_post aan/uit + platforms + laatste runs
  POST /api/social-auto/{project}/enable   → zet auto_post AAN (default FB+IG)
  POST /api/social-auto/{project}/disable  → zet auto_post UIT
  POST /api/social-auto/{project}/run-now → één run direct (handmatige test/trigger)

Auto-post staat NOOIT aan tenzij de mens dit expliciet aanzet (enable). De engine
in backend/shared/social_auto.py respecteert de vlag in de sites-tabel.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from ...shared.database import get_conn
from ...domains.seo import sites as sites_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social-auto", tags=["social-auto"])

AUTO_PLATFORMS = ("facebook", "instagram")


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def _find_site_id(name: str) -> Optional[str]:
    for s in sites_service.list_sites():
        if _norm(s.get("name", "")) == _norm(name):
            return s["id"]
    return None


def _read_config(site_id: str) -> dict:
    full = sites_service.get_site(site_id) or {}
    enabled = bool(int(full.get("auto_social_enabled") or 0))
    plats = (full.get("auto_social_platforms") or "").strip()
    platforms = [p.strip().lower() for p in plats.split(",") if p.strip()] if plats else (AUTO_PLATFORMS if enabled else [])
    return {"enabled": enabled, "platforms": platforms}


@router.get("/{project}/status")
def status(project: str):
    sid = _find_site_id(project)
    if not sid:
        raise HTTPException(404, f"Project '{project}' staat niet in de sites-tabel")
    cfg = _read_config(sid)
    # Check of de social-services ook écht geconfigureerd zijn (tokens aanwezig).
    configured = {}
    try:
        from ...shared import facebook as fb, instagram as ig
        configured["facebook"] = fb.is_configured(project)
        configured["instagram"] = ig.is_configured(project)
    except Exception:
        pass
    with get_conn() as conn:
        last = conn.execute(
            "SELECT id, project, action, detail, status, created_at FROM activity_log "
            "WHERE action LIKE 'social_auto%' AND project=? ORDER BY created_at DESC LIMIT 3",
            (project,),
        ).fetchall()
    return {"project": project, "config": cfg, "token_configured": configured,
            "recent_runs": [dict(r) for r in last]}


@router.post("/{project}/enable")
def enable(project: str, body: dict = {}):
    sid = _find_site_id(project)
    if not sid:
        raise HTTPException(404, f"Project '{project}' staat niet in de sites-tabel")
    platforms = body.get("platforms") or list(AUTO_PLATFORMS)
    platforms = [p for p in platforms if p in AUTO_PLATFORMS]
    if not platforms:
        platforms = list(AUTO_PLATFORMS)
    with get_conn() as conn:
        conn.execute(
            "UPDATE sites SET auto_social_enabled=1, auto_social_platforms=? WHERE id=?",
            (",".join(platforms), sid),
        )
    return {"success": True, "enabled": True, "platforms": platforms,
            "note": "Auto-post staat nu AAN. De dagelijkse run plaatst goedgekeurde packs op "
                    + ", ".join(platforms) + ". Zet uit met /disable."}


@router.post("/{project}/disable")
def disable(project: str):
    sid = _find_site_id(project)
    if not sid:
        raise HTTPException(404, f"Project '{project}' staat niet in de sites-tabel")
    with get_conn() as conn:
        conn.execute("UPDATE sites SET auto_social_enabled=0 WHERE id=?", (sid,))
    return {"success": True, "enabled": False,
            "note": "Auto-post staat nu UIT. Er wordt niets meer automatisch geplaatst."}


@router.post("/{project}/run-now")
async def run_now(project: str):
    from ...shared import social_auto as sa
    try:
        result = await sa.run_auto_social(project)
    except Exception as e:
        raise HTTPException(400, str(e)[:300])
    return {"success": True, **result}

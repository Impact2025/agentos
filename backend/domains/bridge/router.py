"""
Bridge API — status en handmatig synchroniseren met de cloud-companion.

  GET  /api/bridge/status    → geconfigureerd? laatste sync-uitslag
  POST /api/bridge/sync-now  → draai één sync-cyclus direct
"""
from fastapi import APIRouter, HTTPException

from . import service

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


@router.get("/status")
def bridge_status():
    return {
        "enabled": service.enabled(),
        "config_state": service.config_state(),
        "remote_url": service.remote_url(),
        "last_sync": service.last_sync(),
        "failure_streak": service.failure_streak(),
    }


@router.post("/sync-now")
async def sync_now():
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.sync_once()

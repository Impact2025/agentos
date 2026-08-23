"""
Bridge API — status en handmatig synchroniseren met de cloud-companion.

  GET  /api/bridge/status    → geconfigureerd? laatste sync-uitslag
  POST /api/bridge/sync-now  → draai één sync-cyclus direct
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


class WhatsappReplyBody(BaseModel):
    id: str
    text: str


class WhatsappDismissBody(BaseModel):
    id: str


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


@router.get("/whatsapp-stats")
async def whatsapp_stats():
    """Proxy naar de remote WhatsApp-agent-statistieken (Neon-Postgres).

    Het lokale dashboard draait op SQLite; de WhatsApp-data staat in het
    remote-systeem. We proxy'en met de bestaande BRIDGE_TOKEN — remote blijft
    bron van waarheid, geen database-credentials worden gedeeld.
    """
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.whatsapp_stats_proxy()


# ── Communicatie — volledig overzicht op :1250, niet alleen op de telefoon ──
# Zelfde proxy-redenering als whatsapp-stats hierboven: de data staat in het
# remote-systeem (Neon), dit endpoint praat er alleen namens :1250 mee via de
# bestaande BRIDGE_TOKEN. Twee schrijvende routes (reply/dismiss) ook hier —
# het antwoord verstuurt Vercel zelf naar Meta, deze machine heeft daarvoor
# geen WhatsApp-credential nodig.
@router.get("/whatsapp")
async def whatsapp_conversations_list():
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.whatsapp_list_proxy()


@router.get("/whatsapp-conversations")
async def whatsapp_conversations():
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.whatsapp_conversations_proxy()


@router.get("/whatsapp-thread")
async def whatsapp_thread(wa_id: str):
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.whatsapp_thread_proxy(wa_id)


@router.post("/whatsapp-reply")
async def whatsapp_reply(body: WhatsappReplyBody):
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.whatsapp_reply_proxy(body.id, body.text)


@router.post("/whatsapp-dismiss")
async def whatsapp_dismiss(body: WhatsappDismissBody):
    if not service.enabled():
        raise HTTPException(400, detail="Bridge niet geconfigureerd — zet BRIDGE_REMOTE_URL en BRIDGE_TOKEN in .env")
    return await service.whatsapp_dismiss_proxy(body.id)

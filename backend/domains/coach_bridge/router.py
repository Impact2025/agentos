"""Read-only endpoint voor De Sparringpartner (mijn-ondernemers-os).

Er bestaat in deze backend nog geen inkomend-tokenpatroon (BRIDGE_TOKEN is
uitgaand, zie shared/config.py) — dit is de eerste. Fail closed: een lege
COACH_BRIDGE_TOKEN betekent "niet geconfigureerd" en de route weigert dan
hard (503), in plaats van elke aanroeper zonder token door te laten. Dit
ontsluit holding-brede bedrijfscijfers, dus de veilige kant is dicht.
"""
from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Header

from ...shared import config
from ...shared.database import get_conn
from .context import build_holding_context

logger = logging.getLogger(__name__)

# Inkomend (mijn-ondernemers-os -> ImpactOS): holding-brede context, geen sessie
# nodig (het is geen browser), wél een eigen token-check. Staat in main.py's
# PUBLIC_PREFIXES zodat de gewone login-gate deze route niet blokkeert.
router = APIRouter(prefix="/api/coach-context", tags=["coach-bridge"])

# Uitgaand (browser in ImpactOS' Control Room -> mijn-ondernemers-os): een
# ander pad, bewust NIET in PUBLIC_PREFIXES — dit blijft achter de normale
# sessie-gate, Vincent is al ingelogd in ImpactOS. Intern gebruikt hij hetzelfde
# gedeelde token om mijn-ondernemers-os aan te roepen (zelfde patroon als
# coach_bridge/whatsapp.py:_fetch_signal).
coach_router = APIRouter(prefix="/api/coach", tags=["coach-bridge"])


def _require_token(authorization: str = Header(default="")) -> None:
    if not config.COACH_BRIDGE_TOKEN:
        raise HTTPException(status_code=503, detail="Coach-bridge niet geconfigureerd (COACH_BRIDGE_TOKEN leeg)")
    prefix = "Bearer "
    token = authorization[len(prefix):] if authorization.startswith(prefix) else ""
    if not token or not secrets.compare_digest(token, config.COACH_BRIDGE_TOKEN):
        raise HTTPException(status_code=401, detail="Ongeldig of ontbrekend token")


@router.get("/holding")
async def get_holding_context(authorization: str = Header(default="")) -> dict:
    _require_token(authorization)
    return await build_holding_context()


# Vincents eigen postvak (Outlook/Graph), niet de helpdesk-projectmailboxen —
# zelfde bron als action_center/service.py's "Postvak"-kaarten (2d hierboven,
# outlook_emails-tabel), hier voor AipaCoach in plaats van het Actiecentrum.
# Bewust read-heavy en action-thin: send/reject hergebruiken exact dezelfde
# bridge_actions-functies als het Actiecentrum, geen aparte implementatie.
@router.get("/mail/pending")
async def get_pending_mail(authorization: str = Header(default="")) -> dict:
    _require_token(authorization)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, subject, from_name, from_email, ai_summary, suggested_reply, "
            "priority, received_at "
            "FROM outlook_emails "
            "WHERE folder='inbox' AND is_replied=0 AND suggested_reply_dismissed=0 "
            "AND suggested_reply IS NOT NULL AND suggested_reply != '' "
            "ORDER BY priority DESC, received_at DESC"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/mail/{item_id}/send")
async def send_pending_mail(item_id: str, body: dict, authorization: str = Header(default="")) -> dict:
    _require_token(authorization)
    from ..bridge.actions import _personal_mail_send
    ok, message = await _personal_mail_send(item_id, body)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    return {"ok": True, "message": message}


@router.post("/mail/{item_id}/reject")
async def reject_pending_mail(item_id: str, authorization: str = Header(default="")) -> dict:
    _require_token(authorization)
    from ..bridge.actions import _personal_mail_reject
    ok, message = await _personal_mail_reject(item_id, {})
    return {"ok": ok, "message": message}


def _bridge_configured() -> bool:
    return bool(config.MIJN_ONDERNEMERS_OS_URL and config.COACH_BRIDGE_TOKEN)


async def _call_mijn_ondernemers_os(method: str, path: str) -> dict:
    if not _bridge_configured():
        raise HTTPException(
            status_code=503,
            detail="De Sparringpartner is niet geconfigureerd (MIJN_ONDERNEMERS_OS_URL/COACH_BRIDGE_TOKEN leeg).",
        )
    url = config.MIJN_ONDERNEMERS_OS_URL.rstrip("/") + path
    headers = {"Authorization": f"Bearer {config.COACH_BRIDGE_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(method, url, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning("[coach] mijn-ondernemers-os onbereikbaar (%s %s): %s", method, path, e)
        raise HTTPException(status_code=502, detail="mijn-ondernemers-os is nu niet bereikbaar.") from e

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {"error": resp.text[:300]}

    if resp.status_code >= 400:
        # Geeft de échte reden door (bv. "nog geen ochtendritueel van vandaag")
        # i.p.v. die te verdrinken in een generieke ImpactOS-foutmelding.
        raise HTTPException(status_code=resp.status_code, detail=body.get("error", "Onbekende fout"))
    return body


@coach_router.post("/reflection")
async def trigger_reflection() -> dict:
    return await _call_mijn_ondernemers_os("POST", "/api/coach/bridge/analyse")


@coach_router.get("/lessons")
async def get_lessons() -> dict:
    return await _call_mijn_ondernemers_os("GET", "/api/coach/bridge/lessons")

"""Gedeelde HTTP-client naar mijn-ondernemers-os (Next.js/Neon), Vincents persoonlijke
ochtend/avond/week-app. Eén gedeeld geheim (COACH_BRIDGE_TOKEN) voor alle uitgaande aanroepen
vanuit ImpactOS naar die app — geëxtraheerd uit coach_bridge/router.py zodat zowel coach_bridge
als rituals 'm kunnen gebruiken zonder dat het ene domein het andere importeert.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from . import config

logger = logging.getLogger(__name__)


def bridge_configured() -> bool:
    return bool(config.MIJN_ONDERNEMERS_OS_URL and config.COACH_BRIDGE_TOKEN)


async def call_mijn_ondernemers_os(method: str, path: str, json: Optional[Any] = None) -> dict:
    """Roept mijn-ondernemers-os aan met het gedeelde bridge-token. Gooit HTTPException bij
    elke fout (niet-geconfigureerd, onbereikbaar, of een foutstatus) — de aanroeper beslist
    zelf of dat fail-open (loggen, lege data) of fail-loud (doorgeven aan de frontend) wordt."""
    if not bridge_configured():
        raise HTTPException(
            status_code=503,
            detail="mijn-ondernemers-os-bridge niet geconfigureerd (MIJN_ONDERNEMERS_OS_URL/COACH_BRIDGE_TOKEN leeg).",
        )
    url = config.MIJN_ONDERNEMERS_OS_URL.rstrip("/") + path
    headers = {"Authorization": f"Bearer {config.COACH_BRIDGE_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(method, url, headers=headers, json=json)
    except Exception as e:  # noqa: BLE001
        logger.warning("[bridge] mijn-ondernemers-os onbereikbaar (%s %s): %s", method, path, e)
        raise HTTPException(status_code=502, detail="mijn-ondernemers-os is nu niet bereikbaar.") from e

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {"error": resp.text[:300]}

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=body.get("error", "Onbekende fout") if isinstance(body, dict) else "Onbekende fout")
    return body

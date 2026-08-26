"""Echte reistijd via Google Distance Matrix — vervangt de vaste 30-minuten-
buffer in agent.py zodra `GOOGLE_MAPS_API_KEY` en `AGENDA_HOME_ADDRESS`
gezet zijn. Niet geconfigureerd, of de call mislukt: geen crash, gewoon
`None` — de aanroeper valt dan terug op de vaste buffer, zelfde regel als elk
ander niet-geconfigureerd kanaal in dit domein (bv. de bridge zelf).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from ...shared import config

log = logging.getLogger(__name__)

_ENDPOINT = "https://maps.googleapis.com/maps/api/distancematrix/json"


def configured() -> bool:
    return bool(config.GOOGLE_MAPS_API_KEY and config.AGENDA_HOME_ADDRESS)


def travel_minutes_sync(destination: str) -> Optional[int]:
    """Enkele-richting reistijd in minuten van AGENDA_HOME_ADDRESS naar
    `destination` (rekening houdend met actueel verkeer), of None als niet
    geconfigureerd, de locatie leeg is, of de call mislukt."""
    if not configured() or not (destination or "").strip():
        return None
    try:
        resp = httpx.get(_ENDPOINT, params={
            "origins": config.AGENDA_HOME_ADDRESS,
            "destinations": destination,
            "mode": "driving",
            "departure_time": "now",  # laat Google actueel verkeer meewegen i.p.v. een leeg gemiddelde
            "key": config.GOOGLE_MAPS_API_KEY,
        }, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK":
            log.warning("[agenda-reistijd] Distance Matrix gaf status %s", data.get("status"))
            return None
        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            # Meestal NOT_FOUND: Google kon de locatietekst niet als adres lezen
            # (bv. "bij de klant") — geen fout, gewoon te vaag om te routeren.
            log.info("[agenda-reistijd] geen route naar '%s': %s", destination, element.get("status"))
            return None
        seconds = (element.get("duration_in_traffic") or element["duration"])["value"]
        return max(1, round(seconds / 60))
    except Exception as e:  # noqa: BLE001
        log.warning("[agenda-reistijd] Distance Matrix-call mislukt voor '%s': %s", destination, e)
        return None


async def travel_minutes(destination: str) -> Optional[int]:
    """Async variant voor aanroepers die al in een event loop zitten (de
    NL-commando-flow) — sync httpx.get() zou anders die loop blokkeren."""
    return await asyncio.to_thread(travel_minutes_sync, destination)

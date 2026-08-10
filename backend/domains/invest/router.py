"""API van de Beursmeester.

Alles wat de wereld verandert (een order) zit achter POST /proposals/{id}/approve
en wordt uitsluitend door een menselijke klik aangeroepen. De rest is lezen.

De analyse-ronde draait als achtergrondtaak: hij duurt minuten (Claude Code
werkt in een map, schrijft scripts en draait ze), en een request-handler is
geen plek om daarop te wachten.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException

from . import analytics, history, portfolio, risk, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invest", tags=["invest"])

# Achtergrondtaken hebben een vastgehouden referentie nodig: de event loop
# bewaart er maar een zwakke, en een ronde die halverwege wordt opgeruimd laat
# een 'running' rij achter zonder dat er iets draait (zelfde valkuil als bij
# de scheduler-inhaalknop).
_taken: set = set()


def _start(coro) -> None:
    taak = asyncio.create_task(coro)
    _taken.add(taak)
    taak.add_done_callback(_taken.discard)


@router.get("")
def get_overzicht() -> Dict[str, Any]:
    return service.overzicht()


@router.get("/portfolio")
def get_portefeuille() -> Dict[str, Any]:
    return {
        "snapshot": portfolio.snapshot(),
        "rendement": portfolio.rendement(),
        "nav_reeks": portfolio.nav_reeks(dagen=180),
        "posities_gesloten": [p for p in portfolio.posities(alleen_open=False)
                              if p["status"] == "closed"],
        "trades": portfolio.trades(limiet=50),
    }


@router.get("/dashboard")
def get_dashboard() -> Dict[str, Any]:
    """Alles wat het Beursmeester-dashboard toont, in één antwoord.

    Bewust één endpoint en niet zeven: de tabellen moeten dezelfde peildatum
    delen. Haalt de UI ze los op, dan staat de NAV van vóór de sync naast de
    posities van erná, en dan spreken twee panelen elkaar tegen zonder dat er
    iets stuk is.
    """
    return analytics.management_rapport()


@router.get("/trackrecord")
def get_trackrecord() -> Dict[str, Any]:
    return {"statistiek": analytics.handelsstatistiek(),
            **analytics.gesloten_resultaten()}


@router.get("/proposals")
def get_voorstellen() -> Dict[str, Any]:
    return {"items": service.open_voorstellen()}


@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: str) -> Dict[str, Any]:
    resultaat = service.keur_goed(proposal_id)
    if not resultaat["ok"]:
        # 409: het voorstel bestaat, maar de wereld is veranderd sinds het werd
        # gemaakt. Dat is iets anders dan een verkeerd verzoek.
        raise HTTPException(status_code=409, detail=resultaat["reden"])
    return resultaat


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: str, reden: str = "") -> Dict[str, Any]:
    resultaat = service.wijs_af(proposal_id, reden)
    if not resultaat["ok"]:
        raise HTTPException(status_code=404, detail=resultaat["reden"])
    return resultaat


@router.post("/run")
async def run_ronde(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    background_tasks.add_task(service.run_daily_cycle)
    return {"status": "gestart",
            "message": "De beursronde draait op de achtergrond; dit duurt enkele minuten."}


@router.post("/sync-history")
async def sync_history() -> Dict[str, Any]:
    return await history.sync()


@router.get("/history/{symbol}")
def get_history(symbol: str, dagen: int = 250) -> Dict[str, Any]:
    reeks = history.reeks(symbol, dagen)
    if not reeks:
        raise HTTPException(status_code=404, detail=f"geen historie voor '{symbol}'")
    return {"symbol": symbol, "reeks": reeks, "verouderd": history.is_verouderd(symbol)}


@router.post("/resume")
def resume() -> Dict[str, Any]:
    """Hef een handelsstop op. Bewust een aparte, expliciete handeling: een
    stop die vanzelf verloopt, leert niemand iets."""
    risk.hervat()
    return {"status": "hervat", "risico": risk.portefeuille_status()}

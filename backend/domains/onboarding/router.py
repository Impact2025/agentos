"""Iris-onboarding — intake-API.

De per-klant OAuth-koppelflow (stap 3) loopt sinds de verhuizing naar Iris
Remote niet meer via deze router: Google/Microsoft redirecten na consent
naar `remote/api/oauth.js` (Vercel, publiek bereikbaar — deze lokale
instance hoeft dat niet te zijn), en het resultaat komt hier binnen via het
Bridge-commando `oauth_token_relay` (backend/domains/bridge/actions.py),
niet via een HTTP-redirect-route. `service.disconnect_channel` blijft hier
staan — intrekken is een lokale DB-actie zonder externe redirect nodig."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class Step1Body(BaseModel):
    profile: str


class Step2Body(BaseModel):
    tone_text: str


class Step4Body(BaseModel):
    preset: str
    overrides: Optional[dict] = None


@router.get("/{site_id}")
def get_status(site_id: str):
    try:
        return service.get_status(site_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{site_id}/step/1")
def step1(site_id: str, body: Step1Body):
    try:
        return service.save_step1(site_id, body.profile)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{site_id}/step/2")
async def step2(site_id: str, body: Step2Body):
    try:
        return await service.save_step2(site_id, body.tone_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{site_id}/step/4")
def step4(site_id: str, body: Step4Body):
    try:
        return service.save_step4(site_id, body.preset, body.overrides)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{site_id}/complete")
def complete(site_id: str):
    try:
        return service.complete_onboarding(site_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{site_id}/oauth/{provider}")
def disconnect(site_id: str, provider: str):
    try:
        ok = service.disconnect_channel(site_id, provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Geen koppeling gevonden")
    return {"ok": True}

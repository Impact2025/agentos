"""Leer-raamwerk API — inzicht in wat agents aantoonbaar leren.

Endpoints:
  GET /api/learning           Overzicht: welke agents hebben lessen/voorspellingen
  GET /api/learning/{agent}   Lessen + voorspellingen + trefkans van één agent
"""
from fastapi import APIRouter

from ...shared import learning

router = APIRouter(prefix="/api/learning", tags=["learning"])


@router.get("")
def overview():
    agents = learning.agents_with_lessons()
    return {
        "agents": [
            {
                "agent": a,
                "lessons": len(learning.active_lessons(a)),
                "track_record": learning.track_record(a),
            }
            for a in agents
        ]
    }


@router.get("/{agent}")
def agent_detail(agent: str):
    return {
        "agent": agent,
        "lessons": learning.active_lessons(agent),
        "predictions": learning.predictions(agent),
        "track_record": learning.track_record(agent),
        "prompt_block": learning.lessons_block(agent),
    }

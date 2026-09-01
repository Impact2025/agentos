"""
Persoonlijke rituelen API router.

Endpoints:
  GET/POST  /api/rituals/morning?date=YYYY-MM-DD   Ochtendritueel (upsert op datum)
  GET/POST  /api/rituals/evening?date=YYYY-MM-DD   Avondritueel (upsert op datum)
  GET/POST  /api/rituals/weekly-start?year=&week=  Weekstart (upsert op jaar+week)
  GET/POST  /api/rituals/weekly-review?year=&week= Weekreview (upsert op jaar+week)
  GET/POST  /api/rituals/wins                       Wins ("Cookie Jar")
  DELETE    /api/rituals/wins/{id}
  GET/POST  /api/rituals/focus                      Focus-sessies
  POST      /api/rituals/focus/{id}/complete
  GET/POST  /api/rituals/goals                      Persoonlijke doelen (lokaal, zie service.py)
  PUT/DELETE /api/rituals/goals/{id}
  GET       /api/rituals/status                     Status vandaag + streaks (voor de UI)

Morning/evening/weekstart/weekreview/wins/focus praten via de service-laag met
mijn-ondernemers-os (bridge), vandaar async def + await hieronder — de request/
response-vorm blijft ongewijzigd, dus geen wijziging nodig in de frontend
(frontend/js/ritual-gate.js, tabs-rituals.js).
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...shared.projects import squash_project
from .service import get_service, _iso_week, _today


def _project(project: Optional[str]) -> Optional[str]:
    """Normaliseert een binnenkomende ?project=-query-param naar dezelfde canonical/squashed
    vorm als project_bridge_tokens gebruikt (Fase 2 deel 2) — None blijft None (Vincents eigen
    aanroepen, ongewijzigd gedrag)."""
    return squash_project(project) if project else None

router = APIRouter(prefix="/api/rituals", tags=["rituals"])


class MorningPayload(BaseModel):
    intentie: str = ""
    affirmatie: str = ""
    dankbaarheid: List[str] = []
    energyLevel: int = 7
    sleepQuality: int = 7
    sleepTime: str = ""
    wakeTime: str = ""
    focusBlok1: Dict[str, Any] = {}
    focusBlok2: Dict[str, Any] = {}


class EveningPayload(BaseModel):
    whatWentWell: str = ""
    biggestWin: str = ""
    whatLearned: str = ""
    challenges: str = ""
    energyLevel: int = 5
    tomorrowTop3: List[str] = []
    gratitude: str = ""
    adhdScores: Dict[str, int] = {}
    focusCheck: List[Dict[str, Any]] = []


class WeeklyStartPayload(BaseModel):
    weekIntention: str = ""
    mainGoals: List[str] = []
    focusAreas: Dict[str, int] = {}
    learningGoal: str = ""
    obstacles: str = ""
    successMetrics: str = ""


class WeeklyReviewPayload(BaseModel):
    wins: List[str] = []
    challenges: str = ""
    learnings: str = ""
    productivityScore: int = 7
    energyScore: int = 7
    carryForward: str = ""
    leaveBehind: str = ""
    growthMoment: str = ""
    whatGave: str = ""
    whatLearned: str = ""
    howContributed: str = ""
    howMakeBetter: str = ""


class WinPayload(BaseModel):
    title: str
    description: str = ""
    category: str = "personal"
    impactLevel: int = 1
    date: Optional[str] = None
    tags: List[str] = []


class FocusStartPayload(BaseModel):
    startTime: str
    goal: str = ""
    date: Optional[str] = None


class GoalPayload(BaseModel):
    title: str
    description: str = ""
    why: str = ""
    painIfNot: str = ""
    pleasureIfDone: str = ""
    nextActions: List[str] = []
    category: str = "personal"
    deadline: str = ""


class GoalPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    why: Optional[str] = None
    painIfNot: Optional[str] = None
    pleasureIfDone: Optional[str] = None
    nextActions: Optional[List[str]] = None
    category: Optional[str] = None
    progress: Optional[int] = None
    completed: Optional[bool] = None
    deadline: Optional[str] = None


@router.get("/status")
async def status():
    svc = get_service()
    return {
        "today": await svc.get_today_status(),
        "streaks": await svc.get_streaks(),
        "focus_completion": await svc.get_focus_completion(_today()),
    }


@router.get("/next-required")
async def next_required():
    """Het volgende verplichte ritueel (verplichte gate). Frontend toont een
    full-screen overlay als `isRequired` en `next.isAvailable` beide True; een
    zachte banner als `isAvailable` False is (avond vóór 17:00)."""
    return await get_service().get_next_required()


# ---------------------------------------------------------------- morning
@router.get("/morning")
async def get_morning(date: Optional[str] = None, project: Optional[str] = None):
    return await get_service().get_morning(date or _today(), _project(project)) or {}


@router.post("/morning")
async def save_morning(payload: MorningPayload, date: Optional[str] = None, project: Optional[str] = None):
    return await get_service().save_morning(date or _today(), payload.model_dump(), _project(project))


# ---------------------------------------------------------------- evening
@router.get("/evening")
async def get_evening(date: Optional[str] = None, project: Optional[str] = None):
    return await get_service().get_evening(date or _today(), _project(project)) or {}


@router.post("/evening")
async def save_evening(payload: EveningPayload, date: Optional[str] = None, project: Optional[str] = None):
    return await get_service().save_evening(date or _today(), payload.model_dump(), _project(project))


# ------------------------------------------------------------ weekly start
@router.get("/weekly-start")
async def get_weekly_start(year: Optional[int] = None, week: Optional[int] = None, project: Optional[str] = None):
    y, w = (year, week) if year and week else _iso_week()
    return await get_service().get_weekly_start(y, w, _project(project)) or {}


@router.post("/weekly-start")
async def save_weekly_start(payload: WeeklyStartPayload, year: Optional[int] = None, week: Optional[int] = None, project: Optional[str] = None):
    y, w = (year, week) if year and week else _iso_week()
    return await get_service().save_weekly_start(y, w, payload.model_dump(), _project(project))


# ----------------------------------------------------------- weekly review
@router.get("/weekly-review")
async def get_weekly_review(year: Optional[int] = None, week: Optional[int] = None, project: Optional[str] = None):
    y, w = (year, week) if year and week else _iso_week()
    return await get_service().get_weekly_review(y, w, _project(project)) or {}


@router.post("/weekly-review")
async def save_weekly_review(payload: WeeklyReviewPayload, year: Optional[int] = None, week: Optional[int] = None, project: Optional[str] = None):
    y, w = (year, week) if year and week else _iso_week()
    return await get_service().save_weekly_review(y, w, payload.model_dump(), _project(project))


# ---------------------------------------------------------------- wins
@router.get("/wins")
async def list_wins(limit: int = 20, category: Optional[str] = None, project: Optional[str] = None):
    return await get_service().list_wins(limit=limit, category=category, project=_project(project))


@router.post("/wins")
async def add_win(payload: WinPayload, project: Optional[str] = None):
    return await get_service().add_win(payload.model_dump(), _project(project))


@router.delete("/wins/{win_id}")
async def delete_win(win_id: int, project: Optional[str] = None):
    await get_service().delete_win(win_id, _project(project))
    return {"ok": True}


# -------------------------------------------------------------- focus
@router.get("/focus")
async def list_focus(date: Optional[str] = None, limit: int = 20, project: Optional[str] = None):
    return await get_service().list_focus_sessions(date=date, limit=limit, project=_project(project))


@router.post("/focus")
async def start_focus(payload: FocusStartPayload, project: Optional[str] = None):
    return await get_service().start_focus_session(payload.model_dump(), _project(project))


@router.post("/focus/{focus_id}/complete")
async def complete_focus(focus_id: int, project: Optional[str] = None):
    result = await get_service().complete_focus_session(focus_id, _project(project))
    if result is None:
        raise HTTPException(status_code=404, detail="Focus-sessie niet gevonden")
    return result


# -------------------------------------------------------------- doelen (lokaal, zie service.py)
@router.get("/goals")
def list_goals(include_completed: bool = True):
    return get_service().list_goals(include_completed=include_completed)


@router.post("/goals")
def add_goal(payload: GoalPayload):
    return get_service().add_goal(payload.model_dump())


@router.put("/goals/{goal_id}")
def update_goal(goal_id: int, payload: GoalPatch):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    result = get_service().update_goal(goal_id, patch)
    if result is None:
        raise HTTPException(status_code=404, detail="Doel niet gevonden")
    return result


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: int):
    get_service().delete_goal(goal_id)
    return {"ok": True}

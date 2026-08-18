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
  GET/POST  /api/rituals/goals                      Persoonlijke doelen
  PUT/DELETE /api/rituals/goals/{id}
  GET       /api/rituals/status                     Status vandaag + streaks (voor de UI)
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .service import get_service, _iso_week, _today

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
def status():
    svc = get_service()
    return {"today": svc.get_today_status(), "streaks": svc.get_streaks()}


@router.get("/next-required")
def next_required():
    """Het volgende verplichte ritueel (verplichte gate). Frontend toont een
    full-screen overlay als `isRequired` en `next.isAvailable` beide True; een
    zachte banner als `isAvailable` False is (avond vóór 17:00)."""
    return get_service().get_next_required()


# ---------------------------------------------------------------- morning
@router.get("/morning")
def get_morning(date: Optional[str] = None):
    return get_service().get_morning(date or _today()) or {}


@router.post("/morning")
def save_morning(payload: MorningPayload, date: Optional[str] = None):
    return get_service().save_morning(date or _today(), payload.model_dump())


# ---------------------------------------------------------------- evening
@router.get("/evening")
def get_evening(date: Optional[str] = None):
    return get_service().get_evening(date or _today()) or {}


@router.post("/evening")
def save_evening(payload: EveningPayload, date: Optional[str] = None):
    return get_service().save_evening(date or _today(), payload.model_dump())


# ------------------------------------------------------------ weekly start
@router.get("/weekly-start")
def get_weekly_start(year: Optional[int] = None, week: Optional[int] = None):
    y, w = (year, week) if year and week else _iso_week()
    return get_service().get_weekly_start(y, w) or {}


@router.post("/weekly-start")
def save_weekly_start(payload: WeeklyStartPayload, year: Optional[int] = None, week: Optional[int] = None):
    y, w = (year, week) if year and week else _iso_week()
    return get_service().save_weekly_start(y, w, payload.model_dump())


# ----------------------------------------------------------- weekly review
@router.get("/weekly-review")
def get_weekly_review(year: Optional[int] = None, week: Optional[int] = None):
    y, w = (year, week) if year and week else _iso_week()
    return get_service().get_weekly_review(y, w) or {}


@router.post("/weekly-review")
def save_weekly_review(payload: WeeklyReviewPayload, year: Optional[int] = None, week: Optional[int] = None):
    y, w = (year, week) if year and week else _iso_week()
    return get_service().save_weekly_review(y, w, payload.model_dump())


# ---------------------------------------------------------------- wins
@router.get("/wins")
def list_wins(limit: int = 20, category: Optional[str] = None):
    return get_service().list_wins(limit=limit, category=category)


@router.post("/wins")
def add_win(payload: WinPayload):
    return get_service().add_win(payload.model_dump())


@router.delete("/wins/{win_id}")
def delete_win(win_id: int):
    get_service().delete_win(win_id)
    return {"ok": True}


# -------------------------------------------------------------- focus
@router.get("/focus")
def list_focus(date: Optional[str] = None, limit: int = 20):
    return get_service().list_focus_sessions(date=date, limit=limit)


@router.post("/focus")
def start_focus(payload: FocusStartPayload):
    return get_service().start_focus_session(payload.model_dump())


@router.post("/focus/{focus_id}/complete")
def complete_focus(focus_id: int):
    result = get_service().complete_focus_session(focus_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Focus-sessie niet gevonden")
    return result


# -------------------------------------------------------------- doelen
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

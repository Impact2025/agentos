"""Goal Mode — Pydantic schema's voor langetermijndoelen en taken."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class GoalTaskSchema(BaseModel):
    """Eén sub-taak binnen een fase van een Goal."""
    id: str
    goal_id: str
    phase_id: str
    title: str
    description: str = ""
    skill: str = ""
    status: str = "pending"  # pending | ready | running | completed | failed | skipped
    dependencies: List[str] = []
    retry_count: int = 0
    max_retries: int = 3
    result: str = ""
    error: str = ""
    workspace_path: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    created_at: str = ""
    updated_at: str = ""


class GoalPhaseSchema(BaseModel):
    """Eén fase in een Goal (chronologische groep taken)."""
    id: str
    goal_id: str
    title: str
    description: str = ""
    ord: int = 0
    status: str = "pending"
    tasks: List[GoalTaskSchema] = []


class GoalSchema(BaseModel):
    """Een overkoepelend langetermijndoel met fasen en taken."""
    id: str
    title: str
    objective: str
    project: str = ""
    status: str = "draft"  # draft | running | paused | completed | failed
    phase_count: int = 0
    task_count: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    current_phase: int = 0
    current_task: str = ""
    phases: List[GoalPhaseSchema] = []


# ── Request/Response schemas ──────────────────────────────────────────

class GoalCreateRequest(BaseModel):
    """Nieuwe goal aanmaken."""
    title: str
    objective: str
    project: str = "WeAreImpact"


class GoalPlanResponse(BaseModel):
    """Response na decompositie — getoond in de UI voordat de gebruiker akkoord gaat."""
    phases: List[dict]
    task_count: int
    estimated_duration: str = ""
    plan_summary: str = ""


class TaskUpdateRequest(BaseModel):
    """Statusupdate voor 1 taak (handmatige override)."""
    status: str  # pending | ready | running | completed | failed | skipped

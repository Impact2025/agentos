"""Agent OS — Mission Control

FastAPI server die alle domein-routers monteert onder 1 app.
Elk domein (chat, pipeline, prospecting, seo, etc.) is een aparte
package in backend/domains/ met eigen router + services.

Gedeelde bibliotheek: backend/shared/ (database, config, models, utils, agent_runner).
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .shared.database import init_db
from .shared.config import OBSIDIAN_VAULT_PATH, hermes_backend
from .domains.chat import hermes as hermes_service
from .expert.team import ensure_expert_team

from .domains.chat.obsidian import ObsidianService
from .scheduler import start_scheduler, stop_scheduler, get_scheduler_status
from .domains.pipeline.conveyor import conveyor_loop

# ── Domein-routers ──────────────────────────────────────────────────────────
from .domains.chat import router as chat_router
from .domains.chat import sessions as sessions_router
from .domains.chat import obsidian_router
from .domains.chat import upload as chat_upload_router
from .domains.chat import journeys_router
from .domains.pipeline import router as tasks_router
from .domains.pipeline import profiles as agent_profiles_router
from .domains.prospecting import router as leads_router
from .domains.vacancies import router as vacancies_router
from .domains.seo import router as demand_router
from .domains.seo import sites_router
from .domains.seo import knowledge as knowledge_router
from .domains.delegate import router as delegate_router
from .domains.loop import router as loops_router
from .domains.finance import router as finance_router
from .domains.analytics import router as analytics_router
from .domains.publish import router as publish_router
from .domains.outlook import router as outlook_router
from .domains.linkedin import router as linkedin_router
from .domains.social import router as social_router
from .domains.content_queue import router as content_queue_router
from .domains.projects import router as projects_router
from .domains.projects import weareimpact  # noqa — activity/content/blog routes
from .domains.projects.weareimpact import activity_router
from .domains.goal import router as goal_router
from .infinite_context import router as infinite_context_router
from .domains.strategist import router as strategist_router
from .domains.seo import optimizer as seo_optimizer
from .domains.radar import router as radar_router
from .domains.action_center import router as action_center_router

BASE_DIR = Path(__file__).parent.parent
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[OK] Database geinitialiseerd", flush=True)
    team = ensure_expert_team()
    print(f"[OK] Expert-team profielen actief ({len(team)} specialisten)", flush=True)
    print(
        f"[OK] Hermes [{hermes_backend()}]: {hermes_service.active_model()} | configured: {hermes_service.is_configured()}",
        flush=True,
    )
    obs = ObsidianService(OBSIDIAN_VAULT_PATH)
    if obs.is_configured:
        print(
            f"[OK] Obsidian vault: {OBSIDIAN_VAULT_PATH} ({obs.total_file_count()} bestanden)",
            flush=True,
        )
    else:
        print(
            "[WARN] Obsidian vault niet geconfigureerd (stel OBSIDIAN_VAULT_PATH in .env in)",
            flush=True,
        )
    start_scheduler()

    # Achtergrondtaken van doelen overleven een herstart niet — hervat bij het
    # opstarten meteen alles wat nog op 'running' staat, zodat je niet
    # handmatig op "Opnieuw uitvoeren" hoeft te klikken na elke restart.
    try:
        from .domains.strategist.service import autoheal_goals
        boot_heal = autoheal_goals()
        if boot_heal["deleted"] or boot_heal["resumed"]:
            print(
                f"[OK] Autoheal bij opstarten: {len(boot_heal['deleted'])} opgeruimd, "
                f"{len(boot_heal['resumed'])} doelen hervat",
                flush=True,
            )
    except Exception as e:
        print(f"[WARN] Autoheal bij opstarten mislukt: {e}", flush=True)

    loop_task = asyncio.create_task(conveyor_loop(poll_interval=2.0))
    app.state.conveyor_task = loop_task

    yield

    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass
    stop_scheduler()


app = FastAPI(
    title="Agent OS",
    description="Lokaal AI-dashboard met Obsidian integratie, Claude & Hermes agents",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1250",
        "http://127.0.0.1:1250",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Monteer alle domein-routers ─────────────────────────────────────────────
app.include_router(chat_router.router)
app.include_router(sessions_router.router)
app.include_router(obsidian_router.router)
app.include_router(chat_upload_router.router)
app.include_router(journeys_router.router)
app.include_router(tasks_router.router)
app.include_router(agent_profiles_router.router)
app.include_router(delegate_router.router)
app.include_router(loops_router.router)
app.include_router(leads_router.router)
app.include_router(vacancies_router.router)
app.include_router(demand_router.router)
app.include_router(sites_router.router)
app.include_router(knowledge_router.router)
app.include_router(analytics_router.router)
app.include_router(finance_router.router)
app.include_router(publish_router.router)
app.include_router(outlook_router.router)
app.include_router(linkedin_router.router)
app.include_router(social_router.router)
app.include_router(content_queue_router.router)
app.include_router(projects_router.router)
app.include_router(activity_router)
app.include_router(goal_router.router)
app.include_router(infinite_context_router.router)
app.include_router(strategist_router.router)
app.include_router(seo_optimizer.router)
app.include_router(radar_router.router)
app.include_router(action_center_router.router)


# ── Status / health endpoints ──────────────────────────────────────────────
@app.get("/api/status")
def status():
    obs = ObsidianService(OBSIDIAN_VAULT_PATH)
    return {
        "status": "online",
        "hermes": {
            "configured": hermes_service.is_configured(),
            "model": hermes_service.active_model(),
            "backend": hermes_backend(),
        },
        "obsidian": {
            "configured": obs.is_configured,
            "vault_path": OBSIDIAN_VAULT_PATH,
            "file_count": obs.total_file_count() if obs.is_configured else 0,
        },
    }


@app.get("/api/scheduler/status")
def scheduler_status():
    return get_scheduler_status()


@app.get("/api/conveyor/status")
def conveyor_status():
    loop_task = getattr(app.state, "conveyor_task", None)
    if loop_task is None:
        return {"running": False}
    return {
        "running": not loop_task.done(),
        "done": loop_task.done(),
        "cancelled": loop_task.cancelled(),
    }


# Frontend serveren — alles wat niet /api/ is gaat naar index.html
app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")

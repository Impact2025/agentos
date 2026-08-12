"""Agent OS — Mission Control

FastAPI server die alle domein-routers monteert onder 1 app.
Elk domein (chat, pipeline, prospecting, seo, etc.) is een aparte
package in backend/domains/ met eigen router + services.

Gedeelde bibliotheek: backend/shared/ (database, config, models, utils, agent_runner).
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi import Request
from pathlib import Path

from .shared.logging_config import setup_logging

# Vóór elke andere import die een logger aanmaakt of logt.
setup_logging()

from .shared.database import init_db
from .shared.config import OBSIDIAN_VAULT_PATH, hermes_backend, domain_enabled, ENABLED_DOMAINS, AGENTOS_INSTANCE_NAME
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
from .domains.linkbuilding import router as linkbuilding_router
from .domains.vacancies import router as vacancies_router
from .domains.seo import router as demand_router
from .domains.seo import sites_router
from .domains.seo import knowledge as knowledge_router
from .domains.onboarding import router as onboarding_router
from .domains.delegate import router as delegate_router
from .domains.loop import router as loops_router
from .domains.finance import router as finance_router
from .domains.invest import router as invest_router
from .domains.analytics import router as analytics_router
from .domains.publish import router as publish_router
from .domains.outlook import router as outlook_router
from .domains.linkedin import router as linkedin_router
from .domains.social import router as social_router
from .domains.social_inbox import router as social_inbox_router
from .domains.social_content import router as social_content_router
from .domains.content_queue import router as content_queue_router
from .domains.projects import router as projects_router
from .domains.projects import weareimpact  # noqa — activity/content/blog routes
from .domains.projects.weareimpact import activity_router
from .domains.goal import router as goal_router
from .domains.learning import router as learning_router
from .domains.health import router as health_router
from .infinite_context import router as infinite_context_router
from .domains.strategist import router as strategist_router
from .domains.seo import optimizer as seo_optimizer
from .domains.radar import router as radar_router
from .domains.rituals import router as rituals_router
from .domains.action_center import router as action_center_router
from .domains.iris import router as iris_router
from .domains.researcher import router as researcher_router
from .domains.auth import router as auth_router
from .domains.auth import service as auth_service
from .domains.omni import router as omni_router

BASE_DIR = Path(__file__).parent.parent
logger = logging.getLogger("agentos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database geinitialiseerd")
    team = ensure_expert_team()
    logger.info("Expert-team profielen actief (%d specialisten)", len(team))
    logger.info(
        "Hermes [%s]: %s | configured: %s",
        hermes_backend(), hermes_service.active_model(), hermes_service.is_configured(),
    )
    obs = ObsidianService(OBSIDIAN_VAULT_PATH)
    if obs.is_configured:
        logger.info("Obsidian vault: %s (%d bestanden)",
                    OBSIDIAN_VAULT_PATH, obs.total_file_count())
    else:
        logger.warning("Obsidian vault niet geconfigureerd (stel OBSIDIAN_VAULT_PATH in .env in)")

    # Achtergrondtaken van doelen overleven een herstart niet — hervat bij het
    # opstarten meteen alles wat nog op 'running' staat, zodat je niet
    # handmatig op "Opnieuw uitvoeren" hoeft te klikken na elke restart.
    # Vóór de scheduler: die start meteen een inhaalslag, en die mag geen doelen
    # oppakken die nog als verweesd in de database staan.
    try:
        from .domains.strategist.service import autoheal_goals
        boot_heal = autoheal_goals()
        if boot_heal["deleted"] or boot_heal["resumed"]:
            logger.info("Autoheal bij opstarten: %d opgeruimd, %d doelen hervat",
                        len(boot_heal["deleted"]), len(boot_heal["resumed"]))
    except Exception:
        logger.exception("Autoheal bij opstarten mislukt")

    start_scheduler()

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

# ── Login-gate ──────────────────────────────────────────────────────
# Beschermt de hele app (ook de gevaarlijke /api/* die mail versturen en
# publiceren) met een sessie-cookie. Staat uit zolang AGENTOS_PASSWORD niet
# is gezet (lokale dev blijft zonder slot). Bij deploy verplicht.
from .domains.auth import service as _auth_service  # noqa: E402


@app.middleware("http")
async def auth_guard_middleware(request: Request, call_next):
    return await _auth_service.auth_guard(request, call_next)


@app.middleware("http")
async def static_no_cache_middleware(request: Request, call_next):
    # Zonder Cache-Control past de browser heuristische caching toe op de
    # frontend-bestanden en draait een gebruiker na een deploy nog dagen oude
    # JS (knoppen die niet meer bestaan, oude flows). no-cache = altijd even
    # hervalideren; ongewijzigde bestanden blijven een goedkope 304.
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and (
            path.endswith((".js", ".css", ".html")) or path in ("/", "")):
        response.headers["Cache-Control"] = "no-cache"
    return response

# ── Monteer alle domein-routers ─────────────────────────────────────────────
# Kern: geen domain-tag, altijd aan (ook op een klant-instance met een
# beperkte AGENTOS_ENABLED_DOMAINS-whitelist — zie shared/config.py).
app.include_router(chat_router.router)
app.include_router(sessions_router.router)
app.include_router(obsidian_router.router)
app.include_router(chat_upload_router.router)
app.include_router(journeys_router.router)
app.include_router(projects_router.router)
app.include_router(activity_router)
app.include_router(health_router.router)
app.include_router(infinite_context_router.router)
app.include_router(action_center_router.router)
app.include_router(auth_router.router)
# Onboarding is per-klant setup — hoort bij de kern, niet achter een
# domain-whitelist: elke nieuwe instance moet zijn eerste klant kunnen
# onboarden, ongeacht welke domeinen daarna zijn afgesproken.
app.include_router(onboarding_router.router)
# strategist_router draagt ook /control-room — de databron van de hele Control
# Room-homepage (project-cards, systeemgezondheid), niet alleen de Doelen-
# functies. Die hoort dus bij de kern, niet achter de "goal"-whitelist: anders
# 404't de complete homepage op een instance zonder Doelen-engine. Alleen de
# Doelen-tab en het aanmaken/starten van doelen blijven achter "goal" (zie
# goal_router hieronder + de frontend die de Strategist-analyseknop verbergt).
app.include_router(strategist_router.router)

# Optioneel per domain-tag — een klant-instance monteert alleen wat is
# afgesproken (bv. Nicole: mail,calendar,publish,seo,iris). Zonder whitelist
# (de bestaande installatie) staat domain_enabled() alles toe.
if domain_enabled("pipeline"):
    app.include_router(tasks_router.router)
    app.include_router(agent_profiles_router.router)
    app.include_router(delegate_router.router)
    app.include_router(loops_router.router)
    from .domains.gauntlet import router as gauntlet_router
    app.include_router(gauntlet_router.router)
if domain_enabled("prospecting"):
    app.include_router(leads_router.router)
if domain_enabled("learning"):
    app.include_router(learning_router.router)
if domain_enabled("linkbuilding"):
    app.include_router(linkbuilding_router.router)
if domain_enabled("vacancies"):
    app.include_router(vacancies_router.router)
if domain_enabled("seo"):
    app.include_router(demand_router.router)
    app.include_router(sites_router.router)
    app.include_router(knowledge_router.router)
    app.include_router(seo_optimizer.router)
    app.include_router(omni_router.router)
if domain_enabled("analytics"):
    app.include_router(analytics_router.router)
if domain_enabled("finance"):
    app.include_router(finance_router.router)
if domain_enabled("invest"):
    app.include_router(invest_router.router)
if domain_enabled("publish"):
    app.include_router(publish_router.router)
    app.include_router(content_queue_router.router)
# outlook_router leest globale env-vars (OUTLOOK_CLIENT_ID) i.p.v. per-project
# DB-rijen zoals het generieke `mail`-domein (mailboxes-tabel) — bewust een
# eigen tag zodat een klant-instance 'm nooit per ongeluk meekrijgt via de
# 'mail'-whitelist en tegen Vincents eigen Outlook-app aanloopt.
if domain_enabled("outlook_legacy"):
    app.include_router(outlook_router.router)
if domain_enabled("social"):
    app.include_router(linkedin_router.router)
    app.include_router(social_router.router)
    app.include_router(social_inbox_router.router)
    app.include_router(social_content_router.router)
if domain_enabled("goal"):
    app.include_router(goal_router.router)
if domain_enabled("radar"):
    app.include_router(radar_router.router)
if domain_enabled("rituals"):
    app.include_router(rituals_router.router)
if domain_enabled("iris"):
    app.include_router(iris_router.router)
if domain_enabled("researcher"):
    app.include_router(researcher_router.router)
if domain_enabled("mail"):
    from .domains.mail import router as mail_router
    app.include_router(mail_router.router)
if domain_enabled("calendar"):
    from .domains.calendar import router as calendar_router
    app.include_router(calendar_router.router)
if domain_enabled("bridge"):
    from .domains.bridge import router as bridge_router
    app.include_router(bridge_router.router)


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
        # None = geen whitelist, alles aan (hoofdinstallatie). Een lijst is een
        # klant-instance — de frontend verbergt tabs die niet in de lijst staan.
        "enabled_domains": sorted(ENABLED_DOMAINS) if ENABLED_DOMAINS else None,
        "instance_name": AGENTOS_INSTANCE_NAME,
    }


@app.get("/api/scheduler/status")
def scheduler_status():
    return get_scheduler_status()


@app.get("/api/scheduler/gaps")
def scheduler_gaps():
    """Wat er niet gebeurde toen de machine uit stond — per taak samengevat."""
    from .shared import downtime
    return {"gaps": downtime.summary()}


@app.post("/api/scheduler/jobs/{job_id}/run")
async def scheduler_run_job(job_id: str):
    """Draai een gemiste taak alsnog. Sluit bij succes de openstaande gaten."""
    from .scheduler import run_job_now
    try:
        return await run_job_now(job_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


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

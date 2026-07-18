"""APScheduler — alle terugkerende agent-jobs van Agent OS.

Drie eigenschappen die deze scheduler betrouwbaar maken op een machine die niet
24/7 aanstaat:

1. **Inhaalslag bij opstarten.** Een cron-job die vuurt vóórdat de server draait
   wordt door APScheduler niet "gemist" — hij wordt simpelweg nooit ingepland
   (`misfire_grace_time` dekt alleen runs die verstrijken terwijl de scheduler
   al loopt). Boot je om 06:57, dan slaan de GSC-sync (06:30) en Iris' briefing
   (06:45) stil over, en draait het ochtendrapport om 07:00 op cijfers van
   gisteren. `_run_catchups` haalt die runs alsnog op, in chronologische
   volgorde, zodat de keten gsc_sync -> iris_briefing -> daily_digest intact is.
2. **Run-historie in SQLite** (`scheduler_runs`), niet in het procesgeheugen.
   Anders is na elke herstart "heeft nooit gedraaid" niet te onderscheiden van
   "draaide vóór de herstart" — en daar hangt de inhaalslag van af.
3. **Eén bron van waarheid** voor de jobs: de `_SPECS`-lijst hieronder. Label,
   trigger en inhaalgedrag staan op één plek in plaats van verspreid over
   vijftien bijna-identieke `add_job`-aanroepen.

Geen enkele job hier publiceert of verstuurt iets extern — dat blijft achter de
Wachtrij-gate.
"""
import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Awaitable, Callable

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_PAUSED
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

from .shared.database import get_conn
from .domains.analytics.reporter import run_weekly_report
from .domains.finance.reporter import (
    run_daily_report as run_finance_daily,
    run_weekly_report as run_finance_weekly,
)
from .domains.publish.content_pipeline import (
    run_biweekly_content_job,
    run_content_improver_job,
)
from .domains.publish.content_learning import run_content_learning_eval
from .domains.vacancies.service import run_vacancy_scan_job
from .domains.seo.optimizer import run_weekly_optimizer_job
from .domains.seo.engine import run_weekly_demand_scan
from .domains.radar.service import scan_the_skies
from .domains.seo.feedback import run_daily_gsc_sync
from .domains.researcher.service import get_service as researcher_svc
from .domains.action_center.digest import run_daily_digest
from .domains.iris.service import run_morning_briefing
from .domains.iris.service import run_iris_prediction_eval
from .domains.prospecting.outreach import run_daily_outreach_batch
from .domains.prospecting.learning import run_outreach_learning_eval
from .domains.linkbuilding.prospector import run_weekly_linkbuilding
from .domains.linkbuilding.monitor import run_link_monitor

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_catchup_task: asyncio.Task | None = None
_TZ = pytz.timezone("Europe/Amsterdam")

# Zoekvenster waarbinnen we het laatste geplande vuurmoment opzoeken. Wat we
# daarvan werkelijk inhalen is strenger: alleen runs van vandáág (zie
# `_pending_catchups`). Een ochtendrapport van gisteren om 07:00 alsnog mailen
# terwijl dat van vandaag over drie minuten draait, helpt niemand.
_CATCHUP_WINDOW = timedelta(hours=24)

# Sentinel-rij in scheduler_runs: het moment waarop deze installatie voor het
# eerst historie bijhield. Runs van vóór dat moment halen we nooit in.
_BASELINE_ID = "__baseline__"

# Noodrem: hangt een ingehaalde job, dan mag de scheduler niet eeuwig gepauzeerd
# blijven. Ruim boven een trage LLM-briefing.
_CATCHUP_TIMEOUT = timedelta(minutes=20)


# ── Jobs die eigen orkestratie nodig hebben ────────────────────────────────

from .shared.config import BRIDGE_SYNC_MINUTES as _BRIDGE_SYNC_MINUTES


async def _bridge_sync_job() -> None:
    """Cloud-companion-sync: push wat op een mens wacht, pull en pas onderweg
    genomen besluiten toe. Slaat stil over zolang de bridge niet geconfigureerd
    is — geen error-ruis op een verse installatie."""
    from .domains.bridge import service as bridge
    if not bridge.enabled():
        return
    await bridge.sync_once()


async def _autoheal_job() -> None:
    """Periodieke zelf-reparatie: hervat doelen die zijn blijven hangen (bv. na
    een server-herstart) en ruimt kapotte draft-doelen op. Async zodat de job op
    de event loop draait — nodig omdat het herstarten van een doel een
    asyncio-achtergrondtaak aanmaakt."""
    from .domains.strategist.service import autoheal_goals
    try:
        report = autoheal_goals()
    except Exception:
        # Volledige traceback naar de log — anders is de oorzaak in de
        # monitoring-UI niet te achterhalen.
        logger.exception("Autoheal-run gefaald")
        raise
    if report["deleted"] or report["resumed"]:
        logger.info(
            "Autoheal: %d verwijderd, %d hervat, %d overgeslagen",
            len(report["deleted"]), len(report["resumed"]), len(report["skipped"]),
        )


async def _monthly_content_goal(project: str, title: str, objective: str) -> None:
    """Herstart maandelijks de AEO-contentmotor van een project, zodat de
    conveyor de Wachtrij blijft vullen. De mens houdt de publiceer-gate.

    Coroutine, geen sync-functie met een eigen event loop: `start_goal_async`
    doet intern `asyncio.create_task`, en die taak moet landen op de loop van de
    server. Draaien we hem in een wegwerp-loop die we daarna sluiten, dan staat
    het doel wel op `running` in de database maar draait er niets meer — en
    moest de 15-minuten-autoheal het puin ruimen.
    """
    from .domains.goal import service as goal_svc
    try:
        plan = await goal_svc.create_and_plan(title=title, objective=objective, project=project)
        goal_id = plan.get("goal_id") if isinstance(plan, dict) else None
        if not goal_id:
            logger.error("[%s] maandelijkse content-goal: planner gaf geen goal_id terug", project)
            return
        goal_svc.confirm_plan(goal_id)
        await goal_svc.start_goal_async(goal_id)
        logger.info("[%s] content-goal G2 (her)gestart: %s", project, goal_id)
    except Exception:
        logger.exception("[%s] maandelijkse content-goal mislukt", project)
        raise


_ICTUSGO_OBJECTIVE = (
    "Per maand 4 goedgekeurde Mission Radar-signalen omzetten in gepubliceerde "
    "AEO-listicles op ictusgo.nl via de auto-AEO conveyor (listicle, video, "
    "reddit). Human-in-the-loop: nooit auto-publiceren, altijd menselijke "
    "review-gate."
)
_WEAREIMPACT_OBJECTIVE = (
    "Per maand 4 goedgekeurde Mission Radar-signalen omzetten in gepubliceerde "
    "AEO-listicles op weareimpact.nl via de auto-AEO conveyor (listicle, video, "
    "reddit). Focus: AI voor zorg/welzijn/gemeenten, sociaal domein, LEGO "
    "Serious Play, change management. Human-in-the-loop: nooit auto-publiceren, "
    "altijd menselijke review-gate."
)


# ── Job-register ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class JobSpec:
    id: str
    label: str
    func: Callable[[], Awaitable[None] | None]
    trigger: BaseTrigger
    # Inhalen bij opstarten als de geplande run in de laatste 24 uur viel en niet
    # geslaagd is. Alleen zinvol voor cron-jobs die op een vast tijdstip iets
    # opleveren; niet voor jobs die uit zichzelf al vaak genoeg langskomen, en
    # niet voor jobs met een blijvend neveneffect (zoals doelen aanmaken).
    catch_up: bool = False
    misfire_grace_time: int = 6 * 3600
    coalesce: bool = True


def _cron(**kwargs) -> CronTrigger:
    return CronTrigger(timezone=_TZ, **kwargs)


# ── Google Agenda-sync ────────────────────────────────────────────────────
# Periodieke cache-verversing zodat de UI/Iris altijd verse events heeft.
# Stil als niet geconfigureerd (geen side-effects). Gedefinieerd vóór _SPECS
# zodat de JobSpec ernaar kan verwijzen.
def calendar_sync_job() -> None:
    from .domains.calendar import service as calendar_service
    if not calendar_service.is_configured():
        return
    try:
        asyncio.run(calendar_service.get_week_events())
        logger.info("Calendar-sync: week-cache bijgewerkt")
    except Exception as e:
        # Leesbaar doorgooien: de kale API-fout ('404 Not Found') vertelt niet
        # dat de agenda met het service-account gedeeld moet worden. De
        # vertaling belandt via scheduler_runs in het Actiecentrum (één rij per
        # job, dus geen kaarten-spam).
        raise RuntimeError(calendar_service.explain_error(e)) from e


# ── Iris-herkanselaar ──────────────────────────────────────────────────────
# Valt de 06:45-briefing terug op puur cijfers (provider-quota op, lege LLM),
# dan probeert deze job het later op de dag opnieuw tot er een volwaardige
# analyse staat. run_morning_briefing degradeert nooit (een volwaardige
# briefing blijft staan) en dedupet acties per dag, dus herkansen is veilig.
async def _iris_retry_job() -> None:
    from .domains.iris import service as iris_service
    from .shared.outcomes import llm_quota_backoff_active
    if not iris_service.briefing_needs_retry():
        return
    if llm_quota_backoff_active():
        logger.info("[iris-retry] provider-quota recent op — herkansing uitgesteld")
        return
    logger.info("[iris-retry] terugval-briefing gevonden — nieuwe poging voor een volwaardige analyse")
    await run_morning_briefing()


# ── NotebookLM-onderzoek-agent ─────────────────────────────────────
# Vaste "kennisronde" voor WeAreImpact: één diepte-vraag tegen
# het standaard SEO-notebook. Het rapport landt in de vault
# (10_Projects/WeAreImpact/onderzoek/), blogs pikken hem automatisch
# op via _researcher_context(). Achtergrond + defensief: een mislukte
# run logt naar het Actiecentrum, hij kraakt nooit de scheduler.
async def _researcher_job() -> None:
    from .shared.config import NOTEBOOKLM_ENABLED
    if not NOTEBOOKLM_ENABLED:
        logger.info("[researcher] uitgeschakeld (NOTEBOOKLM_ENABLED=0)")
        return
    QUESTION = (
        "Welke content gaps ziet dit notebook voor WeAreImpact.nl op het "
        "gebied van interim AI consultancy in het sociaal domein? Noem 3 "
        "concrete blog-onderwerpen die onze concurrenten (nog) niet "
        "dekken, met per onderwerp de kernvraag die de lezer beantwoord "
        "wil zien."
    )
    logger.info("[researcher] kenisronde WeAreImpact start...")
    try:
        # Geen notebook_id → gebruikt NOTEBOOKLM_DEFAULT_NOTEBOOK (nu tijdelijk
        # de podcast, want de SEO-notebooks in library.json geven "Request
        # access" — zie NOTES in researcher/service.py). Zodra Vincent de
        # juiste, gedeelde SEO-notebook-URL's levert, zet NOTEBOOKLM_DEFAULT_NOTEBOOK
        # terug op weareimpact-seo-research en draait dit automatisch tegen SEO.
        await researcher_svc().run_research(
            "weareimpact", QUESTION, notebook_id=None,
        )
        logger.info("[researcher] kenisronde klaar — rapport in vault")
    except Exception:
        logger.exception("[researcher] kenisronde mislukt (niet fataal)")
        try:
            from .shared.outcomes import log_outcome
            log_outcome(
                project="WeAreImpact", action="notebooklm_research",
                detail="Kennisronde mislukt (zie agentos.log).",
                next_step="Controleer of notebooklm-mcp is ingelogd (re_auth).",
                status="error",
            )
        except Exception:
            pass


# Volgorde = leesvolgorde van de dag. De inhaalslag sorteert zelf op tijdstip.
_SPECS: list[JobSpec] = [
    JobSpec(
        "weekly_demand_scan", "Demand Engine-scan (kansen verversen, incl. cold-start voor verse sites)",
        run_weekly_demand_scan, _cron(day_of_week="mon", hour=6, minute=15), catch_up=True,
    ),
    # Bewust géén catch_up: een gemiste check heelt zichzelf morgen, en de
    # monitor hoort niet in de gsc→iris→digest-inhaalketen thuis.
    JobSpec(
        "link_monitor", "Link-monitor (staat de afgesproken backlink er, en blijft hij?)",
        run_link_monitor, _cron(hour=6, minute=20),
    ),
    JobSpec(
        "gsc_sync", "GSC-feedback-loop (performance → Radar growth-signalen)",
        run_daily_gsc_sync, _cron(hour=6, minute=30), catch_up=True,
    ),
    JobSpec(
        "iris_briefing", "Iris dagbriefing (manager-analyse · cijfers per project · bijsturing)",
        run_morning_briefing, _cron(hour=6, minute=45), catch_up=True,
    ),
    JobSpec(
        "daily_digest", "Ochtendrapport (fouten · wacht-op-jou · gisteren opgeleverd)",
        run_daily_digest, _cron(hour=7, minute=0), catch_up=True,
    ),
    JobSpec(
        "vacancy_scan", "Opdrachten-zoekagent (2x/week)",
        run_vacancy_scan_job, _cron(day_of_week="mon,thu", hour=7, minute=0), catch_up=True,
    ),
    JobSpec(
        "outreach_learning_eval", "Outreach-leerlus (voorspellingen afrekenen + stijl-lessen, wekelijks)",
        run_outreach_learning_eval, _cron(day_of_week="mon", hour=7, minute=10), catch_up=True,
    ),
    JobSpec(
        "daily_outreach_batch", "Outreach-batch (concepten klaarzetten ter review, ma-vr)",
        run_daily_outreach_batch, _cron(day_of_week="mon-fri", hour=7, minute=15), catch_up=True,
    ),
    JobSpec(
        "linkbuilding_weekly", "Linkbuilding-weekrun (kansen zoeken + concepten ter review, wo)",
        run_weekly_linkbuilding, _cron(day_of_week="wed", hour=7, minute=30), catch_up=True,
    ),
    JobSpec(
        "daily_finance_report", "Finance dagrapport",
        run_finance_daily, _cron(hour=7, minute=30), catch_up=True,
    ),
    JobSpec(
        "content_learning_eval", "Content-leerlus (welke artikel-vorm haalt clicks — wekelijkse evaluatie)",
        run_content_learning_eval, _cron(day_of_week="mon", hour=7, minute=40), catch_up=True,
    ),
    JobSpec(
        "seo_optimizer_scan", "SEO Optimizer-scan (interne links, CTR, refresh)",
        run_weekly_optimizer_job, _cron(day_of_week="mon", hour=7, minute=45), catch_up=True,
    ),
    JobSpec(
        "weekly_ga_report", "GA weekrapport",
        run_weekly_report, _cron(day_of_week="mon", hour=8, minute=0), catch_up=True,
    ),
    JobSpec(
        "weekly_finance_report", "Finance weekrapport",
        run_finance_weekly, _cron(day_of_week="mon", hour=8, minute=15), catch_up=True,
    ),
    JobSpec(
        "biweekly_content", "Blog + social auto-content (2x/week)",
        run_biweekly_content_job, _cron(day_of_week="tue,fri", hour=9, minute=0), catch_up=True,
    ),
    JobSpec(
        "ictusgo_monthly_content_goal", "IctusGo AEO-contentgoal (maandelijks herstarten)",
        partial(_monthly_content_goal, "ictusgo", "G2 — AEO-contentmotor IctusGo", _ICTUSGO_OBJECTIVE),
        _cron(day=1, hour=8, minute=0),
    ),
    JobSpec(
        "weareimpact_monthly_content_goal", "WeAreImpact AEO-contentgoal (maandelijks herstarten)",
        partial(_monthly_content_goal, "weareimpact", "G2 — AEO-contentmotor WeAreImpact", _WEAREIMPACT_OBJECTIVE),
        _cron(day=1, hour=9, minute=0),
    ),
    JobSpec(
        "radar_sky_scan", "Mission Radar sky-scan (concurrenten & trends, elke 4 uur)",
        scan_the_skies, IntervalTrigger(hours=4),
    ),
    JobSpec(
        "content_improver", "Content-verbeteraar (onder-85 artikelen zelf bijschaven, elke 30 min)",
        run_content_improver_job, IntervalTrigger(minutes=30),
    ),
    JobSpec(
        "goal_autoheal", "Doelen-zelfreparatie (verweesde/dubbele doelen)",
        _autoheal_job, IntervalTrigger(minutes=15), misfire_grace_time=300, coalesce=True,
    ),
    JobSpec(
        "bridge_sync", "Bridge-sync (review-gates ↔ cloud-companion; besluiten onderweg toepassen)",
        _bridge_sync_job, IntervalTrigger(minutes=_BRIDGE_SYNC_MINUTES),
        misfire_grace_time=120, coalesce=True,
    ),
    JobSpec(
        "calendar_sync", "Google Agenda-sync (cache bijwerken, elke 15 min)",
        calendar_sync_job, IntervalTrigger(minutes=15), misfire_grace_time=300, coalesce=True,
    ),
    JobSpec(
        "iris_briefing_retry", "Iris-herkanselaar (terugval-briefing later alsnog volwaardig)",
        _iris_retry_job, IntervalTrigger(minutes=45), misfire_grace_time=600, coalesce=True,
    ),
    # ── NotebookLM-onderzoek-agent ─────────────────────────────────────
    # Vaste "kennisronde": per project één diepte-vraag tegen het
    # standaard SEO-notebook, rapport landt in de vault, blogs halen
    # hem automatisch op als context. Achtergrond (NotebookLM kan traag
    # zijn), nooit publiceren — alleen vault + (optie) review-gate.
    JobSpec(
        "researcher_daily", "NotebookLM-onderzoek (kennisronde WeAreImpact, di/do 10:00)",
        _researcher_job, _cron(day_of_week="tue,thu", hour=10, minute=0),
        catch_up=True,
    ),
]


# ── Mail helpdesk: per actieve mailbox een eigen poll-job ──────────────────
# Elk project krijgt zijn eigen mailbox (tabel `mailboxes`). Hier maken we
# automatisch één JobSpec per ingeschakelde mailbox, met poll-interval uit de
# mailbox-rij. Zo hoeft de scheduler niet handmatig per project bijgewerkt.
# Review-gate: deze jobs VERSTUREN nooit — ze zetten concepten klaar in het
# Actiecentrum (mail_reply, status=pending_review).

def _mailbox_job(mailbox_id: str) -> None:
    from .domains.mail.service import run_mailbox
    from .shared.database import get_conn
    with get_conn() as conn:
        mb = conn.execute(
            "SELECT * FROM mailboxes WHERE id=? AND enabled=1", (mailbox_id,)
        ).fetchone()
    if not mb:
        return
    run_mailbox(dict(mb))


def _mailbox_specs() -> list[JobSpec]:
    try:
        from .shared.database import get_conn
        with get_conn() as conn:
            boxes = conn.execute(
                "SELECT id, project, address, poll_minutes FROM mailboxes WHERE enabled=1"
            ).fetchall()
    except Exception:
        return []
    specs = []
    for mb in boxes:
        mid = mb["id"]
        specs.append(JobSpec(
            f"mail_{mid}",
            f"Mail helpdesk {mb['project']} ({mb['address']})",
            partial(_mailbox_job, mid),
            IntervalTrigger(minutes=int(mb["poll_minutes"] or 30)),
            misfire_grace_time=600, coalesce=True,
        ))
    return specs


# ── Social Inbox: per project/kanaal een eigen poll-job ─────────────────
# Elk project krijgt per kanaal (LinkedIn/IG/FB/TikTok) een eigen inbox
# (tabel `social_inboxes`). Hier maken we automatisch één JobSpec per
# ingeschakelde inbox, met poll-interval uit de inbox-rij. Zo hoeft de
# scheduler niet handmatig per project bijgewerkt. Review-gate: deze jobs
# VERSTUREN nooit — ze zetten concept-antwoorden klaar ter goedkeuring.

def _social_inbox_job(inbox_id: str) -> None:
    from .shared.social_inbox import run_inbox
    try:
        asyncio.run(run_inbox(inbox_id))
    except Exception as e:
        logger.exception("Social inbox %s poll mislukt", inbox_id)
        try:
            from .shared.outcomes import log_outcome
            log_outcome(
                project="Social", action="social_poll",
                detail=f"Social inbox poll mislukt: {e}",
                next_step="Controleer de kanaal-tokens in de Social-tab.",
                status="error",
            )
        except Exception:
            pass


def _social_inbox_specs() -> list[JobSpec]:
    try:
        from .shared.database import get_conn
        with get_conn() as conn:
            boxes = conn.execute(
                "SELECT id, project, platform, poll_minutes FROM social_inboxes WHERE enabled=1"
            ).fetchall()
    except Exception:
        return []
    specs = []
    for b in boxes:
        bid = b["id"]
        specs.append(JobSpec(
            f"social_{bid}",
            f"Social {b['platform']} ({b['project']})",
            partial(_social_inbox_job, bid),
            IntervalTrigger(minutes=int(b["poll_minutes"] or 30)),
            misfire_grace_time=600, coalesce=True,
        ))
    return specs


_SPECS = _SPECS + _mailbox_specs() + _social_inbox_specs()
_BY_ID: dict[str, JobSpec] = {s.id: s for s in _SPECS}


# ── Run-historie ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(_TZ)


def _record_run(job_id: str, status: str, error: str | None, source: str = "schedule") -> None:
    now = _now().isoformat()
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_runs (job_id, status, last_run_at, last_ok_at, error, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status      = excluded.status,
                    last_run_at = excluded.last_run_at,
                    -- een mislukte run mag de laatste geslaagde niet wissen:
                    -- anders zou de inhaalslag hem eindeloos blijven herhalen
                    last_ok_at  = COALESCE(excluded.last_ok_at, scheduler_runs.last_ok_at),
                    error       = excluded.error,
                    source      = excluded.source
                """,
                (job_id, status, now, now if status == "ok" else None, error, source),
            )
    except Exception:
        # De historie is diagnostiek, geen kritiek pad — een kapotte schrijf mag
        # nooit de job zelf laten omvallen.
        logger.exception("Kon run-historie van '%s' niet wegschrijven", job_id)


def _load_runs() -> dict[str, dict]:
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM scheduler_runs").fetchall()
        return {r["job_id"]: dict(r) for r in rows}
    except Exception:
        logger.exception("Kon run-historie niet lezen")
        return {}


def _on_job_event(event) -> None:
    """Registreert het resultaat van elke job-run — voor de monitoring-UI én
    voor de inhaalslag bij de volgende start."""
    if event.code == EVENT_JOB_ERROR:
        # str(exception) kan leeg zijn — geef dan tenminste het exceptietype,
        # anders toont het dashboard een nietszeggende "onbekende fout".
        err = str(event.exception).strip() or type(event.exception).__name__
        _record_run(event.job_id, "error", err)
        logger.error("Scheduler-job '%s' faalde: %s", event.job_id, event.exception,
                     exc_info=event.exception)
    elif event.code == EVENT_JOB_MISSED:
        # Gemist ≠ mislukt: gebeurt bv. rond een serverherstart. De job draait
        # gewoon weer op het volgende geplande moment.
        _record_run(event.job_id, "missed",
                    "run gemist (server was tijdelijk niet beschikbaar) — draait bij de "
                    "volgende geplande run vanzelf")
        logger.warning("Scheduler-job '%s' gemist (misfire)", event.job_id)
    else:  # EVENT_JOB_EXECUTED
        _record_run(event.job_id, "ok", None)
        logger.info("Scheduler-job '%s' klaar", event.job_id)


# ── Inhaalslag ─────────────────────────────────────────────────────────────

def _last_fire_before(trigger: BaseTrigger, now: datetime, window: timedelta) -> datetime | None:
    """Het laatste geplande vuurmoment van `trigger` binnen [now - window, now].

    APScheduler kent geen `get_previous_fire_time`; we lopen het venster vooruit
    af. Bij een IntervalTrigger ligt `start_date` per definitie in de toekomst
    (nu + interval), dus die levert vanzelf None op — precies goed, want een
    interval-job hoeft niet ingehaald te worden.
    """
    fire = trigger.get_next_fire_time(None, now - window)
    previous = None
    for _ in range(1000):  # ruim; voorkomt een oneindige lus bij een rare trigger
        if fire is None or fire > now:
            break
        if previous is not None and fire <= previous:
            break  # trigger loopt niet vooruit — nooit gebeuren, maar niet vastlopen
        previous = fire
        fire = trigger.get_next_fire_time(previous, now)
    return previous


async def _invoke(func: Callable) -> None:
    """Roep een job aan, of hij nu sync of async is."""
    target = func.func if isinstance(func, partial) else func
    if inspect.iscoroutinefunction(target):
        await func()
    else:
        await asyncio.to_thread(func)


def _pending_catchups(now: datetime) -> list[tuple[datetime, JobSpec]]:
    runs = _load_runs()

    # Nulmeting. Zonder historie weten we niet wat er gisteren al gedraaid heeft,
    # en zouden we bij de allereerste start met deze code prompt een ochtendrapport
    # mailen en een outreach-batch bouwen voor runs die al lang gedaan zijn.
    # Eerste boot: niets inhalen, alleen de meetlat neerleggen.
    baseline_row = runs.get(_BASELINE_ID)
    if baseline_row is None:
        _record_run(_BASELINE_ID, "ok", None, source="seed")
        logger.info("Scheduler-historie leeg — nulmeting gezet, geen inhaalslag bij deze start")
        return []
    try:
        baseline = datetime.fromisoformat(baseline_row["last_run_at"])
    except (ValueError, TypeError):
        baseline = now - _CATCHUP_WINDOW

    pending: list[tuple[datetime, JobSpec]] = []
    for spec in _SPECS:
        if not spec.catch_up:
            continue
        fire = _last_fire_before(spec.trigger, now, _CATCHUP_WINDOW)
        if fire is None or fire < baseline:
            continue
        if fire.date() != now.date():
            # Het laatste vuurmoment lag op een eerdere dag. De opbrengst van
            # deze jobs (rapport, briefing, batch) veroudert per dag; die van
            # gisteren alsnog draaien levert niets op — vandaag draait hij toch.
            continue
        last_ok = (runs.get(spec.id) or {}).get("last_ok_at")
        if last_ok:
            try:
                if datetime.fromisoformat(last_ok) >= fire:
                    continue  # die run is al gedaan
            except ValueError:
                pass  # onleesbare tijdstempel: liever inhalen dan overslaan
        pending.append((fire, spec))
    pending.sort(key=lambda p: p[0])
    return pending


async def _run_catchups() -> None:
    """Haalt gemiste runs op in de volgorde waarin ze hadden moeten draaien.

    Strikt sequentieel: het ochtendrapport hoort Iris' briefing van vandaag te
    zien, en Iris hoort de GSC-cijfers van vanochtend te zien. Een job die
    struikelt houdt de rest niet tegen — een mislukte GSC-sync is geen reden om
    ook de briefing over te slaan.
    """
    pending = _pending_catchups(_now())
    if not pending:
        return
    logger.info("Inhaalslag: %d job(s) gemist terwijl de server uit stond", len(pending))
    for fire, spec in pending:
        logger.warning("Inhaalslag '%s' — had om %s moeten draaien",
                       spec.id, fire.strftime("%d-%m %H:%M"))
        try:
            await _invoke(spec.func)
        except Exception as e:
            _record_run(spec.id, "error", str(e).strip() or type(e).__name__, source="catchup")
            logger.exception("Inhaalslag '%s' gefaald", spec.id)
        else:
            _record_run(spec.id, "ok", None, source="catchup")
            logger.info("Inhaalslag '%s' klaar", spec.id)


async def _startup_catchup() -> None:
    """Draait de inhaalslag terwijl de scheduler gepauzeerd staat, en hervat hem.

    Zonder die pauze loopt de inhaalslag naast de gewone planning. Start de
    server om 06:57, dan haalt hij Iris' briefing van 06:45 in — een LLM-run van
    minuten — terwijl de scheduler om 07:00 doodleuk het ochtendrapport afvuurt.
    Dat rapport ziet dan geen briefing van vandaag (`digest.py` vergelijkt op
    datum) en laat Iris' advies stil weg: precies de keten die de inhaalslag
    hoort te beschermen.

    Gepauzeerd vuurt er niets. Bij `resume()` haalt APScheduler de vuurmomenten
    die intussen verstreken zijn alsnog op — daar zijn `misfire_grace_time` en
    `coalesce` voor. De pauze duurt normaal milliseconden (er valt niets in te
    halen) en hooguit de duur van de ochtendketen.
    """
    try:
        await asyncio.wait_for(_run_catchups(), timeout=_CATCHUP_TIMEOUT.total_seconds())
    except asyncio.TimeoutError:
        logger.error("Inhaalslag overschreed %s — scheduler wordt alsnog hervat",
                     _CATCHUP_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Inhaalslag onverwacht gefaald — scheduler wordt alsnog hervat")
    finally:
        _resume_scheduler()


def _resume_scheduler() -> None:
    """Hervat de gepauzeerde scheduler. Nooit een fout hier laten ontsnappen: de
    planning weer aanzetten is belangrijker dan de reden waarom het misging."""
    try:
        if _scheduler is not None and _scheduler.state == STATE_PAUSED:
            _scheduler.resume()
            logger.info("Scheduler hervat — planning actief")
    except Exception:
        logger.exception("Kon de scheduler niet hervatten")


# ── Levenscyclus ───────────────────────────────────────────────────────────

def _check_local_backend() -> None:
    """Health-check op de lokale LiteLLM-backend bij scheduler-startup.

    Voorkomt stomme fail-first-call-terugval: in plaats van pas bij de
    éérste LLM-aanroep te merken dat LiteLLM down is, pingen we 'm bij
    opstarten. Staat de lokale backend aan maar faalt de ping, dan loggen we
    pro-actief 'degraded mode' zodat je niet pas in de Wachtrij ziet dat
    artikelen achterblijven. (Wereldklasse = pro-actief, niet reactief.)
    """
    from .shared.config import HERMES_LOCAL_URL, HERMES_LOCAL_KEY
    if not HERMES_LOCAL_URL:
        return  # geeen lokale backend geconfigureerd — niks te checken
    import httpx
    # HERMES_LOCAL_URL eindigt vaak al op /v1 (OpenAI-stijl base-url, bv. Ollama
    # op :11434/v1) — dan géén tweede /v1 aanplakken, anders 404't een backend
    # die gewoon UP is en meldt de startup ten onrechte 'degraded'.
    _base = HERMES_LOCAL_URL.rstrip("/")
    url = _base + ("/models" if _base.endswith("/v1") else "/v1/models")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                url,
                headers={"Authorization": f"Bearer {HERMES_LOCAL_KEY or 'x'}"},
            )
        if resp.status_code < 400:
            logger.info("[health] Lokale LLM-backend UP (%s) — primair actief.",
                        HERMES_LOCAL_URL)
        else:
            logger.warning(
                "[health] Lokale LLM-backend antwoordt %s — agents vallen "
                "terug op cloud (OpenModel/deepseek). Degraded mode.",
                resp.status_code,
            )
    except Exception as e:
        logger.warning(
            "[health] Lokale LLM-backend NIET bereikbaar (%s: %s) — "
            "agents vallen terug op cloud (OpenModel/deepseek). Degraded mode.",
            type(e).__name__, e,
        )


def start_scheduler() -> None:
    global _scheduler, _catchup_task
    _scheduler = AsyncIOScheduler(timezone=_TZ)
    for spec in _SPECS:
        _scheduler.add_job(
            spec.func,
            spec.trigger,
            id=spec.id,
            replace_existing=True,
            misfire_grace_time=spec.misfire_grace_time,
            coalesce=spec.coalesce,
        )
    _scheduler.add_listener(
        _on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED
    )
    # Gepauzeerd: geen enkele job mag vuren zolang de inhaalslag loopt, anders
    _scheduler.start(paused=True)
    logger.info("Scheduler gestart (gepauzeerd) — %d jobs ingepland",
                len(_SPECS))

    # Pro-actieve health-check op de lokale backend vóór de inhaalslag.
    try:
        _check_local_backend()
    except Exception:
        logger.exception("[health] Lokale-backend-check onverwacht mislukt")

    # Op de achtergrond, zodat een trage LLM-briefing het opstarten niet ophoudt.
    _catchup_task = asyncio.create_task(_startup_catchup())


def get_scheduler_status() -> dict:
    """Status van alle scheduler-jobs t.b.v. de monitoring-UI."""
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "catching_up": False, "paused": False, "jobs": []}

    runs = _load_runs()
    jobs = []
    for job in _scheduler.get_jobs():
        spec = _BY_ID.get(job.id)
        run = runs.get(job.id)
        jobs.append(
            {
                "id": job.id,
                "label": spec.label if spec else job.id,
                "trigger": str(job.trigger),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                # Blijft over herstarts heen bestaan: null betekent nu écht
                # "heeft nog nooit gedraaid".
                "last_run": {
                    "status": run["status"],
                    "time": run["last_run_at"],
                    "error": run["error"],
                    "source": run["source"],
                } if run else None,
                "catch_up": bool(spec.catch_up) if spec else False,
            }
        )
    return {
        "running": True,
        # Tijdens de inhaalslag staat de planning stil; dat is normaal en duurt
        # kort. Blijft dit hangen, dan zit een ingehaalde job vast.
        "catching_up": bool(_catchup_task and not _catchup_task.done()),
        "paused": _scheduler.state == STATE_PAUSED,
        "jobs": jobs,
    }


def stop_scheduler() -> None:
    global _scheduler
    if _catchup_task and not _catchup_task.done():
        _catchup_task.cancel()
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    logger.info("Scheduler gestopt")

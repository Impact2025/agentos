"""
APScheduler — wekelijks GA rapport.
Conveyor loop wordt apart aangestuurd vanuit FastAPI startup voor async-compatibiliteit.
"""
import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

from .domains.analytics.reporter import run_weekly_report
from .domains.finance.reporter import (
    run_daily_report as run_finance_daily,
    run_weekly_report as run_finance_weekly,
)
from .domains.publish.content_pipeline import run_biweekly_content_job
from .domains.vacancies.service import run_vacancy_scan_job
from .domains.seo.optimizer import run_weekly_optimizer_job
from .domains.radar.service import scan_the_skies
from .domains.seo.feedback import run_daily_gsc_sync
from .domains.action_center.digest import run_daily_digest

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_TZ = pytz.timezone("Europe/Amsterdam")

# Leesbare namen per job-id voor de monitoring-UI.
_JOB_LABELS = {
    "weekly_ga_report": "GA weekrapport",
    "daily_finance_report": "Finance dagrapport",
    "weekly_finance_report": "Finance weekrapport",
    "biweekly_content": "Blog + social auto-content (2x/week)",
    "goal_autoheal": "Doelen-zelfreparatie (verweesde/dubbele doelen)",
    "vacancy_scan": "Opdrachten-zoekagent (2x/week)",
    "seo_optimizer_scan": "SEO Optimizer-scan (interne links, CTR, refresh)",
    "gsc_sync": "GSC-feedback-loop (performance → Radar growth-signalen)",
    "radar_sky_scan": "Mission Radar sky-scan (concurrenten & trends, elke 4 uur)",
    "daily_digest": "Ochtendrapport (fouten · wacht-op-jou · gisteren opgeleverd)",
    "daily_outreach_batch": "Outreach-batch (concepten klaarzetten ter review, ma-vr)",
}


async def _autoheal_job() -> None:
    """Periodieke zelf-reparatie: hervat doelen die zijn blijven hangen (bv.
    na een server-herstart) en ruimt kapotte draft-doelen op. Async zodat
    de job direct op de event loop draait — nodig omdat het herstarten van
    een doel een asyncio-achtergrondtaak aanmaakt."""
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

# Laatste-run resultaat per job-id: {status, time, error}.
_last_runs: dict[str, dict] = {}


def _on_job_event(event) -> None:
    """Registreert het resultaat van elke job-run voor de monitoring-UI."""
    now = datetime.now(_TZ).isoformat()
    if event.code == EVENT_JOB_ERROR:
        # str(exception) kan leeg zijn — geef dan tenminste het exceptietype,
        # anders toont het dashboard een nietszeggende "onbekende fout".
        err = str(event.exception).strip() or type(event.exception).__name__
        _last_runs[event.job_id] = {
            "status": "error",
            "time": now,
            "error": err,
        }
        logger.error("Scheduler-job '%s' faalde: %s", event.job_id, event.exception,
                     exc_info=event.exception)
    elif event.code == EVENT_JOB_MISSED:
        # Gemist ≠ mislukt: gebeurt bv. rond een serverherstart. De job draait
        # gewoon weer op het volgende geplande moment.
        _last_runs[event.job_id] = {
            "status": "missed",
            "time": now,
            "error": "run gemist (server was tijdelijk niet beschikbaar) — draait bij de volgende geplande run vanzelf",
        }
        logger.warning("Scheduler-job '%s' gemist (misfire)", event.job_id)
    else:  # EVENT_JOB_EXECUTED
        _last_runs[event.job_id] = {"status": "ok", "time": now, "error": None}


def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=_TZ)
    _scheduler.add_job(
        run_weekly_report,
        CronTrigger(day_of_week="mon", hour=8, minute=0, timezone=_TZ),
        id="weekly_ga_report",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    # Finance: dagrapport elke ochtend, diep macro-/liquiditeitsweekrapport op maandag.
    _scheduler.add_job(
        run_finance_daily,
        CronTrigger(hour=7, minute=30, timezone=_TZ),
        id="daily_finance_report",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    _scheduler.add_job(
        run_finance_weekly,
        CronTrigger(day_of_week="mon", hour=8, minute=15, timezone=_TZ),
        id="weekly_finance_report",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    # Blog + social auto-content: 2x/week (di + vr), alleen voor sites met
    # auto_content_enabled=1. Publiceert/post NOOIT automatisch — zet enkel een
    # concept klaar in de content_jobs-wachtrij voor menselijke goedkeuring
    # (zie backend/domains/content_queue/router.py).
    _scheduler.add_job(
        run_biweekly_content_job,
        CronTrigger(day_of_week="tue,fri", hour=9, minute=0, timezone=_TZ),
        id="biweekly_content",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    # Opdrachten-zoekagent: 2x/week (ma + do), doorzoekt LinkedIn/Freelance.nl/
    # Indeed/BMC.nl + brede webzoekactie op interim-/zzp-vacatures die passen bij
    # Vincents profiel. Publiceert niets extern — vult alleen het Opdrachten-tabblad.
    _scheduler.add_job(
        run_vacancy_scan_job,
        CronTrigger(day_of_week="mon,thu", hour=7, minute=0, timezone=_TZ),
        id="vacancy_scan",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    # SEO Optimizer: wekelijkse scan (ma 07:45, vóór het GA-weekrapport) voor
    # alle sites met een GSC-koppeling. LLM-vrij — vult alleen de
    # Optimalisatie-tab met interne-link-, CTR- en refresh-kansen.
    _scheduler.add_job(
        run_weekly_optimizer_job,
        CronTrigger(day_of_week="mon", hour=7, minute=45, timezone=_TZ),
        id="seo_optimizer_scan",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    # GSC-feedback-loop: dagelijks de pagina-performance ophalen en growth-
    # signalen terugvoeren naar de Mission Radar. Sluit de cirkel: wat je
    # publiceert wordt gemeten, en de agent schrijft versterkende content.
    # Draait alleen als er ten minste één site met GSC-property is.
    _scheduler.add_job(
        run_daily_gsc_sync,
        CronTrigger(hour=6, minute=30, timezone=_TZ),
        id="gsc_sync",
        replace_existing=True,
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    # Mission Radar: elke 4 uur de watchlist (concurrenten/keywords/RSS) scannen.
    # Publiceert niets — vult alleen het Radar-tabblad + schrijft topsignalen als
    # markdown naar de Obsidian-vault (10_Projects/_trends/) voor de geheugen-loop.
    from apscheduler.triggers.interval import IntervalTrigger
    _scheduler.add_job(
        scan_the_skies,
        IntervalTrigger(hours=4),
        id="radar_sky_scan",
        replace_existing=True,
        misfire_grace_time=6 * 3600,  # tot 6u later alsnog inhalen (bv. na slaapstand/herstart)
        coalesce=True,
    )
    # Ochtendrapport: dagelijkse digest van het Actiecentrum — vóór de andere
    # rapporten zodat Vincent één samenvatting heeft in plaats van losse mails.
    _scheduler.add_job(
        run_daily_digest,
        CronTrigger(hour=7, minute=0, timezone=_TZ),
        id="daily_digest",
        replace_existing=True,
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    # Outreach-batch: elke werkdag concepten klaarzetten voor de beste
    # onbenaderde leads (OUTREACH_DAILY_TARGET). Verstuurt NOOIT zelf —
    # concepten wachten in het Actiecentrum op Vincents verzendklik.
    from .domains.prospecting.outreach import run_daily_outreach_batch
    _scheduler.add_job(
        run_daily_outreach_batch,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=15, timezone=_TZ),
        id="daily_outreach_batch",
        replace_existing=True,
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    # Autoheal: elke 15 min — vangt verweesde 'running'-doelen op (bv. na een
    # server-herstart) zonder dat iemand handmatig op "Opnieuw uitvoeren" hoeft
    # te klikken.
    _scheduler.add_job(
        _autoheal_job,
        IntervalTrigger(minutes=15),
        id="goal_autoheal",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_listener(
        _on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED
    )
    _scheduler.start()
    logger.info(
        "[OK] Scheduler actief — GA weekrapport ma 08:00 · Finance dagrapport 07:30 · "
        "Finance weekrapport ma 08:15 · Blog+social auto-content di/vr 09:00 · "
        "Opdrachten-zoekagent ma/do 07:00 · Mission Radar sky-scan elke 4 uur · "
        "Doelen-autoheal elke 15 min"
    )


def get_scheduler_status() -> dict:
    """Status van alle scheduler-jobs t.b.v. de monitoring-UI."""
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append(
            {
                "id": job.id,
                "label": _JOB_LABELS.get(job.id, job.id),
                "trigger": str(job.trigger),
                "next_run": next_run.isoformat() if next_run else None,
                "last_run": _last_runs.get(job.id),
            }
        )
    return {"running": True, "jobs": jobs}


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    logger.info("Scheduler gestopt")

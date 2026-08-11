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
   Hoe ver terug dat gaat hangt af van wat de opbrengst waard is (zie
   `_pending_catchups`): rapporten en briefings alleen van vandaag — die van
   gisteren alsnog mailen helpt niemand — en jobs waarvan de dag níét terugkomt
   (`gap_cost`) tot twee weken terug, maar altijd hooguit één keer.
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
from .shared.config import domain_enabled
from .domains.analytics.reporter import run_weekly_report
from .domains.finance.reporter import (
    run_daily_report as run_finance_daily,
    run_weekly_report as run_finance_weekly,
)
from .domains.invest.history import sync as run_invest_market_sync
from .domains.invest.service import run_daily_cycle as run_invest_cycle
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

# Terugkijkvenster voor jobs waarvan de dag níét terugkomt (`gap_cost` gevuld).
# Aanleiding: de machine stond 28-31 juli 2026 vier werkdagen uit; de
# outreach-batch vuurde vier keer niet en de vacaturescan sloeg over. Die runs
# werden wél geteld, maar alleen met een knop "Nu alsnog draaien" eronder — dus
# gebeurde er niets tot iemand hem aanklikte. Twee weken is ruim genoeg voor een
# vakantie en kort genoeg dat de opbrengst nog ergens over gaat.
#
# Let op wat hier NIET gebeurt: vier gemiste vuurmomenten worden één inhaalrun,
# niet vier. `_last_fire_before` geeft het láátste moment terug, en dat is de
# bedoeling — vier outreach-batches tegelijk is vier stapels concepten en vier
# keer LLM-kosten voor werk dat één keer gedaan hoort te worden.
_GAP_CATCHUP_WINDOW = timedelta(days=14)

# Sentinel-rij in scheduler_runs: het moment waarop deze installatie voor het
# eerst historie bijhield. Runs van vóór dat moment halen we nooit in.
_BASELINE_ID = "__baseline__"

# Noodrem: hangt een ingehaalde job, dan mag de scheduler niet eeuwig gepauzeerd
# blijven. Ruim boven een trage LLM-briefing. 40 min zodat ook een lange
# inhaalslag na een meerdaagse downtime de kritieke rapporten volledig afwerkt
# in plaats van bij 20 min de staart (Finance, Beursmeester, kennisronde) af te
# kappen.
_CATCHUP_TIMEOUT = timedelta(minutes=40)


# ── Jobs die eigen orkestratie nodig hebben ────────────────────────────────

from .shared.config import BRIDGE_SYNC_MINUTES as _BRIDGE_SYNC_MINUTES


async def _postvak_sync_job() -> None:
    """Vincents eigen Outlook-postvak bijhouden.

    Bewust een interval-job zonder `gap_cost`: de opbrengst veroudert per dag en
    is bij de volgende run vanzelf weer vers — een gemiste sync is geen gemis.
    Wat er wél moest komen is de job zelf; tot 11 aug 2026 was er geen enkele,
    en werd er alleen opgehaald als een mens erom vroeg.
    """
    from .domains.outlook import service as outlook
    if not outlook.is_configured():
        return
    await outlook.run_postvak_sync()


async def _bridge_sync_job() -> None:
    """Cloud-companion-sync: push wat op een mens wacht, pull en pas onderweg
    genomen besluiten toe.

    Drie standen, want ze vragen om verschillend gedrag: helemaal niet ingevuld
    is een verse installatie (stil overslaan, geen error-ruis), half ingevuld is
    een configuratiefout die je alleen op je telefoon zou merken als een
    bevroren lijst (meteen melden), en ingevuld draait gewoon."""
    from .domains.bridge import service as bridge
    state = bridge.config_state()
    if state == "partial":
        bridge.report_misconfiguration()
        return
    if state == "off":
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


async def _iris_selfheal_job() -> None:
    """Iris' zelfherstel-ronde: openstaande fouten eerst zélf proberen op te
    lossen (probe = het werk echt opnieuw doen), en alleen melden wat ze niet
    voor elkaar krijgt.

    Deze job mag zelf nooit falen: een foutkaart over het opruimen van
    foutkaarten is precies de ruis die hij bestrijdt. `run_selfheal` vangt
    daarom alles af en rapporteert via zijn returnwaarde.
    """
    from .domains.iris.selfheal import run_selfheal
    report = await run_selfheal(source="scheduler")
    if report.get("healed") or report.get("escalated"):
        logger.info(
            "Iris-zelfherstel: %d bekeken, %d zelf opgelost, %d gemeld",
            report.get("checked", 0), report.get("healed", 0), report.get("escalated", 0),
        )


def _waarheidsaudit_job() -> None:
    """De tegenhanger van de zelfherstel-ronde: zoeken wat stíl kapot is.

    Draait om 06:40, vlak vóór Iris' briefing van 06:45, zodat haar oordeel de
    verse bevindingen meeneemt. Hij draait óók aan het begin van de briefing
    zelf — dubbel draaien is aantoonbaar onschadelijk (de ronde is idempotent en
    doet alleen SQL), en zo blijft de audit staan als één van beide paden faalt.

    `catch_up=True`: is de server om 06:40 uit, dan wil je die ronde alsnog. Een
    dag zonder audit is een dag waarin stille schade onzichtbaar blijft.
    """
    from .domains.iris.integrity import run_audit
    r = run_audit(source="scheduler")
    if r.get("nieuw") or r.get("geescaleerd") or r.get("kaarten_gesloten"):
        logger.info("Waarheidsaudit: %d nieuw, %d opgelost, %d open, %d gemeld, %d kaart(en) dicht",
                    r.get("nieuw", 0), r.get("opgelost", 0), r.get("open", 0),
                    r.get("geescaleerd", 0), r.get("kaarten_gesloten", 0))


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
    # Wat er verloren gaat als een geplande run overgaat terwijl de machine uit
    # staat — in werk, niet in runs. Leeg = de opbrengst veroudert per dag en is
    # bij de volgende run vanzelf weer vers (rapporten, briefings, syncs met een
    # terugkijkvenster); zo'n gemiste run is geen gemis en hoort niemand wakker
    # te maken. Gevuld = de dag is echt weg, en dan verschijnt er een kaart mét
    # een knop die de job alsnog draait. Zie shared/downtime.py.
    gap_cost: str = ""
    # Prioriteit voor de inhaalslag: lagere waarde = eerder. Kritieke rapporten
    # (Finance, Beursmeester, kennisronde) krijgen 0 zodat ze vóór de zware
    # LLM-briefings draaien — die laatste zijn de bottleneck die de 20-min-
    # grens liet overschrijden en de staart liet liggen.
    priority: int = 10
    # Domain-tag (zie shared/config.py:domain_enabled). Leeg = kernfunctionaliteit,
    # draait altijd. Een klant-instance met een AGENTOS_ENABLED_DOMAINS-whitelist
    # slaat elke job over waarvan de tag niet in de lijst staat — anders draait
    # Beursmeester/Finance/de client-specifieke maandelijkse contentgoals gewoon
    # door op een instance die daar nooit om heeft gevraagd, en betaalt die klant
    # (of Vincent) LLM-kosten voor werk dat niemand ziet.
    domain: str = ""


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


def calendar_reminder_job() -> None:
    """Dagelijkse agenda-herinnering (1 dag van tevoren) per mail."""
    from .domains.calendar import reminder as cal_reminder
    try:
        n = cal_reminder.run_reminders()
        logger.info("Agenda-herinnering: %s mail(s) verstuurd", n)
    except Exception as e:  # noqa: BLE001
        logger.exception("Agenda-herinnering mislukt: %s", e)


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

    # Kennisronde per project. Elke entry: (project, vraag). De rapporten
    # landen in 10_Projects/{project}/onderzoek/ en worden door de
    # content-pipeline automatisch als schrijfcontext opgepikt — méér en
    # vaker onderzoek = scherpere, uniekere artikelen (minder rejects op de
    # kwaliteitsgrens). TeambuildingMetImpact krijgt twee diepte-vragen omdat
    # die map lang dun bleef (1 vraag-bestand van weken terug) en de
    # content-motor daar structureel onder de grens scoort.
    QUESTIONS: list[tuple[str, str]] = [
        (
            "teambuildingmetimpact",
            "Welke wetenschappelijke bewijzen zijn er dat interactieve workshops "
            "teamontwikkeling écht verbeteren (cohesie, psychologische veiligheid, "
            "prestatie)? Geef 3 citeerbare inzichten met de kerncijfers erbij.",
        ),
        (
            "teambuildingmetimpact",
            "Hoe reken je de maatschappelijke meerwaarde (SROI) van teambuilding met "
            "een sociaal doel door? Geef een concreet rekenvoorbeeld met cijfers en "
            "de factoren die de SROI het meest opdrijven.",
        ),
        (
            "weareimpact",
            "Welke content gaps ziet dit notebook voor WeAreImpact.nl op het "
            "gebied van interim AI consultancy in het sociaal domein? Noem 3 "
            "concrete blog-onderwerpen die onze concurrenten (nog) niet "
            "dekken, met per onderwerp de kernvraag die de lezer beantwoord "
            "wil zien.",
        ),
    ]

    ok = 0
    for project, question in QUESTIONS:
        try:
            # Geen notebook_id → gebruikt NOTEBOOKLM_DEFAULT_NOTEBOOK (nu tijdelijk
            # de podcast, want de SEO-notebooks in library.json geven "Request
            # access" — zie NOTES in researcher/service.py). Zodra Vincent de
            # juiste, gedeelde SEO-notebook-URL's levert, zet NOTEBOOKLM_DEFAULT_NOTEBOOK
            # terug op weareimpact-seo-research en draait dit automatisch tegen SEO.
            await researcher_svc().run_research(
                project, question, notebook_id=None,
            )
            ok += 1
            logger.info("[researcher] %s — rapport in vault", project)
        except Exception:
            logger.exception("[researcher] vraag mislukt voor %s (niet fataal)", project)
            try:
                from .shared.outcomes import log_outcome
                log_outcome(
                    project=project, action="notebooklm_research",
                    detail=f"Kennisronde mislukt voor {project} (zie agentos.log).",
                    next_step="Controleer of notebooklm-mcp is ingelogd (re_auth).",
                    status="error",
                )
            except Exception:
                pass
    logger.info("[researcher] kennisronde klaar — %d/%d rapporten gelukt", ok, len(QUESTIONS))


# Volgorde = leesvolgorde van de dag. De inhaalslag sorteert zelf op tijdstip.
_SPECS: list[JobSpec] = [
    JobSpec(
        "weekly_demand_scan", "Demand Engine-scan (kansen verversen, incl. cold-start voor verse sites)",
        run_weekly_demand_scan, _cron(day_of_week="mon", hour=6, minute=15), catch_up=True,
        gap_cost="de kansenvoorraad is een week niet ververst; de contentmotor kan drooglopen",
        domain="seo",
    ),
    # Bewust géén catch_up: een gemiste check heelt zichzelf morgen, en de
    # monitor hoort niet in de gsc→iris→digest-inhaalketen thuis.
    JobSpec(
        "link_monitor", "Link-monitor (staat de afgesproken backlink er, en blijft hij?)",
        run_link_monitor, _cron(hour=6, minute=20), domain="linkbuilding",
    ),
    JobSpec(
        "gsc_sync", "GSC-feedback-loop (performance → Radar growth-signalen)",
        run_daily_gsc_sync, _cron(hour=6, minute=30), catch_up=True, domain="seo",
    ),
    JobSpec(
        "iris_briefing", "Iris dagbriefing (manager-analyse · cijfers per project · bijsturing)",
        run_morning_briefing, _cron(hour=6, minute=45), catch_up=True, domain="iris",
    ),
    JobSpec(
        "daily_digest", "Ochtendrapport (fouten · wacht-op-jou · gisteren opgeleverd)",
        run_daily_digest, _cron(hour=7, minute=0), catch_up=True,
    ),
    # Agenda-herinnering: 1 dag van tevoren per mail. Vroeg in de ochtend
    # zodat de herinnering bij het wakker-worden in de inbox ligt. Stil als SMTP
    # niet is geconfigureerd (geen side-effects).
    JobSpec(
        "calendar_reminder", "Agenda-herinnering (1 dag van tevoren, per mail)",
        calendar_reminder_job, _cron(hour=7, minute=5), domain="calendar",
    ),
    JobSpec(
        "vacancy_scan", "Opdrachten-zoekagent (2x/week)",
        run_vacancy_scan_job, _cron(day_of_week="mon,thu", hour=7, minute=0), catch_up=True,
        gap_cost="geen enkele interim-opdracht gescand; verse uitvragen zijn hoogstwaarschijnlijk gemist",
        domain="vacancies",
    ),
    JobSpec(
        "outreach_learning_eval", "Outreach-leerlus (voorspellingen afrekenen + stijl-lessen, wekelijks)",
        run_outreach_learning_eval, _cron(day_of_week="mon", hour=7, minute=10), catch_up=True,
        domain="prospecting",
    ),
    JobSpec(
        "daily_outreach_batch", "Outreach-batch (concepten klaarzetten ter review, ma-vr)",
        run_daily_outreach_batch, _cron(day_of_week="mon-fri", hour=7, minute=15), catch_up=True,
        # De acquisitieformule meet input tegen output. Een dag zonder concepten
        # is niet alleen een dag zonder mails — hij vervalst ook de ratio's,
        # want de output van vandaag hoort bij de input van vorige week.
        gap_cost="geen outreach-concepten klaargezet; die werkdag komt niet terug en de conversiecijfers kloppen niet",
        domain="prospecting",
    ),
    JobSpec(
        "linkbuilding_weekly", "Linkbuilding-weekrun (kansen zoeken + concepten ter review, wo)",
        run_weekly_linkbuilding, _cron(day_of_week="wed", hour=7, minute=30), catch_up=True,
        gap_cost="geen linkkansen gezocht en geen outreach-concepten voor backlinks klaargezet",
        domain="linkbuilding",
    ),
    # Koershistorie vóór alles wat erop rekent: het finance-dagrapport (07:30)
    # en de beursronde (07:45). Draait ook in het weekend — crypto handelt door,
    # en een maandagronde op vrijdagkoersen is drie dagen blind.
    # priority=0: deze lopen altijd als eerste in de inhaalslag (kritieke
    # rapporten mogen niet achter de zware LLM-briefings aanlopen en in de
    # 20-min-grens vastlopen).
    JobSpec(
        "invest_market_sync", "Beursmeester: koershistorie ophalen",
        run_invest_market_sync, _cron(hour=7, minute=0), catch_up=True,
        priority=0, domain="invest",
    ),
    JobSpec(
        "daily_finance_report", "Finance dagrapport",
        run_finance_daily, _cron(hour=7, minute=30), catch_up=True,
        priority=0, domain="finance",
    ),
    # Gevulde gap_cost: de analyse van vandaag veroudert wél (morgen is er een
    # nieuwe), maar dezelfde ronde toetst óók de stops van de open posities.
    # Die dag komt niet terug — een positie liep dan zonder bescherming.
    JobSpec(
        "invest_daily_cycle", "Beursmeester: stops toetsen + analyse",
        run_invest_cycle, _cron(day_of_week="mon-fri", hour=7, minute=45), catch_up=True,
        gap_cost="stops en koersdoelen van de open posities zijn niet getoetst, "
                 "en er zijn geen beleggingsvoorstellen klaargezet",
        priority=0, domain="invest",
    ),
    JobSpec(
        "content_learning_eval", "Content-leerlus (welke artikel-vorm haalt clicks — wekelijkse evaluatie)",
        run_content_learning_eval, _cron(day_of_week="mon", hour=7, minute=40), catch_up=True,
        domain="publish",
    ),
    JobSpec(
        "seo_optimizer_scan", "SEO Optimizer-scan (interne links, CTR, refresh)",
        run_weekly_optimizer_job, _cron(day_of_week="mon", hour=7, minute=45), catch_up=True,
        gap_cost="geen optimalisatie-suggesties; wegzakkende pagina's zijn een week niet opgemerkt",
        domain="seo",
    ),
    JobSpec(
        "weekly_ga_report", "GA weekrapport",
        run_weekly_report, _cron(day_of_week="mon", hour=8, minute=0), catch_up=True,
        domain="analytics",
    ),
    JobSpec(
        "weekly_finance_report", "Finance weekrapport",
        run_finance_weekly, _cron(day_of_week="mon", hour=8, minute=15), catch_up=True,
        domain="finance",
    ),
    JobSpec(
        "biweekly_content", "Blog + social auto-content (2x/week)",
        run_biweekly_content_job, _cron(day_of_week="tue,fri", hour=9, minute=0), catch_up=True,
        gap_cost="geen artikelen geschreven; de Wachtrij is die ronde niet aangevuld",
        domain="publish",
    ),
    JobSpec(
        "ictusgo_monthly_content_goal", "IctusGo AEO-contentgoal (maandelijks herstarten)",
        partial(_monthly_content_goal, "ictusgo", "G2 — AEO-contentmotor IctusGo", _ICTUSGO_OBJECTIVE),
        _cron(day=1, hour=8, minute=0), domain="goal",
    ),
    JobSpec(
        "weareimpact_monthly_content_goal", "WeAreImpact AEO-contentgoal (maandelijks herstarten)",
        partial(_monthly_content_goal, "weareimpact", "G2 — AEO-contentmotor WeAreImpact", _WEAREIMPACT_OBJECTIVE),
        _cron(day=1, hour=9, minute=0), domain="goal",
    ),
    JobSpec(
        "radar_sky_scan", "Mission Radar sky-scan (concurrenten & trends, elke 4 uur)",
        scan_the_skies, IntervalTrigger(hours=4), domain="radar",
    ),
    JobSpec(
        "content_improver", "Content-verbeteraar (onder-85 artikelen zelf bijschaven, elke 30 min)",
        run_content_improver_job, IntervalTrigger(minutes=30), domain="publish",
    ),
    JobSpec(
        "goal_autoheal", "Doelen-zelfreparatie (verweesde/dubbele doelen)",
        _autoheal_job, IntervalTrigger(minutes=15), misfire_grace_time=300, coalesce=True,
        domain="goal",
    ),
    JobSpec(
        "bridge_sync", "Bridge-sync (review-gates ↔ cloud-companion; besluiten onderweg toepassen)",
        _bridge_sync_job, IntervalTrigger(minutes=_BRIDGE_SYNC_MINUTES),
        misfire_grace_time=120, coalesce=True, domain="bridge",
    ),
    JobSpec(
        "outlook_sync", "Postvak ophalen + triëren (elke 20 min)",
        _postvak_sync_job, IntervalTrigger(minutes=20), misfire_grace_time=600,
        coalesce=True, domain="mail",
    ),
    JobSpec(
        "calendar_sync", "Google Agenda-sync (cache bijwerken, elke 15 min)",
        calendar_sync_job, IntervalTrigger(minutes=15), misfire_grace_time=300, coalesce=True,
        domain="calendar",
    ),
    JobSpec(
        "iris_selfheal", "Iris' zelfherstel (fouten eerst zelf oplossen, pas melden als het niet lukt)",
        _iris_selfheal_job, IntervalTrigger(minutes=10), misfire_grace_time=300, coalesce=True,
        domain="iris",
    ),
    JobSpec(
        "waarheidsaudit", "Waarheidsaudit (zoekt wat stil kapot is, vóór de briefing)",
        _waarheidsaudit_job, _cron(hour=6, minute=40), catch_up=True,
    ),
    JobSpec(
        "iris_briefing_retry", "Iris-herkanselaar (terugval-briefing later alsnog volwaardig)",
        _iris_retry_job, IntervalTrigger(minutes=45), misfire_grace_time=600, coalesce=True,
        domain="iris",
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
        priority=0, domain="researcher",
    ),
]

# Filter vóór de mailbox/social-specs eraan geplakt worden: die laatste zijn
# al project-gescopet via de database (lege tabel op een verse instance =
# vanzelf geen jobs), dus daar is filteren op domain niet nodig — maar de
# vaste _SPECS hierboven draaien ongeacht data, en precies dát maakt de
# whitelist hier nodig (anders synct een klant-instance zonder Beursmeester
# toch elke ochtend koershistorie).
_SPECS = [s for s in _SPECS if domain_enabled(s.domain)]


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
    # Een gemiste run is geen run. Het vuurmoment ging voorbij zónder dat de
    # functie ooit is aangeroepen — er is niets uitgevoerd, dus er valt niets te
    # boeken als "laatste run". Tot 2 aug 2026 zette een misfire wél
    # `last_run_at`, en daardoor voldeed de linkbuilding-weekrun aan de
    # definitie van `downtime.never_succeeded` ("heeft gevuurd, nooit geslaagd"):
    # het Actiecentrum meldde hem als defécte taak terwijl hij simpelweg nooit
    # aan de beurt was geweest omdat de machine uit stond. Twee verschillende
    # storingen met twee verschillende oplossingen, en de kaart noemde de
    # verkeerde. Het gemiste moment zelf gaat niet verloren: dat leeft in
    # `scheduler_gaps` en in `last_missed_at`.
    gemist = status == "missed"
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_runs (job_id, status, last_run_at, last_ok_at,
                                            last_missed_at, error, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status         = excluded.status,
                    -- een misfire heeft niets uitgevoerd: laat de laatste
                    -- échte run staan
                    last_run_at    = COALESCE(excluded.last_run_at, scheduler_runs.last_run_at),
                    -- een mislukte run mag de laatste geslaagde niet wissen:
                    -- anders zou de inhaalslag hem eindeloos blijven herhalen
                    last_ok_at     = COALESCE(excluded.last_ok_at, scheduler_runs.last_ok_at),
                    last_missed_at = COALESCE(excluded.last_missed_at, scheduler_runs.last_missed_at),
                    error          = excluded.error,
                    source         = excluded.source
                """,
                (job_id, status, None if gemist else now, now if status == "ok" else None,
                 now if gemist else None, error, source),
            )
    except Exception:
        # De historie is diagnostiek, geen kritiek pad — een kapotte schrijf mag
        # nooit de job zelf laten omvallen.
        logger.exception("Kon run-historie van '%s' niet wegschrijven", job_id)
    if status == "ok":
        # Geslaagd = de openstaande gaten van deze job zijn dicht, ongeacht of
        # dat kwam doordat iemand op "nu alsnog draaien" drukte of doordat de
        # volgende geplande run gewoon slaagde. Een kaart die blijft staan voor
        # iets dat weer werkt, is dezelfde ruis als een kaart die nooit kwam.
        try:
            from .shared import downtime
            downtime.mark_recovered(job_id)
        except Exception:
            logger.exception("Kon gemiste runs van '%s' niet afsluiten", job_id)


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
        # Gemist ≠ mislukt: gebeurt rond een serverherstart en elke keer dat de
        # machine slaapt of hibernate't — dan lopen alle vuurmomenten in één klap
        # voorbij de grace time. Noem dus niet "de server was niet beschikbaar"
        # als oorzaak (dat is vaak simpelweg onwaar en stuurt het zoeken de
        # verkeerde kant op); noem het geplande moment, dat is het enige feit
        # dat we hier hebben.
        when = getattr(event, "scheduled_run_time", None)
        when_txt = f" van {when.strftime('%d-%m %H:%M')}" if when else ""
        # De geruststelling "draait vanzelf bij de volgende run" is alleen waar
        # voor een job die weleens slaagt. Voor een job die dat nog nooit deed
        # is het een belofte die al maanden niet wordt ingelost, en juist die
        # tekst hield de linkbuilding-weekrun onzichtbaar. Kijk dus eerst.
        ooit_gelukt = bool((_load_runs().get(event.job_id) or {}).get("last_ok_at"))
        staart = ("draait bij de volgende geplande run vanzelf" if ooit_gelukt
                  else "LET OP: deze taak is nog nooit geslaagd — wachten helpt hier niet")
        _record_run(event.job_id, "missed",
                    f"geplande run{when_txt} overgeslagen (machine sliep of server lag "
                    f"even stil) — {staart}")
        logger.warning("Scheduler-job '%s' gemist (misfire)", event.job_id)
        # Ook een misfire tijdens bedrijf is een gat: de opbrengst van die run
        # is er niet, en dat telt even hard als een gat door een uitgezette
        # machine.
        try:
            from .shared import downtime
            spec = _BY_ID.get(event.job_id)
            if spec is not None and when is not None and spec.gap_cost:
                downtime.record_gap(spec.id, spec.label, when,
                                    cost=spec.gap_cost, recoverable=True)
        except Exception:
            logger.exception("Kon gemiste run van '%s' niet vastleggen", event.job_id)
    else:  # EVENT_JOB_EXECUTED
        _record_run(event.job_id, "ok", None)
        logger.info("Scheduler-job '%s' klaar", event.job_id)


# ── Eén uitvoer-poort: dagslot + volgorde ──────────────────────────────────
#
# Waarom (7 aug 2026, gemeten): de laptop stond in slaapstand en werd om 08:33
# gewekt. Er was dus géén koude start — het serverproces leefde nog — en de
# beschermde, chronologische inhaalslag draaide niet. In plaats daarvan speelde
# APScheduler de gemiste vuurmomenten in zijn éígen volgorde af: het
# ochtendrapport om 08:33:49, Iris' briefing pas om 08:38:49. `digest.py` eist
# een briefing van vandáág, dus het rapport ging de deur uit zonder haar advies
# — precies de ketenbreuk waar de gepauzeerde start tegen beschermt, maar dan
# via een pad dat die bescherming niet kende.
#
# De les: de volgorde moet niet aan één mechanisme hangen (de inhaalslag) maar
# aan de jobs zelf. Vandaar twee dingen die op élk pad gelden — koude start,
# ontwaken, misfire-herhaling, handmatige knop:
#   * een dagslot: een inhaalbare job draait hooguit één keer per dag geslaagd,
#     wie hem ook aanzwengelt. Zonder dat betekent "twee mechanismen die allebei
#     werken" simpelweg twee LLM-briefings en twee ochtendrapporten.
#   * een afhankelijkheid: wie een voorganger nodig heeft, zorgt dat die eerst
#     draait (`ensure_ran_today`) in plaats van te hopen op de juiste volgorde.

_JOB_LOCKS: dict[str, asyncio.Lock] = {}


def _job_lock(job_id: str) -> asyncio.Lock:
    lock = _JOB_LOCKS.get(job_id)
    if lock is None:
        lock = _JOB_LOCKS[job_id] = asyncio.Lock()
    return lock


def _ok_vandaag(job_id: str) -> bool:
    """Is deze job vandaag al geslaagd? (lokale dag, niet UTC)"""
    laatste = (_load_runs().get(job_id) or {}).get("last_ok_at")
    if not laatste:
        return False
    try:
        moment = datetime.fromisoformat(laatste)
    except (ValueError, TypeError):
        return False
    if moment.tzinfo is None:
        moment = _TZ.localize(moment)
    return moment.astimezone(_TZ).date() == _now().date()


async def run_spec_once(spec: JobSpec, *, source: str, force: bool = False) -> bool:
    """Draai `spec`, maar hooguit één geslaagde run per dag.

    Retourneert True als hij gedraaid heeft. `force=True` is voor de menselijke
    knop "Nu alsnog draaien": wie er bewust om vraagt, krijgt hem — de rem is er
    tegen mechanismen die elkaar dubbelen, niet tegen Vincent.

    Alleen voor `catch_up`-jobs: dat zijn per definitie cron-jobs die één keer
    per dag iets opleveren. Interval-jobs (mail, radar, bridge) hóren vaker te
    draaien en gaan hier niet langs.
    """
    async with _job_lock(spec.id):
        if not force and spec.catch_up and _ok_vandaag(spec.id):
            logger.info("Job '%s' vandaag al geslaagd — %s slaat over", spec.id, source)
            return False
        await _invoke(spec.func)
        return True


async def _run_spec_geplanned(spec: JobSpec) -> None:
    """Wat APScheduler aanroept. Slaat over wat vandaag al geslaagd is.

    Belangrijk: bij overslaan gooien we niets. De EXECUTED-listener schrijft dan
    `ok` weg met de tijd van nu, en dat klopt ook — de opbrengst van vandaag ís
    er, alleen door een ander pad geleverd.
    """
    await run_spec_once(spec, source="planning")


async def ensure_ran_today(job_id: str) -> bool:
    """Zorg dat een voorganger van vandaag er is vóór je verder gaat.

    Gebruikt door het ochtendrapport voor Iris' briefing. Bewust géén nieuwe
    planning maar dezelfde poort: staat de briefing er al, dan gebeurt er niets;
    draait hij op dit moment elders, dan wachten we op het slot in plaats van
    hem een tweede keer te starten.

    Draait niets vóór het geplande moment: om 05:00 handmatig het ochtendrapport
    opvragen hoort geen briefing van 06:45 naar voren te trekken.
    """
    spec = _BY_ID.get(job_id)
    if spec is None:
        return False
    now = _now()
    if _last_fire_before(spec.trigger, now, _CATCHUP_WINDOW) is None:
        return False  # vandaag nog niet aan de beurt geweest
    try:
        gedraaid = await run_spec_once(spec, source="afhankelijkheid")
    except Exception as e:
        # Wel vastleggen: anders staat er straks een ochtendrapport zónder
        # advies terwijl de historie zegt dat de briefing niets is overkomen.
        _record_run(spec.id, "error", str(e).strip() or type(e).__name__,
                    source="dependency")
        logger.exception("Voorganger '%s' kon niet draaien — de aanroeper gaat "
                         "verder met wat er wél is", job_id)
        return False
    if gedraaid:
        _record_run(spec.id, "ok", None, source="dependency")
    return gedraaid


# ── Inhaalslag ─────────────────────────────────────────────────────────────

def _fires_between(trigger: BaseTrigger, start: datetime, end: datetime,
                   limit: int = 200) -> list[datetime]:
    """Alle geplande vuurmomenten in [start, end].

    Nodig om stilstand te kúnnen tellen: `scheduler_runs` bewaart één rij per
    job, dus daaruit is "vier keer niet gedraaid" niet af te leiden — die rij
    zegt alleen dat de laatste run op 27 juli slaagde. De trigger zelf weet wél
    hoe vaak hij intussen had moeten vuren.
    """
    out: list[datetime] = []
    fire = trigger.get_next_fire_time(None, start)
    for _ in range(limit):
        if fire is None or fire > end:
            break
        out.append(fire)
        nxt = trigger.get_next_fire_time(fire, end)
        if nxt is None or nxt <= fire:
            break
        fire = nxt
    return out


def _seed_first_seen(now: datetime, baseline: datetime) -> list[str]:
    """Leg per job vast vanaf wanneer hij bestaat. Retourneert de nieuwe jobs.

    `_baseline` doet dit al voor de hele installatie, met precies de juiste
    reden: "een verse installatie zou meteen twee weken stilstand rapporteren
    over runs die nooit hadden hoeven draaien." Diezelfde redenering geldt per
    job — een JobSpec die vandaag wordt toegevoegd, is voor zichzelf een verse
    installatie, hoe oud de rest ook is.

    Zonder dit rekent `_record_downtime_gaps` het hele trigger-verleden toe aan
    een job van gisteren: bij het toevoegen van `invest_daily_cycle` (2 aug
    2026) verscheen meteen de kaart *"Beursmeester: stops toetsen + analyse
    draaide 9× tussen 21-07 en 31-07 niet — stops en koersdoelen van de open
    posities zijn niet getoetst"*. Er waren op 21 juli geen posities, geen
    stops en geen job. Een zelfverzekerde zin met een knop eronder over werk
    dat nooit heeft bestaan is precies de soort onwaarheid waar de
    waarheidsaudit voor is.

    Bekende jobs krijgen de installatie-nulmeting, zodat hun bestaande
    meldgedrag ongewijzigd blijft; alleen écht nieuwe job-id's krijgen `now`.
    """
    bekend = {jid for jid in runs_ids()}
    nieuw: list[str] = []
    with get_conn() as conn:
        for spec in _SPECS:
            eerste = now if spec.id not in bekend else baseline
            cur = conn.execute(
                "INSERT INTO scheduler_runs (job_id, status, first_seen_at, source) "
                "VALUES (?, 'onbekend', ?, 'seed') "
                "ON CONFLICT(job_id) DO UPDATE SET first_seen_at = "
                "COALESCE(scheduler_runs.first_seen_at, excluded.first_seen_at)",
                (spec.id, eerste.isoformat()),
            )
            if spec.id not in bekend and cur.rowcount:
                nieuw.append(spec.id)
    if nieuw:
        logger.info("Nieuwe geplande taak/taken herkend (geen stilstand vóór nu): %s",
                    ", ".join(nieuw))
    return nieuw


def runs_ids() -> set[str]:
    """Job-id's die al historie hebben. Los van `_load_runs` omdat we hier
    alleen de sleutels nodig hebben, vóórdat de seed ze allemaal aanmaakt."""
    try:
        with get_conn() as conn:
            return {r["job_id"] for r in conn.execute(
                "SELECT job_id FROM scheduler_runs WHERE first_seen_at IS NOT NULL "
                "OR last_run_at IS NOT NULL OR last_missed_at IS NOT NULL")}
    except Exception:
        logger.exception("Kon bekende job-id's niet lezen")
        return set()


def _record_downtime_gaps(now: datetime, runs: dict[str, dict],
                          baseline: datetime) -> int:
    """Leg vast welke geplande runs overgingen terwijl de machine uit stond.

    Draait ná de inhaalslag-selectie en telt bewust óók de runs die we
    níet inhalen. Dat onderscheid is de kern: een ochtendrapport van gisteren
    alsnog mailen helpt niemand (dus niet inhalen), maar een outreach-batch die
    vier werkdagen niet draaide is werk dat nooit meer gebeurt — en dat hoort
    iemand te weten (dus wél vastleggen).
    """
    from .shared import downtime

    _seed_first_seen(now, baseline)
    runs = _load_runs()   # opnieuw lezen: de seed heeft first_seen_at gezet
    horizon = max(baseline, now - timedelta(days=downtime.LOOKBACK_DAYS))
    nieuw = 0
    for spec in _SPECS:
        if not spec.catch_up:
            continue  # interval-jobs komen vanzelf weer langs
        run = runs.get(spec.id) or {}
        last_ok = None
        if run.get("last_ok_at"):
            try:
                last_ok = datetime.fromisoformat(run["last_ok_at"])
            except (ValueError, TypeError):
                last_ok = None
        # Een job kan alleen vuurmomenten missen die ná zijn bestaan liggen.
        job_horizon = horizon
        if run.get("first_seen_at"):
            try:
                job_horizon = max(horizon, datetime.fromisoformat(run["first_seen_at"]))
            except (ValueError, TypeError):
                pass
        for fire in _fires_between(spec.trigger, job_horizon, now):
            if fire.date() == now.date():
                continue  # dat is het werkterrein van de inhaalslag zelf
            if last_ok and last_ok >= fire:
                continue  # die run is gewoon gedraaid
            if downtime.record_gap(spec.id, spec.label, fire,
                                   cost=spec.gap_cost,
                                   recoverable=bool(spec.gap_cost)):
                nieuw += 1
    return nieuw


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


def _baseline(runs: dict[str, dict], now: datetime) -> datetime | None:
    """Het moment waarop deze installatie voor het eerst historie bijhield.

    None = er is nog geen nulmeting; die wordt dan gezet. Vóór de nulmeting
    weten we niets, en dan is zowel inhalen als "gemist"-melden giswerk: een
    verse installatie zou meteen twee weken stilstand rapporteren over runs
    die nooit hadden hoeven draaien.
    """
    row = runs.get(_BASELINE_ID)
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row["last_run_at"])
    except (ValueError, TypeError):
        return now - _CATCHUP_WINDOW


def _job_baseline(runs: dict[str, dict], job_id: str, baseline: datetime) -> datetime:
    """De vroegste datum waarvan déze job iets mag inhalen.

    Naast de installatie-nulmeting telt het moment waarop de job zelf voor het
    eerst bestond (`first_seen_at`, gezet door `_seed_first_seen`). Zonder dat
    haalt een JobSpec die vandaag wordt toegevoegd runs in van vóór zijn eigen
    bestaan — dezelfde fout als de kaart "draaide 9× niet" over een periode
    waarin er geen posities, geen stops en geen job waren. Voor een verse job
    geldt wat voor een verse installatie geldt.
    """
    ondergrens = baseline
    eerste = (runs.get(job_id) or {}).get("first_seen_at")
    if eerste:
        try:
            ondergrens = max(ondergrens, datetime.fromisoformat(eerste))
        except (ValueError, TypeError):
            pass
    return ondergrens


def _pending_catchups(now: datetime) -> list[tuple[datetime, JobSpec]]:
    runs = _load_runs()

    # Nulmeting. Zonder historie weten we niet wat er gisteren al gedraaid heeft,
    # en zouden we bij de allereerste start met deze code prompt een ochtendrapport
    # mailen en een outreach-batch bouwen voor runs die al lang gedaan zijn.
    # Eerste boot: niets inhalen, alleen de meetlat neerleggen.
    baseline = _baseline(runs, now)
    if baseline is None:
        _record_run(_BASELINE_ID, "ok", None, source="seed")
        logger.info("Scheduler-historie leeg — nulmeting gezet, geen inhaalslag bij deze start")
        return []

    pending: list[tuple[datetime, JobSpec]] = []
    for spec in _SPECS:
        if not spec.catch_up:
            continue
        # Hoe ver we terugkijken volgt uit wat de opbrengst waard is — precies
        # hetzelfde onderscheid dat `gap_cost` maakt bij het melden.
        venster = _GAP_CATCHUP_WINDOW if spec.gap_cost else _CATCHUP_WINDOW
        fire = _last_fire_before(spec.trigger, now, venster)
        if fire is None or fire < _job_baseline(runs, spec.id, baseline):
            continue
        if fire.date() != now.date():
            if not spec.gap_cost:
                # Het laatste vuurmoment lag op een eerdere dag. De opbrengst van
                # déze jobs (rapport, briefing, sync met terugkijkvenster)
                # veroudert per dag; die van gisteren alsnog draaien levert niets
                # op — vandaag draait hij toch, en het ochtendrapport van gisteren
                # mailen terwijl dat van vandaag eraan komt maakt de inbox
                # onbetrouwbaar.
                continue
            volgende = spec.trigger.get_next_fire_time(None, now)
            if volgende is not None and volgende.date() == now.date():
                # Hij komt vandaag zelf nog langs. Boot je om 06:57, dan is het
                # laatste vuurmoment van de outreach-batch gisteren 07:15 — maar
                # over achttien minuten draait die van vandaag. Inhalen zou twee
                # batches binnen het uur betekenen.
                continue
        last_ok = (runs.get(spec.id) or {}).get("last_ok_at")
        if last_ok:
            try:
                if datetime.fromisoformat(last_ok) >= fire:
                    continue  # die run is al gedaan
            except ValueError:
                pass  # onleesbare tijdstempel: liever inhalen dan overslaan
        pending.append((fire, spec))
    # Sorteer op prioriteit (laag = eerst) en daarna op tijd, zodat kritieke
    # rapporten (Finance, Beursmeester, kennisronde) vóór de zware LLM-briefings
    # draaien. Anders kapte de 20-min-grens de staart af en bleven juist de
    # kritieke jobs liggen.
    pending.sort(key=lambda p: (p[1].priority, p[0]))
    return pending


def _report_downtime(now: datetime) -> None:
    """Registreer en meld wat er niet gebeurde terwijl de machine uit stond.

    Mag nooit de opstart breken: dit is diagnostiek. Een scheduler die niet
    start omdat het bijhouden van gemiste runs stukging, is oneindig veel
    erger dan het gat dat we niet zagen.
    """
    try:
        from .shared import downtime
        runs = _load_runs()
        baseline = _baseline(runs, now)
        if baseline is None:
            return  # verse installatie: er valt niets te missen
        nieuw = _record_downtime_gaps(now, runs, baseline)
        # Een job die is ingepland, heeft gevuurd en nog nóóit slaagde is geen
        # stilstand maar een defect. Die escaleert los, want "draait bij de
        # volgende geplande run vanzelf" is voor hem aantoonbaar onwaar — de
        # linkbuilding-weekrun leefde maanden op precies die belofte.
        kapot = downtime.never_succeeded(runs, [s.id for s in _SPECS])
        for jid in kapot:
            spec = _BY_ID.get(jid)
            downtime.report_never_succeeded(
                jid, spec.label if spec else jid, (runs.get(jid) or {}).get("error") or "")
        downtime.prune()
        if nieuw or kapot:
            logger.warning(
                "Stilstand: %d nieuw gemist vuurmoment(en), %d job(s) nog nooit geslaagd",
                nieuw, len(kapot))
    except Exception:
        logger.exception("Kon gemiste runs niet vastleggen (niet fataal)")


async def _run_catchups() -> None:
    """Haalt gemiste runs op in de volgorde waarin ze hadden moeten draaien.

    Strikt sequentieel: het ochtendrapport hoort Iris' briefing van vandaag te
    zien, en Iris hoort de GSC-cijfers van vanochtend te zien. Een job die
    struikelt houdt de rest niet tegen — een mislukte GSC-sync is geen reden om
    ook de briefing over te slaan.
    """
    now = _now()
    # Eerst vastleggen wát er is overgegaan, dan pas inhalen wat vandaag nog
    # zin heeft. Andersom zou de inhaalslag zijn eigen sporen uitwissen.
    _report_downtime(now)
    pending = _pending_catchups(now)
    if not pending:
        return
    logger.info("Inhaalslag: %d job(s) gemist terwijl de server uit stond", len(pending))
    # Wat er nog moet: hierop rust de melding als de tijdgrens de staart afkapt.
    _catchup_rest.clear()
    _catchup_rest.extend(spec.label for _, spec in pending)
    for fire, spec in pending:
        logger.warning("Inhaalslag '%s' — had om %s moeten draaien",
                       spec.id, fire.strftime("%d-%m %H:%M"))
        try:
            gedraaid = await run_spec_once(spec, source="inhaalslag")
        except Exception as e:
            _record_run(spec.id, "error", str(e).strip() or type(e).__name__, source="catchup")
            logger.exception("Inhaalslag '%s' gefaald", spec.id)
        else:
            if gedraaid:
                _record_run(spec.id, "ok", None, source="catchup")
                logger.info("Inhaalslag '%s' klaar", spec.id)
            else:
                logger.info("Inhaalslag '%s' overgeslagen — vandaag al geslaagd", spec.id)
        finally:
            # Gelukt of gefaald: hij is langsgekomen. Alleen wat de tijdgrens
            # nooit heeft bereikt blijft staan.
            if spec.label in _catchup_rest:
                _catchup_rest.remove(spec.label)


#: Labels van inhaalruns die nog moeten; leeg zodra de ronde klaar is.
_catchup_rest: list[str] = []


def _meld_afgekapte_inhaalslag() -> None:
    """De tijdgrens kapte de inhaalslag af — zeg wat er dus níét is gebeurd.

    De grens zelf blijft: onbeperkt wachten betekent dat één hangende LLM-call
    de planning van de hele dag ophoudt. Maar afkappen zonder melden is precies
    de faalmodus die dit bestand bestrijdt — een taak die overgeslagen wordt
    zonder dat iemand het ziet. Jobs met een `gap_cost` houden hun eigen kaart
    mét inhaalknop; deze kaart is er voor de rest, en voor het overzicht.
    """
    if not _catchup_rest:
        return
    try:
        from .shared.outcomes import log_outcome
        log_outcome(
            "Systeem", "inhaalslag_afgekapt",
            f"De inhaalslag bij het opstarten liep tegen de tijdgrens van "
            f"{int(_CATCHUP_TIMEOUT.total_seconds() // 60)} minuten aan. Niet meer "
            f"gedraaid vandaag: {', '.join(_catchup_rest)}.",
            status="error",
            next_step="Start de overgebleven taken handmatig via het Actiecentrum "
                      "(knop 'Nu alsnog draaien'), of laat ze morgen op hun eigen "
                      "tijd lopen.",
        )
    except Exception:
        logger.exception("Kon de afgekapte inhaalslag niet melden (niet fataal)")


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
        _meld_afgekapte_inhaalslag()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Inhaalslag onverwacht gefaald — scheduler wordt alsnog hervat")
    finally:
        _resume_scheduler()


#: Hoe vaak de hartslag tikt, en vanaf welk gat we concluderen dat de machine
#: weg was. Ruim boven de tik zodat een trage seconde of een drukke event loop
#: geen "ontwaken" is; ruim onder het uur zodat een middagdutje wél telt.
_HARTSLAG_SECONDEN = 60
_WAKE_DREMPEL = timedelta(minutes=5)

_laatste_hartslag: datetime | None = None


async def _hartslag() -> None:
    """Merk op dat de machine heeft geslapen, en behandel dat als een start.

    Aanleiding (7 aug 2026): een koude start is niet het enige moment waarop de
    ochtendketen inhaalwerk nodig heeft. De laptop gaat 's avonds dicht en 's
    ochtends open; het serverproces leeft dan gewoon door, dus `start_scheduler`
    draait niet en de beschermde inhaalslag ook niet. Wat er dan gebeurde: het
    ochtendrapport draaide vijf minuten vóór Iris' briefing en ging zonder haar
    advies de deur uit.

    De klok is hier de enige betrouwbare getuige: sliep de machine, dan springt
    hij vooruit terwijl deze taak niet draaide. Suspend, hibernate, een
    dichtgeklapte laptop en een bevroren proces zien er van binnenuit hetzelfde
    uit — en ze verdienen alle vier dezelfde behandeling.
    """
    global _laatste_hartslag
    nu = _now()
    vorige, _laatste_hartslag = _laatste_hartslag, nu
    if vorige is None:
        return
    gat = nu - vorige
    if gat < _WAKE_DREMPEL:
        return
    logger.warning("Machine was %s weg (slaapstand of bevroren) — inhaalslag als "
                   "bij een koude start", str(gat).split(".")[0])
    if _catchup_task is not None and not _catchup_task.done():
        logger.info("Inhaalslag loopt al — geen tweede ronde")
        return
    _start_catchup_ronde()


def _start_catchup_ronde() -> None:
    """Pauzeer, haal geordend in, hervat. Eén ronde tegelijk."""
    global _catchup_task
    try:
        if _scheduler is not None and _scheduler.state != STATE_PAUSED:
            _scheduler.pause()
    except Exception:
        logger.exception("Kon de scheduler niet pauzeren voor de inhaalslag")
    _catchup_task = asyncio.create_task(_startup_catchup())


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
            # Via de poort, niet rechtstreeks: bij het ontwaken uit slaapstand
            # herhaalt APScheduler de gemiste vuurmomenten terwijl de
            # wake-inhaalslag dezelfde jobs geordend afwerkt. Zonder dagslot
            # levert dat twee briefings en twee ochtendrapporten op.
            partial(_run_spec_geplanned, spec),
            spec.trigger,
            id=spec.id,
            replace_existing=True,
            misfire_grace_time=spec.misfire_grace_time,
            coalesce=spec.coalesce,
        )
    _scheduler.add_listener(
        _on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED
    )
    # Hartslag: detecteert dat de machine heeft geslapen. Bewust géén JobSpec —
    # hij levert niets op, en in de takenlijst van het dashboard zou hij alleen
    # ruis zijn. `misfire_grace_time=None` want juist de gemiste hartslag ná een
    # slaapstand is het signaal dat we nodig hebben.
    _scheduler.add_job(
        _hartslag, IntervalTrigger(seconds=_HARTSLAG_SECONDEN),
        id="__hartslag__", replace_existing=True,
        misfire_grace_time=None, coalesce=True,
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


# Sterke referenties naar handmatig gestarte inhaalruns (zie `run_job_now`).
_manual_tasks: set[asyncio.Task] = set()


async def _run_and_record(spec: JobSpec) -> dict:
    try:
        # `force`: wie op "Nu alsnog draaien" klikt, krijgt hem — ook als hij
        # vandaag al liep. Het slot blijft wél gelden, zodat de knop niet
        # bovenop een lopende run duikt.
        await run_spec_once(spec, source="handmatig", force=True)
    except Exception as e:
        _record_run(spec.id, "error", str(e).strip() or type(e).__name__, source="manual")
        logger.exception("Handmatige inhaalslag '%s' gefaald", spec.id)
        return {"ok": False, "job_id": spec.id, "label": spec.label,
                "error": str(e).strip() or type(e).__name__}
    # `_record_run(..., 'ok')` sluit de openstaande gaten van deze job.
    _record_run(spec.id, "ok", None, source="manual")
    logger.info("Handmatige inhaalslag '%s' klaar", spec.id)
    return {"ok": True, "job_id": spec.id, "label": spec.label}


async def run_job_now(job_id: str, background: bool = True) -> dict:
    """Draai een geplande taak alsnog, op verzoek.

    Dit is wat een "gemiste run"-melding pas nuttig maakt: zonder deze knop
    weet je alleen dát er werk is overgeslagen, en dan is de melding een
    verwijt in plaats van een oplossing.

    Twee grenzen. (a) Alleen jobs met `catch_up=True`: de rest is óf een
    interval-job die vanzelf langskomt, óf een job met een blijvend
    neveneffect (de maandelijkse doel-jobs maken doelen aan — die twee keer
    draaien laat werk dubbel lopen). (b) Geen enkele job hier publiceert of
    verstuurt iets; alles landt achter de bestaande review-gates. Handmatig
    starten verandert daar niets aan.
    """
    spec = _BY_ID.get(job_id)
    if spec is None:
        raise KeyError(f"Onbekende taak '{job_id}'")
    if not spec.catch_up:
        raise ValueError(
            f"'{spec.label}' kan niet handmatig ingehaald worden — deze taak komt "
            "vanzelf weer langs of heeft een blijvend neveneffect.")
    logger.warning("Handmatige inhaalslag '%s' gestart", job_id)
    if not background:
        return await _run_and_record(spec)
    # Op de achtergrond: een contentronde of outreach-batch duurt minuten en
    # zou de HTTP-call laten aflopen. De uitkomst is niet verloren — de run
    # schrijft zichzelf naar `scheduler_runs`, sluit bij succes de gaten, en
    # een mislukking komt als foutkaart terug in hetzelfde Actiecentrum.
    #
    # De referentie vasthouden is geen netheid maar noodzaak: de event loop
    # bewaart alleen een zwakke verwijzing, dus een taak waarvan niemand de
    # handle bijhoudt kan halverwege door de garbage collector worden
    # opgeruimd. Dan staat er "gestart" in de log, verdwijnt de run zonder
    # spoor, en blijft het gat open zonder dat iemand weet waarom.
    task = asyncio.create_task(_run_and_record(spec))
    _manual_tasks.add(task)
    task.add_done_callback(_manual_tasks.discard)
    return {"ok": True, "started": True, "job_id": job_id, "label": spec.label,
            "note": "Draait op de achtergrond — de kaart verdwijnt zodra hij slaagt."}


def get_scheduler_status() -> dict:
    """Status van alle scheduler-jobs t.b.v. de monitoring-UI."""
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "catching_up": False, "paused": False, "jobs": []}

    runs = _load_runs()
    jobs = []
    for job in _scheduler.get_jobs():
        spec = _BY_ID.get(job.id)
        run = runs.get(job.id)
        # Een rij die alleen `first_seen_at` draagt is geen run-historie maar
        # een registratie dát de job bestaat (zie _seed_first_seen). Die mag
        # nooit als uitvoering lezen: `null` betekent hier "heeft nog nooit
        # gedraaid", en dat onderscheid is precies waar de inhaalslag op stuurt.
        if run and not run.get("last_run_at") and not run.get("last_missed_at"):
            run = None
        jobs.append(
            {
                "id": job.id,
                "label": spec.label if spec else job.id,
                "trigger": str(job.trigger),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                # Blijft over herstarts heen bestaan: null betekent nu écht
                # "heeft nog nooit gedraaid".
                # `time` is het moment waarop deze status ontstond: bij een
                # misfire is dat het overgeslagen vuurmoment, niet een run —
                # `last_run_at` blijft dan leeg (of staat op de vorige échte run).
                "last_run": {
                    "status": run["status"],
                    "time": (run["last_missed_at"] if run["status"] == "missed"
                             else run["last_run_at"]) or run["last_run_at"],
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

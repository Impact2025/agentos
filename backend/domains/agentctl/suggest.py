"""Agent Control — suggestie-engine (Iris' staf: aansturing van de agents).

Dit IS Iris' aanstuur-mechanisme. Ze leest de harde per-project cijfers
(4 pijlers + trend), bepaalt per project de grootste hefboom, en koppelt die
aan de expert-agent (via het gezicht Mara/Bram/Noor) die 'm uitvoert.

Deterministisch, geen LLM — Iris beslist op feiten, niet op een gok.
`suggest()` toont de aanstuur-beslissing (voor de UI); `execute_all()` voert
haar besluit uit door de juiste expert aan het werk te zetten.

Regel: de laagste pijler van een project = de grootste kans. Eén aansturing
per project (de top-hefboom), gesorteerd op potentieel. Zo blijft Iris' stal
overzichtelijk bezet i.p.v. alles tegelijk te bombarderen.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from ..iris import metrics as iris_metrics
from . import service as agentctl_service

logger = logging.getLogger(__name__)

# Hoelang een content-pijler Gauntlet-run mag lopen vóór we 'm loslaten
# (zelfde grens als orchestrator/service.py:process_one_under_threshold).
_CONTENT_MAX_WAIT_S = 600

# Pijler -> (agent-profielnaam, taak-template)
# Taak-template krijgt {project} en {detail} ingevuld.
_PILLAR_AGENT = {
    "content": (
        "SEO Copywriter",
        "Schrijf 1 nieuw SEO-artikel (≥900 woorden, E-E-A-T, Vincent-stijl) voor "
        "{project} rond de zoekterm met de grootste klik-kans volgens GSC.",
    ),
    "seo": (
        "SEO Editor",
        "Optimaliseer de 3 zwakst scorende pagina's van {project}: titel/snippet/CTR "
        "en interne links, zodat de gemiddelde positie en CTR stijgen.",
    ),
    "uitvoering": (
        "Content Editor",
        "Pak de vastgelopen doelen/taken van {project} op: hervat de mislukte deeltaken "
        "en rond de lopende doelen af.",
    ),
    "hygiene": (
        "Content Judge",
        "Controleer de uitkomst-feed van {project} op fouten en needs_work-jobs en herstel "
        "de hygiëne (afgekapte titels, gebroken links, indexatie-sabotage).",
    ),
}


# Pijlers die bewust géén agent hebben: ze staan in `metrics.project_scores`
# als inzicht voor Iris' briefing en de bijbehorende tab, niet als werk dat een
# agent kan oppakken. Expliciet opsommen (in plaats van "alles wat niet in
# _PILLAR_AGENT staat negeren we") zodat een nieuwe pijler een besluit afdwingt
# in plaats van stil uit de suggesties te verdwijnen — dat is precies wat de
# invariant `suggestie_pijler_zonder_agent` toetst.
_INFORMATIEVE_PIJLERS = {"geo"}


def _pillar_label(key: str) -> str:
    return {
        "content": "Content",
        "seo": "SEO",
        "uitvoering": "Uitvoering",
        "hygiene": "Hygiëne",
    }.get(key, key)


def suggest() -> Dict[str, Any]:
    """Bereken de top-acties per project op basis van de Iris-cijfers.

    Returns:
      suggestions: lijst van dicts {project, pillar, agent, task, priority, grade}
      generated_at, count
    """
    try:
        scores = iris_metrics.project_scores()
    except Exception as exc:
        logger.exception("Iris scores ophalen mislukt")
        return {"suggestions": [], "error": str(exc), "generated_at": "", "count": 0}

    out: List[Dict[str, Any]] = []
    for p in scores:
        project = p["project"]
        grade = p.get("grade") or 0
        pillars = p.get("pillars", {})
        if not pillars:
            continue
        # Laagste pijler = grootste hefboom.
        #
        # Alleen de pijlers waarvoor een agent bestaat doen mee (16 aug 2026).
        # `metrics.project_scores` levert sinds kort een vijfde pijler `geo` mee
        # die per docstring "niet meegeteld" is in de totaalscore, maar wél in
        # `pillars` staat — en dus door iedereen die erover itereert wél werd
        # meegeteld. Twee storingen tegelijk: (a) een site zonder GEO-scan heeft
        # `score: None`, en `.get("score", 0)` vangt een ontbrekende sleutel af,
        # geen aanwezige None — de sort viel om met "'<' not supported between
        # 'NoneType' and 'int'" en nam de hele scheduler-job mee (iris_auto_deploy
        # stond een etmaal op error); (b) `geo` staat op schaal 0-100 tussen
        # pijlers van 0-25, dus een site met een lage GEO-score zou 'm als
        # zwakste aanwijzen, in `_PILLAR_AGENT` niets vinden en via `continue`
        # stilzwijgend géén suggestie opleveren. Filteren op de agent-tabel lost
        # beide op en houdt de set automatisch synchroon met wat we kunnen
        # uitvoeren: een pijler zonder mechanisme is geen suggestie.
        kandidaten = [
            (k, v) for k, v in pillars.items()
            if k in _PILLAR_AGENT and isinstance((v or {}).get("score"), (int, float))
        ]
        if not kandidaten:
            continue
        ranked = sorted(kandidaten, key=lambda kv: (kv[1]["score"], kv[0]))
        weakest_key, weakest = ranked[0]
        # Alleen voorstellen doen als er écht wat te winnen valt (< 90% van de pijler)
        if weakest.get("score", 0) >= 22:  # pijler max 25 -> 22 is solide
            continue
        agent_name, tmpl = _PILLAR_AGENT.get(weakest_key, (None, None))
        if not agent_name:
            continue
        detail = weakest.get("note") or ""
        task = tmpl.format(project=project, detail=detail)
        out.append({
            "project": project,
            "pillar": _pillar_label(weakest_key),
            "pillar_key": weakest_key,
            "pillar_score": weakest.get("score", 0),
            "agent": agent_name,
            "task": task,
            "grade": grade,
            # Prioriteit: lagere pijlerscore + lagere grade = hoger
            "priority": round((25 - weakest.get("score", 0)) + (10 - grade), 1),
        })

    # Sorteer op prioriteit (hoogste eerst), dan grade
    out.sort(key=lambda s: (-s["priority"], s["grade"]))

    # Losse agent-id's bijvoegen voor de frontend (zodat die direct kan deployen)
    with _conn() as conn:
        name_to_id = {
            r["name"]: r["id"]
            for r in conn.execute("SELECT id, name FROM agent_profiles")
        }
    for s in out:
        s["agent_id"] = name_to_id.get(s["agent"])

    return {
        "suggestions": out,
        "count": len(out),
        "generated_at": agentctl_service._now(),
    }


def _conn():
    """Hergebruik de gedeelde connectiefabriek uit agentctl.service."""
    from ...shared.database import get_conn
    return get_conn()


# ── agentctl_deploys: boekhouding + dedupe ────────────────────────────────
# Elke pijler-uitvoering krijgt een eigen rij i.p.v. het oude fire-and-forget
# event, want niets in de codebase las het `run_id` van dat event ooit terug —
# 13 Gauntlet-runs per klik zonder landingsplek in Wachtrij/Actiecentrum. Zie
# CLAUDE.md: "activiteit is geen effect".

def _today_has_deploy(project: str, pillar: str) -> bool:
    """Max 1×/dag per (project, pijler) — en niet alleen tegen agentctl's eigen
    historie: `pillar_guard` kijkt óók of Iris' briefing deze pijler vandaag al
    voor dit project aanpakte (content_run/seo_refresh). Zonder die
    cross-check start `iris_auto_deploy` (07:00) vlak na Iris' 06:45-briefing
    alsnog een tweede, volledige Gauntlet-run voor dezelfde site — zie
    `iris/pillar_guard.py` voor het gemeten incident."""
    from ..iris import pillar_guard
    return pillar_guard.pillar_handled_today(project, pillar)


def _record_deploy(*, run_id: str = "", project: str, pillar: str, agent: str,
                   task: str, status: str, artifact: str = "") -> int:
    # created_at via SQLite's eigen datetime('now') — niet Python's _now() (ISO
    # met 'T' en tijdzone-suffix): _today_has_deploy en de integrity-invariant
    # vergelijken met datetime('now', ...)/date('now'), en die vergelijking is
    # alleen betrouwbaar tegen hetzelfde stringformaat. Zelfde regel als
    # activity_log (shared/outcomes.py).
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO agentctl_deploys "
            "(run_id, project, pillar, agent, task, status, artifact, created_at, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,datetime('now'),'')",
            (run_id, project, pillar, agent, task, status, artifact),
        )
        return cur.lastrowid


def _resolve_deploy(deploy_id: int, *, status: str, artifact: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE agentctl_deploys SET status=?, artifact=?, resolved_at=datetime('now') WHERE id=?",
            (status, artifact, deploy_id),
        )


# ── Per-pijler uitvoering ──────────────────────────────────────────────────
# Elke pijler routeert naar het echte, al-gepoorte mechanisme voor dat werk
# i.p.v. een generieke Gauntlet-opstel zonder tool-access. Alleen 'content'
# (een nieuw artikel) is werk waar een verse Gauntlet-run bij past — en die
# krijgt nu een landingsplek in de Wachtrij (zelfde patroon als
# orchestrator/service.py:process_one_under_threshold).

async def _execute_seo(project: str, agent: str, task: str) -> Dict[str, Any]:
    """SEO-pijler: geen Gauntlet. Gebruikt de échte CTR-optimizer
    (seo/optimizer.py:optimize_query), gegrond op GSC-paginadata, voor de
    quick wins uit het laatste weekrapport (analytics/insights.py)."""
    from ..seo import sites as sites_service
    from ..seo import optimizer as seo_optimizer
    from ..analytics import insights
    from ...shared.outcomes import log_outcome

    deploy_id = _record_deploy(project=project, pillar="seo", agent=agent, task=task, status="running")

    site = sites_service.find_site_by_project(project)
    queries = insights.quick_wins_for(project, limit=3)
    if not site or not queries:
        reason = "geen site-koppeling gevonden" if not site else "geen quick wins in het laatste weekrapport"
        _resolve_deploy(deploy_id, status="no_effect")
        return {"project": project, "pillar": "seo", "agent": agent, "ok": False, "reason": reason}

    varianten_ok = 0
    gefaald = 0
    for q in queries:
        query = q.get("query")
        if not query:
            continue
        try:
            result = await seo_optimizer.optimize_query(site, query)
        except Exception:
            logger.exception("optimize_query mislukt voor %s / %s", project, query)
            gefaald += 1
            continue
        if result.get("outcome") == "varianten":
            varianten_ok += 1
        else:
            gefaald += 1

    artifact = f"/#project={project}"
    if varianten_ok:
        detail = (f"{varianten_ok} zoekwoord(en) met nieuwe titel/meta-varianten "
                  "klaar in Optimalisatie.")
        _resolve_deploy(deploy_id, status="staged", artifact=artifact)
        log_outcome(project, "agentctl_seo", detail, artifact=artifact,
                    next_step="Bekijk de titel/meta-varianten in Optimalisatie en pas de beste toe.",
                    status="ok")
        return {"project": project, "pillar": "seo", "agent": agent, "ok": True,
                "detail": detail, "artifact": artifact}

    detail = "Geen bruikbare varianten voor de quick wins van dit project."
    _resolve_deploy(deploy_id, status="no_effect")
    log_outcome(project, "agentctl_seo", detail, status="error" if gefaald else "ok")
    return {"project": project, "pillar": "seo", "agent": agent, "ok": False, "reason": detail}


def _execute_content(project: str, agent: str, task: str, agent_id: Optional[int]) -> Dict[str, Any]:
    """Content-pijler: spawnt écht een Gauntlet-run (nieuw artikel schrijven —
    het enige werk waar dat voor past) en start een achtergrond-poller die de
    uitkomst afsluit. Fire-and-forget richting de caller (de HTTP-respons hoeft
    niet op minuten Gauntlet-werk te wachten), maar niet meer fire-and-forget
    richting het systeem: de poller sluit de deploy-rij en de outcome-kaart
    altijd af, ongeacht de uitkomst.

    Benchmark expliciet meegeven (i.p.v. deploy_agent's generieke terugval
    "lever concreet bruikbaar werk") is niet cosmetisch: gemeten op 13 aug 2026
    liet die vage lat elke pijler-run — óók "optimaliseer 3 pagina's",
    "hervat vastgelopen doelen" — de drempel halen en automatisch naar de
    Wachtrij gaan via gauntlet/service.py:_auto_queue_run (bedoeld voor exact
    déze route, ongewijzigd gebleven), met titels als "[SEO Editor]
    Optimaliseer de 3 zwakst scorende pagina's van ..." als 'hook'-content.
    Een échte artikel-lat + de `project '{project}'`-frase (die _auto_queue_run
    met een regex leest om de juiste site te resolven) samen lossen dat op.
    """
    if not agent_id:
        return {"project": project, "pillar": "content", "agent": agent,
                "ok": False, "reason": "geen agent-id"}
    # Doorvoer-rem: dezelfde grens als iris/actions.py:content_run. Zonder
    # deze check schrijft deze pijler gewoon door terwijl de Wachtrij al
    # propvol staat — een artikel erbij levert dan niets op, het maakt de
    # stapel waar de opbrengst vandaan moet komen alleen hoger (CLAUDE.md:
    # "doorvoer boven productie"). Vóór 22 aug 2026 had alleen Iris' eigen
    # content_run deze rem; deze route (Gauntlet, duurder per run) had 'm niet.
    from ..iris import actions as iris_actions
    from ..seo import sites as sites_service
    site = sites_service.find_site_by_project(project)
    if site:
        wachtrij = iris_actions.pending_review_count(site["id"])
        if wachtrij >= iris_actions.QUEUE_JAM:
            deploy_id = _record_deploy(project=project, pillar="content", agent=agent,
                                       task=task, status="no_effect")
            reason = (f"NIET gestart: er wachten al {wachtrij} concepten op goedkeuring "
                      f"voor {project} — eerst de Wachtrij beoordelen.")
            return {"project": project, "pillar": "content", "agent": agent,
                    "ok": False, "reason": reason}
    benchmark = (
        f"BENCHMARK voor project '{project}': een publicatie-klaar SEO-artikel "
        "van minstens 900 woorden, Nederlands, eerste persoon waar passend, "
        "geen verzonnen cijfers, geen AI-buzzwords, met een heldere intro/kern/CTA-"
        "opbouw en minstens één concreet, praktisch voorbeeld. Drempel: 80+."
    )
    try:
        res = agentctl_service.deploy_agent(agent_id=agent_id, task=task, project=project,
                                            benchmark=benchmark)
    except Exception as exc:
        logger.exception("Deploy van %s mislukte voor %s", agent, project)
        return {"project": project, "pillar": "content", "agent": agent,
                "ok": False, "reason": str(exc)[:160]}
    run_id = res.get("run_id")
    if not run_id:
        return {"project": project, "pillar": "content", "agent": agent,
                "ok": False, "reason": "geen run_id van Gauntlet"}

    deploy_id = _record_deploy(run_id=run_id, project=project, pillar="content",
                               agent=agent, task=task, status="running")
    asyncio.create_task(_poll_content_run(deploy_id, run_id, project))
    return {"project": project, "pillar": "content", "agent": agent,
            "ok": True, "run_id": run_id, "status": "running"}


async def _poll_content_run(deploy_id: int, run_id: str, project: str) -> None:
    """Achtergrondtaak: wacht de Gauntlet-run af en sluit de deploy-rij +
    outcome-kaart af. Publiceren gebeurt al automatisch door
    gauntlet/service.py:_auto_queue_run zodra de run de drempel haalt (exact
    daarvoor gebouwd) — deze poller leest `published_job_id` terug i.p.v. zelf
    nogmaals te publiceren, want dat zou hetzelfde artikel dubbel in de
    Wachtrij zetten. Alleen als auto-queue zelf stil faalde (published_job_id
    leeg ondanks passed/partial) volgt één handmatige herstelpoging."""
    from ..gauntlet import service as gauntlet_service
    from ...shared.outcomes import log_outcome

    started = time.monotonic()
    run: Optional[Dict[str, Any]] = None
    final_status = "running"
    while time.monotonic() - started < _CONTENT_MAX_WAIT_S:
        await asyncio.sleep(10)
        try:
            run = gauntlet_service.get_run(run_id)
            final_status = run.get("status") if run else "stopped"
            if final_status != "running":
                break
        except Exception:
            logger.exception("Poll van agentctl-content-run %s mislukt", run_id)

    if final_status == "running":
        _resolve_deploy(deploy_id, status="error")
        log_outcome(project, "agentctl_content",
                    f"Gauntlet-run {run_id} liep langer dan {_CONTENT_MAX_WAIT_S}s en is losgelaten.",
                    next_step="Bekijk de run in de Gauntlet-tab; stop 'm handmatig als hij vastzit.",
                    status="error")
        return

    if final_status in ("passed", "partial"):
        job_id = (run or {}).get("published_job_id") or ""
        if not job_id:
            try:
                pub = gauntlet_service.publish_run_to_wachtrij(run_id, site_name=project)
                job_id = pub.get("job_id") or ""
            except Exception as exc:
                logger.exception("Herstelpoging publish mislukt voor run %s", run_id)
                _resolve_deploy(deploy_id, status="error")
                log_outcome(project, "agentctl_content",
                            f"Gauntlet-run {run_id} haalde de kwaliteitsgrens, maar publiceren naar "
                            f"de Wachtrij mislukte: {exc}",
                            next_step="Bekijk de Gauntlet-run en probeer opnieuw.", status="error")
                return
        artifact = f"/api/content-queue/{job_id}" if job_id else ""
        _resolve_deploy(deploy_id, status="staged", artifact=artifact)
        log_outcome(project, "agentctl_content",
                    f"Nieuw artikel geschreven en klaargezet in de Wachtrij (Gauntlet-run {run_id}).",
                    artifact=artifact, next_step="Beoordeel het nieuwe artikel in de Wachtrij.",
                    status="ok")
        return

    # failed / stopped / stopped_by_user / failed_billing: geen effect, geen
    # foutkaart — een niet-geslaagde Gauntlet-poging is de bedoelde uitkomst
    # van de kwaliteitsgate, geen storing.
    _resolve_deploy(deploy_id, status="no_effect")
    log_outcome(project, "agentctl_content",
                f"Gauntlet-run {run_id} haalde de kwaliteitsgrens niet (status {final_status}).",
                next_step="Bekijk de run in de Gauntlet-tab.", status="ok")


def _execute_uitvoering(project: str, agent: str, task: str) -> Dict[str, Any]:
    """Uitvoering-pijler: geen Gauntlet — dit dupliceert `goal_autoheal`
    (elke 15 min, systeembreed). Een Gauntlet-run kan een vastgelopen doel niet
    hervatten, alleen er proza over schrijven. Roept dezelfde functie direct
    aan en rapporteert wat er voor dít project gebeurde."""
    from ..strategist import service as strategist_service
    from ...shared.outcomes import log_outcome
    from ...shared.projects import squash_project

    deploy_id = _record_deploy(project=project, pillar="uitvoering", agent=agent, task=task, status="running")

    report = strategist_service.autoheal_goals()
    doel = squash_project(project)
    resumed = [r for r in report.get("resumed", []) if squash_project(r.get("project") or "") == doel]
    deleted = [r for r in report.get("deleted", []) if squash_project(r.get("project") or "") == doel]

    if resumed or deleted:
        parts = []
        if resumed:
            parts.append(f"{len(resumed)} vastgelopen doel(en) hervat")
        if deleted:
            parts.append(f"{len(deleted)} kapotte/duplicaat-conceptdoel(en) opgeruimd")
        detail = "; ".join(parts) + "."
        artifact = f"/#project={project}"
        _resolve_deploy(deploy_id, status="staged", artifact=artifact)
        log_outcome(project, "agentctl_uitvoering", detail, artifact=artifact,
                    next_step="Bekijk de doelen in de Doelen-tab.", status="ok")
        return {"project": project, "pillar": "uitvoering", "agent": agent, "ok": True,
                "detail": detail, "artifact": artifact}

    _resolve_deploy(deploy_id, status="no_effect")
    log_outcome(project, "agentctl_uitvoering",
                "Autoheal gedraaid: niets vastgelopen om te hervatten.", status="ok")
    return {"project": project, "pillar": "uitvoering", "agent": agent, "ok": True,
            "detail": "niets te hervatten"}


async def _execute_hygiene(project: str, agent: str, task: str) -> Dict[str, Any]:
    """Hygiëne-pijler: geen losse Gauntlet-spawn — dit ís het 'Verwerk er één'-
    mechanisme (orchestrator.process_one_under_threshold), nu per project
    aangeroepen i.p.v. via de knop. Orchestrator logt zijn eigen outcome-kaart;
    hier alleen de agentctl_deploys-rij afsluiten met hetzelfde oordeel."""
    from ..orchestrator import service as orchestrator_service

    deploy_id = _record_deploy(project=project, pillar="hygiene", agent=agent, task=task, status="running")

    result = await orchestrator_service.process_one_under_threshold(project=project)
    if result.get("processed"):
        job_id = result.get("published_job_id")
        artifact = f"/api/content-queue/{job_id}" if job_id else ""
        _resolve_deploy(deploy_id, status="staged", artifact=artifact)
        return {"project": project, "pillar": "hygiene", "agent": agent, "ok": True,
                "detail": "vastgelopen stuk herschreven, in de Wachtrij", "artifact": artifact}

    _resolve_deploy(deploy_id, status="no_effect")
    return {"project": project, "pillar": "hygiene", "agent": agent, "ok": True,
            "detail": result.get("reason", "niets te herstellen")}


async def _execute_one(s: Dict[str, Any]) -> Dict[str, Any]:
    project, pillar = s["project"], s["pillar_key"]
    agent, task = s.get("agent", ""), s.get("task", "")

    if _today_has_deploy(project, pillar):
        return {"project": project, "pillar": pillar, "agent": agent,
                "ok": False, "reason": "al bezig of vandaag al gedaan"}

    try:
        if pillar == "seo":
            return await _execute_seo(project, agent, task)
        if pillar == "content":
            return _execute_content(project, agent, task, s.get("agent_id"))
        if pillar == "uitvoering":
            return _execute_uitvoering(project, agent, task)
        if pillar == "hygiene":
            return await _execute_hygiene(project, agent, task)
    except Exception as exc:
        logger.exception("Suggestie voor %s (%s) mislukt", project, pillar)
        return {"project": project, "pillar": pillar, "agent": agent,
                "ok": False, "reason": str(exc)[:160]}
    return {"project": project, "pillar": pillar, "agent": agent,
            "ok": False, "reason": "onbekende pijler"}


async def execute_all(limit: Optional[int] = None) -> Dict[str, Any]:
    """Voer elke suggestie uit — elke pijler via zijn eigen, echte mechanisme
    (zie _execute_seo/_execute_content/_execute_uitvoering/_execute_hygiene).

    limit: cap op het aantal uit te voeren suggesties (bijv. de top-N per dag,
    zodat de stal niet in één keer overbelast raakt). None = alles.
    Stop niet bij de eerste fout: elke suggestie krijgt zijn eigen run; een
    misfire loggen we en gaan door met de rest.
    """
    data = suggest()
    suggestions = data.get("suggestions", [])
    if limit is not None:
        suggestions = suggestions[:limit]
    results = [await _execute_one(s) for s in suggestions]
    ok_n = sum(1 for r in results if r.get("ok"))
    return {"executed": len(results), "succeeded": ok_n, "results": results}


async def auto_deploy_daily(max_deploys: int = 5, max_busy: int = 6) -> Dict[str, Any]:
    """Dagelijkse autonome inzet (scheduler-job).

    Voert de top-`max_deploys` suggesties uit, maar ALLEEN als de stal niet al
    zwaar bezet is (< `max_busy` agents bezig). Zo voorkomen we dat Iris de
    hele stal in één ochtend bombardreert terwijl er nog runs van gisteren
    lopen. Geeft een leesbaar rapport terug voor scheduler_runs.

    Async zodat de Gauntlet-pijplijn (asyncio.create_task in spawn_gauntlet)
    een levende loop vindt wanneer de scheduler dit via asyncio.run() aanroept.
    """
    occ = agentctl_service.list_agents().get("summary", {})
    busy = occ.get("busy_count", 0)
    if busy >= max_busy:
        msg = f"Overgeslagen: {busy} agents al bezig (drempel {max_busy})."
        logger.info("Auto-deploy: %s", msg)
        return {"skipped": True, "reason": msg, "busy": busy}
    res = await execute_all(limit=max_deploys)
    logger.info("Auto-deploy: %d/%d runs gestart (was %d bezig).",
                res.get("succeeded", 0), res.get("executed", 0), busy)
    return {"skipped": False, **res, "busy_before": busy}

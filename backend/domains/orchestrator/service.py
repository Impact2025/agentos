"""Iris Orchestrator — zware escalatie voor content die de goedkope route niet redde.

Er bestaat al een autonome, elke-30-min verbeteraar (`publish.content_pipeline.
run_content_improver_job`) die 'needs_work'/'pending_review'-stukken onder de
grens oppakt met één goedkope LLM-verbeterronde. Die zet een stuk dat na
CONTENT_IMPROVER_MAX_ATTEMPTS nog steeds faalt bewust op 'stuck' en stopt —
"escaleert naar mens (geen verdere LLM-runs)" staat letterlijk in die code.

De Orchestrator vult precies dát gat: 'stuck' (de goedkope route gaf het op) en
'rejected' (een mens keurde het af) stukken, via de zwaardere Gauntlet Loop
(meerdere parallelle builders + blinde critici tegen de project-benchmark).
Hij raakt daarom NOOIT 'needs_work'/'pending_review' aan — dat zou dubbel werk
zijn op dezelfde rij (twee systemen die onafhankelijk aan hetzelfde artikel
sleutelen, dubbel LLM-budget) en zou de expliciete "geen verdere LLM-runs"-
belofte aan een 'stuck'-stuk stilzwijgend doorbreken zodra het weer aanstaat.

Daarom draait dit bewust NIET als scheduler-job: automatisch opnieuw LLM-runs
starten op content die het systeem al aan een mens overdroeg, is precies de
belofte die `content_pipeline` net brak. Vincent triggert 'm handmatig vanaf
het dashboard (Agenten-tab), per stuk, zoveel of weinig als hij wil.

Veiligheidsmodel (verantwoorde autonomie):
- Alleen 'stuck' en 'rejected' stukken onder de grens — nooit de rijen die
  content_improver al bewerkt.
- Nooit automatisch gepubliceerd — altijd 'pending_review' (wacht op
  menselijke goedkeuring).
- Eén klik = één stuk (geen storm-loop).
- Een stuk dat na de Gauntlet nog onder de grens zit, wordt NIET opnieuw in de
  queue gezet (anders oneindige loop) — het blijft 'rejected' met een notitie.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agentos.orchestrator")

# Lokale API-base (Orchestrator draait in dezelfde server, roept de eigen HTTP
# endpoints aan zodat elke LLM-call in de server-event-loop zit).
_API_BASE = "http://127.0.0.1:1250"
_DEFAULT_THRESHOLD = 80


# ── Project-benchmarks (uit de vault, als fallback hardcoded voor de kern-projecten) ──
# Elke entry: korte stijl-brief die de Gauntlet-blind-criticus als lat gebruikt.
_PROJECT_BENCHMARKS: Dict[str, str] = {
    "WeAreImpact": (
        "BENCHMARK = echte WeAreImpact-stijl (vault 10_Projects/WeAreImpact): Vincent van Munster, "
        "AI-consultant sociaal domein, 15+ jaar uit de welzijnssector. Toon: menselijk, geen rapporten "
        "maar naast je staan, 'Koffie met Vincent' als CTA, Iris/Bijeen/Brickme als echte cases. "
        "Eerste persoon, Nederlands, geen em-dashes, geen verzonnen cijfers. Drempel: 85+."
    ),
    "Bijeen": (
        "BENCHMARK = echte Bijeen-stijl (vault 10_Projects/Bijeen): evenementen voor welzijn en "
        "sociaal domein, praktisch, geen dikke evaluatie-formulieren, direct uitvoerbaar. Nederlands, "
        "eerste persoon waar passend, geen AI-buzzwords. Drempel: 80+."
    ),
    "Pootgelukkig": (
        "BENCHMARK = echte Pootgelukkig-stijl: hondenuitlaat/baan, persoonlijk, warm, SEO-E-E-A-T. "
        "Nederlands, geen verzonnen cijfers. Drempel: 80+."
    ),
    "BewaardVoorJou": (
        "BENCHMARK = echte BewaardVoorJou-stijl: erfstukken/verhalen bewaren, empathisch, NL. "
        "Drempel: 80+."
    ),
}


def _project_for_job(job: Dict[str, Any]) -> str:
    """Bepaal het project van een content_job via site_id → site-naam."""
    site_id = job.get("site_id") or ""
    try:
        from ...domains.seo import sites as sites_service
        site = sites_service.get_site(site_id) or {} if hasattr(sites_service, "get_site") else {}
        name = site.get("name", "")
        if name:
            return name
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon project niet bepalen voor job %s: %s", job.get("id"), exc)
    # fallback: site_id zélf (vaak de project-naam)
    return str(site_id)


def _benchmark_for_project(project: str) -> str:
    """Geef de hardcoded benchmark voor een project, of een generieke fallback."""
    for key, bench in _PROJECT_BENCHMARKS.items():
        if key.lower() in project.lower():
            return bench
    return (
        f"BENCHMARK = schrijf in de stijl van project '{project}' (zie vault 10_Projects/{project}). "
        "Nederlands, eerste persoon, geen verzonnen cijfers, leesbaar, SEO-vriendelijk. Drempel: 80+."
    )


async def _api_post(path: str, payload: Dict[str, Any], cookie: Optional[str] = None) -> Dict[str, Any]:
    """POST naar de lokale API (async, binnen de server-event-loop)."""
    import httpx
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{_API_BASE}{path}", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


async def _api_get(path: str, cookie: Optional[str] = None) -> Dict[str, Any]:
    import httpx
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{_API_BASE}{path}", headers=headers)
        r.raise_for_status()
        return r.json()


def _find_under_threshold_jobs(threshold: int = _DEFAULT_THRESHOLD) -> List[Dict[str, Any]]:
    """Haal content_jobs op die de goedkope verbeteraar niet redde.

    Bewust ALLEEN 'stuck' (content_improver gaf het na max pogingen op) en
    'rejected' (een mens keurde het af) — 'needs_work'/'pending_review' zijn
    het jachtgebied van de 30-min content_improver-job en horen hier niet in,
    anders bewerken twee systemen onafhankelijk dezelfde rij.
    """
    from ..publish import content_pipeline
    candidate_statuses = ("stuck", "rejected")
    found: List[Dict[str, Any]] = []
    try:
        for status in candidate_statuses:
            jobs = content_pipeline.list_jobs(status=status)
            for j in jobs:
                score = j.get("seo_score") or 0
                if isinstance(score, str):
                    try:
                        score = float(score)
                    except ValueError:
                        score = 0
                if score < threshold:
                    found.append(j)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon content_queue niet uitlezen: %s", exc)
    return found


async def process_one_under_threshold(
    threshold: int = _DEFAULT_THRESHOLD,
    max_wait_s: int = 600,
) -> Dict[str, Any]:
    """Verwerk ÉÉN stuk onder de grens via de Gauntlet Loop.

    Returns een status-dict met wat er gebeurde.
    """
    jobs = _find_under_threshold_jobs(threshold)
    if not jobs:
        return {"processed": False, "reason": "geen stukken onder de grens"}

    job = jobs[0]
    job_id = job.get("id")
    project = _project_for_job(job)
    benchmark = _benchmark_for_project(project)
    title = job.get("title") or f"{project} artikel"
    keyword = job.get("keyword") or ""
    objective = (
        f"Herschrijf het artikel '{title}' (project {project}) naar een wereldklasse versie "
        f"die de kwaliteitsgrens van {threshold}+ haalt. Behoud de kernboodschap en de project-stijl. "
        f"Max 1200 woorden, Nederlands, geen verzonnen cijfers."
    )

    logger.info("Orchestrator: start Gauntlet voor job %s (%s, score %s)", job_id, project, job.get("seo_score"))

    # 1. Start Gauntlet — DIRECT in-process (geen HTTP, dus geen auth-401).
    #    spawn_gauntlet() roept asyncio.create_task in de lopende server-event-loop.
    try:
        from ..gauntlet import service as gauntlet_service
        spawn = gauntlet_service.spawn_gauntlet(
            objective=objective, benchmark=benchmark, threshold=threshold,
            max_iterations=3,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gauntlet-start mislukt")
        _log(project, "error", f"Gauntlet-start mislukt voor '{title}': {exc}",
             next_step="Controleer de Gauntlet-service en probeer opnieuw vanaf de Agenten-tab.")
        return {"processed": False, "reason": f"Gauntlet-start mislukt: {exc}"}
    run_id = spawn.get("run_id")
    if not run_id:
        _log(project, "error", f"Gauntlet gaf geen run_id terug voor '{title}'.")
        return {"processed": False, "reason": "Geen run_id van Gauntlet"}

    # 2. Wacht tot de run klaar is (poll de DB-status direct).
    started = time.monotonic()
    final_status = "running"
    while time.monotonic() - started < max_wait_s:
        await asyncio.sleep(10)
        try:
            run = gauntlet_service.get_run(run_id)
            final_status = run.get("status") if run else "stopped"
            if final_status != "running":
                break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Poll Gauntlet %s mislukt: %s", run_id, exc)

    if final_status == "running":
        _log(project, "error",
             f"Gauntlet-run voor '{title}' liep langer dan {max_wait_s}s en is losgelaten "
             f"(run {run_id} draait mogelijk nog door).",
             next_step="Bekijk de run in de Gauntlet-tab; stop 'm handmatig als hij vastzit.")
        return {"processed": False, "job_id": job_id, "run_id": run_id,
                "reason": "Gauntlet-run duurde te lang (> %ds)" % max_wait_s}

    # 3. Alleen publiceren als de run de grens haalde (passed/partial).
    if final_status in ("passed", "partial"):
        try:
            pub = gauntlet_service.publish_to_weareimpact(
                run_id, site_name=project, title=title, keyword=keyword,
                slug=_slugify(title),
            )
            published_job_id = pub.get("job_id")
            _log(project, "ok",
                 f"'{title}' via de Gauntlet Loop herschreven (was '{job.get('status')}', "
                 f"score {job.get('seo_score')}) en teruggezet in de Wachtrij.",
                 artifact=f"/api/content-queue/{published_job_id}" if published_job_id else "",
                 next_step="Beoordeel het herschreven stuk in de Wachtrij.")
            return {
                "processed": True,
                "job_id": job_id,
                "run_id": run_id,
                "run_status": final_status,
                "published_job_id": published_job_id,
                "new_status": "pending_review",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Publish mislukt")
            _log(project, "error", f"Gauntlet haalde de grens voor '{title}', maar publiceren "
                 f"naar de Wachtrij mislukte: {exc}",
                 next_step="Controleer content_pipeline en probeer opnieuw.")
            return {"processed": False, "job_id": job_id, "run_id": run_id,
                    "reason": f"Publish mislukt: {exc}"}

    # Run haalde de grens niet → niet opnieuw in de queue (geen loop).
    _log(project, "ok",
         f"Gauntlet-herschrijving van '{title}' haalde de grens van {threshold} niet "
         f"(uitkomst: {final_status}) — blijft 'rejected', geen nieuwe poging.",
         next_step="Herschrijf handmatig, of pas de project-benchmark aan als de lat onhaalbaar is.")
    return {"processed": False, "job_id": job_id, "run_id": run_id,
            "run_status": final_status, "reason": "Gauntlet haalde grens niet"}


def _log(project: str, status: str, detail: str, *, artifact: str = "", next_step: str = "") -> None:
    from ...shared.outcomes import log_outcome
    try:
        log_outcome(project, "orchestrator_gauntlet", detail,
                    artifact=artifact, next_step=next_step, status=status)
    except Exception:  # noqa: BLE001
        logger.exception("Kon Orchestrator-uitkomst niet loggen")


def _slugify(text: str) -> str:
    import re
    t = re.sub(r"[^a-z0-9\s-]", "", text.lower()).strip()
    t = re.sub(r"\s+", "-", t)
    return t[:80] or "gauntlet-run"

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

Incident 14 aug 2026: dat laatste punt dekte alleen het faalpad. Bij een
GESLAAGDE Gauntlet-run publiceert `publish_run_to_wachtrij` een gloednieuw
content_job — maar het bronrecord (dat 'm liet vinden) bleef gewoon 'rejected'
staan. Elke volgende aanroep (Agent Control's hygiëne-pijler, of Vincent die
nogmaals klikt) vond dus hetzélfde bronrecord opnieuw en herschreef het
opnieuw: één Bijeen- en één WeAreImpact-artikel werden zo op één dag 20+ keer
door de zware 3-criticus Gauntlet Loop gehaald — genoeg om de hele dagbudget
(5M tokens) leeg te trekken zonder dat er ooit iets écht "klaar" was. Fix:
`mark_superseded` sluit het bronrecord af zodra de herschrijving slaagt (het
telt dan niet meer als 'rejected'/'stuck'), en `ORCHESTRATOR_MAX_ATTEMPTS`
(cross-run teller, net als content_pipeline's CONTENT_IMPROVER_MAX_ATTEMPTS)
stopt de heroppak-lus ook als het artikel telkens onder de grens blijft
hangen — beide paden krijgen nu dezelfde discipline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("impactos.orchestrator")

# Eén lopende herschrijving per artikel (15 aug 2026).
#
# `process_one_under_threshold` heet "process ONE", maar niets belette twee
# gelijktijdige aanroepen dezelfde job op te pakken: de selectie leest de
# database, de cap-teller wordt opgehoogd en dán start de zware run — tussen
# lezen en ophogen past een tweede aanroep. Gemeten: vijf bronrecords van
# hetzelfde artikel wezen naar dezelfde opvolger, en twee runs startten 0,3
# seconde na elkaar. Elke gelijktijdige run is een volle 3-criticus-ronde aan
# LLM-budget voor werk dat één keer hoort te gebeuren.
#
# Bewust in het procesgeheugen en niet in de database: dit bewaakt "draait er
# nú een run in dít proces", en dat is precies wat een herstart ongeldig maakt.
# De duurzame rem tegen herhaling over herstarts heen is `orchestrator_attempts`
# (zie content_pipeline.mark_superseded, dat hem meegeeft aan de opvolger).
_LOPEND: set[str] = set()

# Lokale API-base (Orchestrator draait in dezelfde server, roept de eigen HTTP
# endpoints aan zodat elke LLM-call in de server-event-loop zit).
_API_BASE = "http://127.0.0.1:1250"
_DEFAULT_THRESHOLD = 80


# ── Project-benchmarks (uit de vault, als fallback hardcoded voor de kern-projecten) ──
# Elke entry: korte stijl-brief die de Gauntlet-blind-criticus als lat gebruikt.
_PROJECT_BENCHMARKS: Dict[str, str] = {
    "WeAreImpact": (
        "BENCHMARK voor project 'WeAreImpact' = echte WeAreImpact-stijl (vault 10_Projects/WeAreImpact): "
        "Vincent van Munster, AI-consultant sociaal domein, 15+ jaar uit de welzijnssector. Toon: menselijk, "
        "geen rapporten maar naast je staan, 'Koffie met Vincent' als CTA, Iris/Bijeen/Brickme als echte "
        "cases. Eerste persoon, Nederlands, geen em-dashes, geen verzonnen cijfers. Drempel: 85+."
    ),
    "Bijeen": (
        "BENCHMARK voor project 'Bijeen' = echte Bijeen-stijl (vault 10_Projects/Bijeen): evenementen voor "
        "welzijn en sociaal domein, praktisch, geen dikke evaluatie-formulieren, direct uitvoerbaar. "
        "Nederlands, eerste persoon waar passend, geen AI-buzzwords. Drempel: 80+."
    ),
    "Pootgelukkig": (
        "BENCHMARK voor project 'Pootgelukkig' = echte Pootgelukkig-stijl: hondenuitlaat/baan, persoonlijk, "
        "warm, SEO-E-E-A-T. Nederlands, geen verzonnen cijfers. Drempel: 80+."
    ),
    "BewaardVoorJou": (
        "BENCHMARK voor project 'BewaardVoorJou' = echte BewaardVoorJou-stijl (vault 10_Projects/Bewaardvoorjou): "
        "iemands levensverhaal of erfstukken veiligstellen voor de volgende generatie, "
        "met een warme, empathische AI-interviewer die géén schrijfervaring vereist. "
        "Toon: teder, persoonlijk, geen jargon, Nederlands. E-E-A-T: put uit echte "
        "herinneringen en familiebinding, geen verzonnen cijfers. Drempel: 85+."
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
    """Geef de hardcoded benchmark voor een project, of een generieke fallback.

    Exacte naam-match, geen substring (15 aug 2026). `if key.lower() in
    project.lower()` liet elke sitenaam die een andere bevat de verkeerde
    stijlgids pakken — 'Impact' zit in zowel WeAreImpact als
    TeambuildingMetImpact, en de iteratievolgorde van een dict besliste wie
    won. Gemeten diezelfde dag: een artikel over GPS-teambuilding bij Cpunt
    werd herschreven tegen de Bijeen-benchmark.

    Dat is niet alleen een verkeerde toon: het project komt uit het site_id
    (zie `_project_for_job`), dus een stuk dat op de verkeerde site belandde
    kreeg vervolgens ook de huisstijl van die verkeerde site en landde daarna
    opnieuw op diezelfde plek. De fout bevestigde zichzelf elke ronde.
    """
    doel = "".join(c for c in project.lower() if c.isalnum())
    for key, bench in _PROJECT_BENCHMARKS.items():
        if "".join(c for c in key.lower() if c.isalnum()) == doel:
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


def _find_under_threshold_jobs(
    threshold: int = _DEFAULT_THRESHOLD, project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Haal content_jobs op die de goedkope verbeteraar niet redde.

    Neemt 'stuck' (content_improver gaf het na max pogingen op), 'rejected'
    (een mens keurde het af) EN 'needs_work' mee. Wereldklasse-motief: de
    5-fasen write-and-publish pipeline laat artikelen die de grens niet halen op
    'needs_work' staan, en de 30-min content_improver pakt die niet (of geeft ze
    niet door) — zonder deze regel hangen ze voor altijd onder de grens. Zie
    impactos-skill pitfall "Orchestrator negeert needs_work" (aug 2026).

    Storm-bescherming zit bewust NIET in dit filter maar in de caller
    (`process_one_under_threshold`): de cross-run cap (ORCHESTRATOR_MAX_ATTEMPTS),
    de `_LOPEND`-guard (geen twee gelijktijdige runs) en `mark_superseded`
    (bronrecord wordt afgesloten zodra de herschrijving in de Wachtrij staat).
    Daardoor kan de content_improver een needs_work-rij nooit meer dubbel
    bewerken nadat de Gauntlet er een opvolger voor maakte.

    `project`: optioneel filter (squash-vergeleken, zelfde regel als elders —
    zie shared/projects.py) zodat een per-project trigger (Agent Control) niet
    per ongeluk het stuk van een ánder project oppakt.
    """
    from ..publish import content_pipeline
    from ...shared.projects import squash_project
    candidate_statuses = ("stuck", "rejected", "needs_work")
    doel = squash_project(project) if project else None
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
                if score >= threshold:
                    continue
                if doel is not None and squash_project(_project_for_job(j)) != doel:
                    continue
                found.append(j)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon content_queue niet uitlezen: %s", exc)
    return found


async def process_one_under_threshold(
    threshold: int = _DEFAULT_THRESHOLD,
    max_wait_s: int = 600,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """Verwerk ÉÉN stuk onder de grens via de Gauntlet Loop.

    Returns een status-dict met wat er gebeurde.
    """
    from ...shared.config import ORCHESTRATOR_MAX_ATTEMPTS

    jobs = _find_under_threshold_jobs(threshold, project=project)
    if not jobs:
        return {"processed": False, "reason": "geen stukken onder de grens"}

    # Cross-run cap: sla stukken over die al ORCHESTRATOR_MAX_ATTEMPTS keer door
    # de zware Gauntlet Loop zijn gehaald zonder ooit gesloten te worden (nooit
    # de grens gehaald — mark_superseded had anders al toegeslagen). Zonder
    # deze filter vindt elke aanroep hetzelfde vastgelopen stuk terug en
    # verbrandt hij er weer een volle 3-criticus-ronde aan (incident 14 aug
    # 2026: 20+ herschrijvingen van hetzelfde artikel op één dag).
    processable = [j for j in jobs
                   if int(j.get("orchestrator_attempts") or 0) < ORCHESTRATOR_MAX_ATTEMPTS]
    if not processable:
        return {"processed": False,
                "reason": f"alle {len(jobs)} onder-de-grens-stuk(ken) zijn al "
                          f"{ORCHESTRATOR_MAX_ATTEMPTS}x geprobeerd — vastgelopen, wacht op "
                          "een mens (zie de content-stuck-kaart)."}

    # Sla stukken over waar in dit proces al een run voor loopt (zie _LOPEND).
    vrij = [j for j in processable if j.get("id") not in _LOPEND]
    if not vrij:
        return {"processed": False,
                "reason": f"voor alle {len(processable)} kandidaat-stuk(ken) loopt al een "
                          "Gauntlet-run — een tweede tegelijk is dezelfde ronde dubbel betaald."}

    job = vrij[0]
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

    # Slot vasthouden zolang déze run loopt (zie _LOPEND). try/finally, want
    # een fout of een time-out moet het stuk weer vrijgeven — anders is één
    # mislukte run genoeg om dit artikel tot de volgende herstart te blokkeren.
    _LOPEND.add(job_id)
    try:
        # Poging tellen VÓÓR de zware run start — ook een timeout of een run die de
        # grens niet haalt heeft al het volle LLM-budget gekost en moet meetellen,
        # anders reset de teller stil bij elke misser.
        from ..publish import content_pipeline
        attempts_used = content_pipeline.bump_orchestrator_attempts(job_id)

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
                pub = gauntlet_service.publish_run_to_wachtrij(
                    run_id, site_name=project, title=title, keyword=keyword,
                    slug=_slugify(title),
                )
                published_job_id = pub.get("job_id")
                # Sluit het bronrecord af — anders vindt de volgende aanroep hem
                # weer terug ('rejected'/'stuck' blijft anders voor altijd geldig)
                # en herschrijft hij hetzelfde artikel nogmaals.
                if published_job_id:
                    content_pipeline.mark_superseded(job_id, published_job_id)
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

        # Run haalde de grens niet. Bij de laatste toegestane poging: één keer
        # luid escaleren (status='error', dedupe via de kaart zelf) en het stuk
        # telt vanaf nu als vastgelopen (ORCHESTRATOR_MAX_ATTEMPTS-check hierboven
        # sluit het uit van toekomstige pogingen — geen storm meer).
        if attempts_used >= ORCHESTRATOR_MAX_ATTEMPTS:
            _log(project, "error",
                 f"'{title}' haalt de kwaliteitsgrens van {threshold} niet, ook niet na "
                 f"{attempts_used} zware Gauntlet-pogingen (laatste uitkomst: {final_status}) "
                 "— vastgelopen, geen verdere automatische pogingen meer.",
                 next_step="Herschrijf handmatig, of verlaag CONTENT_MIN_SCORE/de project-benchmark "
                           "als de lat structureel onhaalbaar blijkt.")
        else:
            _log(project, "ok",
                 f"Gauntlet-herschrijving van '{title}' haalde de grens van {threshold} niet "
                 f"(uitkomst: {final_status}, poging {attempts_used}/{ORCHESTRATOR_MAX_ATTEMPTS}) "
                 "— blijft 'rejected'.",
                 next_step="Herschrijf handmatig, of wacht op de volgende Gauntlet-poging.")
        return {"processed": False, "job_id": job_id, "run_id": run_id,
                "run_status": final_status, "reason": "Gauntlet haalde grens niet",
                "attempts_used": attempts_used}
    finally:
        _LOPEND.discard(job_id)


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

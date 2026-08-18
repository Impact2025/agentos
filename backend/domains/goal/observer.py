"""
Dashboard Observer — proactieve zelfsturende goals.

Dit is de ontbrekende laag in Agent OS: een agent (de "observer") die het
dashboard / de Control Room *uitleest*, zelf de conclusie trekt
"project X heeft geen lopend doel terwijl er wel werk ligt", en dan
*eigenhandig* een doel aanmaakt — achter de menselijke publiceer-gate.

Waarom dit bestond niet:
- Voorheen kwamen doelen alleen uit (a) een handmatige "Strategist analyse"-
  knop, of (b) de maandlijkse content-goal-cron. Die cron riep
  `create_and_plan()` aan, dat de LLM-proxy (:8899) nodig heeft om een plan te
  *decomposeren*. Valt die proxy uit (HTTP 000), dan sterft de cron stil en
  verschijnt er géén doel — en niemand kijkt nog eens. Dat is precies wat op
  1 aug 2026 (en nu, 13 aug) gebeurde: 0 actieve doelen, terwijl er werk lag.
- Er was géén code-pad dat de Control Room uitleest en een afwijking ("0
  actieve doelen voor WeAreImpact") vertaalde naar actie.

Deze observer doorbreekt die afhankelijkheid:
- Het *aanmaken* van een doel is hier LLM-VRIJ. We schrijven zélf een geldig
  plan.json (deterministisch, zelfde vorm als `confirm_plan` verwacht) en
  bevestigen het. Geen proxy nodig → nooit een stille dode run.
- Het *uitvoeren* van een doel (content schrijven/publiceren) blijft wél de
  LLM-achtergrond-loop, en die retry't vanzelf zodra de proxy terug is.
- Doelen landen op `ready` — achter de Wachtrij/publiceer-gate. Er wordt
  niets geautomatiseerd gepubliceerd. De mens blijft de poortwachter.

Idempotent: per project/periode wordt hoogstens één actief doel geseed; een
reeds lopend of recent doel blokkeert een nieuwe seed.

Gebruik:
    from .observer import observe_dashboard, seed_project_goal
    report = observe_dashboard()          # volledige scan + seeds
    r = seed_project_goal("WeAreImpact")  # één project geforceerd (handmatig API)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.projects import squash_project
from . import service as goal_service

logger = logging.getLogger(__name__)

# ── Configuratie ────────────────────────────────────────────────────────
# Een project krijgt een nieuw doel als:
#   - er GEEN actief doel is (running/ready), én
#   - het project GSC/SEO-geconfigureerd is (dus er ligt data om op te acteren), én
#   - het laatste doel ouder is dan MIN_GOAL_AGE_DAYS dagen (default 21).
# Zo voorkomen we dat we elke 30 min een doel spammen zodra één doel klaar is.
MIN_GOAL_AGE_DAYS = 21

# Welke projecten mogen automatisch een doel krijgen? Alles wat GSC heeft
# (zie `_project_has_work`) is in principe eligible; deze set is een
# expliciete veiligheidsklep zodat we nooit per ongeluk een klant/side-project
# voldoelen. Leeg = alleen de kern-contentprojecten hieronder.
ELIGIBLE_PROJECTS: set[str] = {
    "WeAreImpact",
    "Bewaard voor Jou",
    "Bewaardvoorjou",
    "Ictusgo",
    "Bijeen",
    "Pootgelukkig",
    "Steentjebij Steentje",
    "TeambuildingMetImpact",
    "Vrijwilligersmatch",
    "Liefde voor Iedereen",
    "DatingAssistent",
    "Skillkaart",
}

# Objectieven per project. Deze zijn dezelfde als de maandlijkse content-goals:
# "per maand N stuks omzetten in gepubliceerde content via de auto-AEO
# conveyor, achter de menselijke review-gate". Ze zijn bewust concreet zodat
# `confirm_plan` er een zinnig deterministisch plan uit destilleert.
PROJECT_OBJECTIVES: Dict[str, str] = {
    "WeAreImpact": (
        "Per maand 4 goedgekeurde Mission Radar-signalen omzetten in gepubliceerde "
        "AEO-listicles op weareimpact.nl via de auto-AEO conveyor (listicle, video, "
        "reddit). Focus: AI voor zorg/welzijn/gemeenten, sociaal domein, LEGO "
        "Serious Play, change management. Human-in-the-loop: nooit auto-publiceren, "
        "altijd menselijke review-gate."
    ),
    "Bewaard voor Jou": (
        "Per maand 4 goedgekeurde kansen uit de Demand Engine (Search Console + "
        "Mission Radar) omzetten in gepubliceerde content op bewaardvoorjou.nl — "
        "vóór een nieuwe kans wordt geschreven, controleren of het zoekwoord al "
        "wordt gedekt door een bestaande live pagina (cluster-/zoekwoordkannibalisatie "
        "is hier al twee keer geconstateerd) en géén tweede artikel op hetzelfde "
        "zoekwoord starten. Focus: levensverhalen vastleggen, digitale "
        "nalatenschap, herinneringen voor kinderen/kleinkinderen, 65+. "
        "Human-in-the-loop: nooit auto-publiceren, altijd menselijke review-gate."
    ),
    "Ictusgo": (
        "Per maand 4 goedgekeurde Mission Radar-signalen omzetten in gepubliceerde "
        "AEO-listicles op ictusgo.nl via de auto-AEO conveyor (listicle, video, "
        "reddit). Human-in-the-loop: nooit auto-publiceren, altijd menselijke "
        "review-gate."
    ),
    "Bijeen": (
        "Per maand 4 goedgekeurde kansen uit de Demand Engine omzetten in "
        "gepubliceerde content op bijeen.app (eventplatform). Focus: events, "
        "netwerken, community-building. Human-in-the-loop: nooit auto-publiceren, "
        "altijd menselijke review-gate."
    ),
    "Pootgelukkig": (
        "Per maand 4 goedgekeurde kansen uit de Demand Engine omzetten in "
        "gepubliceerde content op pootgelukkig.nl (honden/welzijn). Focus: "
        "hondenwelzijn, baasje-dier-band, zorghonden. Human-in-the-loop: nooit "
        "auto-publiceren, altijd menselijke review-gate."
    ),
    "Steentjebij Steentje": (
        "Per maand 4 goedgekeurde kansen uit de Demand Engine omzetten in "
        "gepubliceerde content voor Steentje bij Steentje (vrijwilligerswerk). "
        "Human-in-the-loop: nooit auto-publiceren, altijd menselijke review-gate."
    ),
    "TeambuildingMetImpact": (
        "Per maand 4 goedgekeurde kansen omzetten in gepubliceerde content voor "
        "TeambuildingMetImpact. Human-in-the-loop: nooit auto-publiceren, altijd "
        "menselijke review-gate."
    ),
    "Vrijwilligersmatch": (
        "Per maand 4 goedgekeurde kansen omzetten in gepubliceerde content voor "
        "Vrijwilligersmatch. Human-in-the-loop: nooit auto-publiceren, altijd "
        "menselijke review-gate."
    ),
    "Liefde voor Iedereen": (
        "Per maand 4 goedgekeurde kansen omzetten in gepubliceerde content voor "
        "Liefde voor Iedereen. Human-in-the-loop: nooit auto-publiceren, altijd "
        "menselijke review-gate."
    ),
    "DatingAssistent": (
        "Per maand 4 goedgekeurde kansen omzetten in gepubliceerde content voor de "
        "DatingAssistent. Human-in-the-loop: nooit auto-publiceren, altijd "
        "menselijke review-gate."
    ),
    "Skillkaart": (
        "Per maand 4 goedgekeurde kansen omzetten in gepubliceerde content voor "
        "Skillkaart. Human-in-the-loop: nooit auto-publiceren, altijd menselijke "
        "review-gate."
    ),
}

# Fallback-objectief voor eligible projecten zonder specifieke entry.
_DEFAULT_OBJECTIVE = (
    "Per maand 4 goedgekeurde kansen uit de Demand Engine omzetten in "
    "gepubliceerde content via de auto-AEO conveyor. Human-in-the-loop: nooit "
    "auto-publiceren, altijd menselijke review-gate."
)


# ── Hulpfuncties ────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_has_work(project: str) -> bool:
    """Heeft dit project GSC/SEO-data waarop geacteerd kan worden?

    We tellen sites met een gsc_property (de kwaliteitsgate voor content-
    kansen). Zonder GSC is er niets om maandelijks op te baseren, dus seeden
    we dan niet.
    """
    try:
        from ...domains.seo import sites as sites_service
        sites = sites_service.list_sites()
        for s in sites:
            name = (s.get("name") or "").strip()
            prop = (s.get("gsc_property") or "").strip()
            if not prop:
                continue
            if squash_project(name) == squash_project(project):
                return True
    except Exception:
        # bij twijfel: wel seeden (de goals lopen toch achter de gate)
        return True
    return False


def _active_goal_for(project: str) -> Optional[Dict[str, Any]]:
    """Laatste actieve (running/ready) goal voor dit project, of None."""
    goals = goal_service.list_goals(limit=200, project=project)
    for g in goals:
        if g.get("status") in ("running", "ready", "paused"):
            return g
    return None


def _last_goal_age_days(project: str) -> Optional[float]:
    """Leeftijd in dagen van het meest recente doel voor dit project."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) FROM goals WHERE project = ?",
                (project,),
            ).fetchone()
        if not row or not row[0]:
            return None
        created = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - created).total_seconds() / 86400.0
    except Exception:
        return None


def _similar_open_goal(title: str, project: str) -> Optional[str]:
    """Voorkom duplicaat-seeds: bestaat er een open doel met ~dezelfde titel?"""
    existing = goal_service._find_similar_open_goal(title, project)  # type: ignore[attr-defined]
    return existing["id"] if existing else None


# ── Deterministisch plan (LLM-vrij) ─────────────────────────────────────
def _seed_plan(title: str, objective: str, project: str) -> Dict[str, Any]:
    """Bouw een geldig plan.json zónder LLM.

    Hetzelfde contract als `confirm_plan` verwacht:
        {"plan_summary", "estimated_duration", "phases":[{"title","description","tasks":[...]}]}
    Taken krijgen een 'skill' en optioneel 'dependencies' (titel-referenties,
    die `confirm_plan` via `_normalize_dep_refs` oplost). De publisher-taken
    blijven achter de review-gate (stage in Wachtrij, géén auto-publish).
    """
    obj = (objective or "").lower()
    n_articles = 4  # default maandtarget; overriden als het objectief anders zegt
    m = re.search(r"(\d+)\s+(?:goedgekeurde|kansen|signalen|artikel)", obj)
    if m:
        try:
            n_articles = max(1, int(m.group(1)))
        except ValueError:
            pass

    research_tasks = []
    write_tasks = []
    publish_tasks = []

    if any(w in obj for w in ("gsc", "data", "analyse", "demand", "radar", "kans")):
        research_tasks.append({
            "title": f"Analyseer zoekvragen & kansen voor {project}",
            "description": (
                f"Gebruik Search Console-data en de Demand Engine om de {n_articles} "
                f"meest waardevolle, onbedekte zoekvragen voor {project} te bepalen. "
                "Controleer op kannibalisatie met bestaande live pagina's. Lever een "
                "concreet lijstje kansen (geen live analytics-calls nodig)."
            ),
            "skill": "analyst",
            "dependencies": [],
        })

    for i in range(n_articles):
        wt = {
            "title": f"Schrijf artikel {i+1} voor {project}",
            "description": (
                f"Schrijf een publicabel AEO-artikel (inclusief FAQPage-sectie waar "
                f"zinvol) voor {project}, gebaseerd op de research-output. Baseer op "
                "de bestaande kennisbank/merkstem."
            ),
            "skill": "content-writer",
            "dependencies": [t["title"] for t in research_tasks],
        }
        write_tasks.append(wt)
        publish_tasks.append({
            "title": f"Publiceer artikel {i+1} ({project})",
            "description": (
                f"Stage artikel {i+1} in de Wachtrij ter review — de mens keurt pas "
                "goed. Nooit automatisch live zetten."
            ),
            "skill": "publisher",
            "dependencies": [wt["title"]],
        })

    phases = []
    if research_tasks:
        phases.append({"title": "Fase 1: Onderzoek & selectie", "description": "",
                       "tasks": research_tasks})
    if write_tasks:
        phases.append({"title": "Fase 2: Content creatie", "description": "",
                       "tasks": write_tasks})
    if publish_tasks:
        phases.append({"title": "Fase 3: Publicatie (review-gate)", "description": "",
                       "tasks": publish_tasks})

    if not phases:  # minimale vangnetstructuur
        phases.append({
            "title": "Uitvoering",
            "description": "",
            "tasks": [{
                "title": f"Werk het doel uit voor {project}",
                "description": objective,
                "skill": "content-writer",
                "dependencies": [],
            }],
        })

    return {
        "plan_summary": f"Geautomatiseerd (LLM-vrij) plan voor: {title}. "
                        f"Zet {n_articles} content-kansen om in gepubliceerde content "
                        f"voor {project}, achter de menselijke review-gate.",
        "estimated_duration": "2-5 dagen",
        "phases": phases,
    }


# ── Kern: seed één project ──────────────────────────────────────────────
def seed_project_goal(project: str, force: bool = False) -> Dict[str, Any]:
    """Maak (indien nodig) een actief doel aan voor één project.

    Returns een dict met keys: project, action ('seeded'|'skipped'|'noop'),
    goal_id (optioneel), reason.
    """
    # 1. Eligible?
    if project not in ELIGIBLE_PROJECTS:
        return {"project": project, "action": "skipped",
                "reason": "niet in ELIGIBLE_PROJECTS"}

    objective = PROJECT_OBJECTIVES.get(project) or _DEFAULT_OBJECTIVE
    title = f"G2 — AEO-contentmotor {project}"

    # 2. Al een actief doel? Dan niets doen (tenzij force).
    active = _active_goal_for(project)
    if active and not force:
        return {"project": project, "action": "noop",
                "reason": f"heeft al actief doel {active['id']} ({active['status']})"}

    # 3. Recent doel jonger dan MIN_GOAL_AGE_DAYS? Dan nog niet seeden.
    if not force:
        age = _last_goal_age_days(project)
        if age is not None and age < MIN_GOAL_AGE_DAYS:
            return {"project": project, "action": "skipped",
                    "reason": f"laatste doel pas {age:.0f}d oud (<{MIN_GOAL_AGE_DAYS}d)"}

    # 4. Heeft het project werk om op te baseren (GSC-data)?
    if not force and not _project_has_work(project):
        return {"project": project, "action": "skipped",
                "reason": "geen GSC/SEO-werk voor dit project"}

    # 5. Duplicaat-seed voorkomen (titel ~gelijk aan open doel).
    dup = _similar_open_goal(title, project)
    if dup and not force:
        return {"project": project, "action": "noop",
                "reason": f"gelijksoortig open doel {dup}"}

    # 6. Maak het doel aan — LLM-VRIJ.
    try:
        goal_id = goal_service._create_goal(title, objective, project)  # type: ignore[attr-defined]
        ws = goal_service.GOALS_WORKSPACE / goal_id
        ws.mkdir(parents=True, exist_ok=True)
        plan = _seed_plan(title, objective, project)
        (ws / "plan.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        # confirm zonder LLM: schrijft fasen/taken, zet status='ready'
        goal_service.confirm_plan(goal_id)
        logger.info("[observer] doel geseed voor %s: %s (ready, achter gate)", project, goal_id)
        return {"project": project, "action": "seeded", "goal_id": goal_id,
                "reason": "LLM-vrij plan aangemaakt + bevestigd (ready, achter gate)"}
    except Exception:
        logger.exception("[observer] seeden mislukt voor %s", project)
        return {"project": project, "action": "error",
                "reason": "exception tijdens aanmaken (zie log)"}


# ── Kern: observeer het hele dashboard ──────────────────────────────────
def observe_dashboard() -> Dict[str, Any]:
    """Lees de Control Room-achtige status en seed doelen waar nodig.

    Dit is de zelfsturende laag: in plaats van dat jij (of de Strategist-knop)
    handmatig moet constateren "er staan 0 actieve doelen", doet de observer
    dat periodiek en autonoom — mét de menselijke gate intact.

    Returns een rapport met per project de actie.
    """
    results: List[Dict[str, Any]] = []
    seeded = 0
    skipped = 0
    noop = 0

    # Gebruik de Control Room-status als bron (dezelfde data als het dashboard).
    try:
        from ...domains.strategist.service import control_room_status
        cr = control_room_status()
        projects = cr.get("projects", [])
    except Exception:
        projects = []

    if projects:
        candidates = [p["name"] for p in projects
                      if p.get("name") in ELIGIBLE_PROJECTS
                      and p.get("goals_running", 0) == 0]
    else:
        candidates = sorted(ELIGIBLE_PROJECTS)

    for project in candidates:
        r = seed_project_goal(project)
        results.append(r)
        if r["action"] == "seeded":
            seeded += 1
        elif r["action"] == "noop":
            noop += 1
        else:
            skipped += 1

    return {
        "timestamp": _now_iso(),
        "scanned": len(candidates),
        "seeded": seeded,
        "skipped": skipped,
        "noop": noop,
        "results": results,
    }

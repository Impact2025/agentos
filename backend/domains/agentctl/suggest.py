"""Agent Control — suggestie-engine (Iris' autonome inzet-voorstellen).

Deterministisch, geen LLM: leest de harde Iris-cijfers (per project 4 pijlers
+ trend) en koppelt elke zwakke pijler aan de expert-agent met de grootste
hefboom. Levert kant-en-klare deploy-acties op die Iris (of Vincent) met één
klik kan uitvoeren via deploy_agent() — dezelfde pijplijn als de handmatige
deploy-knop.

Regel: de laagste pijler van een project = de grootste kans. Eén suggestie per
project (de top-hefboom), gesorteerd op potentieel (hoe lager de pijler, hoe
hoger de prioriteit). Zo blijft de stal overzichtelijk bezet i.p.v. alles tegelijk
te bombarderen.
"""
import logging
from typing import Any, Dict, List, Optional

from ..iris import metrics as iris_metrics
from . import service as agentctl_service

logger = logging.getLogger(__name__)

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
        # Laagste pijler = grootste hefboom
        ranked = sorted(
            pillars.items(),
            key=lambda kv: (kv[1].get("score", 0), kv[0]),
        )
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


def execute_all(limit: Optional[int] = None) -> Dict[str, Any]:
    """Voer elke suggestie uit als echte agent-deploy (via de Gauntlet-pijplijn).

    limit: cap op het aantal uit te voeren suggesties (bijv. de top-N per dag,
    zodat de stal niet in één keer overbelast raakt). None = alles.
    Stop niet bij de eerste fout: elke suggestie krijgt zijn eigen run; een
    misfire loggen we en gaan door met de rest.
    """
    data = suggest()
    suggestions = data.get("suggestions", [])
    if limit is not None:
        suggestions = suggestions[:limit]
    results = []
    for s in suggestions:
        if not s.get("agent_id"):
            results.append({"project": s["project"], "ok": False,
                            "reason": "geen agent-id", "agent": s.get("agent")})
            continue
        try:
            res = agentctl_service.deploy_agent(
                agent_id=s["agent_id"],
                task=s["task"],
                project=s["project"],
            )
            results.append({
                "project": s["project"],
                "agent": s["agent"],
                "ok": bool(res.get("ok")),
                "run_id": res.get("run_id"),
            })
        except Exception as exc:
            logger.exception("Suggestie voor %s mislukt", s["project"])
            results.append({"project": s["project"], "agent": s["agent"],
                            "ok": False, "reason": str(exc)[:120]})
    ok_n = sum(1 for r in results if r.get("ok"))
    return {"executed": len(results), "succeeded": ok_n, "results": results}


def _conn():
    """Hergebruik de gedeelde connectiefabriek uit agentctl.service."""
    from ...shared.database import get_conn
    return get_conn()


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
    res = execute_all(limit=max_deploys)
    logger.info("Auto-deploy: %d/%d runs gestart (was %d bezig).",
                res.get("succeeded", 0), res.get("executed", 0), busy)
    return {"skipped": False, **res, "busy_before": busy}

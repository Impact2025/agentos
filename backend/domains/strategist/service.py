"""Strategist & Control Room — AI-manager die status analyseert en prioriteiten stelt."""
import asyncio
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...shared.config import OBSIDIAN_VAULT_PATH, hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner
from ...domains.seo.engine import list_opportunities
from ...domains.seo import sites as sites_service
from ...domains.goal.service import (
    list_goals, delete_goal as _delete_goal,
    resume_stalled_goal as _resume_stalled_goal, is_goal_active,
)
from ...infinite_context import InfiniteContextEngine

logger = logging.getLogger(__name__)

# Strategist-doelen direct bevestigen + starten. Veilig omdat de goal-executor
# zelf niets extern publiceert of verstuurt — dat blijft achter de menselijke
# Wachtrij-gate. Uitschakelen kan met STRATEGIST_AUTOSTART=0 in .env.
import os as _os
_AUTOSTART_GOALS = _os.getenv("STRATEGIST_AUTOSTART", "1") not in ("0", "false", "no")

_infinite = InfiniteContextEngine(OBSIDIAN_VAULT_PATH)

# Laatste autoheal-run — voor het systeemgezondheid-paneel.
_last_autoheal: Dict[str, Any] = {"time": None, "deleted": 0, "resumed": 0, "skipped": 0}

# ── CONTROL ROOM ────────────────────────────────────────────────────

def _count_goals_by_status() -> Dict[str, int]:
    """Tel doelen per status over alle projecten."""
    counts: Dict[str, int] = {}
    try:
        goals = list_goals(limit=100)
        for g in goals:
            s = g.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
    except Exception:
        pass
    return counts


def _count_opportunities(site_name: str) -> Dict[str, int]:
    """Tel kansen per status voor een site."""
    counts: Dict[str, int] = {}
    try:
        site = _find_site(site_name)
        if site:
            opps = list_opportunities(site_id=site.get("id"))
            counts["total"] = len(opps)
            for o in opps:
                s = o.get("status", "new")
                counts[s] = counts.get(s, 0) + 1
    except Exception:
        pass
    return counts


def _find_site(name: str) -> Optional[Dict[str, Any]]:
    norm = lambda x: x.lower().replace(" ", "").replace("-", "")
    target = norm(name)
    for s in sites_service.list_sites():
        if norm(s["name"]) == target:
            return s
    return None


def _project_goals(name: str) -> List[Dict[str, Any]]:
    """Haal goals voor 1 project uit de database."""
    results: List[Dict[str, Any]] = []
    try:
        all_goals = list_goals(limit=100)
        for g in all_goals:
            if g.get("project", "").lower() == name.lower():
                results.append({
                    "id": g["id"],
                    "title": g["title"],
                    "status": g.get("status", ""),
                    "phase_count": g.get("phase_count", 0),
                    "task_count": g.get("task_count", 0),
                    "completed_tasks": g.get("completed_tasks", 0),
                    "failed_tasks": g.get("failed_tasks", 0),
                    "created_at": g.get("created_at", ""),
                })
    except Exception:
        pass
    return results


def control_room_status() -> Dict[str, Any]:
    """Aggregeer de status van ALLE projecten + systemen."""
    # ── Projecten ───────────────────────────────────────────────
    from ...domains.projects.router import _scan_projects
    raw_projects = _scan_projects()
    # Filter workspace-mappen die geen echte projecten zijn
    raw_projects = [p for p in raw_projects if not p.get("name", "").startswith("_")]

    projects: List[Dict[str, Any]] = []
    for p in raw_projects:
        name = p["name"]
        goals = _project_goals(name)
        opps = _count_opportunities(name)
        running_goals = [g for g in goals if g["status"] in ("running", "ready", "paused")]
        projects.append({
            "name": name,
            "description": p.get("skill", {}).get("description", ""),
            "content_count": p.get("content_count", 0),
            "prospecting_runs": p.get("prospecting_runs", 0),
            "goals": goals[:5],
            "goals_total": len(goals),
            "goals_running": len(running_goals),
            "opportunities": opps,
            "gsc_configured": _find_site(name) is not None,
        })

    # ── Doelen overzicht ────────────────────────────────────────
    goal_counts = _count_goals_by_status()
    total_goals = sum(goal_counts.values())

    # ── Infinite Context (Obsidian) ─────────────────────────────
    vault_info: Dict[str, Any] = {"configured": False}
    try:
        vault_path = Path(OBSIDIAN_VAULT_PATH) if OBSIDIAN_VAULT_PATH else None
        if vault_path and vault_path.exists():
            # Tel notities per AgentOS-folder
            sessions = list(vault_path.rglob("AgentOS/Sessions/*.md"))
            tasks = list(vault_path.rglob("AgentOS/Tasks/**/*.md"))
            goals = list(vault_path.rglob("AgentOS/Goals/*.md"))
            vault_info = {
                "configured": True,
                "path": str(vault_path),
                "total_notes": sum(1 for _ in vault_path.rglob("*.md")),
                "sessions_count": len(sessions),
                "tasks_logged": len(tasks),
                "goals_logged": len(goals),
                "omi_configured": _infinite.omi_configured,
            }
    except Exception:
        pass

    # ── Systeem ─────────────────────────────────────────────────
    from ...domains.chat import hermes as hermes_service
    try:
        goals_running_count = goal_counts.get("running", 0) + goal_counts.get("ready", 0)
    except Exception:
        goals_running_count = 0

    return {
        "projects": [p for p in projects if not p.get("name", "").startswith("_")],
        "goals_summary": {
            "total": total_goals,
            "running": goals_running_count,
            "draft": goal_counts.get("draft", 0),
            "completed": goal_counts.get("completed", 0),
            "partial": goal_counts.get("partial", 0),
            "failed": goal_counts.get("failed", 0),
            "paused": goal_counts.get("paused", 0),
        },
        "system": {
            "hermes_backend": hermes_backend(),
            "hermes_model": hermes_service.active_model(),
            "hermes_configured": hermes_service.is_configured(),
            "obsidian": vault_info,
            "scheduler_active": True,
        },
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }


# ── AUTOHEAL — deterministische zelf-reparatie (geen LLM-giswerk) ──────
#
# De Strategist-executie maakte vroeger voor élke geconstateerde afwijking
# een NIEUW doel aan waarvan de titel letterlijk de instructie was
# ("Verwijder het draft-doel ..."), zonder die instructie ooit echt uit te
# voeren. Zulke doelen blijven zelf op 'draft' staan → een dode-mus-lus.
#
# Autoheal lost de twee mechanische oorzaken daarvan direct op, zonder LLM:
#   1. Lege draft-doelen wier titel zelf zo'n instructie is (restanten van
#      de oude bug) → verwijderen.
#   2. Lege draft-doelen die exact dezelfde titel hebben als een ander doel
#      in hetzelfde project dat wél taken heeft (duplicaten) → verwijderen.
#   3. Doelen die in de database nog op 'running' staan maar geen actieve
#      achtergrond-taak meer hebben (typisch na een server-herstart) →
#      hervatten.
#
# Alles wat hierbuiten valt (bv. of een SEO-kans echt is opgelost) vereist
# menselijke/inhoudelijke beoordeling en wordt gerapporteerd, niet auto-fixed.

_ARTIFACT_TITLE_PREFIXES = (
    "zet ", "zet het", "open het", "verwijder het", "start nieuw doel",
    "controleer de", "rond het", "rond de", "koppel ", "heropen",
)


def _is_meta_artifact_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t.startswith(_ARTIFACT_TITLE_PREFIXES):
        return False
    return "doel" in t or "status" in t


def autoheal_goals() -> Dict[str, Any]:
    """Scan alle doelen en fix structurele problemen direct via echte
    functie-aanroepen (delete/resume) — geen nieuwe placeholder-doelen."""
    all_goals = list_goals(limit=200)

    deleted: List[Dict[str, str]] = []
    resumed: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []

    # Groepeer per project voor duplicate-detectie
    by_project: Dict[str, List[Dict[str, Any]]] = {}
    for g in all_goals:
        by_project.setdefault(g.get("project", ""), []).append(g)

    for project, goals in by_project.items():
        titled_with_tasks = {
            (g["title"] or "").strip().lower()
            for g in goals if g.get("task_count", 0) > 0
        }
        for g in goals:
            if g.get("status") != "draft" or g.get("task_count", 0) > 0:
                continue
            title = g.get("title", "")
            reason = None
            if _is_meta_artifact_title(title):
                reason = "meta-instructie-artefact (oude Strategist-bug)"
            elif title.strip().lower() in titled_with_tasks:
                reason = "duplicaat van een doel dat al taken heeft"
            if reason:
                try:
                    _delete_goal(g["id"])
                    deleted.append({"goal_id": g["id"], "project": project, "title": title, "reason": reason})
                except Exception as e:
                    skipped.append({"goal_id": g["id"], "project": project, "title": title, "reason": str(e)})

    # Verweesde running-doelen hervatten (geen actieve taak in dit proces)
    for g in all_goals:
        if g.get("status") == "running" and not is_goal_active(g["id"]):
            try:
                _resume_stalled_goal(g["id"])
                resumed.append({"goal_id": g["id"], "project": g.get("project", ""), "title": g.get("title", "")})
            except Exception as e:
                skipped.append({"goal_id": g["id"], "project": g.get("project", ""), "title": g.get("title", ""), "reason": str(e)})

    global _last_autoheal
    _last_autoheal = {
        "time": __import__("datetime").datetime.now().isoformat(),
        "deleted": len(deleted), "resumed": len(resumed), "skipped": len(skipped),
    }

    return {"deleted": deleted, "resumed": resumed, "skipped": skipped}


# ── SYSTEEMGEZONDHEID ────────────────────────────────────────────────

def system_health(project: Optional[str] = None) -> Dict[str, Any]:
    """Geeft een compact overzicht van wat er mis kan zijn, zodat het
    dashboard problemen kan tonen zonder dat iemand handmatig moet zoeken.

    `project` (optioneel): beperk de publish-foutenteller tot één project,
    zodat een Bijeen-fout niet op het Bewaardvoorjou-dashboard verschijnt.
    """
    from ...scheduler import get_scheduler_status

    all_goals = list_goals(limit=200)
    failed_goals = [g for g in all_goals if g.get("status") == "failed"]
    stalled_goals = [
        g for g in all_goals
        if g.get("status") == "running" and not is_goal_active(g["id"])
    ]

    sched = get_scheduler_status()
    failed_jobs = [
        j for j in sched.get("jobs", [])
        if (j.get("last_run") or {}).get("status") in ("error", "missed")
    ]

    # ── Live-publicatie-status (laatste 2 dagen) ──────────────────────────
    # We onderscheiden twee zaken die de oude code op één hoop gooide:
    #   * live-fout        → de publish-API gaf een échte fout (401, netwerk,
    #                        exception). Dit is een actie-item.
    #   * live-overgeslagen→ géén publish-backend geconfigureerd; artikel is
    #                        bewust alleen lokaal opgeslagen. GEEN fout, wél
    #                        een config-actie (BEWAARDVOORJOU_PUBLISH_URL/_KEY).
    # Allebei tellen we pas mee als er daarna géén geslaagde 'live' voor hetzelfde
    # artikel is gelogd (anders is het al opgelost en blijft het een spookmelding).
    def _title_of(detail: str) -> str:
        """Robuuste titel-extractie uit een log-detail, onafhankelijk van
        apostrofjes in de titel. We pakken de tekst tussen de eerste twee
        enkele quotes: '...'."""
        m = re.search(r"'([^']*)'", detail or "")
        return (m.group(1) if m else (detail or "")).strip()[:60]

    publish_failures: List[Dict[str, Any]] = []   # echte fouten
    publish_unconfigured: List[Dict[str, Any]] = []  # geen backend
    try:
        with get_conn() as conn:
            proj_clause, params = "", []
            if project:
                proj_clause = "AND project = ? "
                params = [project]
            rows = conn.execute(
                "SELECT id, project, action, detail, created_at FROM activity_log "
                "WHERE action IN ('live-fout', 'live-overgeslagen') "
                f"AND created_at >= datetime('now', '-2 days') {proj_clause}"
                "ORDER BY created_at DESC LIMIT 20",
                params,
            ).fetchall()
        for r in rows:
            title = _title_of(r["detail"])
            with get_conn() as conn:
                fixed = conn.execute(
                    "SELECT 1 FROM activity_log WHERE action='live' AND project=? "
                    "AND detail LIKE ? AND created_at >= ? LIMIT 1",
                    (r["project"], f"%{title}%", r["created_at"]),
                ).fetchone()
            if fixed:
                continue  # al opgelost — geen melding
            entry = {
                "project": r["project"], "detail": r["detail"],
                "time": r["created_at"], "action": r["action"],
            }
            if r["action"] == "live-fout":
                publish_failures.append(entry)
            else:
                publish_unconfigured.append(entry)
    except Exception:
        logger.exception("Kon publish-status niet ophalen voor health-check")

    issues: List[str] = []
    if stalled_goals:
        issues.append(f"{len(stalled_goals)} doel(en) staan op 'running' maar draaien niet (verweesd na herstart)")
    if failed_goals:
        issues.append(f"{len(failed_goals)} doel(en) zijn mislukt en wachten op een retry")
    if failed_jobs:
        issues.append(f"{len(failed_jobs)} scheduler-taak(en) zijn recent mislukt of overgeslagen")
    if publish_failures:
        issues.append(f"{len(publish_failures)} artikel(en) konden niet live gezet worden (echte publish-fout)")
    if publish_unconfigured:
        issues.append(
            f"{len(publish_unconfigured)} artikel(en) staan klaar maar zijn niet gepubliceerd — "
            "geen publish-backend geconfigureerd (zie *.env)"
        )
    from ...domains.chat import hermes as hermes_service
    if not hermes_service.is_configured():
        issues.append("Geen AI-backend geconfigureerd")

    return {
        "ok": not issues,
        "issues": issues,
        "stalled_goals": [{"goal_id": g["id"], "title": g["title"], "project": g.get("project", "")} for g in stalled_goals],
        "failed_goals": [{"goal_id": g["id"], "title": g["title"], "project": g.get("project", "")} for g in failed_goals],
        "failed_jobs": [{"id": j["id"], "label": j["label"], "last_run": j["last_run"]} for j in failed_jobs],
        "publish_failures": publish_failures,
        "publish_unconfigured": publish_unconfigured,
        "last_autoheal": _last_autoheal,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }


# ── STRATEGIST — AI Prioriteiten ────────────────────────────────────

_STRATEGIST_SYSTEM = """Je bent de Strategist Agent — de AI-manager van Agent OS.
Je taak: analyseer de huidige status van alle projecten, doelen, kansen en systemen,
en stel concrete, geprioriteerde acties voor.

Je denkt als een ervaren projectmanager:
1. Welke doelen lopen of lopen vast?
2. Welke SEO-kansen zijn het meest waardevol?
3. Wat heeft direct aandacht nodig?
4. Wat kan geautomatiseerd worden?

Je antwoordt in het Nederlands met een helder, gestructureerd overzicht.
Geen algemeenheden — wees specifiek en concreet met aantallen, namen en acties."""


_STRATEGIST_PROMPT_TEMPLATE = """Analyseer de volgende status van Agent OS en stel prioriteiten op.

## Doelen (goals)
{goals_summary}

## Projecten
{projects_detail}

## SEO-kansen
{opportunities_summary}

## Systeemstatus
{system}

## Infinite Context (Obsidian)
{obsidian}

Antwoord met een gestructureerd prioriteitenoverzicht in Markdown:
- 🔴 **Kritiek** (direct actie vereist)
- 🟡 **Belangrijk** (deze week aanpakken)
- 🟢 **Opportuniteit** (binnen 2 weken)
- ℹ️ **Automatisering** (kan door AI-agent gedaan worden)

Per prioriteit: wat, waarom, concrete actie, in welke tool/agent.
Wees specifiek: noem doelen bij naam, aantallen, en welke skill/tool.
Geen algemeenheden — dit zijn direct uitvoerbare instructies."""


async def strategist_analyse() -> Dict[str, Any]:
    """Laat Hermes een strategische analyse maken en prioriteiten stellen."""
    status = control_room_status()

    # Formatteer data voor de prompt
    goals_summary_lines: List[str] = []
    for gs_key, gs_val in status.get("goals_summary", {}).items():
        goals_summary_lines.append(f"- {gs_key}: {gs_val}")
    goals_summary = "\n".join(goals_summary_lines)

    # Project-detail-blok
    proj_lines: List[str] = []
    for p in status.get("projects", []):
        gs = [f"{g['title']} ({g['status']})" for g in p.get("goals", [])]
        opp = p.get("opportunities", {})
        proj_lines.append(
            f"- **{p['name']}**: {p.get('description','')[:80]}\n"
            f"  - Content: {p.get('content_count',0)} | "
            f"  - Doelen: {p.get('goals_running',0)} actief ({p.get('goals_total',0)} totaal)\n"
            f"  - SEO-kansen: {opp.get('total',0)} ({opp.get('new',0)} nieuw)\n"
            f"  - Lopende doelen: {'; '.join(gs[:3]) or 'geen'}"
        )
    projects_detail = "\n".join(proj_lines)

    # Kansen per project
    opp_lines: List[str] = []
    for p in status.get("projects", []):
        opp = p.get("opportunities", {})
        opp_lines.append(f"- {p['name']}: {opp.get('total',0)} totaal, {opp.get('new',0)} nieuw")
    opp_summary = "\n".join(opp_lines)

    sys_info = status.get("system", {})
    sys_lines = [
        f"- Backend: {sys_info.get('hermes_backend','?')}",
        f"- Model: {sys_info.get('hermes_model','?')}",
        f"- Geconfigureerd: {sys_info.get('hermes_configured',False)}",
    ]
    system = "\n".join(sys_lines)

    obs = sys_info.get("obsidian", {})
    obs_lines = [
        f"- Vault: {obs.get('configured',False)}",
        f"- Notities: {obs.get('total_notes',0)}",
        f"- Sessies gelogd: {obs.get('sessions_count',0)}",
        f"- Taken gelogd: {obs.get('tasks_logged',0)}",
        f"- OMI: {obs.get('omi_configured',False)}",
    ]
    obsidian_info = "\n".join(obs_lines)

    user_prompt = _STRATEGIST_PROMPT_TEMPLATE.format(
        goals_summary=goals_summary,
        projects_detail=projects_detail,
        opportunities_summary=opp_summary,
        system=system,
        obsidian=obsidian_info,
    )

    # Roep Hermes aan voor de analyse
    full = ""
    try:
        async for chunk in agent_runner.run_agent(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=_STRATEGIST_SYSTEM,
            agent="hermes",
            use_tools=False,
            max_tokens=2000,
        ):
            if chunk.get("type") == "text":
                full += chunk["text"]
            elif chunk.get("type") == "error":
                logger.warning(f"Strategist agent error: {chunk.get('message')}")
                full = full or f"⚠️ Hermes-fout: {chunk.get('message', 'Onbekend')}"
    except Exception as e:
        err_detail = str(e) or type(e).__name__
        logger.exception(f"Strategist analyse mislukt ({type(e).__name__}): {e}")
        full = f"⚠️ Analyse mislukt: {err_detail}"
        return {"analysis": full, "error": err_detail, "status": status}

    return {"analysis": full.strip(), "status": status, "timestamp": __import__("datetime").datetime.now().isoformat()}


# ── EXECUTE — Prioriteiten omzetten naar concrete acties ────────────

_EXECUTE_SYSTEM = """Je bent de Operationeel Manager van Agent OS.
Je krijgt een strategische analyse met prioriteiten en vertaalt deze naar
concrete, uitvoerbare acties.

Geef een eenvoudige tekstoutput met bullet points (geen JSON).
Elke regel: - PRIORITEIT: project | actie

Prioriteitencodes:
- KRITIEK (direct actie vereist)
- BELANGRIJK (deze week)
- KANS (binnen 2 weken)  
- AUTO (kan door AI)

Max 5 items. Wees specifiek: noem projecten en doelen bij naam.
"""

# Parse platte tekst acties (fallback voor LLM's die JSON negeren)


_ADMIN_ACTION_KEYWORDS = (
    "verwijder", "delete", "status wijzig", "naar running", "naar draft",
    "open het", "open de", "controleer de uitkomst", "start nieuw doel",
    "doelen-agent", "hercontroleer", "markeer",
)


def _looks_like_admin_action(action_text: str, target_skill: str) -> bool:
    """Herken actiepunten die over doelen-administratie gaan (i.p.v. echt
    nieuw werk) — die horen door autoheal_goals() afgehandeld te worden,
    niet door er weer een nieuw draft-doel van te maken."""
    t = (action_text or "").lower()
    return any(k in t for k in _ADMIN_ACTION_KEYWORDS)


async def strategist_execute(analysis: str) -> Dict[str, Any]:
    """Voer eerst de deterministische zelf-reparatie uit (autoheal), en
    vertaal daarna de resterende, echte werk-prioriteiten naar nieuwe doelen."""
    import json as json_mod

    heal_report = autoheal_goals()

    # --- EXECUTE prompt (platte tekst, geen JSON) ---
    user_prompt = (
        "Vertaal de volgende Strategist-analyse naar concrete acties.\n\n"
        f"{analysis}\n\n"
        "Geef bullet points met prioriteit en actie (max 5 items):\n"
        "- KRITIEK: project | actie\n- BELANGRIJK: project | actie\n- etc."
    )

    # Vraag Hermes om de analyse te ontleden in actie-items
    raw = ""
    try:
        async for chunk in agent_runner.run_agent(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=_EXECUTE_SYSTEM,
            agent="hermes",
            use_tools=False,
            max_tokens=1500,
        ):
            if chunk.get("type") == "text":
                raw += chunk["text"]
            elif chunk.get("type") == "error":
                logger.warning(f"Strategist execute error: {chunk.get('message')}")
    except Exception as e:
        logger.exception(f"Strategist execute mislukt: {e}")
        return {"error": str(e), "actions": []}

    # --- Parse platte tekst acties ---
    actions = []
    import re
    for line in raw.split('\n'):
        line = line.strip()
        if not line or not line.startswith('-'):
            continue
        # Pattern: - PRIORITEIT: project | actie
        m = re.match(r'^-\s*(\w+)\s*:\s*([^|]+)\|\s*(.+)$', line, re.IGNORECASE)
        if m:
            priority_raw, project_raw, action_raw = m.groups()
            priority_map = {
                'KRITIEK': 'kritiek', 'BELANGRIJK': 'belangrijk',
                'KANS': 'opportuniteit', 'OPPORTUNITEIT': 'opportuniteit',
                'AUTO': 'automatisering', 'AUTOMATISERING': 'automatisering'
            }
            priority = priority_map.get(priority_raw.upper(), priority_raw.lower())
            actions.append({
                "priority": priority,
                "action": action_raw.strip(),
                "target_project": project_raw.strip(),
                "target_skill": "content-writer" if "content" in action_raw.lower() else "",
            })

    if not actions:
        return {
            "error": None if (heal_report["deleted"] or heal_report["resumed"]) else "Kon geen acties extraheren uit analyse",
            "raw": raw[:500], "actions": [], "autoheal": heal_report,
        }

    # Probeer doelen aan te maken voor kritieke items — maar alleen voor
    # échte nieuwe werk-items. Admin/doelen-beheer-acties (verwijderen,
    # status wijzigen, etc.) zijn al door autoheal_goals() afgehandeld;
    # die zouden anders opnieuw een dode placeholder-goal opleveren.
    created_goals = []
    created_tasks = 0
    admin_skipped = []
    for item in actions[:3]:  # Max 3 om niet te veel te doen
        priority = item.get("priority", "")
        action_text = item.get("action", "")
        project = item.get("target_project", "") or item.get("project", "")
        skill = item.get("target_skill", "") or ""

        if _looks_like_admin_action(action_text, skill):
            admin_skipped.append({"action": action_text, "project": project})
            continue

        if priority in ("kritiek", "belangrijk") and project:
            try:
                # Maak een goal aan — en start hem meteen. De draft-status
                # voegde geen veiligheid toe (elke taak is concept-only of
                # eindigt in de Wachtrij-review-gate) maar liet doelen wél
                # verstoffen totdat iemand toevallig op Bevestig klikte.
                from ...domains.goal.service import (
                    create_and_plan, confirm_plan, start_goal_async,
                )
                goal = await create_and_plan(
                    title=action_text[:80],
                    objective=f"Strategist prioriteit ({priority}): {action_text}",
                    project=project,
                )
                if goal and goal.get("goal_id"):
                    auto_started = False
                    if _AUTOSTART_GOALS:
                        try:
                            confirm_plan(goal["goal_id"])
                            await start_goal_async(goal["goal_id"])
                            auto_started = True
                        except Exception as e:
                            logger.warning(
                                f"Auto-start van doel '{action_text[:40]}' mislukt "
                                f"(blijft als draft in het Actiecentrum staan): {e}"
                            )
                    created_goals.append({
                        "goal_id": goal["goal_id"],
                        "title": action_text[:80],
                        "project": project,
                        "priority": priority,
                        "auto_started": auto_started,
                    })
                    # Tel taken uit het plan
                    plan = goal.get("plan", {})
                    for ph in plan.get("phases", []):
                        created_tasks += len(ph.get("tasks", []))
                    from ...shared.outcomes import log_outcome
                    log_outcome(
                        project, "strategist_goal",
                        f"Doel '{action_text[:80]}' aangemaakt"
                        + (" en direct gestart" if auto_started else " (wacht op bevestiging)"),
                        next_step="" if auto_started else "Bevestig of verwijder het doel in het Actiecentrum",
                    )
            except Exception as e:
                logger.warning(f"Kon goal niet aanmaken voor '{action_text[:40]}': {e}")

    return {
        "actions": actions[:5],
        "created_goals": created_goals,
        "created_tasks": created_tasks,
        "autoheal": heal_report,
        "admin_skipped": admin_skipped,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }

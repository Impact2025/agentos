"""Agent Control service: occupancy, deploy, orphan-recovery.

Alle LLM-werk gaat via de BESTAANDE pijplijnen (gauntlet / delegate / goals).
Dit domein is een controlelaag, geen nieuwe agent-engine.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...expert.team import ensure_expert_team
from ..gauntlet import service as gauntlet_service
from ..delegate import event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Orphan recovery ──────────────────────────────────────────────────────────
# Gauntlet / loops / delegate zijn asyncio-background-tasks die een server-
# herstart NIET overleven. Als de DB bij opstart nog 'running' zegt maar er is
# geen levende taak, markeren we ze als 'stopped' (geen dataverlies, alleen
# eerlijke status). Goals hebben hun eigen autoheal; die laten we met rust.
def recover_orphans() -> Dict[str, int]:
    """Bij opstart: 'running' runs/loops zonder levende taak -> 'stopped'."""
    recovered = {"gauntlet": 0, "loops": 0}
    with get_conn() as conn:
        # Gauntlet-runs: 'running' maar zonder echte eindtijd (= leeg of NULL)
        # zijn wezen door de mand gevallen bij een herstart.
        cur = conn.execute(
            "UPDATE gauntlet_runs SET status='stopped', finished_at=?, "
            "updated_at=? WHERE status='running' "
            "AND (finished_at IS NULL OR finished_at='')",
            (_now(), _now()),
        )
        recovered["gauntlet"] = cur.rowcount
        # Loops
        cur = conn.execute(
            "UPDATE loops SET status='stopped', finished_at=? "
            "WHERE status='running' AND (finished_at IS NULL OR finished_at='')",
            (_now(),),
        )
        recovered["loops"] = cur.rowcount
    if recovered["gauntlet"] or recovered["loops"]:
        logger.warning(
            "AgentCtl orphan-recovery: %d gauntlet-run(s), %d loop(s) als stopped gemarkeerd",
            recovered["gauntlet"], recovered["loops"],
        )
    return recovered


# ── Goal-backlog opruimen ─────────────────────────────────────────────────────
# Oude 'partial' goals (conveyor is vastgelopen, geen actieve run meer) vervuilen
# het dashboard en vertroebelen Iris' cijfers. We markeren ze als 'completed' i.p.v.
# ze te verwijderen: het werk (completed_tasks) is gedaan, alleen de afronding niet
# geregistreerd. Niet-destructief, en de data blijft beschikbaar voor analyse.
def cleanup_stale_goals(older_than_days: int = 7) -> Dict[str, Any]:
    """Markeer 'partial' goals ouder dan N dagen (zonder actieve run) als completed."""
    cleaned: Dict[str, Any] = {"goals_marked": 0, "goals": []}
    with get_conn() as conn:
        # Alleen partials zonder lopende taak in dit proces.
        rows = conn.execute(
            "SELECT id, title, project, completed_tasks, created_at FROM goals "
            "WHERE status='partial' AND created_at < datetime('now', ?)",
            (f"-{older_than_days} days",),
        ).fetchall()
        for r in rows:
            # Actieve run? Dan niet aanraken.
            active = conn.execute(
                "SELECT 1 FROM gauntlet_runs WHERE objective LIKE ? AND status='running' LIMIT 1",
                (f"%{r['title'][:20]}%",),
            ).fetchone()
            if active:
                continue
            conn.execute(
                "UPDATE goals SET status='completed', finished_at=? WHERE id=?",
                (_now(), r["id"]),
            )
            cleaned["goals"].append({
                "id": r["id"], "project": r["project"], "title": r["title"],
                "completed_tasks": r["completed_tasks"],
            })
            cleaned["goals_marked"] += 1
    if cleaned["goals_marked"]:
        logger.warning("AgentCtl goal-cleanup: %d stale partial-goals als completed gemarkeerd",
                       cleaned["goals_marked"])
    return cleaned


# ── Live occupancy ────────────────────────────────────────────────────────────
def _active_workloads() -> Dict[str, List[str]]:
    """Bepaal per agent-naam waar die nu (mogelijk) mee bezig is.

    AgentCtl-deploys zetten de agent-naam als '[Rol] taak' prefix in de
    Gauntlet-run objective. Die prefix parsen we om de agent als 'busy' te
    markeren. Daarnaast matchen we op Gauntlet-subtask-rollen en delegate-
    worker-profiles (voor handmatig gestarte runs).
    """
    import re
    busy: Dict[str, List[str]] = {}

    def _mark(agent_name: str, what: str):
        busy.setdefault(agent_name, []).append(what)

    _owner_re = re.compile(r"^\[([^\]]+)\]\s*(.*)$", re.DOTALL)

    with get_conn() as conn:
        # Actieve gauntlet-runs: parse '[Agent] taak' prefix -> owner busy
        rows = conn.execute(
            "SELECT id, objective FROM gauntlet_runs WHERE status='running'"
        ).fetchall()
        for r in rows:
            obj = (r["objective"] or "")
            _mark("__gauntlet__", f"Gauntlet {r['id'][:24]}: {obj[:60]}")
            m = _owner_re.match(obj.strip())
            if m:
                owner = m.group(1).strip()
                _mark(owner, f"{m.group(2)[:60]} (run {r['id'][:24]})")
        # Actieve gauntlet-subtasks (rollen ~ agenten)
        sub = conn.execute(
            "SELECT s.role AS role, s.status AS status FROM gauntlet_subtasks s "
            "JOIN gauntlet_runs g ON g.id=s.run_id WHERE g.status='running'"
        ).fetchall()
        for s in sub:
            _mark(str(s["role"]), f"subtaak '{s['role']}' ({s['status']})")
        # Actieve delegate-workers (profile = agent-naam)
        try:
            dw = conn.execute(
                "SELECT profile, status FROM delegate_workers WHERE status IN "
                "('running','pending')"
            ).fetchall()
            for w in dw:
                if w["profile"]:
                    _mark(str(w["profile"]), f"worker ({w['status']})")
        except Exception:
            pass
        # Actieve goals
        goals = conn.execute(
            "SELECT title, project, status FROM goals WHERE status='running' "
            "OR status='partial'"
        ).fetchall()
        for g in goals:
            _mark("__goals__", f"Goal '{g['title'][:40]}' ({g['project']})")
    return busy


def list_agents() -> List[Dict[str, Any]]:
    """De 13 expert-profielen MET live occupancy + health.

    'state': idle | busy | unknown
    'work':  lijst met wat de agent nu doet (leeg bij idle)
    """
    ensure_expert_team()
    busy = _active_workloads()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, model, memory_session FROM agent_profiles ORDER BY id"
        ).fetchall()

    agents: List[Dict[str, Any]] = []
    for r in rows:
        name = r["name"]
        work = busy.get(name, [])
        # Matchen op deelstring: een agent kan bezig zijn als zijn naam in een
        # rol/goal voorkomt (bv. 'SEO Copywriter' in gauntlet-subtaak-rol).
        if not work:
            for k, v in busy.items():
                if k.startswith("__"):
                    continue
                if name.lower() in k.lower() or k.lower() in name.lower():
                    work = v
                    break
        agents.append({
            "id": r["id"],
            "name": name,
            "model": r["model"] or "deepseek-v4-flash",
            "state": "busy" if work else "idle",
            "work": work,
            "memory_session": r["memory_session"],
            "deployable": True,
        })
    # Samenvatting bovenaan
    idle = [a["name"] for a in agents if a["state"] == "idle"]
    busy_n = [a["name"] for a in agents if a["state"] == "busy"]
    return {
        "agents": agents,
        "summary": {
            "total": len(agents),
            "idle": idle,
            "busy": busy_n,
            "idle_count": len(idle),
            "busy_count": len(busy_n),
        },
        "running_gauntlets": len(busy.get("__gauntlet__", [])),
        "running_goals": len(busy.get("__goals__", [])),
        "generated_at": _now(),
    }


# ── Deploy: zet een agent op een taak (via bestaande pijplijn) ────────────────
def deploy_agent(agent_id: int, task: str, project: Optional[str] = None,
                 benchmark: Optional[str] = None) -> Dict[str, Any]:
    """Start een echte taak met één expert-agent via de Gauntlet-pijplijn.

    Dit is GEEN nep-knop: het roept dezelfde spawn_gauntlet aan die de UI
    gebruikt, met de agent-profielnaam als rol. De taak draait echt in de
    achtergrond en is te volgen via /api/gauntlet/stream.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, model FROM agent_profiles WHERE id=?", (agent_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"Geen agent met id={agent_id}")

    role = row["name"]
    objective = f"[{role}] {task}"
    # Zonder benchmark kan de Gauntlet niet scoren; we gebruiken dan de taak
    # zelf als minimale richtlijn zodat de run niet weigert.
    bench = benchmark or (
        f"Richtlijn: lever concreet, bruikbaar werk voor project "
        f"'{project or 'algemeen'}' volgens de expertise van {role}. "
        f"Geen plaatsvervangers, geen 'zie boven'."
    )
    try:
        result = gauntlet_service.spawn_gauntlet(
            objective=objective,
            benchmark=bench,
            threshold=80,
            max_iterations=3,
            session_id=None,
            model_override=None,  # profielmodel (deepseek-v4-flash) via gateway
        )
    except Exception as exc:  # pragma: no cover — doorzetten naar caller
        logger.exception("Deploy van %s mislukte", role)
        raise
    event_bus.publish({
        "type": "agentctl_deploy",
        "agent_id": agent_id,
        "role": role,
        "run_id": result.get("run_id"),
        "task": task[:120],
        "project": project,
        "at": _now(),
    })
    return {
        "ok": True,
        "agent": role,
        "run_id": result.get("run_id"),
        "message": f"{role} is gestart op de taak (volg via /api/gauntlet/stream).",
    }


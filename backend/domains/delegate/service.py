"""
Delegate Service — parallelle subagent-orkestratie (de 'Delegate Tool').

Verschil met de conveyor:
  * conveyor_loop = SEQUENTIËLE assembly line (1 ready-taak tegelijk, lineaire keten).
  * delegate_service = PARALLELLE fan-out (N onafhankelijke workers tegelijk).

Flow:
  1. De Lead Agent (chat) roept de `delegate`-tool aan met een lijst workers.
  2. spawn_delegation() persisteert de batch + workers en start ze als
     achtergrond-asyncio-tasks. Het keert BINNEN milliseconden terug → de UI
     blokkeert niet.
  3. Elke worker draait in zijn eigen context-box (eigen system prompt, eigen
     Obsidian-context, geen kruisbesmetting met andere workers).
  4. Zodra een worker klaar is, stroomt het resultaat als zelfstandig,
     self-contained bericht terug via de event_bus → UI/dashboard.

Fouttolerantie: elke worker draait in een eigen try/except én onder
asyncio.gather(return_exceptions=True). Eén crashende worker laat de andere
vier gewoon doorlopen.
"""
from __future__ import annotations

import asyncio
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...shared.config import BASE_DIR, OBSIDIAN_VAULT_PATH, hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from . import event_bus
from ...domains.chat.obsidian import ObsidianService
from ...domains.pipeline.service import get_agent_profile
from ...infinite_context import InfiniteContextEngine

logger = logging.getLogger(__name__)

_obsidian = ObsidianService(OBSIDIAN_VAULT_PATH)
_infinite_ctx = InfiniteContextEngine(OBSIDIAN_VAULT_PATH)
DELEGATE_WORKSPACE_ROOT = BASE_DIR / "workspaces" / "delegations"

# Sterke referenties naar lopende achtergrond-tasks. Zonder dit kan de garbage
# collector een create_task()'d coroutine opruimen vóór die klaar is.
_BG_TASKS: "set[asyncio.Task]" = set()

DEFAULT_WORKER_PROMPT = (
    "Je bent een gespecialiseerde AI-worker in een parallel agent-team. "
    "Je hebt één strak omkaderd doel. Lever een direct bruikbaar, self-contained "
    "eindproduct in Markdown — geen meta-uitleg vooraf of achteraf, geen vragen terug. "
    "Schrijf in het Nederlands tenzij anders gevraagd."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Profielmodel → bare model-string die de cloud-gateway snapt.

    Geeft de model-string terug (eventuele 'openrouter/'-prefix gestript)
    zodra een cloud-sleutel aanwezig is — ongeacht welke backend de app
    standaard gebruikt. run_agent routeert automatisch naar de juiste
    cloud-backend (zie _cloud_backend_for_model), dus een 'pro'-profiel
    (claude-sonnet-4-6 via OpenModel) wordt echt gehonoreerd i.p.v. op het
    goedkope default-model te vallen. Bij geen profielmodel/sleutel → None."""
    if not profile_model:
        return None
    model = profile_model.strip()
    if model.startswith("openrouter/"):
        from ...shared.config import OPENROUTER_API_KEY
        return model[len("openrouter/"):] if OPENROUTER_API_KEY else None
    from ...shared.config import OPENMODEL_API_KEY
    return model if OPENMODEL_API_KEY else None


def _profile_backend(profile_model: Optional[str]) -> Optional[str]:
    """Legacy helper — wordt niet meer gebruikt sinds run_agent auto-route doet.
    Bestaat nog voor backwards-compat met oudere aanroepers; geeft de cloud-
    backend terug als het profiel een cloud-model noemt én de sleutel aanwezig
    is, anders None (standaard-backend)."""
    if not profile_model:
        return None
    model = profile_model.strip()
    if model.startswith("openrouter/"):
        from ...shared.config import OPENROUTER_API_KEY
        return "openrouter" if OPENROUTER_API_KEY else None
    from ...shared.config import OPENMODEL_API_KEY
    return "openmodel" if OPENMODEL_API_KEY else None


# ── Shared memory / brand brief (Obsidian) ───────────────────────────────────

def _build_brand_brief(objective: str, cta: Optional[str]) -> str:
    """Haal merkrichtlijnen, conversiedoelen en interne link-targets uit Obsidian.

    Dit is de gedeelde context die ELKE worker meekrijgt, zodat het hele team
    on-brand schrijft en naar dezelfde conversiedoelen toewerkt.
    """
    parts: List[str] = []
    if _obsidian.is_configured:
        brand_ctx = _obsidian.build_context(
            f"merkrichtlijnen tone of voice conversiedoel interne links CTA {objective}",
            max_chars=2500,
        )
        if brand_ctx:
            parts.append("## Merk- & conversiekader (Obsidian, gedeeld geheugen)\n" + brand_ctx)
    if cta:
        parts.append("## Verplichte call-to-action\n" + cta)
    return "\n\n".join(parts)


# ── Persistence ──────────────────────────────────────────────────────────────

def _create_batch(objective: str, session_id: Optional[str], workers: List[Dict[str, Any]]) -> Dict[str, Any]:
    delegation_id = f"deleg-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    now = _now()
    worker_rows: List[Dict[str, Any]] = []
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO delegations (id, objective, session_id, status, worker_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (delegation_id, objective, session_id or "", "running", len(workers), now, now),
        )
        for idx, w in enumerate(workers):
            worker_id = str(uuid.uuid4())
            profile_id = _resolve_profile_id(conn, w)
            conn.execute(
                """INSERT INTO subagents
                   (id, delegation_id, position, role, goal, profile_id, status,
                    result, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued', '', '', ?, ?)""",
                (worker_id, delegation_id, idx, w.get("role") or f"worker-{idx+1}",
                 w.get("goal") or "", profile_id, now, now),
            )
            worker_rows.append({
                "id": worker_id, "position": idx,
                "role": w.get("role") or f"worker-{idx+1}",
                "goal": w.get("goal") or "",
                "profile_id": profile_id,
                # Default uit, net als de conveyor: zwakke modellen hallucineren
                # anders tool-calls. Zet per worker expliciet use_tools=true voor
                # research-workers op een capabel model.
                "use_tools": bool(w.get("use_tools", False)),
            })
    return {"delegation_id": delegation_id, "workers": worker_rows}


def _resolve_profile_id(conn, w: Dict[str, Any]) -> Optional[int]:
    """Vind een profiel-id via expliciete id of via profielnaam."""
    if w.get("profile_id"):
        return int(w["profile_id"])
    name = w.get("profile_name") or w.get("profile")
    if name:
        row = conn.execute("SELECT id FROM agent_profiles WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
    return None


def _update_worker(worker_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE subagents SET {set_clause} WHERE id = ?",
            list(fields.values()) + [worker_id],
        )


def _finish_batch(delegation_id: str) -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status FROM subagents WHERE delegation_id = ?", (delegation_id,)
        ).fetchall()
        done = sum(1 for r in rows if r["status"] == "done")
        failed = sum(1 for r in rows if r["status"] == "error")
        status = "done" if failed == 0 else ("partial" if done else "failed")
        conn.execute(
            "UPDATE delegations SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?",
            (status, _now(), _now(), delegation_id),
        )
    return {"done": done, "failed": failed, "status": status}


# ── Worker execution (geïsoleerde context-box) ───────────────────────────────

async def _run_worker(delegation_id: str, objective: str, brand_brief: str, worker: Dict[str, Any]) -> None:
    worker_id = worker["id"]
    role = worker["role"]
    goal = worker["goal"]

    # Eigen system prompt = profiel-brein (of default) + gedeeld merk/conversiekader.
    profile = get_agent_profile(worker.get("profile_id"))
    base_prompt = (profile.get("system_prompt") if profile else "") or DEFAULT_WORKER_PROMPT
    model_override = _resolve_model_override(profile.get("model") if profile else None)
    # Als het profiel een cloud-model noemt, forceer dan die cloud-backend —
    # anders draait een 'pro'-profiel stilzwijgend op de lokale Ollama-default.
    backend_override = _profile_backend(profile.get("model") if profile else None)
    system_prompt = base_prompt
    if brand_brief:
        system_prompt += "\n\n" + brand_brief

    # Per-worker, doel-specifieke Obsidian-context (bovenop de gedeelde brief).
    goal_ctx = _obsidian.build_context(goal, max_chars=1500) if _obsidian.is_configured else ""

    # Geïsoleerde user-message: alleen het eigen doel + de eigen context. Geen
    # output of context van zusje-workers → geen kruisbesmetting.
    user_message = (
        f"# Teamopdracht (overkoepelend, ter oriëntatie)\n{objective}\n\n"
        f"# JOUW taak ({role})\n{goal}\n"
    )
    if goal_ctx:
        user_message += f"\n# Relevante kennis uit Obsidian\n{goal_ctx}\n"

    started = time.perf_counter()
    _update_worker(worker_id, status="running", started_at=_now())
    event_bus.publish({
        "type": "worker_start", "delegation_id": delegation_id,
        "worker_id": worker_id, "role": role, "goal": goal,
    })

    try:
        chunks: List[str] = []
        async for event in agent_service.run_agent(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
            agent="hermes",
            model_override=model_override,
            use_tools=worker.get("use_tools", False),
            backend_override=backend_override,
        ):
            etype = event.get("type")
            if etype == "error":
                raise RuntimeError(event.get("message") or "Onbekende agent-fout")
            if etype == "tool_start":
                # Lichte voortgangsmelding voor observability in de UI.
                event_bus.publish({
                    "type": "worker_progress", "delegation_id": delegation_id,
                    "worker_id": worker_id, "role": role,
                    "tool": event.get("name", ""),
                })
            elif etype == "text":
                chunks.append(event["text"])

        result = "".join(chunks).strip() or "_(De worker leverde geen tekst op.)_"
        duration_ms = int((time.perf_counter() - started) * 1000)

        workspace_file = _write_result_file(delegation_id, worker, result)
        _update_worker(
            worker_id, status="done", result=result, error="",
            finished_at=_now(), duration_ms=duration_ms,
            workspace_path=str(workspace_file) if workspace_file else "",
        )
        # Self-contained bericht → stroomt terug naar de UI als zelfstandige kaart.
        event_bus.publish({
            "type": "worker_done", "delegation_id": delegation_id,
            "worker_id": worker_id, "role": role, "goal": goal,
            "status": "done", "title": role, "content": result,
            "duration_ms": duration_ms,
        })
        logger.info("Worker %s (%s) klaar in %sms", worker_id, role, duration_ms)

        # ── WRITE: Log worker-resultaat naar Obsidian (Infinite Context) ──
        if _infinite_ctx.is_configured and result and result != "_(De worker leverde geen tekst op.)_":
            try:
                _infinite_ctx.log_agent_session(
                    title=f"Worker: {role}",
                    summary=f"**Doel:** {goal[:300]}\n\n**Resultaat:**\n{result[:1000]}",
                    tags=["agentos", "delegate", role.replace(" ", "_")],
                )
            except Exception as e:
                logger.warning("Infinite Context worker log mislukt: %s", e)

    except Exception as exc:  # noqa: BLE001 — bewust breed: één worker mag de batch niet slopen
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_worker(
            worker_id, status="error", error=str(exc),
            finished_at=_now(), duration_ms=duration_ms,
        )
        event_bus.publish({
            "type": "worker_error", "delegation_id": delegation_id,
            "worker_id": worker_id, "role": role, "goal": goal,
            "status": "error", "title": role, "content": f"Worker faalde: {exc}",
            "error": str(exc), "duration_ms": duration_ms,
        })
        logger.exception("Worker %s (%s) faalde: %s", worker_id, role, exc)


def _write_result_file(delegation_id: str, worker: Dict[str, Any], content: str) -> Optional[Path]:
    """Schrijf het resultaat ook naar een workspace-bestand (DB blijft bron van waarheid)."""
    try:
        slug = "".join(c if c.isalnum() else "-" for c in worker["role"].lower()).strip("-") or "worker"
        path = DELEGATE_WORKSPACE_ROOT / delegation_id / f"{worker['position']+1:02d}-{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
    except Exception as werr:  # noqa: BLE001
        logger.warning("Kon worker-resultaat niet wegschrijven: %s", werr)
        return None


async def _run_batch(delegation_id: str, objective: str, cta: Optional[str], workers: List[Dict[str, Any]]) -> None:
    brand_brief = _build_brand_brief(objective, cta)
    event_bus.publish({
        "type": "delegation_start", "delegation_id": delegation_id,
        "objective": objective, "worker_count": len(workers),
        "roles": [w["role"] for w in workers],
    })

    # DE PARALLELLE FAN-OUT. return_exceptions=True is de vangnet-laag bovenop de
    # per-worker try/except: zelfs een onverwachte fout stopt de siblings niet.
    await asyncio.gather(
        *(_run_worker(delegation_id, objective, brand_brief, w) for w in workers),
        return_exceptions=True,
    )

    summary = _finish_batch(delegation_id)
    event_bus.publish({
        "type": "delegation_done", "delegation_id": delegation_id,
        "objective": objective, **summary,
    })
    logger.info("Delegation %s afgerond: %s", delegation_id, summary)


# ── Publieke API ─────────────────────────────────────────────────────────────

def spawn_delegation(
    objective: str,
    workers: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    cta: Optional[str] = None,
) -> Dict[str, Any]:
    """Start een parallelle delegatie en KEER DIRECT TERUG (non-blocking).

    Wordt aangeroepen vanuit de `delegate`-tool, die zelf binnen een lopende
    event loop draait (FastAPI/uvicorn). We persisteren synchroon (snel) en
    starten daarna de workers als losse achtergrond-task.
    """
    if not workers:
        raise ValueError("Geen workers opgegeven om te delegeren.")

    batch = _create_batch(objective, session_id, workers)
    delegation_id = batch["delegation_id"]

    task = asyncio.create_task(_run_batch(delegation_id, objective, cta, batch["workers"]))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

    return {
        "delegation_id": delegation_id,
        "worker_count": len(batch["workers"]),
        "roles": [w["role"] for w in batch["workers"]],
    }


def get_delegation(delegation_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        d = conn.execute("SELECT * FROM delegations WHERE id = ?", (delegation_id,)).fetchone()
        if not d:
            return None
        ws = conn.execute(
            "SELECT * FROM subagents WHERE delegation_id = ? ORDER BY position ASC", (delegation_id,)
        ).fetchall()
    out = dict(d)
    out["workers"] = [dict(w) for w in ws]
    return out


def list_delegations(limit: int = 25) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM delegations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

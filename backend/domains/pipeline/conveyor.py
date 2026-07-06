"""
Conveyor loop: scant de database op ready-taken, voert ze uit via agenten
en schuift de state machine door: todo -> ready -> running -> done/awaiting_approval.

Dit is de motor van het content-pipeline, met traceerbare state transitions voor wereldklasse observability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ...shared.config import BASE_DIR, hermes_backend
from ...shared import agent_runner as agent_service
from ...domains.pipeline.service import (
    get_next_ready_task,
    set_task_status,
    get_agent_profile,
    get_previous_result,
)

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ROOT = BASE_DIR / "workspaces"
STATE_TRANSITIONS = [
    "todo",
    "ready",
    "running",
    "done",
    "awaiting_approval",
]

DEFAULT_SYSTEM_PROMPT = (
    "Je bent een professionele AI-agent in een content-pipeline. "
    "Je werkt in Markdown, volgt de projectnormen en schrijft in het Nederlands. "
    "Je levert direct bruikbare eindproducten zonder uitleg."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Vertaal een profiel-model naar een waarde die de actieve backend snapt.

    Profielen slaan modellen op als 'openrouter/<vendor>/<model>'. Voor de
    OpenRouter-backend strippen we de 'openrouter/'-prefix. Voor lokale Hermes /
    Ollama is het model vast (gateway bepaalt dit), dus geen override.
    """
    if not profile_model:
        return None
    model = profile_model.strip()
    backend = hermes_backend()
    if backend == "openrouter":
        return model[len("openrouter/"):] if model.startswith("openrouter/") else model
    return None


async def _run_agent_for_task(
    agent_name: str,
    title: str,
    description: str,
    system_prompt: str,
    model_override: Optional[str],
    prior_result: Optional[str] = None,
) -> str:
    user_message = (
        f"# Opdracht\n"
        f"Titel: {title}\n"
        f"Beschrijving: {description}\n"
    )
    if prior_result:
        # Geef de output van de vorige pijplijnstap mee als input.
        user_message += (
            "\n# Resultaat van de vorige stap (gebruik dit als input)\n"
            f"{prior_result}\n"
        )

    chunks: list[str] = []
    async for event in agent_service.run_agent(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        agent=agent_name or "hermes",
        model_override=model_override,
        use_tools=False,
    ):
        if event.get("type") == "error":
            raise RuntimeError(event.get("message") or "Onbekende agent-fout")
        text = event.get("text") or ""
        chunks.append(text)

    return "".join(chunks).strip()


def _write_workspace_file(workspace_path: str, content: str) -> Path:
    full_path = DEFAULT_WORKSPACE_ROOT / workspace_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return full_path


async def _execute_task(task: dict) -> dict:
    agent_name = task.get("agent") or "hermes"
    workspace_path = task.get("workspace_path") or ""
    task_id = task.get("id")

    # Profiel-bewust: gebruik model + system_prompt van het toegewezen profiel.
    profile = get_agent_profile(task.get("assigned_agent_id"))
    system_prompt = (profile.get("system_prompt") or "").strip() if profile else ""
    model_override = _resolve_model_override(profile.get("model") if profile else None)

    # Ketting-bewust: geef de output van de vorige stap mee als input.
    prior_result = get_previous_result(task)

    started_at = _now()
    logger.info(
        "Task %s start execution (agent=%s, profiel=%s, model=%s)",
        task_id, agent_name,
        profile.get("name") if profile else "-",
        model_override or "default",
    )
    set_task_status(task_id, "running", started_at=started_at, error="")
    started = time.perf_counter()

    try:
        result_text = await _run_agent_for_task(
            agent_name=agent_name,
            title=task.get("title") or "",
            description=task.get("description") or "",
            system_prompt=system_prompt,
            model_override=model_override,
            prior_result=prior_result,
        )

        if not result_text:
            result_text = "_(De agent leverde geen tekst op.)_"

        # Optioneel ook naar een workspace-bestand schrijven (DB blijft bron van waarheid).
        if workspace_path:
            try:
                _write_workspace_file(workspace_path, result_text)
            except Exception as werr:  # noqa: BLE001
                logger.warning("Task %s: workspace-bestand schrijven mislukt: %s", task_id, werr)

        duration_ms = int((time.perf_counter() - started) * 1000)
        updated = set_task_status(
            task_id, "awaiting_approval",
            result=result_text, error="",
            finished_at=_now(), duration_ms=duration_ms,
        )

        logger.info(
            "Task %s finished: status=%s duration=%sms",
            task_id,
            (updated or {}).get("status", "awaiting_approval"),
            duration_ms,
        )
        return {
            "task_id": task_id,
            "status": (updated or {}).get("status", "awaiting_approval"),
            "output": result_text,
            "duration_ms": duration_ms,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Task %s failed: %s", task_id, exc)
        duration_ms = int((time.perf_counter() - started) * 1000)
        # Terug naar 'todo' (niet 'ready') zodat de conveyor niet in een faal-lus komt;
        # de fout blijft zichtbaar in de UI.
        updated = set_task_status(
            task_id, "todo",
            error=str(exc), finished_at=_now(), duration_ms=duration_ms,
        )
        return {
            "task_id": task_id,
            "status": (updated or {}).get("status", "todo"),
            "output": f"Fout tijdens uitvoering: {exc}",
            "duration_ms": duration_ms,
        }


async def conveyor_loop(
    poll_interval: float = 2.0,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    logger.info(
        "Conveyor loop gestart (interval %.1fs, states=%s)",
        poll_interval,
        ", ".join(STATE_TRANSITIONS),
    )
    if stop_event is None:
        stop_event = asyncio.Event()

    while not stop_event.is_set():
        try:
            task = get_next_ready_task()
            if task:
                logger.info(
                    "Pick next ready task: %s (%s)",
                    task.get("title"),
                    task.get("id"),
                )
                result = await _execute_task(task)
                logger.info(
                    "Task transitioned to %s in %sms",
                    result.get("status"),
                    result.get("duration_ms"),
                )
            else:
                await asyncio.sleep(poll_interval)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fout in conveyor loop: %s", exc)
            await asyncio.sleep(poll_interval)

    logger.info("Conveyor loop gestopt")


run_conveyor = conveyor_loop

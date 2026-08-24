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
from ...shared.database import get_conn
from ...domains.pipeline.service import (
    get_ready_tasks,
    set_task_status,
    get_agent_profile,
    get_previous_result,
    revive_stalled_chains,
)
from ...shared.failures import describe_exception
from ...shared.outcomes import require_llm_budget, BudgetExceeded

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ROOT = BASE_DIR / "workspaces"
# Een 'running' taak die langer dan deze grens nergens meer door de conveyor
# wordt aangeraakt, is een wees: de conveyor pakt alléén 'ready' op, dus een
# taak op 'running' wordt nooit meer herstart en blokkeert zijn keten. De
# sweep (en de boot-time recover_orphans) zetten die na deze leeftijd terug
# naar 'todo', waarna revive_stalled_chains() de foutklasse beoordeelt.
STALE_RUNNING_HOURS = 6
STATE_TRANSITIONS = [
    "todo",
    "ready",
    "running",
    "done",
    "awaiting_approval",
    "needs_work",
]

DEFAULT_SYSTEM_PROMPT = (
    "Je bent een professionele AI-agent in een content-pipeline. "
    "Je werkt in Markdown, volgt de projectnormen en schrijft in het Nederlands. "
    "Je levert direct bruikbare eindproducten zonder uitleg."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Profielmodel → bare model-string die de cloud-gateway snapt.

    Geeft de model-string terug (eventuele 'openrouter/'-prefix gestript)
    zodra een cloud-sleutel aanwezig is — ongeacht welke backend de app
    standaard gebruikt. Zo wordt een 'pro'-profiel (bv. claude-sonnet-4-6 via
    OpenModel) echt gehonoreerd en niet stilzwijgend overschreven door het
    goedkope default-model. Bij geen profielmodel of geen cloud-sleutel → None."""
    if not profile_model:
        return None
    model = profile_model.strip()
    if model.startswith("openrouter/"):
        from ...shared.config import OPENROUTER_API_KEY
        return model[len("openrouter/"):] if OPENROUTER_API_KEY else None
    from ...shared.config import OPENMODEL_API_KEY
    return model if OPENMODEL_API_KEY else None


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
        purpose=f"conveyor:{agent_name}" if agent_name else "conveyor",
    ):
        if event.get("type") == "error":
            raise RuntimeError(event.get("message") or "Onbekende agent-fout")
        text = event.get("text") or ""
        chunks.append(text)

    return "".join(chunks).strip()


def _assess_output(text: str, task: dict) -> dict:
    """Minimale, LLM-vrije kwaliteitscheck op taak-output.

    Houdt rommel uit de downstream Wachtrij-gate: een te korte of structuurloze
    tekst wordt 'needs_work' in plaats van 'done'. Dit is een snelle
    voorfilter — de echte SEO-score (>=80) gebeurt in content_pipeline.

    Drempel wordt dynamisch verlaagd als de actieve backend lokaal/Ollama is
    (zie hermes_backend()): die leveren kortere responses, maar de content-
    pipeline-gate (80+) blijft de echte kwaliteitsbeoordeling.
    """
    from ...shared.config import hermes_backend
    _backend = hermes_backend()
    _is_local = _backend in ("local", "ollama")
    _min_len = 100 if _is_local else 200
    if not text or len(text.strip()) < _min_len:
        return {"ok": False, "reason": f"output te kort (<{_min_len} tekens)"}
    # Lijst-/artikel-taken horen koppen te hebben; een muur aan platte tekst
    # is onbruikbaar als SEO-concept.
    has_heading = any(line.strip().startswith("#") for line in text.splitlines())
    if not has_heading and (task.get("workspace_path") or "").endswith(
        ("listicle.md", "reddit.md", "video.md")
    ):
        return {"ok": False, "reason": "geen koppen/structuur in concept"}
    return {"ok": True, "reason": ""}


async def _auto_stage_ready_listicles() -> int:
    """Zodra een AEO-listicle-taak 'done' is, schuif hem zelfstandig door naar
    de publicatie-wachtrij (pending_review / needs_work). Sluit de AEO-loop
    zonder dat iemand op 'queue-listicle' hoeft te klikken. Geeft het aantal
    gestageerde jobs terug. Werkt zacht — een fout logt en telt niet mee."""
    from ..radar.service import get_service as radar_service
    from ..radar.models import _list_ready_converted_listicles

    staged = 0
    for sig_id, task in _list_ready_converted_listicles():
        try:
            await radar_service().queue_listicle(sig_id)
            staged += 1
        except Exception:
            logger.exception("Auto-stage van listicle voor signaal %s mislukt", sig_id)
    return staged


def _write_workspace_file(workspace_path: str, content: str) -> Path:
    full_path = DEFAULT_WORKSPACE_ROOT / workspace_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return full_path


def _sweep_stale_running(stale_hours: int = STALE_RUNNING_HOURS) -> int:
    """Zet 'running' taken ouder dan `stale_hours` terug naar 'todo'.

    Gedeelde logica tussen de conveyor-sweep (elke revive-ronde) en de
    handmatige /api/tasks/cleanup-stale. Gebruikt set_task_status zodat de
    keten-doorrol intact blijft (geen opvolger wordt gepromoveerd — die
    mag pas als de taak écht 'done' is). Retourneert het aantal geraakte taken.
    """
    note = (
        f"Stale 'running' gereset ({stale_hours}h+) — conveyor kwijt bij "
        "restart/crash; opnieuw starten of verwijderen."
    )
    swept = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status='running' "
            "AND (started_at IS NULL OR started_at < datetime('now', ?))",
            (f"-{stale_hours} hours",),
        ).fetchall()
        for r in rows:
            set_task_status(r["id"], "todo", error=note)
            swept += 1
    return swept


async def _execute_task(task: dict) -> dict:
    agent_name = task.get("agent") or "hermes"
    workspace_path = task.get("workspace_path") or ""
    task_id = task.get("id")

    # Budgetrem vóór elke uitvoering, niet alleen bij autonome jobs elders —
    # zonder dit zou een volle backlog vastgelopen taken (zie revive_stalled_chains)
    # het dagbudget in één ronde kunnen leegtrekken, precies het patroon uit het
    # Gauntlet-incident van 15 aug 2026 (6,2M tokens op één dag door een pad
    # zonder budgetguard). Terug naar 'todo' i.p.v. falen binnen de try-block,
    # zodat dit niet als een 'transient' crash meetelt in de retry-teller —
    # revive_stalled_chains classificeert de tekst zelf opnieuw als 'quota'.
    try:
        require_llm_budget("conveyor")
    except BudgetExceeded as exc:
        detail = str(exc)
        logger.info("Task %s uitgesteld: %s", task_id, detail)
        set_task_status(task_id, "todo", error=detail, finished_at=_now())
        return {"task_id": task_id, "status": "todo", "output": detail, "duration_ms": 0}

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
        # Eigen kwaliteitscheck: is de output bruikbaar? Minimale drempels
        # (lengte + structuur) houden rommel uit de downstream Wachtrij-gate.
        # Bij falen → 'needs_work' (de publicatie-gate weigert die toch <80).
        quality = _assess_output(result_text, task)
        if quality["ok"]:
            updated = set_task_status(
                task_id, "done",
                result=result_text, error="",
                finished_at=_now(), duration_ms=duration_ms,
            )
        else:
            updated = set_task_status(
                task_id, "needs_work",
                result=result_text, error=quality["reason"],
                finished_at=_now(), duration_ms=duration_ms,
            )

        logger.info(
            "Task %s finished: status=%s duration=%sms quality=%s",
            task_id,
            (updated or {}).get("status", "done"),
            duration_ms,
            "ok" if quality["ok"] else quality["reason"],
        )
        return {
            "task_id": task_id,
            "status": (updated or {}).get("status", "done"),
            "output": result_text,
            "duration_ms": duration_ms,
            "quality": quality,
        }
    except Exception as exc:  # noqa: BLE001
        # str(exc) is bij de hele httpx/anyio-familie vaak leeg (zie
        # shared/failures.py) — dan staat er "mislukt: " en weet niemand waar
        # te beginnen. describe_exception() vult 'm altijd.
        detail = describe_exception(exc)
        logger.exception("Task %s failed: %s", task_id, detail)
        duration_ms = int((time.perf_counter() - started) * 1000)
        # Terug naar 'todo' (niet 'ready') zodat de conveyor niet meteen in een
        # faal-lus komt; revive_stalled_chains() pakt 'm later weer op zodra de
        # faalklasse en de afkoelperiode dat toestaan (zie pipeline/service.py).
        updated = set_task_status(
            task_id, "todo",
            error=detail, finished_at=_now(), duration_ms=duration_ms,
        )
        return {
            "task_id": task_id,
            "status": (updated or {}).get("status", "todo"),
            "output": f"Fout tijdens uitvoering: {detail}",
            "duration_ms": duration_ms,
        }


async def conveyor_loop(
    poll_interval: float = 2.0,
    max_parallel: int = 5,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Segmented Batch Dispatch voor de conveyor.

    In plaats van taken strikt één-voor-één af te vuren, pakt de loop elke
    poll-ronde alle 'ready' taken en vuurt ze *parallel* af via asyncio.gather.

    Waarom dat veilig is: een taak wordt pas 'ready' zodra zijn keten-
    voorganger 'done' is (state-machine in set_task_status). Twee 'ready'
    taken horen dus altijd tot verschillende ketens en zijn volledig
    onafhankelijk — precies de "veilige" taken uit een segmented-dispatch.
    Afhankelijke (ketting-)stappen wachten automatisch tot hun voorganger klaar
    is, omdat ze pas daarna op 'ready' komen.

    `max_parallel` is een concurrency-cap: hooguit N taken tegelijk, zodat de
    LLM-gateway niet onder een grote fan-out bezwijkt. Onafhankelijke taken
    boven de cap wachten op de volgende poll-ronde.
    """
    logger.info(
        "Conveyor loop gestart (interval %.1fs, max_parallel=%d, states=%s)",
        poll_interval, max_parallel,
        ", ".join(STATE_TRANSITIONS),
    )
    if stop_event is None:
        stop_event = asyncio.Event()

    revive_interval = 30.0
    last_revive = 0.0

    while not stop_event.is_set():
        try:
            ready = get_ready_tasks(limit=max_parallel)
            if ready:
                logger.info(
                    "Batch-dispatch: %d onafhankelijke taak/taken parallel", len(ready)
                )
                # asyncio.gather vuurt alle taken tegelijk af. return_exceptions
                # zorgt dat één crashende taak de andere nooit blokkeert —
                # net als de delegate-fan-out. Fouten worden hierna per-taak
                # gelogd, niet herraise'd.
                results = await asyncio.gather(
                    *(_execute_task(task) for task in ready),
                    return_exceptions=True,
                )
                for task, res in zip(ready, results):
                    if isinstance(res, Exception):
                        logger.exception(
                            "Taak %s crashte in batch-dispatch: %s",
                            task.get("id"), res,
                        )
                    else:
                        logger.info(
                            "Taak %s -> %s in %sms",
                            task.get("id"),
                            res.get("status"),
                            res.get("duration_ms"),
                        )
                # Zelfstandige doorrol: als AEO-listicles klaar zijn, schuif ze
                # meteen door naar de Wachtrij-gate (menselijke publish-klik).
                try:
                    staged = await _auto_stage_ready_listicles()
                    if staged:
                        logger.info("Auto-stage: %d listicle(s) naar Wachtrij", staged)
                except Exception:  # noqa: BLE001
                    logger.exception("Auto-stage na batch mislukt (niet fataal)")
            else:
                await asyncio.sleep(poll_interval)

            # Vastgelopen ketens vlottrekken: gecrashte taken die hun
            # afkoelperiode hebben uitgezeten, en 'todo'-taken die alleen nooit
            # gepromoveerd werden. Debounced (elke 30s, niet elke poll) — de
            # scan loopt over de hele tasks-tabel en hoeft niet elke 2s.
            now_mono = time.monotonic()
            if now_mono - last_revive >= revive_interval:
                last_revive = now_mono
                try:
                    stats = revive_stalled_chains()
                    if stats.get("promoted_new") or stats.get("promoted_retry"):
                        logger.info(
                            "Revive: %d nieuw ontgrendeld, %d herprobeerd, %d wacht op een mens",
                            stats.get("promoted_new", 0), stats.get("promoted_retry", 0),
                            stats.get("blocked_human", 0),
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("Revive van vastgelopen ketens mislukt (niet fataal)")
                # Zombie-sweep: 'running' taken die al urenlang nergens meer
                # door de conveyor worden aangeraakt, terugzetten naar 'todo'.
                # De conveyor pakt alléén 'ready' op, dus een taak die op
                # 'running' vastzit (crash zonder terugschrijven, of een taak
                # die in een except bleef hangen) blokkeert zijn hele keten voor
                # altijd — tenzij iemand handmatig /api/tasks/cleanup-stale
                # aanriep. Deze sweep doet dat automatisch, debounced, net als
                # de revive-stap. Idempotent: na de reset pakt
                # revive_stalled_chains() de foutklasse weer op.
                try:
                    swept = _sweep_stale_running(stale_hours=STALE_RUNNING_HOURS)
                    if swept:
                        logger.warning(
                            "Stale-sweep: %d 'running' taak/taken (>%dh) teruggezet naar 'todo'",
                            swept, STALE_RUNNING_HOURS,
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("Stale-sweep mislukt (niet fataal)")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fout in conveyor loop: %s", exc)
            await asyncio.sleep(poll_interval)

    logger.info("Conveyor loop gestopt")


run_conveyor = conveyor_loop

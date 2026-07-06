"""
Loop Service — Loop Engineering: maker-agent + beoordelaar-agent in een lus.

Het idee (Hermes 0.17 "Loop Engineering"):
  1. Een MAKER produceert een concept voor de opdracht.
  2. Een BEOORDELAAR scoort dat concept 0–100 tegen de opdracht en geeft
     concrete, bruikbare feedback (strikte JSON).
  3. Haalt de score de drempel niet, dan herschrijft de maker het concept mét de
     feedback als input. Dit herhaalt tot de drempel gehaald is (verdict=pass) of
     het max aantal iteraties op is.

Verschil met de buren:
  * conveyor_loop = sequentiële assembly line (verschillende stappen achter elkaar).
  * delegate_service = parallelle fan-out (N onafhankelijke workers tegelijk).
  * loop_service = ITERATIEVE verbeterlus (dezelfde opdracht, steeds beter).

Net als de delegate-laag draait een lus als losse achtergrond-asyncio-task en
stroomt elke ronde live terug via de event_bus naar de UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...shared.config import hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...domains.delegate import event_bus
from ...domains.pipeline.service import get_agent_profile

logger = logging.getLogger(__name__)

# Sterke referenties naar lopende achtergrond-tasks (zoals delegate_service),
# anders kan de GC een create_task()'d coroutine opruimen vóór die klaar is.
_BG_TASKS: "set[asyncio.Task]" = set()

DEFAULT_THRESHOLD = 85
DEFAULT_MAX_ITERATIONS = 4

DEFAULT_MAKER_PROMPT = (
    "Je bent een topcopywriter/maker in een kwaliteitslus. Je levert een direct "
    "bruikbaar, self-contained eindproduct in Markdown — geen meta-uitleg vooraf "
    "of achteraf, geen vragen terug. Krijg je feedback van een beoordelaar, verwerk "
    "die dan punt voor punt en lever een merkbaar betere versie. Schrijf in het "
    "Nederlands tenzij anders gevraagd."
)

DEFAULT_REVIEWER_PROMPT = (
    "Je bent een strenge, eerlijke kwaliteitsbeoordelaar. Je beoordeelt een concept "
    "tegen de opdracht op inhoud, structuur, toon, correctheid en bruikbaarheid. "
    "Je bent kritisch: een hoge score verdien je, je geeft hem niet weg. Je feedback "
    "is concreet en uitvoerbaar (wat moet beter en hoe), geen algemeenheden."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Vertaal een profiel-model naar een waarde die de actieve backend snapt.
    (Zelfde logica als conveyor/delegate: alleen OpenRouter krijgt een override.)"""
    if not profile_model:
        return None
    model = profile_model.strip()
    if hermes_backend() == "openrouter":
        return model[len("openrouter/"):] if model.startswith("openrouter/") else model
    return None


def _extract_json(raw: str) -> str:
    """Pak het eerste JSON-object uit een LLM-antwoord, ook in ```-fences."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s.strip()


# ── Persistence ──────────────────────────────────────────────────────────────

def _create_loop(
    objective: str,
    session_id: Optional[str],
    maker_profile_id: Optional[int],
    reviewer_profile_id: Optional[int],
    threshold: int,
    max_iterations: int,
) -> str:
    loop_id = f"loop-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO loops
               (id, objective, session_id, maker_profile_id, reviewer_profile_id,
                threshold, max_iterations, status, best_score, best_output,
                iterations_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running', -1, '', 0, ?, ?)""",
            (loop_id, objective, session_id or "", maker_profile_id, reviewer_profile_id,
             threshold, max_iterations, now, now),
        )
    return loop_id


def _record_iteration(
    loop_id: str, iteration: int, draft: str, score: int,
    feedback: str, passed: bool, duration_ms: int,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO loop_iterations
               (loop_id, iteration, draft, score, feedback, passed, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (loop_id, iteration, draft, score, feedback, 1 if passed else 0, duration_ms, _now()),
        )


def _update_loop(loop_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE loops SET {set_clause} WHERE id = ?",
            list(fields.values()) + [loop_id],
        )


# ── Agent-aanroepen ──────────────────────────────────────────────────────────

async def _run_text_agent(system_prompt: str, user_message: str, model_override: Optional[str]) -> str:
    """Draai een agent zonder tools en verzamel de tekst (content-taak)."""
    chunks: List[str] = []
    async for event in agent_service.run_agent(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        agent="hermes",
        model_override=model_override,
        use_tools=False,
    ):
        if event.get("type") == "error":
            raise RuntimeError(event.get("message") or "Onbekende agent-fout")
        if event.get("type") == "text":
            chunks.append(event["text"])
    return "".join(chunks).strip()


async def _run_maker(
    objective: str, profile: Optional[dict], model_override: Optional[str],
    prior_draft: Optional[str], feedback: Optional[str],
) -> str:
    system_prompt = (profile.get("system_prompt") if profile else "") or DEFAULT_MAKER_PROMPT
    user_message = f"# Opdracht\n{objective}\n"
    if prior_draft and feedback:
        user_message += (
            "\n# Je vorige versie\n" + prior_draft +
            "\n\n# Feedback van de beoordelaar (verwerk dit punt voor punt)\n" + feedback +
            "\n\nLever nu een merkbaar betere, volledige nieuwe versie."
        )
    draft = await _run_text_agent(system_prompt, user_message, model_override)
    return draft or "_(De maker leverde geen tekst op.)_"


async def _run_reviewer(
    objective: str, draft: str, profile: Optional[dict],
    model_override: Optional[str], threshold: int,
) -> Tuple[int, str, bool]:
    """Laat de beoordelaar scoren. Retourneert (score 0-100, feedback, passed)."""
    base_prompt = (profile.get("system_prompt") if profile else "") or DEFAULT_REVIEWER_PROMPT
    system_prompt = base_prompt + (
        "\n\nANTWOORD UITSLUITEND met één JSON-object, zonder markdown, zonder uitleg eromheen:\n"
        '{"score": <geheel getal 0-100>, "verdict": "pass" | "revise", "feedback": "<concrete, uitvoerbare feedback>"}\n'
        f"Geef verdict 'pass' alleen als de kwaliteit minstens {threshold}/100 is."
    )
    user_message = (
        f"# Opdracht\n{objective}\n\n"
        f"# Te beoordelen concept\n{draft}\n\n"
        f"Beoordeel dit concept. Drempel om te slagen: {threshold}/100."
    )
    raw = await _run_text_agent(system_prompt, user_message, model_override)
    return _parse_review(raw, threshold)


def _parse_review(raw: str, threshold: int) -> Tuple[int, str, bool]:
    """Robuust de score/feedback/verdict uit de beoordelaar-output halen.

    Faalt het JSON-parsen, dan behandelen we het als 'nog niet geslaagd' met de
    ruwe output als feedback — zo blijft de lus doorlopen i.p.v. te crashen.
    """
    try:
        obj = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return 0, f"(Beoordelaar gaf geen geldige JSON-score.) Ruwe output:\n{raw[:1500]}", False

    try:
        score = int(round(float(obj.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    feedback = str(obj.get("feedback") or "").strip()
    verdict = str(obj.get("verdict") or "").strip().lower()
    # 'passed' = expliciet verdict pass OF score haalt de drempel.
    passed = verdict == "pass" or score >= threshold
    return score, feedback, passed


# ── De lus zelf ──────────────────────────────────────────────────────────────

async def _run_loop(
    loop_id: str, objective: str,
    maker_profile_id: Optional[int], reviewer_profile_id: Optional[int],
    threshold: int, max_iterations: int,
) -> None:
    maker_profile = get_agent_profile(maker_profile_id)
    reviewer_profile = get_agent_profile(reviewer_profile_id)
    maker_override = _resolve_model_override(maker_profile.get("model") if maker_profile else None)
    reviewer_override = _resolve_model_override(reviewer_profile.get("model") if reviewer_profile else None)

    event_bus.publish({
        "type": "loop_start", "loop_id": loop_id, "objective": objective,
        "threshold": threshold, "max_iterations": max_iterations,
        "maker": (maker_profile or {}).get("name") or "Maker (default)",
        "reviewer": (reviewer_profile or {}).get("name") or "Beoordelaar (default)",
    })

    best_score = -1
    best_output = ""
    prior_draft: Optional[str] = None
    feedback: Optional[str] = None
    final_status = "stopped"

    try:
        for i in range(1, max_iterations + 1):
            event_bus.publish({
                "type": "loop_iteration_start", "loop_id": loop_id,
                "iteration": i, "max_iterations": max_iterations,
            })
            started = time.perf_counter()

            draft = await _run_maker(objective, maker_profile, maker_override, prior_draft, feedback)
            score, fb, passed = await _run_reviewer(
                objective, draft, reviewer_profile, reviewer_override, threshold
            )
            duration_ms = int((time.perf_counter() - started) * 1000)

            _record_iteration(loop_id, i, draft, score, fb, passed, duration_ms)
            if score > best_score:
                best_score = score
                best_output = draft
            _update_loop(loop_id, iterations_run=i, best_score=best_score, best_output=best_output)

            event_bus.publish({
                "type": "loop_iteration", "loop_id": loop_id, "iteration": i,
                "max_iterations": max_iterations, "score": score, "threshold": threshold,
                "passed": passed, "feedback": fb, "draft": draft, "duration_ms": duration_ms,
            })
            logger.info("Loop %s ronde %s/%s: score=%s passed=%s",
                        loop_id, i, max_iterations, score, passed)

            if passed:
                final_status = "passed"
                break

            prior_draft = draft
            feedback = fb

        else:
            final_status = "stopped"  # max_iterations bereikt zonder pass

        _update_loop(loop_id, status=final_status, finished_at=_now())
        event_bus.publish({
            "type": "loop_done", "loop_id": loop_id, "status": final_status,
            "best_score": best_score, "threshold": threshold,
            "iterations_run": min(i, max_iterations), "best_output": best_output,
        })
        logger.info("Loop %s afgerond: status=%s best_score=%s", loop_id, final_status, best_score)

    except Exception as exc:  # noqa: BLE001 — een crash mag de server niet slopen
        _update_loop(loop_id, status="failed", finished_at=_now())
        event_bus.publish({
            "type": "loop_error", "loop_id": loop_id,
            "error": str(exc), "best_score": best_score,
        })
        logger.exception("Loop %s faalde: %s", loop_id, exc)


# ── Publieke API ─────────────────────────────────────────────────────────────

def spawn_loop(
    objective: str,
    maker_profile_id: Optional[int] = None,
    reviewer_profile_id: Optional[int] = None,
    threshold: int = DEFAULT_THRESHOLD,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Start een kwaliteitslus en KEER DIRECT TERUG (non-blocking).

    Net als spawn_delegation: persisteer synchroon (snel) en draai de lus als
    losse achtergrond-task die live naar de event_bus stroomt.
    """
    if not objective or not objective.strip():
        raise ValueError("Een opdracht (objective) is verplicht voor een lus.")
    threshold = max(1, min(100, int(threshold)))
    max_iterations = max(1, min(10, int(max_iterations)))

    loop_id = _create_loop(
        objective.strip(), session_id, maker_profile_id, reviewer_profile_id,
        threshold, max_iterations,
    )

    task = asyncio.create_task(_run_loop(
        loop_id, objective.strip(), maker_profile_id, reviewer_profile_id,
        threshold, max_iterations,
    ))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

    return {
        "loop_id": loop_id,
        "threshold": threshold,
        "max_iterations": max_iterations,
    }


def find_profile_id(name: str) -> Optional[int]:
    """Zoek een agent-profiel-id op naam (voor presets/integraties zoals outreach)."""
    if not name:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM agent_profiles WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def get_loop(loop_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        loop = conn.execute("SELECT * FROM loops WHERE id = ?", (loop_id,)).fetchone()
        if not loop:
            return None
        iters = conn.execute(
            "SELECT * FROM loop_iterations WHERE loop_id = ? ORDER BY iteration ASC", (loop_id,)
        ).fetchall()
    out = dict(loop)
    out["iterations"] = [dict(r) for r in iters]
    return out


def list_loops(limit: int = 25) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, objective, status, threshold, max_iterations, best_score, "
            "iterations_run, created_at, finished_at "
            "FROM loops ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

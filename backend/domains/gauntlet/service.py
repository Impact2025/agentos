"""
Gauntlet Loop — AgentOS-implementatie van het "Gauntlet Loop"-patroon.

Dit is de orchestrator die AgentOS' bestaande Loop Engineering (maker/beoordelaar)
en Delegate-laag (parallelle subagents) KOMBINEERT tot de 3-pijler-Gauntlet uit de
video van Matt Schumer / Julian Goldie:

  Pijler 1 — Taaksplitsing & parallelle subagents
      Een Lead Agent (hier: een decompose-stap via de Hermes-agent) splitst de
      opdracht op in deeltaken; elke deeltaak krijgt een eigen gespecialiseerde
      builder-subagent die tegelijk met de anderen draait (asyncio.gather).

  Pijler 2 — Blinde, onvermoeibare critici per subagent
      Elke builder krijgt een eigen criticus. De criticus ziet ALLEEN de output
      (het eindproduct) en de benchmark — nooit het bouwproces of de builder-prompt.
      Dat breekt de "AI geeft zichzelf altijd een 10"-self-bias: de criticus meet
      blind tegen de echte benchmark en levert een harde score 0–100 + verdict.

  Pijler 3 — Echte benchmarks zonder ingebouwde stopconditie
      De opdrachtgever levert een scherpe BENCHMARK (referentie-artifact of
      -omschrijving). De criticus toetst de output er hard tegenaan. De loop blijft
      draaien (builder herschrijft met feedback) totdat de deeltaak de benchmark
      doorstaat of max_iterations bereikt is.

Stopconditie / menselijke eindjurat (de waarschuwing uit de video):
  Een Gauntlet stopt NOOIT vanzelf als de benchmark onhaalbaar scherp is. Daarom
  heeft elke run een harde max_iterations-per-deeltaak én een expliciete STOP-
  knop (POST /api/gauntlet/{id}/stop). Na afloop beoordeelt een MENS het overgebleven
  resultaat als eindjurat (POST /api/gauntlet/{id}/verdict). De UI toont een
  "Stop" + "Beoordeel als mens"-knop.

Architectuur:
  - Draait als losse achtergrond-asyncio-task (net als loop/delegate) → non-blocking.
  - Stroomt elke gebeurtenis live terug via de gedeelde event_bus (gauntlet_*-events).
  - Hergebruikt agent_runner.run_agent, event_bus, en de agent_profiles-tabel.
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
from ...domains.publish.content_pipeline import create_job as _create_content_job  # noqa: E402
from . import brand_brief

logger = logging.getLogger(__name__)


class BillingError(RuntimeError):
    """OpenModel (of andere backend) meldt onvoldoende saldo (HTTP 402).

    Een Gauntlet-run die hierop stuit, wordt netjes als 'failed_billing'
    gemarkeerd i.p.v. 5 deeltaken op 'error' te laten eindigen — zodat de
    gebruiker direct ziet: laad je OpenModel-saldo op, niet 'systeem bug'.
    """

# Sterke referenties naar lopende achtergrond-tasks (anders ruimt GC de task op).
_BG_TASKS: "set[asyncio.Task]" = set()

DEFAULT_THRESHOLD = 85
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_STOP = False  # zachte default; de mens bepaalt wanneer het stopt

# System-prompt voor de Lead/Decompose-stap: splitst de opdracht in deeltaken.
_DECOMPOSE_PROMPT = (
    "Je bent de Lead Agent van een Gauntlet Loop. Je krijgt één overkoepelende "
    "opdracht en je splitst die op in 2–6 scherp omkaderde, onafhankelijke deeltaken "
    "die parallel door gespecialiseerde builders kunnen worden uitgevoerd. "
    "Elke deeltaak moet self-contained zijn (een andere builder kan 'm zonder context "
    "van de andere doen). Geen overlap, geen vage deeltaken.\n\n"
    "SPLITSRICHTLIJNEN:\n"
    "- Is de opdracht één samenhangend eindproduct (bv. een landingspagina, een artikel, "
    "een rapport)? Splits dan op IN DE STRUCTURELE ONDERDELEN die elk een waarneembaar, "
    "los beoordeelbaar deel zijn. Voor een landingspagina: Hero/intro, Diensten/aanbod, "
    "Bewijs/resultaten, Projecten/cases, CTA/sluiting — elk als eigen deeltaak.\n"
    "- Bevat de opdracht expliciet 'en', 'plus', of meerdere verschillende assets? Splits "
    "die dan op per asset.\n"
    "- Liever 3–5 gerichte deeltaken dan 1 vage 'Hoofdtaak'. Eén deeltaak is alleen oké "
    "als het product écht niet te ontleden valt in losse componenten.\n\n"
    "ANTWOORD UITSLUITEND met één JSON-object, zonder markdown eromheen:\n"
    '{"subtasks": [{"role": "<korte specialist-rol, bv. \'Hero-schrijver\'>", '
    '"goal": "<een strakke, concrete opdracht voor deze builder>"}, ...]}'
)

MAKER_DEFAULT = (
    "Je bent een gespecialiseerde builder in een Gauntlet Loop. Je levert een direct "
    "bruikbaar, self-contained eindproduct in Markdown — geen meta-uitleg, geen vragen "
    "terug. Krijg je feedback van een blinde criticus, verwerk die dan punt voor punt en "
    "lever een merkbaar betere versie."
)

CRITIC_DEFAULT = (
    "Je bent een BLINDE, onvermoeibare criticus in een Gauntlet Loop. Je ziet ALLEEN het "
    "eindproduct van de builder en de benchmark — nooit het bouwproces. Je meet het product "
    "hard tegen de benchmark en geeft een eerlijke score 0–100. Je bent meedogenloos: een 10 "
    "verdien je niet, je geeft 'm niet weg. Je feedback is concreet en uitvoerbaar."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Profielmodel → bare model-string die de cloud-gateway snapt (zie loop/delegate)."""
    if not profile_model:
        return None
    model = profile_model.strip()
    if model.startswith("openrouter/"):
        from ...shared.config import OPENROUTER_API_KEY
        return model[len("openrouter/"):] if OPENROUTER_API_KEY else None
    from ...shared.config import OPENMODEL_API_KEY
    return model if OPENMODEL_API_KEY else None


def _extract_json(raw: str) -> str:
    """Pak het eerste JSON-object uit een LLM-antwoord, ook in fences."""
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

def _create_run(
    objective: str,
    benchmark: str,
    session_id: Optional[str],
    threshold: int,
    max_iterations: int,
) -> str:
    run_id = f"gaunt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO gauntlet_runs
               (id, objective, benchmark, session_id, status, threshold, max_iterations,
                subtask_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?, 0, ?, ?)""",
            (run_id, objective, benchmark, session_id or "", threshold, max_iterations, now, now),
        )
    return run_id


def _create_subtask(run_id: str, position: int, role: str, goal: str) -> str:
    st_id = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO gauntlet_subtasks
               (id, run_id, position, role, goal, status, best_score, best_output,
                iterations_run, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'queued', -1, '', 0, ?, ?)""",
            (st_id, run_id, position, role, goal, now, now),
        )
    return st_id


def _record_subtask_iteration(
    st_id: str, iteration: int, draft: str, score: int,
    feedback: str, passed: bool, duration_ms: int,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO gauntlet_iterations
               (subtask_id, iteration, draft, score, feedback, passed, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (st_id, iteration, draft, score, feedback, 1 if passed else 0, duration_ms, _now()),
        )


def _update_run(run_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE gauntlet_runs SET {set_clause} WHERE id = ?",
            list(fields.values()) + [run_id],
        )


def _update_subtask(st_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE gauntlet_subtasks SET {set_clause} WHERE id = ?",
            list(fields.values()) + [st_id],
        )


# ── Agent-aanroepen ──────────────────────────────────────────────────────────

async def _run_text_agent(
    system_prompt: str, user_message: str, model_override: Optional[str],
    max_tokens: int = 4096, call_timeout: float = 90.0,
) -> str:
    """Draai een agent zonder tools en verzamel tekst (content-taak).

    `call_timeout` voorkomt dat één trage OpenModel-call de hele Gauntlet-run
    blokkeert: bij timeout wordt RuntimeError opgegooid (en door de caller
    geretry'd of als 'error' gemarkeerd i.p.v. de run te laten hangen).
    """
    chunks: List[str] = []
    try:
        async with asyncio.timeout(call_timeout):
            async for event in agent_service.run_agent(
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                agent="hermes",
                model_override=model_override,
                use_tools=False,  # content-taken: zwakke modellen lekken anders tool-syntax
                max_tokens=max_tokens,
            ):
                if event.get("type") == "error":
                    msg = event.get("message") or "Onbekende agent-fout"
                    if "402" in msg or "insufficient balance" in msg or "billing" in msg.lower():
                        raise BillingError(msg)
                    raise RuntimeError(msg)
                if event.get("type") == "text":
                    chunks.append(event["text"])
    except asyncio.TimeoutError:
        raise RuntimeError(f"LLM-call time-out na {call_timeout:.0f}s (OpenModel traag/leeg)")
    return "".join(chunks).strip()


async def _run_text_agent_retry(
    system_prompt: str, user_message: str, model_override: Optional[str],
    max_tokens: int = 4096, max_attempts: int = 3, call_timeout: float = 90.0,
) -> str:
    """_run_text_agent met retry bij tijdelijke backend-haps (gateway :8899 flaky).

    De LLM-gateway (OpenModel via :8899) is periodiek onbereikbaar (HTTP 000 /
    verbinding geweigerd). Eén such hap mag een hele Gauntlet-deeltaak niet doden —
    we proberen het tot 3x met een korte pauze, en pas daarna paseren we de fout
    door zodat de deeltaak op 'error' belandt (ipv stil te crashen).
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await _run_text_agent(
                system_prompt, user_message, model_override, max_tokens, call_timeout,
            )
        except BillingError:
            raise  # saldo-op: nooit retry'en, direct naar de run-loop
        except RuntimeError as exc:
            last_err = exc
            logger.warning(
                "Gauntlet agent-call poging %d/%d mislukt: %s", attempt, max_attempts, exc
            )
            if attempt < max_attempts:
                await asyncio.sleep(3 * attempt)  # korte, oplopende pauze
    assert last_err is not None
    raise last_err


async def _decompose(objective: str, model_override: Optional[str]) -> List[Dict[str, str]]:
    """Lead Agent splitst de opdracht op in deeltaken. Fallback bij faal = 1 deeltaak.

    Vangt ook agent-fouten (gateway down, BillingError bij leeg OpenModel-saldo)
    zodat de run niet hard crasht met lege subtasks — er blijft dan één Hoofdtaak
    over die de gebruiker alsnog kan laten lopen (en die zelf de fout netjes meldt).
    """
    try:
        raw = await _run_text_agent(_DECOMPOSE_PROMPT, f"# Opdracht\n{objective}", model_override)
        obj = json.loads(_extract_json(raw))
        subs = obj.get("subtasks") or []
        cleaned = [
            {"role": str(s.get("role") or f"deeltaak {i+1}").strip(),
             "goal": str(s.get("goal") or "").strip()}
            for i, s in enumerate(subs)
            if str(s.get("goal") or "").strip()
        ]
        if cleaned:
            return cleaned
    except (json.JSONDecodeError, ValueError, AttributeError):
        logger.warning("Decompose gaf geen geldige JSON; val terug op 1 deeltaak.")
    except (RuntimeError, BillingError) as exc:
        logger.warning("Decompose agent-call faalde (%s); val terug op 1 deeltaak.", exc)
    return [{"role": "Hoofdtaak", "goal": objective}]


async def _run_builder(
    goal: str, brand_brief: str, model_override: Optional[str],
    prior_draft: Optional[str], feedback: Optional[str],
) -> str:
    system_prompt = MAKER_DEFAULT
    if brand_brief:
        system_prompt += "\n\n" + brand_brief
    user_message = f"# Jouw deeltaak\n{goal}\n"
    if prior_draft and feedback:
        user_message += (
            "\n# Je vorige versie\n" + prior_draft +
            "\n\n# Feedback van de blinde criticus (verwerk dit punt voor punt)\n" + feedback +
            "\n\nLever nu een merkbaar betere, VOLLEDIGE nieuwe versie (de hele pagina, " +
            "eindigend met de sluiting en beide CTA's). Breek niet af."
        )
    # Bij lege output: 1 retry (geen 3× bellen — verbrandt saldo op een model dat
    # toch niks geeft bij dit deeltaak-type). De caller (de lus) gaat dan naar de
    # volgende ronde met feedback i.p.v. de run te laten hangen.
    for attempt in range(1):
        draft = await _run_text_agent_retry(
            system_prompt, user_message, model_override, max_tokens=8192, call_timeout=90.0,
        )
        if draft and draft.strip() and "_(De builder leverde geen tekst op.)_" not in draft:
            return draft
        logger.warning("Builder leverde lege output (poging %d); ga naar volgende ronde.", attempt + 1)
    # Lege output en er is een eerdere versie: behoud die (beter dan een lege
    # placeholder). Voorkomt dat een deeltaak compleet leeg wordt als de builder in
    # een latere ronde hapert op lange context (OpenModel geeft dan lege body).
    if prior_draft and prior_draft.strip():
        return prior_draft
    return "_(De builder leverde geen tekst op.)_"


# Wereldklasse-default: gebruik het 'smart' model i.p.v. de goedkope flash,
# zodat de Gauntlet geen sub-prime content produceert. Kan per-run worden
# overschreven via model_override in de API-aanroep.
def _default_model() -> Optional[str]:
    try:
        from ...shared.config import OPENMODEL_SMART_MODEL
        return OPENMODEL_SMART_MODEL or None
    except Exception:  # noqa: BLE001
        return None


def _parse_critic(raw: str, threshold: int) -> Tuple[int, str, bool, bool]:
    """Robuuste parse van de blinde criticus: score/feedback/verdict.

    Returns (score, feedback, passed, ok). `ok` is False als de criticus geen
    geldige JSON leverde — de caller kan dan retry'en i.p.v. de score op 0 te
    zetten (wat een goede run zou laten stranden op een parser-fout).
    """
    try:
        obj = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        # Fallback: zoek het eerste {...} blok (ook zonder fences / met prose
        # eromheen). deepseek-v4-flash schrijft soms een intro-regel vóór de JSON.
        import re
        m = re.search(r"\{[^{}]*\"score\"[^{}]*\}", raw, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return 0, "(Criticus gaf geen geldige JSON.)", False, False
        else:
            return 0, "(Criticus gaf geen geldige JSON.)", False, False
    try:
        score = int(round(float(obj.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    feedback = str(obj.get("feedback") or "").strip()
    verdict = str(obj.get("verdict") or "").strip().lower()
    passed = verdict == "pass" or score >= threshold
    return score, feedback, passed, True


async def _run_critic(
    goal: str, draft: str, benchmark: str, model_override: Optional[str], threshold: int,
    retries: int = 2,
) -> Tuple[int, str, bool, bool]:
    """BLINDE criticus: ziet alleen output + benchmark + deeltaak, nooit het bouwproces.

    Bij een mislukte parse (geen geldige JSON) retry't hij `retries` keer.
    Returns (score, feedback, passed, ok). Als ook na retry geen geldige JSON
    komt, is ok=False en houdt de caller de vorige beste versie aan.
    """
    system_prompt = CRITIC_DEFAULT + (
        "\n\nANTWOORD UITSLUITEND met één JSON-object, zonder markdown eromheen:\n"
        '{"score": <geheel getal 0-100>, "verdict": "pass" | "revise", '
        '"feedback": "<concrete, uitvoerbare feedback>"}\n'
        "Je MAG geen enkele andere tekst schrijven vóór of ná de JSON. "
        "Geen uitleg, geen inleiding. Alleen het JSON-object.\n"
        f"Geef verdict 'pass' alleen als het product minstens {threshold}/100 haalt tegen de benchmark."
    )
    user_message = (
        f"# Deeltaak (waar het product aan moet voldoen)\n{goal}\n\n"
        f"# BENCHMARK (waar het product hard tegenaan gemeten wordt)\n{benchmark}\n\n"
        f"# Het eindproduct van de builder (DIT alleen beoordeel je)\n{draft}\n\n"
        f"Beoordeel het product blind tegen de benchmark. Drempel: {threshold}/100."
    )
    last_raw = ""
    for _ in range(retries + 1):
        try:
            last_raw = await _run_text_agent_retry(
                system_prompt, user_message, model_override, max_tokens=4096, call_timeout=90.0
            )
        except BillingError:
            raise  # saldo-op: niet retry'en, meteen naar de run-loop
        except RuntimeError as exc:
            logger.warning("Criticus-call mislukt: %s", exc)
            continue  # timeout/haper: probeer opnieuw (binnen retries) i.p.v. te stranden
        score, fb, passed, ok = _parse_critic(last_raw, threshold)
        if ok:
            return score, fb, passed, True
    # Ook na retry geen geldige JSON: behoud vorige versie (geen score-0-straf)
    return best_score_keep, "(Criticus leverde geen parseerbare beoordeling; vorige versie behouden.)", False, False


# Sentinel zodat _run_critic weet dat er nog geen 'beste' versie is
best_score_keep = 0


# ── Eén deeltaak: builder + blinde criticus in een lus ───────────────────────

async def _run_subtask(
    run_id: str, st_id: str, position: int, role: str, goal: str,
    benchmark: str, brand_brief: str, threshold: int, max_iterations: int,
    stop_flag: Dict[str, bool], model_override: Optional[str],
) -> None:
    event_bus.publish({
        "type": "gauntlet_subtask_start", "run_id": run_id, "subtask_id": st_id,
        "position": position, "role": role, "goal": goal,
    })
    best_score = -1
    best_output = ""
    prior_draft: Optional[str] = None
    feedback: Optional[str] = None
    final_status = "stopped"

    try:
        for i in range(1, max_iterations + 1):
            if stop_flag.get("stop"):
                final_status = "stopped_by_user"
                break
            started = time.perf_counter()
            draft = await _run_builder(goal, brand_brief, model_override, prior_draft, feedback)
            score, fb, passed, ok = await _run_critic(
                goal, draft, benchmark, model_override, threshold
            )
            if not ok:
                # Criticus faalde (geen parseerbare JSON) — de builder leverde
                # WÉL een draft, dus bewaar die als beste versie en ga door naar
                # de volgende ronde i.p.v. de run te laten stranden op lege output.
                logger.warning(
                    "Criticus-parse faalde voor %s ronde %d; draft behouden, doorgaan.",
                    role, i,
                )
                if len(draft) > len(best_output):
                    best_score = max(best_score, 0)  # onbeoordeeld, niet -1
                    best_output = draft
                _record_subtask_iteration(
                    st_id, i, draft, best_score if best_score >= 0 else 0,
                    fb, False, int((time.perf_counter() - started) * 1000),
                )
                # Schrijf de draft naar de DB zodat hij niet verloren gaat bij een
                # criticus-faal (zonder dit blijft de deeltaak op 'queued' met lege output).
                _update_subtask(st_id, iterations_run=i, best_score=max(best_score, 0), best_output=best_output)
                event_bus.publish({
                    "type": "gauntlet_subtask_iteration", "run_id": run_id,
                    "subtask_id": st_id, "position": position, "role": role,
                    "iteration": i, "max_iterations": max_iterations,
                    "score": best_score if best_score >= 0 else 0,
                    "threshold": threshold, "passed": False, "feedback": fb,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                })
                prior_draft = draft
                feedback = fb
                continue
            duration_ms = int((time.perf_counter() - started) * 1000)
            _record_subtask_iteration(st_id, i, draft, score, fb, passed, duration_ms)
            if score > best_score:
                best_score = score
                best_output = draft
            _update_subtask(st_id, iterations_run=i, best_score=best_score, best_output=best_output)

            event_bus.publish({
                "type": "gauntlet_subtask_iteration", "run_id": run_id, "subtask_id": st_id,
                "position": position, "role": role, "iteration": i,
                "max_iterations": max_iterations, "score": score, "threshold": threshold,
                "passed": passed, "feedback": fb, "duration_ms": duration_ms,
            })
            if passed:
                final_status = "passed"
                break
            prior_draft = draft
            feedback = fb
        else:
            final_status = "stopped"  # max_iterations bereikt zonder pass

        _update_subtask(st_id, status=final_status)
        event_bus.publish({
            "type": "gauntlet_subtask_done", "run_id": run_id, "subtask_id": st_id,
            "position": position, "role": role, "status": final_status,
            "best_score": best_score, "threshold": threshold,
        })
    except Exception as exc:  # noqa: BLE001
        _update_subtask(st_id, status="error")
        event_bus.publish({
            "type": "gauntlet_subtask_error", "run_id": run_id, "subtask_id": st_id,
            "position": position, "role": role, "error": str(exc),
        })
        logger.exception("Gauntlet deeltaak %s (%s) faalde: %s", st_id, role, exc)


# ── De volledige Gauntlet run ──────────────────────────────────────────────────

async def _run_gauntlet(
    run_id: str, objective: str, benchmark: str, threshold: int, max_iterations: int,
    session_id: Optional[str], stop_flag: Dict[str, bool],
    model_override: Optional[str] = None,
) -> None:
    # Wereldklasse-default: gebruik het smart-model (dieper, betere content)
    # tenzij de caller een specifiek model meegeeft via de API.
    model_override = model_override or _default_model()
    # Gedeelde merk-brief uit de vault (Vincent's Schrijf-DNA) — één keer per run
    # opgehaald en naar elke builder gestuurd zodat alle deeltaken in dezelfde
    # stem schrijven.
    brand_brief_txt = brand_brief.get_brand_brief()

    event_bus.publish({
        "type": "gauntlet_start", "run_id": run_id, "objective": objective,
        "benchmark_len": len(benchmark), "threshold": threshold,
        "max_iterations": max_iterations,
    })

    try:
        # Pijler 1: decompose → parallelle subagents
        subtasks = await _decompose(objective, model_override)
        _update_run(run_id, subtask_count=len(subtasks))
        st_rows = [
            {"id": _create_subtask(run_id, idx, s["role"], s["goal"]),
             "position": idx, "role": s["role"], "goal": s["goal"]}
            for idx, s in enumerate(subtasks)
        ]
        event_bus.publish({
            "type": "gauntlet_plan", "run_id": run_id,
            "subtasks": [{"position": r["position"], "role": r["role"], "goal": r["goal"]}
                         for r in st_rows],
        })

        # Pijler 1+2+3: elke deeltaak draait zijn eigen builder+blinde-critic-lus,
        # allemaal tegelijk (parallel fan-out zoals de delegate-laag).
        results = await asyncio.gather(*(
            _run_subtask(
                run_id, r["id"], r["position"], r["role"], r["goal"],
                benchmark, brand_brief_txt, threshold, max_iterations, stop_flag, model_override,
            )
            for r in st_rows
        ), return_exceptions=True)
        # Billing-fout? Markeer de hele run als failed_billing (niet 5× error).
        if any(isinstance(r, BillingError) for r in results):
            _update_run(run_id, status="failed_billing", finished_at=_now())
            event_bus.publish({
                "type": "gauntlet_error", "run_id": run_id,
                "error": "OpenModel-saldo op (HTTP 402). Laad je saldo op en start de run opnieuw.",
            })
            logger.error("Gauntlet %s: OpenModel billing-fout — run gestopt.", run_id)
            return

        # Status bepalen: passed = alle deeltaken geslaagd; anders partial.
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT status FROM gauntlet_subtasks WHERE run_id = ?", (run_id,)
            ).fetchall()
        statuses = [r["status"] for r in rows]
        if all(s == "passed" for s in statuses) and statuses:
            final_status = "passed"
        elif stop_flag.get("stop"):
            final_status = "stopped_by_user"
        elif any(s == "passed" for s in statuses):
            final_status = "partial"
        else:
            final_status = "stopped"

        # Schrijf de echte eindscore terug naar de run — anders blijft
        # best_overall_score op -1 staan terwijl de deeltaken wél een score
        # hebben, en liegt het dashboard (en de publish-gate) over de kwaliteit.
        overall = _best_overall_score(run_id)
        _update_run(run_id, status=final_status, finished_at=_now(),
                     best_overall_score=overall)

        # Auto-queue: een run die de benchmark haalt, gaat automatisch naar de
        # publish-pijplijn als content_job (status pending_review — wacht op
        # menselijke goedkeuring, NOOIT automatisch live). Zo wordt Iris' dagelijkse
        # inzet daadwerkelijk reviewbare content i.p.v. een run die in het vacuüm
        # verdwijnt. Bij elke fout loggen we en laten de run intact.
        if overall >= threshold:
            try:
                _auto_queue_run(run_id, threshold)
            except Exception as que:
                logger.warning("Auto-queue van %s mislukte (run blijft intact): %s", run_id, que)

        event_bus.publish({
            "type": "gauntlet_done", "run_id": run_id, "status": final_status,
            "best_overall_score": overall,
            "subtask_statuses": statuses,
            "message": "Menselijke eindjurat vereist: beoordeel het overgebleven resultaat "
                       "als laatste jury (POST /api/gauntlet/{id}/verdict).",
        })
        logger.info("Gauntlet %s afgerond: status=%s, best_overall_score=%s", run_id, final_status, overall)
    except Exception as exc:  # noqa: BLE001
        _update_run(run_id, status="failed", finished_at=_now())
        event_bus.publish({"type": "gauntlet_error", "run_id": run_id, "error": str(exc)})
        logger.exception("Gauntlet %s faalde: %s", run_id, exc)


# ── Publieke API ──────────────────────────────────────────────────────────────

def spawn_gauntlet(
    objective: str,
    benchmark: str,
    threshold: int = DEFAULT_THRESHOLD,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    session_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Start een Gauntlet Loop en KEER DIRECT TERUG (non-blocking)."""
    if not objective or not objective.strip():
        raise ValueError("Een opdracht (objective) is verplicht voor een Gauntlet.")
    if not benchmark or not benchmark.strip():
        raise ValueError(
            "Een benchmark is verplicht: lever een scherpe referentie (tekst, voorbeeld-"
            "artifact of beschrijving) waar de blinde critici hard tegenaan meten."
        )
    threshold = max(1, min(100, int(threshold)))
    max_iterations = max(1, min(10, int(max_iterations)))

    run_id = _create_run(objective.strip(), benchmark.strip(), session_id, threshold, max_iterations)
    stop_flag: Dict[str, bool] = {"stop": False}
    # stop_flag bewaren zodat de STOP-endpoint de lopende run kan afbreken.
    _STOP_FLAGS[run_id] = stop_flag

    task = asyncio.create_task(
        _run_gauntlet(run_id, objective.strip(), benchmark.strip(), threshold,
                      max_iterations, session_id, stop_flag, model_override)
    )
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    task.add_done_callback(lambda _t: _STOP_FLAGS.pop(run_id, None))

    return {"run_id": run_id, "threshold": threshold, "max_iterations": max_iterations}


# Stop-vlaggen per run_id, zodat de STOP-endpoint de lopende asyncio-task kan afbreken.
_STOP_FLAGS: "Dict[str, Dict[str, bool]]" = {}


def stop_gauntlet(run_id: str) -> bool:
    """Zet de stop-vlag voor een lopende run. De lussen breken bij de volgende ronde af."""
    flag = _STOP_FLAGS.get(run_id)
    if flag is None:
        # Run bestaat mogelijk niet (meer) of is al klaar. Markeer in DB als gestopt.
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE gauntlet_runs SET status='stopped_by_user', finished_at=? "
                "WHERE id=? AND status='running'",
                (_now(), run_id),
            )
        return cur.rowcount > 0
    flag["stop"] = True
    return True


def record_verdict(run_id: str, verdict: str, note: Optional[str] = None) -> bool:
    """Menselijke eindjurat: sla het oordeel van de mens op als laatste jury."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE gauntlet_runs SET human_verdict=?, human_note=?, updated_at=? "
            "WHERE id=?",
            (verdict, note or "", _now(), run_id),
        )
    return cur.rowcount > 0


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM gauntlet_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return None
        subs = conn.execute(
            "SELECT * FROM gauntlet_subtasks WHERE run_id = ? ORDER BY position ASC", (run_id,)
        ).fetchall()
        iters = conn.execute(
            "SELECT * FROM gauntlet_iterations WHERE subtask_id IN "
            "(SELECT id FROM gauntlet_subtasks WHERE run_id = ?) ORDER BY subtask_id, iteration ASC",
            (run_id,),
        ).fetchall()
    out = dict(run)
    out["subtasks"] = [dict(s) for s in subs]
    out["iterations"] = [dict(i) for i in iters]
    return out


def list_runs(limit: int = 25) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, objective, status, threshold, max_iterations, subtask_count, "
            "best_overall_score, human_verdict, published_job_id, created_at, finished_at "
            "FROM gauntlet_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Publish-gate: PASSED-run → content_job voor WeAreImpact ───────────────────
#
# De blinde criticus moet de benchmark halen (status 'passed' of 'partial' met ten
# minste één deeltaak boven drempel) VOORDAT er iets naar de publish-pijplijn mag.
# Een run onder de drempel wordt geweigerd — zo publiceer je nooit sub-standaard
# kopij (de kernbelofte van de Gauntlet Loop).

def _best_overall_score(run_id: str) -> int:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT best_score FROM gauntlet_subtasks WHERE run_id = ?", (run_id,)
        ).fetchall()
    scores = [r["best_score"] for r in rows if r["best_score"] is not None and r["best_score"] >= 0]
    return max(scores) if scores else -1


def _assemble_draft(run: Dict[str, Any]) -> str:
    """Zet de beste output per deeltaak aan elkaar tot één samenhangend stuk."""
    parts = []
    for s in run.get("subtasks", []):
        out = (s.get("best_output") or "").strip()
        if out and out != "_(De builder leverde geen tekst op.)_":
            parts.append(f"## {s.get('role', 'Deeltaak')}\n\n{out}")
    return "\n\n".join(parts) if parts else (run.get("objective") or "")


def publish_to_weareimpact(run_id: str, site_id: Optional[str] = None,
                          site_name: Optional[str] = None, title: Optional[str] = None,
                          keyword: Optional[str] = None, slug: Optional[str] = None) -> Dict[str, Any]:
    """Publish-gate: zet een PASSED Gauntlet-run om in een content_job (pending_review).

    Blokkeert als de run niet (ten minste gedeeltelijk) de benchmark haalde:
    - status 'failed' / 'stopped' / 'stopped_by_user' → geweigerd.
    - status 'partial' of 'passed' → toegestaan, maar alleen deeltaken boven de
      drempel worden meegenomen; een deeltaak onder drempel blijft uit de job.
    """
    run = get_run(run_id)
    if not run:
        raise ValueError("Gauntlet run niet gevonden.")
    status = run.get("status")
    threshold = run.get("threshold") or DEFAULT_THRESHOLD
    allowed = status in ("passed", "partial")
    if not allowed:
        raise ValueError(
            f"Run {run_id} heeft status '{status}' — pas toe nadat de blinde criticus "
            f"de benchmark haalt (>= {threshold}). Publiceren geblokkeerd."
        )

    # Neem alleen deeltaken die de drempel haalden (bij 'partial' kunnen er
    # deeltaken onder zitten die de mens alsnog wil weigeren).
    passed_subs = [
        s for s in run.get("subtasks", [])
        if (s.get("best_score") or -1) >= threshold
    ]
    if not passed_subs:
        raise ValueError(
            f"Geen enkele deeltaak haalde de drempel ({threshold}). Publiceren geblokkeerd."
        )

    draft = _assemble_draft({**run, "subtasks": passed_subs})

    # Site-resolutie: expliciete site_id wint; anders site_name → site_id;
    # anders terugval op WeAreImpact (legacy). Zo stuurt de Orchestrator het
    # juiste project mee zonder dat hij site_id's hoeft te kennen.
    resolved_site_id = site_id
    if not resolved_site_id and site_name:
        resolved_site_id = _resolve_site_id_by_name(site_name)
    if not resolved_site_id:
        resolved_site_id = _resolve_weareimpact_site_id()

    job_title = (title or run.get("objective") or "Gauntlet-run")[:120]
    job_slug = slug or _slugify(job_title)
    job_keyword = keyword or ""

    # ── Content-type detectie ──────────────────────────────────────────────
    objective_l = (run.get("objective") or "").lower()
    benchmark_l = (run.get("benchmark") or "").lower()
    is_outreach = any(k in objective_l or k in benchmark_l for k in
                      ("linkedin", "outreach", "lead", "uitnodig", "benaderen", "acquisitie"))
    # Hook/snippet-herkenning: een losse SEO-hook, titel of snippet is GEEN
    # artikel en mag nooit als pagina op de site (zie content_pipeline
    # is_non_page_content). Landt zo'n taak toch als 'blog', dan krijgt hij de
    # "Publiceer"-knop en staat de ene zin live op de site.
    is_hook = any(k in objective_l or k in benchmark_l for k in
                  ("seo-hook", "seo hook", "1-zin", "1 zin", "snippet", "hook",
                   "meta-description", "meta description", "titeltje", "kopje"))
    content_type = ("linkedin_outreach" if is_outreach
                    else "hook" if is_hook
                    else "blog")
    if is_outreach:
        social_copy = _draft_to_social_copy(draft)
        blog_html = ""
    else:
        social_copy = {}
        blog_html = _md_to_html(draft)
    # Maak een content_job aan in de publish-pijplijn (status pending_review =
    # wacht op menselijke goedkeuring, NOOIT automatisch live).
    job_id = _create_content_job(
        site_id=resolved_site_id,
        title=job_title,
        keyword=job_keyword,
        rationale=f"Gegenereerd via Gauntlet Loop (run {run_id}, status {status}). "
                  f"Blinde criticus haalde benchmark >= {threshold}. Type: {content_type}.",
        blog_html=blog_html,
        seo_score=float(_best_overall_score(run_id)),
        social_copy=social_copy,
        image_bytes=None,
        slug=job_slug,
        status="pending_review",
        qc_report={"source": "gauntlet", "run_id": run_id, "threshold": threshold,
                   "content_type": content_type},
        dedupe=False,
        content_type=content_type,
    )
    _update_run(run_id, published_job_id=job_id)
    event_bus.publish({
        "type": "gauntlet_published", "run_id": run_id, "job_id": job_id,
        "site_id": resolved_site_id,
    })
    logger.info("Gauntlet %s gepubliceerd naar content_job %s (site %s).", run_id, job_id, resolved_site_id)
    return {"run_id": run_id, "job_id": job_id, "site_id": resolved_site_id, "status": "pending_review"}


def _auto_queue_run(run_id: str, threshold: int) -> Optional[str]:
    """Stuur een geslaagde run automatisch naar de publish-pijplijn.

    Wordt aangeroepen bij run-afronding (overall >= threshold). Maakt een
    content_job met status 'pending_review' — wacht op menselijke goedkeuring,
    NOOIT automatisch live. Site-resolutie: project-naam uit de run objective
    (formaat '[Agent] taak voor <project>') → anders WeAreImpact-legacy.
    """
    run = get_run(run_id)
    if not run:
        return None
    objective = (run.get("objective") or "")
    # Project-naam zit in de agent-deploy benchmark ("... voor project 'X'")
    import re
    m = re.search(r"project\s+['\"]([^'\"]+)['\"]", (run.get("benchmark") or ""), re.IGNORECASE)
    project = m.group(1).strip() if m else None
    site_id = _resolve_site_id_by_name(project) if project else None
    out = publish_to_weareimpact(run_id, site_id=site_id, site_name=project)
    logger.info("Auto-queue: run %s -> content_job %s (site %s)", run_id, out.get("job_id"), out.get("site_id"))
    return out.get("job_id")


def _resolve_weareimpact_site_id() -> str:
    """Zoek de WeAreImpact-site in de publish-pijplijn; val terug op 'weareimpact'."""
    try:
        from ...domains.seo import sites as sites_service
        sites = sites_service.list_sites() if hasattr(sites_service, "list_sites") else []
        for s in sites:
            if "weareimpact" in str(s.get("name", "")).lower() or "weareimpact" in str(s.get("id", "")).lower():
                return s.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon WeAreImpact-site niet resolveren: %s", exc)
    return "weareimpact"


def _resolve_site_id_by_name(site_name: str) -> Optional[str]:
    """Zoek een site_id op project-naam (case-insensitive, spaties/streepjes genegeerd)."""
    norm = lambda x: "".join(c for c in str(x).lower() if c.isalnum())
    target = norm(site_name)
    try:
        from ...domains.seo import sites as sites_service
        sites = sites_service.list_sites() if hasattr(sites_service, "list_sites") else []
        for s in sites:
            if norm(s.get("name", "")) == target or norm(s.get("id", "")) == target:
                return s.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kon site niet resolveren op naam %r: %s", site_name, exc)
    return None


def _slugify(text: str) -> str:
    import re
    t = re.sub(r"[^a-z0-9\s-]", "", text.lower()).strip()
    t = re.sub(r"\s+", "-", t)
    return t[:80] or "gauntlet-run"


def _draft_to_social_copy(draft: str) -> Dict[str, str]:
    """Parse een Gauntlet-draft (## Rol + paragrafen) naar aparte LinkedIn-berichten.

    Elke H2-blok wordt één bericht onder een sleutel als 'linkedin_<rol-slug>'.
    Zo kan Iris/gebruiker ze per doelgroep plakken ipv als één site-pagina.
    """
    import re
    blocks = re.split(r"\n## ", draft)
    out: Dict[str, str] = {}
    for i, block in enumerate(blocks):
        if not block.strip():
            continue
        lines = block.split("\n", 1)
        role = (lines[0].strip() or f"bericht {i}").lower()
        role_key = re.sub(r"[^a-z0-9]+", "_", role).strip("_")[:40]
        body = lines[1].strip() if len(lines) > 1 else ""
        # HTML → platte tekst voor LinkedIn (geen tags in een DM).
        text = re.sub(r"<[^>]+>", "", body).strip()
        if text:
            out[f"linkedin_{role_key}"] = text
    return out or {"linkedin_bericht": re.sub(r"<[^>]+>", "", draft).strip()}


def _md_to_html(md: str) -> str:
    """Minimale markdown→HTML voor de content_job (H2, paragrafen, bold)."""
    import re
    html_parts = []
    for block in md.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            html_parts.append(f"<h2>{_esc(block[3:].strip())}</h2>")
        elif block.startswith("# "):
            html_parts.append(f"<h1>{_esc(block[2:].strip())}</h1>")
        else:
            html_parts.append(f"<p>{_esc(block)}</p>")
    return "\n".join(html_parts)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

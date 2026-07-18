"""Generiek leer-raamwerk — het Iris-patroon voor élke agent.

Een agent "leert" hier op precies één manier: met bewijs. Een les is een zin
plus de cijfers waarop hij rust; elke les wordt getoetst met falsifieerbare
voorspellingen die na hun horizon worden afgerekend tegen échte data (via een
per-agent `resolver`, nooit via een LLM-zelfoordeel). Correcte voorspellingen
verhogen het vertrouwen van de les, foute verlagen het, en bij herhaald falen
wordt de les ingetrokken (`active=0`). Bewijs weegt, niet herhaling.

Lessen sturen uitsluitend prompts (via `lessons_block()`), nooit gedrag: geen
enkele les zet zelf een knop om, en alles blijft achter de review-gates.

Twee voorspellings-vormen (`comparison`):
- 'trend'     : is de metriek t.o.v. de baseline de gewenste kant op bewogen?
                (het Iris-model; beweging < `noise` telt als 'unclear')
- 'threshold' : haalt de uitkomst de target-drempel? (bv. "de reply-rate-kloof
                tussen twee varianten blijft ≥ 1 procentpunt")

`lower_is_better` draait de betekenis van 'up' om (bv. GSC-positie): 'up'
betekent altijd "beter", en dit vlaggetje bepaalt welke kant dat op is.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pytz

from .database import get_conn

logger = logging.getLogger(__name__)
_TZ = pytz.timezone("Europe/Amsterdam")

# Resolver-signatuur: (metric, context) -> huidige waarde, of None als de
# metriek (nog) niet eerlijk meetbaar is — dan wordt de voorspelling 'unclear'.
Resolver = Callable[[str, str], Optional[float]]

# Intrekkings-drempel: minstens zoveel metingen én onder deze trefkans.
_RETRACT_MIN_MEASURED = 3
_RETRACT_BELOW = 0.34


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


def _norm(text: str) -> str:
    """Normaliseer een les voor dedupe: kleine letters, alleen woordtekens."""
    return re.sub(r"[^a-z0-9à-ÿ]+", " ", (text or "").lower()).strip()


# ── Lessen ─────────────────────────────────────────────────────────────────

def upsert_lesson(agent: str, lesson: str, *, category: str = "",
                  evidence: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Leg een les vast, of bevestig een bestaande (dedupe op genormaliseerde
    tekst per agent). Een ingetrokken les wordt NIET heropgevoerd — herhaling
    houdt een weerlegde les niet in leven; alleen nieuw bewijs (een correcte
    voorspelling) zou dat mogen, en dat pad bestaat bewust niet automatisch.

    Retourneert het les-id, of None als de les leeg is of ingetrokken was."""
    lesson = (lesson or "").strip()
    if not agent or not lesson:
        return None
    key = _norm(lesson)
    now = _now_iso()
    ev = json.dumps(evidence or {}, ensure_ascii=False)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, lesson, active FROM agent_lessons WHERE agent = ?",
            (agent,),
        ).fetchall()
        for r in rows:
            if _norm(r["lesson"]) == key:
                if not r["active"]:
                    return None
                conn.execute(
                    "UPDATE agent_lessons SET times_confirmed = times_confirmed + 1, "
                    "evidence = ?, updated_at = ? WHERE id = ?",
                    (ev, now, r["id"]),
                )
                return r["id"]
        lid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO agent_lessons (id, agent, lesson, category, evidence, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lid, agent, lesson[:400], category, ev, now, now),
        )
    return lid


def active_lessons(agent: str, max_n: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_lessons WHERE agent = ? AND active = 1 "
            "ORDER BY confidence DESC, times_confirmed DESC, updated_at DESC LIMIT ?",
            (agent, max_n),
        ).fetchall()
    return [dict(r) for r in rows]


def top_lesson(agent: str) -> Optional[Dict[str, Any]]:
    """De sterkste les met echt bewijs — voor bv. het ochtendrapport.
    None zolang er niets is dat meer is dan een eerste vermoeden."""
    for lesson in active_lessons(agent, max_n=5):
        if lesson["confidence"] >= 0.5 and (
            lesson["times_confirmed"] >= 2 or lesson["predictions_made"] >= 1
        ):
            return lesson
    return None


def lessons_block(agent: str, max_n: int = 5) -> str:
    """Promptblok met de actieve lessen van een agent, inclusief hoe hard elke
    les is. Leeg ("") zolang er niets geleerds is — dan geen blok injecteren."""
    lessons = active_lessons(agent, max_n=max_n)
    if not lessons:
        return ""
    lines = ["Geleerde lessen (gemeten uit echte resultaten, niet gegokt):"]
    for lesson in lessons:
        proof = []
        if lesson["predictions_made"]:
            proof.append(f"trefkans {round(lesson['confidence'] * 100)}% "
                         f"over {lesson['predictions_made']} voorspelling(en)")
        if lesson["times_confirmed"] > 1:
            proof.append(f"{lesson['times_confirmed']}× bevestigd")
        suffix = f" [{'; '.join(proof)}]" if proof else ""
        lines.append(f"- {lesson['lesson']}{suffix}")
    return "\n".join(lines)


# ── Voorspellingen ─────────────────────────────────────────────────────────

def record_prediction(agent: str, *, metric: str, direction: str, baseline: Optional[float],
                      statement: str, context: str = "", horizon_days: int = 14,
                      target: Optional[float] = None, comparison: str = "trend",
                      lower_is_better: bool = False, noise: float = 0.5,
                      lesson_id: str = "") -> Optional[str]:
    """Leg één toetsbare voorspelling vast. Ongeldig of niet-meetbaar → None
    (beter geen voorspelling dan een die niet eerlijk af te rekenen is).
    Dedupe: één open voorspelling per (agent, metric, context, direction)."""
    direction = (direction or "").strip().lower()
    comparison = (comparison or "trend").strip().lower()
    if (not agent or not metric or direction not in ("up", "down")
            or comparison not in ("trend", "threshold") or baseline is None):
        return None
    if comparison == "threshold" and target is None:
        return None
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT 1 FROM agent_predictions WHERE status = 'open' AND agent = ? "
            "AND metric = ? AND context = ? AND direction = ? LIMIT 1",
            (agent, metric, context, direction),
        ).fetchone()
        if dup:
            return None
        horizon_days = max(1, min(60, int(horizon_days or 14)))
        due = (datetime.now(_TZ) + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
        pid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO agent_predictions
               (id, agent, context, metric, direction, comparison, lower_is_better,
                noise, baseline, target, horizon_days, due_date, lesson_id, statement,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (pid, agent, context, metric, direction, comparison,
             1 if lower_is_better else 0, float(noise), float(baseline), target,
             horizon_days, due, lesson_id, (statement or "")[:400], _now_iso()),
        )
    return pid


def _judge(pred: Dict[str, Any], outcome: float) -> tuple[str, str]:
    """Beoordeel één voorspelling: correct | wrong | unclear + toelichting.
    Puur regelgebaseerd — hier komt nooit een LLM aan te pas."""
    baseline = pred["baseline"]
    target = pred["target"]
    lower_better = bool(pred["lower_is_better"])
    wants_better = pred["direction"] == "up"

    if pred["comparison"] == "threshold":
        # Haalt de uitkomst de drempel? 'up' = op of boven de target blijven
        # (of eronder, als lager beter is).
        if wants_better != lower_better:
            reached = outcome >= target
        else:
            reached = outcome <= target
        note = f"uitkomst {outcome:g} vs drempel {target:g} (baseline {baseline:g})"
        return ("correct", note) if reached else ("wrong", note)

    # trend: beweging t.o.v. de baseline, genormaliseerd naar "verbetering".
    improvement = (baseline - outcome) if lower_better else (outcome - baseline)
    if abs(improvement) < pred["noise"]:
        return "unclear", f"nauwelijks bewogen ({baseline:g} → {outcome:g})"
    hit = (improvement > 0) == wants_better
    return ("correct", f"{baseline:g} → {outcome:g}") if hit \
        else ("wrong", f"tegengesteld ({baseline:g} → {outcome:g})")


def evaluate_due(agent: str, resolver: Resolver, today: Optional[str] = None) -> Dict[str, Any]:
    """Reken alle voorspellingen van deze agent af waarvan de horizon
    verstreek. Werkt het vertrouwen van gekoppelde lessen bij en retourneert
    een verslag (voor prompt/briefing/uitkomstkaart)."""
    today = today or _today()
    with get_conn() as conn:
        due_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM agent_predictions WHERE status = 'open' "
            "AND agent = ? AND due_date <= ?",
            (agent, today),
        ).fetchall()]

    evaluated: List[Dict[str, Any]] = []
    for pred in due_rows:
        try:
            outcome = resolver(pred["metric"], pred["context"])
        except Exception:
            logger.exception("[learning] resolver van '%s' faalde op %s/%s",
                             agent, pred["metric"], pred["context"])
            outcome = None
        if outcome is None:
            _close(pred["id"], "unclear", None, "metriek (nog) niet meetbaar")
            evaluated.append({**pred, "status": "unclear", "outcome": None,
                              "note": "niet meetbaar"})
            continue
        status, note = _judge(pred, float(outcome))
        _close(pred["id"], status, float(outcome), note)
        if pred["lesson_id"] and status in ("correct", "wrong"):
            _update_confidence(pred["lesson_id"], correct=status == "correct")
        evaluated.append({**pred, "status": status, "outcome": float(outcome), "note": note})

    correct = sum(1 for e in evaluated if e["status"] == "correct")
    wrong = sum(1 for e in evaluated if e["status"] == "wrong")
    decided = correct + wrong
    return {
        "evaluated": evaluated,
        "correct": correct,
        "wrong": wrong,
        "unclear": sum(1 for e in evaluated if e["status"] == "unclear"),
        "accuracy": round(correct / decided * 100, 1) if decided else None,
    }


def _close(pid: str, status: str, outcome: Optional[float], note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_predictions SET status = ?, outcome_value = ?, "
            "outcome_note = ?, evaluated_at = ? WHERE id = ?",
            (status, outcome, note[:300], _now_iso(), pid),
        )


def _update_confidence(lesson_id: str, *, correct: bool) -> None:
    """Bewijs weegt: correct → vertrouwen omhoog, fout → omlaag; bij herhaald
    falen wordt de les ingetrokken. (Zelfde wiskunde als Iris.)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT predictions_made, predictions_correct FROM agent_lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()
        if not row:
            return
        made = (row["predictions_made"] or 0) + 1
        ok = (row["predictions_correct"] or 0) + (1 if correct else 0)
        # Laplace-gladgestreken zodat één meting niet meteen 0/100% geeft.
        confidence = round((ok + 1) / (made + 2), 3)
        active = 0 if (made >= _RETRACT_MIN_MEASURED and confidence < _RETRACT_BELOW) else 1
        conn.execute(
            "UPDATE agent_lessons SET predictions_made = ?, predictions_correct = ?, "
            "confidence = ?, active = ?, updated_at = ? WHERE id = ?",
            (made, ok, confidence, active, _now_iso(), lesson_id),
        )


# ── Inzicht (API/UI) ───────────────────────────────────────────────────────

def predictions(agent: str, status: Optional[str] = None,
                max_n: int = 50) -> List[Dict[str, Any]]:
    q = "SELECT * FROM agent_predictions WHERE agent = ?"
    params: List[Any] = [agent]
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, due_date ASC LIMIT ?"
    params.append(max_n)
    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def track_record(agent: str, days: int = 90) -> Dict[str, Any]:
    """Trefkans van een agent over de afgelopen periode — eerlijk zelfoordeel."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM agent_predictions "
            "WHERE agent = ? AND status != 'open' AND evaluated_at >= datetime('now', ?) "
            "GROUP BY status",
            (agent, f"-{days} days"),
        ).fetchall()
        open_n = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_predictions "
            "WHERE agent = ? AND status = 'open'", (agent,),
        ).fetchone()["n"]
    counts = {r["status"]: r["n"] for r in rows}
    correct, wrong = counts.get("correct", 0), counts.get("wrong", 0)
    decided = correct + wrong
    return {
        "correct": correct,
        "wrong": wrong,
        "unclear": counts.get("unclear", 0),
        "accuracy": round(correct / decided * 100, 1) if decided else None,
        "open": open_n,
    }


def agents_with_lessons() -> List[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT agent FROM agent_lessons "
            "UNION SELECT DISTINCT agent FROM agent_predictions ORDER BY 1"
        ).fetchall()
    return [r[0] for r in rows]

"""Iris' voorspellingen — de gesloten leer-lus.

Een manager is pas wereldklasse als ze aantoonbaar leert. Daarvoor legt Iris
bij haar advies falsifieerbare voorspellingen vast (metric, richting, horizon,
baseline), en rekent ze die na `due_date` af tegen de echte cijfers uit de
GSC-historie. Correcte voorspellingen versterken de onderliggende les; foute
voorspellingen laten haar vertrouwen dalen en trekken de les uiteindelijk in.

Metrieken die we kunnen meten (per site):
- clicks / impressions / position / ctr : uit `history.site_trend` (last7-venster)
- live_content                          : uit de content-pijler (live_30d)

Positie is bijzonder: LAGER is beter. 'up'/'beter' betekent dus dat het getal
daalt. Dat wordt hier consequent afgehandeld.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz

from ...shared.database import get_conn

logger = logging.getLogger(__name__)
_TZ = pytz.timezone("Europe/Amsterdam")

_VALID_METRICS = {"clicks", "impressions", "position", "ctr", "live_content"}
# Ruis-drempel: een beweging kleiner dan dit telt als "geen echte verandering".
_NOISE = {"clicks": 1.0, "impressions": 5.0, "position": 0.5, "ctr": 0.5, "live_content": 0.5}


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


def metric_value(project_snapshot: Dict[str, Any], metric: str) -> Optional[float]:
    """Huidige waarde van een metriek voor een project uit de snapshot.

    Retourneert None als de waarde (nog) niet meetbaar is — dan kan een
    voorspelling er niet eerlijk tegen afgerekend worden.
    """
    if metric == "live_content":
        return float(project_snapshot["pillars"]["content"]["live_30d"])
    trend = project_snapshot.get("trend")
    if not trend or not trend.get("site"):
        return None
    last7 = trend["site"]["last7"]
    if metric == "clicks":
        return float(last7["clicks"])
    if metric == "impressions":
        return float(last7["impressions"])
    if metric == "position":
        return last7["avg_position"]  # kan None zijn
    if metric == "ctr":
        imps = last7["impressions"]
        return round(last7["clicks"] / imps * 100, 2) if imps else None
    return None


def create_prediction(*, report_date: str, project: str, site_id: str, metric: str,
                      direction: str, baseline: Optional[float], statement: str,
                      horizon_days: int = 7, target: Optional[float] = None,
                      lesson_id: str = "") -> Optional[str]:
    """Leg één voorspelling vast. Ongeldige/niet-meetbare voorspellingen worden
    stil overgeslagen (retourneert None) — beter geen voorspelling dan een die
    niet eerlijk te toetsen is."""
    metric = (metric or "").strip().lower()
    direction = (direction or "").strip().lower()
    if metric not in _VALID_METRICS or direction not in ("up", "down") or baseline is None:
        return None
    # Dedupe: één open voorspelling per (site, metric, richting). De LLM
    # herhaalt zichzelf graag over briefings heen ("WeAreImpact haalt 12
    # clicks" twee keer open) — dat vertroebelt de trefkans en de briefing.
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT 1 FROM iris_predictions WHERE status = 'open' "
            "AND site_id = ? AND metric = ? AND direction = ? LIMIT 1",
            (site_id, metric, direction),
        ).fetchone()
    if dup:
        return None
    horizon_days = max(1, min(30, int(horizon_days or 7)))
    due = (datetime.strptime(report_date, "%Y-%m-%d") + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    pid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO iris_predictions
               (id, report_date, project, site_id, metric, direction, baseline,
                target, horizon_days, due_date, lesson_id, statement, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (pid, report_date, project, site_id, metric, direction,
             float(baseline), target, horizon_days, due, lesson_id, statement[:400], _now_iso()),
        )
    return pid


def _judge(metric: str, direction: str, baseline: float, target: Optional[float],
           outcome: float) -> tuple[str, str]:
    """Beoordeel één voorspelling: correct | wrong | unclear + een toelichting."""
    # Positie: lager = beter. Normaliseer 'verbetering' naar een positief getal.
    if metric == "position":
        improvement = baseline - outcome          # positief = beter geworden
        wanted_improvement = direction == "up"
    else:
        improvement = outcome - baseline           # positief = gestegen
        wanted_improvement = direction == "up"

    noise = _NOISE.get(metric, 0.5)
    bewogen = abs(improvement) >= noise
    moved_up = improvement > 0
    hit_direction = bewogen and (moved_up == wanted_improvement)

    if target is not None:
        # Met een expliciet doel ís het doel de claim, en dan mag de ruisdrempel
        # niet vóór de doeltoets komen. Vóór 2 aug 2026 stond hij daar wel, en
        # dan heette "TeambuildingMetImpact krijgt 1 click" bij 0 → 0 netjes
        # 'unclear' — niet meegeteld in de trefkans. Vijf van de negen unclears
        # waren zulke stilstanden; de gemelde 42,9% was in werkelijkheid 26%.
        # Een voorspelling die een drempel noemt en die drempel niet haalt is
        # geen onbeslist geval maar een misser, hoe stil de metriek ook bleef.
        if metric == "position":
            reached = outcome <= target if direction == "up" else outcome >= target
        else:
            reached = outcome >= target if direction == "up" else outcome <= target
        if reached:
            return "correct", f"doel gehaald ({baseline:g} → {outcome:g}, doel {target:g})"
        if hit_direction:
            return "unclear", f"juiste kant maar doel niet gehaald ({baseline:g} → {outcome:g}, doel {target:g})"
        if not bewogen:
            return "wrong", f"niet bewogen, doel niet gehaald ({baseline:g} → {outcome:g}, doel {target:g})"
        return "wrong", f"verkeerde kant ({baseline:g} → {outcome:g}, doel {target:g})"

    # Zonder target telt alleen de richting — en dáár is een beweging binnen de
    # ruismarge wél oprecht onbeslist: er is geen drempel om aan af te meten.
    if not bewogen:
        return "unclear", f"nauwelijks bewogen ({baseline:g} → {outcome:g})"
    return ("correct", f"{baseline:g} → {outcome:g}") if hit_direction \
        else ("wrong", f"tegengesteld ({baseline:g} → {outcome:g})")


def evaluate_due(projects: List[Dict[str, Any]], today: Optional[str] = None) -> Dict[str, Any]:
    """Reken alle openstaande voorspellingen af waarvan de horizon verstreken is.

    `projects` is de metrics-snapshot (lijst per project) met huidige waarden.
    Werkt het vertrouwen van de gekoppelde lessen bij. Retourneert een verslag
    voor in de prompt en de briefing.
    """
    today = today or _today()
    by_site = {p["site_id"]: p for p in projects}
    by_name = {p["project"]: p for p in projects}
    evaluated: List[Dict[str, Any]] = []

    with get_conn() as conn:
        due_rows = conn.execute(
            "SELECT * FROM iris_predictions WHERE status = 'open' AND due_date <= ?",
            (today,),
        ).fetchall()

    for row in due_rows:
        p = dict(row)
        snap = by_site.get(p["site_id"]) or by_name.get(p["project"])
        if not snap:
            _close_prediction(p["id"], "untested", None, "project niet meer in snapshot")
            continue
        outcome = metric_value(snap, p["metric"])
        if outcome is None:
            _close_prediction(p["id"], "untested", None, "metriek nog niet meetbaar")
            evaluated.append({**p, "status": "untested", "outcome": None,
                              "note": "nog niet meetbaar"})
            continue
        status, note = _judge(p["metric"], p["direction"], p["baseline"], p["target"], outcome)
        _close_prediction(p["id"], status, outcome, note)
        if p["lesson_id"] and status in ("correct", "wrong"):
            _update_lesson_confidence(p["lesson_id"], correct=status == "correct")
        evaluated.append({**p, "status": status, "outcome": outcome, "note": note})

    correct = sum(1 for e in evaluated if e["status"] == "correct")
    wrong = sum(1 for e in evaluated if e["status"] == "wrong")
    decided = correct + wrong
    return {
        "evaluated": evaluated,
        "correct": correct,
        "wrong": wrong,
        # 'unclear' = wél gemeten, geen uitsluitsel (nauwelijks bewogen, of de
        # juiste kant op zonder het doel te halen). 'untested' = nooit gemeten,
        # dus geen uitspraak over Iris' trefzekerheid. Die twee op één hoop
        # gooien laat de leerlus slechter lijken dan hij is: op 27 jul 2026
        # stonden 12 uitkomsten als 'unclear' geboekt, waarvan er 6 puur
        # opruimwerk waren van dubbele voorspellingen.
        "unclear": sum(1 for e in evaluated if e["status"] == "unclear"),
        "untested": sum(1 for e in evaluated if e["status"] == "untested"),
        "accuracy": round(correct / decided * 100, 1) if decided else None,
    }


def _close_prediction(pid: str, status: str, outcome: Optional[float], note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE iris_predictions SET status = ?, outcome_value = ?, outcome_note = ?, "
            "evaluated_at = ? WHERE id = ?",
            (status, outcome, note[:300], _now_iso(), pid),
        )


def _update_lesson_confidence(lesson_id: str, *, correct: bool) -> None:
    """Bewijs weegt: correcte voorspelling verhoogt het vertrouwen van de les,
    foute verlaagt het. Zakt het vertrouwen te ver, dan wordt de les
    ingetrokken (active=0) — herhaling houdt een foute les niet in leven."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT predictions_made, predictions_correct FROM iris_lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()
        if not row:
            return
        made = (row["predictions_made"] or 0) + 1
        ok = (row["predictions_correct"] or 0) + (1 if correct else 0)
        # Laplace-gladgestreken trefkans zodat één meting niet meteen 0/100% geeft.
        confidence = round((ok + 1) / (made + 2), 3)
        # Intrekken: minstens 3 metingen én < 34% raak.
        active = 0 if (made >= 3 and confidence < 0.34) else 1
        conn.execute(
            "UPDATE iris_lessons SET predictions_made = ?, predictions_correct = ?, "
            "confidence = ?, active = ?, updated_at = ? WHERE id = ?",
            (made, ok, confidence, active, _now_iso(), lesson_id),
        )


def open_predictions() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM iris_predictions WHERE status = 'open' ORDER BY due_date ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def track_record(days: int = 60) -> Dict[str, Any]:
    """Iris' eigen trefkans over de afgelopen periode — eerlijk zelfoordeel."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM iris_predictions "
            "WHERE status != 'open' AND evaluated_at >= datetime('now', ?) GROUP BY status",
            (f"-{days} days",),
        ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    correct, wrong = counts.get("correct", 0), counts.get("wrong", 0)
    decided = correct + wrong
    return {
        "correct": correct,
        "wrong": wrong,
        "unclear": counts.get("unclear", 0),
        # Nooit gemeten (project weg, metriek niet meetbaar, duplicaat) — telt
        # niet mee als Iris' onvermogen om te voorspellen.
        "untested": counts.get("untested", 0) + counts.get("superseded", 0),
        "accuracy": round(correct / decided * 100, 1) if decided else None,
        "open": len(open_predictions()),
    }

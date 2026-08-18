"""Astros Momentum — echte trenddetectie over meerdere scans.

De bestaande `signal_score` is een *snapshot*: versheid + bron-autoriteit +
keyword-match op het moment van vinden. Dat is een redelijke "is dit
interessant"-schatting, maar het ziet niet of iets daadwerkelijk *stijgt* —
het verschil tussen een artikel dat net uitkwam (hoog, want vers) en een
artikel dat viral *gaat* (hoog én stijgend). Precies die tweede categorie
is wat Goldies "Astros" claimt te vinden maar niet kan bewijzen.

Oplossing: we loggen elke scan-run per signaal de score, en berekenen uit
de tijdreeks een momentum-index:

  momentum_index (0-100) = velocity_component + consistency_component
    - velocity: hoe snel stijgt de score? (lineair over de laatste N metingen)
    - consistency: zijn er meerdere onafhankelijke scans die 'm bevestigen?

Daarnaast een `trend`-label: rising | exploding | steady | cooling — zodat
de UI en de dagelijkse digest direct kunnen zeggen "dit explodeert".

De module is volledig additief: bestaande signalen zónder momentum-rij
krijgen trend='new' en momentum_index=0 totdat er genoeg metingen zijn.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from ...shared.database import get_conn

# Minimaal aantal metingen voordat we een betrouwbaar momentum durven te geven.
MIN_MEASUREMENTS = 2
# Hoeveel metingen we maximaal bewaren per signaal (rolling window).
MAX_SAMPLES = 12


def ensure_momentum_schema() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS radar_momentum (
                id              TEXT PRIMARY KEY,
                signal_id       TEXT NOT NULL,
                project         TEXT NOT NULL DEFAULT '',
                measurements    TEXT NOT NULL DEFAULT '[]',   -- JSON: [{t, score}]
                momentum_index  REAL NOT NULL DEFAULT 0,
                trend           TEXT NOT NULL DEFAULT 'new', -- new|rising|exploding|steady|cooling
                first_seen      TEXT NOT NULL,
                last_seen       TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_momentum_signal "
            "ON radar_momentum(signal_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_momentum_proj_trend "
            "ON radar_momentum(project, trend, momentum_index DESC)"
        )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _compute_trend(scores: List[float]) -> tuple[float, str]:
    """Bereken (momentum_index, trend_label) uit een score-tijdreeks.

    scores: oplopend in tijd. Returneert (0.0, 'new') bij <2 metingen.
    """
    if len(scores) < MIN_MEASUREMENTS:
        return 0.0, "new"
    # Velocity: lineair verloop (slope) over de reeks, genormaliseerd naar 0-100.
    n = len(scores)
    xs = list(range(n))
    xbar = sum(xs) / n
    ybar = sum(scores) / n
    num = sum((x - xbar) * (y - ybar) for x, y in zip(xs, scores))
    den = sum((x - xbar) ** 2 for x in xs) or 1
    slope = num / den  # gemiddelde stijging per meting (score-punten)
    # Een stijging van ~5 punten/scan is "hard stijgend"; clamp naar 0-100.
    velocity = max(0.0, min(100.0, slope * 20.0))

    # Consistency: hoeveel van de metingen bevestigen een stijging?
    rises = sum(1 for i in range(1, n) if scores[i] > scores[i - 1])
    consistency = (rises / (n - 1)) * 100.0 if n > 1 else 0.0

    momentum = round(0.6 * velocity + 0.4 * consistency, 1)

    if momentum >= 70 and slope >= 4:
        trend = "exploding"
    elif momentum >= 40 and slope > 0:
        trend = "rising"
    elif slope < -2:
        trend = "cooling"
    else:
        trend = "steady"
    return momentum, trend


def record_measurement(signal_id: str, project: str, score: float) -> Dict:
    """Voeg één meting toe voor een signaal en herbereken momentum/trend.

    Wordt aangeroepen vanuit de scan-loop (per vers signaal dat we opslaan,
    of per bestaand signaal dat we her-zien). Idempotent en defensief.
    """
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM radar_momentum WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row:
            samples = json.loads(row["measurements"] or "[]")
            samples.append({"t": now, "score": round(float(score), 1)})
            # Rolling window — oudste eraf.
            samples = samples[-MAX_SAMPLES:]
            momentum, trend = _compute_trend([s["score"] for s in samples])
            conn.execute(
                """UPDATE radar_momentum
                   SET measurements = ?, momentum_index = ?, trend = ?,
                       last_seen = ?, updated_at = ?
                   WHERE signal_id = ?""",
                (json.dumps(samples, ensure_ascii=False), momentum, trend,
                 now, now, signal_id),
            )
            return {"momentum_index": momentum, "trend": trend, "samples": len(samples)}
        else:
            samples = [{"t": now, "score": round(float(score), 1)}]
            mid = f"mom_{signal_id}"
            conn.execute(
                """INSERT INTO radar_momentum
                   (id, signal_id, project, measurements, momentum_index, trend,
                    first_seen, last_seen, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (mid, signal_id, project, json.dumps(samples, ensure_ascii=False),
                 0.0, "new", now, now, now),
            )
            return {"momentum_index": 0.0, "trend": "new", "samples": 1}


def get_momentum(signal_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM radar_momentum WHERE signal_id = ?", (signal_id,)
        ).fetchone()
    return dict(row) if row else None


def top_momentum(project: Optional[str] = None, limit: int = 25) -> List[Dict]:
    """Signaal-IDs met de hoogste momentum, optioneel per project.

    Join met radar_signals voor de titel/url zodat de UI/ digest direct
    bruikbare rijen krijgt.
    """
    with get_conn() as conn:
        if project:
            rows = conn.execute(
                """SELECT m.*, s.title, s.url, s.source, s.signal_score, s.project
                   FROM radar_momentum m
                   JOIN radar_signals s ON s.id = m.signal_id
                   WHERE m.project = ? AND m.trend != 'new'
                   ORDER BY m.momentum_index DESC LIMIT ?""",
                (project.lower(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.*, s.title, s.url, s.source, s.signal_score, s.project
                   FROM radar_momentum m
                   JOIN radar_signals s ON s.id = m.signal_id
                   WHERE m.trend != 'new'
                   ORDER BY m.momentum_index DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]

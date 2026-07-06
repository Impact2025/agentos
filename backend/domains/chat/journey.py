"""
Mission Control journey-recorder.

Legt elke agent-run vast als een 'journey' met geordende events
(thought / tool_start / tool_result / text / error). Hiermee kun je:
  - achterwaarts scannen: waar switchte de agent van denkproces naar een tool-fout?
  - meerdere runs van dezelfde taak naast elkaar leggen (read-only audit).

De recorder is best-effort: een fout bij het loggen mag een lopende
chat-stream nooit onderbreken.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_journey(
    session_id: str,
    agent: str = "",
    model: str = "",
    user_message: str = "",
) -> str:
    journey_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO journeys (id, session_id, agent, model, user_message, status, started_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (journey_id, session_id, agent, model, user_message, _now()),
        )
    return journey_id


def record_event(
    journey_id: str,
    seq: int,
    type: str,
    name: str = "",
    content: str = "",
    is_error: bool = False,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO journey_events (journey_id, seq, type, name, content, is_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (journey_id, seq, type, name, content, 1 if is_error else 0, _now()),
        )


def finish_journey(
    journey_id: str,
    status: str = "done",
    final_text: str = "",
    error: str = "",
    total_tokens: int = 0,
) -> None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT started_at FROM journeys WHERE id = ?", (journey_id,)
        ).fetchone()
        duration_ms = 0
        finished = _now()
        if row:
            try:
                started = datetime.fromisoformat(row["started_at"])
                duration_ms = int((datetime.fromisoformat(finished) - started).total_seconds() * 1000)
            except ValueError:
                duration_ms = 0
        conn.execute(
            """
            UPDATE journeys
            SET status = ?, final_text = ?, error = ?, finished_at = ?, duration_ms = ?, total_tokens = ?
            WHERE id = ?
            """,
            (status, final_text, error, finished, duration_ms, total_tokens, journey_id),
        )


def list_journeys(session_id: str) -> List[Dict]:
    """Runs voor een sessie (nieuwste eerst), met event-telling — voor de audit-lijst."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT j.id, j.session_id, j.agent, j.model, j.user_message, j.status,
                   j.error, j.started_at, j.finished_at, j.duration_ms, j.total_tokens,
                   COUNT(e.id) AS event_count,
                   SUM(CASE WHEN e.is_error = 1 THEN 1 ELSE 0 END) AS error_count
            FROM journeys j
            LEFT JOIN journey_events e ON e.journey_id = j.id
            WHERE j.session_id = ?
            GROUP BY j.id
            ORDER BY j.started_at DESC
            """,
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_journey(journey_id: str) -> Optional[Dict]:
    """Eén journey met alle events op volgorde — voor het achterwaarts scannen."""
    with get_conn() as conn:
        j = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
        if not j:
            return None
        events = conn.execute(
            "SELECT seq, type, name, content, is_error, created_at "
            "FROM journey_events WHERE journey_id = ? ORDER BY seq ASC, id ASC",
            (journey_id,),
        ).fetchall()
    result = dict(j)
    result["events"] = [dict(e) for e in events]
    return result

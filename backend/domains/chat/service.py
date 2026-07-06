"""
SQLite-backed conversatie geheugen per sessie.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Optional
from ...shared.database import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Sessions ────────────────────────────────────────────────────────────────

def create_session(name: str, agent: str = "claude") -> Dict:
    session_id = str(uuid.uuid4())
    created_at = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, name, agent, created_at) VALUES (?, ?, ?, ?)",
            (session_id, name, agent, created_at),
        )
    return {"id": session_id, "name": name, "agent": agent, "created_at": created_at, "message_count": 0}


def list_sessions() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.id, s.name, s.agent, s.created_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_session(session_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return cur.rowcount > 0


# ── Messages ─────────────────────────────────────────────────────────────────

def add_message(session_id: str, role: str, content: str) -> Dict:
    created_at = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, created_at),
        )
        msg_id = cur.lastrowid
    return {"id": msg_id, "session_id": session_id, "role": role, "content": content, "created_at": created_at}


def get_messages(session_id: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages_for_api(session_id: str) -> List[Dict]:
    """Geeft berichten terug in het formaat dat de Anthropic/OpenRouter API verwacht."""
    msgs = get_messages(session_id)
    return [{"role": m["role"], "content": m["content"]} for m in msgs]

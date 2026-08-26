"""Besluiten — service-laag: aanmaken, oplijsten, afronden."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from .models import ensure_schema


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["options"] = json.loads(d.get("options") or "[]")
    except (TypeError, ValueError):
        d["options"] = []
    return d


def add_decision(project: str, title: str, context: str = "", options: Optional[List[str]] = None,
                  deadline: str = "") -> Dict[str, Any]:
    ensure_schema()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO decisions (project, title, context, options, status, deadline, created_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?)""",
            (project, title, context, json.dumps(options or []), deadline, _now_iso()),
        )
        did = cur.lastrowid
    return get_decision(did) or {}


def get_decision(did: int) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)).fetchone()
    return _row_to_dict(row) if row else None


def list_decisions(project: str, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE project = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT ?", (project, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def resolve_decision(did: int, decision: str, reasoning: str = "") -> Optional[Dict[str, Any]]:
    ensure_schema()
    existing = get_decision(did)
    if not existing:
        return None
    with get_conn() as conn:
        conn.execute(
            "UPDATE decisions SET status='besloten', decision=?, reasoning=?, decided_at=? WHERE id=?",
            (decision, reasoning, _now_iso(), did),
        )
    return get_decision(did)


def reopen_decision(did: int) -> Optional[Dict[str, Any]]:
    """Een afgerond besluit terugzetten naar open — voor als de omstandigheden
    veranderen en het toch opnieuw moet. Wist bewust de vorige keuze niet uit
    de rij zelf (die blijft zichtbaar tot een nieuwe resolve hem overschrijft),
    zodat 'waarom heropend' reconstrueerbaar blijft uit de context ernaast."""
    ensure_schema()
    existing = get_decision(did)
    if not existing:
        return None
    with get_conn() as conn:
        conn.execute("UPDATE decisions SET status='open', decided_at='' WHERE id=?", (did,))
    return get_decision(did)


def delete_decision(did: int) -> None:
    ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM decisions WHERE id = ?", (did,))

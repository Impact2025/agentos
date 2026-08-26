"""Besluiten — SQLite-schema. Zelfde aanpak als backend/domains/rituals/models.py."""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    title       TEXT NOT NULL,
    context     TEXT DEFAULT '',       -- de dilemma/aanleiding, waarom dit nu speelt
    options     TEXT DEFAULT '[]',     -- JSON-array van overwogen opties (optioneel)
    status      TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'besloten'
    decision    TEXT DEFAULT '',       -- de uitkomst, pas gevuld bij afronden
    reasoning   TEXT DEFAULT '',       -- waarom dit besluit, pas gevuld bij afronden
    deadline    TEXT DEFAULT '',       -- optioneel: YYYY-MM-DD
    created_at  TEXT NOT NULL,
    decided_at  TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_decisions_project_status ON decisions(project, status, created_at DESC);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True

"""
Meeting-notulen-schema.

Geen eigen transcriptie: er is geen audio-opnamepad of Teams-koppeling
beschikbaar in deze sessie. Vincent plakt een transcript (uit Teams/Zoom-
export, een dicteerapp, of getypte aantekeningen) en de LLM vat samen en
haalt actiepunten eruit — die actiepunten landen als taken in `crm_tasks`
(dezelfde tabel als de rest van de CRM, geen tweede takenlijst).

Schema leeft in dit domein zodat het zelfstandig te verwijderen is.
ensure_schema() is idempotent.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS meeting_notes (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    company_id     TEXT DEFAULT '',
    deal_id        TEXT DEFAULT '',
    meeting_date   TEXT DEFAULT '',
    transcript     TEXT NOT NULL,
    summary        TEXT DEFAULT '',
    action_items   TEXT DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'nieuw',
    created_at     TEXT NOT NULL,
    summarized_at  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_meeting_notes_company ON meeting_notes(company_id);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True

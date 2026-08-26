"""De Sparringpartner — SQLite-schema (single-user, zelfde aanpak als
backend/domains/rituals/models.py).

25 aug 2026: dit was eerst gebouwd tegen mijn-ondernemers-os (Next.js/Neon).
Bleek fout: Vincents dagelijkse ritueel-gewoonte was al eerder verhuisd naar
ImpactOS' eigen `rituals`-domein (zie de docstring daar) — de Neon-database had
een gat van twee maanden. De coach las dus de verlaten kopie. Hier, native
tegen dezelfde tabellen als Iris al ziet, is de enige plek waar dit klopt.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS coach_lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key     TEXT NOT NULL UNIQUE,   -- stabiele sleutel voor dedupe
    technique       TEXT NOT NULL,          -- grow|mi|oplossingsgericht|cgt|act|systemisch|strengths
    insight         TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.5,   -- Laplace-gladgestreken, 0-1
    times_confirmed INTEGER NOT NULL DEFAULT 0,
    times_disproven INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coach_energy_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    activity    TEXT NOT NULL,
    category    TEXT DEFAULT '',
    direction   TEXT NOT NULL,   -- 'gain' | 'cost'
    created_at  TEXT NOT NULL
);

-- Dedupe voor het proactieve WhatsApp-signaal: hetzelfde patroon appt
-- hoogstens één keer per dag, ook al draait de scheduler-job elke 2 uur.
CREATE TABLE IF NOT EXISTS coach_whatsapp_sent (
    pattern_key TEXT NOT NULL,
    date        TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    PRIMARY KEY (pattern_key, date)
);

CREATE INDEX IF NOT EXISTS idx_coach_lessons_active ON coach_lessons(active, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_coach_energy_date ON coach_energy_log(date DESC);
"""

_schema_ready = False


def _drop_stale_whatsapp_table(conn) -> None:
    """Eenmalige opruiming: eerder vandaag (25 aug 2026) bestond een gelijknamige
    tabel met kolom `date_string` (de eerste, verworpen opzet via mijn-
    ondernemers-os — zie coach/models.py-docstring). IF NOT EXISTS zou die
    verkeerde vorm laten staan. Leeg en zonder waarde, dus veilig te droppen."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(coach_whatsapp_sent)")}
    if cols and "date" not in cols:
        conn.execute("DROP TABLE coach_whatsapp_sent")


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        _drop_stale_whatsapp_table(conn)
        conn.executescript(DDL)
    _schema_ready = True

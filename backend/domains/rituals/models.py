"""
Persoonlijke rituelen — SQLite-schema.

Alleen `ritual_goals` (Robbins-stijl why/pain/pleasure) leeft nog hier lokaal —
géén onderdeel van backend/domains/goal/, dat is projectuitvoering
(LLM-decompositie → taken). Ochtend/avond/weekstart/weekreview/wins/focus zijn
verhuisd naar mijn-ondernemers-os (Next.js/Neon) als bron van waarheid; zie
backend/domains/rituals/service.py. De ritual_morning/ritual_evening/
ritual_weekly_start/ritual_weekly_review/ritual_wins/ritual_focus_sessions-
tabellen hieronder blijven bewust bestaan (ongebruikte, niet-verwijderde data
uit de periode vóór de bridge) — geen DDL-opruiming op Vincents lokale
database, dat is onnodig risico voor iets dat ook prima als dode data kan
blijven liggen. `ensure_schema()` is idempotent, aangeroepen bij eerste gebruik.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS ritual_morning (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
    intentie        TEXT DEFAULT '',
    affirmatie      TEXT DEFAULT '',
    dankbaarheid    TEXT DEFAULT '[]',      -- JSON-array van 3 strings
    energy_level    INTEGER DEFAULT 7,
    sleep_quality   INTEGER DEFAULT 7,
    sleep_time      TEXT DEFAULT '',
    wake_time       TEXT DEFAULT '',
    focus_blok1     TEXT DEFAULT '{}',      -- JSON {onderwerp, doel}
    focus_blok2     TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ritual_evening (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL UNIQUE,
    what_went_well  TEXT DEFAULT '',
    biggest_win     TEXT DEFAULT '',
    what_learned    TEXT DEFAULT '',
    challenges      TEXT DEFAULT '',
    energy_level    INTEGER DEFAULT 5,
    tomorrow_top3   TEXT DEFAULT '[]',      -- JSON-array van 3 strings
    gratitude       TEXT DEFAULT '',
    adhd_scores     TEXT DEFAULT '{}',      -- JSON {symptoom: 0-3}, optioneel
    focus_check     TEXT DEFAULT '[]',      -- JSON [{onderwerp, done}] — terugkoppeling op de focusblokken van dezelfde ochtend
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ritual_weekly_start (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            INTEGER NOT NULL,
    week_number     INTEGER NOT NULL,
    week_intention  TEXT DEFAULT '',
    main_goals      TEXT DEFAULT '[]',      -- JSON-array
    focus_areas     TEXT DEFAULT '{}',      -- JSON {work,health,relationships,personal}
    learning_goal   TEXT DEFAULT '',
    obstacles       TEXT DEFAULT '',
    success_metrics TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    UNIQUE(year, week_number)
);

CREATE TABLE IF NOT EXISTS ritual_weekly_review (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    year              INTEGER NOT NULL,
    week_number       INTEGER NOT NULL,
    wins              TEXT DEFAULT '[]',    -- JSON-array
    challenges        TEXT DEFAULT '',
    learnings         TEXT DEFAULT '',
    productivity_score INTEGER DEFAULT 7,
    energy_score      INTEGER DEFAULT 7,
    carry_forward     TEXT DEFAULT '',
    leave_behind      TEXT DEFAULT '',
    growth_moment     TEXT DEFAULT '',
    what_gave         TEXT DEFAULT '',
    what_learned      TEXT DEFAULT '',
    how_contributed   TEXT DEFAULT '',
    how_make_better   TEXT DEFAULT '',
    created_at        TEXT NOT NULL,
    UNIQUE(year, week_number)
);

CREATE TABLE IF NOT EXISTS ritual_wins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'personal',  -- business|personal|health|learning
    impact_level  INTEGER NOT NULL DEFAULT 1,          -- 1-5
    date          TEXT NOT NULL,
    tags          TEXT DEFAULT '[]',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ritual_focus_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    goal        TEXT DEFAULT '',
    completed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- Koppelt een ImpactOS-project (canonical/squashed via shared/projects.py) aan het
-- bridge-token van diens mijn-ondernemers-os-organisatie (Fase 2 deel 1). Plaintext, niet
-- gehasht — moet uitgaand verstuurd worden, zelfde niveau als COACH_BRIDGE_TOKEN in .env.
-- Puur lokaal, nooit gesynchroniseerd. Zie scripts/link_client_bridge_token.py.
CREATE TABLE IF NOT EXISTS project_bridge_tokens (
    project_slug  TEXT PRIMARY KEY,
    token         TEXT NOT NULL,
    label         TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ritual_goals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    description       TEXT DEFAULT '',
    why               TEXT DEFAULT '',
    pain_if_not       TEXT DEFAULT '',
    pleasure_if_done  TEXT DEFAULT '',
    next_actions      TEXT DEFAULT '[]',
    category          TEXT NOT NULL DEFAULT 'personal',  -- business|health|relationships|personal
    progress          INTEGER NOT NULL DEFAULT 0,          -- 0-100
    completed         INTEGER NOT NULL DEFAULT 0,
    deadline          TEXT DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ritual_wins_date ON ritual_wins(date DESC);
CREATE INDEX IF NOT EXISTS idx_ritual_focus_date ON ritual_focus_sessions(date DESC);
CREATE INDEX IF NOT EXISTS idx_ritual_goals_completed ON ritual_goals(completed, created_at DESC);
"""

_schema_ready = False

# Idempotente ALTER TABLE's voor kolommen die na de eerste versie zijn toegevoegd
# (zelfde aanpak als radar/models.py) — installaties van vóór de migratie missen
# de kolom anders stilzwijgend en `get_evening` zou op een KeyError stuk lopen.
_MIGRATIES = {
    "ritual_evening": [
        ("focus_check", "ALTER TABLE ritual_evening ADD COLUMN focus_check TEXT DEFAULT '[]'"),
    ],
}


def _migrate(conn) -> None:
    for tabel, kolommen in _MIGRATIES.items():
        bestaand = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabel})")}
        for naam, ddl in kolommen:
            if naam not in bestaand:
                conn.execute(ddl)


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
        _migrate(conn)
    _schema_ready = True


def get_project_bridge_token(project_slug: str) -> "str | None":
    """Token voor een gekoppeld klant-project, of None als er geen koppeling is.
    `project_slug` moet al gesquasht zijn (shared.projects.squash_project) — dit doet zelf
    geen normalisatie, om niet twee plekken te hebben die kunnen uiteenlopen."""
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT token FROM project_bridge_tokens WHERE project_slug = ?", (project_slug,)
        ).fetchone()
    return row["token"] if row else None


def has_project_bridge_token(project_slug: str) -> bool:
    return get_project_bridge_token(project_slug) is not None

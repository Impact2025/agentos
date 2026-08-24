"""
Persoonlijke rituelen — SQLite-schema (ochtend/avond, weekstart/weekreview,
wins, focus-sessies, persoonlijke doelen).

Overgezet uit D:\\apps\\impactreis3\\mijn-ondernemers-os (Next.js/Neon), waar
ritueel-*voltooiing* alleen in localStorage stond en dus onzichtbaar was voor
Iris/ImpactOS. Hier leeft alles in SQLite (single-user, geen user_id) zodat het
domein — zoals de rest van backend/domains/ — zelfstandig te verwijderen of
verplaatsen is. `ensure_schema()` is idempotent, aangeroepen bij eerste gebruik.

`ritual_goals` is bewust een eigen tabel en géén onderdeel van
backend/domains/goal/: die laatste is projectuitvoering (LLM-decompositie →
taken), dit zijn Robbins-stijl persoonlijke doelen (why/pain/pleasure).
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

"""
Mission Radar — SQLite-schema voor de watchlist en gevonden signalen.

Twee tabellen:
  radar_watchlist  wat we monitoren: concurrenten (site:), keywords of RSS-feeds
  radar_signals    wat de Sky Scanner vond: trending content met Signal Score +
                   AI-gegenereerde hook/invalshoek/titels (nooit gekopieerde titels)

Het schema leeft bewust in dit domein (i.p.v. shared/database.py) zodat het
radar-domein zelfstandig te verwijderen/verplaatsen is. ensure_schema() is
idempotent en wordt aangeroepen bij het eerste gebruik van de service.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS radar_watchlist (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'keyword',  -- keyword | competitor | rss
    value       TEXT NOT NULL,                    -- zoekwoord, domein of feed-url
    active      INTEGER NOT NULL DEFAULT 1,
    last_scanned_at TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_signals (
    id              TEXT PRIMARY KEY,
    watch_id        TEXT DEFAULT '',
    project         TEXT NOT NULL DEFAULT '',
    keyword         TEXT DEFAULT '',              -- het gemonitorde keyword/domein
    title           TEXT NOT NULL,                -- originele titel van de bron
    url             TEXT NOT NULL,
    source          TEXT DEFAULT '',              -- reddit | youtube | news | blog | rss | overig
    snippet         TEXT DEFAULT '',
    published_days_ago INTEGER DEFAULT -1,
    signal_score    REAL DEFAULT 0,               -- 0-100 virality/relevantie
    ai_hook         TEXT DEFAULT '',              -- LLM: sterke hook
    ai_angle        TEXT DEFAULT '',              -- LLM: unieke invalshoek
    ai_titles       TEXT DEFAULT '[]',            -- LLM: JSON-array titelvoorstellen
    ai_match_score  INTEGER DEFAULT -1,           -- LLM: match met profiel 0-100
    status          TEXT NOT NULL DEFAULT 'new',  -- new | targeted | converted | dismissed
    obsidian_path   TEXT DEFAULT '',
    scanned_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_radar_signals_url ON radar_signals(project, url);
CREATE INDEX IF NOT EXISTS idx_radar_signals_status ON radar_signals(project, status, signal_score DESC);
CREATE INDEX IF NOT EXISTS idx_radar_watchlist_project ON radar_watchlist(project, active);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True


def _list_ready_converted_listicles():
    """Vind AEO-listicle-taken die klaar zijn om naar de Wachtrij gestaged te
    worden: de taak heeft status 'done' (Conveyor keurde 'm goed) én het
    signaal is al 'converted' (aeo_attack heeft de keten aangemaakt).

    Retourneert een lijst van (signal_id, task) tuples. De workspace-basismap
    van de taak is 'radar-aeo-<slug>' (aangemaakt door aeo_attack); we matchen
    het signaal via dezelfde slug in radar_signals (obsidian_path) of via de
    title-slug. Robuust alternatief: als er geen obsidian_path is, zoeken we
    het 'converted'-signaal waarvan de AEO-listicle-task dezelfde base draagt.
    """
    from ...shared.database import get_conn as _gc

    out = []
    with _gc() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'done' "
            "AND workspace_path LIKE 'radar-aeo-%' "
            "AND workspace_path LIKE '%-listicle.md'"
        ).fetchall()
        for t in rows:
            task = dict(t)
            wp = task.get("workspace_path") or ""
            base = wp.rsplit("/", 1)[0] if "/" in wp else ""
            if not base or not base.startswith("radar-aeo-"):
                continue
            # 'radar-aeo-<slug>' → '<slug>'
            slug = base[len("radar-aeo-"):]
            sig = conn.execute(
                "SELECT id FROM radar_signals "
                "WHERE status = 'converted' "
                "AND (obsidian_path LIKE ? OR obsidian_path = '') "
                "ORDER BY created_at DESC LIMIT 1",
                (f"%/{slug}.md",),
            ).fetchone()
            if sig:
                out.append((sig["id"], task))
    return out

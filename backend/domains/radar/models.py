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
    type        TEXT NOT NULL DEFAULT 'keyword',  -- keyword | competitor | rss | youtube | reddit | brand_mention | linkedin_signal
    value       TEXT NOT NULL,                    -- zoekwoord, domein of feed-url | LinkedIn-account (handle/URL)
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


# Kolommen die ná de eerste versie zijn bijgekomen. Idempotent per kolom,
# zelfde patroon als `shared/database._migrate`.
_MIGRATIES = {
    "radar_signals": (
        # De signaalpoort (`quality.py`, 3 aug 2026): waaróm een gevonden stuk
        # geen signaal is. Bewaard in plaats van weggegooid, zodat de poort
        # controleerbaar blijft en een geweerd signaal alsnog op te pakken is.
        ("filter_reason", "ALTER TABLE radar_signals ADD COLUMN filter_reason TEXT DEFAULT ''"),
        ("filter_label",  "ALTER TABLE radar_signals ADD COLUMN filter_label TEXT DEFAULT ''"),
        ("filter_detail", "ALTER TABLE radar_signals ADD COLUMN filter_detail TEXT DEFAULT ''"),
        # Onderscheidt "Toch oppakken" (een mens koos bewust voor dit signaal,
        # ondanks het gate-oordeel) van "nog nooit door de huidige poort
        # herbeoordeeld" — allebei zien er hetzelfde uit (status='new',
        # filter_reason='') zónder deze vlag. Nodig sinds `_reconcile_quality`
        # (9 aug 2026) rijen met een verouderd/leeg oordeel herbeoordeelt: zonder
        # dit zou zo'n herbeoordeling een bewust herstelde 'Toch oppakken'-rij
        # bij de eerstvolgende poort-verbetering alsnog terugzetten op
        # 'uitgefilterd' — de override zou dan nooit blijvend zijn.
        # `signal_kind` onderscheidt de twee Radar-stromen: 'trend' (de gewone
        # concurrentie/keyword-scan) en 'linkedin_signal' (een hand-raiser die
        # een bepaalde account engageerde). De prospecting-brug kijkt op dit
        # veld zodat hij alleen échte hand-raisers oppikt, niet trend-signalen.
        ("signal_kind", "ALTER TABLE radar_signals ADD COLUMN signal_kind TEXT DEFAULT 'trend'"),
        # Voor een hand-raiser: het LinkedIn-profiel waarop we hem vonden, en
        # de account die hij engageerde (de 'watcher' waaruit hij kwam).
        ("source_handle", "ALTER TABLE radar_signals ADD COLUMN source_handle TEXT DEFAULT ''"),
        ("watcher_account", "ALTER TABLE radar_signals ADD COLUMN watcher_account TEXT DEFAULT ''"),
        # Engagement-context: welk signaal hij gaf (liked / commented / posted).
        # Bepaalt mede de prioriteit in de prospecting-brug (comment > like).
        ("engagement", "ALTER TABLE radar_signals ADD COLUMN engagement TEXT DEFAULT ''"),
        # Of de hand-raiser al gebridged is naar een prospecting-lead (idempotent).
        ("bridged_lead_id", "ALTER TABLE radar_signals ADD COLUMN bridged_lead_id TEXT DEFAULT ''"),
        # De vlag uit de toelichting hierboven bij filter_reason (9 aug 2026):
        # onderscheidt "Toch oppakken" van "nog nooit door de huidige poort
        # herbeoordeeld". Was hier alleen als commentaar gedocumenteerd, nooit
        # als kolom aangemaakt — elke aanroep van quality_review_batch faalde
        # daardoor hard op 'no such column'.
        ("quality_reviewed", "ALTER TABLE radar_signals ADD COLUMN quality_reviewed INTEGER DEFAULT 0"),
    ),
    "radar_watchlist": (
        # `last_scanned_at` werd alleen gezet als er iets wérd opgeslagen. Daardoor
        # waren "gescand, niets nieuws", "gescand, gefaald" en "nooit gescand" niet
        # van elkaar te onderscheiden — vijf projecten stonden zeven dagen op
        # 27 juli zonder dat iemand kon zien of dat rust of storing was.
        ("last_status", "ALTER TABLE radar_watchlist ADD COLUMN last_status TEXT DEFAULT ''"),
        ("last_error",  "ALTER TABLE radar_watchlist ADD COLUMN last_error TEXT DEFAULT ''"),
        ("last_found",  "ALTER TABLE radar_watchlist ADD COLUMN last_found INTEGER DEFAULT 0"),
        ("scan_count",  "ALTER TABLE radar_watchlist ADD COLUMN scan_count INTEGER DEFAULT 0"),
        ("signal_count", "ALTER TABLE radar_watchlist ADD COLUMN signal_count INTEGER DEFAULT 0"),
    ),
}


def _migrate(conn) -> None:
    for tabel, kolommen in _MIGRATIES.items():
        bestaand = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabel})")}
        for naam, ddl in kolommen:
            if naam not in bestaand:
                conn.execute(ddl)
    # Projectnamen zijn hoofdletterongevoelig bedoeld — `add_watch` verkleint ze
    # al — maar er stonden 'WeAreImpact' (539 signalen) naast 'weareimpact' (79)
    # en 'Bijeen' naast 'bijeen'. `list_watch` matcht exact op kleine letters,
    # dus de hoofdlettervarianten waren via de UI onbereikbaar: watches die wél
    # scanden maar in geen enkel overzicht stonden.
    conn.execute("UPDATE radar_watchlist SET project = lower(project) "
                 "WHERE project != lower(project)")
    # Op signals ligt een UNIQUE index (project, url). Stond dezelfde URL onder
    # beide schrijfwijzen, dan botsen ze bij het verkleinen; ruim die eerst op
    # en houd de rij met de meeste inhoud (een opgepakt of verrijkt signaal
    # weegt zwaarder dan een kale dubbelganger).
    conn.execute(
        """DELETE FROM radar_signals WHERE id IN (
               SELECT id FROM (
                   SELECT id, ROW_NUMBER() OVER (
                       PARTITION BY lower(project), url
                       ORDER BY (status != 'new') DESC, ai_match_score DESC, created_at
                   ) AS rang
                   FROM radar_signals
               ) WHERE rang > 1
           )"""
    )
    conn.execute("UPDATE radar_signals SET project = lower(project) "
                 "WHERE project != lower(project)")


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
        _migrate(conn)
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

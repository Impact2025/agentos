import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    agent       TEXT NOT NULL DEFAULT 'claude',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT 'openrouter/meta-llama/llama-3.1-8b-instruct',
    system_prompt TEXT DEFAULT '',
    memory_session TEXT DEFAULT '',
    mcp_servers   TEXT DEFAULT '[]',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    description       TEXT DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'todo',
    agent             TEXT DEFAULT NULL,
    assigned_agent_id INTEGER DEFAULT NULL REFERENCES agent_profiles(id) ON DELETE SET NULL,
    position          INTEGER DEFAULT 0,
    workspace_path    TEXT DEFAULT '',
    result            TEXT DEFAULT '',
    error             TEXT DEFAULT '',
    started_at        TEXT DEFAULT '',
    finished_at       TEXT DEFAULT '',
    duration_ms       INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Demand Engine: portfolio van sites (GSC-property + publicatie-config per site).
CREATE TABLE IF NOT EXISTS sites (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    base_url         TEXT DEFAULT '',
    gsc_property     TEXT DEFAULT '',
    publish_api_url  TEXT DEFAULT '',
    publish_api_key  TEXT DEFAULT '',
    default_author   TEXT DEFAULT '',
    linkedin_token   TEXT DEFAULT '',
    linkedin_user_urn TEXT DEFAULT '',
    facebook_page_id     TEXT DEFAULT '',
    facebook_page_token  TEXT DEFAULT '',
    instagram_business_id TEXT DEFAULT '',
    twitter_api_key       TEXT DEFAULT '',
    twitter_api_secret    TEXT DEFAULT '',
    twitter_access_token  TEXT DEFAULT '',
    twitter_access_secret TEXT DEFAULT '',
    auto_content_enabled  INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL
);

-- Demand Engine: 'striking distance'-zoekwoordkansen uit Search Console.
CREATE TABLE IF NOT EXISTS opportunities (
    id                TEXT PRIMARY KEY,
    site_id           TEXT NOT NULL,
    query             TEXT NOT NULL,
    clicks            INTEGER DEFAULT 0,
    impressions       INTEGER DEFAULT 0,
    ctr               REAL DEFAULT 0,
    position          REAL DEFAULT 0,
    opportunity_score REAL DEFAULT 0,
    action            TEXT DEFAULT '',
    angle             TEXT DEFAULT '',
    rationale         TEXT DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'new',
    scanned_at        TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_opportunities_site ON opportunities(site_id, status, opportunity_score);

CREATE TABLE IF NOT EXISTS leads (
    id            TEXT PRIMARY KEY,
    org_name      TEXT NOT NULL,
    website       TEXT DEFAULT '',
    contacts      TEXT DEFAULT '[]',
    summary       TEXT DEFAULT '',
    relevance     TEXT DEFAULT 'gemiddeld',
    status        TEXT NOT NULL DEFAULT 'prospect',
    search_query  TEXT DEFAULT '',
    obsidian_path TEXT DEFAULT '',
    -- NAW-velden (verrijking via scraper + KvK)
    phone         TEXT DEFAULT '',
    email         TEXT DEFAULT '',
    address       TEXT DEFAULT '',
    city          TEXT DEFAULT '',
    postal_code   TEXT DEFAULT '',
    kvk_number    TEXT DEFAULT '',
    lead_type       TEXT DEFAULT 'overig',
    enriched_at     TEXT DEFAULT '',
    score           INTEGER DEFAULT 50,
    tags            TEXT DEFAULT '[]',
    -- Hunter.io verrijkingsvelden
    hunter_verified INTEGER DEFAULT 0,
    email_status    TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Opdrachten-zoekagent: interim-vacatures gevonden via LinkedIn/Freelance.nl/Indeed/BMC/overig.
CREATE TABLE IF NOT EXISTS vacancies (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    organization  TEXT DEFAULT '',
    url           TEXT NOT NULL,
    source        TEXT DEFAULT 'overig',
    role_query    TEXT DEFAULT '',
    location      TEXT DEFAULT '',
    hours_text    TEXT DEFAULT '',
    contract_type TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    fit_score     INTEGER DEFAULT 50,
    fit_rationale TEXT DEFAULT '',
    posted_days_ago INTEGER DEFAULT -1,
    status        TEXT NOT NULL DEFAULT 'new',
    search_query  TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Mission Control journey-recorder: elke agent-run + de stappen erin.
CREATE TABLE IF NOT EXISTS journeys (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    agent        TEXT DEFAULT '',
    model        TEXT DEFAULT '',
    user_message TEXT DEFAULT '',
    final_text   TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'running',
    error        TEXT DEFAULT '',
    started_at   TEXT NOT NULL,
    finished_at  TEXT DEFAULT '',
    duration_ms  INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS journey_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    type        TEXT NOT NULL,
    name        TEXT DEFAULT '',
    content     TEXT DEFAULT '',
    is_error    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (journey_id) REFERENCES journeys(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_journeys_session ON journeys(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_journey_events_journey ON journey_events(journey_id, seq);

-- Delegate-laag: parallelle subagent-batches (de 'Boss order').
CREATE TABLE IF NOT EXISTS delegations (
    id           TEXT PRIMARY KEY,
    objective    TEXT NOT NULL,
    session_id   TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'running',  -- running | done | partial | failed
    worker_count INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    finished_at  TEXT DEFAULT ''
);

-- Delegate-laag: de individuele parallelle workers binnen een batch.
CREATE TABLE IF NOT EXISTS subagents (
    id             TEXT PRIMARY KEY,
    delegation_id  TEXT NOT NULL,
    position       INTEGER DEFAULT 0,
    role           TEXT NOT NULL,
    goal           TEXT DEFAULT '',
    profile_id     INTEGER DEFAULT NULL REFERENCES agent_profiles(id) ON DELETE SET NULL,
    status         TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | error
    result         TEXT DEFAULT '',
    error          TEXT DEFAULT '',
    workspace_path TEXT DEFAULT '',
    started_at     TEXT DEFAULT '',
    finished_at    TEXT DEFAULT '',
    duration_ms    INTEGER DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (delegation_id) REFERENCES delegations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subagents_delegation ON subagents(delegation_id, position);

-- Loop Engineering: maker-agent + beoordelaar-agent draaien in een lus tot een
-- ingestelde kwaliteitsscore is gehaald (of het max aantal iteraties bereikt is).
CREATE TABLE IF NOT EXISTS loops (
    id                  TEXT PRIMARY KEY,
    objective           TEXT NOT NULL,
    session_id          TEXT DEFAULT '',
    maker_profile_id    INTEGER DEFAULT NULL REFERENCES agent_profiles(id) ON DELETE SET NULL,
    reviewer_profile_id INTEGER DEFAULT NULL REFERENCES agent_profiles(id) ON DELETE SET NULL,
    threshold           INTEGER DEFAULT 85,
    max_iterations      INTEGER DEFAULT 4,
    status              TEXT NOT NULL DEFAULT 'running',  -- running | passed | stopped | failed
    best_score          INTEGER DEFAULT -1,
    best_output         TEXT DEFAULT '',
    iterations_run      INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT DEFAULT ''
);

-- Loop Engineering: elke maker→beoordelaar-ronde binnen één lus.
CREATE TABLE IF NOT EXISTS loop_iterations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id      TEXT NOT NULL,
    iteration    INTEGER NOT NULL,
    draft        TEXT DEFAULT '',
    score        INTEGER DEFAULT 0,
    feedback     TEXT DEFAULT '',
    passed       INTEGER DEFAULT 0,
    duration_ms  INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (loop_id) REFERENCES loops(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_loop_iterations_loop ON loop_iterations(loop_id, iteration);

-- Netlify publisher: gepubliceerde artikelen per site (bron voor de volledige
-- site-rebuild bij elke deploy). Eén rij per (site_id, slug).
CREATE TABLE IF NOT EXISTS published_pages (
    id          TEXT PRIMARY KEY,
    site_id     TEXT NOT NULL,
    slug        TEXT NOT NULL,
    title       TEXT DEFAULT '',
    html        TEXT DEFAULT '',
    url         TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_published_pages_site_slug ON published_pages(site_id, slug);

-- Content-wachtrij: 2x/week auto-gegenereerde blog + social-copy, wacht op
-- menselijke goedkeuring voordat er iets live/gepost wordt (nooit auto-publish).
CREATE TABLE IF NOT EXISTS content_jobs (
    id           TEXT PRIMARY KEY,
    site_id      TEXT NOT NULL,
    title        TEXT NOT NULL,
    keyword      TEXT DEFAULT '',
    rationale    TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending_review',  -- pending_review | approved | rejected | published
    blog_html    TEXT DEFAULT '',
    seo_score    REAL DEFAULT 0,
    social_copy  TEXT DEFAULT '{}',   -- JSON: {"linkedin": "...", "facebook": "...", "instagram": "...", "twitter": "..."}
    image_path   TEXT DEFAULT '',
    slug         TEXT DEFAULT '',
    publish_result TEXT DEFAULT '{}',  -- JSON: netlify-url, gsc-status, per-platform post-resultaten
    error        TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    reviewed_at  TEXT DEFAULT '',
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_jobs_site_status ON content_jobs(site_id, status, created_at DESC);

-- Outlook / Microsoft Graph integratie
CREATE TABLE IF NOT EXISTS outlook_tokens (
    id          INTEGER PRIMARY KEY,
    account_id  TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL,
    name        TEXT DEFAULT '',
    token_cache TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outlook_emails (
    id              TEXT PRIMARY KEY,
    subject         TEXT DEFAULT '',
    from_email      TEXT DEFAULT '',
    from_name       TEXT DEFAULT '',
    to_email        TEXT DEFAULT '',
    received_at     TEXT NOT NULL,
    body_preview    TEXT DEFAULT '',
    body_html       TEXT DEFAULT '',
    is_read         INTEGER DEFAULT 0,
    is_replied      INTEGER DEFAULT 0,
    folder          TEXT DEFAULT 'inbox',
    importance      TEXT DEFAULT 'normal',
    has_attachments INTEGER DEFAULT 0,
    triage_label    TEXT DEFAULT '',
    priority        INTEGER DEFAULT 50,
    ai_summary      TEXT DEFAULT '',
    ai_action       TEXT DEFAULT '',
    reply_hint      TEXT DEFAULT '',
    triaged_at      TEXT DEFAULT '',
    lead_id         TEXT DEFAULT '',
    thread_id       TEXT DEFAULT '',
    synced_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outlook_emails_received ON outlook_emails(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_outlook_emails_label    ON outlook_emails(triage_label, priority DESC);
CREATE INDEX IF NOT EXISTS idx_outlook_emails_unread   ON outlook_emails(is_read, received_at DESC);

-- Goal Mode: langetermijndoelen met autonome decompositie & executie
CREATE TABLE IF NOT EXISTS goals (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    objective       TEXT NOT NULL,
    project         TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',
    phase_count     INTEGER DEFAULT 0,
    task_count      INTEGER DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    failed_tasks    INTEGER DEFAULT 0,
    current_phase   INTEGER DEFAULT 0,
    current_task    TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    started_at      TEXT DEFAULT '',
    finished_at     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS goal_phases (
    id          TEXT PRIMARY KEY,
    goal_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    ord         INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed | skipped
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS goal_tasks (
    id              TEXT PRIMARY KEY,
    goal_id         TEXT NOT NULL,
    phase_id        TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    skill           TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | ready | running | completed | failed | skipped
    dependencies    TEXT DEFAULT '[]',  -- JSON array van task-id's
    retry_count     INTEGER DEFAULT 0,
    max_retries     INTEGER DEFAULT 3,
    result          TEXT DEFAULT '',
    error           TEXT DEFAULT '',
    workspace_path  TEXT DEFAULT '',
    started_at      TEXT DEFAULT '',
    finished_at     TEXT DEFAULT '',
    duration_ms     INTEGER DEFAULT 0,
    ord             INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE,
    FOREIGN KEY (phase_id) REFERENCES goal_phases(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_goal_phases_goal ON goal_phases(goal_id, ord);
CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal  ON goal_tasks(goal_id, phase_id, status);
CREATE INDEX IF NOT EXISTS idx_goal_tasks_phase ON goal_tasks(goal_id, phase_id, status);

-- SEO Optimizer: verbeterkansen op bestaande content (interne links, CTR, refresh)
CREATE TABLE IF NOT EXISTS seo_suggestions (
    id          TEXT PRIMARY KEY,
    site_id     TEXT NOT NULL,
    type        TEXT NOT NULL,              -- internal_link | ctr | refresh
    page        TEXT DEFAULT '',            -- doel-URL waar de kans over gaat
    query       TEXT DEFAULT '',            -- belangrijkste zoekwoord (indien bekend)
    title       TEXT DEFAULT '',            -- korte omschrijving voor de UI
    data        TEXT DEFAULT '{}',          -- JSON: type-specifieke details
    score       REAL DEFAULT 0,             -- prioriteit (hoger = belangrijker)
    status      TEXT NOT NULL DEFAULT 'new',-- new | done | dismissed
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_seo_suggestions_site ON seo_suggestions(site_id, type, status);

CREATE TABLE IF NOT EXISTS case_studies (
    id          TEXT PRIMARY KEY,
    site_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT DEFAULT '',            -- korte samenvatting voor prompts/matching
    body        TEXT DEFAULT '',            -- harde data: cijfers, resultaten, verhaal
    tags        TEXT DEFAULT '',            -- komma-gescheiden trefwoorden voor matching
    source_url  TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',  -- active | archived
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_case_studies_site ON case_studies(site_id, status);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(DDL)
        _migrate(conn)


def _migrate(conn) -> None:
    """Idempotente kolom- en status-migraties voor bestaande databases."""
    task_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "assigned_agent_id" not in task_cols:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN assigned_agent_id INTEGER "
            "REFERENCES agent_profiles(id) ON DELETE SET NULL"
        )
    if "workspace_path" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN workspace_path TEXT DEFAULT ''")
    # Conveyor-resultaat + telemetrie zodat output nooit verloren gaat.
    for col, ddl in (
        ("result", "ALTER TABLE tasks ADD COLUMN result TEXT DEFAULT ''"),
        ("error", "ALTER TABLE tasks ADD COLUMN error TEXT DEFAULT ''"),
        ("started_at", "ALTER TABLE tasks ADD COLUMN started_at TEXT DEFAULT ''"),
        ("finished_at", "ALTER TABLE tasks ADD COLUMN finished_at TEXT DEFAULT ''"),
        ("duration_ms", "ALTER TABLE tasks ADD COLUMN duration_ms INTEGER DEFAULT 0"),
    ):
        if col not in task_cols:
            conn.execute(ddl)

    # NAW-kolommen voor leads (verrijking fase)
    lead_cols = {row["name"] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    for col, ddl in (
        ("phone",            "ALTER TABLE leads ADD COLUMN phone TEXT DEFAULT ''"),
        ("email",            "ALTER TABLE leads ADD COLUMN email TEXT DEFAULT ''"),
        ("address",          "ALTER TABLE leads ADD COLUMN address TEXT DEFAULT ''"),
        ("city",             "ALTER TABLE leads ADD COLUMN city TEXT DEFAULT ''"),
        ("postal_code",      "ALTER TABLE leads ADD COLUMN postal_code TEXT DEFAULT ''"),
        ("kvk_number",       "ALTER TABLE leads ADD COLUMN kvk_number TEXT DEFAULT ''"),
        ("lead_type",        "ALTER TABLE leads ADD COLUMN lead_type TEXT DEFAULT 'overig'"),
        ("enriched_at",      "ALTER TABLE leads ADD COLUMN enriched_at TEXT DEFAULT ''"),
        ("score",            "ALTER TABLE leads ADD COLUMN score INTEGER DEFAULT 50"),
        ("tags",             "ALTER TABLE leads ADD COLUMN tags TEXT DEFAULT '[]'"),
        ("hunter_verified",  "ALTER TABLE leads ADD COLUMN hunter_verified INTEGER DEFAULT 0"),
        ("email_status",     "ALTER TABLE leads ADD COLUMN email_status TEXT DEFAULT ''"),
        # Acquisitie-formule: funnel-tijdstempels (eenmalig gezet — de basis
        # voor conversiemeting input → output) + outreach-concept achter de gate.
        ("contacted_at",     "ALTER TABLE leads ADD COLUMN contacted_at TEXT DEFAULT ''"),
        ("replied_at",       "ALTER TABLE leads ADD COLUMN replied_at TEXT DEFAULT ''"),
        ("call_at",          "ALTER TABLE leads ADD COLUMN call_at TEXT DEFAULT ''"),
        ("won_at",           "ALTER TABLE leads ADD COLUMN won_at TEXT DEFAULT ''"),
        ("lost_at",          "ALTER TABLE leads ADD COLUMN lost_at TEXT DEFAULT ''"),
        ("outreach_subject", "ALTER TABLE leads ADD COLUMN outreach_subject TEXT DEFAULT ''"),
        ("outreach_draft",   "ALTER TABLE leads ADD COLUMN outreach_draft TEXT DEFAULT ''"),
        ("outreach_drafted_at", "ALTER TABLE leads ADD COLUMN outreach_drafted_at TEXT DEFAULT ''"),
    ):
        if col not in lead_cols:
            conn.execute(ddl)

    # Status-funnel migratie: oude generieke waarden → nieuwe funnel-stappen
    _STATUS_MAP = {
        "prospect":  "new",
        "benaderd":  "contacted",
        "gesprek":   "contacted",
        "klant":     "replied",
    }
    for old, new in _STATUS_MAP.items():
        conn.execute("UPDATE leads SET status = ? WHERE status = ?", (new, old))

    journey_cols = {row["name"] for row in conn.execute("PRAGMA table_info(journeys)").fetchall()}
    if journey_cols and "total_tokens" not in journey_cols:
        conn.execute("ALTER TABLE journeys ADD COLUMN total_tokens INTEGER DEFAULT 0")

    profile_cols = {row["name"] for row in conn.execute("PRAGMA table_info(agent_profiles)").fetchall()}
    if "memory_session" not in profile_cols:
        conn.execute("ALTER TABLE agent_profiles ADD COLUMN memory_session TEXT DEFAULT ''")
    if "mcp_servers" not in profile_cols:
        conn.execute("ALTER TABLE agent_profiles ADD COLUMN mcp_servers TEXT DEFAULT '[]'")

    # Outlook-emails: nieuwe kolommen (idempotent)
    oe_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='outlook_emails'"
    ).fetchone()
    if oe_exists:
        oe_cols = {row["name"] for row in conn.execute("PRAGMA table_info(outlook_emails)").fetchall()}
        for col, ddl in (
            ("to_email",    "ALTER TABLE outlook_emails ADD COLUMN to_email TEXT DEFAULT ''"),
            ("is_replied",  "ALTER TABLE outlook_emails ADD COLUMN is_replied INTEGER DEFAULT 0"),
            ("reply_hint",  "ALTER TABLE outlook_emails ADD COLUMN reply_hint TEXT DEFAULT ''"),
        ):
            if col not in oe_cols:
                conn.execute(ddl)

    # Goal Mode: ord kolom voor task volgorde (idempotent)
    gt_cols = {row["name"] for row in conn.execute("PRAGMA table_info(goal_tasks)").fetchall()}
    if "ord" not in gt_cols:
        conn.execute("ALTER TABLE goal_tasks ADD COLUMN ord INTEGER DEFAULT 0")
    gp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(goal_phases)").fetchall()} if \
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='goal_phases'").fetchone() else set()
    if gp_cols and "ord" not in gp_cols:
        conn.execute("ALTER TABLE goal_phases ADD COLUMN ord INTEGER DEFAULT 0")
    rows = conn.execute("SELECT id, status FROM tasks WHERE status NOT IN ('todo','ready','running','done','awaiting_approval')").fetchall()
    for row in rows:
        mapping = {
            "backlog": "todo",
            "in_progress": "running",
        }
        new_status = mapping.get(row["status"], "todo")
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, row["id"]))

    # LinkedIn per-site tokens
    site_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sites)").fetchall()}
    if "linkedin_token" not in site_cols:
        conn.execute("ALTER TABLE sites ADD COLUMN linkedin_token TEXT DEFAULT ''")
    if "linkedin_user_urn" not in site_cols:
        conn.execute("ALTER TABLE sites ADD COLUMN linkedin_user_urn TEXT DEFAULT ''")

    # Social publisher: Facebook/Instagram/X per-site credentials + auto-content toggle
    for col, ddl in (
        ("facebook_page_id",      "ALTER TABLE sites ADD COLUMN facebook_page_id TEXT DEFAULT ''"),
        ("facebook_page_token",   "ALTER TABLE sites ADD COLUMN facebook_page_token TEXT DEFAULT ''"),
        ("instagram_business_id", "ALTER TABLE sites ADD COLUMN instagram_business_id TEXT DEFAULT ''"),
        ("twitter_api_key",       "ALTER TABLE sites ADD COLUMN twitter_api_key TEXT DEFAULT ''"),
        ("twitter_api_secret",    "ALTER TABLE sites ADD COLUMN twitter_api_secret TEXT DEFAULT ''"),
        ("twitter_access_token",  "ALTER TABLE sites ADD COLUMN twitter_access_token TEXT DEFAULT ''"),
        ("twitter_access_secret", "ALTER TABLE sites ADD COLUMN twitter_access_secret TEXT DEFAULT ''"),
        ("auto_content_enabled",  "ALTER TABLE sites ADD COLUMN auto_content_enabled INTEGER DEFAULT 0"),
        ("external_db_url",       "ALTER TABLE sites ADD COLUMN external_db_url TEXT DEFAULT ''"),
        ("ga4_property_id",       "ALTER TABLE sites ADD COLUMN ga4_property_id TEXT DEFAULT ''"),
        # Kennisbank (information gain) + batch + directe indexering
        ("profile",            "ALTER TABLE sites ADD COLUMN profile TEXT DEFAULT ''"),
        ("ctas",               "ALTER TABLE sites ADD COLUMN ctas TEXT DEFAULT '[]'"),
        ("content_batch_size", "ALTER TABLE sites ADD COLUMN content_batch_size INTEGER DEFAULT 1"),
        ("indexnow_key",       "ALTER TABLE sites ADD COLUMN indexnow_key TEXT DEFAULT ''"),
    ):
        if col not in site_cols:
            conn.execute(ddl)

    # Content-jobs: QC-rapport van de meertraps-generator + gebruikte casestudy,
    # zodat de Wachtrij per artikel kan tonen welke checks zijn gedaan/gefixt.
    cj_cols = {row["name"] for row in conn.execute("PRAGMA table_info(content_jobs)").fetchall()}
    for col, ddl in (
        ("qc_report",     "ALTER TABLE content_jobs ADD COLUMN qc_report TEXT DEFAULT '{}'"),
        ("case_study_id", "ALTER TABLE content_jobs ADD COLUMN case_study_id TEXT DEFAULT ''"),
    ):
        if cj_cols and col not in cj_cols:
            conn.execute(ddl)

    # Social publisher: quote-card afbeelding per gepubliceerde pagina (base64 PNG),
    # meegenomen in elke Netlify full-site-rebuild zodat Instagram een publieke
    # image-url heeft die niet verdwijnt bij een volgend publish van een ander artikel.
    pp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(published_pages)").fetchall()}
    if pp_cols and "image_b64" not in pp_cols:
        conn.execute("ALTER TABLE published_pages ADD COLUMN image_b64 TEXT DEFAULT ''")

    # Opdrachten: leeftijd van de vacature (dagen sinds plaatsing, -1 = onbekend) —
    # gebruikt om verlopen/oude vacatures uit de standaardweergave te filteren.
    vac_cols = {row["name"] for row in conn.execute("PRAGMA table_info(vacancies)").fetchall()}
    if vac_cols and "posted_days_ago" not in vac_cols:
        conn.execute("ALTER TABLE vacancies ADD COLUMN posted_days_ago INTEGER DEFAULT -1")

    # Uitkomst-kaarten: activity_log bestond alleen impliciet (aangemaakt door
    # oudere code) — hier expliciet, plus artefact-link ("waar staat het"),
    # next_step ("wat moet Vincent doen") en status (ok/error) zodat het
    # Actiecentrum fouten kan oppikken.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS activity_log (
            id         TEXT PRIMARY KEY,
            project    TEXT NOT NULL,
            action     TEXT NOT NULL,
            detail     TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    act_cols = {row["name"] for row in conn.execute("PRAGMA table_info(activity_log)").fetchall()}
    for col, ddl in (
        ("artifact",  "ALTER TABLE activity_log ADD COLUMN artifact TEXT DEFAULT ''"),
        ("next_step", "ALTER TABLE activity_log ADD COLUMN next_step TEXT DEFAULT ''"),
        ("status",    "ALTER TABLE activity_log ADD COLUMN status TEXT DEFAULT 'ok'"),
    ):
        if col not in act_cols:
            conn.execute(ddl)

    # Actiecentrum: weggeklikte inbox-items (bv. een fout die je gezien hebt).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS inbox_dismissals (
            kind         TEXT NOT NULL,
            ref_id       TEXT NOT NULL,
            dismissed_at TEXT NOT NULL,
            PRIMARY KEY (kind, ref_id)
        )"""
    )


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

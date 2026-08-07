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
    live_url          TEXT DEFAULT '',
    published_at      TEXT DEFAULT '',
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

-- Linkbuilding: linkkans-funnel per site. De agent zoekt en kwalificeert
-- prospects en zet mailconcepten klaar; versturen kan ALLEEN via de
-- approve-endpoint (review-gate). Tijdstempels vanaf 'contacted' zijn de
-- basis voor de linkbuilding-formule ("X mails -> 1 link live").
CREATE TABLE IF NOT EXISTS link_prospects (
    id                  TEXT PRIMARY KEY,
    site_id             TEXT NOT NULL,
    domain              TEXT NOT NULL,
    url                 TEXT DEFAULT '',
    page_title          TEXT DEFAULT '',
    prospect_type       TEXT DEFAULT 'overig',       -- gastblog | resource | partner | gids | mention | overig
    relevance_score     INTEGER DEFAULT 0,
    rationale           TEXT DEFAULT '',
    contact_email       TEXT DEFAULT '',
    target_url          TEXT DEFAULT '',             -- onze pagina die de link moet krijgen
    anchor_text         TEXT DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'new', -- new|qualified|outreach_review|contacted|replied|agreed|link_live|verified (zijuitgang: lost)
    outreach_subject    TEXT DEFAULT '',
    outreach_draft      TEXT DEFAULT '',
    outreach_drafted_at TEXT DEFAULT '',
    contacted_at        TEXT DEFAULT '',
    replied_at          TEXT DEFAULT '',
    agreed_at           TEXT DEFAULT '',
    link_live_at        TEXT DEFAULT '',
    verified_at         TEXT DEFAULT '',
    lost_at             TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_link_prospects_site_domain ON link_prospects(site_id, domain);

-- Linkbuilding: de concrete afspraak (welke link, waar) en of hij er staat.
-- De monitor crawlt source_url en zoekt de link naar target_url:
-- pending -> live (-> verified op de prospect) en live -> lost bij verdwijnen.
CREATE TABLE IF NOT EXISTS link_placements (
    id           TEXT PRIMARY KEY,
    prospect_id  TEXT NOT NULL,
    site_id      TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    target_url   TEXT NOT NULL,
    anchor_text  TEXT DEFAULT '',
    rel          TEXT DEFAULT '',                    -- '' (dofollow) | nofollow | sponsored | ugc
    status       TEXT NOT NULL DEFAULT 'pending',    -- pending | live | lost
    first_seen   TEXT DEFAULT '',
    last_checked TEXT DEFAULT '',
    check_fails  INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (prospect_id) REFERENCES link_prospects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_link_placements_status ON link_placements(status, site_id);

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

-- Cache voor de rijke bridge-context. De bridge-sync draait elke 3 minuten;
-- Google Analytics, Graph en Agenda elke ronde bevragen zou quota verbranden
-- en de sync traag maken. Per sectie een eigen TTL (zie bridge/context.py).
CREATE TABLE IF NOT EXISTS bridge_context_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,              -- JSON
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    job_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,              -- ok | error | missed
    last_run_at TEXT NOT NULL,              -- laatste run, ongeacht uitkomst
    last_ok_at  TEXT,                       -- laatste geslaagde run; bepaalt of een run ingehaald moet worden
    error       TEXT,
    source      TEXT NOT NULL DEFAULT 'schedule'  -- schedule | catchup | manual
);
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
        # Outreach-leerlus: welke concept-stijl dit concept gebruikte (JSON:
        # opening/toon/lengte) — de koppeling tussen aanpak en reply-uitkomst.
        ("outreach_variant", "ALTER TABLE leads ADD COLUMN outreach_variant TEXT DEFAULT ''"),
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
            # Vooraf gegenereerd conceptantwoord voor de top-urgente mails
            # (bridge/context.py, ensure_suggested_replies) — eenmalig gezet,
            # niet opnieuw gegenereerd zolang de mail als urgent geldt.
            ("suggested_reply",           "ALTER TABLE outlook_emails ADD COLUMN suggested_reply TEXT DEFAULT ''"),
            ("suggested_reply_at",        "ALTER TABLE outlook_emails ADD COLUMN suggested_reply_at TEXT DEFAULT ''"),
            ("suggested_reply_dismissed", "ALTER TABLE outlook_emails ADD COLUMN suggested_reply_dismissed INTEGER DEFAULT 0"),
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
        # Testsites tellen niet mee in Iris' cijfers/prioritering — anders wordt
        # "TestSite omhoog tillen" het topadvies terwijl het geen echt project is.
        ("is_test",            "ALTER TABLE sites ADD COLUMN is_test INTEGER DEFAULT 0"),
        # Alleen-website-publicatie: geen social-fan-out en geen Content Multiplier
        # (social-pack + video). Voor sites die bewust niet op social willen (Daar).
        ("website_only",       "ALTER TABLE sites ADD COLUMN website_only INTEGER DEFAULT 0"),
        # Sites zonder publish-API (bv. LiefdeVoorIedereen/datingsite2026: content
        # gaat via een Prisma-admin-sessie, niet een publieke endpoint) — Vincent
        # pusht die zelf. Zonder deze vlag probeert approve_and_publish eeuwig een
        # PUBLISH_URL/_KEY die nooit gaat bestaan, en verschijnt elke goedkeuring
        # als 'Publiceren mislukt' in het Actiecentrum terwijl er niets mis is.
        # Met de vlag exporteert de pipeline het klaar-artikel naar de vault i.p.v.
        # een HTTP-publish te proberen, en telt dat als afgeronde levering.
        ("manual_publish",     "ALTER TABLE sites ADD COLUMN manual_publish INTEGER DEFAULT 0"),
    ):
        if col not in site_cols:
            conn.execute(ddl)
    if "is_test" not in site_cols:
        # Eenmalige nulmeting: bestaande sites met 'test' in de naam markeren.
        conn.execute("UPDATE sites SET is_test = 1 WHERE lower(name) LIKE '%test%'")

    # Demand Engine kansen: live-URL + publicatietimestamp, zodat de Kansen-card
    # in de UI direct kan tonen of een artikel écht live staat (ipv alleen de
    # handmatige 'Gepubliceerd'-vink). Idempotent.
    opp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
    if "live_url" not in opp_cols:
        conn.execute("ALTER TABLE opportunities ADD COLUMN live_url TEXT DEFAULT ''")
    if "published_at" not in opp_cols:
        conn.execute("ALTER TABLE opportunities ADD COLUMN published_at TEXT DEFAULT ''")

    # Content-jobs: QC-rapport van de meertraps-generator + gebruikte casestudy,
    # zodat de Wachtrij per artikel kan tonen welke checks zijn gedaan/gefixt.
    cj_cols = {row["name"] for row in conn.execute("PRAGMA table_info(content_jobs)").fetchall()}
    for col, ddl in (
        ("qc_report",        "ALTER TABLE content_jobs ADD COLUMN qc_report TEXT DEFAULT '{}'"),
        ("case_study_id",    "ALTER TABLE content_jobs ADD COLUMN case_study_id TEXT DEFAULT ''"),
        # Infographic (base64 PNG) per artikel: gaat bij goedkeuring mee de
        # pagina in (Google Afbeeldingen + AI Overviews citeren beeldbronnen).
        ("infographic_path", "ALTER TABLE content_jobs ADD COLUMN infographic_path TEXT DEFAULT ''"),
    ):
        if cj_cols and col not in cj_cols:
            conn.execute(ddl)

    # Social publisher: quote-card afbeelding per gepubliceerde pagina (base64 PNG),
    # meegenomen in elke Netlify full-site-rebuild zodat Instagram een publieke
    # image-url heeft die niet verdwijnt bij een volgend publish van een ander artikel.
    pp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(published_pages)").fetchall()}
    if pp_cols and "image_b64" not in pp_cols:
        conn.execute("ALTER TABLE published_pages ADD COLUMN image_b64 TEXT DEFAULT ''")
    # Infographic per pagina (base64 PNG) — mee in elke full-site-rebuild als
    # images/{slug}-infographic.png, en als <figure> in het artikel zelf.
    if pp_cols and "infographic_b64" not in pp_cols:
        conn.execute("ALTER TABLE published_pages ADD COLUMN infographic_b64 TEXT DEFAULT ''")

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

    # GSC-prestatiemetrics per gepubliceerde pagina (feedback-loop).
    pp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(published_pages)").fetchall()}
    for col, ddl in (
        ("gsc_clicks",       "ALTER TABLE published_pages ADD COLUMN gsc_clicks INTEGER DEFAULT 0"),
        ("gsc_impressions",  "ALTER TABLE published_pages ADD COLUMN gsc_impressions INTEGER DEFAULT 0"),
        ("gsc_ctr",          "ALTER TABLE published_pages ADD COLUMN gsc_ctr REAL DEFAULT 0"),
        ("gsc_position",     "ALTER TABLE published_pages ADD COLUMN gsc_position REAL DEFAULT 0"),
        ("gsc_top_query",    "ALTER TABLE published_pages ADD COLUMN gsc_top_query TEXT DEFAULT ''"),
        ("gsc_synced_at",    "ALTER TABLE published_pages ADD COLUMN gsc_synced_at TEXT DEFAULT ''"),
    ):
        if col not in pp_cols:
            conn.execute(ddl)

    # GSC-historie — dagreeksen per site (scope='site', echte dagcijfers via de
    # date-dimensie) en dagelijkse per-pagina-snapshots (scope='page', trailing
    # 28-dagen-aggregaat op sync-datum). Basis voor trend-delta's: zonder
    # historie kan niemand (Iris incluis) bewijzen dat een interventie werkte.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gsc_history (
            id          TEXT PRIMARY KEY,
            site_id     TEXT NOT NULL,
            scope       TEXT NOT NULL DEFAULT 'site',  -- site | page
            page_url    TEXT DEFAULT '',               -- leeg bij scope='site'
            date        TEXT NOT NULL,                 -- YYYY-MM-DD
            clicks      INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0,
            ctr         REAL DEFAULT 0,
            position    REAL DEFAULT 0,
            top_query   TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            UNIQUE(site_id, scope, page_url, date)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gsc_history_site_date "
        "ON gsc_history(site_id, scope, date DESC)"
    )

    # Iris — de manager-agent: dagelijkse briefing (rapport per dag) en het
    # lessen-geheugen waarmee ze over dagen heen leert en zichzelf verbetert.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS iris_reports (
            id            TEXT PRIMARY KEY,
            report_date   TEXT NOT NULL,          -- YYYY-MM-DD (Europe/Amsterdam)
            markdown      TEXT NOT NULL,          -- de volledige briefing
            grades        TEXT DEFAULT '{}',      -- JSON: {project: {score, oordeel}}
            learned       TEXT DEFAULT '[]',      -- JSON: wat Iris vandaag leerde
            improvements  TEXT DEFAULT '[]',      -- JSON: wat ze concreet aanpaste
            advice        TEXT DEFAULT '[]',      -- JSON: advies-van-vandaag voor Vincent
            metrics       TEXT DEFAULT '{}',      -- JSON: de harde cijfers-snapshot
            created_at    TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iris_reports_date ON iris_reports(report_date DESC)"
    )
    # Terugval-vlag: 0 = briefing zonder LLM (puur cijfers). De herkanselaar
    # (scheduler-job iris_briefing_retry) draait dan later op de dag alsnog een
    # volwaardige analyse. Oude terugval-rijen (vóór deze kolom) worden herkend
    # aan de "_LLM niet beschikbaar"-marker in de markdown.
    ir_cols = {r["name"] for r in conn.execute("PRAGMA table_info(iris_reports)").fetchall()}
    if "llm_ok" not in ir_cols:
        conn.execute("ALTER TABLE iris_reports ADD COLUMN llm_ok INTEGER DEFAULT 1")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS iris_lessons (
            id              TEXT PRIMARY KEY,
            lesson          TEXT NOT NULL,        -- de les, in één zin
            category        TEXT DEFAULT '',      -- seo | content | funnel | systeem | proces
            source          TEXT DEFAULT '',      -- waaruit de les volgde
            times_confirmed INTEGER DEFAULT 1,    -- vaker bevestigd = zwaarder gewicht
            active          INTEGER DEFAULT 1,    -- 0 = achterhaald/ingetrokken
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )"""
    )
    # Evidence-based leren: elke les wordt getoetst met falsifieerbare
    # voorspellingen. Een les die correcte voorspellingen oplevert wint
    # vertrouwen; faalt haar voorspelling dan daalt het — en bij herhaald
    # falen wordt de les ingetrokken (active=0). Zo weegt bewijs, niet herhaling.
    les_cols = {r["name"] for r in conn.execute("PRAGMA table_info(iris_lessons)").fetchall()}
    for col, ddl in (
        ("predictions_made",    "ALTER TABLE iris_lessons ADD COLUMN predictions_made INTEGER DEFAULT 0"),
        ("predictions_correct", "ALTER TABLE iris_lessons ADD COLUMN predictions_correct INTEGER DEFAULT 0"),
        ("confidence",          "ALTER TABLE iris_lessons ADD COLUMN confidence REAL DEFAULT 0.5"),
    ):
        if col not in les_cols:
            conn.execute(ddl)

    # Iris' voorspellingen — de gesloten leer-lus. Bij elk advies/bijsturing
    # legt Iris een toetsbare voorspelling vast (metric, richting, horizon).
    # De volgende ochtend na `due_date` rekent ze die af tegen de echte
    # GSC-/cijferdata. Dit is wat 'leert en verbetert' aantoonbaar maakt.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS iris_predictions (
            id            TEXT PRIMARY KEY,
            report_date   TEXT NOT NULL,          -- dag waarop de voorspelling is gedaan
            project       TEXT DEFAULT '',
            site_id       TEXT DEFAULT '',
            metric        TEXT NOT NULL,          -- clicks | position | impressions | ctr | live_content
            direction     TEXT NOT NULL,          -- up | down (positie: up = beter = lager getal)
            baseline      REAL DEFAULT 0,         -- metriekwaarde op moment van voorspellen
            target        REAL,                   -- optioneel: verwachte waarde
            horizon_days  INTEGER DEFAULT 7,
            due_date      TEXT NOT NULL,          -- report_date + horizon
            lesson_id     TEXT DEFAULT '',        -- de les die deze voorspelling toetst
            statement     TEXT DEFAULT '',        -- de voorspelling in mensentaal
            status        TEXT NOT NULL DEFAULT 'open',  -- open | correct | wrong | unclear
            outcome_value REAL,
            outcome_note  TEXT DEFAULT '',
            evaluated_at  TEXT DEFAULT '',
            created_at    TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iris_predictions_due "
        "ON iris_predictions(status, due_date)"
    )

    # Iris' kennisbank — Vincent voedt haar met onderzoek (bv. GEO/AEO/SEO).
    # Markdown-bestanden in de vault-map worden gedistilleerd tot toepasbare
    # principes die in Iris' analyse-prompt én in de content-schrijfprompts
    # terechtkomen. Zo wordt ze aantoonbaar slimmer van wat jij aanlevert.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS iris_knowledge (
            id            TEXT PRIMARY KEY,
            source        TEXT NOT NULL DEFAULT 'vault',  -- vault | manual
            source_path   TEXT DEFAULT '',                -- vault-bestandspad (leeg bij manual)
            title         TEXT NOT NULL,
            content_hash  TEXT DEFAULT '',                -- detecteert gewijzigde bestanden
            summary       TEXT DEFAULT '',                -- korte distillatie
            principles    TEXT DEFAULT '[]',              -- JSON: toepasbare principes
            tags          TEXT DEFAULT '[]',              -- JSON: bv. ['geo','seo','content']
            scope         TEXT DEFAULT 'all',             -- 'all' of een projectnaam
            active        INTEGER DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iris_knowledge_active "
        "ON iris_knowledge(active, scope)"
    )

    # Iris' actie-voorstellen ("Wil je dat ik dit fix?") — de brug van
    # analyse naar uitvoering. Iris legt per briefing concreet uitvoerbare
    # acties klaar; Vincent keurt per stuk goed (apply) of wijst af (reject).
    # Elke rij is idempotent: dezelfde (report, type, target) wordt niet
    # twee keer aangeboden, en een eenmaal goedgekeurde/afgewezen actie
    # blijft gesloten. Agenten publiceren/versturen NOOIT zelf — alles
    # landt achter de bestaande review-gates (Wachtrij / Actiecentrum).
    # status: pending | approved | rejected | applied | failed
    # type: content_run | seo_refresh | outreach_run | gsc_connect | goal_draft
    conn.execute(
        "CREATE TABLE IF NOT EXISTS iris_suggestions ("
        "  id            TEXT PRIMARY KEY,"
        "  report_date   TEXT NOT NULL,"
        "  scope         TEXT NOT NULL DEFAULT 'all',"
        "  type          TEXT NOT NULL,"
        "  title         TEXT NOT NULL,"
        "  detail        TEXT DEFAULT '',"
        "  target        TEXT DEFAULT '',"
        "  payload       TEXT DEFAULT '{}',"
        "  priority      INTEGER DEFAULT 5,"
        "  status        TEXT NOT NULL DEFAULT 'pending',"
        "  applied_detail TEXT DEFAULT '',"
        "  decided_at    TEXT DEFAULT '',"
        "  applied_at    TEXT DEFAULT '',"
        "  goal_id       TEXT DEFAULT '',"
        "  created_at    TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iris_suggestions_report "
        "ON iris_suggestions(report_date, status)"
    )
    # goal_id linkt een goal_draft-suggestie aan het concept-doel dat Iris
    # bij apply() aanmaakte. Zonder die link tonen het Actiecentrum (goal
    # draft) en de Iris-suggestie (applied) dezelfde intentie tweemaal met
    # tegenstrijdige statussen ("Uitgevoerd ✓" én "Bevestig & start").
    sug_cols = {r["name"] for r in conn.execute("PRAGMA table_info(iris_suggestions)").fetchall()}
    if "goal_id" not in sug_cols:
        conn.execute("ALTER TABLE iris_suggestions ADD COLUMN goal_id TEXT DEFAULT ''")

    # ── Iris' storings-repertoire: wat werkte er bij wélke fout? ────────────
    # Elke "analyseer & fix" op een foutkaart legt hier een rij vast, gesleuteld
    # op een genormaliseerde fout-handtekening (nummers/UUID's/titels eruit).
    # Zo hoeft dezelfde storing maar één keer door de LLM: bij herhaling pakt
    # Iris de remedie die eerder wérkte, en zakt een remedie die blijft falen
    # vanzelf weg (successes vs. failures). Dit is het leer-deel van de lus —
    # het uitvoer-deel blijft in triage.py achter dezelfde review-gates.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS iris_error_fixes (
            id            TEXT PRIMARY KEY,
            signature     TEXT NOT NULL,        -- genormaliseerde fout-vingerafdruk
            project       TEXT DEFAULT '',
            sample_action TEXT DEFAULT '',      -- hoe de fout zich noemt
            sample_detail TEXT DEFAULT '',      -- één voorbeeld, voor de UI
            diagnosis     TEXT DEFAULT '',      -- Iris' oorzaak-analyse
            remedy_type   TEXT DEFAULT '',      -- whitelist-actie uit triage.py
            remedy_payload TEXT DEFAULT '{}',
            human_step    TEXT DEFAULT '',      -- wat een mens moet doen (als agents niet kunnen)
            attempts      INTEGER DEFAULT 0,
            successes     INTEGER DEFAULT 0,
            failures      INTEGER DEFAULT 0,
            occurrences   INTEGER DEFAULT 1,    -- hoe vaak deze fout langskwam
            last_result   TEXT DEFAULT '',
            active        INTEGER DEFAULT 1,    -- 0 = remedie bewezen nutteloos
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_iris_error_fixes_sig "
        "ON iris_error_fixes(signature)"
    )

    # ── Faal-reeksen: "probeer het zelf" vs. "maak een mens wakker" ─────────
    # Eén mislukte poll is geen storing (het netwerk hikt, de laptop sliep);
    # drie op rij wél. Die telling staat in SQLite en niet in het procesgeheugen,
    # anders is na een herstart "nooit gefaald" niet te onderscheiden van "faalt
    # al uren" — en juist dat verschil bepaalt of Vincent een kaart moet zien.
    # Zie backend/shared/failures.py.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_failure_streaks (
            key             TEXT PRIMARY KEY,   -- bv. 'social_fetch:si_1003…'
            fail_count      INTEGER DEFAULT 0,
            first_failed_at TEXT,
            last_failed_at  TEXT,
            last_detail     TEXT DEFAULT '',
            failure_class   TEXT DEFAULT '',    -- transient/auth/quota/config/unknown
            escalated       INTEGER DEFAULT 0   -- 1 = al gemeld, niet nóg een kaart
        )"""
    )

    # ── Iris' zelfherstel-logboek ──────────────────────────────────────────
    # Elke poging van Iris om een foutkaart zélf op te lossen (probe, remedie,
    # uitkomst). Bestaat los van activity_log omdat een mislukte poging géén
    # inbox-item mag worden — anders vervang je één rode kaart door drie. Het is
    # tegelijk het bewijsmateriaal onder "Iris loste dit zelf op".
    conn.execute(
        """CREATE TABLE IF NOT EXISTS iris_heal_log (
            id          TEXT PRIMARY KEY,
            signature   TEXT NOT NULL,          -- zelfde vingerafdruk als iris_error_fixes
            source_kind TEXT DEFAULT '',        -- activity_log | scheduler
            source_id   TEXT DEFAULT '',
            project     TEXT DEFAULT '',
            action      TEXT DEFAULT '',
            failure_class TEXT DEFAULT '',
            remedy      TEXT DEFAULT '',        -- welke probe/remedie is geprobeerd
            result      TEXT DEFAULT '',        -- healed | failed | escalated | waiting
            note        TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iris_heal_log_sig "
        "ON iris_heal_log(signature, created_at)"
    )

    # ── Mail helpdesk (review-gate): per project een eigen mailbox ──────────
    # Een mailbox koppelt een project aan zijn POP3-inbox + SMTP-verzender.
    # AgentOS haalt mail op, filtert spam, laat de LLM een concept-antwoord
    # schrijven en zet dat klaar in mail_reply (status=pending_review). Niets
    # vertrekt zonder Vincents expliciete klik — zelfde discipline als de
    # content-wachtrij / Iris "publiceert nooit zelf".
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mailboxes (
            id             TEXT PRIMARY KEY,
            project        TEXT NOT NULL,          -- projectnaam (skillkaart, bijeen, ...)
            label          TEXT NOT NULL DEFAULT '',
            address        TEXT NOT NULL,          -- hallo@bijeen.app
            pop_host       TEXT NOT NULL,
            pop_port       INTEGER NOT NULL DEFAULT 110,
            pop_user       TEXT NOT NULL,
            pop_password   TEXT NOT NULL DEFAULT '',
            smtp_host      TEXT NOT NULL DEFAULT '',
            smtp_port      INTEGER NOT NULL DEFAULT 587,
            smtp_user      TEXT NOT NULL DEFAULT '',
            smtp_password  TEXT NOT NULL DEFAULT '',
            brand_context  TEXT DEFAULT '',          -- merkstem voor de drafter
            knowledge_scope TEXT DEFAULT 'all',     -- filter op iris_knowledge.scope
            poll_minutes   INTEGER NOT NULL DEFAULT 30,
            enabled        INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mailboxes_project "
        "ON mailboxes(project)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mail_inbox (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox_id   TEXT NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE,
            uidl         TEXT NOT NULL,              -- POP3 UIDL, dedupe-sleutel
            from_addr    TEXT NOT NULL,
            from_name    TEXT DEFAULT '',
            subject      TEXT DEFAULT '',
            body_text    TEXT DEFAULT '',
            received_at  TEXT DEFAULT '',
            classified   TEXT DEFAULT 'unknown',    -- question|invoice|spam|newsletter|other
            created_at   TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_inbox_uidl "
        "ON mail_inbox(mailbox_id, uidl)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mail_reply (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox_id   TEXT NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE,
            inbox_id     INTEGER NOT NULL REFERENCES mail_inbox(id) ON DELETE CASCADE,
            to_addr      TEXT NOT NULL,
            subject      TEXT NOT NULL,
            draft_body   TEXT NOT NULL,             -- LLM-concept (markdown/NL)
            status       TEXT DEFAULT 'pending_review', -- pending_review|sent|edited|rejected
            edited_body  TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now')),
            sent_at      TEXT DEFAULT ''
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mail_reply_status "
        "ON mail_reply(mailbox_id, status)"
    )
    # Negeerlijst: afzenders waarop de helpdesk nooit meer een concept maakt
    # ("Niet meer reageren" in het Actiecentrum). pattern is een lowercase
    # e-mailadres, of '@domein' voor een heel domein.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mail_ignored_senders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern      TEXT NOT NULL UNIQUE,     -- 'x@y.nl' of '@y.nl'
            reason       TEXT DEFAULT '',           -- bv. onderwerp van de mail die ertoe leidde
            created_at   TEXT DEFAULT (datetime('now'))
        )"""
    )

    # ── Social Inbox (per project, per kanaal: LinkedIn/IG/FB/TikTok) ────────
    # Gespiegeld aan de mail-helpdesk: de agent leest reacties/DM's, schrijft
    # een concept in de merkstem, en zet dat klaar achter de review-gate.
    # Nooit auto-antwoorden — de mens keurt eerst (net als mail_reply).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS social_inboxes (
            id              TEXT PRIMARY KEY,
            project         TEXT NOT NULL,            -- 'bewaardvoorjou','weareimpact',...
            platform        TEXT NOT NULL,            -- 'linkedin'|'facebook'|'instagram'|'tiktok'
            label           TEXT DEFAULT '',
            creds_json      TEXT DEFAULT '{}',         -- page_id/token, ig_id, li urn, tiktok open_id
            brand_context   TEXT DEFAULT '',          -- merkstem-hint (projectnaam -> Schrijf-DNA)
            poll_minutes    INTEGER DEFAULT 30,
            enabled         INTEGER DEFAULT 1,
            created_at      TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_inboxes_project "
        "ON social_inboxes(project)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS social_inbox_msg (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            inbox_id        TEXT NOT NULL REFERENCES social_inboxes(id) ON DELETE CASCADE,
            platform        TEXT NOT NULL,
            external_id     TEXT NOT NULL,             -- comment/post/DM id op het kanaal (dedupe)
            author_name     TEXT DEFAULT '',
            author_handle   TEXT DEFAULT '',
            text            TEXT DEFAULT '',
            kind            TEXT DEFAULT 'unknown',    -- question|praise|complaint|spam|other
            parent_url      TEXT DEFAULT '',           -- link naar de post (UI-context)
            thread_json     TEXT DEFAULT '[]',         -- eerdere reacties in dezelfde thread
            draft_body      TEXT DEFAULT '',
            status          TEXT DEFAULT 'pending_review', -- pending_review|sent|edited|rejected|ignored
            edited_body     TEXT DEFAULT '',
            manual          INTEGER DEFAULT 0,         -- 1 = kanaal staat geen API-antwoord toe (plak-adapter)
            created_at      TEXT DEFAULT (datetime('now')),
            sent_at         TEXT DEFAULT ''
        )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_social_ext "
        "ON social_inbox_msg(inbox_id, external_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_msg_status "
        "ON social_inbox_msg(inbox_id, status)"
    )

    # ── Social Content Creatie (posts + beeld-brief + TikTok-scriptpack) ────
    # De CREATIE-laag: agents maken eigen posts/beeld/TikTok i.p.v. reageren op
    # andermans berichten (dat doet social_inbox). Alles achter een review-gate
    # (status=pending_review) — niets wordt automatisch gepost. Eén pack =
    # per-platform tekst + optioneel image_brief_json + tiktok_pack_json.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS social_posts (
            id               TEXT PRIMARY KEY,
            project          TEXT NOT NULL,           -- 'weareimpact','bijeen',...
            theme            TEXT NOT NULL,           -- waar de post over gaat
            angle            TEXT DEFAULT '',          -- gekozen invalshoek
            brand_context    TEXT DEFAULT '',          -- merkstem-hint (projectnaam)
            copy_json        TEXT DEFAULT '{}',         -- {"linkedin":"...","facebook":"...",...}
            image_brief_json TEXT DEFAULT '{}',        -- Canva-ready brief + MJ-prompt
            tiktok_pack_json TEXT DEFAULT '{}',        -- hook/script/shotlist/hashtags
            status           TEXT DEFAULT 'pending_review', -- pending_review|approved|rejected|posted
            concept          INTEGER DEFAULT 0,        -- 1 = lokale fallback (geen LLM), niet productieklaar
            approved_at      TEXT DEFAULT '',
            posted_result_json TEXT DEFAULT '{}',      -- resultaat van publish_pack()
            video_path       TEXT DEFAULT '',           -- projectrelatief pad naar gerenderde 9:16 short
            created_at       TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_posts_project "
        "ON social_posts(project, status)"
    )
    # Bestaande databases (tabel al aangemaakt vóór de video-feature): kolom bijzetten.
    sp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(social_posts)").fetchall()}
    if "video_path" not in sp_cols:
        conn.execute("ALTER TABLE social_posts ADD COLUMN video_path TEXT DEFAULT ''")

    # ── LinkedIn posts log (eigen posts die AgentOS plaatst) ──────────────
    # Basis voor de lokale analyse-laag: de LinkedIn Posts API (statistieken
    # lezen) vereist partner-toegang die een solo-founder niet heeft, dus houden
    # we hier bij wat wij zélf hebben geplaatst.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS linkedin_posts (
            id          TEXT PRIMARY KEY,    -- LinkedIn post URN (urn:li:share:xxx)
            project     TEXT DEFAULT '',
            text        TEXT DEFAULT '',
            url         TEXT DEFAULT '',
            posted_at   TEXT NOT NULL,
            source      TEXT DEFAULT 'api'   -- api | pack
        )"""
    )

    # ── LLM-kosten-telemetrie ──────────────────────────────────────────────
    # Elke OpenModel/Claude/Hermes-aanroep (ook de autonome achtergrond-jobs)
    # wordt hier gelogd zodat token-verbruik zichtbaar is vóórdat de echte
    # externe quota leegloopt. Geïntroduceerd na de "quota in één dag leeg"
    # incident (2026-07-10): background-jobs schreven hun verbruik nergens heen.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS llm_usage (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            backend      TEXT NOT NULL,   -- openmodel | anthropic | openrouter | hermes-local | ollama
            model        TEXT NOT NULL,
            route        TEXT DEFAULT '',  -- bv. content-improver | radar-sky | chat | seo-demand
            prompt_tokens   INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            status       TEXT DEFAULT 'ok',  -- ok | error | empty
            error        TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_created "
        "ON llm_usage(created_at)"
    )

    # Cross-run verbeter-teller: voorkomt dat de content-verbeteraar elke 30 min
    # hetzelfde vastgelopen artikel blijft oppakken (incident 2026-07-10).
    cj_cols = {r["name"] for r in conn.execute("PRAGMA table_info(content_jobs)").fetchall()}
    if "improve_attempts" not in cj_cols:
        conn.execute(
            "ALTER TABLE content_jobs ADD COLUMN improve_attempts INTEGER DEFAULT 0"
        )

    # Wereldklasse-uitbreidingen: threading + per-mailbox From-naam + auto-reply-bescherming
    mb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(mailboxes)").fetchall()}
    if "from_display" not in mb_cols:
        conn.execute("ALTER TABLE mailboxes ADD COLUMN from_display TEXT DEFAULT ''")
    # Handtekening per project: gaat onder elk concept mee (WYSIWYG in de review).
    if "signature" not in mb_cols:
        conn.execute("ALTER TABLE mailboxes ADD COLUMN signature TEXT DEFAULT ''")
    # SSL-vlag voor de POP3-poller. Office365/Exchange en de meeste hosters
    # hebben basic-auth op poort 110 allang uitgezet en verplichten POP3_SSL
    # (poort 995). Zonder deze vlag strandde elke Exchange-mailbox stilzwijgend.
    if "pop_ssl" not in mb_cols:
        conn.execute("ALTER TABLE mailboxes ADD COLUMN pop_ssl INTEGER NOT NULL DEFAULT 0")
    # ── Microsoft Graph (OAuth2 client_credentials) voor Office365/Exchange ──
    # Basic auth (POP3/IMAP/SMTP-wachtwoord) is sinds 1 okt 2022 uitgeschakeld
    # bij Exchange Online. Mailboxen op M365 authenticeren daarom via Graph met
    # een Entra-app (client_credentials). Deze kolommen bewaren de app-creds
    # per mailbox — NIET het accountwachtwoord, en niet in .env.
    for col, ddl in (
        ("auth_method",       "ALTER TABLE mailboxes ADD COLUMN auth_method TEXT DEFAULT 'pop'"),
        ("graph_tenant_id",   "ALTER TABLE mailboxes ADD COLUMN graph_tenant_id TEXT DEFAULT ''"),
        ("graph_client_id",   "ALTER TABLE mailboxes ADD COLUMN graph_client_id TEXT DEFAULT ''"),
        ("graph_client_secret", "ALTER TABLE mailboxes ADD COLUMN graph_client_secret TEXT DEFAULT ''"),
        ("graph_user_upn",    "ALTER TABLE mailboxes ADD COLUMN graph_user_upn TEXT DEFAULT ''"),
    ):
        if col not in mb_cols:
            conn.execute(ddl)
    mi_cols = {r["name"] for r in conn.execute("PRAGMA table_info(mail_inbox)").fetchall()}
    for col, ddl in (
        ("message_id",  "ALTER TABLE mail_inbox ADD COLUMN message_id TEXT DEFAULT ''"),
        ("in_reply_to", "ALTER TABLE mail_inbox ADD COLUMN in_reply_to TEXT DEFAULT ''"),
        ("references",  'ALTER TABLE mail_inbox ADD COLUMN "references" TEXT DEFAULT \'\''),
        ("auto_submitted", "ALTER TABLE mail_inbox ADD COLUMN auto_submitted INTEGER DEFAULT 0"),
    ):
        if col not in mi_cols:
            conn.execute(ddl)
    mr_cols = {r["name"] for r in conn.execute("PRAGMA table_info(mail_reply)").fetchall()}
    for col, ddl in (
        ("in_reply_to", "ALTER TABLE mail_reply ADD COLUMN in_reply_to TEXT DEFAULT ''"),
        ("references",  'ALTER TABLE mail_reply ADD COLUMN "references" TEXT DEFAULT \'\''),
    ):
        if col not in mr_cols:
            conn.execute(ddl)

    # ── Agenda-voorstellen (mail → afspraak, human-in-the-loop) ──
    # Iris detecteert afspraak-verzoeken in mail en stelt een slot voor met
    # conflict- + reistijd-logica. Niét direct in de agenda geschreven: eerst
    # pending_review, Vincent keurt goed → pas dan block_time() naar Google.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS calendar_proposals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox_id       TEXT NOT NULL,
            inbox_id         INTEGER NOT NULL,
            from_addr        TEXT DEFAULT '',
            subject          TEXT DEFAULT '',
            title            TEXT DEFAULT '',
            proposed_start   TEXT DEFAULT '',
            proposed_end     TEXT DEFAULT '',
            location         TEXT DEFAULT '',
            is_remote        INTEGER DEFAULT 0,
            duration_min     INTEGER DEFAULT 30,
            travel_buffer_min INTEGER DEFAULT 0,
            priority         TEXT DEFAULT 'normal',
            conflict_note    TEXT DEFAULT '',
            -- 'ok' = tegen de agenda's gecontroleerd, 'unavailable' = geen
            -- agenda gekoppeld, 'error' = check mislukt. Die laatste twee zijn
            -- géén bewijs van een vrij slot; goedkeuren weigert erop.
            conflict_checked TEXT DEFAULT 'ok',
            rationale        TEXT DEFAULT '',
            status           TEXT NOT NULL DEFAULT 'pending_review',
            booked_event_id  TEXT DEFAULT '',
            booked_link      TEXT DEFAULT '',
            created_at       TEXT DEFAULT (datetime('now')),
            decided_at       TEXT DEFAULT ''
        )"""
    )
    # Bestaande databases: kolom bijtrekken. Oude rijen krijgen 'ok' — die zijn
    # gemaakt onder het oude gedrag, waar een mislukte check als "vrij" gold.
    cp_cols = {r["name"] for r in
               conn.execute("PRAGMA table_info(calendar_proposals)").fetchall()}
    if "conflict_checked" not in cp_cols:
        conn.execute("ALTER TABLE calendar_proposals ADD COLUMN "
                     "conflict_checked TEXT DEFAULT 'ok'")


    # ── Google Agenda-cache (Fase 1: lezen + blokkeren) ────────────────────
    # Lokale kopie van gesyncte events zodat de UI/Iris ook werkt als Google
    # even niet bereikbaar is. Gevuld door de calendar-sync job + bij elke
    # GET /api/calendar/events. Idempotent op event_id.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS calendar_events (
            event_id     TEXT PRIMARY KEY,
            summary      TEXT DEFAULT '',
            start_at     TEXT DEFAULT '',     -- ISO (dateTime of date)
            end_at       TEXT DEFAULT '',
            all_day      INTEGER DEFAULT 0,
            location     TEXT DEFAULT '',
            hangout_link TEXT DEFAULT '',
            html_link    TEXT DEFAULT '',
            synced_at    TEXT DEFAULT ''
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_events_start "
        "ON calendar_events(start_at)"
    )

    # ── Generiek leer-raamwerk (shared/learning.py) ────────────────────────
    # Het Iris-patroon (lessen + falsifieerbare voorspellingen, afgerekend
    # tegen échte cijfers) veralgemeend naar élke agent via een `agent`-kolom.
    # Iris zelf blijft (voorlopig) op haar eigen iris_*-tabellen draaien.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_lessons (
            id                  TEXT PRIMARY KEY,
            agent               TEXT NOT NULL,        -- outreach | content | vacancy | ...
            lesson              TEXT NOT NULL,        -- de les, in één stabiele zin
            category            TEXT DEFAULT '',
            evidence            TEXT DEFAULT '{}',    -- JSON: de cijfers waarop de les rust
            times_confirmed     INTEGER DEFAULT 1,
            predictions_made    INTEGER DEFAULT 0,
            predictions_correct INTEGER DEFAULT 0,
            confidence          REAL DEFAULT 0.5,     -- Laplace-gladgestreken trefkans
            active              INTEGER DEFAULT 1,    -- 0 = ingetrokken (bewijs won)
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_lessons_agent "
        "ON agent_lessons(agent, active)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_predictions (
            id              TEXT PRIMARY KEY,
            agent           TEXT NOT NULL,
            context         TEXT DEFAULT '',          -- waarover (bv. 'opening:observatie>vraag')
            metric          TEXT NOT NULL,            -- agent-eigen metriek (resolver rekent hem uit)
            direction       TEXT NOT NULL,            -- up | down (= beter | slechter)
            comparison      TEXT DEFAULT 'trend',     -- trend (beweging t.o.v. baseline) | threshold (haalt outcome de target)
            lower_is_better INTEGER DEFAULT 0,        -- bv. GSC-positie: lager getal = beter
            noise           REAL DEFAULT 0.5,         -- beweging kleiner dan dit = 'unclear'
            baseline        REAL NOT NULL,            -- waarde op moment van voorspellen (uit échte data)
            target          REAL,
            horizon_days    INTEGER DEFAULT 14,
            due_date        TEXT NOT NULL,
            lesson_id       TEXT DEFAULT '',          -- de les die deze voorspelling toetst
            statement       TEXT DEFAULT '',          -- de voorspelling in mensentaal
            status          TEXT NOT NULL DEFAULT 'open',  -- open | correct | wrong | unclear
            outcome_value   REAL,
            outcome_note    TEXT DEFAULT '',
            evaluated_at    TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_predictions_due "
        "ON agent_predictions(agent, status, due_date)"
    )

    # ── GSC-expert agent (Search Console fix-gids) ─────────────────────────
    # De agent analyseert GSC-notificatiemails, haalt live Search Console-data
    # op en schrijft een fix-gids. Hij leert per domein/reden uit Vincents
    # feedback (ratings) zodat latere antwoorden beter worden. Alle data hier
    # is de leer-laag; de agent leest gsc_analyses + gsc_feedback bij elke
    # nieuwe analyse.
    mr_cols = {r["name"] for r in conn.execute("PRAGMA table_info(mail_reply)").fetchall()}
    if "gsc_status" not in mr_cols:
        # 'pending' = wacht op actie, 'resolved' = agent heeft geanalyseerd
        # (en evt. verzonden), 'failed' = analyse mislukt.
        conn.execute("ALTER TABLE mail_reply ADD COLUMN gsc_status TEXT DEFAULT ''")
    if "gsc_confidence" not in mr_cols:
        # 0..1 — de agent's zekerheid dat de fix-gids volledig en correct is.
        conn.execute("ALTER TABLE mail_reply ADD COLUMN gsc_confidence REAL DEFAULT 0")
    if "gsc_fixed_by" not in mr_cols:
        # 'agent' = automatisch verzonden/opgelost, 'vincent' = handmatig,
        # '' = nog niet (alleen geanalyseerd).
        conn.execute("ALTER TABLE mail_reply ADD COLUMN gsc_fixed_by TEXT DEFAULT ''")
    if "gsc_analysis_id" not in mr_cols:
        conn.execute("ALTER TABLE mail_reply ADD COLUMN gsc_analysis_id TEXT DEFAULT ''")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS gsc_analyses (
            id              TEXT PRIMARY KEY,
            domain          TEXT NOT NULL,
            site_name       TEXT DEFAULT '',
            reason          TEXT DEFAULT '',
            used_live_gsc   INTEGER DEFAULT 0,
            analysis        TEXT DEFAULT '',     -- de gegenereerde fix-gids
            confidence      REAL DEFAULT 0,
            disposition     TEXT DEFAULT '',     -- 'sent' | 'resolved' | 'review'
            auto_sent       INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gsc_analyses_domain "
        "ON gsc_analyses(domain, created_at)"
    )

    # Vincents feedback per analyse — de brandstof voor het leren.
    # score: 1 (nutteloos) .. 5 (perfect). corrected_text: als Vincent de gids
    # verbeterde vóór verzenden, staat hier de definitieve versie (goudstandaard).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gsc_feedback (
            id              TEXT PRIMARY KEY,
            analysis_id     TEXT NOT NULL,
            domain          TEXT DEFAULT '',
            reason          TEXT DEFAULT '',
            score           INTEGER DEFAULT 0,
            corrected_text  TEXT DEFAULT '',
            note            TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gsc_feedback_analysis "
        "ON gsc_feedback(analysis_id)"
    )


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Wacht op een vergrendelde DB in plaats van direct "database is locked" te
    # gooien. Onder gelijktijdige schrijvers (scheduler, conveyor, mail-poll) is
    # een korte lock normaal; zonder busy_timeout mislukt elke write die niet
    # binnen microseconden de lock krijgt — precies wat de Graph-mailflow
    # (langzame network-fetch vóór de write) blootlegde. 5s bleek te krap: de
    # mail-poll classificeert/draft per bericht via de LLM terwijl hij de write-
    # transactie vasthoudt, en de piepkleine scheduler-writes (`_record_run`)
    # gaven dan "database is locked". De echte oplossing is de lock niet over
    # traag werk vasthouden (zie social_inbox.run_inbox); 15s is de vangrail
    # voor de flows waar dat nog wél gebeurt.
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

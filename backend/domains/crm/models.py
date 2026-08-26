"""
CRM-schema — bedrijven, contacten, deals, activiteiten, taken.

Bouwt voort op `prospecting/funnel.py` (leads new → … → won/lost) i.p.v. het
te vervangen: een lead die 'won' wordt, krijgt hier automatisch een bedrijf +
deal (zie `service.deal_uit_lead`). Er bestond al een losstaande CRM in
d:/apps/weareimpact (Next.js + Neon, /admin/crm) met vrijwel hetzelfde model
(companies/contacts/deals/activities/tasks) — die is sinds 4 juli 2026 niet
meer aangeraakt en staat in geen enkele AgentOS-notitie, terwijl de
prospecting-funnel actief en projectbreed gebruikt wordt. Twee administraties
van hetzelfde ding is precies de fout die deze codebase overal vermijdt
(zie CLAUDE.md 3a/3a-ter/16), dus wordt de funnel hier uitgebouwd tot een
volwaardige CRM in plaats van een tweede, aparte administratie te starten.

Het schema leeft bewust in dit domein (i.p.v. shared/database.py) zodat het
zelfstandig te verwijderen is — zelfde afweging als orders/models.py.
ensure_schema() is idempotent.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS crm_companies (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    website     TEXT DEFAULT '',
    industry    TEXT DEFAULT '',
    city        TEXT DEFAULT '',
    phone       TEXT DEFAULT '',
    email       TEXT DEFAULT '',
    kvk_number  TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    lead_id     TEXT DEFAULT '',   -- herkomst: prospecting.leads.id, leeg = handmatig aangemaakt
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_companies_name ON crm_companies(name);
CREATE INDEX IF NOT EXISTS idx_crm_companies_lead ON crm_companies(lead_id);

CREATE TABLE IF NOT EXISTS crm_contacts (
    id          TEXT PRIMARY KEY,
    company_id  TEXT DEFAULT '',
    first_name  TEXT NOT NULL,
    last_name   TEXT DEFAULT '',
    email       TEXT DEFAULT '',
    phone       TEXT DEFAULT '',
    job_title   TEXT DEFAULT '',
    is_primary  INTEGER DEFAULT 0,
    notes       TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_contacts_company ON crm_contacts(company_id);

-- Stages volgen bewust dezelfde taal als prospecting.funnel.FUNNEL_STAGES
-- vanaf 'contacted': een deal ontstaat pas als een lead die kant op beweegt.
-- 'onderhandeling'/'gewonnen'/'verloren' zijn het vervolg ná de funnel.
CREATE TABLE IF NOT EXISTS crm_deals (
    id                   TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL,
    contact_id           TEXT DEFAULT '',
    lead_id              TEXT DEFAULT '',
    title                TEXT NOT NULL,
    value_cents          INTEGER DEFAULT 0,
    stage                TEXT NOT NULL DEFAULT 'gesprek',
    probability           INTEGER DEFAULT 20,
    expected_close_date  TEXT DEFAULT '',
    description          TEXT DEFAULT '',
    source               TEXT DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    closed_at            TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_crm_deals_company ON crm_deals(company_id);
CREATE INDEX IF NOT EXISTS idx_crm_deals_stage ON crm_deals(stage);

CREATE TABLE IF NOT EXISTS crm_activities (
    id           TEXT PRIMARY KEY,
    company_id   TEXT DEFAULT '',
    contact_id   TEXT DEFAULT '',
    deal_id      TEXT DEFAULT '',
    type         TEXT NOT NULL,        -- call | mail | meeting | note | systeem
    subject      TEXT NOT NULL,
    description  TEXT DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_activities_company ON crm_activities(company_id);
CREATE INDEX IF NOT EXISTS idx_crm_activities_deal ON crm_activities(deal_id);

CREATE TABLE IF NOT EXISTS crm_tasks (
    id           TEXT PRIMARY KEY,
    company_id   TEXT DEFAULT '',
    contact_id   TEXT DEFAULT '',
    deal_id      TEXT DEFAULT '',
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    priority     TEXT DEFAULT 'normal',   -- laag | normal | hoog
    status       TEXT NOT NULL DEFAULT 'open',  -- open | done
    due_date     TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    completed_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_crm_tasks_status ON crm_tasks(status);
CREATE INDEX IF NOT EXISTS idx_crm_tasks_due ON crm_tasks(due_date);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True

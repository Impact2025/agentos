"""
Facturatie-schema — bonnetjes, uren-factuurconcepten, debiteuren-snapshots,
herinneringen.

DigiBoox (het boekhoudpakket, gekozen 25 aug 2026) heeft geen publieke API:
geen REST-endpoint, geen Zapier/Make. Wat er wél is: een persoonlijk mailadres
met eigen OCR ('ScanPilot AI') voor bonnetjes/inkoopfacturen, en een Excel-
importwizard voor verkoopfacturen. Dat schema is daarom bewust in twee
betrouwbaarheidsniveaus geknipt:
  - bonnetjes: volautomatisch doorgestuurd — DigiBoox' eigen OCR is de bron
    van waarheid over het bedrag, ImpactOS leest alleen voor eigen logging.
  - uren/debiteuren: halfautomatisch — ImpactOS genereert een concept/export,
    Vincent doet de laatste import-klik zelf in DigiBoox. Geen enkel bedrag
    wordt hier ooit als 'geboekt in DigiBoox' aangenomen, want dat weten we
    domweg niet zonder API.

Schema leeft in dit domein (i.p.v. shared/database.py) zodat het zelfstandig
te verwijderen is. ensure_schema() is idempotent.
"""
from ...shared.database import get_conn

DDL = """
-- Bonnetjes/inkoopfacturen. `read_amount_cents`/`read_summary` zijn ImpactOS'
-- eigen (lichte) lezing voor het dashboard — nooit de bron van waarheid over
-- het bedrag; dat blijft bij DigiBoox' eigen ScanPilot-OCR na het doorsturen.
CREATE TABLE IF NOT EXISTS billing_receipts (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL DEFAULT 'upload',  -- upload | whatsapp | mail
    filename          TEXT NOT NULL,
    file_path         TEXT NOT NULL,
    read_amount_cents INTEGER DEFAULT NULL,
    read_summary      TEXT DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'nieuw',   -- nieuw | doorgestuurd | mislukt
    forwarded_at      TEXT DEFAULT '',
    forward_error     TEXT DEFAULT '',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_billing_receipts_status ON billing_receipts(status);

-- Urenregel per conceptfactuur (agenda-afgeleid, dus per definitie een
-- aanname tot Vincent hem bevestigt — geblokkeerd ≠ gewerkt).
CREATE TABLE IF NOT EXISTS billing_invoice_lines (
    id            TEXT PRIMARY KEY,
    draft_id      TEXT NOT NULL,
    description   TEXT NOT NULL,
    event_date    TEXT DEFAULT '',
    hours         REAL NOT NULL DEFAULT 0,
    calendar_event_id TEXT DEFAULT '',
    excluded      INTEGER DEFAULT 0   -- Vincent kan een regel uitsluiten vóór goedkeuring
);
CREATE INDEX IF NOT EXISTS idx_billing_lines_draft ON billing_invoice_lines(draft_id);

CREATE TABLE IF NOT EXISTS billing_invoice_drafts (
    id              TEXT PRIMARY KEY,
    client_name     TEXT NOT NULL,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    hourly_rate_cents INTEGER NOT NULL DEFAULT 0,
    vat_percent     INTEGER NOT NULL DEFAULT 21,
    status          TEXT NOT NULL DEFAULT 'concept',  -- concept | goedgekeurd | geexporteerd
    export_path     TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    approved_at     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_billing_drafts_status ON billing_invoice_drafts(status);

-- Eén import van de openstaande-postenlijst uit DigiBoox (Vincent exporteert
-- die zelf periodiek — er is geen live lezing zonder API). `imported_at`
-- bepaalt of herinneringen erop mogen leunen (zie service.py).
CREATE TABLE IF NOT EXISTS billing_debtor_snapshots (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    row_count    INTEGER DEFAULT 0,
    imported_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_debtor_rows (
    id              TEXT PRIMARY KEY,
    snapshot_id     TEXT NOT NULL,
    client_name     TEXT NOT NULL,
    invoice_number  TEXT DEFAULT '',
    invoice_date    TEXT DEFAULT '',
    due_date        TEXT DEFAULT '',
    amount_cents    INTEGER NOT NULL DEFAULT 0,
    email           TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_billing_debtor_rows_snapshot ON billing_debtor_rows(snapshot_id);

-- Herinnerings-/aanmaningsconcepten, achter dezelfde Wachtrij-gate als
-- outreach: nooit automatisch verstuurd. `tone` trapt op vanaf `days_overdue`.
CREATE TABLE IF NOT EXISTS billing_reminders (
    id             TEXT PRIMARY KEY,
    debtor_row_id  TEXT NOT NULL,
    client_name    TEXT NOT NULL,
    days_overdue   INTEGER NOT NULL DEFAULT 0,
    tone           TEXT NOT NULL DEFAULT 'vriendelijk',  -- vriendelijk | dringend | aanmaning
    subject        TEXT NOT NULL DEFAULT '',
    draft          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'review',  -- review | verstuurd | overgeslagen
    created_at     TEXT NOT NULL,
    sent_at        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_billing_reminders_status ON billing_reminders(status);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True

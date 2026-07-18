-- Agent OS Remote — Neon-schema (eenmalig draaien in de Neon SQL-editor).
-- Vier tabellen, meer niet: gespiegelde wacht-op-mens-items, de besluiten-
-- outbox vanaf je telefoon, briefings als leesvoer en losse notities.

CREATE TABLE IF NOT EXISTS sync_items (
  key          TEXT PRIMARY KEY,          -- "<dismiss_kind>:<item_id>" (stabiel)
  kind         TEXT NOT NULL,             -- bv. content_review, mail_reply
  dismiss_kind TEXT NOT NULL,             -- content | mail | outreach | calendar | ...
  item_id      TEXT NOT NULL,
  title        TEXT,
  project      TEXT,
  created_at   TEXT,
  summary      TEXT,
  actions      JSONB DEFAULT '[]',
  detail       JSONB,                     -- preview: artikel-HTML, mailconcept, enz.
  status       TEXT NOT NULL DEFAULT 'active',  -- active | archived
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decisions (
  id         SERIAL PRIMARY KEY,
  item_key   TEXT NOT NULL,
  item_kind  TEXT NOT NULL,               -- dismiss_kind van het item
  item_id    TEXT NOT NULL,
  action     TEXT NOT NULL,               -- approve | reject | send | edit | dismiss
  payload    JSONB DEFAULT '{}',
  status     TEXT NOT NULL DEFAULT 'pending',   -- pending | applied | failed
  result     TEXT,                        -- wat AgentOS terugmeldde
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);

-- Dubbel tikken op dezelfde knop mag nooit twee besluiten opleveren.
CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_pending
  ON decisions (item_key) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS briefings (
  id           SERIAL PRIMARY KEY,
  payload      JSONB NOT NULL,            -- {iris: {...}, funnel: {...}}
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Web-push-abonnementen (fase 2): één rij per browser/telefoon.
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id         SERIAL PRIMARY KEY,
  endpoint   TEXT NOT NULL UNIQUE,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
  id         SERIAL PRIMARY KEY,
  text       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'pending',   -- pending | synced
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

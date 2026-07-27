-- Agent OS Remote — Neon-schema (eenmalig draaien in de Neon SQL-editor).
-- Gespiegelde wacht-op-mens-items, de besluiten-outbox vanaf je telefoon,
-- briefings als leesvoer, losse notities, plus twee tabellen die de publieke
-- voordeur bewaken: intrekbare sessies en een brute-force-rem.
-- Bijwerken van een bestaande installatie kan door dit bestand opnieuw te
-- draaien: alles is IF NOT EXISTS.

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

-- De rijke context (mail/agenda/analytics/seo/pulse) krijgt een eigen tabel met
-- precies één rij, en zit niet in `briefings`: hij ververst elke sync terwijl
-- een briefing er één per dag is. Zou hij in briefings staan, dan groeide die
-- tabel met 480 rijen per dag en werd "de laatste briefing" onvindbaar.
CREATE TABLE IF NOT EXISTS context_snapshot (
  id           INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  payload      JSONB NOT NULL,
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

-- ── Voordeur ───────────────────────────────────────────────────────────────
-- Sessies staan in de database en niet in een afgeleide HMAC, want een sessie
-- die je niet kunt intrekken is geen sessie maar een tweede wachtwoord: hij
-- blijft geldig tot je APP_PASSWORD wijzigt. Opgeslagen wordt de SHA-256 van
-- het cookie-token, zodat een database-lek geen sessies uitdeelt.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  label      TEXT,                          -- grove apparaat-hint uit de UA
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);

-- Brute-force-rem. Serverless heeft geen procesgeheugen, dus de teller moet in
-- de database staan. Het IP wordt gepepperd gehasht: genoeg om te tellen,
-- niets om te lekken.
CREATE TABLE IF NOT EXISTS login_attempts (
  ip_hash      TEXT PRIMARY KEY,
  fails        INT NOT NULL DEFAULT 0,
  first_fail   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_fail    TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_until TIMESTAMPTZ
);

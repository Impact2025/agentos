-- Agent OS Remote — Neon-schema (eenmalig draaien in de Neon SQL-editor, of via
-- `node migrate.mjs`). Gespiegelde wacht-op-mens-items, de besluiten-outbox
-- vanaf je telefoon, briefings als leesvoer, losse notities, plus twee
-- tabellen die de publieke voordeur bewaken: intrekbare sessies en een
-- brute-force-rem.
-- Bijwerken van een bestaande installatie kan door dit bestand opnieuw te
-- draaien: elke CREATE is IF NOT EXISTS, elke ALTER is idempotent (guards via
-- IF EXISTS/IF NOT EXISTS, geen DO-blokken — migrate.mjs splitst naïef op ';'
-- en zou een PL/pgSQL-blok stukknippen).
--
-- Multi-tenant (10 aug 2026): elke klant (WeAreImpact, Nicole, ...) is een
-- eigen rij in `tenants`; alle andere tabellen scopen op `tenant` (de slug).
-- Eén Vercel-deploy + één Neon-database bedient zo meerdere klanten, elk met
-- een eigen subdomein, eigen wachtwoord en eigen BRIDGE_TOKEN. Een bestaande
-- installatie (vóór deze migratie) heeft precies één klant en migreert
-- hieronder automatisch naar tenant 'weareimpact' — de ALTER-blokken per
-- tabel doen dat: kolom toevoegen, bestaande rijen backfillen, dan pas de
-- kolom verplichten. Nooit andersom, want dan breekt de backfill-UPDATE zelf
-- al op de NOT NULL-eis.

CREATE TABLE IF NOT EXISTS tenants (
  slug          TEXT PRIMARY KEY,        -- ook het subdomein: <slug>.<BASE_DOMAIN>
  name          TEXT NOT NULL,           -- weergavenaam ("WE SHAPE THE FUTURE")
  token_hash    TEXT UNIQUE NOT NULL,    -- SHA-256 van BRIDGE_TOKEN (de lokale AgentOS-instance)
  password_hash TEXT NOT NULL,           -- scrypt van het inlogwachtwoord voor de telefoon/browser
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Live agenda/GSC zonder AgentOS (12 aug 2026): per-tenant kopie van het
-- Google-service-account, meegestuurd door de lokale bridge_sync bij elke
-- push (api/bridge.js:push) — één bron van waarheid (de lokale .env), geen
-- los provisioneringsscript met een geplakte sleutel. calendar_private_key_enc
-- is versleuteld (AES-256-GCM, api/_crypto.js) met TENANT_SECRET_KEY, een
-- Vercel-only secret die nooit lokaal hoeft te staan. Eén globale env-var
-- zou hier fout zijn: dit is multi-tenant, en klant A's agenda mag nooit met
-- klant B's credentials worden opgehaald.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS calendar_client_email TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS calendar_private_key_enc TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS calendar_calendar_id TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS calendar_busy_ids TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS calendar_sub TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS gsc_sites JSONB DEFAULT '[]'::jsonb;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS google_synced_at TIMESTAMPTZ;
-- Zichtbare diagnose i.p.v. een stille terugval op cache: dezelfde reden
-- waarom scheduler_gaps/integrity_findings bestaan in de Python-kant.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS google_last_error TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS google_last_error_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS sync_items (
  tenant       TEXT NOT NULL,
  key          TEXT NOT NULL,             -- "<dismiss_kind>:<item_id>" (stabiel binnen een tenant)
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
ALTER TABLE sync_items ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE sync_items SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE sync_items ALTER COLUMN tenant SET NOT NULL;
-- Vóór multi-tenant was `key` zelf de primary key; nu is dat pas binnen een
-- tenant uniek (twee klanten kunnen allebei ooit "mail:42" hebben).
ALTER TABLE sync_items DROP CONSTRAINT IF EXISTS sync_items_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_items_tenant_key ON sync_items (tenant, key);
CREATE INDEX IF NOT EXISTS idx_sync_items_tenant_status ON sync_items (tenant, status, updated_at);

CREATE TABLE IF NOT EXISTS decisions (
  id         SERIAL PRIMARY KEY,
  tenant     TEXT NOT NULL,
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
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE decisions SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE decisions ALTER COLUMN tenant SET NOT NULL;

-- Dubbel tikken op dezelfde knop mag nooit twee besluiten opleveren — nu per tenant.
DROP INDEX IF EXISTS idx_decisions_pending;
CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_pending
  ON decisions (tenant, item_key) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_decisions_tenant_created ON decisions (tenant, created_at);

CREATE TABLE IF NOT EXISTS briefings (
  id           SERIAL PRIMARY KEY,
  tenant       TEXT NOT NULL,
  payload      JSONB NOT NULL,            -- {iris: {...}, funnel: {...}}
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE briefings ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE briefings SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE briefings ALTER COLUMN tenant SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_briefings_tenant_gen ON briefings (tenant, generated_at DESC);

-- De rijke context (mail/agenda/analytics/seo/pulse) krijgt een eigen tabel met
-- precies één rij PER TENANT, en zit niet in `briefings`: hij ververst elke
-- sync terwijl een briefing er één per dag is. Zou hij in briefings staan, dan
-- groeide die tabel met 480 rijen per dag en werd "de laatste briefing"
-- onvindbaar. Vóór multi-tenant stond hier een harde `id=1 CHECK` — precies
-- één rij voor de hele app; die CHECK moet weg vóórdat een tweede tenant
-- ooit een rij kan wegschrijven.
CREATE TABLE IF NOT EXISTS context_snapshot (
  tenant       TEXT NOT NULL,
  payload      JSONB NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE context_snapshot ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE context_snapshot SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE context_snapshot ALTER COLUMN tenant SET NOT NULL;
ALTER TABLE context_snapshot DROP CONSTRAINT IF EXISTS context_snapshot_pkey;
ALTER TABLE context_snapshot DROP CONSTRAINT IF EXISTS context_snapshot_id_check;
ALTER TABLE context_snapshot DROP COLUMN IF EXISTS id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_context_snapshot_tenant ON context_snapshot (tenant);

-- Web-push-abonnementen (fase 2): één rij per browser/telefoon, per tenant —
-- anders krijgt Nicole's telefoon een melding over jouw Wachtrij.
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id         SERIAL PRIMARY KEY,
  tenant     TEXT NOT NULL,
  endpoint   TEXT NOT NULL UNIQUE,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE push_subscriptions SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE push_subscriptions ALTER COLUMN tenant SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_push_subs_tenant ON push_subscriptions (tenant);

CREATE TABLE IF NOT EXISTS notes (
  id         SERIAL PRIMARY KEY,
  tenant     TEXT NOT NULL,
  text       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'pending',   -- pending | synced
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE notes ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE notes SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE notes ALTER COLUMN tenant SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notes_tenant ON notes (tenant, status, created_at);

-- ── Voordeur ───────────────────────────────────────────────────────────────
-- Sessies staan in de database en niet in een afgeleide HMAC, want een sessie
-- die je niet kunt intrekken is geen sessie maar een tweede wachtwoord: hij
-- blijft geldig tot je het tenant-wachtwoord wijzigt. Opgeslagen wordt de
-- SHA-256 van het cookie-token, zodat een database-lek geen sessies uitdeelt.
-- `tenant` staat óók op de sessie zelf (niet alleen af te leiden uit het
-- subdomein bij inloggen): requireSession() vergelijkt de sessie-tenant met
-- de tenant van het huidige verzoek, zodat een cookie die per ongeluk op een
-- ander subdomein belandt niet stilzwijgend voor een andere klant werkt.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  tenant     TEXT NOT NULL,
  label      TEXT,                          -- grove apparaat-hint uit de UA
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS tenant TEXT;
UPDATE sessions SET tenant = 'weareimpact' WHERE tenant IS NULL;
ALTER TABLE sessions ALTER COLUMN tenant SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions (tenant);

-- Brute-force-rem. Bewust NIET tenant-gescoped (het IP is de aanvaller, niet de
-- klant), en serverless heeft geen procesgeheugen, dus de teller moet in de
-- database staan. Het IP wordt gepepperd gehasht: genoeg om te tellen, niets
-- om te lekken. De pepper komt uit IP_PEPPER (nieuw — vóór multi-tenant leende
-- dit de globale BRIDGE_TOKEN, die nu per tenant verschilt en dus geen goede
-- gedeelde pepper meer is).
CREATE TABLE IF NOT EXISTS login_attempts (
  ip_hash      TEXT PRIMARY KEY,
  fails        INT NOT NULL DEFAULT 0,
  first_fail   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_fail    TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_until TIMESTAMPTZ
);

-- Iris-onboarding stap 3 (OAuth-relay, zie api/oauth.js): kortlevende,
-- eenmalig te gebruiken CSRF-state. De OAuth `state`-param draagt hier ook
-- routing-info (welke tenant/site dit is) — zonder een server-side binding
-- zou een aanvaller een authorize-link met een geraden site_id kunnen delen
-- en zijn eigen Google/Microsoft-account aan iemand anders' site koppelen.
-- `authorize` schrijft een rij (na requireSession — alleen een ingelogde
-- gebruiker van die tenant mag een koppelpoging starten), `callback` leest
-- 'm op state en verwijdert 'm meteen (eenmalig, dus een herhaalde callback
-- met dezelfde code faalt in plaats van een tweede keer toe te passen).
CREATE TABLE IF NOT EXISTS oauth_state (
  state      TEXT PRIMARY KEY,
  tenant     TEXT NOT NULL,
  site_id    TEXT NOT NULL,
  provider   TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Impact OS Remote — Neon-schema (eenmalig draaien in de Neon SQL-editor, of via
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
  token_hash    TEXT UNIQUE NOT NULL,    -- SHA-256 van BRIDGE_TOKEN (de lokale ImpactOS-instance)
  password_hash TEXT NOT NULL,           -- scrypt van het inlogwachtwoord voor de telefoon/browser
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Live agenda/GSC zonder ImpactOS (12 aug 2026): per-tenant kopie van het
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
  result     TEXT,                        -- wat ImpactOS terugmeldde
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

-- Impact Calculator-leads (weareimpact.nl/impact-calculator): de website pusht
-- elke ontgrendeling hier rechtstreeks naartoe (op=impact-lead) — dit ontstaat
-- buiten ImpactOS om, dus is er geen lokale rij om op te reageren totdat de
-- bridge-sync 'm ophaalt (op=impact-leads), verrijkt en Iris er een verslag
-- over laat schrijven. Zelfde pending/ack-vorm als notes, eigen tabel omdat de
-- payload en verwerking niets met een vault-notitie te maken hebben.
CREATE TABLE IF NOT EXISTS impact_leads (
  id           SERIAL PRIMARY KEY,
  tenant       TEXT NOT NULL,
  email        TEXT NOT NULL,
  naam         TEXT,
  organisatie  TEXT,
  inputs       JSONB,
  results      JSONB,
  status       TEXT NOT NULL DEFAULT 'pending',   -- pending | processed | failed
  error        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_impact_leads_tenant ON impact_leads (tenant, status, created_at);

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

-- Iris-onboarding OAuth-accounts (per klant gekoppeld via de wizard in
-- remote/api/oauth.js, weggeschreven door de backend-relay
-- backend/domains/onboarding/resolve.py:store_relayed_token). Eén rij per
-- (site_id, provider) — de per-klant Google/Microsoft-credentials die de
-- cloud-agenda-lezer en de GSC-sync gebruiken. Volledig gescheiden van de
-- service-account-kolommen in `tenants`.
CREATE TABLE IF NOT EXISTS oauth_accounts (
  id               TEXT PRIMARY KEY,
  site_id          TEXT NOT NULL,
  provider         TEXT NOT NULL,
  account_email    TEXT,
  credentials_json TEXT NOT NULL,
  scopes           TEXT DEFAULT '',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (site_id, provider)
);

-- ── WhatsApp (16 aug 2026) ───────────────────────────────────────────────────
-- Iris appen: één Meta-app/WABA-token bedient in principe alle klanten (het
-- token en de webhook-secret zijn dus gewone Vercel-env-vars, net als
-- OPENROUTER_API_KEY — geen klantgeheim), maar élk 06-nummer hoort bij precies
-- één tenant en mag alleen antwoorden aan de afzenders die die klant heeft
-- opgegeven. Zonder die koppeling zou een binnenkomend bericht op het GEEN
-- tenant weten te kiezen (of, erger, de verkeerde), en Iris zou dan met de
-- context van klant A tegen klant B praten. `whatsapp_phone_number_id` is het
-- Meta-ID van Iris' eígen nummer (niet de afzender); daarop routeert de
-- webhook. `whatsapp_allowed_from` is een kommagescheiden lijst van E.164-
-- nummers zonder '+' (zoals Meta ze aanlevert) die met dat nummer mogen praten
-- — een onbekende afzender krijgt nooit antwoord, ook niet "wie ben je": een
-- vreemde die het nummer weet te raden praat anders zomaar tegen je bedrijfsdata.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id TEXT UNIQUE;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS whatsapp_allowed_from TEXT;

-- Eén WhatsApp-draadje per (tenant, afzender) — WhatsApp kent zelf geen
-- "sessie" zoals de app-chat (die stuurt zijn hele geschiedenis elke keer
-- mee); zonder deze tabel zou Iris bij elk bericht met geheugenverlies
-- beginnen. `messages` is dezelfde vorm als wat iris.js al gebruikt
-- ([{role, content}, ...]), afgekapt op MAX_TURNS vóór het opslaan.
CREATE TABLE IF NOT EXISTS whatsapp_threads (
  tenant     TEXT NOT NULL,
  wa_id      TEXT NOT NULL,
  messages   JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, wa_id)
);
-- Welk project een klantengesprek betreft (18 aug 2026, klant-modus). Één
-- gedeeld nummer bedient alle projecten van een tenant; zonder dit zou Iris
-- bij élk bericht opnieuw moeten raden of vragen welk bedrijf het betreft.
-- NULL = nog niet vastgesteld (of dit is een manager-gesprek, waar het niet
-- van toepassing is).
ALTER TABLE whatsapp_threads ADD COLUMN IF NOT EXISTS project TEXT;

-- Communicatie-overzicht (22 aug 2026): tot dusver was er geen enkele plek
-- die liet zien wíe er appt behalve wanneer klant-Iris vastliep (escalaties).
-- `contact_name` komt uit Meta's `value.contacts[].profile.name`, meegestuurd
-- bij elk binnenkomend bericht maar tot nu toe nooit opgeslagen — zonder dat
-- toont elk overzicht alleen een telefoonnummer. `created_at` krijgt bewust
-- GEEN default bij de ALTER (dat zou elke bestaande rij op "nu" zetten en dus
-- élk bestaand gesprek als "nieuw contact" laten binnenkomen); backfill op de
-- enige tijdstip die we al kennen (updated_at), pas dáárna een DEFAULT voor
-- nieuwe rijen. `ON CONFLICT DO UPDATE` in whatsapp.js:saveThread raakt deze
-- kolom nooit aan, dus blijft hij vastgepind op het allereerste bericht.
ALTER TABLE whatsapp_threads ADD COLUMN IF NOT EXISTS contact_name TEXT;
ALTER TABLE whatsapp_threads ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
UPDATE whatsapp_threads SET created_at = updated_at WHERE created_at IS NULL;
ALTER TABLE whatsapp_threads ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE whatsapp_threads ALTER COLUMN created_at SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_whatsapp_threads_tenant_updated ON whatsapp_threads (tenant, updated_at DESC);

-- Klant-gesprek dat Iris niet uit de kennisbank kon/mocht beantwoorden
-- (onzeker antwoord, of iets met gevolgen: offerte, afspraak, klacht,
-- persoonsgegevens). Bewust GEEN local-execution-omweg via `decisions` zoals
-- de rest van de bridge: het versturen van het antwoord heeft alleen het
-- gedeelde WHATSAPP_TOKEN nodig (al aanwezig in de Vercel-env), dus Vincents
-- eigen typewerk in Iris Remote kan meteen naar de klant — geen 3 minuten
-- wachten op de eerstvolgende bridge_sync voor iets dat al bij Vercel ligt.
CREATE TABLE IF NOT EXISTS whatsapp_escalations (
  id              SERIAL PRIMARY KEY,
  tenant          TEXT NOT NULL,
  wa_id           TEXT NOT NULL,
  phone_number_id TEXT NOT NULL,
  project         TEXT,
  question        TEXT NOT NULL,
  reason          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open',   -- open | answered | dismissed
  reply_text      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  answered_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_escalations_open
  ON whatsapp_escalations (tenant, status, created_at);

-- Meta levert 'at least once' — bij een trage reactie (LLM + tool-rondes kan
-- een paar seconden duren) stuurt hij een webhook soms nog een keer. De
-- deduplicatie is hier een state-machine (zie api/whatsapp.js: claimMessage /
-- markReplied) in plaats van een eenmalige "gezien"-flag:
--   status 'received' = bericht geclaimd, verwerking (nog) niet voltooid.
--                        Een Vercel-timeout of crash laat het hier staan, dus
--                        Meta's retry krijgt een nieuwe kans in plaats van dat
--                        het bericht stil gedropt wordt.
--   status 'replied'  = antwoord daadwerkelijk verzonden. Een retry op een
--                        'replied' rij wordt vroeg gedropt (al geleverd).
-- Alleen zo kan een > 60s durende verwerking nooit een bericht laten
-- verdwijnen. Oude 'received'-rijen (> 1u) ruimt de webhook zelf op.
CREATE TABLE IF NOT EXISTS whatsapp_processed (
  message_id    TEXT PRIMARY KEY,
  status        TEXT NOT NULL DEFAULT 'received',   -- received | replied
  processed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_processed_at ON whatsapp_processed (processed_at);
CREATE INDEX IF NOT EXISTS idx_whatsapp_processed_status ON whatsapp_processed (status, processed_at);

-- Per-afzender rate-limit (kostenbescherming, api/whatsapp.js: throttled).
-- Eén rij per wa_id; tellers rollen per venster terug naar 1. Beschermt tegen
-- spam-loops en LLM-kosten zonder legitieme klantengesprekken te breken.
-- Grenzen: > 20 berichten/uur OF > 6 berichten/60s ⇒ drop vóór de LLM.
CREATE TABLE IF NOT EXISTS whatsapp_throttle (
  wa_id         TEXT PRIMARY KEY,
  count_1h      INT NOT NULL DEFAULT 1,
  count_1m      INT NOT NULL DEFAULT 1,
  window_start  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_throttle_win ON whatsapp_throttle (window_start);

-- Kostenrem voor klant-Iris (18 aug 2026): dit nummer staat open voor
-- iedereen (bewust, zie CLAUDE.md 14f) en elk klantbericht kost een paar
-- LLM-rondes. Zonder plafond kan één grap, bot, of eindeloze loop de rekening
-- laten oplopen — dezelfde reden waarom de rest van ImpactOS overal
-- `DAILY_TOKEN_BUDGET`/`require_llm_budget` heeft. Telt per (tenant, wa_id,
-- dag); simpel genoeg om atomisch bij te werken zonder een aparte lock.
CREATE TABLE IF NOT EXISTS whatsapp_rate_limit (
  tenant TEXT NOT NULL,
  wa_id  TEXT NOT NULL,
  day    DATE NOT NULL,
  count  INT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant, wa_id, day)
);

-- ── LSP-workshop "Bouw je AI-assistent" (24 aug 2026, AI Leadership Lab) ────
-- Eenmalig evenement: teams sturen een foto van hun LEGO-bouwwerk (grootste
-- administratieve frictie, of hun ideale AI-assistent) + één toelichtende
-- regel naar Iris, via WhatsApp (LSP_WORKSHOP_KEYWORD in het bijschrift) of
-- e-mail (zelfde woord in het onderwerp, via Vincents eigen postvak). Eén
-- analysepad (_lsp_core.js) bedient beide kanalen. `image_data_url` staat als
-- tekst in Neon i.p.v. losse blob-storage: bij ~10 teams en het bestaande
-- 5MB-plafond (_whatsapp_media.js) is dat voor een eenmalig evenement geen
-- probleem. `impactos_synced` is het bridge-pullvlag, zelfde vorm als
-- impact_leads.status hierboven — de bridge (backend/domains/bridge/
-- lsp_workshop.py) haalt nieuwe rijen op en logt er een Actiecentrum-kaart
-- van via outcomes.log_outcome.
CREATE TABLE IF NOT EXISTS lsp_submissions (
  id                  SERIAL PRIMARY KEY,
  tenant              TEXT NOT NULL,
  source              TEXT NOT NULL,           -- whatsapp | email
  sender              TEXT NOT NULL,           -- wa_id of e-mailadres
  contact_name        TEXT,
  team_label          TEXT,
  note_text           TEXT,
  image_data_url      TEXT,
  dashboard_summary   TEXT,
  participant_report  TEXT,
  status              TEXT NOT NULL DEFAULT 'nieuw',  -- nieuw | verwerkt | fout
  error               TEXT,
  impactos_synced     BOOLEAN NOT NULL DEFAULT false,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lsp_submissions_tenant_created
  ON lsp_submissions (tenant, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lsp_submissions_unsynced
  ON lsp_submissions (tenant, impactos_synced) WHERE impactos_synced = false;

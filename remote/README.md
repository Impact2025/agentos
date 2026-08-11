# Iris Remote — cloud-companion voor Agent OS

PWA-achtige assistent (Vercel + Neon) waarmee Vincent onderweg — of met de pc
uit — zijn dag overziet én de review-gates bedient: agenda en mailbox in één
scherm, een deterministisch "wat gaat goed / wat gaat slecht", GA4- en
GSC-cijfers, Wachtrij-artikelen, helpdesk-mails, outreach en agendavoorstellen
goedkeuren/afwijzen, werk aanzwengelen, met Iris chatten, en notities
achterlaten die de vault in stromen.

**Architectuur (pull-model):** de lokale AgentOS-machine belt elke
`BRIDGE_SYNC_MINUTES` (default 3) zelf naar buiten — geen open poorten, geen
tunnel. Push: alle wacht-op-mens-items (Actiecentrum) + previews + briefing →
Neon. Pull: besluiten die onderweg genomen zijn; die worden lokaal uitgevoerd
via exact dezelfde servicefuncties als de UI-knoppen (whitelist in
`backend/domains/bridge/actions.py`), dus alle gates blijven gelden. Staat de
pc uit, dan stapelen besluiten zich op en voert de eerstvolgende sync ze uit.

## Eenmalige setup

### 1. Neon (database)
1. Maak een gratis project op https://neon.tech (regio: Frankfurt).
2. Kopieer de connection string (postgres://…) → dit wordt `DATABASE_URL`.
3. Schema toepassen: zet `DATABASE_URL` in `remote/.env.dev.local` en draai
   `node migrate.mjs`. (Of plak `schema.sql` in de Neon SQL-editor.) Alles is
   `IF NOT EXISTS`/idempotent, dus herhalen is veilig — draai het opnieuw na
   een update om nieuwe tabellen/kolommen erbij te krijgen. Sinds 10 aug 2026
   is dit schema **multi-tenant**: een bestaande installatie migreert
   automatisch naar tenant `weareimpact` (zie de ALTER-blokken in `schema.sql`).

### 2. GitHub + Vercel (hosting)
1. Zorg dat deze repo (of alleen de map `remote/`) op GitHub staat.
2. https://vercel.com → *Add New Project* → importeer de repo.
3. **Root Directory: `remote`** (belangrijk — anders probeert Vercel de
   Python-backend te bouwen). Framework preset: *Other*.
4. Environment variables:
   | Naam | Waarde |
   |---|---|
   | `DATABASE_URL` | de Neon-connection-string |
   | `IP_PEPPER` | lang random geheim voor de brute-force-rem, bv. `openssl rand -hex 32` (niet per klant — één waarde voor de hele deploy) |
   | `BASE_DOMAIN` | jouw eigen domein zonder subdomein, bv. `domein.nl` — bepaalt welk subdomein bij welke klant hoort (`nicole.domein.nl` → tenant `nicole`). Leeg = iedereen praat met `DEFAULT_TENANT` (de huidige `*.vercel.app`-situatie) |
   | `DEFAULT_TENANT` | welke tenant-slug geldt als er geen `BASE_DOMAIN`-match is, default `weareimpact` |
   | `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | voor push-meldingen: `npx web-push generate-vapid-keys` (optioneel — zonder keys geen meldingen, verder werkt alles) |
   | `VAPID_SUBJECT` | `mailto:v.munster@weareimpact.nl` |
   | **LLM voor cloud-Iris** | Kies één provider — cloud-Iris kiest OpenRouter als die key er staat, anders OpenModel. Zonder een van beide werkt alles behalve de Iris-chat. |
   | `OPENROUTER_API_KEY` | zelfde key als lokaal. `iris.js` praat dan in OpenAI-formaat met OpenRouter (Bearer-auth) |
   | `OPENROUTER_MODEL` | optioneel, default `anthropic/claude-sonnet-4-5` (of je lokale `CLAUDE_VIA_OPENROUTER`) |
   | `OPENMODEL_API_KEY` | alternatief voor OpenRouter: de OpenModel-gateway (Anthropic-formaat) |
   | `OPENMODEL_SMART_MODEL` | model bij OpenModel, bv. `claude-sonnet-4-6`. Zonder valt ze terug op het bulkmodel — dommere Iris |
   | **Iris-onboarding — per-klant OAuth** (`api/oauth.js`) | De browser-consentredirect loopt hier (publiek bereikbaar), niet via de lokale instance — zie CLAUDE.md 14. Zelfde client_id/secret horen ook in de lokale `.env` van elke klant-instance (voor het ververs-token-endpoint, ná de eerste koppeling), zie `.env.example`. |
   | `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google Cloud Console > Credentials > OAuth client ID (Web application). Redirect-URI: `https://<jouw-deploy>/api/oauth?provider=google&op=callback` |
   | `OUTLOOK_CLIENT_ID` / `OUTLOOK_CLIENT_SECRET` / `OUTLOOK_TENANT_ID` | Zelfde Azure-app als de lokale Outlook-integratie, plus een client secret (App registrations > Certificates & secrets). Redirect-URI: `https://<jouw-deploy>/api/oauth?provider=microsoft&op=callback` |

   `BRIDGE_TOKEN` en `APP_PASSWORD` staan **niet** meer als globale env var —
   die zijn per klant en leven als gehashte kolommen in de `tenants`-tabel
   (zie "Meerdere klanten" hieronder). Elke bestaande deploy moet dus éénmalig
   een tenant-rij krijgen, ook voor de eerste/enige klant.
5. Deploy → noteer de URL (bv. `https://agentos-pearl-tau.vercel.app`).

### 3. Een klant provisioneren (eenmalig per klant, ook de eerste)
```
cd remote
node scripts/add-tenant.mjs weareimpact "Agent OS"
```
Het script vraagt een wachtwoord (min. 16 tekens, getypt = gemaskeerd) en
print daarna éénmalig een `BRIDGE_TOKEN` — die staat daarna alleen nog gehasht
in de database, dus bewaar 'm meteen. Opnieuw draaien voor dezelfde slug
roteert wachtwoord én token (de oude worden dan ongeldig).

### 4. Lokaal (AgentOS `.env`, per instance)
```
BRIDGE_REMOTE_URL=https://agentos-pearl-tau.vercel.app
BRIDGE_TOKEN=<token uit add-tenant.mjs voor déze klant>
BRIDGE_SYNC_MINUTES=3
```
Herstart AgentOS (`agentos_service.cmd`, of `agentos_service_<klant>.cmd`). De
scheduler-job `bridge_sync` draait dan elke 3 minuten; handmatig testen:
`POST /api/bridge/sync-now`, status via `GET /api/bridge/status`. Alle klanten
delen dezelfde `BRIDGE_REMOTE_URL` — het `BRIDGE_TOKEN` bepaalt welke tenant.

### 5. Telefoon
Open de klant-specifieke URL (met `BASE_DOMAIN`: `https://<slug>.<domein>`;
zonder: de Vercel-URL werkt voor `DEFAULT_TENANT`), log in met het wachtwoord
uit stap 3, en kies "Zet op beginscherm" — dan gedraagt het zich als app
(donker Iris-thema, bottom-nav). Meldingen aanzetten: tab *Systeem* →
"Meldingen inschakelen". Op iPhone werkt web-push alléén vanuit de
op-het-beginscherm-gezette app (iOS 16.4+), niet vanuit Safari zelf.

## Meerdere klanten (multi-tenant)

Eén Vercel-deploy + één Neon-database bedient meerdere klanten. Wat een
klant scheidt van de rest:

- **Eigen rij in `tenants`** (`slug`, weergavenaam, gehasht wachtwoord, gehasht
  `BRIDGE_TOKEN`) — aangemaakt via `node scripts/add-tenant.mjs <slug> "<naam>"`.
- **Eigen subdomein** (`nicole.domein.nl`) bepaalt voor de brówser welke tenant
  een verzoek hoort te zien (`_lib.js:tenantFromHost`). Zonder `BASE_DOMAIN`
  gezet in Vercel valt alles terug op `DEFAULT_TENANT` — handig zolang er nog
  geen eigen domein aan hangt, maar dan is er dus maar één klant bereikbaar.
- **Eigen `BRIDGE_TOKEN`** bepaalt voor de lokale AgentOS-machine welke tenant
  ze pushen/pullen (`_lib.js:resolveBridgeTenant`) — dat gaat via de hash van
  het token, niet via het subdomein, want alle instances praten met dezelfde
  `BRIDGE_REMOTE_URL`.
- Elke tabel (`sync_items`, `decisions`, `briefings`, `context_snapshot`,
  `notes`, `sessions`, `push_subscriptions`) draagt een `tenant`-kolom en elke
  query filtert erop — geen enkele klant kan andermans data zien, ook niet per
  ongeluk via een gedeelde primary key (die zijn allemaal `(tenant, ...)`
  geworden).

**Wildcard-domein instellen in Vercel** (Vincent, handmatig — vergt toegang tot
je Vercel-project en DNS die ik hier niet heb):
1. Vercel-project → *Settings → Domains* → voeg `*.domein.nl` toe (of losse
   subdomeinen per klant, als je liever geen wildcard wilt).
2. Zet bij je DNS-provider een `CNAME *.domein.nl → cname.vercel-dns.com`
   (Vercel toont de exacte waarde).
3. Zet `BASE_DOMAIN=domein.nl` in de Vercel-environment-variables en redeploy.
4. Vanaf dan opent `<slug>.domein.nl` automatisch de juiste tenant — er is geen
   per-subdomein Vercel-configuratie nodig, dat loopt allemaal via de ene
   wildcard plus de `tenants`-tabel.

## Wat er verder in zit
- **Vandaag** (startscherm): je agenda van vandaag met het eerstvolgende blok en
  de vrije gaten, de stand van je mailbox (achterstand, urgente berichten,
  oudste open vraag), en bovenaan `pulse` — een lijst *wat vraagt aandacht* en
  *wat gaat goed*, deterministisch afgeleid uit de cijfers. Geen LLM: dit moet
  blijven werken als de gateway plat ligt, en het is meteen de grondstof die
  cloud-Iris meekrijgt zodat ze niet hoeft te gokken.
- **Cijfers** (tab Briefings, onderaan): GA4 laatste 7 dagen naast de 7 daarvóór,
  verkeersbronnen, best bekeken pagina's, en per site de pagina's die stijgen of
  wegzakken — met een knop om Iris de dalers te laten verrijken.
- **Werk aanzwengelen**: knoppen (en Iris' `start_werk`-tool) voor
  `content_run`, `seo_refresh`, `outreach_run`, `lead_search`,
  `linkbuilding_run`, `mail_sync`, `helpdesk_run`, `iris_briefing`,
  `context_refresh` en `digest`. Ze hergebruiken Iris' eigen hendels inclusief
  klemmen en dedupe (max 1 run per dag per doelwit), en het resultaat landt
  ALTIJD in een review-gate — er gaat niets live.
- **Cloud-Iris** (tab Briefings): een echte agent-lus met tools over de laatste
  snapshot (`lees_context`, `lees_besluiten`, `lees_briefing`, `start_werk`,
  `stel_besluit_voor`). Ze mag werk starten; ze mag géén gate passeren. Vindt ze
  dat er iets goedgekeurd of verstuurd moet worden, dan levert ze een voorstel
  dat als knop verschijnt — jij tikt. Zou ze dat zelf mogen queuen, dan was het
  model de goedkeurder geworden en had de review-gate geen betekenis meer.
- **Push-meldingen**: bij een écht nieuw besluit in de sync (geen herhaal-spam
  bij elke push) en wanneer een onderweg genomen besluit lokaal mislukt.

De rijke context staat in `context_snapshot` (precies één rij, elke sync
overschreven) en niet in `briefings`: hij ververst elke sync terwijl een
briefing er één per dag is. Lokaal wordt hij gecachet in
`bridge_context_cache` met een eigen TTL per sectie (mail 5 min, agenda 15 min,
SEO 60 min, GA4 6 uur) — zonder die cache zou een sync elke drie minuten GA4,
Graph en Google Agenda bevragen.

## Veiligheid

Achter dit ene wachtwoord zitten "publiceren" en "mail versturen", op een adres
dat iedereen kan bereiken. Daar is de voordeur naar gebouwd:

- **Elk tenant-wachtwoord moet ≥ 16 tekens zijn** (`scripts/add-tenant.mjs`
  weigert kortere) — een korte code is hier het zwakste punt van het hele
  systeem en geen enkele rem daarna maakt dat goed. Wachtwoorden staan nooit
  in platte tekst in Neon: `password_hash` is scrypt (traag, gezouten), niet
  sha256 — dat laatste is voor hoge-entropie tokens, niet voor mensentekst.
- **Brute-force-rem** (`login_attempts`): vijf misslagen gratis, daarna
  verdubbelt de wachttijd per poging tot een uur. Per IP, gepepperd gehasht —
  genoeg om te tellen, niets om te lekken. De teller staat in de database, want
  serverless heeft geen procesgeheugen.
- **Sessies zijn intrekbaar** (`sessions`): het cookie is een random token
  waarvan alleen de SHA-256 in de database staat. *Systeem → Overal uitloggen*
  trekt élk apparaat in. (Eerder was het cookie een afgeleide van je wachtwoord:
  dan is uitloggen alleen lokaal en blijft een gelekt cookie geldig tot je het
  wachtwoord wijzigt.)
- **Content-Security-Policy met `script-src 'self'`**: er draait geen enkel
  script van derden op de pagina die publicaties goedkeurt. Dat kan sinds
  Tailwind en de fonts lokaal staan (zie *Assets* hieronder).
- Geen secrets in Neon in leesbare vorm — wachtwoorden en bridge-tokens staan
  alleen als hash, verder alleen werkdata (previews + besluiten). Opgeruimd na 14 dagen.
- Twee gescheiden sloten: bearer-token → tenant voor de bridge, sessiecookie
  (tenant-gebonden, geverifieerd tegen het subdomein) voor de UI.
- **Elke tabel is tenant-gescoped** — twee klanten delen dezelfde database maar
  nooit een rij; elke query filtert op `tenant`, en de unieke sleutels zijn
  allemaal `(tenant, ...)` in plaats van globaal.
- De cloud kan nooit iets publiceren of versturen: een besluit is niets anders
  dan dezelfde lokale knop, later ingedrukt — inclusief kwaliteitsgate,
  adres-validatie en conflict-checks.

## Assets (Tailwind + fonts)

`tailwind.css`, `fonts.css` en `fonts/` zijn **gegenereerd en staan in git** —
net als `frontend/tailwind.css`. Zo hangt een deploy niet af van een live
download, en draait Vercel geen buildstap.

Regenereren na een stijl- of icoonwijziging:
```
cd remote && npm install && npm run assets
```
- `npm run assets:css` — Tailwind vooraf bouwen (24 KB) uit `tailwind.config.js`.
  Dit verving `cdn.tailwindcss.com`, dat de CSS ín de browser compileerde: traag
  op mobiel, een flits ongestileerde pagina, en een script van derden op de
  goedkeur-pagina.
- `npm run assets:fonts` — `build-fonts.mjs` haalt Inter/JetBrains (alleen de
  latin-subsets) en Material Symbols op. Van die laatste **alleen de iconen die
  de app gebruikt**: 15 KB in plaats van 1,1 MB. Het script drukt de gevonden
  iconenlijst af — controleer die als je iconen toevoegt, want een icoon dat er
  niet in zit rendert als losse tekst ("warning") in plaats van een pictogram.

## Offline

De service worker cachet de schil (HTML/CSS/JS/fonts), dus de app opent in de
trein zonder wit scherm. `/api/*` gaat **altijd** naar het netwerk en wordt
nooit gecachet: een besluit nemen op een gecachete inbox is goedkeuren wat je
niet meer ziet.

## Bestanden
- `api/bridge.js` — push/decisions/ack/notes (bearer, alleen voor de lokale machine)
- `api/ui.js` — login/items/decide/briefing/notes/outbox/sessions (sessiecookie, voor jou)
- `api/_lib.js` — Neon-client, wachtwoord- en sessiebeheer, brute-force-rem
- `index.html` + `app.js` + `style.css` — Iris Remote-frontend
- `tailwind.config.js` + `tailwind-src.css` + `build-fonts.mjs` — assets-build
- `schema.sql` — het schema; `node migrate.mjs` past het toe op `DATABASE_URL`
- `dev-server.mjs` — lokaal draaien: `npm run dev -- 8642`

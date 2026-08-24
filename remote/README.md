# Iris Remote — cloud-companion voor Impact OS

PWA-achtige assistent (Vercel + Neon) waarmee Vincent onderweg — of met de pc
uit — zijn dag overziet én de review-gates bedient: agenda en mailbox in één
scherm, een deterministisch "wat gaat goed / wat gaat slecht", GA4- en
GSC-cijfers, Wachtrij-artikelen, helpdesk-mails, outreach en agendavoorstellen
goedkeuren/afwijzen, werk aanzwengelen, met Iris chatten, en notities
achterlaten die de vault in stromen.

**Architectuur (pull-model):** de lokale ImpactOS-machine belt elke
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
   | `TENANT_SECRET_KEY` | **Voor live agenda/GSC zonder ImpactOS** (zie hieronder). 32 bytes base64, bv. `openssl rand -base64 32`. Vercel-only — nooit lokaal nodig. Ontbreekt hij, dan blijft alles werken zoals voorheen (cache-only), er verschijnt alleen geen live-badge. |

   `BRIDGE_TOKEN` en `APP_PASSWORD` staan **niet** meer als globale env var —
   die zijn per klant en leven als gehashte kolommen in de `tenants`-tabel
   (zie "Meerdere klanten" hieronder). Elke bestaande deploy moet dus éénmalig
   een tenant-rij krijgen, ook voor de eerste/enige klant.
5. Deploy → noteer de URL (bv. `agentos-pearl-tau.vercel.app`).

### 3. Een klant provisioneren (eenmalig per klant, ook de eerste)
```
cd remote
node scripts/add-tenant.mjs weareimpact "Impact OS"
```
Het script vraagt een wachtwoord (min. 16 tekens, getypt = gemaskeerd) en
print daarna éénmalig een `BRIDGE_TOKEN` — die staat daarna alleen nog gehasht
in de database, dus bewaar 'm meteen. Opnieuw draaien voor dezelfde slug
roteert wachtwoord én token (de oude worden dan ongeldig).

### 4. Lokaal (ImpactOS `.env`, per instance)
```
BRIDGE_REMOTE_URL=agentos-pearl-tau.vercel.app
BRIDGE_TOKEN=<token uit add-tenant.mjs voor déze klant>
BRIDGE_SYNC_MINUTES=3
```
Herstart ImpactOS (`impactos_service.cmd`, of `impactos_service_<klant>.cmd`). De
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

### 6. WhatsApp (optioneel — Iris appen)

Praat met Iris via WhatsApp, met dezelfde tool-lus als de app-chat: ze leest
de snapshot, mag `start_werk`/`plan_agenda` in de rij zetten (landt achter de
gates, uitgevoerd door de lokale `bridge_sync` zoals altijd) en kan een
besluit alleen vóórstellen, nooit zelf goedkeuren. Het webhook-endpoint is
`api/whatsapp.js`; de tool-lus zelf staat in `api/_iris_core.js`, gedeeld met
`api/iris.js` — er is maar één plek waar staat wat Iris mag.

**Wat alleen jij kunt doen (Meta-account, buiten mijn bereik):**
1. Maak een Meta Business-account + een **App** (type "Business") op
   [developers.facebook.com](https://developers.facebook.com/apps). Voeg het
   product **WhatsApp** toe.
2. **Voeg je 06-nummer toe** onder WhatsApp → API Setup → "Add phone number".
   Dat nummer mag op geen enkel toestel al als gewone WhatsApp actief staan
   (zie de waarschuwing hieronder) — Meta stuurt een SMS/belcode ter
   verificatie, dat moet je zelf ontvangen op dat nummer.
3. Genereer een **permanent System User access token** (Business Settings →
   System Users → nieuwe system user → toegang tot de WhatsApp-app →
   "Generate token" met de scopes `whatsapp_business_messaging` en
   `whatsapp_business_management`). Een tijdelijk token uit de Quickstart-tab
   verloopt na 24 uur en is dus niks voor productie.
4. Noteer het **phone_number_id** (WhatsApp → API Setup, staat naast je
   nummer) — dat is *niet* hetzelfde als het telefoonnummer zelf.
5. Verzin een **verify token** (een willekeurige string van jezelf, geen Meta-
   waarde) voor stap 8 hieronder.
6. Zet in de Vercel-environment-variables (gedeeld over alle tenants, net als
   `OPENROUTER_API_KEY` — dit is jouw Meta-app, geen klantgeheim):
   ```
   WHATSAPP_TOKEN=<het permanente System User-token>
   WHATSAPP_APP_SECRET=<App Dashboard → Settings → Basic → App Secret>
   WHATSAPP_VERIFY_TOKEN=<de string die je in stap 5 verzon>
   ```
   Redeploy zodat de nieuwe env meegaat.
7. Draai `node migrate.mjs` (voegt `whatsapp_phone_number_id`,
   `whatsapp_allowed_from` en de thread/dedupe-tabellen toe).
8. Koppel het nummer aan een tenant en zeg wie ermee mag praten:
   ```
   node scripts/add-whatsapp.mjs weareimpact <phone_number_id> 31612345678
   ```
   Het laatste argument is jouw eigen 06-nummer in E.164 zónder `+` (zoals
   Meta het aanlevert), kommagescheiden als je later iemand anders als
   manager toegang geeft — zíj krijgen de volle Iris (start_werk, agenda
   plannen, besluiten voorstellen). **Iedereen die niet op die lijst staat
   komt automatisch bij klant-Iris terecht**, niet bij stilte: een aparte,
   veel beperkte assistent die alleen uit de kennisbank van je projecten
   antwoordt (`sites.profile` moet gevuld zijn — een site zonder profiel
   levert geen klantkennis) en bij twijfel of bij iets met gevolgen
   (offerte, afspraak, klacht, persoonsgegevens) niets verzint maar een
   kaart voor je klaarzet op het Vandaag-scherm in Iris Remote, met een
   tekstveld om zelf te reageren — dat antwoord gaat direct de deur uit,
   geen wachten op een sync. Zie CLAUDE.md 14f voor het ontwerp.

   **Beveiligingsgrens (verifieerbaar):** klant-Iris mag *nooit* bij de
   manager-toolset komen. Dat rust niet alleen op "het model krijgt een
   kleinere toolset" — er zit defense-in-depth bovenop: elke tool-call die
   het model teruggeeft, wordt tégen een harde allowlist gecheckt vóór
   uitvoering. Een model dat per ongeluk `start_werk` of `stel_besluit_voor`
   teruggeeft, wordt geweigerd en resulteert in een escalatie naar Vincent —
   zo kan een verkeerd uitgelijnd model de review-gate nooit omzeilen. Dit is
   bewezen door `tests/whatsapp-security.test.js` (`node --test`).

   **Betrouwbaarheid (geen verdwenen berichten):** deduplicatie is een
   state-machine (`whatsapp_processed.status` = `received` → `replied`). Een
   bericht wordt pas als verwerkt gemarkeerd *nadat* het antwoord daadwerkelijk
   is verzonden. Een Vercel-timeout of crash laat het op `received` staan, dus
   Meta's retry krijgt een nieuwe kans in plaats van dat het bericht stil
   gedropt wordt. Alleen een retry op een al-verzonden (`replied`) bericht
   wordt vroeg gedropt.

   **Kostenbescherming:** per-afzender rate-limit (`whatsapp_throttle`):
   > 20 berichten/uur of > 6 berichten/60s ⇒ het bericht wordt vóór de LLM
   gedropt. Beschermt tegen spam-loops en LLM-kosten; legitieme
   klantengesprekken (een handvol berichten per dag) raken dit nooit.

   **Thread-continuïteit:** wanneer jij een escalatie beantwoordt vanuit Iris
   Remote (`whatsapp-reply`), wordt jouw antwoord teruggeschreven naar de
   klant-draad. Een vervolgvraag van de klant die naar jouw antwoord verwijst
   ("en wat kost dat dan?") heeft Iris dus in context — ze herhaalt of
   escaleert niet nutteloos.
9. Terug in het Meta App Dashboard: WhatsApp → Configuration → Webhook →
   *Edit* → callback-URL `https://<jouw-domein>/api/whatsapp`, verify token =
   de string uit stap 5. Klik *Verify and save* (dat is de GET-verificatie die
   `api/whatsapp.js` afhandelt), abonneer daarna op het veld **messages**.
10. Stuur een appje naar je eigen nieuwe nummer. Eerste antwoord kan een paar
    seconden duren (een LLM-call plus eventueel een paar tool-rondes).

⚠️ **Het nummer wordt daarna een puur zakelijk WhatsApp Business-nummer.**
Zodra het aan de Cloud API hangt, kun je er niet ook de gewone WhatsApp-app
mee gebruiken op je telefoon — dat is een harde regel van Meta, geen
instelling. Gebruik dus een vrij 06-nummer (een tweede simkaart of een
eSIM/VoIP-nummer volstaat, WhatsApp hoeft er nooit op te bellen), nooit het
nummer dat je privé al gebruikt.

**Meerdere klanten**: `whatsapp_phone_number_id` staat op de tenant-rij, dus
Nicole kan later haar eigen 06-nummer krijgen zonder een nieuwe deploy — wel
een tweede Meta-app-nummer (WHATSAPP_TOKEN blijft gedeeld: één Meta-app kan
meerdere nummers dragen) en een eigen `add-whatsapp.mjs`-koppeling.

## Live agenda + GSC-trend (zonder ImpactOS)

Agenda en GSC-cijfers hoeven niet te wachten op de eerstvolgende `bridge_sync`
of op een draaiende ImpactOS: ze gebruiken een Google **service-account**
(lang-levende sleutel, geen interactieve login) en kunnen daarom rechtstreeks
door Vercel worden opgehaald (`api/_google.js`). Mail blijft wél op de cache —
Outlook gebruikt MSAL-delegated-OAuth, geen sleutel die een los proces
herbruikt (zie CLAUDE.md 14d).

**Hoe de koppeling ontstaat — automatisch, geen los provisioneringsscript:**
elke `bridge_sync`-push stuurt (als agenda lokaal via `service_google.py` is
geconfigureerd) het service-account mee. Vercel versleutelt de sleutel bij
ontvangst (AES-256-GCM, `TENANT_SECRET_KEY`) en slaat 'm op in `tenants`.
Rotereert Vincent de sleutel lokaal, dan volgt Vercel binnen één synccyclus.

**Setup (eenmalig, ná `TENANT_SECRET_KEY` in de Vercel-env):**
1. `node migrate.mjs` — voegt de nieuwe `tenants`-kolommen toe (additief, veilig).
2. Lokale ImpactOS herstarten — de eerstvolgende `bridge_sync` stuurt de
   Google-config mee (alleen als `CALENDAR_CLIENT_EMAIL`/`CALENDAR_PRIVATE_KEY`
   inline zijn ingevuld — een los JSON-keyfile op de machine kan Vercel niet
   lezen). GSC-sites komen mee vanuit `sites.gsc_property` (lokale `sites`-tabel).
3. Controleren: tab *Systeem* → "Live agenda & GSC" toont wanneer de
   credential voor het laatst ontvangen is en hoeveel GSC-sites gekoppeld
   zijn. Rood + een foutmelding betekent: credential ontvangen, maar de live
   call zelf faalt (bv. het service-account heeft geen toegang meer tot de
   agenda) — precies zichtbaar in plaats van een stille terugval op cache.

**Multi-tenant, expliciet**: dit is per klant. Er is bewust géén globale
Vercel-env-var met één Google-credential — dat zou klant A's agenda aan
klant B tonen. Elke tenant-rij draagt zijn eigen (versleutelde) sleutel.

## Meerdere klanten (multi-tenant)

Eén Vercel-deploy + één Neon-database bedient meerdere klanten. Wat een
klant scheidt van de rest:

- **Eigen rij in `tenants`** (`slug`, weergavenaam, gehasht wachtwoord, gehasht
  `BRIDGE_TOKEN`) — aangemaakt via `node scripts/add-tenant.mjs <slug> "<naam>"`.
- **Eigen subdomein** (`nicole.domein.nl`) bepaalt voor de brówser welke tenant
  een verzoek hoort te zien (`_lib.js:tenantFromHost`). Zonder `BASE_DOMAIN`
  gezet in Vercel valt alles terug op `DEFAULT_TENANT` — handig zolang er nog
  geen eigen domein aan hangt, maar dan is er dus maar één klant bereikbaar.
- **Eigen `BRIDGE_TOKEN`** bepaalt voor de lokale ImpactOS-machine welke tenant
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
- `api/ui.js` — login/items/decide/briefing/notes/outbox/sessions/google-status (sessiecookie, voor jou)
- `api/_lib.js` — Neon-client, wachtwoord- en sessiebeheer, brute-force-rem
- `api/_google.js` — live agenda + GSC-trend rechtstreeks bij Google, zonder ImpactOS
- `api/_crypto.js` — AES-256-GCM voor de per-tenant Google-sleutel in Neon
- `index.html` + `app.js` + `style.css` — Iris Remote-frontend
- `tailwind.config.js` + `tailwind-src.css` + `build-fonts.mjs` — assets-build
- `schema.sql` — het schema; `node migrate.mjs` past het toe op `DATABASE_URL`
- `dev-server.mjs` — lokaal draaien: `npm run dev -- 8642`

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
   `IF NOT EXISTS`, dus herhalen is veilig — draai het opnieuw na een update om
   nieuwe tabellen zoals `sessions` en `login_attempts` erbij te krijgen.

### 2. GitHub + Vercel (hosting)
1. Zorg dat deze repo (of alleen de map `remote/`) op GitHub staat.
2. https://vercel.com → *Add New Project* → importeer de repo.
3. **Root Directory: `remote`** (belangrijk — anders probeert Vercel de
   Python-backend te bouwen). Framework preset: *Other*.
4. Environment variables:
   | Naam | Waarde |
   |---|---|
   | `DATABASE_URL` | de Neon-connection-string |
   | `BRIDGE_TOKEN` | lang random geheim, bv. `openssl rand -hex 32` |
   | `APP_PASSWORD` | het wachtwoord waarmee jij inlogt op je telefoon — **minimaal 16 tekens**, zie hieronder |
   | `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | voor push-meldingen: `npx web-push generate-vapid-keys` (optioneel — zonder keys geen meldingen, verder werkt alles) |
   | `VAPID_SUBJECT` | `mailto:v.munster@weareimpact.nl` |
   | **LLM voor cloud-Iris** | Kies één provider — cloud-Iris kiest OpenRouter als die key er staat, anders OpenModel. Zonder een van beide werkt alles behalve de Iris-chat. |
   | `OPENROUTER_API_KEY` | zelfde key als lokaal. `iris.js` praat dan in OpenAI-formaat met OpenRouter (Bearer-auth) |
   | `OPENROUTER_MODEL` | optioneel, default `anthropic/claude-sonnet-4-5` (of je lokale `CLAUDE_VIA_OPENROUTER`) |
   | `OPENMODEL_API_KEY` | alternatief voor OpenRouter: de OpenModel-gateway (Anthropic-formaat) |
   | `OPENMODEL_SMART_MODEL` | model bij OpenModel, bv. `claude-sonnet-4-6`. Zonder valt ze terug op het bulkmodel — dommere Iris |
5. Deploy → noteer de URL (bv. `https://iris-remote.vercel.app`).

### 3. Lokaal (AgentOS `.env`)
```
BRIDGE_REMOTE_URL=https://iris-remote.vercel.app
BRIDGE_TOKEN=<zelfde token als in Vercel>
BRIDGE_SYNC_MINUTES=3
```
Herstart AgentOS (`agentos_service.cmd`). De scheduler-job `bridge_sync` draait
dan elke 3 minuten; handmatig testen: `POST /api/bridge/sync-now`, status via
`GET /api/bridge/status`.

### 4. Telefoon
Open de Vercel-URL, log in, en kies "Zet op beginscherm" — dan gedraagt het
zich als app (donker Iris-thema, bottom-nav). Meldingen aanzetten: tab
*Systeem* → "Meldingen inschakelen". Op iPhone werkt web-push alléén vanuit de
op-het-beginscherm-gezette app (iOS 16.4+), niet vanuit Safari zelf.

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

- **`APP_PASSWORD` moet ≥ 16 tekens zijn.** Is hij korter, dan weigert de app te
  starten met een 503 in plaats van hem stilzwijgend te accepteren — een korte
  code is hier het zwakste punt van het hele systeem en geen enkele rem daarna
  maakt dat goed. Genereer er een met `openssl rand -base64 24`.
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
- Geen secrets in Neon; alleen werkdata (previews + besluiten). Opgeruimd na 14 dagen.
- Twee gescheiden sloten: bearer-token voor de bridge, sessiecookie voor de UI.
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

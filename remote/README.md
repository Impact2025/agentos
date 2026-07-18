# Iris Remote — cloud-companion voor Agent OS

Klein PWA-achtig dashboard (Vercel + Neon) waarmee Vincent onderweg — of met de
pc uit — de review-gates kan bedienen: Wachtrij-artikelen, helpdesk-mails,
outreach en agenda-voorstellen goedkeuren/afwijzen, de Iris-briefing lezen en
notities achterlaten die de vault in stromen.

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
2. Open de SQL-editor en draai de inhoud van `schema.sql`.
3. Kopieer de connection string (postgres://…) → dit wordt `DATABASE_URL`.

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
   | `APP_PASSWORD` | het wachtwoord waarmee jij inlogt op je telefoon |
   | `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | voor push-meldingen: `npx web-push generate-vapid-keys` (optioneel — zonder keys geen meldingen, verder werkt alles) |
   | `VAPID_SUBJECT` | `mailto:v.munster@weareimpact.nl` |
   | `OPENMODEL_API_KEY` | voor cloud-Iris-chat (zelfde key als lokaal; optioneel) |
   | `OPENMODEL_MODEL` | optioneel, default `deepseek-v4-flash` |
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

> Schema al eerder gedraaid? `schema.sql` is idempotent — draai hem gewoon
> opnieuw in de Neon SQL-editor om nieuwe tabellen (o.a. `push_subscriptions`)
> erbij te krijgen.

## Fase 2 — wat er verder in zit
- **Push-meldingen**: bij een écht nieuw besluit in de sync (geen herhaal-spam
  bij elke push) en wanneer een onderweg genomen besluit lokaal mislukt.
- **Cloud-Iris** (tab Briefings): chat met Iris over de laatst gesynchroniseerde
  snapshot (briefing, funnel, open besluiten) — werkt ook als je pc uitstaat.
  Ze kan niets uitvoeren; acties lopen altijd via de Actiecentrum-knoppen.

## Veiligheid
- Geen secrets in Neon; alleen werkdata (previews + besluiten). Opgeruimd na 14 dagen.
- Twee gescheiden sloten: bearer-token voor de bridge, wachtwoord+HMAC-cookie voor de UI.
- De cloud kan nooit iets publiceren of versturen: een besluit is niets anders
  dan dezelfde lokale knop, later ingedrukt — inclusief kwaliteitsgate,
  adres-validatie en conflict-checks.

## Bestanden
- `api/bridge.js` — push/decisions/ack/notes (bearer, alleen voor de lokale machine)
- `api/ui.js` — login/items/decide/briefing/notes/outbox (sessiecookie, voor jou)
- `api/_lib.js` — Neon-client + auth-helpers
- `index.html` + `app.js` + `style.css` — Iris Remote-frontend (Tailwind CDN, geen build)
- `schema.sql` — eenmalig in Neon draaien

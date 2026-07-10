# TeambuildingMetImpact — "Als een pro aan het werk" (Mission Radar + Doelen)

**Datum:** 2026-07-08 · **Door:** Hermes Agent · **Status:** UITGEVOERD ✅

## Wat was de kloof
Teambuildingmetimpact was de ENIGE brand zonder autonome engine: 21 artikelen +
frontend + SKILL.md waren er wél, maar Radar = 0 watch-items / 0 signalen / 0 doelen.
bewaardvoorjou (de "pro"-benchmark) had 25 watches, 89 signalen, 7 converted, goals.
Bewijs dat het kon: IctusGo was dezelfde dag cold van 0 → 23 watches + 20 signalen + 3 goals.

## Uitgevoerd (6-stappen-chain, gekopieerd van IctusGo-coldstart)
1. **Watchlist geseed (3 lagen)** via `scripts/seed_teambuildingmetimpact_watchlist.py`
   (idempotent). Resultaat: **25 items** — 8 concurrenten (teambuilding.nl, teamevents.nl,
   eventfully.nl, flitz-events.nl, citygame.nl, seriousplay.nl, strategicseriousplay.com,
   meevanderant.nl) + 14 gap-keywords (WKR/CSRD/ESG/regio/HR 2026) + 3 RSS
   (NU.nl/Werk, Frankwatching, MKB Servicedesk).
2. **Echte scan gedraaid** (Tavily + LLM-enrich, SSE + in-process fallback).
   Tavily-limit GETEST: geen cap melding → alle watches leverden signalen.
3. **Signalen + vault-notes**: na scan **118→138 signalen** (top_score 78.4).
   Memory-loop schreef **23 trend-notes** naar `10_Projects/_trends/`
   (lego-serious-play, impact-day-organiseren, bedrijfsvrijwilligerswerk, sroi-berekenen,
   vrijwilligers-werven, flitz-events, teambuilding-nl, ...).
4. **3 doelen aangemaakt** via `POST /api/goals/plan` (objective-veld, 240s timeout):
   - `goal-...-af426aa5062e` — Radar-warmup: signaalgeschiedenis opbouwen (draft)
   - `goal-...-bb9c633d930c` — AEO-contentmotor: signalen → gepubliceerde impact-content (draft)
   - `goal-...-a7110fd1427` — ESG/regio-dominatie: posities op money-keywords (draft)
   Stray "test"-goal verwijderd.
5. **Schedule = gratis**: `radar_sky_scan` (APScheduler, 4u) pikt teambuildingmetimpact
   automatisch op. Volgende run: 21:15 vandaag.
6. **Autonomie-gate**: auto-AEO startte al (1 converted signaal → concept-listicle tot
   aan Wachtrij). Publicatie blijft menselijk (CLAUDE.md regel 5).

## Belangrijke bijvinding: backend-crash tijdens lange SSE-scan
Een `curl -m 420 -N POST /api/radar/scan` liet de hele uvicorn-worker crashen toen de
SSE-stream werd afgebroken (backend viel weg, HTTP 000). Oorzaak: SSE-generator breekt
als de client disconnect. **Les voor volgende keer:** lange scans NIET via curl-SSE
draaien met korte timeout. Gebruik het in-process script
`scripts/scan_teambuildingmetimpact.py` (of `scan_teambuilding_continue.py`) in een
background-terminal met notify. De backend is opnieuw opgestart (pid 81144) en gezond.

## Verificatie-criteria (alle groen)
- `GET /api/radar/stats?project=teambuildingmetimpact` → total 138, top_score 78.4 ✅
- `GET /api/radar/sky?project=teambuildingmetimpact&min_score=50` → top signalen zichtbaar ✅
- `ls 10_Projects/_trends/` → 23 teambuilding trend-notes ✅
- `GET /api/goals?project=teambuildingmetimpact` → 3 doelen in draft ✅
- `GET /api/scheduler/status` → radar_sky_scan actief, next 21:15 ✅

## UPDATE (zelfde dag, "start als een pro")
Vincent gaf groen licht: de 3 doelen zijn **confirm + start** gezet via
`scripts/start_teambuilding_goals.py` (POST /api/goals/confirm + /start per goal).
Status van alle 3: **running**. De agent executeert nu zelfstandig:
- ESG/regio-dominatie: 44 tasks gegenereerd
- AEO-contentmotor: 12 tasks
- Radar-warmup: 2 tasks
(448 goal_tasks + 43 pipeline tasks totaal in DB.)

Daarmee is teambuildingmetimpact nu op hetzelfde autonomie-niveau als bewaardvoorjou:
Mission Radar (25 watches, 138 signalen, 23 vault-notes, 4u-auto-scan) + 3 lopende
doelen. Enige humane handeling die rest: publicatie (Wachtrij-gate) en review van
AEO-concepten via seed-blogs.js.

## Jouw volgende klikken (human-in-the-loop)
1. Review de **Radar-tab** → target beste signalen of laat auto-AEO lopen tot Wachtrij.
2. Voor publicatie: AEO-concept → `artikelen/blog/<slug>.md` (v2.0-regels!) →
   `node prisma/seed-blogs.js`. **Slug-guard**: lijst live slugs eerst (5 zijn ronde-1
   al overschreven).
3. Open actiepunten uit totaalplan-2026-07-08.md: hub-spoke interne link-graph,
   cases voorzien van echte namen, GSC-baseline vóór intensievere scans.

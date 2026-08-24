# Totaalplan: Teambuilding met Impact als een pro aan het werk zetten

**Datum:** 2026-07-08 · **Door:** Hermes Agent · **Status:** PLAN + UITVOERING

## 0. Diagnose — wat staat er (en wat ontbreekt)

| Laag | bewaardvoorjou (pro) | teambuildingmetimpact (nu) | IctusGo (referentie-coldstart) |
|------|----------------------|----------------------------|--------------------------------|
| Projectmap + SKILL.md | ✅ | ✅ | ✅ |
| Frontend geregistreerd | ✅ | ✅ (amber) | ✅ |
| Radar watchlist | **25 items** | **0 ❌** | 23 items |
| Radar signalen | **89** (7 converted) | **0 ❌** | 20 |
| Doelen (Goals) | 1+ (completed) | **0 ❌** | 3 |
| Content-pipeline | ✅ (content_jobs) | ⚠️ anders (seed-blogs.js) | – |
| GSC gekoppeld | ✅ | ✅ (sc-domain) | ✅ |
| Content live | 11+ artikelen | 21 artikelen | – |

**Conclusie:** teambuildingmetimpact mist precies de 3 dingen die bewaardvoorjou
"pro" maken: (1) Mission Radar-watchlist, (2) een lopende scan met signaalgeschiedenis,
(3) autonome doelen. De content-zijde is juist rijker dan bewaardvoorjou (21 vs 11
artikelen) — de blinde vlek zit in *signalering + autonomie*, niet in *productie*.

## 1. SEO-expert analyse (markt & content-gaps)

Web-search was op dit moment niet beschikbaar (Firecrawl billing), dus de *live*
concurrentie/keyword-verkenning gebeurt via de Radar zelf (Tavily + RSS) ná het seeden.
Op basis van het merk-DNA en de bestaande 21 artikelen zijn dit de strategische gaten:

**A. Concurrentielagen (wie zit op de money-keywords)**
- Generieke teambuilding/events: teambuilding.nl, teamevents.nl, eventfully.nl,
  flitz-events.nl, citygame.nl
- Maatschappelijke/social-impact hoek: meevanderant.nl, teamsforteams, hetgoededoel
- LEGO® Serious Play® hoek: seriousplay.nl, strategicseriousplay.com
- (De Radar toont na scan welke écht ranken op onze termen → prioritering)

**B. Content-gap keywords (head + long-tail, gemapped op bestaande artikelen)**
- `bedrijfsvrijwilligerswerk organiseren` (hub, slug botst al — zie actiepunt)
- `impact day organiseren` / `impact-day` (hub)
- `maatschappelijke teambuilding` / `bedrijfsuitje met impact`
- `mvo teambuilding esg` / `csrd teambuilding` (ESRS S1 — sterke 2026-hook)
- `wkr teambuilding 2026` (fiscale hoek, weinig concurrentie)
- `lego serious play teambuilding` (onderscheidend — we zijn de enige NL-aanbieder
  mét geautomatiseerd impactrapport)
- `teambuilding haarlemmermeer` / `teambuilding hoofddorp` (lokale dominatie)
- `vrijwilligersdag teambuilding` / `social return teamdag` / `impact meten teamdag`

**C. E-E-A-T voorsprong (gebruiken in AEO)**
- Enige NL-aanbieder met geautomatiseerd impactrapport ná de dag (IctusGo.nl)
- Lokale Haarlemmermeer-cases: Voedselbank, MeerWaarde, Ons Tweede Thuis, Rabobank
- Harde cijfers uit de PDF's: €34,79/vrijwilligersuur; 57% minder verloop;
  94% voelt zich positiever; 91% van millennials wil purpose-werkgever

## 2. Het "als een pro" plan — 6 stappen (gekopieerd van IctusGo-coldstart)

1. **Radar watchlist seeden (3 lagen)** — concurrenten (site:-monitoring) +
   gap-keywords + RSS. Idempotent seeder `seed_teambuildingmetimpact_watchlist.py`.
2. **Bewijs met echte scan** — `POST /api/radar/scan {enrich:true}`; verifieer
   `stats.total > 0` en `watch_count` == aantal. Check Tavily-limit-log.
3. **Top-signalen targeten** — signals ≥70 schrijven automatisch de vault in
   (`10_Projects/_trends/`); hoogste scores `PATCH status=targeted`.
4. **3 doelen aanmaken** via `POST /api/goals/plan` (objective-veld! 240s timeout):
   - G1 Radar-warmup → 50+ signalen, wekelijks scanrapport
   - G2 AEO-contentmotor → goedgekeurde signalen → gepubliceerde blogs/landingspagina's
   - G3 ESG/regio-dominatie → posities op money-keywords + WKR/CSRD-hoek
5. **Schedule = gratis** — `scan_the_skies()` (APScheduler 4u) pikt teambuildingmetimpact
   automatisch op. Geen Hermes-cron nodig (Windows-path-bug).
6. **Autonomie-gate** — AEO-auto-attack zet signalen om in concept-listicles tot aan
   de Wachtrij. Publicatie blijft menselijk (CLAUDE.md regel 5).

## 3. Kritische verschil vs bewaardvoorjou: de publicatie-pijplijn

bewaardvoorjou publiceert via de ImpactOS `content_pipeline` (content_jobs → site).
teambuildingmetimpact publiceert via een **eigen Next.js/Prisma-stack**:
`artikelen/blog/<slug>.md` → `node prisma/seed-blogs.js` → Neon DB → live.

Gevolg voor de AEO-motor:
- De auto-aeo levert een concept-listicle op. Voor teambuilding moet dat concept
  daarna handmatig (of via `scripts/convert_impactdays.py`) naar het site-formaat:
  - v2.0-regels: 0× "u"/"uw", geen AI-woorden, eerste persoon Vincent
  - Parser-gotchas: GEEN JSON-LD/HTML-comments/tabellen/CTA-tokens in de body
  - Frontmatter: title≤60, meta_description≤155, UNIEKE slug (slug-botsing = silent overwrite!)
- **Slug-collisie is hier levensgevaarlijk** (5 slugs zijn ronde-1 al overschreven).
  Guard: lijst live slugs vóór elke seed, diff, hernoem bij conflict.

## 4. Open actiepunten (overgenomen uit onboarding-log)
- [ ] 5 overschreven slugs herstellen (bedrijfsvrijwilligerswerk, corporate-volunteering-nederland,
  impact-day-organiseren, mvo-teambuilding, social-return-teamdag) of bewust laten.
- [ ] Hub-spoke interne link-graph (technische SEO-slag, verhoogt autoriteit van alle 21 posts).
- [ ] Cases voorzien van echte klantnamen (nu anoniem).
- [ ] GSC-positionele baseline vastleggen vóór de scans intensiveren.

## 5. Verificatie-criteria (succes = "pro")
- `GET /api/radar/stats?project=teambuildingmetimpact` → total > 0, top_score > 0
- `GET /api/radar/sky?project=teambuildingmetimpact&min_score=50` → top signalen zichtbaar
- `ls "10_Projects/_trends/"` → nieuwe trend-notes voor teambuilding
- `GET /api/goals` → 3 doelen met project=teambuildingmetimpact in `draft`
- APScheduler `radar_sky_scan` draait elke 4u en includeert teambuildingmetimpact

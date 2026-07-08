---
name: ictusgo
description: "IctusGo.nl — GPS Teambuilding met Sociale Impact. Next.js 15, Drizzle ORM, Neon DB. Mission Radar agent volledig aangesloten op Agent OS sinds 8 juli 2026."
version: 1.0.0
tags: [gps, teambuilding, sociale-impact, welzijn, hr, wkr, csrd, hoofddorp, schiphol, ictusgo, seo, aeo]
---

# IctusGo

## Status (8 juli 2026)
- **Website:** https://ictusgo.nl — let op: `ictusgo.app` bestaat niet (DNS faalt). Nergens meer naar linken.
- **Stack:** Next.js 15, TypeScript, Drizzle ORM, Neon (Postgres), NextAuth v5, Stripe, OpenRouter AI (Claude), GA4
- **Code:** `D:\apps\ictusgo\`
- **5 varianten:** WijkTocht · ImpactSprint · FamilieTocht · JeugdTocht · VoetbalMissie
- **USP:** Geluksmomenten Score (GMS) — verbinding, betekenis, plezier, groei
- **Agent OS aansluiting:** project-folder aangemaakt, frontend geregistreerd, Mission Radar watchlist geseed, goals aangemaakt. Sky Scanner pikt IctusGo elke 4u automatisch mee.

## Merkidentiteit
- **Toon:** Deskundig, warm, nuchter, B1-niveau. Geen zweverige AI-hype.
- **Doelgroep:** HR-/MT-beslissers bij bedrijven rond Hoofddorp/Schiphol (en NL-breed); teams die maatschappelijke impact willen koppelen aan teambuilding.
- **Kernboodschap:** "Teamgeluk dat telt — GPS-tochten waarbij je team groeit én de buurt versterkt."
- **Auteurspersona:** Vincent van Munster (eerste persoon), ervarings-carrousel: LSP-facilitator / GPS-platform-innovator / oud-bestuurder Stichting de Baan / social enterprise ondernemer.
- **Kleuren:** navy #0F172A + groen #00E676 accent.

## Product — de 5 varianten
1. **WijkTocht** — GPS-route door de eigen wijk, opdrachten rond lokale initiatieven
2. **ImpactSprint** — korte, energieke variant gericht op maatschappelijke opgaven
3. **FamilieTocht** — familiesamenhorigheid, geen bedrijfscontext
4. **JeugdTocht** — scholen/jeugdgroepen, AVG-proof
5. **VoetbalMissie** — sportclub/teamcontext

## SEO-fundament (Sprint 1–3, live sinds 3 juli 2026)
- 5 landingspagina's: `/teambuilding-hoofddorp`, `/teambuilding-haarlemmermeer`, `/gps-teamuitje`, `/maatschappelijk-teamuitje`, `/teambuilding-zonder-wkr`
- 14 artikelen (7 blog + 7 kennisbank), elk met eigen OG-image
- Technisch: sitemap, robots, Organization/WebSite/FAQPage/Service/BlogPosting schema, canonical, IndexNow (Bing/Yandex), GA4 conversie-events
- **Openstaand (menselijk, niet door agent):** GSC-property + `GOOGLE_SITE_VERIFICATION`, GA4 conversiedoelen UI, zoekvolumes valideren in Keyword Planner, Lighthouse na deploy

## Mission Radar — watchlist (de Sky Scanner-voeding)
Geseed 8 juli 2026. Drie lagen, exact zoals BVJ:
- **Concurrenten** (`site:`-monitoring): teamevents.nl, eventfully.nl, flitz-events.nl, teambuilding.nl, citygame.nl, scavenger.nl, spelevent.nl, meetinn.nl
- **Keywords** (uit sprint-onderzoek: WKR, CSRD/ESRS S1, regio Hoofddorp/Schiphol, HR-trends 2026): gps teambuilding, teambuilding hoofddorp, teambuilding haarlemmermeer, maatschappelijk teamuitje, teamuitje sociale impact, wkr teambuilding 2026, csrd teambuilding, bedrijfsuitje hoofddorp schiphol, gps teamuitje bedrijf, vrijwilligers teambuilding, teambuilding zonder wkr, geluksmomenten team
- **RSS** (HR/werk/nieuws rond arbeidsmarkt & MKB): NU.nl Werk, Frankwatching, MKB Servicedesk

## Content- & AEO-workflow (de agent-pijplijn)
1. Sky Scanner (elke 4u) → signaal-extractie uit concurrenten/keywords/RSS
2. Heuristische Signal Score (versheid + bron-autoriteit + keyword-match + Tavily) + AI-angle (Radar Trend-Analist: hook + unieke invalshoek + 3 titels, géén kopie)
3. Top-signalen (score ≥ 70) → automatisch markdown in Obsidian-vault `10_Projects/_trends/` (geheugen-loop)
4. Auto-AEO (score ≥ 75, max 3/scan) → conveyor-taken: listicle → videoscript → Reddit-concept (mens klikt "publiceer")
5. Trend-sync → Demand Engine zet top-kansen klaar voor de contentpijplijn
6. NotebookLM-pakket per signaal (bron · podcast · infographic · shorts)

## E-E-A-T-invalshoeken (per artikel één kiezen)
- **A — Fiscaal/HR:** open met "Als innovatiemanager bouw ik AI-oplossingen…" bij WKR/CSRD/bedrijfsuitje
- **B — Impact & betekenis:** open met "Toen ik als bestuurder van Stichting de Baan…" bij sociale impact/vrijwilligers
- **C — Beleving & teamgeluk:** open met "Ik heb honderden teams zien vastlopen op…" bij GPS/beleving

## Schrijfregels
- Sentence-case tussenkoppen, korte alinea's (≤3-4 zinnen), bullets
- Min. 2 focuszoektermen in intro/body/conclusie
- Verplichte secties: E-E-A-T-intro · kern · GMS/pijlers waar relevant · "Wat neem je mee" (3 inzichten) · CTA naar ictusgo.nl · 2-3 interne links
- Nooit als externe tekstschrijver — altijd Vincent van Munster

## Goals (Mission Radar doelen-pijplijn)
- **G1 — Radar-warmup:** 30 dagen watchlist bouwen tot 50+ signalen, wekelijkse scan-rapportage
- **G2 — AEO-contentmotor:** per maand 4 goedgekeurde signalen → gepubliceerde listicles op ictusgo.nl
- **G3 — Regionale dominatie:** Hoofddorp/Schiphol op positie 1-3 voor "teambuilding hoofddorp" e.o.

## Kwaliteitsparameters
- Max 3 auto-AEO-aanvallen per scan (menselijke review-gate op publicatie)
- Human-in-the-loop: agent publiceert NOOIT automatisch
- Wekelijkse review in Agent OS dashboard (Radar-tab + Demand Engine)

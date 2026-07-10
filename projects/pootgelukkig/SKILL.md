---
name: Pootgelukkig
description: "Pootgelukkig — AI-gestuurd adoptieplatform voor asieldieren (koppelt asielen aan adoptanten)"
version: 2.0.0
tags: [adoptie, asiel, dieren, vrijwilligers, herplaatsing, hond, kat, konijn, dierenwelzijn]
---

# Pootgelukkig

## Merkidentiteit
- **Website:** https://pootgelukkig.nl  (IndexNow key: d3b5c5b8a7e94f2e9c1a6f3d8b2e4c7a)
- **Toon:** Warm, hoopvol, toegankelijk — overal waar adoptie centraal staat. B1-niveau, geen AI-hypes of kille tech-taal.
- **Doelgroep:**
  - Consument: mensen die een dier willen adopteren (hond, kat, konijn, cavia)
  - B2B: asielmedewerkers, dierenasielen, vrijwilligerscoördinatoren
- **Kernboodschap:** "Elk dier verdient een gelukkig thuis."
- **Oprichter / initiatief:** Maya van Munster (13 jr, idee) — uitvoering WeAreImpact BV / Vincent van Munster.
- **Tech stack:** Next.js 15, React 19, Neon PostgreSQL, Drizzle ORM, OpenRouter (claude-sonnet-4-5), Auth.js v5, Resend, Vercel.

## De 7 tools van Pootgelukkig (AI-gestuurd platform)
1. **Asiel Copilot** (`/api/admin/copilot`) — proactieve AI-werkmaat voor asielmedewerkers: dagelijkse briefing, geprioriteerde taken, inzichten, teksten. Leest live DB-context (dieren, adopties, afspraken, medisch, berichten).
2. **Copilot Briefing** (`/api/admin/copilot/briefing`) — genereert de ochtend-briefing + taaklijst + stats uit actuele asieldata.
3. **Adoptant Assistent** (`/api/assistent`) — chatwidget voor bezoekers; beantwoordt vragen over asiel, openingsuren, procedure, en het specifieke dier waar ze in geïnteresseerd zijn.
4. **Verhaal-generator** (`/api/ai/verhaal`) — schrijft een warm adoptieverhaal (3 zinnen) vanuit dier-kenmerken (ras, energie, tags).
5. **Dier-scan** (`/api/ai/dier-scan`) — vision: analyseert een dierenfoto → soort/ras/leeftijd/geslacht/energie/tags + verhaal (claude-sonnet-4-5, image_url).
6. **Nazorg-plan** (`/api/nazorg/generate`) — genereert gepersonaliseerd 14-dagen nazorgplan per adoptie (gedragstips + checklist).
7. **Blog-generator** (`/api/admin/beheer/blog/genereer`) — wereldklasse SEO-artikel (700+ woorden, Markdown, focuskeyword, interne/externe links, SEO-score) → concept in DB.

## Content-fundament (SEO)
- **Blog:** 28 artikelen (15 adoptant, 10 asiel, 3 Pootgelukkig) — compleet met Article+BreadcrumbList+FAQPage/HowTo-schema.
- **Kennisbank:** 25 artikelen over 8 categorieën (Voorbereiding, Intake, Thuiskomst, Nazorg, Dashboard, Matching, Privacy/AVG, Hoe het werkt).
- **Marketing:** 10 pagina's (home, blog, kennisbank, voor-asielen, werkwijze, prijzen, over-ons, contact, faq, intake) — WebSite/Product/ContactPoint-schema.
- **Nog te doen (uit README):** blog → 40+, 23 KB cover images (FAL pending), interne-linkmatrix, GSC-koppeling.

## E-E-A-T-invalshoeken (gebruik voor content)
- **A — Dierenwelzijn & expertise:** open met kennis van asielwerk / diergedrag.
- **B — Toegankelijkheid & angst voor technologie:** adoptie is emotioneel; benader rustig, praktisch.
- **C — Impact & gemeenschap:** elk dier een thuis = maatschappelijke impact; rol van vrijwilligers.

## Schrijfstijl & SEO-Regels
- Focuskeyword in titel + eerste alinea + minstens 1 H2/H3 (dichtheid 0.5–2.5%).
- Korte alinea's (max 3-4 zinnen), bullet points, warme actieve taal.
- Verplichte secties: intro, kern (voordeel→praktijk), 2-4 interne links (/blog, /kennisbank), 2-3 externe links (dierenbescherming.nl, licg.nl, rijksoverheid.nl), CTA naar /intake of /zoeken.

## Mission Radar — watchlist & doelen
- Radar-watchlist: 8 concurrenten (dierenbescherming/ikzoekbaas/verhuisdieren/...) + 16 adoptie-keywords + 4 RSS (Dierenbescherming/LICG/Dierennoodhulp/Zooplaats).
- Scorer-boost: `_HIGH_VALUE_TOKENS["pootgelukkig"]` in backend/domains/radar/scorer.py (adoptie/hond adopteren/kat adopteren/...).
- Doelen: (1) Radar-warmup 50+ signalen, (2) AEO-contentmotor 4 artikelen/maand, (3) Adoptie-autoriteit op money-keywords.
- AEO staat aan (AEO_AUTO_ATTACK=1, MIN_SCORE=66, MAX=4) → na een scan met boost vuurt de auto-AEO op beste signalen.

## Workflow
- CRM pipeline: Excel → genereer_ts.py → nl-asielen.ts → db:import-asielen → crm:asielen
- Telegram bot: @pootgelukkig_bot
- Content: adoptieverhalen, asielinformatie, dierverzorgingstips

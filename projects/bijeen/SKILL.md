---
name: Bijeen
description: "Bijeen — event management SaaS platform voor de Nederlandse welzijnssector"
tags: [welzijn, evenementen, SaaS, WMO, AVG, sociaal-domein]
---

# Bijeen

## Status
- **Website:** https://bijeen.app (live, Vercel, Next.js 14 App Router + Drizzle/Neon Postgres)
- **Codebase:** `D:\apps\bijeen\welzijnsevent-starter\welzijnsevent\`
- **GSC:** Geconfigureerd — `sc-domain:bijeen.app`, siteFullUser via het gedeelde service account
- **Git:** GitHub `Impact2025/welzijnsevent`, branch `main` → Vercel auto-deploy
- **Content:** 13 blogposts (na cannibalization-merge juli 2026, was 17), 24 kennisbankartikelen, allemaal gepubliceerd
- **IndexNow:** actief (key in `public/{key}.txt` + Vercel env `INDEXNOW_KEY`)

## Merk & doelgroep
Bijeen is een sectorspecifiek eventplatform voor Nederlandse welzijnsorganisaties (WMO-gefinancierd,
ANBI-stichtingen). Kernonderscheid t.o.v. generieke tools (Eventbrite e.d.): WMO-rapportage,
vrijwilligersbeheer, AVG/dataopslag binnen de EU, sectorspecifieke functionaliteit.
Oprichter/auteur: Vincent van Munster (ook oprichter van WeAreImpact, strategisch adviesbureau
sociaal domein — https://weareimpact.nl). Content linkt bewust en organisch naar WeAreImpact
(auteursbio + contextuele kennisbank-verwijzingen), nooit naar de kale homepage als er een
specifiekere WeAreImpact-pagina bestaat.

**Tone of voice:** direct, ervaringsgedreven (Vincent's eigen praktijk bij Stichting de Baan,
Philia, DatingAssistent), geen platformbashing, concrete cijfers (4,2u administratie/evenement,
38% afhaakgrens check-in, €1,50-6 SROI per euu, 15% Sociaal Tarief korting ANBI).

## Bekende structurele risico's (juli 2026 opgelost, blijf alert)
- **Keyword cannibalization:** eerdere contentbatches leverden meerdere bijna-identieke artikelen
  op hetzelfde keyword (Eventbrite-alternatief, SROI, AI-in-sociaal-domein, eventsoftware-faalt).
  4 paren zijn samengevoegd + 301-redirect. **Check bij nieuwe content altijd eerst of het
  onderwerp al bestaat** (zie kennisbank/blog-inventaris) voordat je een nieuw artikel plant.
- **Meta title/description:** moet passen binnen budget aan de bron (title ≤51 tekens excl.
  " — Bijeen"-suffix, description ≤155 tekens) — runtime-truncatie in `src/lib/seo.ts` is een
  vangnet, geen vervanging voor goed geschreven meta's.
- **FAQPage schema:** wordt automatisch gegenereerd uit een `<h2>Veelgestelde vragen</h2>`-sectie
  in de content (zowel blog als kennisbank) — geen aparte schema-invoer nodig, gewoon FAQ-secties
  in de juiste HTML-structuur schrijven.

## GSC-realiteit (2 juli 2026 meting)
Zeer jonge site: 408 impressies / 12 kliks over 3 maanden. Bijna uitsluitend merkzoekopdrachten
("bijeen"). Content (blog/KB) heeft nog nauwelijks organische zichtbaarheid — knelpunt is
domeinautoriteit/backlinks + crawltijd, niet meer on-page optimalisatie. Zie
`BIJEEN_SEO_PLAN.md` hoofdstuk 8 (Backlink & Outreach) voor de strategie.

## Sleutelbestanden
- SEO-strategie: `D:\apps\bijeen\BIJEEN_SEO_PLAN.md`
- Keyword-onderzoek + content-gaps: `D:\apps\bijeen\BIJEEN_KEYWORD_RESEARCH.md`
- Social media kalender: `D:\apps\bijeen\BIJEEN_CONTENT_CALENDAR.md`
- GSC-scripts (werkend, service account): `D:\apps\bijeen\gsc_analyse.py`, `gsc_diepe_analyse.py`
- Interne link-updates: `D:\apps\bijeen\update_interne_links.py` (schrijft direct naar productie-DB)

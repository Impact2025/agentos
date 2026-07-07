---
name: Daarwebsite
description: "Daar — vrijwilligersplatform voor grip en geluk; blog en kennisbank op daar.nl"
version: 1.0.0
tags: [vrijwilligers, vrijwilligersmanagement, saas, kennisbank, blog, seo]
---

# Daarwebsite (daar.nl)

## Merkidentiteit
- **Website:** https://daar.nl
- **Toon:** Nuchter, praktisch, warm-professioneel. Onderbouwd met (DAAR-)onderzoek, nooit hype. "Wij"-vorm namens Team DAAR (Vincent, Saviem, Thijs).
- **Doelgroep:** Vrijwilligerscoördinatoren, bestuurders van vrijwilligersorganisaties, gemeenten en fondsen in Nederland.
- **Kernboodschap:** Het complete platform voor vrijwilligersmanagement — van werving tot impactmeting, met de unieke Geluksformule.

## Content-richtlijnen
- Schrijf in het Nederlands, B1-toegankelijk maar inhoudelijk stevig.
- Elk artikel opent met "Wat je leert in dit artikel" (bulletlijst) en sluit af met een conclusie.
- Onderbouw claims met DAAR-onderzoeksrapporten; blogposts krijgen een sectie "Geraadpleegde DAAR-onderzoeksrapporten".
- Lengte: streef naar 1.500+ woorden voor kennisbank-gidsen (pillar), 800–1.200 voor blogposts. Huidige artikelen zitten daar deels onder — verdieping is de openstaande contentopdracht.
- CTA's: VrijwilligersCheck (`/vrijwilligerscheck`) primair, contact/afspraak secundair. De artikel-template rendert al een vaste CTA-sectie — geen extra harde CTA's in de body.

## SEO-invalshoek
- Hoofdzoekwoorden: vrijwilligersmanagement, vrijwilligers werven, vrijwilligersretentie, vrijwilligers behouden, impact meten vrijwilligerswerk, vrijwilligersbeheer software.
- Subthema's (= categorieën): vrijwilligersretentie, werving-onboarding, impact-meten, technologie-ai, organisatie-management, welzijn-waardering.
- Structuur: kennisbank = pillar-gidsen (`/kennisbank/<slug>`), blog = ondersteunende cluster-posts (`/blog/<slug>`). Categoriepagina's op `/kennisbank/categorie/<slug>` en `/blog/categorie/<slug>`.
- Interne links: elk nieuw artikel krijgt 3–5 contextuele interne links + een "Verder lezen"-sectie (h2 + ul), en minstens één bestaand artikel linkt terug. Linkstrategie: zie `SEO/interne-linkstructuur.md`.
- Meta: metaTitle ≤ 60 tekens (merk wordt via template "| Daar" toegevoegd — niet zelf toevoegen), metaDescription 120–160 tekens, featuredImageAlt altijd invullen.

## Techniek / publiceren
- Repo: `D:\daarwebsite\daar-nextjs` (Next.js 15 App Router, deploy via Vercel).
- Content staat NIET in de repo maar in een Neon Postgres-database, Prisma-model `Article` (`type` = `KENNISBANK` | `BLOG`, `status` = `PUBLISHED`). Connectiestring: `DATABASE_URL` in `D:\daarwebsite\daar-nextjs\.env`.
- De AgentOS-dedup via `external_db_url` werkt hier niet (verwacht tabel `blog_posts`); de live-sitemap-fallback (`https://daar.nl/sitemap.xml`) dekt dit af — de sitemap bevat sinds 2026-07-07 alle blog- én kennisbank-URL's.
- Publiceren = artikel in de database zetten (status `PUBLISHED`) — dit gaat direct live. Dus: concepten altijd eerst als `DRAFT` en via de Wachtrij-gate laten reviewen.

## Eerder werk
- `SEO/seo-audit-2026-07-07.md` — volledige SEO-audit + alle doorgevoerde fixes (code + database).
- `SEO/interne-linkstructuur.md` — de pillar/cluster-linkmap tussen alle 14 artikelen.
- `SEO/content-inventory.md` — inventaris van alle gepubliceerde artikelen met linkstatistieken.

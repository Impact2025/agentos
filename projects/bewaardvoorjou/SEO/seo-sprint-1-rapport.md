---
title: "SEO Sprint 1 — JSON-LD, IndexNow, meta descriptions, interne links"
slug: seo-sprint-1
created_at: 2026-06-27
author: Hermes Agent
tags: [seo, sprint-1, gestructureerde-data, indexnow, interne-links, meta-optimalisatie]
---

## Sprint 1 resultaten (27 juni 2026)

### JSON-LD structured data
- **12/12 landingspagina's** hebben nu schema.org markup
- Homepage: Organization + WebSite + SearchAction schema
- /voor-baby: Product schema
- /voor-baby/hoe-het-werkt: HowTo + FAQ schema
- /autobiografie-hulp: WebPage + BreadcrumbList schema
- /veilig-digitaal-familiearchief: WebPage + BreadcrumbList schema
- /faq: FAQPage schema
- /levensverhaal-vastleggen: WebPage + Product schema
- /levensverhaal-opschrijven: WebPage + HowTo schema
- /levensverhaal-bewaren-usb: WebPage + Product schema
- /vaderdag: WebPage schema
- /cadeau-opa-80-jaar: WebPage + BreadcrumbList schema
- /pricing: Product schema
- KB detail template: Article + BreadcrumbList schema
- Blog detail template: BlogPosting + BreadcrumbList schema

### IndexNow
- Key: `5ea345ef169f44a79679b5df61c1ea6b`
- Key file: `public/5ea345ef169f44a79679b5df61c1ea6b.txt` (HTTP 200 ✅)
- Frontend implementatie: `src/lib/indexing.ts` — pingIndexNow()
- Backend implementatie: `app/services/indexing.py` — async ping bij publicatie
- Railway env: INDEXNOW_KEY + SITE_URL gezet
- Bulk ping verstuurd: 12 core URLs

### Meta descriptions
- **11/11 pagina's** geoptimaliseerd: doelwoord vooraan, CTA aan het eind, 120-155 chars
- Verouderde Vaderdag-datum verwijderd ("Bestel vóór 17 juni" → "Maak van Vaderdag een blijvend cadeau")
- Homepage kreeg title + description (miste die volledig)

### Interne links
- `/levensverhaal-opschrijven` → 3 KB-artikelen (complete gids, memoires, kosten)
- `/levensverhaal-bewaren-usb` → 3 KB-artikelen (opslag, tijdsvrijgave, export)
- `/veilig-digitaal-familiearchief` → 3 KB-artikelen (opslag, toegang, export)
- `/voor-baby` → 3 KB-artikelen (mijlpalen, digitaal vs papier, kraamcadeau)

### Backlinks
- **Backlink #1**: WeAreImpact footer → bewaardvoorjou.nl

---
name: teambuildingmetimpact
description: "Teambuilding met Impact — bedrijfsvrijwilligerswerk, impact days & LEGO Serious Play"
version: 1.0.0
tags: [teambuilding, bedrijfsvrijwilligerswerk, impact-day, lego-serious-play, mvo, esg, seo, ictusgo, haarlemmermeer]
---

# Teambuilding met Impact

## Merkidentiteit
- **Website:** https://www.teambuildingmetimpact.nl
- **Merkeigenaar:** Vincent van Munster, oprichter & gecertificeerd LEGO® Serious Play®-facilitator
- **Toon:** Nuchter, vakbekwaam, concreet. Eerste persoon (ik/mijn/we). Geen jargon, geen hype.
- **Doelgroep:** HR-managers, MVO-coördinatoren, directeuren, inkoop/HR bij bedrijven die maatschappelijke impact willen koppelen aan teamontwikkeling.
- **Kernboodschap:** Bedrijfsvrijwilligerswerk en impact days die écht meetbaar zijn — met automatisch impactrapport via IctusGo.nl.
- **Onderscheid:** Enige aanbieder in NL met geautomatiseerd impactrapport ná de dag; lokale verankering in Haarlemmermeer (Voedselbank, MeerWaarde, Ons Tweede Thuis, Gemeente, Rabobank); LEGO® Serious Play® als verdiepingslaag.

## Merknamen (correct schrijven)
- LEGO® Serious Play® (® op LEGO én Serious Play)
- IctusGo.nl (partner / impactrapport-tool)
- Vincent van Munster (oprichter)
- Teambuilding met Impact (bedrijfsnaam)

## Verboden in content (v2.0-gids 2026)
- Geen "u" / "uw" — schrijf in "jij"-vorm.
- Geen AI-woorden: bovendien, daarnaast, wellicht, ten slotte, kortom, het is belangrijk, ongeacht, desalniettemin, concluderend, samenvattend, kort gezegd.
- Geen verboden Yoast/AI-zinnen.

## Projectstructuur
```
projects/teambuildingmetimpact/
├── SKILL.md                 ← Dit bestand
├── content/                ← Gepubliceerde artikelen (bron: D:/APPS/Teambuilding/artikelen/blog/*.md)
├── log/                    ← Uitgevoerde SEO-taken, analyses, pushes
│   └── [datum].md
└── SEO/                    ← SEO-analyses, concurrentie, contentplannen
```

## Publicatie-pijplijn (belangrijk!)
De site is GEEN WordPress maar een Next.js-app (`D:/APPS/Teambuilding/lsp-workshop-app`, repo `Impact2025/teambuildingmetimpact` op Vercel).
Blogposts worden gevuld via `prisma/seed-blogs.js` uit Markdown in `D:/APPS/Teambuilding/artikelen/blog/*.md` → upsert naar Neon Postgres (DATABASE_URL in `.env`). De site laadt posts direct uit de DB (geen git/markdown-build voor content).
- **Content pushen:** converteer artikel → `artikelen/blog/<slug>.md` → `node prisma/seed-blogs.js` → live in DB.
- **Code pushen (parser/UI):** `git commit` + `git push` naar `master` → Vercel rebuild.
- De site-renderer (`src/app/blog/[slug]/page.tsx`) kent ALLEEN `#/##/###`, `**bold**`, `[link](url)`, `- ` bullets, genummerde `1. ` lijsten. Geen JSON-LD/HTML-comments/tabellen/CTA-syntax — die moeten bij conversie weg.

## Dashboard data (via AgentOS frontend)
- **GSC:** sc-domain:teambuildingmetimpact.nl
- **Site:** https://www.teambuildingmetimpact.nl
- **Backend stack:** Next.js 16 + React 19 + Prisma 7 + Neon Postgres, Vercel-deploy.

## Huidige status (8 jul 2026)
- 11 ImpactDays-artikelen live (3 landingspagina's + 8 blogs), v2.0-compliant, bug-vrij.
- 10 bestaande posts (pre-ImpactDays) nog live.
- Parser-upgrade (ol/ul-ondersteuning) gecommit+pushed → verbetert alle posts.
- Let op: 5 slugs (bedrijfsvrijwilligerswerk, corporate-volunteering-nederland, impact-day-organiseren, mvo-teambuilding, social-return-teamdag) zijn in ronde 1 OVERSCHREVEN door ImpactDays-content (slug-conflict). Zie log/2026-07-08.

## Interne links / autoriteit
- Hub: /blog/impact-day-organiseren, /blog/bedrijfsvrijwilligerswerk, /blog/mvo-teambuilding
- Cases: /blog/case-impact-day-voedselbank-haarlemmermeer, /blog/case-impact-day-ons-tweede-thuis (anoniem)
- Volledige hub-spoke link-graph is een open technische-SEO-slag.

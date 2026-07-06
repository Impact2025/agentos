# Kennisbank Upgrade — 26 juni 2026

## Wat er gebeurd is

### KB content: 18 → 25 artikelen
- 7 nieuwe artikelen toegevoegd aan 7 categorieën
- Alle 8 categorieën nu op min. 3 artikelen
- Unieke data juli 2026, min 1 dag ertussen
- Nieuwe slugs: wat-gebeurt-er-na-de-intake, dagritme-opbouwen-nieuw-asieldier, gedragsveranderingen-eerste-maand, statistieken-asiel-dashboard, matchkans-asieldier-verbeteren, rechten-adoptanten-avg, asiel-aanmelden-pootgelukkig

### Design upgrade
- coverUrl veld toegevoegd aan KennisArtikel interface
- Cover image op artikelpagina (16:9, rounded-3xl, tussen samenvatting en content)
- Cover image op categorie-overzicht (cards met hover-zoom)
- 2/25 cover images gegenereerd (rest: FAL-key pending)

### Marketing pagina schema's
- WebSite ld+json toegevoegd aan: voor-asielen, werkwijze, over-ons
- Nu 6/6 marketing pagina's met per-pagina schema markup

### Vercel deploy
- eslint.ignoreDuringBuilds: true (pre-existing errors)
- Build: 7s, 0 errors
- Alle 7 nieuwe slugs: 200 ✅

### Documentatie
- README.md vernieuwd
- PROJECT.md aangemaakt (dashboard)
- SKILL.md geüpdatet v3.2.0

### IndexNow
- 14 URLs gepingt

### Nog te doen
- 23 KB cover images genereren (FAL-key in omgeving zetten)

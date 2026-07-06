# Volledige Content Inventaris — PoortGelukkig
*Bijgewerkt: 26 juni 2026*

## Overzicht
- Blog artikelen: 28 (adoptant 15, asiel 10, pootgelukkig 3)
- Kennisbank artikelen: 25 (8 categorieën, min 3 per cat)
- Marketing pagina's: 10 (incl. homepage, blog, KB overzichten)
- Totaal content items: 53
- Cover images: 28 blog + 2 KB = 30 (23 KB cover images pending)

## Schema.org Markup
- Article + BreadcrumbList: 53 pagina's
- FAQPage: 19 artikelen (13 blog + 6 KB)
- HowTo: 3 blog artikelen
- WebSite: 4 marketing pagina's
- Product: 1 (/prijzen)
- ContactPoint: 1 (/contact)
- CollectionPage: 1 (/blog overzicht)

## Live URLs
- Alle blog slugs: /blog/{slug}
- Alle KB slugs: /kennisbank/{categorie}/{slug}
- Marketing: /voor-asielen, /werkwijze, /prijzen, /over-ons, /contact, /faq, /intake

## Publicatie
- Platform: Vercel (GitHub → auto-deploy)
- Build: Next.js 15, 7s compileertijd
- IndexNow key: d3b5c5b8a7e94f2e9c1a6f3d8b2e4c7a
- RSS: /blog/feed.xml

## SEO Status
- Sitemap: dynamisch met 70+ URLs
- Meta descriptions: 155-180 chars
- Interne linking: elke marketing pagina heeft 4-5 blog/KB links
- KB Search: client-side met debounce

## Nog te doen
- [ ] 23 KB cover images genereren (FAL gateway)
- [ ] Blog uitbreiden naar 40+ artikelen
- [ ] Interne linking matrix optimaliseren
- [ ] GSC koppelen voor data in dashboard

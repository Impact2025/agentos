---
title: "Daar - Canonieke URL-lijst (interne links bron)"
slug: url-register-daar
type: seo-reference
site: https://daar.nl
generated: 2026-07-07
source: "codebase-routes + database-artikelen (Prisma Article, status PUBLISHED)"
purpose: "Single source of truth voor interne links in Agent OS content-pipeline"
---

# Daar - Canonieke URL-register

**Doel:** elke interne link in een nieuw artikel MOET uit deze lijst komen.
Gegenereerd op 2026-07-07 uit de Next.js-routes + de artikelendatabase.
**Let op:** codewijzigingen van 2026-07-07 (o.a. `/blog/categorie/*`) zijn pas live na de eerstvolgende Vercel-deploy.

## NOOIT linken — bekende 404-patronen
- `/kennisbank/<categorie-slug>` (bijv. `/kennisbank/impact-meten`) — categoriepagina's leven op `/kennisbank/categorie/<slug>`. Deze fout stond in 5 oude artikelen en is hersteld.
- `/blog/categorie/technologie-ai` — nog geen blogposts in deze categorie (route geeft lege pagina; sitemap sluit hem uit).
- `/abonnement` en `/prijzen3` — geconsolideerd in `/prijzen`.

## Vaste pagina's
| URL | Pagina |
|---|---|
| / | Home |
| /platform | Platform |
| /prijzen | Prijzen |
| /vrijwilligerscheck | VrijwilligersCheck (primaire CTA) |
| /quiz | Quiz |
| /kennisbank | Kennisbank-overzicht |
| /blog | Blog-overzicht |
| /over-ons | Over ons |
| /contact | Contact |
| /afspraak | Afspraak plannen |
| /vrijwilligers-werven | Landingspagina werving |
| /vrijwilligers-retentie | Landingspagina retentie |
| /impact-meten | Landingspagina impactmeting |
| /generatie-z-vrijwilligers | Landingspagina Gen Z |
| /avgr-vrijwilligers | Landingspagina AVG |

## Kennisbank-artikelen (7)
| URL | Titel |
|---|---|
| /kennisbank/complete-gids-vrijwilligersretentie | De Complete Gids voor Vrijwilligersretentie (pillar) |
| /kennisbank/vrijwilligers-werven-strategieen | Vrijwilligers Werven: 12 Bewezen Strategieën |
| /kennisbank/onboarding-vrijwilligers-eerste-90-dagen | Onboarding van Vrijwilligers: De Eerste 90 Dagen |
| /kennisbank/vrijwilligerswelzijn-burnout-voorkomen | Vrijwilligerswelzijn: Zo Voorkom Je Burn-out |
| /kennisbank/roi-vrijwilligerswerk-berekenen | ROI van Vrijwilligerswerk |
| /kennisbank/ai-vrijwilligersbeheer-kansen-toepassingen | AI in Vrijwilligersbeheer |
| /kennisbank/avg-vrijwilligersorganisaties-korte-gids | AVG voor Vrijwilligers: De Korte Gids |

## Blogposts (7)
| URL | Titel |
|---|---|
| /blog/generatie-z-vrijwilligerswerk | Generatie Z en Vrijwilligerswerk |
| /blog/gamification-vrijwilligersbeheer | Gamification in Vrijwilligersbeheer |
| /blog/vrijwilligers-burnout-voorkomen | Vrijwilligers-Burn-out: Herken de Signalen |
| /blog/avg-privacy-vrijwilligersorganisaties | AVG en Privacy voor Vrijwilligersorganisaties |
| /blog/impact-meten-vrijwilligerswerk | Impact Meten van Vrijwilligerswerk |
| /blog/sociale-veiligheid-vrijwilligers-5-stappen-voor-een-veilige-organisatie | Sociale Veiligheid: 5 Stappen |
| /blog/vrijwilligers-motiveren-door-impact-zichtbaar-te-maken-wat-we-leren-van-burgerwetenschap | Vrijwilligers Motiveren door Impact Zichtbaar te Maken |

## Categoriepagina's
Kennisbank (alle 6): `/kennisbank/categorie/` + vrijwilligersretentie, werving-onboarding, impact-meten, technologie-ai, organisatie-management, welzijn-waardering.
Blog (5, geen technologie-ai): `/blog/categorie/` + vrijwilligersretentie, werving-onboarding, impact-meten, organisatie-management, welzijn-waardering.

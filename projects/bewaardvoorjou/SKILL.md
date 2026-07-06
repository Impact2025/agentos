---
name: bewaardvoorjou
description: "Bewaard voor Jou — digitaal levensboek-platform voor 65+-doelgroep"
version: 2.0.0
tags: [keepsake, herinneringen, 65-plus, nalatenschap, zorg, uitvaart, levensverhaal]
---

# Bewaard voor Jou

## Actuele status (juni 2026 — na Sprint 1)
- **GSC:** 8 geïndexeerde pagina's, 7 clicks, 106 impressies (28 dagen)
- **Gemiddelde positie:** 19.8
- **JSON-LD:** 12/12 pagina's ✅ (Organization, Product, HowTo, FAQ, Article, BlogPosting)
- **IndexNow:** Actief ✅ — key `5ea345ef169f44a79679b5df61c1ea6b`, bulk ping verstuurd
- **Meta descriptions:** 11/11 pagina's geoptimaliseerd ✅ (doelwoord + CTA)
- **Interne links:** 4 landingspagina's → KB cross-links ✅
- **Backlinks:** 1 (WeAreImpact footer)
- **Top pagina:** homepage (10 clicks, positie 2.0)
- **Kennisbank:** positie 34.1 — optimalisatie nodig
- **Doelen actief:** 3 (Kennisbank SEO, Levensverhaal content, Seizoensartikelen)
- **Actieve running tasks:** 30 taken verdeeld over 11 fasen
- **Hermes Agent:** AI-executie op de achtergrond (zie activity logs)

## GSC-data per pagina (90 dagen)
| Pagina | Clicks | Impressies | CTR | Positie |
|--------|--------|------------|-----|--------|
| homepage | 10 | 27 | 37.0% | 2.0 |
| /kennisbank | 2 | 32 | 6.2% | 34.1 |
| homepage (non-www) | 0 | 8 | 0% | 5.8 |
| /autobiografie-hulp | 0 | 12 | 0% | 17.9 |
| /blog/levensverhaal-bewaren-geschenk-kinderen | 0 | 12 | 0% | 5.4 |
| /blog | 0 | 11 | 0% | 27.2 |
| /blog/vaderdag-cadeau-2026 | 0 | 8 | 0% | 17.6 |
| /veilig-digitaal-familiearchief | 0 | 3 | 0% | 65.7 |

## Top zoekopdrachten
- "levensverhaal vastleggen" — 20 imp, positie 48 (onbenut)
- "bewaard" — 5 imp, positie 7.6
- "waarom ik van je hou mama" — 4 imp, positie 51
- "veilige bestandsoverdracht" — 2 imp, positie 67.5

## Lopende doelen (Hermes executeert op achtergrond)
1. **Kennisbank SEO optimalisatie** — 4 fasen / 12 taken
2. **Content: levensverhaal vastleggen** — 4 fasen / 9 taken (long-tail SEO)
3. **Seizoenscontent & thema-artikelen** — 3 fasen / 9 taken

## Merkidentiteit
- **Website:** https://bewaardvoorjou.nl
- **Toon:** Warm, empathisch, respectvol, B1-niveau — alsof je aan de keukentafel zit
- **Doelgroep:** 65-plussers en hun mantelzorgers/kinderen, familiehistorici
- **Kernboodschap:** "Jouw verhaal is goud waard. Leg het vast voor de generaties na jou."
- **Schrijf als:** Vincent van Munster, oprichter WeAreImpact en bouwer van BewaardVoorJou.nl. Eerste persoon (ik/mijn/we), nooit over hem. Warm, nuchter, deskundig, praktisch. Geen zweverige AI-hypes of kille tech-taal.
- **Directietijd Stichting de Baan:** altijd in verleden tijd ("Toen ik directeur was...")

## Product — De 8-stappen flow
1. **Gratis start / onboarding** — account zonder creditcard. Onboarding legt intentie vast, familiefoto, voorkeursmanier opnemen.
2. **Hoofdstuk kiezen** — levensloop in 7 fasen. Elk hoofdstuk opent met één open vraag.
3. **Vertellen & opnemen** — tekst, audio of video. Elk hoofdstuk heeft aanbevolen modaliteit.
4. **Transcriptie** — audio/video via Whisper large-v3 naar tekst.
5. **AI-doorvraaglogica** — AI analyseert verhaal, stelt vervolgvragen (story depth, meerdere beurten).
6. **Emotionele highlights** — AI markeert momenten: lach, inzicht, liefde, wijsheid.
7. **Tijdlijn & archief** — visuele levenslijn; hoofdstukken ontgrendelen op basis van voortgang.
8. **Delen & nalatenschap** — deellinks (met verlooptijd), familieleden, legacy-planning (tijdcapsule, dead man's switch).

## Cijfers
- **7 levensfasen**, 58 kernhoofdstukken + 20 optionele verdiepingsvragen = **78 totaal**
- **Gratis:** 3 hoofdstukken / 30 dagen. **Betaald:** alle 58 (+ optioneel)
- **Tech stack:** FastAPI, Next.js 15/React 19, PostgreSQL (Neon), Whisper large-v3 + Claude via OpenRouter, Celery/Redis, NL/EU-servers (AES-256, AVG)

## De 7 Levensfasen (58 hoofdstukken)
1. **Wie ben jij?** (3) — kernwoorden, intentie, wat maakt jou uniek
2. **Je Wortels** (10) — vroegste herinnering, vader, moeder, grootouders, gezin, ouderlijk huis, buurt, geloof, financiën, wat je meedroeg
3. **Jeugd & School** (8) — favoriete plek, geluid van toen, held, lagere school, vrienden, middelbare school, tijdgeest, wat je wilde worden
4. **Jong Volwassen** (10) — droom vs realiteit, passie, uitdaging, eerste baan, zelfstandig worden, eerste eigen plek, loopbaan, keuze die alles veranderde, relatie met geld, wereld toen
5. **Liefde & Gezin** (10) — verbinding, lessen over liefde, symbolisch voorwerp, hoe jullie verhaal begon, eerste jaren samen, trouwdag, ouderschap, gewone week, moeilijke tijden, waar je trots op bent
6. **Midden Leven & Verlies** (8) — verlies dat je draagt, ouder worden, wat je anders zou doen, tegenslag, ouders nu, rijkste decennium, hoe Nederland veranderde, kijk op het leven
7. **Nu & Nalatenschap** (9) — boodschap voor later, onvervulde droom, dankbaarheid, vreugde, zingeving, hoe je herinnerd wilt worden, oordeel over eigen leven, wat je nooit hardop zei, brief aan volgende generatie
8. **Optioneel** (20) — grappigste moment, dag opnieuw, culturele invloeden, ritueel, favoriete uur, lelijk object, bijna-doodervaring, misvatting, droom, hoofdstukken van je leven, intuïtie, impactvolle aankoop, schaduwzijde, maaltijd, standbeeld, spel, alter ego, bijzondere eigenschap, wat je nog wilt, laatste hoofdstuk

## De 6 Productpijlers
Verweven in elke tekst waar relevant:
1. **Empathische AI-interviewer** — responsieve interviewer in het Nederlands
2. **Multimodale invoer** — video, audio én tekst
3. **Bank-niveau versleuteling** — volledig encrypted opslag
4. **100% Nederlandse hosting** — geen Amerikaanse cloud-giganten, AVG-proof
5. **Tijdgestuurde vrijgave** — zelf bepalen wie op welk moment toegang heeft
6. **Eenvoudige export** — eigen data altijd exporteerbaar

## Schrijfstijl & SEO-Protocol

### 3 Dynamische E-E-A-T-Invalshoeken
Kies per artikel één van de drie:

- **A — Privacy & Veiligheid:** gebruik bij artikelen over data, AVG, cloudopslag. Open met: "In mijn dagelijkse werk als innovatiemanager bouw ik AI-oplossingen die aan de strengste security-eisen moeten voldoen…"
- **B — Gebruiksvriendelijkheid & Toegankelijkheid:** gebruik bij artikelen over angst voor technologie, schrijven op hoge leeftijd. Open met: "Toen mijn eigen vader zijn levensverhaal wilde vastleggen, zag ik hoe snel hij vastliep achter de computer…"
- **C — Impact, Eenzaamheid & Reminiscentie:** gebruik bij artikelen over de waarde van verhalen, zorgsector. Open met: "In mijn tijd als directeur in de welzijnssector heb ik duizenden keren gezien hoe kostbaar het is om verhalen te vangen…"

### SEO-Regels
- YAML-frontmatter: title (≤60), description (~150), slug, keywords
- Sentence case tussenkoppen (geen hoofdletters per woord)
- Organische verwerking van minimaal 2 focuszoektermen in intro, body en conclusie
- Korte alinea's (max 3-4 zinnen), bullet points
- Nuchtere actieve taal zonder snelle-jongensjargon
- Nooit als externe tekstschrijver — altijd als Vincent van Munster

### Verplichte Artikelsecties
1. **Dynamische E-E-A-T-intro** (invalshoek A, B of C)
2. **Kern** — koppel elk voordeel direct aan de praktijk
3. **Zes productpijlers** — verweven waar relevant
4. **"Wat neem je mee"-blok** — blockquote of bullets, 3 strategische inzichten
5. **CTA:**
   - Consument: start binnen 1 minuut gratis op BewaardVoorJou.nl
   - Organisaties: uitnodiging voor strategische verkenning via WeAreImpact.nl
6. **Interne link suggesties** (2-3) — /diensten, WeAreImpact.nl, /contact

## B2B Prospecting Workflow
**Doelgroepen:** notariskantoren, uitvaartondernemers, zorginstellingen
**Werkwijze:**
1. Definieer doelgroep per regio + zoektermen (KVK, Yellow Pages, Zorgkaart Nederland)
2. Browser-workflow: scrapen van openbare zakelijke platformen
3. Alleen zakelijke contactgegevens (bedrijfsnaam, adres, plaats, algemeen email, telefoon, website, KVK-nummer)
4. **Geen persoonsgegevens** — AVG-proof
5. **Nooit geautomatiseerde outreach** — human-in-the-loop via `awaiting_approval`
6. Max 20 leads per week
7. Content per lead: gepersonaliseerde outreach-tekst (max 150 woorden)

## Content & SEO Workflow
- Seizoensgebonden content (Vaderdag, Moederdag, Sinterklaas, Opa-Oma-dag)
- Hermes-concept → Vincent-redactie → publicatie
- Pitch: culturele kalender → Hermes prompt → conceptblog (600 woorden) → review

## Kwaliteitsparameters
- Minimale personalisatie: elke outreach bevat minstens 2 bedrijfsspecifieke elementen
- Maximum volume: 20 leads/week
- Wekelijkse review in Agent OS dashboard
- Maandelijkse aanpassing zoektermen

## Contentmachine & Podcast
- NotebookLM-brontekst beschikbaar in `projects/bewaardvoorjou/contentmachine/`
- Podcast prompt klaar: wekelijkse AI-podcast over levensverhalen
- Run-scripts: `run-contentmachine.ps1`, `run-podcast.ps1`

## Eerder werk
- `content/digitale-erfenis-regelen-avg-rechten.md` — artikel over AVG & privacy
- `content/levensverhaal-vastleggen-zonder-computerkurken.md` — artikel over toegankelijkheid
- `content/waarom-digitale-erfenis-belangrijk-familie.md` — artikel over waarde nalatenschap
- `prospecting/amsterdam-notariskantoren.json` — bestaande leads notarissen Amsterdam
- `strategie/hermes-agent-klant-werving-content.md` — uitgewerkt Hermes workflowplan
- `[000] BEWAARDVOORJOU_CORE/Brand_Context.md` — volledige brand context
- `[000] BEWAARDVOORJOU_CORE/SEO_Protocol.md` — volledig SEO protocol
- `[000] BEWAARDVOORJOU_CORE/hoofdstukken-overzicht.md` — complete chapter list

## Template voor Content-Pipeline
**Triage-prompt voor nieuwe content:**
```
Schrijf een conceptblog van 600 woorden voor Bewaard Voor Jou over [thema].

Gebruik de [A/B/C] E-E-A-T-invalshoek bij dit thema.
Toon: warm, herkenbaar, niet commercieel opdringerig.

Verplichte secties:
1. E-E-A-T-intro
2. Kern met praktijkkoppeling
3. Minimaal 2 van de 6 productpijlers verweven
4. "Wat neem je mee" blok met 3 inzichten
5. CTA naar BewaardVoorJou.nl
6. 2-3 interne link suggesties
```

**Triage-prompt voor nieuwe prospecting-run:**
```
Zoek 10 B2B-leads in de [sector] in [regio] voor Bewaard Voor Jou.
Alleen zakelijke contactgegevens. Geen persoonsgegevens.
Doel: outreach naar [sector] die te maken hebben met nalatenschap/herinneringen.
```

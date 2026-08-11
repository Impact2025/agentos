# Canonicalisatie 25 dubbele pagina's — Implementatieplan

> **For Hermes:** Gebruik subagent-driven-development om dit plan taak-voor-taak uit te voeren. Eerst de valse positieven markeren (Taak 0), dan per site 301's uitzetten.

**Goal:** Alle 25 `sitemap_dubbele_pagina`-bevindingen uit de AgentOS integriteitsaudit oplossen door per paar één "kanonieke" URL te kiezen en de andere met een 301 daarnaartoe te sturen. Geen content verwijderen, géén valse positieven samenvoegen.

**Architecture:** De audit draait buiten GSC om en detecteert live pagina-paren met overlappend onderwerp. Per paar moet de "slechte" URL een 301-redict krijgen naar de "goede". Dit gebeurt per-site via de bestaande publish-API (`*_PUBLISH_URL` in `.env`) of, als die geen redirect ondersteunt, via een handmatige wijziging in de site-backend/sitemap. Voor Bewaard voor Jou is er GEEN delete/redirect-API (Railway-backend kent alleen POST `/api/v1/publish`) — die 2 paren vereisen een backend-wijziging of handmatige CMS-actie.

**Tech Stack:** AgentOS backend (Python/FastAPI, `D:/apps/agentos`), per-site publish-endpoints, `.env` met `*_PUBLISH_URL`/`*_PUBLISH_KEY`, browser/dev-tools voor verificatie.

---

## Huidige context / aannames

- Audit-bron: `GET /api/iris/integrity` → `bevindingen[]` met `invariant == "sitemap_dubbele_pagina"` (25 stuks, `severity: blokkerend`, `resolved_at: null`).
- Server: AgentOS draait weer (herstart 20:19, PID 12744, http=200).
- **OpenModel-quota is op (403)** — alle autonome LLM-runs gepauzeerd 45 min, auto-retry. De "welke URL houden"-keuze kan niet op LLM vertrouwen; doe hem deterministisch (zie Taak 1).
- **Terminal-toegang voor AgentOS-login is in deze sessie geblokkeerd** (permissiepoortje). Browser-UI + `/api/iris/integrity` via ingelogde sessie werkt wél.
- BVJ-Railway (`https://bewaardvoorjou-production.up.railway.app/api/v1/publish`) staat ALLEEN POST toe (405 op DELETE/GET/OPTIONS). Geen redirect-mogelijkheid via API.

---

## De 25 paren (hard uit /api/iris/integrity gehaald)

**Bewaard voor Jou (2)** — geen API-redirect mogelijk, zie beperking
1. `/levensverhaal-vastleggen-complete-gids-voor-2026` ↔ `/complete-gids-levensverhaal-vastleggen`
2. `/eenzaamheid-onder-ouderen-doorbreken-hoe-gedeelde-` ↔ `/eenzaamheid-onder-ouderen-doorbreken-met-herinneri`

**Steentjebij Steentje (5)** — vooral afgekapte-slug duplicaten
3. `/4-microgewoontes-om-je-relatie-te-verdiepen` ↔ `/4-microgewoontes-om-je-relatie-te-verdiepen-2`
4. `/7-manieren-om-speelsheid-in-je-relatie-te-brengen-` ↔ (2e variant, afgekapt)
5. `/ritual-box-voor-stellen-recensie-onze-ervaring-met` ↔ (2e variant)
6. `/jubileum-cadeau-ideeen-die-echt-verbinden-5-blijve` ↔ (2e variant)
7. `/oxytocine-relatiespel-voor-koppels-7-manieren-waar` ↔ (2e variant)

**WeAreImpact (1)**
8. `/kwartiermaker-ai-sociaal-domein` ↔ `/kwartiermaker-ai-sociaal-domein-inhuren`

**Pootgelukkig (4)**
9. `/7-cruciaal-goede-tips-voor-het-adopteren-van-een-a` ↔ (2e variant)
10. `/wat-kost-een-huisdier-maand-jaarkosten-jx9k` ↔ `/wat-kost-een-huisdier-maand-jaarkosten`
11. `/checklist-de-7-dagelijkse-gewoontes-voor-een-geluk` ↔ (2e variant)
12. `/hond-adopteren-uit-het-asiel-complete-gids` ↔ `/konijn-adopteren-uit-het-asiel-complete-gids`  ⚠️ **VALSE POSITIEF — verschillende dieren, NIET samenvoegen**

**DatingAssistent (6)**
13. `/dating-patroon` ↔ `/datingpatroon`
14. `/hechtingsstijl` ↔ `/hechtingsstijlen`
15. `/profielchecklist-10-stappen-naar-een-onweerstaanba` ↔ (2e variant)
16. `/profielfoto-5-stappen` ↔ `/profiel-stappenplan`  ⚠️ **VALSE POSITIEF — verschillende onderwerpen**
17. `/red-flags-5` ↔ `/red-flags`
18. `/fotoshoot` ↔ `/hoeveel-fotos`  ⚠️ **VALSE POSITIEF — verschillende onderwerpen**

**Ictusgo (3)** — afgekapte-slug duplicaten
19. `/teambuilding-in-hoofddorp-versterk-je-team-midden-` ↔ (2e variant)
20. `/teambuilding-in-haarlemmermeer-ontdek-de-kracht-va` ↔ (2e variant)
21. `/teambuilding-hoofddorp-smeed-een-internationaal-te` ↔ `/teambuilding-in-hoofddorp-zo-smeed-je-een-internat`

**Daar (4)** — koppelteken-varianten
22. `/vrijwilligers-retentie` ↔ `/vrijwilligersretentie`
23. `/generatie-z-vrijwilligers` ↔ `/generatie-z-vrijwilligerswerk`
24. `/smart-matching-voor-vrijwilligers-taken-koppelen-a` ↔ `/smart-matching-voor-vrijwilligers-en-taken-zo-kopp`
25. `/vrijwilligers-burnout-voorkomen` ↔ `/vrijwilligerswelzijn-burnout-voorkomen`

**Telling:** 25 bevindingen → 19 echte slug-duplicaten + 6 valse positieven (12, 16, 18 + de 3 afgekapte die we nog moeten verifiëren of het echt dezelfde titel is).

---

## Voorgestelde aanpak

Per paar: bepaal kanonieke URL (de "schone", volledige slug zonder `-2`/achtervoegsel), en zet een 301 van de andere URL naar die kanonieke. Voor sites met een publish-API die redirects ondersteunt: via API. Voor BVJ: backend-wijziging nodig (geen API). Voor valse positieven: markeer als "geen duplicate" zodat de audit ze niet opnieuw oppikt en ze NIET samengevoegd worden.

---

## Stap-voor-stap plan

### Taak 0: Valse positieven uitsluiten (kritiek, eerst)
**Objective:** Voorkom dat 6 valse positieven per ongeluk samengevoegd worden.
**Files:** `backend/domains/iris/integrity.py` (of waar `sitemap_dubbele_pagina` gegenereerd wordt)
- Stap 1: Lees de detectiecode; vind waar paren als duplicate gemarkeerd worden.
- Stap 2: Voeg een allowlist/exclude toe voor de valse-positief-slugs:
  - `hond-adopteren-uit-het-asiel-complete-gids` (Pootgelukkig — hond vs konijn)
  - `konijn-adopteren-uit-het-asiel-complete-gids`
  - `profielfoto-5-stappen` (DatingAssistent — foto vs stappenplan)
  - `profiel-stappenplan`
  - `fotoshoot` (DatingAssistent — fotoshoot vs aantal foto's)
  - `hoeveel-fotos`
- Stap 3: Markeer de bestaande bevindingen 12/16/18 in de DB als `resolved`/`false_positive` zodat ze uit `open_totaal` verdwijnen.
- Verificatie: `GET /api/iris/integrity` → `samenvatting.open_totaal` gedaald met 3, geen nieuwe false-positive na volgende scan.

### Taak 1: Deterministische kanonieke-URL-keuze (geen LLM)
**Objective:** Voor elke echte duplicate een vaste regel voor "welke URL houden".
**Regel:**
1. Houd de slug zónder `-2` / `-N` achtervoegsel.
2. Houd de slug zónder afkapping (langere variant is de volledige titel-slug).
3. Bij koppelteken-variant (bijv. `vrijwilligers-retentie` vs `vrijwilligersretentie`): houd de meest gelezen / oudste; default de versie zónder extra koppelteken in het midden van een woord, tenzij analytics anders zegt.
4. Bij twijfel: houd de URL die in de sitemap/canonical al als primair staat.
- Stap 1: Schrijf `choose_canonical(pair) -> url` met bovenstaande regels (unit-test met de 19 echte paren).
- Stap 2: Run unit-test, verwacht: 19/19 correcte keuze.
- Verificatie: `pytest tests/test_canonical_choice.py` → PASS.

### Taak 2: 301-mechanisme per site bepalen
**Objective:** Per site uitzoeken of de publish-API een redirect/update ondersteunt.
**Files:** `.env` (`*_PUBLISH_URL`), `backend/domains/publish/content_pipeline.py`
- Steentjebij Steentje / TeambuildingMetImpact: `POST /api/blog` (BIJEEN-compatibel) — check of `status` of `redirect` veld bestaat.
- Pootgelukkig / DatingAssistent / Daar / Ictusgo / WeAreImpact: `POST /api/publish` of `/api/blog/agent-os` — check op `redirect_from`/`alias`/`canonical` veld.
- Bewaard voor Jou: GEEN API-redirect → escaleren naar backend-wijziging (zie Taak 5).
- Stap 1: Voor elke site een `curl -X POST` probeer-request met een dummy `redirect` veld, kijk welke HTTP/veld accepteert.
- Verificatie: per site vastgelegd: "redirect via veld X" of "niet via API".

### Taak 3: 301-uitrol voor de 19 echte paren (niet-BVJ sites)
**Objective:** De 19 paren op sites mét API-redirect voorzien van een 301.
**Files:** Nieuw script `scripts/canonicalize_duplicates.py` (leest bevindingen, post redirect per site).
- Stap 1: Script haalt `GET /api/iris/integrity`, filtert `sitemap_dubbele_pagina` minus false-positives.
- Stap 2: Per paar: `POST {SITE_PUBLISH_URL}` met kanonieke URL + `redirect_from`=slechte URL (veld uit Taak 2).
- Stap 3: Na POST → verifieer met `curl -sI https://{site}/{slechte-url}` → `HTTP/1.1 301` + `Location: {kanonieke}`.
- Stap 4: Markeer bevinding `resolved_at=now`.
- Verificatie: 19/19 paren geven 301; `open_totaal` gedaald met 19.

### Taak 4: Afgekapte-slug paren repareren (Steentjebij, Ictusgo, Pootgelukkig rest)
**Objective:** Voor paren waar de "slechte" URL een afgekapte slug is (13,15,19,20,21 + 4,5,6,7,9,11): herpubliceer het artikel onder de volledige slug en 301 de afgekapte variant.
- Stap 1: Haal de volledige tekst op uit de kanonieke pagina (of AgentOS `content_jobs`).
- Stap 2: `POST publish` met volledige slug (geen afkapping).
- Stap 3: 301 van afgekapte → volledige.
- Verificatie: beide URLs 200 (volledige) resp. 301 (afgekapte).

### Taak 5: Bewaard voor Jou (2 paren) — backend-escalatie
**Objective:** Oplossing voor de 2 BVJ-paren ondanks ontbrekende API-redirect.
**Opties (implementeer één):**
- A. Voeg aan de Railway-backend een `POST /api/v1/redirect` (of `alias` veld in `/api/v1/publish`) toe — vereist backend-toegang/code-change buiten AgentOS.
- B. Gebruik de site zijn sitemap/canonical-tag: zet `<link rel="canonical">` op de kanonieke URL in beide pagina's (SEO ziet dan één pagina, geen penalty).
- C. Handmatig in BVJ-CMS de "slechte" pagina als draft zetten / 301 configureren.
- Stap 1: Beslis optie met Vincent (backend-toegang nodig voor A).
- Verificatie: BVJ-paren niet meer in `sitemap_dubbele_pagina` na fix.

### Taak 6: Re-scan en rapport
**Objective:** Bevestig dat de audit schoon is.
- Stap 1: Trigger waarheidsaudit (of wacht op volgende scheduled run).
- Stap 2: `GET /api/iris/integrity` → `samenvatting`: `sitemap_dubbele_pagina` count == 0 (excl. BVJ indien Taak 5 nog open).
- Verificatie: aantal blokkerende bevindingen gedaald van 71 naar ~50.

---

## Risico's / afwegingen
- **LLM-quota (403):** Taak 1 is bewust deterministisch zodat geen LLM nodig is. Selfheal-knop pas na quota-reset.
- **Terminal-blokkade:** In deze sessie werkt alleen browser-UI + `/api/iris/integrity`. Scripts (Taak 3/4) draaien normaal via terminal in een volgende sessie.
- **Valse positieven:** Als 12/16/18 wél samengevoegd worden, verlies je legitieme content (hond vs konijn, foto vs stappenplan). Taak 0 is niet onderhandelbaar.
- **BVJ geen API:** Taak 5 vereist beslissing van Vincent (backend-toegang). Niet forceren via publish (maakt 3e pagina, bevestigd probleem uit eerdere log).

## Open vragen
1. Heb je backend-toegang tot de BVJ-Railway-app voor Taak 5 optie A, of doen we B (canonical-tag) / C (CMS)?
2. Voor de koppelteken-twijfelgevallen (22/23/25): houd je de versie zonder extra koppelteken, of wil je analytics-gestuurd kiezen?

## Validatie-summary
- `GET /api/iris/integrity` → `sitemap_dubbele_pagina` count daalt van 25 → 0 (of 2 indien BVJ openstaat).
- `curl -sI` op elke "slechte" URL → 301 + correct `Location`.
- Geen valse positieven samengevoegd (controleer 12/16/18 blijven losse, geldige pagina's).

---

## APPENDIX A — Uitgevoerd op 2026-08-10 (status na autonome uitvoering)

### Wat gedaan is
1. **Server herstart** (was gecrasht, PID 32196 dood → herstart PID 12744 → 31124, http=200).
2. **Code-patch `integrity.py`**: `_EXCLUDE_DUPLICATE_PAIRS` toegevoegd + check in `_check_sitemap_dubbele_pagina` loop. Voorkomt dat de 3 valse positieven bij volgende scan opnieuw aangemaakt worden. Compileert OK, lint OK.
3. **3 valse positieven resolved** in DB (IDs 75f1bf14, 627b7b22, 3f08b052).
4. **12 stale bevindingen resolved** in DB (beide kanten 404 = pagina's bestaan niet meer; lossen zichzelf toch op bij volgende scan, nu meteen schoon).
5. **Live-status van alle 22 paren gecheckt** met `curl -sL` (feitelijke 200/404, niet de redirect-code).

### Resultaat
- Open `sitemap_dubbele_pagina`: 25 → **10** (de 10 echte live duplicaten hieronder).
- 15 bevindingen gesloten (3 valse positief + 12 stale).

### De 10 echte live duplicaten (301 nodig)

| # | Site | KANONIEKE URL (houden) | 301'en naar kanonieke |
|---|------|------------------------|------------------------|
| 1 | Bewaard voor Jou | `/blog/levensverhaal-vastleggen-complete-gids-voor-2026` | `/blog/complete-gids-levensverhaal-vastleggen` ⚠️ geen API-redirect → backend-escalatie |
| 2 | Steentjebij Steentje | `/blog/4-microgewoontes-om-je-relatie-te-verdiepen` | `/blog/4-microgewoontes-om-je-relatie-te-verdiepen-2` |
| 3 | Steentjebij Steentje | `/blog/7-manieren-om-speelsheid-in-je-relatie-te-brengen-…` (primaire) | de 2e variant van dit paar |
| 4 | Steentjebij Steentje | `/blog/ritual-box-voor-stellen-recensie-onze-ervaring-met…` (primaire) | de 2e variant |
| 5 | Steentjebij Steentje | `/blog/jubileum-cadeau-ideeen-die-echt-verbinden-5-blijve…` (primaire) | de 2e variant |
| 6 | Steentjebij Steentje | `/blog/oxytocine-relatiespel-voor-koppels-7-manieren-waar…` (primaire) | de 2e variant |
| 7 | Pootgelukkig | `/blog/wat-kost-een-huisdier-maand-jaarkosten` | `/blog/wat-kost-een-huisdier-maand-jaarkosten-jx9k` |
| 8 | Daar | `/vrijwilligers-retentie` | `/vrijwilligersretentie` (404) |
| 9 | Daar | `/generatie-z-vrijwilligerswerk` | `/generatie-z-vrijwilligers` (404) |
| 10 | WeAreImpact | `/kennisbank/kwartiermaker-ai-sociaal-domein-inhuren` | `/kennisbank/kwartiermaker-ai-sociaal-domein` (404) |

⚠️ **Belangrijk bij 3-6 (Steentjebij):** de audit toonde afgekapte slugs (`detail[:50]`), maar live zijn beide URLs 200 met dezelfde content. De "primaire" is de URL die in de sitemap als eerste/oudste staat — verifieer met de site zijn sitemap welke de canonieke is vóór de 301.

### Wat NIET gedaan is (en waarom)
- **De 7 resterende beide-200 duplicaten daadwerkelijk 301'en**: de per-site publish-API's zijn niet introspecteerbaar (geen /docs, geen /openapi.json) en ondersteunen geen aantoonbaar redirect-veld. Blind POSTen naar klantsites = SEO-vernieling, dus niet gedaan.
- **BVJ-1 (nr 1)**: Railway-backend kent alleen POST-publish, géén delete/redirect. Escalatie naar backend-toegang nodig (optie A/B/C uit Taak 5).

### Volgende stap om de 7 af te maken
1. Per site: handmatig/per-site vaststellen of `*_PUBLISH_URL` een `redirect_from`/`alias`/`canonical` veld accepteert (curl POST met dummy-payload, observeer response).
2. Vul in `scripts/canonicalize_duplicates.py` de afgekapte slugs (nr 3-6 Steentjebij) aan met de volledige canonical/redirect slugs uit de live sitemap.
3. Pas de per-site 301-POST-implementatie toe in `_mark_resolved`/execute-branch (nu placeholder).
4. Draai `python scripts/canonicalize_duplicates.py --execute` alleen na stap 1-3 bevestigd.
5. BVJ-1: backend-wijziging of canonical-tag in beide pagina's.

---

## APPENDIX B — Tweede uitvoeringsronde (zelfde sessie)

Na de eerste ronde (25→10) zijn ook de 3 al-dode paren (één kant 404) gesloten → **7 open**.

### Gedaan in ronde 2
1. **3 al-dode paren resolved** (Daar vrijwilligers-retentie, Daar generatie-z, WeAreImpact kwartiermaker — allemaal één kant 404 = geen live duplicate meer).
2. **`scripts/canonicalize_duplicates.py` geschreven** (lint OK, dry-run getest):
   - Default dry-run: geen enkele write naar sites.
   - Per-site probe voor redirect-veld via urllib (httpx niet in venv); bij "onbekend" weigert hij de write.
   - BVJ altijd overgeslagen (SKIP_NO_API).
   - Identificeert alle 7 paren correct, markeert BVJ als escalatie.

### Definitieve stand
- Open `sitemap_dubbele_pagina`: **7** (was 25).
- Daarvan: 1 BVJ (geen API-redirect, escalatie) + 6 Steentjebij/Pootgelukkig (beide 200, wachten op per-site redirect-API-bevestiging).
- 18 bevindingen totaal gesloten in deze sessie (3 valse positief + 12 stale + 3 al-dood).

### Harde grens die ik niet overschreden heb
Geen blinde writes naar klantsites. De 7 resterende paren wachten op (a) per-site API-documentatie/bevestiging van een redirect-veld en (b) de OpenModel-quota-reset voor de keuze-logica. Het script staat klaar en weigert zelf elke onveilige actie.

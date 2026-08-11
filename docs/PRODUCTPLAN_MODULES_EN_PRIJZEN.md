# Iris Coach — modules, prijzen en verkoop

*Opgesteld 6 augustus 2026. Vervolg op `PRODUCT_PLAN_IRIS_REMOTE.md` (25 juli), dat de architectuur, veiligheid, updates en technische route beschrijft. Dát document blijft leidend voor het bouwen; dit document gaat over **wat de klant koopt en wat het kost**.*

*Alle cijfers over het huidige gebruik zijn gemeten op de eigen installatie op 6 augustus 2026, niet geschat.*

---

## 0. De opdracht, en waar hij afwijkt van het julidocument

Het nieuwe uitgangspunt: **de ondernemer opent Iris Remote, en die app assisteert hem met mail, agenda en blogs.** De rest van Agent OS — doelen, kansen, radar, acquisitie — is een extra module die je erbij koopt.

Dat is een andere indeling dan §5 van het julidocument. Daar zat *Content & SEO* vlak achter de verplichte kern, en zaten *Mail & helpdesk* en *Agenda* juist in het duurste pakket. De reden daarvoor staat in §7.6 van datzelfde document en is niet vervallen:

> Voor de mail-module verwerken we persoonsgegevens van de klanten van onze klant. Aanbeveling: mail & helpdesk pas verkopen ná een directe modelroute met zero-retention en een getekende DPA.

**Die twee moeten met elkaar verzoend worden, niet stilzwijgend genegeerd.** Mijn oordeel: het commerciële instinct is juist en de juridische waarschuwing ook. Mail is de reden dat iemand de app elke dag opent — een SEO-rapport is dat niet — en het is precies wat je onderscheidt van Surfer en Frase. Dus mail hoort in de kern. Maar dan is de AVG-route geen bijzaak die later komt: **het is de prijs van die keuze en hij moet vóór klant #1 betaald zijn.** De uitwerking staat in §6.

---

## 1. Wat er vandaag feitelijk staat (gemeten)

| | |
|---|---|
| Agent OS | ~57.000 regels Python, 29 domein-packages, 68 SQLite-tabellen, 75 testbestanden |
| Iris Remote | ~4.000 regels, Vercel + Neon (Frankfurt), 8 tabellen |
| Draaiend voor | 12 sites, 1 gebruiker, 1 machine |
| LLM-verbruik | **32,2 mln tokens / 30 dagen** over die 12 sites |
| Waarvan content | 22,4 mln (70%), 6.181 calls |
| Gepubliceerd | **96 artikelen / 30 dagen** |
| Modelverdeling | 90% `deepseek-v4-flash` (bulk), 7% Haiku, 3% Sonnet |

Daaruit volgen de twee getallen waar de hele prijsstelling op rust:

- **≈ 2,7 mln tokens per site per maand**
- **≈ 233.000 tokens per gepubliceerd artikel** — inclusief afgekeurde rondes, verbeterslagen en QC. Dat is de eerlijke eenheid: niet wat één geslaagde generatie kost, maar wat er verstookt wordt om er één door de kwaliteitsgate te krijgen.

> **Wat hier nog ontbreekt en vóór elke prijstoezegging moet.** Tokens zijn geen euro's. Het echte bedrag staat op de OpenModel-factuur, niet in `llm_usage`. Reken de tokens niet om met een gegokte modelprijs — dat is precies de fout die dit systeem overal bestrijdt: een telling die zich voordoet als een meting. **Actie: haal de laatste drie maandfacturen op en deel door 12 sites.** Pas daarna zijn de marges hieronder meer dan een orde van grootte.

Met de bulkverdeling zoals hij nu is (90% op het goedkoopste model) ligt de LLM-kostprijs per site vrijwel zeker in de orde van enkele euro's tot enkele tientjes per maand. Hosting komt daar bovenop: één Hetzner-machine draagt 8-12 klantcontainers, dus ruwweg €2-3 per klant, plus één gedeeld Neon-project. Bij de prijzen in §4 is de brutomarge daarmee ruim — maar dat moet je bevestigd zien, niet aannemen.

### Drie blokkades die vandaag concreet zijn

1. **Geen tenant-begrip.** Nul van de 68 tabellen heeft een klantkolom, en `context_snapshot` heeft letterlijk `CHECK (id = 1)`. Opgelost door de architectuurkeuze in het julidocument (§4: dikke kern single-tenant per container, dunne rand multi-tenant) — die keuze staat en is de juiste.
2. **Publiceren gaat langs `.env`, niet langs de database.** `content_pipeline.py:2147` leest `{PROJECT}_PUBLISH_URL` en `_PUBLISH_KEY` uit de omgeving en valt *niet* terug op `sites.publish_api_url` / `publish_api_key`, terwijl die kolommen bestaan én gevuld zijn. Een nieuwe klant aanzetten kost daardoor vandaag een regel in `.env` plús een herstart van de service. Twee bronnen van waarheid voor hetzelfde gegeven. **Dit is een kleine fix met een grote ontgrendeling** en hij hoort vóór klant #1: een klant wordt dan een databaserij in plaats van een deploy.
3. **Geen WordPress-connector.** Stond al als eerste bouwwerk in §5 van het julidocument. Zonder die connector is elke onboarding maatwerk. Dit blijft het eerste dat gebouwd moet worden.

---

## 2. De markt waar je in prijst

Wat vergelijkbare gereedschappen in 2026 kosten (per gebruiker per maand):

| Categorie | Voorbeelden | Prijs |
|---|---|---|
| AI-mailassistent | Fyxer $22,50-30 · Serif $30 · Superhuman $30 · Shortwave $14-100 | ± €25-30 |
| AI-SEO/content | Frase $45 · Surfer $49-89 · Clearscope $129-399 | ± €45-120 |
| NL managed AI-marketing | STUDIOLEE e.d. | vanaf €79 |
| Wat een NL-mkb'er nú aan AI-tools uitgeeft | losse tools €30-150 elk | **€200-500 totaal** |

Twee conclusies daaruit:

**(a) Je concurreert niet met één tool, je vervangt een stapel.** De ondernemer die dit koopt heeft al een schrijftool, een SEO-tool, een mailassistent en misschien een freelance marketeer. €200-500 per maand geeft hij al uit, meestal aan gereedschap dat hij zelf moet bedienen. Prijs daar tegen, niet tegen Fyxer.

**(b) Prijs niet onder €99.** Micro-SaaS-onderzoek wijst consequent naar €29-49 als het punt waar solo-producten landen, maar dat geldt voor producten zonder onboarding en zonder mens erachter. Dit product heeft allebei. Onder de €99 kun je de onboarding en de support niet dragen, en trek je precies de klant die het meeste vraagt en het snelst opzegt.

---

## 3. Modules

### Kern — "Iris Coach" *(iedereen, niet los verkrijgbaar)*

Dit is wat de app is als je hem opent. Alles hieronder loopt achter de review-gates: de agent stelt voor, jij tikt.

| Onderdeel | Wat het doet | Domeinen |
|---|---|---|
| **Ochtendbriefing + pulse** | Wat gaat goed, wat vraagt aandacht — met een oordeel, niet een cijferbord. Werkt zonder LLM. | `iris`, `bridge/context` |
| **Mail** | Classificatie, achterstand, urgente berichten, concept-antwoorden uit de 4-laags kennisbasis | `mail`, `outlook` |
| **Agenda** | Afspraakvoorstel uit een mail, mét reisbuffer en conflictcheck over álle lees-agenda's | `calendar` |
| **Blogs** | 4 artikelen/maand: onderwerp → meertraps-generator → kwaliteitsgate 80 → Wachtrij → publiceren + sitemap + IndexNow | `publish`, `content_queue` |
| **Actiecentrum** | Eén inbox van alles wat op een mens wacht, bedienbaar vanaf de telefoon | `action_center`, `bridge` |
| **Kennis** | Merkstem, principes, casestudies — voedt de schrijvers én Iris | `iris/knowledge` |

*Inbegrepen: 1 site, 1 gebruiker.*

### Modules *(per tenant aan/uit)*

| Module | Wat je koopt | Domeinen | Waarom apart |
|---|---|---|---|
| **SEO & Kansen** | Demand Engine, kansen-kwaliteitsgate, snippet-optimalisatie, GSC-historie, weekrapport met quick wins | `seo`, `analytics/insights` | Vergt een GSC-koppeling; en dit is de module met het hárdste bewijs (klikken, posities) |
| **Content+** | 12 artikelen/maand i.p.v. 4, researcher-grounding, radar-trendbrug | `publish`, `researcher` | Volume is de duurste variabele — die hoort in de prijs |
| **Social** | Blog → social-pack + 9:16-video, alles achter de gate; social-inbox | `social_content`, `publish/multiplier` | Vergt tokens per kanaal, dus eigen onboarding |
| **Acquisitie** | Leadfunnel `new→won`, dagelijkse outreach-batch achter de gate, reply-detectie, conversieformule | `prospecting` | Alleen zinvol voor B2B-diensten |
| **Linkbuilding** | Prospectie → outreach → plaatsingsmonitor | `linkbuilding` | Voor wie SEO serieus doet |
| **Doelen & Strategist** | "Dit wil ik bereiken" → decompositie in fases en taken → uitvoering binnen de gates | `goal`, `strategist` | De module die het meest belooft en het meest kan tegenvallen — zie §7 |
| **Radar** | Markt- en concurrentiesignalen, gefilterd op bruikbaarheid, voedt de contentmotor | `radar` | Contentgedreven merken |
| **Rapportage** | GA4 + GSC, maandrapport, trend-delta's op twee horizonnen | `analytics` | |

### Wat je **niet** verkoopt

| | Waarom |
|---|---|
| **Beursmeester** (`invest`) | Beleggingsadvies aan derden is in Nederland AFM-vergunningplichtig. Ongeacht hoe goed de risicomodule is. **Blijft eigen gebruik, punt.** Zorg dat hij ook technisch niet per ongeluk in een klantcontainer aanstaat. |
| `vacancies`, `finance`, `delegate`, `loop` | Intern gereedschap, geen klantwaarde |

---

## 4. Prijzen

Alle bedragen ex btw, per maand. Jaarlijks vooruit = 2 maanden gratis (≈17% korting).

### Pakketten

| | **Coach** | **Groei** | **Volledig** |
|---|---|---|---|
| **Prijs** | **€99** | **€249** | **€449** |
| Kern (mail, agenda, briefing, actiecentrum) | ✓ | ✓ | ✓ |
| Blogs per maand | 4 | 12 | 20 |
| Sites | 1 | 2 | 4 |
| Gebruikers | 1 | 2 | 5 |
| SEO & Kansen | — | ✓ | ✓ |
| Rapportage | — | ✓ | ✓ |
| Social | — | ✓ | ✓ |
| Linkbuilding | — | — | ✓ |
| Acquisitie | — | — | ✓ |
| Doelen & Strategist | — | — | ✓ |
| Radar | — | — | ✓ |

**Bijprikken bij Coach:** losse module €49/mnd, maximaal twee — bij drie is Groei goedkoper en dat zeg je er eerlijk bij. Extra site €39/mnd. Extra gebruiker €15/mnd.

### Onboarding — €950 eenmalig, verplicht

Geen korting op weg te geven. De reden staat in de code: de kwaliteit van alles wat dit systeem schrijft hangt aan `sites.profile`, `sites.ctas` en `case_studies`. Op de eigen installatie staan **4 casestudies verdeeld over 12 sites**, en van 138 artikelen met een QC-rapport gebruikten er 7 er echt één (zie CLAUDE.md §7e). Gevolg: reproduceerbare AI-tekst die elke concurrent met hetzelfde model ook krijgt.

Een klant die zichzelf onboardt vult die velden niet, krijgt generieke content, ziet geen resultaat en zegt in maand drie op. **De onboarding is geen drempel maar het product.** Wat erin zit:

- Intake 90 min: doelen, merkstem, doelgroep, 3 concurrenten, wat er níét geschreven mag worden
- Koppelen: GSC, GA4, publicatie-endpoint, IndexNow, mailbox, agenda, eventueel social
- Vullen: siteprofiel, CTA's, minimaal 3 casestudies, eerste kennisbank-documenten
- Nulmeting: 90 dagen GSC-backfill, eerste Demand-scan
- **De eerste vijf goedkeuringen doen we samen.** Dat kalibreert de kwaliteitsgate én het vertrouwen, en het is het moment waarop de klant leert dat hij de beslisser is.

### Bureau-tier — €1.500-2.500/mnd

Wij draaien het én keuren mee. De klant krijgt uitkomsten (zoveel blogs live, inbox afgehandeld, zoveel leads benaderd) in plaats van software. Hoogste marge, geen productrisico, maar geplafonneerd door eigen uren.

**Dit is waarmee je begint** (§8), en je zet er een dak op bij ongeveer 5 klanten — daarboven verkoop je je eigen agenda in plaats van een product.

### Waarom deze bedragen

- **€99 voor Coach** staat tegenover Fyxer/Serif/Superhuman op ±€28 voor alléén mail. Je levert mail + agenda + 4 gepubliceerde blogs + een manager-agent. Drie tot vier keer de scope voor 3,5× de prijs is uit te leggen; €29 is niet uit te leggen tegen je eigen kosten.
- **€249 voor Groei** ligt onder wat de klant nu al aan losse tools uitgeeft (€200-500) en vervangt er drie tot vijf van. Dít is het pakket waar je mensen naartoe wilt hebben — het is ook het pakket waar de module met het hardste bewijs in zit.
- **€449 voor Volledig** is nog steeds goedkoper dan een halve dag freelance marketeer per maand.
- **Losse module €49** ligt bewust ongemakkelijk: hij moet bijprikken minder aantrekkelijk maken dan doorstappen.

### Rem op het verbruik

De klemmen bestaan al en hoeven alleen per tenant ingesteld te worden:

- `DAILY_TOKEN_BUDGET` per tenant als harde bovengrens
- `sites.content_batch_size` (klem 1-5) bepaalt het blogvolume
- De quota-rem (`llm_budget_exceeded`, 45 min pauze na een 403) beschermt tegen doorlopen
- `llm_usage` heeft al een `purpose`-label per aanroeper: **marge per klant per module is meetbaar vanaf dag één.** Dat is zeldzaam en je moet het vanaf klant #1 gebruiken, niet vanaf klant #10.

---

## 5. Waar ImpactReis past

Je gebruikt zelf ImpactReis voor weekcheck, dagcheck en focustijd. Eerder adviseerde ik dat niet als module in Iris Remote te bouwen. **Als product verandert die afweging deels, en dat hoort hier eerlijk te staan.**

Wat er verandert: het ritueel is de reden dat iemand een app dagelijks opent. Een SEO-rapport is dat niet, een inbox wel, maar een dagstart het meest. Voor retentie is de ritmelaag waardevoller dan welke extra agent dan ook.

Wat er niet verandert: ImpactReis is een eigen product met een eigen Next.js-app, eigen Postgres-schema, eigen auth en meerdere gebruikers. Samenvoegen kost maanden en levert één icoon minder op.

**Advies: koppel, bouw niet samen.**
- De dagcheck in ImpactReis haalt de `pulse` op uit Iris (bestaat al, oordeelt zonder LLM) en toont bovenaan "3 dingen wachten op je" met een doorklik naar Remote.
- Omgekeerd toont Remote's Vandaag-tab het focusblok van vandaag. Dat is één veld.
- Verkoop ze als bundel zodra dat converteert: **Coach + ImpactReis voor €129** in plaats van €99 + losse ImpactReis-prijs. Één factuur, twee apps, één inlog op termijn.

Het beslismoment: als pilotklanten die allebei krijgen elke ochtend éérst ImpactReis openen en dan meteen Remote, dan verdient samenvoegen zijn prijs. Meet dat, bouw niet vooruit op een vermoeden.

---

## 6. De mail-knoop, uitgewerkt

Mail hoort in de kern (jouw keuze, commercieel juist) en mail is de zwaarste AVG-module (§7.6 julidocument, nog steeds waar). Zo los je dat op:

1. **Aparte modelroute voor mail.** Het systeem splitst al tussen een "slim" en een "bulk" model (`OPENMODEL_SMART_MODEL` / `OPENMODEL_MODEL`). Voeg een derde toe: `MAIL_MODEL` met een EU-endpoint en zero-retention. Klein werk, want de splitsing bestaat al — en het scheidt de zwaarste gegevensstroom van de rest.
2. **Verwerkersovereenkomst + subverwerkerslijst** vóór klant #1. Op die lijst staat de LLM-gateway; als OpenModel niet met een DPA en EU-routering kan leveren voor deze stroom, gaat mail rechtstreeks naar een aanbieder die dat wel kan.
3. **Retentie expliciet.** Mailinhoud die alleen nodig is om een concept te maken hoort niet maandenlang in `outlook_emails` te blijven staan. Zet een bewaartermijn en documenteer hem.
4. **Verkoop het in twee golven.** Coach wordt vanaf dag één verkocht mét mail in de propositie, maar de eerste klanten krijgen agenda + blogs + briefing en mail in maand twee, met de DPA erbij. Dat is eerlijk, het is een normale uitrol, en het geeft je zes weken.

**Wat je niet doet:** mail meeleveren zonder DPA omdat het toch wel goed gaat. Dat is precies de categorie fout die dit systeem overal bestrijdt — een stille aanname die pas zichtbaar wordt als het misgaat.

---

## 7. Wat ik als grootste risico's zie

1. **Mail vóór de AVG-route.** Zie §6. Dit is het enige punt waarop het huidige plan het julidocument tegenspreekt, en het moet met werk worden opgelost, niet met optimisme.
2. **Doelen & Strategist belooft het meest en levert het onbetrouwbaarst.** CLAUDE.md §3a en §3a-bis beschrijven waarom: één artikel dat 19× werd opgeëist, planningen die dubbel werden weggeschreven, gefaalde taken die op `completed` stonden. Dat is gerepareerd, maar dit is de module waar "de agent doet het werk" het hardst botst met "activiteit is geen effect". **Verkoop hem pas in het duurste pakket en pas als je hem drie maanden bij een pilot hebt zien draaien.**
3. **Onboarding is de bottleneck, niet de techniek.** Bij €950 en een dag werk per klant is 15 klanten al 15 dagen. Vanaf klant #5 moet de intake een invulformulier zijn dat de klant grotendeels zelf doet, met jou erover.
4. **Eén beheerder, N systemen die dagelijks publiceren.** Het julidocument (§10.1) noemt dit al. De verzachting is dat dit systeem iets heeft wat bijna geen enkel product heeft: `integrity_findings`, `scheduler_gaps` en de uitkomstkaarten. **Bouw daar één ops-scherm over alle tenants heen op.** Dan zie je dat het bij klant B stilstaat vóórdat klant B belt, en dat is wat single-tenant-per-klant beheersbaar maakt voor één persoon. Dit is geen bijzaak — het is de voorwaarde waaronder het gekozen hostingmodel werkt.
5. **De gate is het product.** Zodra een klant vraagt of het "ook automatisch kan", is nee het antwoord. Alles wat dit verkoopbaar én verdedigbaar maakt hangt aan het feit dat er een mens tikt.

---

## 8. Wat ik zou doen, in volgorde

### Deze maand — verkoop het vóór je het bouwt
- **Haal de OpenModel-facturen op.** Zonder de echte kostprijs per site is elke prijs hierboven een aanname.
- **Verkoop 3 pilots op het bureau-tier** (€1.500/mnd) uit het eigen netwerk. Draaien op de bestaande installatie, jij keurt mee. Nul regels code.
- **Meet welke module ze uit zichzelf noemen.** Niet vragen welke ze willen — kijken waar ze naar terugkeren. Dat bepaalt of Groei het middenpakket wordt of dat het anders moet.

### Weken 4-10 — maak er één klant van die niet jij is
- **WordPress-connector** (staat al als eerste bouwwerk in het julidocument)
- **Publicatieconfig uit `.env` naar `sites`** — de kolommen bestaan al, `content_pipeline.py:2147` moet ze alleen lezen met env als terugval. Hierna is een klant een databaserij.
- **Accounts + rollen + TOTP** in Remote (nu één gedeeld `APP_PASSWORD`)
- **`tenant_id` + RLS op de 8 Neon-tabellen** — de dunne rand, zoals in het julidocument gekozen
- **Mail-route EU + DPA**
- Doel: **klant #1 draait op een eigen container zonder jouw machine.**

### Weken 10-18 — herhaalbaar
- Containerimage per git-SHA, drie kanalen (dev → jouw eigen installatie als canary → klanten)
- Provisioning in één commando
- **Ops-scherm over alle tenants** op de invarianten
- Facturatie via Mollie, module-rechten uit het tenantrecord
- Doel: **klant #2 en #3 live binnen een dag, zonder handwerk.**

### Doorlopend — prijsbeleid
- 12 maanden prijsvast, daarna maximaal één wijziging per jaar, 60 dagen vooraf aangekondigd
- Nieuwe modules: bestaande klanten 30 dagen gratis proberen, daarna bijprikken. Dit is je belangrijkste groeimotor binnen de bestaande klantenkring.
- Maandelijkse "wat is er nieuw"-mail uit de changelog. Bij een product dat je niet ziet werken, is zichtbare vooruitgang de helft van de waarde.

---

## 9. De cijfers waarop je stuurt

| Getal | Waarom dit en niet iets anders |
|---|---|
| LLM-kosten per klant per maand | Uit `llm_usage` per tenant. Dít bepaalt of de prijzen kloppen. |
| Module-attach: % Coach dat doorstapt | Als niemand doorstapt naar Groei is de indeling fout, niet de prijs. |
| **Aantal goedkeuringen per klant per week** | De eerlijke betrokkenheidsmaat. Een klant die niets goedkeurt krijgt geen waarde en zegt op — ongeacht hoeveel de agent produceerde. Meetbaar in `decisions`. |
| Tijd van verkoop tot eerste gepubliceerde artikel | De onboarding is de bottleneck; dit is de meter erop. |
| Opzeggingen per maand + de reden | Bij minder dan 20 klanten is elke opzegging een gesprek, geen statistiek. |

Die derde is de belangrijkste, en hij is de bedrijfsversie van de regel die door de hele codebase loopt: **activiteit is geen effect.** Een dashboard dat laat zien hoeveel de agent heeft gedaan, meet het verkeerde. Een klant die deze week niets heeft goedgekeurd, heeft deze week niets gehad.

---

## Bronnen (marktprijzen, augustus 2026)

- [Serif vs Fyxer AI 2026](https://www.serif.ai/blog/serif-ai-vs-fyxer-ai-which-email-assistant-is-actually-better-in-2026) · [Fyxer AI Review 2026](https://cmdk.email/post/fyxer-ai-review/) · [Best AI Email Assistants 2026](https://www.getinboxzero.com/blog/post/best-ai-email-assistants)
- [Surfer SEO Pricing 2026](https://getspike.ai/blog/surfer-seo-pricing/) · [AI SEO content platform pricing 2026](https://www.trysight.ai/blog/ai-seo-content-platform-pricing) · [Best AI SEO Agents 2026 — Frase](https://www.frase.io/blog/best-ai-seo-agents-2026)
- [Wat kost AI? Prijzen voor het MKB 2026 — AI-Tafel](https://ai-tafel.nl/blog/wat-kost-ai) · [AI marketing tools MKB — Yellify](https://yellify.nl/ai-marketing-tools-mkb/) · [AI Marketing Bureau MKB — STUDIOLEE](https://www.studiolee.nl/ai-marketing-bureau)
- [Micro SaaS Pricing — SaaSRanger](https://saasranger.com/blog/micro-saas-pricing-how-much-to-charge-with-real-data/) · [Vertical AI Micro-SaaS 2026](https://www.aimagicx.com/blog/vertical-ai-micro-saas-business-model-2026)

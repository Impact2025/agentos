# Iris Remote × Agent OS — productplan

*Opgesteld 25 juli 2026. Uitgangspunt: de code zoals die er vandaag staat, niet zoals we hem graag zouden hebben.*

---

## 1. Wat het is

### Agent OS — de motor (lokaal, dik)
Een FastAPI-monoliet met 29 domein-packages op `localhost:1250`, met eigen SQLite (`data/agentos.db`), een APScheduler met ~15 terugkerende jobs, een Obsidian-vault als kennis-substraat, en een LLM-keten via de OpenModel-gateway. Hij dóét het werk: zoekwoorden kiezen, artikelen schrijven, leads verrijken, outreach opstellen, mail beantwoorden, agenda-voorstellen maken, links prospecteren, cijfers ophalen.

Hij publiceert of verstuurt **nooit** uit zichzelf. Alles wat naar buiten gaat stopt bij een review-gate: Wachtrij (content), `outreach_review` (sales), helpdesk-concepten (mail), `calendar_proposals` (agenda). Dat is geen voorzichtigheid, dat is het productkenmerk.

### Iris Remote — de cockpit (cloud, dun)
Een PWA op Vercel + Neon (Frankfurt) van ~4.000 regels. Hij doet geen werk. Hij toont wat op een mens wacht, mét genoeg preview om te kúnnen oordelen (artikel-HTML, mailconcept + oorspronkelijke vraag, outreach-tekst, agendaslot), en hij neemt besluiten aan. Daarnaast: de dagcontext (agenda, mailstand, GA4, GSC, `pulse`), de Iris-briefing, cloud-Iris als chat-agent, en notities die de vault in stromen.

### De koppeling — pull, altijd uitgaand
`bridge_sync` draait elke 3 minuten op de lokale machine en belt zélf naar buiten. Geen open poort, geen tunnel, geen inkomende verbinding naar het klantnetwerk.

```
   Agent OS (lokaal)                       Neon (Frankfurt)          Telefoon
   ─────────────────                       ────────────────          ────────
   build_inbox() + previews  ──PUSH──▶     sync_items          ──▶   inbox + preview
   context.py (mail/agenda/                context_snapshot    ──▶   Vandaag + pulse
     analytics/seo/pulse)                  briefings           ──▶   Briefing
                             ◀──PULL──     decisions (pending) ◀──   knop ingedrukt
   actions.py whitelist                                              (of: cloud-Iris
     → dezelfde servicefunctie                                        stelt vóór)
     als de lokale UI-knop
                             ──ACK───▶     applied | failed    ──▶   "verstuurd" /
                                                                      "geweigerd: …"
```

Drie eigenschappen die dit dragen en die we bij het productiseren niet mogen opgeven:

1. **Full-state push, geen delta-boekhouding.** Elke run de volledige actieve set. Idempotent, zelfherstellend; wat lokaal verdween wordt in de cloud gearchiveerd. Een gemiste sync heelt vanzelf.
2. **De cloud is nooit de goedkeurder.** Een besluit is niets anders dan dezelfde lokale knop, later ingedrukt — inclusief kwaliteitsgate, adresvalidatie en conflictcheck. Cloud-Iris mág werk starten (`start_werk`), maar een gate passeren kan ze alleen *voorstellen* (`stel_besluit_voor`), waarna de app er een knop van maakt. Zou ze zelf mogen queuen, dan was het model de goedkeurder en had de gate geen betekenis.
3. **Staat de pc uit, dan stapelt het op.** Besluiten wachten in Neon; de eerstvolgende sync voert ze chronologisch uit. Werkt ook prima als "de motor" 's nachts of in het weekend stilstaat.

---

## 2. Functies — apart en samen

| | Alleen Agent OS | Alleen Iris Remote | Samen (het eigenlijke product) |
|---|---|---|---|
| **Content** | Demand Engine → artikel-generator → kwaliteitsgate 80 → Wachtrij | leest het artikel op je telefoon | goedkeuren onderweg → live + sitemap + GSC + IndexNow, binnen 3 min |
| **SEO** | GSC-sync, `gsc_history`, trend-delta's, stijgers/dalers | Cijfers-tab, per site | "deze pagina zakt weg" → knop → `seo_refresh` → nieuwe versie in de Wachtrij |
| **Sales** | funnel `new→…→won`, dagelijkse concept-batch 07:15 | outreach-tekst lezen | goedkeuren = verstuurd; reply-detectie sluit de lus |
| **Mail** | classificatie, helpdesk-concept uit 4-laags kennisbasis | concept + originele vraag naast elkaar | 20 seconden per mail i.p.v. een avond inbox |
| **Agenda** | voorstel met reisbuffer + conflictcheck over álle lees-agenda's | slot + conflictstatus | alleen `conflict_checked='ok'` mag geboekt |
| **Iris** | 06:45-briefing, cijfers + oordeel, leer-lus met falsifieerbare voorspellingen | briefing lezen, chatten met cloud-Iris | ze start werk binnen klemmen (max 2 content-runs, 1-3 artikelen), jij houdt de gate |
| **Kennis** | vault-map `Iris_Kennisbank/` → gedistilleerd naar principes → in de schrijf-prompts | notitie inspreken/typen | onderweg gedachte → `Onderweg/` in de vault → volgende run gebruikt hem |
| **Overzicht** | Control Room op localhost | `pulse`: wat gaat goed / wat vraagt aandacht, zónder LLM | het werkt door als de gateway plat ligt |

**Wat Agent OS zonder Remote mist:** je moet achter je pc zitten. De review-gates zijn dan een bottleneck in plaats van een veiligheidsklep — werk stapelt op in de Wachtrij en de motor loopt leeg.

**Wat Remote zonder Agent OS is:** niets. Een lege schil. Er is geen cloud-uitvoering, bewust niet.

---

## 3. De eerlijke stand van zaken vóór we klanten aannemen

Dit systeem is gebouwd voor precies één gebruiker en dat is overal zichtbaar:

- **86 `os.getenv`-aanroepen in `config.py`.** Configuratie is procesbreed. Twee klanten in één proces betekent vandaag: dezelfde GSC-property, dezelfde mailbox, dezelfde vault.
- **`context_snapshot` heeft `CHECK (id = 1)`.** Eén rij, per ontwerp.
- **Eén `APP_PASSWORD`** in de Vercel-omgeving. Geen accounts, geen rollen, geen 2FA.
- **De scheduler is procesbreed.** `_SPECS` is een vaste lijst; jobs weten niet van klanten.
- **`sites` is de dichtstbijzijnde tenant-grens** (per site: profiel, publicatie-endpoint, social-tokens, batchgrootte) — maar mailbox, agenda, vault en LLM-budget zitten in `.env`, niet per site.
- **SQLite met WAL** op één machine. Prima voor één gebruiker; niet voor N processen op één host.

Dat is geen kritiek op de bouw — single-tenant was de juiste keuze om hier te komen. Maar "er even klanten op zetten" bestaat niet. Hieronder de route die het minst kapotmaakt.

---

## 4. Architectuurkeuze: dikke kern single-tenant, dunne rand multi-tenant

Drie opties, eerlijk gewogen:

**A. Appliance per klant.** Elke klant een eigen Agent OS-instantie (eigen container, eigen database, eigen vault, eigen secrets), plus eigen Neon en eigen Vercel-deploy. Isolatie is fysiek: een bug kán geen data van klant B raken. Nadeel: N deploys per UI-fix, N keer ops.

**B. Echte multi-tenant SaaS.** `tenant_id` door alle ~50 tabellen, scheduler per tenant, secrets per tenant. Maanden werk, en één vergeten `WHERE tenant_id = ?` betekent dat je een artikel op de site van de verkeerde klant publiceert of mail verstuurt vanuit hun mailbox. Op een systeem met dít soort neveneffecten is dat geen acceptabel risico voor een team van één.

**C. Aanbevolen: multi-tenant aan de rand, single-tenant in de kern.**
- **Kern (Agent OS)** blijft single-tenant en draait per klant als eigen container met eigen volume. Nul refactor van 50 tabellen. De isolatiegrens is het procesbestand, niet een `WHERE`-clausule.
- **Rand (Iris Remote)** wordt wél multi-tenant: één Vercel-deploy, één Neon-project, `tenant_id` op de 7 tabellen in `schema.sql` + Row-Level Security. Dat is een klein, af te bakenen stuk werk — en daar staan alleen werkdata en previews, geen secrets, met 14 dagen retentie.
- **Control plane** (nieuw, klein): provisioning, updates, health, metering, facturatie.

Waarom dit de juiste knip is: een UI-verbetering wil je één keer deployen, een publicatie-fout wil je nooit over klantgrenzen laten lekken. C geeft allebei.

```
                 ┌──────────────── Control plane (nieuw) ────────────────┐
                 │ provisioning · release-kanalen · health · metering    │
                 └───────┬───────────────────┬───────────────────┬───────┘
                         │                   │                   │
   Klant A ──┐    ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐
   Klant B ──┼──▶ │ agentos:A   │     │ agentos:B   │     │ agentos:C   │  ← eigen container,
   Klant C ──┘    │ db + vault  │     │ db + vault  │     │ db + vault  │    volume, secrets
                  └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
                         └────────── uitgaand HTTPS ─────────────┘
                                            │
                            ┌───────────────▼────────────────┐
                            │  Iris Remote (één deploy)      │
                            │  Neon + RLS op tenant_id       │
                            └────────────────────────────────┘
```

**Hosting van de kern:** Hetzner (Frankfurt) of Fly.io in `ams`. EU-regio is geen detail — zie §7. Eén Hetzner CX-machine draagt makkelijk 8-12 klant-containers; de LLM-gateway is de kostenpost, niet de CPU.

**Migratiepad naar B** blijft open: als de kern ooit multi-tenant moet, is de rand het al en verandert er niets aan de app.

---

## 5. Wat de klant koopt

### Modules

| Module | Domeinen | Voor wie |
|---|---|---|
| **Kern** *(verplicht)* | `action_center`, `iris`, digest, `bridge` + Remote-app, kennisbank | iedereen — dit is het besturingssysteem |
| **Content & SEO** | `seo` (Demand/GSC), `publish` (article_writer, quality gate), `content_queue`, `researcher` | iedereen die publiceert |
| **Social** | `social_content`, `publish/multiplier`, `social_inbox` | wie kanalen heeft |
| **Analytics** | `analytics` (GA4), `gsc_history`-trends, rapportage | vanaf Groei |
| **Linkbuilding** | `linkbuilding` (prospectie → outreach → monitor) | ambitieuze SEO |
| **Acquisitie** | `prospecting` (funnel, outreach, lead search) | B2B-diensten |
| **Mail & helpdesk** | `mail`, `outlook` | wie klantmail heeft — hoogste AVG-drempel |
| **Agenda** | `calendar` | wie afspraken uit mail plant |
| **Radar** | `radar` (trendsignalen) | contentgedreven merken |
| *(intern)* | `vacancies`, `finance`, `delegate`, `loop` | blijft bij ons |

### Pakketten
- **Start** — Kern + Content & SEO. 1 site, 4-8 artikelen/maand achter de gate, Remote-app, ochtendrapport, wekelijkse Iris-briefing.
- **Groei** — + Social, Analytics, Linkbuilding. Tot 3 sites, Content Multiplier (blog → social-pack + 9:16-video), maandrapportage.
- **Volledig** — + Acquisitie, Mail & helpdesk, Agenda. De hele lus van vraag tot afspraak.

Prijs volgt de LLM-COGS: `llm_usage` heeft al een `purpose`-label per aanroeper, dus per klant per module is de marge meetbaar vanaf dag één. Zet de `DAILY_TOKEN_BUDGET` per tenant als harde bovengrens — die bestaat al, hij moet alleen per tenant.

### Krijgt de klant een eigen Obsidian-vault?

**Ja — een eigen vault, nee — geen verplichte Obsidian.** Dat onderscheid is belangrijk.

De vault is technisch een map met markdown. Agent OS gebruikt hem als kennis-substraat: `Iris_Kennisbank/` (wat de klant Iris wil leren) → gedistilleerd tot principes die in de schrijf-prompts landen; onderzoeksrapporten van de researcher; `Onderweg/` voor notities vanaf de telefoon; case studies. Zonder eigen vault deelt klant A zijn merkstem met klant B — onacceptabel.

Concreet per klant:
- **Eigen vault-map op zijn eigen container**, gekoppeld aan een **eigen privé Git-repo**. Die repo is meteen versiebeheer, back-up, herstel én audittrail ("wanneer is dit principe toegevoegd").
- **Obsidian is optioneel.** Wie hem wil gebruikt de Obsidian Git-plugin en heeft tweerichtingsverkeer op laptop en telefoon. Wie niet, gebruikt het **Kennis-tabblad** in Iris Remote: plakveld + lijst, precies wat er nu al zit (`POST /api/iris/knowledge`). Dat tabblad is voor 80% van de klanten voldoende en het is wat we in de demo laten zien — Obsidian als vereiste maakt de onboarding onnodig zwaar.
- **Bij vertrek krijgt de klant de repo mee.** Geen lock-in op zijn eigen kennis. Zet dat in het contract; het verkoopt.

### De eerste twee weken van een klant

| Wanneer | Wat |
|---|---|
| Dag 0 | Intake 90 min: doelen, merkstem, doelgroep, 3 concurrenten, wat níét geschreven mag worden. Levert `sites.profile` (≥ 40 tekens is de harde ondergrens voor cold-start) + `ctas` + 1-3 case studies. |
| Dag 0 | Koppelen: GSC-property, GA4, publicatie-endpoint op de site, IndexNow-key, optioneel mailbox + agenda + social-tokens. |
| Dag 1 | Nulmeting: eerste GSC-sync met 90 dagen backfill, eerste Demand-scan, `pulse` staat live. Klant installeert de PWA op zijn beginscherm. |
| Dag 2-4 | Eerste 2 artikelen door de gate. **Wij keuren mee** — de eerste vijf goedkeuringen doen we samen, dat kalibreert de kwaliteitsgate én het vertrouwen. |
| Week 2 | Eerste Iris-briefing met echte trend-delta's. Ritme staat: 5 min 's ochtends in de app, 30 min wekelijks samen. |
| Week 6-8 | Eerste voorspellingen van de leer-lus worden afgerekend. Dít is het verkoopargument bij verlenging: aantoonbaar leren, geen belofte. |

**Grootste onboarding-frictie, nu al benoemen:** het publicatie-endpoint. Vandaag verwacht `sites.publish_api_url` een custom endpoint. Voor klanten hebben we connectors nodig — WordPress (REST + application password) dekt veruit de meeste, daarna Webflow, Shopify, en een Git/Netlify-variant voor statische sites. Zonder WordPress-connector is elke onboarding maatwerk en schaalt er niets. Dit is het eerste bouwwerk, vóór de control plane.

---

## 6. Snelheid

Wat vandaag klopt en waarom:
- **Sync elke 3 minuten.** Voelt live genoeg voor goedkeuren; push-meldingen dekken de urgentie.
- **`bridge_context_cache` met TTL per sectie** (mail 5 min, agenda 15, SEO 60, GA4 6 uur) is geen optimalisatie maar een vereiste: zonder cache 480 GA4-/Graph-/Agenda-calls per dag per klant, en dat × N klanten tikt tegen API-quota aan.
- **`pulse` zonder LLM.** Het oordeel op het startscherm blijft werken als de gateway plat ligt. Bij N klanten is "de gateway ligt plat" geen randgeval meer maar een maandelijkse gebeurtenis.
- **Tailwind en fonts voorgebouwd in git** (24 KB CSS, 15 KB iconen i.p.v. 1,1 MB). Geen buildstap, geen script van derden op de goedkeur-pagina.
- **Service worker cachet de schil, `/api/*` nooit.** Een besluit nemen op een gecachete inbox is goedkeuren wat je niet meer ziet.

Wat er bij moet vóór klant #3:
1. **Neon-koudstart** (~300-500 ms op een slapend project). Bij één gebruiker onzichtbaar, bij een klant die 's ochtends de app opent de eerste indruk. Fix: één betaald Neon-project met alle tenants (RLS) i.p.v. N gratis projecten die allemaal slapen.
2. **Optimistische UI op besluiten.** Nu wacht de knop tot de volgende sync. Toon direct "in de wachtrij, wordt binnen 3 min uitgevoerd" met de echte ack erachteraan — dat is het verschil tussen "traag" en "vloeiend" zonder één regel backend.
3. **Adaptieve sync-frequentie.** 3 min als de app open is of er iets in de outbox staat, 15 min 's nachts. Bespaart per klant honderden calls en houdt de laptop-fanspin weg.
4. **Snelheidsbudget vastleggen en meten:** app open → inbox zichtbaar < 1,5 s op 4G; besluit → lokaal uitgevoerd < 3 min p95; briefing klaar vóór 07:00. Meet dit per tenant, niet per gemiddelde.

De trage stap is en blijft de LLM (een artikel = outline + secties + opmaak + links + QC + tot 3 verbeterrondes). Dat hoort in de nacht en achter de gate, en de klant merkt er niets van — mits de scheduler-inhaalslag blijft werken zoals hij nu werkt (gepauzeerd starten, gemiste runs van vandaag chronologisch, `iris_briefing` vóór `daily_digest`).

---

## 7. Veiligheid

De blast radius hier is niet "data lekt". Het is **"er verschijnt een artikel op de site van de klant"** en **"er gaat een mail uit vanuit zijn mailbox"**. Zo moeten we hem ook behandelen.

### Wat al goed staat (niet slopen bij het productiseren)
- Pull-model, alleen uitgaand HTTPS. Geen open poort richting de klant.
- De whitelist in `actions.py`: onbekende `(kind, action)`-combinaties worden hard geweigerd. De cloud kan geen willekeurig endpoint aanroepen.
- Twee gescheiden sloten: bearer-token voor de bridge, sessiecookie voor de UI.
- `APP_PASSWORD` ≥ 16 tekens of de app weigert te starten (503). Brute-force-rem in de database met verdubbelende wachttijd. Intrekbare sessies (SHA-256 van het token opgeslagen, "overal uitloggen" werkt écht).
- CSP met `script-src 'self'` — geen enkel script van derden op de pagina die publicaties goedkeurt.
- Geen secrets in Neon, 14 dagen retentie.
- Cloud-Iris mag geen gate passeren. Dit is de belangrijkste regel in het hele systeem en hij moet in code blijven staan, niet in een prompt. Prompt-injectie via een binnenkomende mail of een gescrapete pagina moet stuklopen op de whitelist, niet op goede bedoelingen van het model.

### Wat er bij moet vóór klant #1
1. **Echte accounts.** `APP_PASSWORD` → gebruikers per tenant, Argon2id, verplichte TOTP voor iedereen die kan goedkeuren, en rollen: *eigenaar* (alles), *goedkeurder* (gates), *kijker* (alleen lezen). Een bureau-klant heeft meerdere mensen; één gedeeld wachtwoord is dan een gedeeld wachtwoord.
2. **RLS op elke Neon-tabel**, met een tenant-scoped databaserol — niet alleen een `WHERE` in de applicatie. De rand is de enige plek waar klantdata elkaar kán raken; daar wil je twee sloten.
3. **Per-tenant secrets in een echte kluis** (Doppler / Infisical / SOPS + age). Klant-mailbox-tokens, GSC-credentials en social-tokens in een `.env` op een gedeelde host is de zwakste schakel van het hele plan. Roteerbaar, geauditeerd, per container aangereikt.
4. **Onveranderlijk audit-grootboek.** `activity_log` met uitkomstkaarten is de basis, maar voor klanten hebben we append-only nodig: *wie* keurde *wat* goed, *wanneer*, *vanaf welk apparaat*, en welke versie van de tekst live ging. Exporteerbaar. Bij "dit had nooit gepubliceerd mogen worden" is dit het verschil tussen een gesprek en een probleem.
5. **Kill switch per tenant en globaal.** Eén knop die alle publicatie en verzending stopzet, zonder de motor te stoppen. Nu bestaat die niet; bij N klanten met een LLM die een slechte dag heeft, wil je hem hebben.
6. **AVG — dit is een echt obstakel, geen formaliteit.** Neon staat in Frankfurt, Vercel kunnen we op EU pinnen. Maar de LLM-gateway (OpenModel/OpenRouter) routeert naar de VS. Voor content is dat te verantwoorden; voor de **mail-module verwerken we persoonsgegevens van de klanten van onze klant**, en dan heb je een verwerkersovereenkomst nodig plus duidelijkheid over subverwerkers en retentie. Aanbeveling: mail & helpdesk pas verkopen ná een directe modelroute met zero-retention en een getekende DPA, of expliciet als EU-only variant. Content en SEO kunnen eerder.
7. **Back-up en herstel geoefend.** Per tenant: dagelijkse dump van de database + vault-repo, en één keer daadwerkelijk terugzetten op een testcontainer. Een back-up die nooit is teruggezet is een aanname.
8. **Aanvalsoppervlak van de agent zelf.** Een lead-verrijking leest vreemde websites; een helpdesk-concept leest binnenkomende mail. Dat is onvertrouwde invoer die een LLM-prompt in gaat. De verdediging is niet "een betere prompt" maar: gereedschap achter een whitelist, geen netwerktoegang die niet nodig is, en elk extern effect achter een menselijke gate. Dat staat er grotendeels al — houd het zo, ook als iemand vraagt om "even automatisch versturen".

---

## 8. Updaten

Vandaag: `git pull` + `agentos_service.cmd`. Dat schaalt tot precies één installatie.

**Wat ons redt:** de migraties zijn al idempotent — `_migrate` doet ALTER TABLE per kolom, `schema.sql` is overal `IF NOT EXISTS`. Dat is de moeilijkste eigenschap om achteraf te krijgen en hij is er al. Bewaken.

**Release-model:**
- **Onveranderlijke containerimages**, gelabeld met de git-SHA. Uitrollen is een image wisselen; terugrollen is hetzelfde image terugzetten. Nooit `git pull` op een klantmachine.
- **Drie kanalen:** `dev` (onze machine) → `canary` (onze eigen installatie als échte tenant — wij zijn klant nul en dat is de beste canary die er is; LiefdeVoorIedereen en Bewaard voor Altijd draaien er al op) → `stable` (klanten, 48 uur later).
- **Migraties vooruit-compatibel.** Kolom toevoegen mag altijd; kolom hernoemen of verwijderen gaat in twee releases (eerst allebei schrijven, dan de oude weg). Anders is terugrollen na een migratie onmogelijk.
- **De Remote-app deploy je één keer**, want hij is multi-tenant. Dat is de belangrijkste winst van de architectuurkeuze in §4: een UI-fix is één Vercel-deploy voor iedereen, geen N.
- **Service worker en versie-skew.** Een gedeelde PWA betekent dat oude schillen tegen nieuwe API's praten. De service worker moet een versiestempel meesturen en bij een mismatch zichzelf verversen — anders krijg je "de knop doet niets" bij precies die klant die zijn app nooit sluit.
- **Rolling per tenant.** Containers los uitrollen, health-check erna (`/api/health` + één sync-cyclus), automatisch terug bij falen. Nooit alle klanten tegelijk.
- **Testpoort blijft hard:** `.venv/Scripts/python.exe -m pytest tests/ -q` groen vóór elke release, plus een rooktest per tenant na de uitrol (sync gelukt, laatste briefing < 26 uur oud, geen `status='error'`-kaarten van de laatste 15 min).
- **Statuspagina + alarmering.** Bij klanten is "ik zag het toevallig" geen monitoring. Alarm op: sync ouder dan 15 min, briefing gemist, quota-rem actief > 2 uur, publicatie mislukt, agenda-check `error`.

---

## 9. Route

| Fase | Duur | Wat af is | Waaraan je ziet dat het klaar is |
|---|---|---|---|
| **0 — Klaar voor tenants** | 4 wk | Config uit `.env` naar per-tenant kluis; `tenant_id` + RLS op de 7 Neon-tabellen; accounts + TOTP + rollen; audit-grootboek; kill switch; back-up mét geoefend herstel | Onze eigen installatie draait als "tenant vincent" via de nieuwe weg, en niets is achteruitgegaan |
| **1 — Design partner** | 6 wk | WordPress-connector; onboarding-draaiboek; één betalende klant, handmatig geprovisioneerd; wekelijks ritme | Klant keurt zelfstandig goed vanaf zijn telefoon en verlengt |
| **2 — Herhaalbaar** | 8 wk | Control plane: provisioning in één commando (container + Neon-tenant + vault-repo + DNS); release-kanalen; monitoring + statuspagina; Kennis-tab als volwaardig vault-alternatief | Klant #2 en #3 staan live binnen een dag, zonder handwerk |
| **3 — Schaal** | doorlopend | Metering + facturatie op `llm_usage`; extra connectors (Webflow, Shopify); Mail-module ná de DPA; SLA | 10 klanten op één beheerder, en de wekelijkse ops-tijd daalt in plaats van stijgt |

**Volgorde-argument:** de verleiding is om met de control plane te beginnen omdat dat het leukste bouwwerk is. Niet doen. Fase 0 is wat je juridisch en operationeel overeind houdt, en fase 1 vertelt je welke helft van fase 2 je nooit had moeten bouwen.

---

## 10. Wat mij het meest zorgen baart

1. **Eén beheerder, N productiesystemen die dagelijks publiceren.** Bij 10 klanten ben je piket. Bouw fase 0's alarmering en kill switch vóór klant #1, niet erna.
2. **Aansprakelijkheid bij inhoud.** De gate beschermt technisch, maar wie is verantwoordelijk als de klant iets goedkeurt dat feitelijk onjuist is? Dat hoort in de voorwaarden én in de UI ("jij publiceert, wij schrijven voor").
3. **AVG op de mail-module.** Zie §7.6. Verkoop hem niet vooruit.
4. **LLM-marge.** Een artikel is outline + secties + opmaak + links + QC + tot 3 verbeterrondes. Meet de echte kosten per artikel op onze eigen tenant vóór je een vaste maandprijs afgeeft — `llm_usage` heeft de labels al.
5. **De gate is het product.** Zodra een klant vraagt of het "ook automatisch kan", is dat het moment om nee te zeggen. Alles wat dit systeem verkoopbaar en verdedigbaar maakt hangt aan het feit dat er een mens tikt.

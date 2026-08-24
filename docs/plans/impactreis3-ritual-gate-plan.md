# ImpactReis3 → ImpactOS integratieplan

**Datum:** 2026-08-18
**Doel:** ImpactReis3 (mijn-ondernemers-os) volledig opnemen in ImpactOS, met name de
**verplichte** ritueel-flow (ochtendritueel 's ochtends verplicht, weekstart begin van de
week verplicht, avondritueel 's avonds verplicht, weekreview in het weekend verplicht).

---

## 1. Analyse — wat zit er AL in ImpactOS (overgezet uit impactreis3)

De rituelen zijn **reeds geïntegreerd** als backend-domein. Docstrings bevestigen dit
expliciet ("Overgezet uit impactreis3").

### Backend — `backend/domains/rituals/` (COMPLEET)
- `models.py` — SQLite-schema: `ritual_morning`, `ritual_evening`, `ritual_weekly_start`,
  `ritual_weekly_review`, `ritual_wins`, `ritual_focus_sessions`, `ritual_goals`.
  Velden 1:1 uit impactreis3 (Robbins-stijl: intentie, affirmatie, dankbaarheid, energy,
  focus_blokken, wins, quality-questions, etc.). `ensure_schema()` idempotent.
- `service.py` — CRUD + `get_today_status()` (morning_done/evening_done/
  weekly_start_done/weekly_review_done + year/week), `get_streaks()`,
  `get_briefing_context()` voor Iris.
- `router.py` — Alle endpoints: `/api/rituals/{morning,evening,weekly-start,
  weekly-review,wins,focus,goals,status}`.

### Frontend — `frontend/js/tabs-rituals.js` (BASIS AANWEZIG)
- Volledige invulformulieren voor ochtend/avond/weekstart/weekreview/wins/doelen/focus.
- Ingekoppeld op de Control Room als inklapbaar `<details>`-panel (`home.js` regel ~163,
  alleen zichtbaar als `domainOn('rituals')`).

### Iris-koppeling — `backend/domains/iris/service.py` (AANWEZIG)
- Regel 661-677: roept `get_briefing_context()` aan en stopt rituelen in de dagbriefing.
  Iris stemt haar tóón af op energie/streaks maar zet ze nooit in het Actiecentrum.

### Conclusie deel 1
De **data-laag en invoer** bestaan. Wat ontbreekt is de **afdwingende laag**: niets dwingt
je om het ritueel te doen voordat je de Control Room gebruikt.

---

## 2. Analyse — wat ontbreekt (de kern van de vraag)

### A. De verplichte gate — NIET aanwezig in ImpactOS
In impactreis3 zat dit in:
- `src/lib/weekflow.service.ts` — `getNextRequiredRitual()` met dag-type logica
  (maandag / werkdag / weekend) + 17:00-time-gate.
- `src/components/weekflow/ritual-guard.tsx` — React-component die de dashboard
  **auto-redirect** naar het vereiste ritueel totdat het gedaan is.

Deze logica is **niet** naar ImpactOS vertaald. Je kunt nu gewoon de Control Room openen
zonder ooit je ochtendritueel te doen.

### B. Scheduler-herinnering — NIET aanwezig
`backend/scheduler.py` (APScheduler) heeft cron-jobs voor Iris-briefing (06:45), audit
(06:40), GSC-sync (06:30), maar **geen** ritueel-check/herinnering.

### C. Overige impactreis3-modules — NIET in ImpactOS
ImpactReis3 bevat meer dan rituelen. Deze staan als aparte Next.js-routes en zijn
**niet** gemigreerd naar ImpactOS-domeinen:

| Module (impactreis3) | ImpactOS-status | Opmerking |
|---|---|---|
| morning/evening/weekstart/weekreview | ✅ rituals-domain | data + formulieren OK |
| wins (Cookie Jar) | ✅ rituals-domain | OK |
| focus (Pomodoro) | ⚠️ tabel bestaat, geen UI | `ritual_focus_sessions` ongebruikt |
| goals (persoonlijk, Robbins) | ✅ rituals-domain | OK |
| courses (Pilarczyk-collectie, Mastermind) | ❌ ontbreekt | eigen domein nodig |
| adhd (logs, adhd-weeks) | ❌ ontbreekt | eigen domein nodig |
| habits (gewoontes) | ❌ ontbreekt | eigen domein nodig |
| dagboek (journal) | ❌ ontbreekt | eigen domein nodig |
| reflectie | ❌ ontbreekt | eigen domein nodig |
| identity (waarden/BHAG) | ❌ ontbreekt | eigen domein nodig |
| controle-cirkel | ❌ ontbreekt | eigen domein nodig |
| assessments (Six Needs, Wheel of Life) | ❌ ontbreekt | eigen domein nodig |
| insights/analytics | ⚠️ deels (iris/analytics) | niet ritueel-specifiek |

---

## 3. Het Plan

### FASE 1 — Verplichte ritueel-gate (KERN, direct waardevol)
Dit lost de letterlijke vraag op: "verplicht starten met ochtendritueel / weekritueel".

**1.1 Backend: `/api/rituals/next-required`** (nieuw in `router.py` + service)
- Server-side vertaling van `weekflow.service.ts`:
  - `getDayType()`: maandag / weekdag / weekend.
  - `isAfter5PM()`: 17:00-gate voor avondritueel.
  - Logica (1:1 uit impactreis3):
    - Maandag: weekly-start niet gedaan → verplicht.
    - Werkdag: morning niet gedaan → verplicht.
      → na 17:00 + evening niet gedaan → verplicht.
      → vóór 17:00 + evening niet gedaan → verplicht maar `isAvailable:false`
        (toon herinnering, blokkeer pas na 17:00).
    - Weekend: weekly-review niet gedaan → verplicht.
- Retourneert `{path, title, isRequired, isAvailable, reason}` zodat de frontend weet
  wat te tonen. Gebruikt bestaande `get_today_status()`-data (geen nieuwe queries).

**1.2 Frontend: gate-overlay** (nieuw `frontend/js/ritual-gate.js`, gekoppeld in `home.js`)
- Vóór `renderHome()` bakent `checkRitualGate()` `/api/rituals/next-required` aan.
- Als `isRequired && isAvailable`: toon full-screen overlay (`#ritual-gate`) i.p.v. de
  Control Room, met reden + knop "Start [ritueel]". Het juiste formulier
  (`showMorningForm()` etc. uit tabs-rituals.js) rendert inline in de overlay.
- Na opslaan: gate opnieuw checken; pas bij `null` rendert de Control Room.
- Als `isRequired && !isAvailable` (avond vóór 17:00): toon zachte banner bovenaan,
  geen blokkade.
- Nood-escape: kleine "Sla over (deze sessie)"-link zodat een bug je niet buitensluit.

**1.3 Scheduler: ochtend-check + herinnering** (`backend/scheduler.py`)
- Nieuwe cron-job `ritual_morning_check` om 07:05 (na Iris-briefing):
  - Roept `get_next_required()`; als ochtendritueel nog niet gedaan → log + optioneel
    een mail/herinnering via bestaande `mail`-domain (zelfde patroon als
    `ochtend-herinnering` in impactreis3).
- Idempotent, schrijft naar `scheduler_runs` (bestaand patroon).

**1.4 Test**
- Start ImpactOS, open Control Room vroeg in de ochtend zonder ritueel → overlay verschijnt.
- Vul ochtendritueel in → overlay sluit, Control Room verschijnt.
- Maandag zonder weekstart → weekstart-overlay. Enz.

### FASE 2 — Resterende impactreis3-modules (OPTIONEEL, "volledig")
Alleen zinvol als je óók courses/adhd/habits/dagboek/identity etc. in ImpactOS wilt.
Per module: nieuw `backend/domains/<module>/` (models + service + router) + frontend-tab
+ eventueel Iris-context. Impactreis3-broncode staat klaar als specificatie
(`src/app/<module>/`, `src/lib/<module>.service.ts`, `src/app/api/<module>/`).
Dit is per module een aparte klus; niet nodig voor de "verplichte ritueel"-vraag.

---

## 4. Aanbevolen volgorde
1. **Fase 1.1** backend next-required (klein, ~40 regels).
2. **Fase 1.2** frontend gate-overlay.
3. **Fase 1.3** scheduler-job.
4. **Fase 1.4** testen.
5. Fase 2 alleen na akkoord ("volledig" = ook de niet-rituele modules).

---

## 5. Open vragen voor goedkeuring
- Fase 1 alleen, of ook Fase 2 (de andere modules)?
- Avondritueel: harde blokkade na 17:00 (zoals impactreis3) of alleen een herinnering?
- Herinnering via mail (outlook) of alleen in-dashboard?

# Agent OS — Herstructureringsplan

> **Status:** Plan (nog niet uitvoeren)
> **Doel:** Van een monolithische all-in-one naar een overzichtelijke domeinstructuur met per-project werkruimtes

**Waarom deze herstructurering?**
- Huidige code is 1 groot FastAPI-bestand (main.py) met 18 routers, 1 database, 893 regels HTML, 2869 regels JS
- Alles door elkaar: pipeline, sales, chat, seo, finance, outlook zitten in 1 server
- Nieuwe projecten toevoegen (Bewaardvoorjou, Steentjebij-steentje) kan nu alleen via losse werkruimtes
- Geen herkenbare domeingrenzen — moeilijk om snel te vinden wat je zoekt

**Principes:**
- Behapbare stappen (elke stap = maximaal 15 minuten werk, blijft werkend)
- Geen microservices — 1 server, 1 poort (eenvoud > perfectie)
- Wel duidelijke domeingrenzen in de code
- Per project een map + SKILL.md met merk-context
- Eerst structuur, dan functionaliteit

---

## Fase 0: Inzicht — Wat hebben we nu?

```
D:/APPS/agentos/
├── backend/
│   ├── main.py              ← 1 app, 18 routers, alle domeinen door elkaar
│   ├── database.py          ← 12 tabellen (sessions, messages, tasks, agent_profiles,
│   │                           leads, journeys, delegations, subagents, loops,
│   │                           loop_iterations, sites, opportunities, published_pages,
│   │                           outlook_tokens, outlook_emails)
│   ├── config.py            ← Alle config uit .env (ANTHROPIC, OPENROUTER, OBSIDIAN, etc.)
│   ├── models/schemas.py    ← Pydantic modellen voor ALLE domeinen door elkaar
│   ├── routers/             ← 16 router-bestanden (chat, tasks, leads, demand, loops, ...)
│   ├── services/            ← 20+ services (conveyor, triage, agent, delegate, loop, leads, ...)
│   └── tools/               ← Tools voor de Hermes agent
├── frontend/
│   ├── index.html           ← 893 regels, ALLE views in 1 bestand
│   ├── app.js               ← 2869 regels, alle logica in 1 bestand
│   └── app.css
├── workspaces/
│   ├── projects/
│   │   └── bewaardvoorjou/  ← 1 project met wat output
│   └── delegations/         ← Runtime output van parallelle workers
├── start.ps1                ← Start alles op :1250
├── .env / .env.example
└── requirements.txt
```

Problemen:
1. **Geen domeinscheiding** — `chat.py` importeert uit `finance_prompts`, `tasks.py` importeert uit `triage_service`
2. **Frontend is 1 monolith** — 8 views, 1 bestand. Elke wijziging riskeert alle views
3. **Geen project-context** — Bewaardvoorjou zit in `workspaces/projects/` maar heeft geen SKILL.md, geen eigen start-script, geen herkenbare grens
4. **Geen gedeelde bibliotheek** — database, config, models zitten los; elk service-bestand importeert direct uit `database.py`

---

## Fase 1: Code herstructureren in domeinen

**Wat verandert er?** Alleen de mappenstructuur. De server blijft hetzelfde werken op :1250.

### Stap 1.1 — Backend opsplitsen in domein-packages

Nieuwe structuur:

```
backend/
├── main.py                     ← Kort: importeert alle domein-apps en monteert ze
├── app.py                      ← Lifespan + middleware (was main.py)
│
├── shared/                     ← Gedeeld door alle domeinen
│   ├── __init__.py
│   ├── database.py             ← Was database.py
│   ├── config.py               ← Was config.py
│   ├── models.py               ← Was models/schemas.py
│   └── utils.py                ← _now(), _slugify(), etc.
│
├── domains/
│   ├── chat/                   ← Chat + sessies + Obsidian
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/chat, /api/sessions
│   │   ├── service.py          ← memory_service, hermes_service
│   │   └── system_prompt.py    ← Prompt templates (chat + finance)
│   │
│   ├── pipeline/               ← Content pipeline (conveyor + triage)
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/tasks, /api/triage
│   │   ├── service.py          ← triage_service + conveyor_loop
│   │   ├── conveyor.py         ← conveyor_loop
│   │   └── agents.py           ← Specialist-profielen (keyword/outline/writer/link)
│   │
│   ├── prospecting/            ← B2B leads (scraper, KvK, Hunter)
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/leads
│   │   ├── service.py          ← leads_service + scraper
│   │   ├── kvk.py              ← kvk_service
│   │   ├── hunter.py           ← hunter_service
│   │   └── enrichment.py       ← Verrijkingslogica
│   │
│   ├── seo/                    ← Demand Engine (Google Search Console)
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/demand, /api/sites
│   │   ├── engine.py           ← demand_engine
│   │   ├── gsc.py              ← google_search_console_service
│   │   └── sites.py            ← sites_service
│   │
│   ├── delegate/               ← Parallelle subagent-orchestratie
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/delegate
│   │   ├── service.py          ← delegate_service
│   │   └── event_bus.py        ← event_bus (bleef al apart)
│   │
│   ├── loop/                   ← Loop Engineering (maker/beoordelaar)
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/loops
│   │   └── service.py          ← loop_service
│   │
│   ├── finance/                ← Financiële rapportage
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/finance
│   │   ├── reporter.py         ← finance_reporter
│   │   └── prompts.py          ← finance_prompts
│   │
│   ├── analytics/              ← Google Analytics rapportage
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/analytics
│   │   └── reporter.py         ← analytics_reporter
│   │
│   ├── publish/                ← Netlify content publisher
│   │   ├── __init__.py
│   │   ├── router.py           ← /api/publish
│   │   └── service.py          ← netlify_service
│   │
│   └── outlook/                ← Outlook / Microsoft Graph
│       ├── __init__.py
│       ├── router.py           ← /api/outlook
│       └── service.py          ← outlook_service
│
├── tools/                      ← Hermes agent tools (blijft zoals het is)
│   └── ...
│
└── expert/                     ← Expert-team profielen
    └── team.py                 ← Was services/expert_team.py
```

**Actie:**
1. Maak `backend/shared/` en verplaats `database.py`, `config.py`
2. Maak `backend/domains/` met submappen
3. Verplaats services + routers per domein (geen code-wijzigingen, alleen imports fixen)
4. Herschrijf `main.py` tot `app.py` (alleen lifespan) en maak nieuwe `main.py` die domeinen importeert

**Waarom dit goed is:**
- Elk domein in eigen map: je ziet in 1 oogopslag wat erbij hoort
- Geen cross-domein imports meer (chat kan niet zomaar finance_prompts importeren)
- Nieuwe domeinen toevoegen = map aanmaken + router toevoegen in main.py

---

### Stap 1.2 — Frontend opsplitsen per view

```
frontend/
├── index.html                  ← Alleen lay-out + view-switcher (max 100 regels)
├── app.css                     ← Blijft (of per view)
├── app.js                      ← Alleen init + router
│
├── views/
│   ├── chat.html               ← Chat-interface
│   ├── chat.js
│   ├── tasks.html              ← Kanban-bord
│   ├── tasks.js
│   ├── obsidian.html           ← Obsidian search
│   ├── obsidian.js
│   ├── leads.html              ← Leads dashboard
│   ├── leads.js
│   ├── demand.html             ← Demand Engine
│   ├── demand.js
│   ├── loops.html              ← Loop Engineering
│   ├── loops.js
│   ├── outlook.html            ← Outlook inbox
│   ├── outlook.js
│   ├── mission.html            ← Mission Control
│   └── mission.js
│
└── components/                 ← Gedeelde UI-componenten
    ├── statusbar.js
    ├── kanban.js
    └── modal.js
```

**Actie:**
1. Splits `index.html` op: elke view (`#view-chat`, `#view-tasks`, etc.) naar eigen `.html`
2. Splits `app.js` op per view (chat-logica, leads-logica, etc.)
3. `index.html` laadt views dynamisch met fetch + innerHTML

**Waarom dit goed is:**
- Je kunt 1 view aanpassen zonder de rest te riskeren
- Nieuwe views toevoegen = nieuw bestand
- Elke view heeft eigen JS — geen 2869-regelig monstrum meer

---

### Stap 1.3 — Expert-team loskoppelen

Verplaats `services/expert_team.py` naar `backend/expert/team.py`

Dit team is géén 'service' — het is een vaste configuratie (profiel-data). Hoor niet tussen de runtime-services.

---

## Fase 2: Project-systeem (per klant)

### Stap 2.1 — Projecten-map met SKILL.md per project

```
projects/
├── README.md                   ← Uitleg: "1 map per klant, voeg SKILL.md toe"
│
├── bewaardvoorjou/
│   ├── SKILL.md                ← Merk, tone-of-voice, doelgroep, links
│   ├── .env                    ← Optioneel: eigen API keys voor dit project
│   ├── scripts/
│   │   ├── content.sh          ← Genereer content voor dit project
│   │   └── prospecting.sh      ← Draai lead-scan voor dit project
│   ├── assets/                 ← Logo's, afbeeldingen, voorbeelden
│   ├── content/                ← Gegenereerde blogs/artikelen
│   └── prospecting/            ← Leads + outreach
│
├── steentjebij-steentje/
│   ├── SKILL.md
│   ├── assets/
│   ├── content/
│   └── prospecting/
│
└── sjabloon/                   ← Template voor nieuwe projecten
    ├── SKILL.md
    └── README.md
```

**Actie:**
1. Maak `projects/` aan op root-niveau (naast `backend/`)
2. Verplaats `workspaces/projects/bewaardvoorjou/` naar `projects/bewaardvoorjou/`
3. Maak `projects/sjabloon/SKILL.md` als template
4. Schrijf project-scripts (kleine PowerShell-scripts die de juiste domein-API aanroepen)

### Stap 2.2 — SKILL.md template voor een project

Dit is de kern: elk project heeft een SKILL.md die Hermes leest vóórdat het voor dat project werkt.

```markdown
---
name: bewaardvoorjou
description: "Bewaard voor Jou — keepsake-merk voor 65+-doelgroep"
version: 1.0.0
tags: [keepsake, herinneringen, 65-plus, nalatenschap, zorg]
---

# Bewaard voor Jou

## Merkidentiteit
- **Website:** https://bewaardvoorjou.nl
- **Toon:** Warm, empathisch, respectvol, B1-niveau
- **Doelgroep:** 65-plussers en hun mantelzorgers/kinderen
- **Kernboodschap:** Leg je verhaal vast voor toekomstige generaties

## Content-richtlijnen
- ...
```

**Actie:**
1. Maak template: `projects/sjabloon/SKILL.md`
2. Vul `projects/bewaardvoorjou/SKILL.md` in op basis van bestaande content
3. Maak `projects/steentjebij-steentje/SKILL.md`

### Stap 2.3 — Project-selectie in backend

De backend krijgt een endpoint om project-context te laden:

```
GET  /api/projects              ← Lijst alle projecten
GET  /api/projects/{name}       ← Laad SKILL.md + metadata
POST /api/projects/{name}/pipeline  ← Start pipeline voor dit project
```

**Actie:**
1. Nieuw domein: `backend/domains/projects/`
2. Scanner die `projects/` map uitleest en SKILL.md parsed
3. Chat-agent kan project-context laden via tool

---

## Fase 3: Nieuwe projecten toevoegen (workflow)

Vanaf nu gaat een nieuw project zo:

```
1. mkdir projects/nieuwe-klant/
2. Kopieer projects/sjabloon/SKILL.md
3. Vul SKILL.md in (merk, toon, doelgroep)
4. Hermes laadt SKILL.md bij elke chat voor dit project
5. Klaar — geen code wijzigen
```

Voorbeeld SKILL.md voor Steentjebij-steentje:

```markdown
---
name: steentjebij-steentje
description: "Steentjebij Steentje — vastgoed- en bouwblog"
version: 1.0.0
tags: [vastgoed, bouw, hypotheek, woningmarkt, nederland]
---

# Steentjebij Steentje

## Merkidentiteit
- **Website:** https://steentjebijsteentje.nl
- **Toon:** Zakelijk, deskundig, toegankelijk — voor starters én doorstromers
- **Doelgroep:** Mensen die een huis kopen/verkopen/verbouwen
- **Kernboodschap:** Elke stap in je woonreis, helder uitgelegd

## Content-thema's
- Hypotheek en financiering
- Aankoopproces (keuringen, bieden, notaris)
- Verbouwingen en kosten
- Woningmarkt-analyse
...
```

---

## Fase 4: Eventueel — Opsplitsen in losse servers

Pas doen als 1 server te groot wordt. Tot die tijd is dit overkill.

Mocht het nodig zijn:

```
agentos-pipeline.exe   :1251   ← Alleen pipeline + conveyor
agentos-chat.exe       :1250   ← Hoofd-chat + Obsidian
agentos-prospecting.exe:1252   ← Leads + KvK + Hunter
agentos-seo.exe        :1253   ← Demand Engine
```

Elke server deelt dezelfde `shared/` bibliotheek. Voor nu: **niet doen**.

---

## Samenvatting: wat levert het op?

| Straks | Voordeel |
|---|---|
| `backend/domains/chat/` | Chat-logica staat op 1 plek |
| `backend/domains/pipeline/` | Pipeline los van sales |
| `backend/domains/prospecting/` | Leads zonder chat-rompslomp |
| `projects/bewaardvoorjou/SKILL.md` | Elk project heeft eigen context |
| `projects/steentjebij-steentje/` | Gewoon een map + SKILL.md, klaar |
| `frontend/views/chat.html` | Per view aanpasbaar zonder risico |
| `shared/` | 1 gedeelde laag voor alles |

---

## Volgorde van uitvoeren

```
Fase 1 — Backend structuur
  1.1.1  Maak backend/shared/ en verplaats database.py + config.py + models
  1.1.2  Maak backend/expert/team.py (was services/expert_team.py)
  1.1.3  Maak backend/domains/ met submappen
  1.1.4  Verplaats services en routers naar domeinen (chat, pipeline, prospecting, etc.)
  1.1.5  Herschrijf backend/main.py tot domein-importer
  1.1.6  Test: starten + status checken

Fase 1b — Frontend structuur
  1.2.1  Splits index.html per view
  1.2.2  Splits app.js per view
  1.2.3  Test: elke view laadt correct

Fase 2 — Project-systeem
  2.1.1  Maak projects/ met README.md + sjabloon/
  2.1.2  Verplaats workspaces/projects → projects/
  2.1.3  Maak SKILL.md voor bewaardvoorjou + steentjebij-steentje
  2.2.1  Backend domein: projects/ (API-endpoints)
  2.2.2  Project-scanner (leest SKILL.md uit)
  2.3    Test: nieuw project toevoegen via template

Fase 3 — Opruimen
  3.1   Verwijder lege/oude bestanden (workspaces/ delegations zijn runtime)
  3.2   Update start.ps1 naar nieuwe structuur
  3.3   Update .env.example
```

Elke stap is zo gemaakt dat de server tussendoor blijft werken. Geen 'big bang' herschrijving.

---

## Openstaande vragen

1. **Moeten projecten een eigen database krijgen?** Lijkt me voor nu niet — 1 SQLite met een `project_id` kolom is simpeler.
2. **Moet de frontend per project een eigen URL krijgen?** (bv. localhost:1250/project/bewaardvoorjou) — Kan later, niet nu.
3. **Moet de SKILL.md door Hermes worden ingeladen?** Ja, zodra Hermes het project kent, laadt het automatisch de SKILL.md voor context. Dit is de 'Hermes Breind'-manier.

---

## Ready to start?

Zeg het woord en ik begin met uitvoeren, stap voor stap, in volgorde van de lijst hierboven. Elke stap blijft werkend — je kunt tussendoor testen.

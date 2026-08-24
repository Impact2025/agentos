# Delegate Tool — Subagent Framework

Parallelle multi-agent-laag bovenop Impact OS. De **Lead Agent** (chat) splitst een
grote opdracht op en delegeert onafhankelijke deeltaken aan **workers** die
parallel in de achtergrond draaien. Resultaten stromen asynchroon terug naar de
UI als zelfstandige berichten.

> Dit staat **naast** de bestaande `conveyor_loop` (sequentiële assembly line).
> De conveyor blijft lineaire ketens draaien; Delegate doet parallelle fan-out.
> Ze delen geen state en bijten elkaar dus niet.

---

## Architectuur in één oogopslag

```
Gebruiker ──"Bouw SEO-funnel voor [keyword]"──▶ Lead Agent (chat, /api/chat/stream)
                                                     │
                                                     │ roept tool `delegate` aan
                                                     ▼
                                        delegate_service.spawn_delegation()
                                         • persisteert batch + workers (SQLite)
                                         • asyncio.create_task(_run_batch)
                                         • KEERT DIRECT TERUG  ◀── UI blokkeert niet
                                                     │
                          ┌──────────────────────────┼──────────────────────────┐
                          ▼                          ▼                          ▼
                    Worker 1 (research)        Worker 2 (blog A)          Worker N (blog …)
                    eigen context-box          eigen context-box          eigen context-box
                    eigen profiel/model        eigen profiel/model        eigen profiel/model
                          │                          │                          │
                          └──────────── event_bus.publish(worker_done) ─────────┘
                                                     │
                                            /api/delegate/stream (SSE)
                                                     │
                                                     ▼
                                   UI: elk resultaat = zelfstandige kaart/bubble
```

| Component | Bestand | Rol |
|---|---|---|
| Event bus | `backend/services/event_bus.py` | In-memory pub/sub; pusht resultaten naar de UI |
| Orchestrator | `backend/services/delegate_service.py` | Spawnt batch, draait workers parallel, fouttolerantie, Obsidian-context |
| Tool | `backend/tools/delegate.py` | `delegate` + `delegation_status` (door Lead Agent aangeroepen) |
| API + SSE | `backend/routers/delegate.py` | `/api/delegate*` endpoints incl. live stream |
| Opslag | `backend/database.py` | Tabellen `delegations` + `subagents` |

---

## Stap 1 — Core logica (geleverd als code)

De asynchrone orchestrator zit in `delegate_service.py`. Kernpunten:

- **Non-blocking:** `spawn_delegation()` persisteert synchroon (snel) en start de
  workers via `asyncio.create_task`. De `delegate`-tool keert binnen ms terug.
- **Parallel:** `_run_batch()` draait alle workers via
  `asyncio.gather(*workers, return_exceptions=True)`.
- **Fouttolerant (dubbele laag):** elke worker heeft een eigen `try/except`
  (faalt → status `error`, event `worker_error`), én `gather(return_exceptions=True)`
  vangt onverwachte fouten op. **Eén crashende worker stopt de andere nooit.**
- **Context-isolatie:** elke worker bouwt zijn eigen message-lijst met enkel zijn
  eigen doel + eigen Obsidian-context. Geen output van zusje-workers → geen
  kruisbesmetting.
- **Sterke task-referenties:** lopende tasks staan in `_BG_TASKS` zodat de GC ze
  niet opruimt nadat het chat-request klaar is.

---

## Stap 2 — Agent Profile configuratie

Een worker krijgt zijn "brein" van een **agent-profiel** (`agent_profiles`-tabel):
`model` + `system_prompt`. Templates staan in
`backend/config_templates/worker_profiles.yaml`.

Laad een profiel zo (UI: Dashboard → Agents, of via API):

```bash
curl -X POST http://localhost:1250/api/agents -H "Content-Type: application/json" -d '{
  "name": "SEO Content Writer",
  "model": "openrouter/meta-llama/llama-3.3-70b-instruct",
  "system_prompt": "Je bent een Nederlandstalige SEO-copywriter. ..."
}'
```

Een worker verwijst er vervolgens naar op naam:

```json
{ "role": "Blogpost: zonnepanelen", "goal": "...", "profile": "SEO Content Writer" }
```

Heeft een worker geen profiel? Dan draait hij op het standaard worker-brein
(`DEFAULT_WORKER_PROMPT`) en het backend-default model.

---

## Stap 3 — Prompt injection & context-isolatie

Elke worker-prompt wordt in `delegate_service._run_worker()` opgebouwd uit drie
strak gescheiden lagen:

1. **System prompt** = profiel-brein (rol + grenzen) **+ gedeeld merk-/conversiekader**.
   Dat kader (`_build_brand_brief`) haalt automatisch uit Obsidian:
   merkrichtlijnen, tone-of-voice, conversiedoelen en interne link-targets — plus
   de verplichte `cta`. Zo schrijft het hele team on-brand naar dezelfde doelen.
2. **Doel-specifieke Obsidian-context** = `build_context(goal)`, alleen relevant
   voor déze worker.
3. **User-message** = de overkoepelende opdracht (ter oriëntatie) + het strakke,
   self-contained doel van deze worker. **Geen** output van andere workers.

**Converteerbare hooks (CTA's):** de Lead Agent geeft `cta` mee aan `delegate`;
die belandt in het gedeelde kader van élke worker met de instructie de CTA
*organisch in de tekst te verweven* (zie de `system_prompt` van het
"SEO Content Writer"-profiel). Wil je harde garanties, voeg dan een extra
"Internal Link & Conversion Strategist"-worker toe als laatste schakel.

**Hoe de Lead Agent dit aanstuurt (plain English):** de chat-system-prompt
(`routers/chat.py`) instrueert de Lead Agent om grote opdrachten zelf op te
splitsen in 2-6 workers met een strak doel + verwacht eindproduct, en `delegate`
aan te roepen met een `cta`. De gebruiker hoeft dus alleen normaal Nederlands te
typen ("bouw een SEO-funnel voor zonnepanelen, hook naar onze nieuwsbrief").

---

## Stap 4 — Integratie & UI (vandaag live)

De backend is al volledig bedraad (tool geregistreerd, router gemount, tabellen
auto-aangemaakt bij opstart). Er resten **twee** kleine stappen:

### 4a. Profielen seeden (optioneel maar aanbevolen)

```python
# scripts/seed_worker_profiles.py  — draai: .venv\Scripts\python.exe scripts\seed_worker_profiles.py
import yaml, datetime
from backend.database import init_db, get_conn

init_db()
with open("backend/config_templates/worker_profiles.yaml", encoding="utf-8") as f:
    profiles = yaml.safe_load(f)["profiles"]

now = datetime.datetime.now(datetime.timezone.utc).isoformat()
with get_conn() as conn:
    for p in profiles:
        exists = conn.execute("SELECT 1 FROM agent_profiles WHERE name = ?", (p["name"],)).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO agent_profiles (name, model, system_prompt, created_at) VALUES (?, ?, ?, ?)",
            (p["name"], p["model"], p["system_prompt"], now),
        )
print("Profielen geseed.")
```

### 4b. Frontend: abonneer op de resultaat-stream

Voeg dit toe aan `frontend/index.html` (bijv. onder `appendMessage`). Het opent
één globale SSE-verbinding en rendert elk afgerond worker-resultaat als
zelfstandige bubble met je bestaande `appendMessage(role, content)`:

```javascript
// Live subagent-resultaten — abonneer één keer bij het laden van het dashboard.
function connectDelegateStream() {
  const es = new EventSource('/api/delegate/stream');
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    switch (ev.type) {
      case 'delegation_start':
        appendMessage('assistant',
          `🧑‍✈️ **Lead Agent** delegeert *${ev.objective}* aan ${ev.worker_count} workers: ${ev.roles.join(', ')}.`);
        break;
      case 'worker_done':
        appendMessage('assistant',
          `### ✅ ${ev.role}\n\n${ev.content}\n\n*(klaar in ${(ev.duration_ms/1000).toFixed(1)}s)*`);
        break;
      case 'worker_error':
        appendMessage('assistant', `### ❌ ${ev.role}\n\n${ev.content}`);
        break;
      case 'delegation_done':
        appendMessage('assistant',
          `🏁 Delegatie afgerond — ${ev.done} klaar, ${ev.failed} mislukt (status: ${ev.status}).`);
        break;
    }
  };
  es.onerror = () => { es.close(); setTimeout(connectDelegateStream, 3000); }; // auto-reconnect
}
connectDelegateStream();
```

### 4c. Testen

```bash
# Backend draait al op :1250. Start een delegatie zonder de chat (handmatig):
curl -X POST http://localhost:1250/api/delegate -H "Content-Type: application/json" -d '{
  "objective": "SEO-funnel voor zonnepanelen",
  "cta": "Schrijf je in voor onze nieuwsbrief",
  "workers": [
    {"role": "Keyword Researcher", "goal": "Lever 15 long-tail zoekwoorden rond zonnepanelen met zoekintentie.", "profile": "Keyword & Market Researcher"},
    {"role": "Blogpost: opbrengst", "goal": "Schrijf een blogpost over de opbrengst van zonnepanelen.", "profile": "SEO Content Writer"},
    {"role": "Blogpost: subsidie", "goal": "Schrijf een blogpost over subsidies voor zonnepanelen.", "profile": "SEO Content Writer"}
  ]
}'

# Volg de resultaten live:
curl -N http://localhost:1250/api/delegate/stream
```

Of gewoon in de chat: *"Bouw een complete SEO-funnel voor zonnepanelen met 3
blogposts, hook naar onze nieuwsbrief."* — de Lead Agent roept `delegate` zelf aan.

---

## API-referentie

| Methode | Endpoint | Doel |
|---|---|---|
| `POST` | `/api/delegate` | Start een delegatie (expliciete workerlijst) |
| `GET`  | `/api/delegate` | Lijst recente batches |
| `GET`  | `/api/delegate/stream` | SSE — live worker-events naar de UI |
| `GET`  | `/api/delegate/{id}` | Eén batch + alle workers (incl. resultaten) |

Events op de stream: `delegation_start`, `worker_start`, `worker_progress`
(tool-gebruik), `worker_done`, `worker_error`, `delegation_done`.

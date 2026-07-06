# Agent OS

Lokaal AI-dashboard (FastAPI + vanilla-JS SPA) op `http://localhost:1250`.
Start: `agentos_service.cmd` (uvicorn `backend.main:app`), of `launch.ps1` (incl. Hermes-gateway).

## Architectuur

- `backend/domains/<domein>/` — één package per domein (router + service). Routers worden in `backend/main.py` gemonteerd.
- `backend/shared/` — database (SQLite, `data/agentos.db`, WAL), config (.env), `agent_runner.py` (Hermes agentic loop), `outcomes.py` (uitkomst-kaarten).
- `backend/tools/` — echte tools voor de agentic loop (websearch/Tavily, Google Analytics, Obsidian, market data).
- `backend/scheduler.py` — APScheduler-jobs (content di/vr 09:00, vacatures ma/do 07:00, radar elke 4u, finance/GA-rapporten, autoheal elke 15min).
- `frontend/app.js` — één SPA-bestand; `index.html` laadt hem. Geen build-stap (Tailwind-css is voorgegenereerd).
- Database-migraties: idempotent in `backend/shared/database.py:_migrate` (ALTER TABLE per kolom).

## Kernflows

1. **Actiecentrum** (`backend/domains/action_center/`): `GET /api/action-center` = de inbox met alles wat op een mens wacht (draft/ready/failed goals, Wachtrij-reviews, publicatiefouten met retry, hoge-fit vacatures, nieuwe leads, scheduler-fouten). De Control Room (home) toont dit bovenaan. Weggeklikte items staan in `inbox_dismissals`.
2. **Uitkomst-kaarten** (`backend/shared/outcomes.py:log_outcome`): elke agent-run logt naar `activity_log` met `artifact` (URL/vault-pad), `next_step` (wat Vincent moet doen) en `status` (`ok`/`error`). `status='error'` verschijnt automatisch in het Actiecentrum. Nieuwe agent-flows MOETEN dit gebruiken.
3. **Goals** (`backend/domains/goal/`): plan (LLM-decompositie) → confirm → start → `_execution_loop` voert taken uit via `_route_by_skill`. Synthese loopt via Claude (`GOAL_USE_CLAUDE=1`, direct of via OpenRouter-terugval), Hermes als vangnet; research/analyse/SEO op Hermes krijgt `use_tools=True`. Retourneert `(result, artifact, next_step)`.
4. **Strategist** (`backend/domains/strategist/`): analyse → execute maakt goals aan en start ze direct (`STRATEGIST_AUTOSTART=1`). Veilig: de goal-executor publiceert/verstuurt zelf niets — dat blijft achter de Wachtrij-gate.
5. **Wachtrij / review-gate** (`backend/domains/content_queue/` + `publish/content_pipeline.py`): auto-content wordt `pending_review`; alleen menselijke goedkeuring publiceert (website + social + zoekmachine-ping). Dit is de enige plek waar iets extern live gaat.

## Regels

- Publiceer/verstuur NOOIT automatisch buiten de Wachtrij-gate om.
- Fouten die menselijke actie vereisen: log met `status='error'` via `log_outcome` — niet alleen `logger.warning`.
- Elke taak/run die "klaar" claimt hoort een artefact-link te hebben (vault-pad of URL).
- Secrets staan in `.env` (gitignored). `google-credentials.json` idem.
- Python: `.venv/Scripts/python.exe`. Tests: `.venv/Scripts/python.exe -m pytest tests/ -q` (draait tegen een wegwerp-DB via `AGENTOS_DB_PATH`). Draai ze vóór elke server-herstart; verifieer daarna tegen de draaiende server (herstart via `agentos_service.cmd`).
- Het ochtendrapport (`backend/domains/action_center/digest.py`) draait dagelijks 07:00 en mailt zodra SMTP in `.env` is ingesteld; on demand via `GET /api/action-center/digest`.

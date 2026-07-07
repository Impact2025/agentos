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
6. **Kwaliteitsgate** (`CONTENT_MIN_SCORE`, default 80): schrijven/review/optimalisatie loopt via `_llm` (Claude → Hermes-terugval). `review_and_improve` verbetert max 3 rondes; blijft de score < 80 dan wordt de job `needs_work` (Actiecentrum: "Verbeter met AI"/"Wijs af") en weigert `approve_and_publish` hard. Goal-publisher (`_stage_to_wachtrij`) staget alleen ≥ 80 — interne rapporten/plannen halen dat nooit en blijven concept. Onleesbare review = score 0, nooit stille 50.
7. **SEO-systeem (Goldie-pipeline)**: Demand Engine (`seo/engine.py`, GSC striking-distance) kiest zoekwoorden → kennisbank (`seo/knowledge.py`: `sites.profile`/`ctas` + `case_studies`-tabel, API `/api/knowledge`) matcht per artikel een casestudy → meertraps-generator (`publish/article_writer.py`: outline → secties → opmaak → gevalideerde interne/externe links → QC op AI-taal/CTA/keyword; rapport in `content_jobs.qc_report`, terugval op single-shot bij falen) → Wachtrij → bij goedkeuring publiceren + sitemap.xml + GSC-submit + IndexNow (`sites.indexnow_key`, keyfile mee in de Netlify-build) + optioneel Google Indexing API (`GOOGLE_INDEXING_ENABLED`). Batch per site via `sites.content_batch_size` (di/vr-run en `run-now?count=`).

## Regels

- Publiceer/verstuur NOOIT automatisch buiten de Wachtrij-gate om.
- Fouten die menselijke actie vereisen: log met `status='error'` via `log_outcome` — niet alleen `logger.warning`.
- Elke taak/run die "klaar" claimt hoort een artefact-link te hebben (vault-pad of URL).
- Secrets staan in `.env` (gitignored). `google-credentials.json` idem.
- Python: `.venv/Scripts/python.exe`. Tests: `.venv/Scripts/python.exe -m pytest tests/ -q` (draait tegen een wegwerp-DB via `AGENTOS_DB_PATH`). Draai ze vóór elke server-herstart; verifieer daarna tegen de draaiende server (herstart via `agentos_service.cmd`).
- Het ochtendrapport (`backend/domains/action_center/digest.py`) draait dagelijks 07:00 en mailt zodra SMTP in `.env` is ingesteld; on demand via `GET /api/action-center/digest`.

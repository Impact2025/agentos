# Agent OS

Lokaal AI-mission-control voor Vincents projecten (WeAreImpact, Bijeen, Bewaard voor Altijd, …):
agents doen het werk — content, SEO, prospecting, vacature-scans, trendradar — en **het
Actiecentrum** vertelt je wat er is gebeurd, waar de resultaten staan en wat er op jóu wacht.

## Starten

```powershell
# Volledig (incl. Hermes-gateway + browser):
.\launch.ps1

# Alleen de server (achtergrond, logt naar agentos.log):
.\agentos_service.cmd
```

Dashboard: **http://localhost:1250** — de Control Room opent met de inbox
("Vandaag — wacht op jou"), het inklapbare Ochtendrapport en de live uitkomst-feed.

## De drie beloftes

1. **Wat moet ik doen?** → het Actiecentrum bovenaan de Control Room. Elk item heeft
   één-klik-acties (bevestig/start/publiceer/wijs af); bulk-knoppen bij 3+ wachtende doelen.
   De browsertab toont `(N) Agent OS` zolang er iets wacht.
2. **Waar staan de resultaten?** → elke agent-run eindigt met een uitkomst-kaart:
   wat gedaan → artefact-link (vault-pad of live-URL) → volgende stap. Zichtbaar in de
   feed en in het dagelijkse Ochtendrapport (07:00; mailt zodra SMTP in `.env` staat).
3. **Wat ging er mis?** → fouten worden inbox-items met een retry-knop, en verdwijnen
   automatisch zodra een latere run ze oplost.

## Veiligheid

Agents publiceren of versturen **nooit** zelfstandig. Alles wat extern live gaat
(website, social, mail) passeert de menselijke review-gate in de **Wachtrij**-tab.

## Ontwikkelen

- Architectuur en regels voor agent-sessies: zie **CLAUDE.md**.
- Tests: `.venv\Scripts\python.exe -m pytest tests\ -q` (wegwerp-DB, raakt je data niet).
- Frontend: `frontend/js/*.js` — klassieke scripts met gedeelde globale scope,
  laadvolgorde in `index.html` (core eerst, INIT laatst). Geen build-stap.
- Backend: FastAPI, domain-driven (`backend/domains/<domein>/router.py + service.py`),
  SQLite (`data/agentos.db`), APScheduler-jobs in `backend/scheduler.py`.
- Config: `.env` (zie `.env.example`); schakelaars: `GOAL_USE_CLAUDE`,
  `STRATEGIST_AUTOSTART`, `AGENTOS_DB_PATH` (tests).

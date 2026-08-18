# AgentOS GEO-module — herstart & verificatie recept

## Wat is gebouwd
- `backend/domains/geo/__init__.py` — domein-package
- `backend/domains/geo/service.py` — deterministische GEO-scan (5 pijlers), persona-CRUD, entity-block-generator
- `backend/domains/geo/router.py` — API: /api/geo/scan|latest|personas|summary|entity-block
- `backend/main.py` — geo-domein gemount (achter `domain_enabled("geo")`, standaard aan)
- `backend/domains/iris/metrics.py` — GEO-pijler toegevoegd aan elke project-briefing (non-breaking)
- `frontend/js/core.js` — GEO-tab toegevoegd aan TABS + TAB_ICONS
- `frontend/js/shell.js` — dispatch naar renderGeoTab
- `frontend/js/tabs-geo.js` — GEO-dashboard (scores, pijlers, persona-beheer, entity-block)
- `frontend/index.html` — <script> tab-geo.js toegevoegd
- `agent_profiles` rij "GEO Specialist" (id 15) — expert-agent onder Agenten-tab

## 1) Herstart (vereist — laadt nieuwe code + endpoints)
```powershell
cd D:/APPS/agentos
$pid = (netstat -ano | Select-String ":1250.*LISTEN" | Select-Object -First 1) -split '\s+' | Select-Object -Last 1
taskkill /PID $pid /F
Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "backend\main.py" -NoNewWindow
# of via uvicorn:
# uvicorn backend.main:app --host 127.0.0.1 --port 1250
```

## 2) Verificatie (live curl tegen :1250)
```bash
# health
curl -s -m3 http://localhost:1250/api/health

# login (lees AGENTOS_PASSWORD uit .env)
curl -X POST localhost:1250/api/auth/login -d '{"username":"vincent","password":"<pwd>"}' -c /tmp/c.txt

# GEO summary (alle sites)
curl -b /tmp/c.txt http://localhost:1250/api/geo/summary

# scan één site (id uit summary)
curl -b /tmp/c.txt http://localhost:1250/api/geo/scan/<site_id>

# entity-block
curl -b /tmp/c.txt -X POST http://localhost:1250/api/geo/entity-block \
  -H 'Content-Type: application/json' \
  -d '{"site_name":"Pootgelukkig","what_it_is":"een adoptieplatform voor asieldieren","what_it_is_not":["dierenwinkel","cattery"]}'
```

## 3) Frontend
- Hard-refresh browser (Ctrl+Shift+R) op localhost:1250
- Nieuwe tab "GEO" verschijnt naast "Optimalisatie"
- Klik een site-kaart → scan, persona-beheer, entity-block-generator

## Bekend/volgende stap
- Structured/DirectAnswer-score is nu een proxy (bestaat gepubliceerd werk mét JSON-LD-infra).
  Een fetch-per-pagina-scan kan dit aanscherpen tot echte per-pagina-verificatie.
- Iris auto-suggestie koppelt GEO nog niet automatisch aan de GEO Specialist-agent;
  dat is een kleine uitbreiding in agentctl/service.py (laagste pijler → agent).

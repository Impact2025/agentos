# Impact OS — hosten voor mobiel (altijd aan)

## Wat is er gedaan
- **Login-gate (server-side)** toegevoegd in `backend/domains/auth/`:
  - `service.py` — HMAC-sessiecookie (geen DB nodig, veilig, rotatie-proof).
  - `router.py` — `/api/auth/login`, `/api/auth/logout`, `/api/auth/me` (altijd open).
  - `main.py` — `auth_guard`-middleware beschermt **alle** routes behalve
    `/api/auth/*`, `/api/status` (health) en de statische frontend-assets.
    Dus ook de gevaarlijke routes (mail versturen, publiceren, outreach) zitten
    achter de login.
  - Gate staat **automatisch UIT** zolang `IMPACTOS_PASSWORD` niet is gezet
    (lokale dev blijft zonder slot). Bij deploy verplicht.
- **Frontend login-scherm** (`frontend/js/auth.js`) + bootstrap-gate in
  `tabs-settings-chat.js` (`checkAuthAndStart()`) + uitlog-knop in de sidebar.
- **Dockerfile**, **.dockerignore**, **fly.toml** klaargezet voor Fly.io.

## Lokale test van de gate (zónder je echte server te raken)
```
cd D:/apps/impactos
set IMPACTOS_PASSWORD=jouwtestwachtwoord
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 1299
# dan: http://127.0.0.1:1299  → login-scherm zichtbaar
```

## Deploy naar Fly.io (altijd aan, HTTPS, mobiel bereikbaar)
1. Installeer Fly CLI: https://fly.io/docs/hands-on/installing/
2. `fly auth login`
3. `fly launch --no-deploy`  (app-naam + regio; neem `ams` voor NL-latency)
4. Volume maken: `fly volumes create agentos_data --region ams --size 1`
5. Secrets zetten (NOOIT in fly.toml / git):
   ```
   fly secrets set IMPACTOS_PASSWORD="<jouw-sterk-wachtwoord>"
   fly secrets set IMPACTOS_SECURE_COOKIE="1"
   fly secrets set ANTHROPIC_API_KEY="sk-..."            # uit je .env
   fly secrets set OBSIDIAN_VAULT_PATH="/app/data"       # of leeg
   # + de overige keys uit .env.example die je nodig hebt (SMTP, Outlook, etc.)
   ```
6. `fly deploy`
7. App draait op `https://<app>.fly.dev` — open op je telefoon, log in, klaar.

## Belangrijk
- Zet `IMPACTOS_PASSWORD` in je **echte** dev-omgeving NIET, tenzij je ook lokaal
  met wachtwoord wilt werken. De 1250-server blijft zoals nu open.
- Mobiel = ga naar de Fly-URL, log in met `IMPACTOS_PASSWORD`. Alles werkt
  precies zoals op localhost, ook als je laptop uit staat.
- De Obsidian-vault sync je niet naar de cloud-server; zet `OBSIDIAN_VAULT_PATH`
  leeg bij deploy (of mount je vault via een aparte sync). Impact OS draait ook
  zonder vault — alleen de Obsidian-gerelateerde acties vallen dan weg.

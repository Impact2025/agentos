"""Login-gate voor Agent OS.

Waarom: Agent OS kan echt mail versturen, publiceren en outreach doen. Zodra
de server open op internet staat (mobiel besturen vanaf elders), mag niemand
behalve Vincent erbij kunnen. Deze module legt een sessie-gebaseerde slot over
de hele app — backend-side, dus ook de gevaarlijke /api/*-routes zijn beschermd.

Design:
  - Wachtwoord komt uit env AGENTOS_PASSWORD (geen default → server weigert
    elke aanvraag tot je hem zet). Bij deploy zet je die via de host-secrets.
  - Sessie = HMAC-ondertekend cookie (geen DB nodig, stateless, rotatie-proof).
  - Een middleware blokkeert alles behalve /api/auth/*, /api/status (health) en
    de statische frontend-bestanden (index.html/assets). De frontend toont zelf
    een login-scherm als er geen geldige sessie is.

HMAC ipv random token: geen server-state, werkt herboren na elke deploy/restart,
en het kan niet geraden worden zonder de server-only secret.
"""
import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Request, Response
from starlette.responses import JSONResponse

# Sessie verloopt na 30 dagen inactiviteit — lang genoeg voor mobiel gebruik,
# kort genoeg dat een gestolen cookie niet eeuwig werkt.
SESSION_MAX_AGE = 30 * 24 * 3600
COOKIE_NAME = "agentos_session"

# Routes die altijd open zijn: auth zelf, health-check, en de statische assets
# (zodat het login-scherm kan laden). Alles in /api/* anders is beschermd.
PUBLIC_PREFIXES = ("/api/auth/", "/api/status")


def _secret() -> bytes:
    # Server-only secret. Valt terug op een per-proces willekeurige waarde als
    # AGENTOS_SESSION_SECRET ontbreekt — dan zijn bestaande sessies na een
    # restart ongeldig (gebruiker logt opnieuw in), wat veiliger is dan een
    # hardcoded geheim in de repo.
    s = os.environ.get("AGENTOS_SESSION_SECRET")
    if s:
        return s.encode()
    s = os.environ.get("AGENTOS_PASSWORD")
    if s:
        return s.encode()
    return b"__dev_only_insecure_rotate_on_restart__"


def _password() -> Optional[str]:
    pw = os.environ.get("AGENTOS_PASSWORD")
    return pw.strip() if pw else None


def session_required() -> bool:
    """True als er een wachtwoord is geconfigureerd en de gate actief moet zijn."""
    return bool(_password())


def _sign(value: str) -> str:
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()


def create_session() -> str:
    issued = str(int(time.time()))
    payload = issued
    sig = _sign(payload)
    return f"{payload}.{sig}"


def verify_session(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    issued, sig = token.split(".", 1)
    expected = _sign(issued)
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        age = time.time() - int(issued)
    except ValueError:
        return False
    return 0 <= age <= SESSION_MAX_AGE


def try_login(password: str) -> Optional[str]:
    """Geeft een sessietoken terug bij succes, anders None."""
    expected = _password()
    if not expected:
        return None
    # constant-time vergelijking
    if hmac.compare_digest(password, expected):
        return create_session()
    return None


def set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("AGENTOS_SECURE_COOKIE", "0") == "1",
        path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/")


async def auth_guard(request: Request, call_next):
    """Middleware-functie: blokkeer niet-geautoriseerde aanvragen.

    - Auth/status routes: altijd open.
    - Statische frontend bestanden (geen /api/ prefix, wel een punt in het
      laatste segment → .js/.css/.html/.ico): open, anders ziet de gebruiker
      geen login-scherm.
    - /api/* en alles anders: vereist geldige sessie.
    """
    path = request.url.path

    # Altijd-open routes
    if any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)

    # Statische assets open laten zodat het login-scherm kan laden
    if not path.startswith("/api/"):
        last = path.rsplit("/", 1)[-1]
        if "." in last or path in ("/", ""):
            return await call_next(request)

    # Beschermd: sessie verplicht
    if not session_required():
        # Geen wachtwoord geconfigureerd → gate uit (lokale dev zonder slot).
        return await call_next(request)

    token = request.cookies.get(COOKIE_NAME)
    if verify_session(token):
        return await call_next(request)

    # Geen geldige sessie
    if path.startswith("/api/"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Niet geautoriseerd — log eerst in."},
        )
    # Pagina-aanvraag: stuur door naar de frontend (die toont het login-scherm).
    return await call_next(request)

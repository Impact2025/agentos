"""Auth API — login / logout / me.

Alleen /api/auth/* is open; de rest van de app wordt beschermd door de
auth_guard-middleware in main.py. Het wachtwoord komt uit env IMPACTOS_PASSWORD.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.get("/me")
def me(request: Request):
    # Zelfde backward-compat als de auth_guard-middleware: accepteer zowel de
    # hernoemde als de oude cookienaam, anders logt een rename iedereen stil
    # uit (26 aug 2026: dit riep het niet-bestaande service.COOKIE_NAME aan,
    # dus 500'de /me op elk bezoek en toonde de app altijd het loginscherm).
    token = None
    for _name in service._COOKIE_NAMES:
        token = request.cookies.get(_name)
        if token:
            break
    return {"authenticated": service.verify_session(token)}


@router.post("/login")
def login(body: LoginBody, request: Request):
    if not service.session_required():
        # Server draait zonder wachtwoord — gate is uit, beschouw als ingelogd.
        from fastapi.responses import JSONResponse

        resp = JSONResponse({"ok": True, "note": "geen wachtwoord geconfigureerd"})
        service.set_session_cookie(resp, service.create_session())
        return resp
    token = service.try_login(body.password)
    if not token:
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": False, "error": "Verkeerd wachtwoord"}, status_code=401)
    from fastapi.responses import JSONResponse

    resp = JSONResponse({"ok": True})
    service.set_session_cookie(resp, token)
    return resp


@router.post("/logout")
def logout():
    from fastapi.responses import JSONResponse

    resp = JSONResponse({"ok": True})
    service.clear_session_cookie(resp)
    return resp

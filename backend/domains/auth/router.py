"""Auth API — login / logout / me.

Alleen /api/auth/* is open; de rest van de app wordt beschermd door de
auth_guard-middleware in main.py. Het wachtwoord komt uit env AGENTOS_PASSWORD.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.get("/me")
def me(request: Request):
    token = request.cookies.get(service.COOKIE_NAME)
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

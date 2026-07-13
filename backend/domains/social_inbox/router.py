"""
Social Inbox API — beheer van per-project social inboxes en de review-gate
voor concept-antwoorden. Gespiegeld aan domains/mail/router.py.

  GET  /api/social-inbox/inboxes            → alle inboxes (filter project/platform)
  POST /api/social-inbox/inboxes            → nieuwe inbox aanmaken
  PUT  /api/social-inbox/inboxes/{id}       → inbox bijwerken (creds, enabled, ...)
  DELETE /api/social-inbox/inboxes/{id}     → inbox verwijderen
  POST /api/social-inbox/inboxes/{id}/poll  → handmatig ophalen nu
  GET  /api/social-inbox/{project}/pending  → concepten wachtend op goedkeuring
  GET  /api/social-inbox/msg/{id}           → één bericht + concept
  POST /api/social-inbox/msg/{id}/approve   → plaats het goedgekeurde antwoord
  POST /api/social-inbox/msg/{id}/reject    → afwijzen
  POST /api/social-inbox/msg/{id}/edit      → concept bewerken
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ...shared import social_inbox as svc
from ...shared.database import get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social-inbox", tags=["social-inbox"])


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


@router.get("/inboxes")
def list_inboxes(project: Optional[str] = Query(None), platform: Optional[str] = Query(None)):
    prj = (project or "").strip()
    plat = (platform or "").strip()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,project,platform,label,brand_context,poll_minutes,enabled,created_at "
            "FROM social_inboxes ORDER BY project, platform"
        ).fetchall()
    boxes = [dict(r) for r in rows]
    if prj:
        boxes = [b for b in boxes if _norm(b["project"]) == _norm(prj)]
    if plat:
        boxes = [b for b in boxes if b["platform"] == plat]
    # Tel per inbox hoeveel concepten wachten
    with get_conn() as conn:
        for b in boxes:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM social_inbox_msg "
                "WHERE inbox_id=? AND status='pending_review'",
                (b["id"],),
            ).fetchone()[0]
            b["pending"] = cnt
    return boxes


@router.post("/inboxes")
def create_inbox(body: dict):
    project = (body.get("project") or "").strip()
    platform = (body.get("platform") or "").strip().lower()
    if not project:
        raise HTTPException(400, "project is verplicht")
    if platform not in svc.PLATFORMS:
        raise HTTPException(400, f"platform moet een van {svc.PLATFORMS} zijn")
    iid = body.get("id") or f"si_{uuid.uuid4().hex[:12]}"
    creds = body.get("creds_json") or {}
    if isinstance(creds, dict):
        creds = json.dumps(creds)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO social_inboxes(id,project,platform,label,creds_json,"
            "brand_context,poll_minutes,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (iid, project, platform, body.get("label", ""), creds,
             body.get("brand_context", project), int(body.get("poll_minutes", 30)),
             int(body.get("enabled", 1)), datetime.now().isoformat()),
        )
    return {"success": True, "id": iid}


@router.put("/inboxes/{inbox_id}")
def update_inbox(inbox_id: str, body: dict):
    allowed = ("label", "creds_json", "brand_context", "poll_minutes", "enabled")
    updates, params = [], []
    for f in allowed:
        if f not in body or body[f] is None:
            continue
        val = body[f]
        if f == "creds_json" and isinstance(val, dict):
            val = json.dumps(val)
        updates.append(f"{f}=?")
        params.append(val)
    if not updates:
        return {"success": False, "detail": "Niets om bij te werken"}
    params.append(inbox_id)
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE social_inboxes SET {', '.join(updates)} WHERE id=?", params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Inbox niet gevonden")
    return {"success": True}


@router.delete("/inboxes/{inbox_id}")
def delete_inbox(inbox_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM social_inboxes WHERE id=?", (inbox_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Inbox niet gevonden")
    return {"success": True}


@router.post("/inboxes/{inbox_id}/poll")
async def poll_inbox(inbox_id: str):
    try:
        n = await svc.run_inbox(inbox_id)
        return {"success": True, "fetched": n}
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@router.get("/{project}/pending")
def pending(project: str):
    prj = (project or "").strip()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.id, m.inbox_id, m.platform, m.author_name, m.author_handle, "
            "m.text, m.kind, m.parent_url, m.draft_body, m.status, m.manual, "
            "m.created_at, i.project, i.label "
            "FROM social_inbox_msg m JOIN social_inboxes i ON i.id=m.inbox_id "
            "WHERE m.status IN ('pending_review','edited') "
            "ORDER BY m.created_at DESC"
        ).fetchall()
    msgs = [dict(r) for r in rows]
    if prj:
        msgs = [m for m in msgs if _norm(m["project"]) == _norm(prj)]
    return msgs


@router.get("/msg/{msg_id}")
def get_msg(msg_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.*, i.project, i.label, i.platform FROM social_inbox_msg m "
            "JOIN social_inboxes i ON i.id=m.inbox_id WHERE m.id=?",
            (msg_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Bericht niet gevonden")
    return dict(row)


@router.post("/msg/{msg_id}/approve")
async def approve_msg(msg_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.*, i.creds_json, i.project FROM social_inbox_msg m "
            "JOIN social_inboxes i ON i.id=m.inbox_id WHERE m.id=?",
            (msg_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Bericht niet gevonden")
        m = dict(row)
        if m["status"] == "sent":
            return {"success": True, "detail": "Al verzonden"}
        text = m.get("edited_body") or m.get("draft_body") or ""
        if not text.strip():
            raise HTTPException(400, "Geen antwoordtekst om te plaatsen")
        inbox = {"project": m["project"], "platform": m["platform"],
                 "creds_json": m["creds_json"]}
        result = await svc.post_reply(inbox, m, text)
        if result.get("manual"):
            # Kanaal staat geen API-antwoord toe (LinkedIn/TikTok): markeer als
            # 'manual' zodat de UI een plak-knop toont i.p.v. een verzend-fout.
            conn.execute(
                "UPDATE social_inbox_msg SET status='sent', manual=1, "
                "sent_at=datetime('now') WHERE id=?", (msg_id,)
            )
            return {"success": True, "manual": True,
                    "detail": "Geen API-antwoord mogelijk op dit kanaal — "
                              "kopieer het antwoord en plaats het handmatig."}
        if result.get("success"):
            conn.execute(
                "UPDATE social_inbox_msg SET status='sent', sent_at=datetime('now') WHERE id=?",
                (msg_id,),
            )
            return {"success": True, "url": result.get("url", "")}
        raise HTTPException(400, result.get("error", "Onbekende fout"))


@router.post("/msg/{msg_id}/reject")
def reject_msg(msg_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE social_inbox_msg SET status='rejected' WHERE id=?", (msg_id,)
        )
    return {"success": True}


@router.post("/msg/{msg_id}/edit")
def edit_msg(msg_id: int, body: dict):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Geen tekst om op te slaan")
    with get_conn() as conn:
        conn.execute(
            "UPDATE social_inbox_msg SET draft_body=?, edited_body=?, status='edited' WHERE id=?",
            (text, text, msg_id),
        )
    return {"success": True}

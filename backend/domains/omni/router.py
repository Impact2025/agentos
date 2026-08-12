"""SERP-Omni router — reverse-engineer de SERP en zet platform-assets klaar.

Endpoints (allemaal achter de agentos_session login-gate):
  GET  /api/omni/status            — module-status + serp-engine gereed
  POST /api/omni/analyze           — analyseer 1 keyword, genereer assets, zet in omni_queue
  GET  /api/omni/queue?site_id=    — staged assets
  POST /api/omni/queue/{id}/approve  — keur goed (markeer 'approved')
  POST /api/omni/queue/{id}/reject   — wijs af
  POST /api/omni/queue/{id}/publish  — publiceer naar het platform achter de gate
  POST /api/omni/batch             — analyseer meerdere keywords (body: {site_id, keywords[], angle})

Veiligheidsmodel (wereldklasse = geen ruis, geen spam):
  - Generatie is deterministisch + FEITEN_GRONDWET-afgedwongen (geen verzinsels).
  - NOTHING post automatisch. Alles 'staged' -> Vincent keurt goed.
  - 'publish' post alleen naar platforms mét een geconfigureerde service
    (LinkedIn/X). Reddit nooit auto-post (ban-risico) -> blijft 'approved',
    Vincent plakt het concept handmatig in Reddit.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...shared.database import get_conn
from ..publish.article_writer import _llm  # forceer import-contract
from .generator import generate_for_keyword, generate_asset
from .serp import analyze_serp, reset_serp_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/omni", tags=["omni"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _site_row(site_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        return dict(conn.execute(
            "SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone() or {})


def _owned_domains(site: Dict) -> List[str]:
    dom = (site.get("base_url") or "").lower().replace("https://", "").replace("http://", "")
    dom = dom.rstrip("/")
    return [dom] if dom else []


class AnalyzeBody(BaseModel):
    site_id: str
    keyword: str
    angle: str = ""


class BatchBody(BaseModel):
    site_id: str
    keywords: List[str] = []
    angle: str = ""


@router.get("/status")
def omni_status():
    return {"module": "serp-omni", "ready": True,
            "note": "reverse-engineert SERP per keyword, staged review-gate"}


def _queue_row(row) -> Dict:
    d = dict(row)
    try:
        d["serp_profile"] = json.loads(d.get("serp_profile") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    return d


@router.post("/analyze")
async def analyze_keyword(body: AnalyzeBody):
    site = _site_row(body.site_id)
    if not site:
        raise HTTPException(404, detail="site niet gevonden")
    result = await generate_for_keyword(body.keyword, site, body.angle,
                                   owned_domains=_owned_domains(site))
    # Schrijf assets naar omni_queue (staged).
    queued = []
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        for a in result.get("assets", []):
            qid = f"omni_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO omni_queue (id, site_id, keyword, asset_type, platform, "
                "title, body, serp_profile, angle, status, score, note, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (qid, body.site_id, body.keyword, a["asset_type"], a["platform"],
                 a.get("title", ""), a.get("body", ""),
                 json.dumps(result.get("serp", {})), body.angle, a.get("status", "staged"),
                 a.get("score", 0), a.get("note", ""),
                 _now_iso(), _now_iso()))
            queued.append(qid)
    return {"keyword": body.keyword, "serp": result.get("serp"),
            "queued": queued, "assets": result.get("assets")}


@router.post("/batch")
async def analyze_batch(body: BatchBody):
    site = _site_row(body.site_id)
    if not site:
        raise HTTPException(404, detail="site niet gevonden")
    if not body.keywords:
        raise HTTPException(400, detail="geen keywords opgegeven")
    if len(body.keywords) > 25:
        raise HTTPException(400, detail="max 25 keywords per batch")
    total = 0
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        for kw in body.keywords:
            kw = kw.strip()
            if not kw:
                continue
            res = await generate_for_keyword(kw, site, body.angle,
                                       owned_domains=_owned_domains(site))
            for a in res.get("assets", []):
                qid = f"omni_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO omni_queue (id, site_id, keyword, asset_type, "
                    "platform, title, body, serp_profile, angle, status, score, "
                    "note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (qid, body.site_id, kw, a["asset_type"], a["platform"],
                     a.get("title", ""), a.get("body", ""),
                     json.dumps(res.get("serp", {})), body.angle,
                     a.get("status", "staged"), a.get("score", 0), a.get("note", ""),
                     _now_iso(), _now_iso()))
                total += 1
    return {"queued": total, "keywords": len([k for k in body.keywords if k.strip()])}


@router.get("/queue")
def omni_queue(site_id: str = Query(""), status: str = Query("")):
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        sql = "SELECT * FROM omni_queue WHERE 1=1"
        params: List[str] = []
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
    return [_queue_row(r) for r in rows]


@router.post("/queue/{qid}/approve")
async def approve_asset(qid: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM omni_queue WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, detail="asset niet gevonden")
        conn.execute(
            "UPDATE omni_queue SET status='approved', approved_at=?, updated_at=? "
            "WHERE id=?", (_now_iso(), _now_iso(), qid))
    return {"id": qid, "status": "approved"}


@router.post("/queue/{qid}/reject")
async def reject_asset(qid: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM omni_queue WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(404, detail="asset niet gevonden")
        conn.execute("UPDATE omni_queue SET status='rejected', updated_at=? WHERE id=?",
                     (_now_iso(), qid))
    return {"id": qid, "status": "rejected"}


@router.post("/queue/{qid}/publish")
async def publish_asset(qid: str):
    """Publiceer een goedgekeurd asset naar het platform achter de gate.

    Alleen platforms mét geconfigureerde service posten echt (LinkedIn/X).
    Reddit -> blijft 'approved',返回 een hint dat Vincent het handmatig plakt.
    """
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = dict(conn.execute("SELECT * FROM omni_queue WHERE id = ?", (qid,)).fetchone() or {})
    if not row:
        raise HTTPException(404, detail="asset niet gevonden")
    if row["status"] != "approved":
        raise HTTPException(422, detail="asset is niet goedgekeurd (status=%s)" % row["status"])

    platform = row["platform"]
    body_text = row["body"]
    title = row["title"]

    if platform == "linkedin":
        try:
            from ...shared import linkedin as svc
            if not svc.is_configured():
                return {"id": qid, "status": "approved",
                        "published": False,
                        "note": "LinkedIn niet geconfigureerd — plak handmatig"}
            res = await svc.post_update(title + "\n\n" + body_text if title else body_text,
                                        None, None)
            ok = bool(res.get("success"))
        except Exception as e:  # noqa: BLE001
            return {"id": qid, "status": "approved", "published": False,
                    "note": f"LinkedIn-fout: {str(e)[:120]}"}
        _mark_published(qid, ok, res)
        return {"id": qid, "status": "published" if ok else "approved",
                "published": ok, "note": res.get("error", "")[:160] if not ok else ""}

    if platform == "x":
        try:
            from ...shared import twitter as svc
            if not svc.is_configured():
                return {"id": qid, "status": "approved",
                        "published": False, "note": "X niet geconfigureerd — plak handmatig"}
            info = await svc.post_update(body_text, None, None)
            ok = bool(info.get("success"))
        except Exception as e:  # noqa: BLE001
            return {"id": qid, "status": "approved", "published": False,
                    "note": f"X-fout: {str(e)[:120]}"}
        _mark_published(qid, ok, info)
        return {"id": qid, "status": "published" if ok else "approved",
                "published": ok, "note": info.get("error", "")[:160] if not ok else ""}

    # reddit / youtube / aeo_snippet: geen automatische poster — Vincent plakt
    # het concept handmatig (ban-risico / upload-flow).
    return {"id": qid, "status": "approved", "published": False,
            "note": f"{platform}: geen automatische poster — plak het concept handmatig"}


def _mark_published(qid: str, ok: bool, result: Dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE omni_queue SET status=?, published_result=?, updated_at=? WHERE id=?",
            ("published" if ok else "approved", json.dumps(result)[:2000],
             _now_iso(), qid))

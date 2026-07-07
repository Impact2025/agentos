"""
Kennisbank — Goldie's pijler 2 ('information gain').

Google straft generieke AI-content af; het verschil zit in unieke context.
Per site slaan we daarom twee dingen op die elke schrijfopdracht in gaan:

  * Profiel + CTA's — op de `sites`-rij zelf (kolommen `profile` en `ctas`):
    wie ben je, USP's, doelgroep, toon, en de call-to-actions die in elk
    artikel thuishoren.
  * Casestudies — `case_studies`-tabel: harde data, cijfers en resultaten
    van echte projecten, als bewijsmateriaal in artikelen.

`match_case_study` koppelt een zoekwoord deterministisch (token-overlap,
geen LLM-kosten) aan de relevantste casestudy; zonder overlap pakt hij de
recentste actieve casestudy zodat er altijd bewijs in het artikel zit.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...shared.database import get_conn
from .optimizer import _significant_tokens
from . import sites as sites_service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_CS_FIELDS = ("title", "summary", "body", "tags", "source_url", "status")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Casestudies CRUD ─────────────────────────────────────────────────────────

def list_case_studies(site_id: str, status: Optional[str] = "active") -> List[Dict]:
    clauses, params = ["site_id = ?"], [site_id]
    if status:
        clauses.append("status = ?")
        params.append(status)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM case_studies WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_case_study(cs_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM case_studies WHERE id = ?", (cs_id,)).fetchone()
    return dict(row) if row else None


def create_case_study(site_id: str, data: Dict) -> Dict:
    if not (data.get("title") or "").strip():
        raise ValueError("Veld 'title' is verplicht.")
    cs_id = str(uuid.uuid4())
    now = _now()
    values = {f: (data.get(f) or "") for f in _CS_FIELDS}
    values["status"] = values["status"] or "active"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO case_studies
               (id, site_id, title, summary, body, tags, source_url, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cs_id, site_id, values["title"], values["summary"], values["body"],
             values["tags"], values["source_url"], values["status"], now, now),
        )
    return get_case_study(cs_id)


def update_case_study(cs_id: str, data: Dict) -> Optional[Dict]:
    updates, params = [], []
    for f in _CS_FIELDS:
        if f in data and data[f] is not None:
            updates.append(f"{f} = ?")
            params.append(data[f])
    if not updates:
        return get_case_study(cs_id)
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(cs_id)
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE case_studies SET {', '.join(updates)} WHERE id = ?", params)
        if cur.rowcount == 0:
            return None
    return get_case_study(cs_id)


def delete_case_study(cs_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM case_studies WHERE id = ?", (cs_id,))
    return cur.rowcount > 0


# ── Matching + prompt-context ────────────────────────────────────────────────

def match_case_study(site_id: str, keyword: str, angle: str = "") -> Optional[Dict]:
    """Relevantste actieve casestudy voor een zoekwoord (token-overlap; tags
    wegen dubbel). Geen overlap → de recentste, zodat er altijd bewijs is."""
    studies = list_case_studies(site_id, status="active")
    if not studies:
        return None
    q_tokens = set(_significant_tokens(f"{keyword} {angle}"))
    best, best_score = None, 0.0
    for cs in studies:
        tag_tokens = set(_significant_tokens((cs.get("tags") or "").replace(",", " ")))
        text_tokens = set(_significant_tokens(f"{cs.get('title', '')} {cs.get('summary', '')}"))
        score = 2.0 * len(q_tokens & tag_tokens) + 1.0 * len(q_tokens & text_tokens)
        if score > best_score:
            best, best_score = cs, score
    return best or studies[0]  # studies is al updated_at DESC gesorteerd


def get_site_knowledge(site: Dict) -> Dict:
    """Profiel + CTA-lijst van een site in bruikbare vorm voor prompts."""
    try:
        ctas = json.loads(site.get("ctas") or "[]")
        if not isinstance(ctas, list):
            ctas = []
    except json.JSONDecodeError:
        ctas = []
    return {
        "profile": (site.get("profile") or "").strip(),
        "ctas": [str(c).strip() for c in ctas if str(c).strip()],
    }


# ── API ──────────────────────────────────────────────────────────────────────

class CaseStudyIn(BaseModel):
    title: str
    summary: str = ""
    body: str = ""
    tags: str = ""
    source_url: str = ""
    status: str = "active"


class CaseStudyPatch(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[str] = None
    source_url: Optional[str] = None
    status: Optional[str] = None


@router.get("/{site_id}/case-studies")
def api_list(site_id: str, status: Optional[str] = None):
    if not sites_service.get_site(site_id):
        raise HTTPException(404, "Site niet gevonden")
    return list_case_studies(site_id, status=status)


@router.post("/{site_id}/case-studies")
def api_create(site_id: str, body: CaseStudyIn):
    if not sites_service.get_site(site_id):
        raise HTTPException(404, "Site niet gevonden")
    try:
        return create_case_study(site_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/case-studies/{cs_id}")
def api_update(cs_id: str, body: CaseStudyPatch):
    if body.status is not None and body.status not in ("active", "archived"):
        raise HTTPException(400, "Ongeldige status — gebruik 'active' of 'archived'")
    updated = update_case_study(cs_id, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Casestudy niet gevonden")
    return updated


@router.delete("/case-studies/{cs_id}")
def api_delete(cs_id: str):
    if not delete_case_study(cs_id):
        raise HTTPException(404, "Casestudy niet gevonden")
    return {"ok": True}

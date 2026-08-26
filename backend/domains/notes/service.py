"""
Meeting-notulen-service.

Eén taak: van een geplakt transcript naar een samenvatting + actiepunten als
CRM-taken. Geen eigen takenlijst — een actiepunt dat nergens anders zichtbaar
is dan in dit ene notitiebestand is precies het soort werk dat blijft liggen
(zelfde les als "activiteit is geen effect" elders in deze codebase: een
notitie die niemand meer opent is geen actiepunt, het is een aantekening).

Onleesbaar LLM-antwoord = status 'mislukt', nooit een verzonnen samenvatting.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from .models import ensure_schema

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r) -> Dict[str, Any]:
    return dict(r) if r is not None else {}


def _summary_prompt(title: str, transcript: str) -> str:
    return (
        f"Dit is het transcript/verslag van een gesprek getiteld '{title}'.\n\n"
        f"{transcript[:12000]}\n\n"
        "Geef een beknopte samenvatting (3-5 zinnen, Nederlands) en een lijst "
        "concrete actiepunten (alleen wat er écht besproken/toegezegd is — verzin "
        "niets, en laat het weg als er geen actiepunten zijn).\n\n"
        "Antwoord UITSLUITEND met JSON (geen markdown):\n"
        '{"summary": "...", "action_items": ["actiepunt 1", "actiepunt 2"]}'
    )


async def maak_notitie(title: str, transcript: str, *, company_id: str = "",
                        deal_id: str = "", meeting_date: str = "") -> Dict[str, Any]:
    """Slaat het transcript op en vat het meteen samen — één ronde, net als
    `billing.genereer_uren_factuur_concept`. Actiepunten worden CRM-taken,
    gekoppeld aan het bedrijf/de deal als die zijn meegegeven."""
    ensure_schema()
    title = (title or "").strip()
    transcript = (transcript or "").strip()
    if not title or not transcript:
        raise ValueError("Titel en transcript zijn verplicht")

    nid = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO meeting_notes (id, title, company_id, deal_id, meeting_date, "
            "transcript, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'nieuw', ?)",
            (nid, title, company_id, deal_id, meeting_date, transcript, now),
        )

    from ..publish.content_pipeline import _llm, _extract_json
    system = (
        "Je vat Nederlandse gesprekstranscripten feitelijk samen. Je verzint nooit "
        "toezeggingen of actiepunten die niet letterlijk in de tekst staan."
    )
    raw = await _llm(system, _summary_prompt(title, transcript), max_tokens=800, purpose="notes")
    if not raw:
        _mark_failed(nid)
        return get_note(nid)
    try:
        data = json.loads(_extract_json(raw))
        summary = (data.get("summary") or "").strip()
        action_items = [str(a).strip() for a in (data.get("action_items") or []) if str(a).strip()]
    except (ValueError, TypeError):
        log.warning("[notes] Onleesbaar LLM-antwoord voor notitie %s", nid)
        _mark_failed(nid)
        return get_note(nid)
    if not summary:
        _mark_failed(nid)
        return get_note(nid)

    task_ids: List[Dict[str, str]] = []
    if action_items:
        from ..crm import service as crm_service
        for item in action_items:
            task = crm_service.create_task(
                item, company_id=company_id, deal_id=deal_id,
                description=f"Actiepunt uit notulen: {title}",
            )
            task_ids.append({"text": item, "task_id": task["id"]})

    with get_conn() as conn:
        conn.execute(
            "UPDATE meeting_notes SET summary = ?, action_items = ?, status = 'samengevat', "
            "summarized_at = ? WHERE id = ?",
            (summary, json.dumps(task_ids), _now(), nid),
        )
    log_outcome(
        "WeAreImpact", "notulen_samengevat",
        f"Notulen '{title}' samengevat, {len(task_ids)} actiepunt(en) als taak vastgelegd.",
        artifact=f"/api/notes/{nid}",
    )
    return get_note(nid)


def _mark_failed(note_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE meeting_notes SET status = 'mislukt' WHERE id = ?", (note_id,))
    log_outcome(
        "WeAreImpact", "notulen_samenvatten_mislukt",
        f"Kon notitie {note_id} niet samenvatten (LLM gaf geen bruikbaar antwoord).",
        status="error",
        next_step="Open Klanten en probeer de notitie opnieuw aan te maken.",
    )


def get_note(note_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = _row(conn.execute("SELECT * FROM meeting_notes WHERE id = ?", (note_id,)).fetchone())
    if not row:
        return None
    row["action_items"] = json.loads(row["action_items"] or "[]")
    return row


def list_notes(status: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM meeting_notes WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()]
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM meeting_notes ORDER BY created_at DESC"
            ).fetchall()]
    return [get_note(i) for i in ids]

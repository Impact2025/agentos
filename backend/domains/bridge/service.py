"""
Bridge — synchroniseert de review-gates met de cloud-companion (Vercel + Neon),
zodat Vincent kan beslissen als deze machine uitstaat.

Ontwerp (pull-model, alleen uitgaand HTTPS — geen open poorten of tunnels):
  1. PUSH: de volledige actieve set "wacht op een mens"-items — de canonieke
     lijst uit het Actiecentrum (`build_inbox()`), per item verrijkt met genoeg
     preview om onderweg te kúnnen beslissen (artikel-HTML, mail-concept +
     oorspronkelijke vraag, outreach-tekst, agendaslot) — plus de laatste
     Iris-briefing en de funnel-cijfers. Full-state elke run: idempotent en
     zelfherstellend, geen delta-boekhouding; wat lokaal verdween wordt in de
     cloud gearchiveerd.
  2. PULL: besluiten die onderweg genomen zijn (`decisions`, status pending).
     Toepassen loopt via de whitelist in `actions.py` — dezelfde service-
     functies als de lokale UI-knoppen, dus alle gates blijven gelden.
  3. ACK: elk besluit krijgt applied/failed + boodschap terug, zodat de
     telefoon toont wat er echt gebeurde.

Staat de pc uit, dan stapelen besluiten zich op in Neon en voert de
eerstvolgende sync ze chronologisch uit.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ...shared.config import BRIDGE_REMOTE_URL, BRIDGE_TOKEN
from ...shared.database import get_conn

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0

# Uitslag van de laatste sync, voor GET /api/bridge/status.
_last_sync: Dict[str, Any] = {}


def enabled() -> bool:
    return bool(BRIDGE_REMOTE_URL and BRIDGE_TOKEN)


# ── Verzamelen: items + previews ────────────────────────────────────────────

def _content_detail(job_id: str) -> Optional[Dict]:
    from ..publish import content_pipeline
    job = content_pipeline.get_job(job_id)
    if not job:
        return None
    return {
        "blog_html": job.get("blog_html") or "",
        "title": job.get("title") or "",
        "keyword": job.get("keyword") or "",
        "seo_score": job.get("seo_score"),
        "status": job.get("status"),
        "site_id": job.get("site_id"),
    }


def _mail_details() -> Dict[str, Dict]:
    from ..mail import service as mail
    out = {}
    for r in mail.pending_replies():
        out[str(r["id"])] = {
            "draft_body": r.get("edited_body") or r.get("draft_body") or "",
            "to_addr": r.get("to_addr"),
            "subject": r.get("subject"),
            "project": r.get("project"),
            "question_subject": r.get("question_subject"),
            "question_body": (r.get("question_body") or "")[:4000],
            "from_name": r.get("from_name"),
            "from_addr": r.get("from_addr"),
        }
    return out


def _outreach_details() -> Dict[str, Dict]:
    from ..prospecting import outreach
    from ..prospecting.router import _svc as leads_svc
    out = {}
    for lead in leads_svc.list_leads(status="outreach_review"):
        out[str(lead["id"])] = {
            "org_name": lead.get("org_name"),
            "city": lead.get("city"),
            "subject": lead.get("outreach_subject"),
            "body": lead.get("outreach_draft"),
            "target_email": outreach.target_email_for(lead),
        }
    return out


def _calendar_details() -> Dict[str, Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, subject, from_addr, title, proposed_start, proposed_end, "
            "location, priority, conflict_checked, rationale "
            "FROM calendar_proposals WHERE status='pending_review'"
        ).fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def collect_items() -> List[Dict[str, Any]]:
    """De Actiecentrum-inbox, verrijkt met previews per item-type. `key` is de
    stabiele identiteit in de cloud (dismiss_kind + id)."""
    from ..action_center import service as ac
    inbox = ac.build_inbox()
    mail_d = _mail_details()
    outreach_d = _outreach_details()
    calendar_d = _calendar_details()

    items = []
    for it in inbox.get("items", []) if isinstance(inbox, dict) else inbox:
        kind = it.get("dismiss_kind") or "error"
        item_id = str(it.get("id"))
        detail: Optional[Dict] = None
        if kind == "content":
            try:
                detail = _content_detail(item_id)
            except Exception:
                logger.exception("Bridge: content-detail mislukt voor %s", item_id)
        elif kind == "mail":
            detail = mail_d.get(item_id)
        elif kind == "outreach":
            detail = outreach_d.get(item_id)
        elif kind == "calendar":
            detail = calendar_d.get(item_id)
        items.append({
            "key": f"{kind}:{item_id}",
            "kind": it.get("kind"),
            "dismiss_kind": kind,
            "item_id": item_id,
            "title": it.get("title"),
            "project": it.get("project"),
            "created_at": it.get("created_at"),
            "summary": it.get("summary"),
            "actions": it.get("actions") or [],
            "detail": detail,
        })
    return items


def collect_briefing() -> Dict[str, Any]:
    """Laatste Iris-briefing + funnel-cijfers als leesvoer voor onderweg."""
    briefing: Dict[str, Any] = {}
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT report_date, markdown, grades, llm_ok FROM iris_reports "
                "ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
        if r:
            briefing["iris"] = {
                "date": r["report_date"],
                "markdown": r["markdown"],
                "grades": json.loads(r["grades"] or "{}"),
                "llm_ok": bool(r["llm_ok"]),
            }
    except Exception:
        logger.exception("Bridge: Iris-briefing ophalen mislukt")
    try:
        from ..prospecting import funnel
        briefing["funnel"] = funnel.funnel_stats()
    except Exception:
        logger.exception("Bridge: funnel-cijfers ophalen mislukt")
    return briefing


def build_push_payload() -> Dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": collect_items(),
        "briefing": collect_briefing(),
    }


# ── Sync-cyclus ─────────────────────────────────────────────────────────────

def _base() -> str:
    return BRIDGE_REMOTE_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {BRIDGE_TOKEN}"}


async def sync_once() -> Dict[str, Any]:
    """Eén volledige cyclus: push state → pull besluiten → toepassen → ack."""
    global _last_sync
    if not enabled():
        return {"ok": False, "detail": "Bridge niet geconfigureerd (BRIDGE_REMOTE_URL/BRIDGE_TOKEN)"}

    summary: Dict[str, Any] = {"ok": True, "pushed": 0, "applied": 0, "failed": 0,
                               "at": datetime.now(timezone.utc).isoformat()}
    try:
        payload = build_push_payload()
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.post(f"{_base()}/api/bridge?op=push", json=payload)
            r.raise_for_status()
            summary["pushed"] = len(payload["items"])

            r = await client.get(f"{_base()}/api/bridge?op=decisions")
            r.raise_for_status()
            decisions = r.json().get("decisions", [])

            acks = []
            for d in decisions:
                ok, message = await _apply(d)
                acks.append({"id": d.get("id"),
                             "status": "applied" if ok else "failed",
                             "result": message})
                summary["applied" if ok else "failed"] += 1
            if acks:
                r = await client.post(f"{_base()}/api/bridge?op=ack", json={"acks": acks})
                r.raise_for_status()

            # Notities die onderweg zijn ingesproken → vault (Onderweg-map).
            r = await client.get(f"{_base()}/api/bridge?op=notes")
            r.raise_for_status()
            notes = r.json().get("notes", [])
            synced_ids = [n["id"] for n in notes if _store_note(n)]
            if synced_ids:
                r = await client.post(f"{_base()}/api/bridge?op=notes-ack",
                                      json={"ids": synced_ids})
                r.raise_for_status()
                summary["notes"] = len(synced_ids)
    except httpx.HTTPError as e:
        logger.warning("Bridge-sync mislukt (netwerk/remote): %s", e)
        summary = {"ok": False, "detail": str(e)[:300],
                   "at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.exception("Bridge-sync mislukt")
        summary = {"ok": False, "detail": str(e)[:300],
                   "at": datetime.now(timezone.utc).isoformat()}
    _last_sync = summary
    return summary


def _store_note(note: Dict[str, Any]) -> bool:
    """Notitie van onderweg → markdown in de vault (map 'Onderweg'). Zonder
    vault-pad loggen we alleen een uitkomst-kaart, dan is de tekst niet kwijt."""
    from ...shared.config import OBSIDIAN_VAULT_PATH
    text = (note.get("text") or "").strip()
    if not text:
        return True  # lege notitie: ack'en en vergeten
    stamp = datetime.now().strftime("%Y-%m-%d %H.%M")
    artifact = ""
    try:
        if OBSIDIAN_VAULT_PATH:
            from pathlib import Path
            folder = Path(OBSIDIAN_VAULT_PATH) / "Onderweg"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"Onderweg {stamp} (#{note.get('id')}).md"
            path.write_text(f"# Notitie onderweg — {stamp}\n\n{text}\n", encoding="utf-8")
            artifact = str(path)
        from ...shared.outcomes import log_outcome
        log_outcome(
            "Bridge", "note_synced",
            f"Notitie van onderweg binnengehaald: {text[:160]}",
            artifact=artifact,
            next_step="Verwerk de notitie (vault-map 'Onderweg') of voer 'm aan Iris' kennisbank.",
        )
        return True
    except Exception:
        logger.exception("Bridge: notitie opslaan mislukt (blijft pending in de cloud)")
        return False


async def _apply(decision: Dict[str, Any]) -> tuple:
    from . import actions
    ok, message = await actions.apply_decision(decision)
    # Een mislukt besluit is menselijke actie waard: het stond onderweg als
    # "gedaan" in Vincents hoofd, maar gebeurde niet.
    if not ok:
        try:
            from ...shared.outcomes import log_outcome
            log_outcome(
                "Bridge", "remote_decision_failed",
                f"Besluit onderweg ({decision.get('item_kind')}/{decision.get('action')} "
                f"op {decision.get('item_id')}) kon niet uitgevoerd worden: {message}",
                next_step="Voer de actie handmatig uit in het Actiecentrum.",
                status="error",
            )
        except Exception:
            logger.exception("Bridge: uitkomst-kaart loggen mislukt")
    return ok, message


def last_sync() -> Dict[str, Any]:
    return dict(_last_sync)

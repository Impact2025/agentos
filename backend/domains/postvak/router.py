"""Postvak — per-project vs. persoonlijk onderscheid.

Probleem (13 aug 2026): de Postvak-tab haalde altijd de énige, globale
persoonlijke Outlook-inbox op (v.munster@weareimpact.nl) en toonde die — ongefilterd
en zonder project-scoping — in elk project. Daardoor zag je in Bewaardvoorjou
ING-, ClubMatch- en Impact2025-CI/CD-mail die niets met dat project te maken heeft.

Oplossing: het Postvak volgt hetzelfde patroon als elke andere tab — het filtert
op het geselecteerde project. In een project toont het de (per-project) mailbox-inbox
uit het helpdesk-systeem (tabel `mail_inbox`, gekoppeld via `mailboxes.project`); de
persoonlijke Outlook-inbox verschijnt alleen op hoofdniveau (geen project geselecteerd).

  GET /api/postvak?project=Bewaardvoorjou
      → {mode:"project", address:"info@bewaardvoorjou.nl", emails:[...]}
        of {mode:"project", address:null, emails:[]} als het project geen mailbox heeft
  GET /api/postvak            (geen project)
      → {mode:"personal"}   — frontend valt terug op /api/outlook/sorted
"""
from typing import List, Optional

from fastapi import APIRouter, Query

from ...shared.database import get_conn
from ..mail import service as mail_service

router = APIRouter(prefix="/api/postvak", tags=["postvak"])


# classified (helpdesk) → weergave-prioriteit + bucket, zodat de Postvak-tab
# dezelfde urgentie-uitdrukking houdt als de persoonlijke inbox.
_PRIORITY = {
    "question": 70,      # vraagt om een antwoord → "Vraagt om actie"
    "appointment": 60,   # agenda-voorstel → ook actie
    "invoice": 50,
    "other": 40,         # "Belangrijk"
    "newsletter": 10,
    "spam": 5,
    "auto": 5,
    "ignored": 5,
    "unknown": 30,
}

_ACTION_BUCKETS = {"question", "appointment"}


def _normalize(row: dict, address: str) -> dict:
    classified = (row.get("classified") or "unknown").lower()
    body = row.get("body_text") or ""
    summary = body.strip().split("\n")[0][:160] if body.strip() else ""
    return {
        "id": f"mb_{row['id']}",  # prefix voorkomt id-botsing met outlook_emails
        "from_name": row.get("from_name") or row.get("from_addr") or "Onbekend",
        "from_email": row.get("from_addr") or "",
        "received_at": row.get("received_at") or "",
        "priority": _PRIORITY.get(classified, 30),
        "subject": row.get("subject") or "(geen onderwerp)",
        "ai_summary": summary,
        "ai_action": "",
        "bucket": "actie" if classified in _ACTION_BUCKETS else "info",
        "classified": classified,
        "mailbox_address": address,
        # body zit al in de lijst → toggle hoeft niet naar de server
        "body_text": body,
    }


@router.get("")
def postvak(project: Optional[str] = Query(None)):
    # Hoofdniveau (geen project): de frontend toont de persoonlijke Outlook-inbox.
    if not project:
        return {"mode": "personal"}

    # Alleen ingeschakelde mailboxen van dít project (genormaliseerde match,
    # zodat 'Bewaardvoorjou' en 'bewaardvoorjou' hetzelfde zijn).
    boxes = [b for b in mail_service.list_mailboxes(project=project)
             if b.get("enabled")]
    if not boxes:
        return {"mode": "project", "address": None, "emails": []}

    mids = [b["id"] for b in boxes]
    placeholder = ",".join("?" * len(mids))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT mi.id, mi.mailbox_id, mi.from_addr, mi.from_name, mi.subject,
                       mi.body_text, mi.received_at, mi.classified
                FROM mail_inbox mi
                WHERE mi.mailbox_id IN ({placeholder})
                ORDER BY mi.received_at DESC
                LIMIT 200""",
            mids,
        ).fetchall()

    emails: List[dict] = [_normalize(dict(r), boxes[0]["address"]) for r in rows]
    return {"mode": "project", "address": boxes[0]["address"], "emails": emails}

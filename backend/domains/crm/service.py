"""
CRM-service — bedrijven, contacten, deals, activiteiten, taken.

Eén regel staat boven de rest: een deal ontstaat nooit uit het niets. Hij komt
ofwel uit een gewonnen lead (`deal_uit_lead`, automatisch bij
`prospecting.funnel.advance_lead(status='won')`) ofwel wordt met de hand
aangemaakt — nooit verzonnen door een LLM. Dat is dezelfde discipline als de
Beursmeester en de Wachtrij: een systeem dat over geld en klanten gaat, mag
niets aannemen dat niet ergens vandaan komt.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from ...shared.projects import squash_project
from .models import ensure_schema

log = logging.getLogger(__name__)

DEAL_STAGES = ["gesprek", "voorstel", "onderhandeling", "gewonnen", "verloren"]
_CLOSED_STAGES = {"gewonnen", "verloren"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row(r) -> Dict[str, Any]:
    return dict(r) if r is not None else {}


# ── Bedrijven ────────────────────────────────────────────────────────────

def list_companies(q: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if q:
            rows = conn.execute(
                "SELECT * FROM crm_companies WHERE name LIKE ? ORDER BY name",
                (f"%{q}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM crm_companies ORDER BY name").fetchall()
        return [_row(r) for r in rows]


def get_company(company_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM crm_companies WHERE id = ?", (company_id,)
        ).fetchone()) or None


def find_company_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Dedupe-zoekopdracht op squash-vorm — zelfde regel als `squash_project`
    elders: 'Bedrijf B.V.' en 'bedrijf bv' mogen geen twee rijen worden."""
    ensure_schema()
    target = squash_project(name)
    if not target:
        return None
    with get_conn() as conn:
        for row in conn.execute("SELECT * FROM crm_companies").fetchall():
            if squash_project(row["name"]) == target:
                return _row(row)
    return None


def create_company(name: str, *, website: str = "", industry: str = "", city: str = "",
                    phone: str = "", email: str = "", kvk_number: str = "",
                    notes: str = "", lead_id: str = "") -> Dict[str, Any]:
    ensure_schema()
    name = (name or "").strip()
    if not name:
        raise ValueError("Bedrijfsnaam is verplicht")
    existing = find_company_by_name(name)
    if existing:
        return existing
    cid = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_companies (id, name, website, industry, city, phone, email, "
            "kvk_number, notes, lead_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, name, website, industry, city, phone, email, kvk_number, notes,
             lead_id, now, now),
        )
    return get_company(cid)


def update_company(company_id: str, **fields) -> Optional[Dict[str, Any]]:
    ensure_schema()
    allowed = {"name", "website", "industry", "city", "phone", "email", "kvk_number", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_company(company_id)
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE crm_companies SET {set_clause} WHERE id = ?",
            (*updates.values(), company_id),
        )
    return get_company(company_id)


# ── Contacten ────────────────────────────────────────────────────────────

def list_contacts(company_id: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if company_id:
            rows = conn.execute(
                "SELECT * FROM crm_contacts WHERE company_id = ? ORDER BY is_primary DESC, first_name",
                (company_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM crm_contacts ORDER BY first_name").fetchall()
        return [_row(r) for r in rows]


def create_contact(first_name: str, *, company_id: str = "", last_name: str = "",
                    email: str = "", phone: str = "", job_title: str = "",
                    is_primary: bool = False, notes: str = "") -> Dict[str, Any]:
    ensure_schema()
    first_name = (first_name or "").strip()
    if not first_name:
        raise ValueError("Voornaam is verplicht")
    cid = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_contacts (id, company_id, first_name, last_name, email, phone, "
            "job_title, is_primary, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, company_id, first_name, last_name, email, phone, job_title,
             1 if is_primary else 0, notes, now, now),
        )
        row = conn.execute("SELECT * FROM crm_contacts WHERE id = ?", (cid,)).fetchone()
    return _row(row)


# ── Deals ────────────────────────────────────────────────────────────────

def list_deals(stage: str = "", company_id: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    q = "SELECT * FROM crm_deals WHERE 1=1"
    params: List[Any] = []
    if stage:
        q += " AND stage = ?"
        params.append(stage)
    if company_id:
        q += " AND company_id = ?"
        params.append(company_id)
    q += " ORDER BY created_at DESC"
    with get_conn() as conn:
        return [_row(r) for r in conn.execute(q, params).fetchall()]


def get_deal(deal_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM crm_deals WHERE id = ?", (deal_id,)).fetchone()) or None


def create_deal(company_id: str, title: str, *, contact_id: str = "", lead_id: str = "",
                 value_cents: int = 0, stage: str = "gesprek", probability: int = 20,
                 expected_close_date: str = "", description: str = "",
                 source: str = "") -> Dict[str, Any]:
    ensure_schema()
    title = (title or "").strip()
    if not title:
        raise ValueError("Titel is verplicht")
    if stage not in DEAL_STAGES:
        stage = "gesprek"
    did = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_deals (id, company_id, contact_id, lead_id, title, value_cents, "
            "stage, probability, expected_close_date, description, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, company_id, contact_id, lead_id, title, value_cents, stage, probability,
             expected_close_date, description, source, now, now),
        )
    log_activity(company_id=company_id, deal_id=did, type_="systeem",
                 subject=f"Deal aangemaakt: {title}")
    return get_deal(did)


def update_deal_stage(deal_id: str, stage: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    if stage not in DEAL_STAGES:
        raise ValueError(f"Onbekende stage: {stage}")
    deal = get_deal(deal_id)
    if not deal:
        return None
    now = _now()
    closed_at = now if stage in _CLOSED_STAGES else ""
    with get_conn() as conn:
        conn.execute(
            "UPDATE crm_deals SET stage = ?, updated_at = ?, closed_at = ? WHERE id = ?",
            (stage, now, closed_at, deal_id),
        )
    log_activity(company_id=deal["company_id"], deal_id=deal_id, type_="systeem",
                 subject=f"Stage gewijzigd: {deal['stage']} -> {stage}")
    return get_deal(deal_id)


def pipeline_summary() -> Dict[str, Any]:
    ensure_schema()
    deals = list_deals()
    open_deals = [d for d in deals if d["stage"] not in _CLOSED_STAGES]
    won = [d for d in deals if d["stage"] == "gewonnen"]
    by_stage: Dict[str, Dict[str, Any]] = {}
    for stage in DEAL_STAGES:
        rows = [d for d in deals if d["stage"] == stage]
        by_stage[stage] = {
            "count": len(rows),
            "value_cents": sum(r["value_cents"] or 0 for r in rows),
        }
    weighted = sum((d["value_cents"] or 0) * (d["probability"] or 0) / 100 for d in open_deals)
    return {
        "total_open_value_cents": sum(d["value_cents"] or 0 for d in open_deals),
        "weighted_value_cents": round(weighted),
        "open_count": len(open_deals),
        "won_count": len(won),
        "by_stage": by_stage,
    }


# ── Activiteiten ─────────────────────────────────────────────────────────

def log_activity(*, company_id: str = "", contact_id: str = "", deal_id: str = "",
                  type_: str, subject: str, description: str = "") -> Dict[str, Any]:
    ensure_schema()
    aid = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_activities (id, company_id, contact_id, deal_id, type, subject, "
            "description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, company_id, contact_id, deal_id, type_, subject, description, now),
        )
        row = conn.execute("SELECT * FROM crm_activities WHERE id = ?", (aid,)).fetchone()
    return _row(row)


def list_activities(company_id: str = "", deal_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    ensure_schema()
    q = "SELECT * FROM crm_activities WHERE 1=1"
    params: List[Any] = []
    if company_id:
        q += " AND company_id = ?"
        params.append(company_id)
    if deal_id:
        q += " AND deal_id = ?"
        params.append(deal_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [_row(r) for r in conn.execute(q, params).fetchall()]


# ── Taken ────────────────────────────────────────────────────────────────

def list_tasks(status: str = "open") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM crm_tasks WHERE status = ? ORDER BY COALESCE(NULLIF(due_date,''),'9999-12-31')",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM crm_tasks ORDER BY COALESCE(NULLIF(due_date,''),'9999-12-31')"
            ).fetchall()
        return [_row(r) for r in rows]


def overdue_tasks() -> List[Dict[str, Any]]:
    """Open taken waarvan de streefdatum voorbij is — de vloer onder 'ik ben
    het vergeten', zelfde functie als `crm_taak_over_datum` in de audit."""
    ensure_schema()
    today = _today()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crm_tasks WHERE status = 'open' AND due_date != '' AND due_date < ? "
            "ORDER BY due_date",
            (today,),
        ).fetchall()
        return [_row(r) for r in rows]


def create_task(title: str, *, company_id: str = "", contact_id: str = "", deal_id: str = "",
                 description: str = "", priority: str = "normal", due_date: str = "") -> Dict[str, Any]:
    ensure_schema()
    title = (title or "").strip()
    if not title:
        raise ValueError("Titel is verplicht")
    tid = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_tasks (id, company_id, contact_id, deal_id, title, description, "
            "priority, status, due_date, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, '')",
            (tid, company_id, contact_id, deal_id, title, description, priority, due_date, now),
        )
        row = conn.execute("SELECT * FROM crm_tasks WHERE id = ?", (tid,)).fetchone()
    return _row(row)


def complete_task(task_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        conn.execute(
            "UPDATE crm_tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (_now(), task_id),
        )
        row = conn.execute("SELECT * FROM crm_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row(row) or None


# ── Lead → deal (de brug met prospecting/funnel.py) ─────────────────────

def deal_uit_lead(lead_id: str) -> Optional[Dict[str, Any]]:
    """Een lead die 'won' wordt, krijgt hier een bedrijf + deal.

    Wordt aangeroepen vanuit `prospecting.funnel.advance_lead` op het moment
    dat de status 'won' wordt gezet — nooit los, want anders kan een status-
    wijziging in de funnel en een deal in de CRM uit elkaar gaan lopen (zelfde
    les als CLAUDE.md-regel "een statuswijziging is geen ingreep in de
    wereld": hier is het omgekeerd — de wereld (gewonnen klant) veranderde al,
    dus moet de CRM het volgen, niet andersom).

    Idempotent op `lead_id`: een dubbele aanroep (bijv. een gecorrigeerde
    status die weer terug- en vooruitgaat) maakt geen tweede deal.
    """
    ensure_schema()
    with get_conn() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        existing_deal = conn.execute(
            "SELECT * FROM crm_deals WHERE lead_id = ?", (lead_id,)
        ).fetchone()
    if not lead:
        return None
    if existing_deal:
        return _row(existing_deal)

    lead = _row(lead)
    company = find_company_by_name(lead["org_name"]) or create_company(
        lead["org_name"],
        website=lead.get("website") or "",
        phone=lead.get("phone") or "",
        email=lead.get("email") or "",
        city=lead.get("city") or "",
        kvk_number=lead.get("kvk_number") or "",
        lead_id=lead_id,
    )

    contact_id = ""
    try:
        contacts = json.loads(lead.get("contacts") or "[]")
    except (TypeError, ValueError):
        contacts = []
    if contacts and isinstance(contacts, list):
        c = contacts[0] if isinstance(contacts[0], dict) else {}
        naam = (c.get("name") or "").strip()
        if naam:
            voornaam, _, achternaam = naam.partition(" ")
            contact = create_contact(
                voornaam, company_id=company["id"], last_name=achternaam,
                email=c.get("email") or lead.get("email") or "",
                phone=c.get("phone") or "", job_title=c.get("role") or "",
                is_primary=True,
            )
            contact_id = contact["id"]

    deal = create_deal(
        company["id"], f"Opdracht {lead['org_name']}",
        contact_id=contact_id, lead_id=lead_id, stage="gewonnen", probability=100,
        source="prospecting", description=lead.get("summary") or "",
    )
    log_activity(company_id=company["id"], deal_id=deal["id"], type_="systeem",
                 subject="Lead gewonnen via acquisitie-funnel",
                 description=f"Automatisch aangemaakt vanuit lead {lead_id}.")
    log_outcome(
        "WeAreImpact", "crm_deal_uit_lead",
        f"Lead '{lead['org_name']}' gewonnen -> deal aangemaakt in de CRM.",
        artifact=f"/api/crm/deals/{deal['id']}",
        next_step="Vul de opdrachtwaarde in en zet een factuurmoment op de agenda.",
    )
    return deal

"""Acquisitie-funnel — de meetlus van input naar output.

Sales als conversieformule: je stuurt op de input (outreach-concepten,
verstuurde mails) en meet de output (reacties, gesprekken, klanten). Deze
module is de boekhouding van oorzaak en gevolg:

  new → enriched → valid → outreach_review → contacted → replied → call → won
                                                                        ↘ lost

Elke stap vanaf 'contacted' krijgt een eenmalige tijdstempel op de lead, zodat
conversieratio's (en dus de formule "X mails → 1 reactie") berekenbaar zijn —
ook als de status daarna verder schuift.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from .opt_out import detect_opt_out

logger = logging.getLogger(__name__)

# Volgorde van de funnel; 'lost' is een zijuitgang vanaf elke stap.
FUNNEL_STAGES = [
    "new", "enriched", "valid", "outreach_review",
    "contacted", "replied", "call", "won",
]

# Stappen met een eigen eenmalige tijdstempel-kolom op leads.
STAGE_TIMESTAMP = {
    "contacted": "contacted_at",
    "replied":   "replied_at",
    "call":      "call_at",
    "won":       "won_at",
    "lost":      "lost_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def advance_lead(lead_id: str, new_status: str) -> Optional[Dict[str, Any]]:
    """Zet een lead naar een funnel-stap en stempel de bijbehorende tijd.

    Tijdstempels worden maar één keer gezet — een lead die per ongeluk terug
    en weer vooruit wordt gezet vervuilt de conversiecijfers niet. Status mag
    wel elke waarde krijgen (Vincent kan corrigeren)."""
    if new_status not in FUNNEL_STAGES and new_status != "lost":
        raise ValueError(f"Onbekende funnel-stap: {new_status}")
    now = _now()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            return None
        lead = dict(row)
        sets = ["status = ?", "updated_at = ?"]
        vals: list = [new_status, now]
        ts_col = STAGE_TIMESTAMP.get(new_status)
        if ts_col and not (lead.get(ts_col) or ""):
            sets.append(f"{ts_col} = ?")
            vals.append(now)
        vals.append(lead_id)
        conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", vals)
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if new_status == "won":
        # De CRM is de enige plek waar een gewonnen lead een bedrijf + deal
        # wordt — nooit los aangeroepen, anders lopen funnel-status en CRM
        # uit elkaar (zie crm/service.py:deal_uit_lead). Een kapotte CRM mag
        # het funnel-besluit zelf nooit blokkeren.
        try:
            from ..crm.service import deal_uit_lead
            deal_uit_lead(lead_id)
        except Exception:  # noqa: BLE001
            logger.exception("[funnel] Kon geen CRM-deal aanmaken voor lead %s", lead_id)
    return dict(row)


def mark_replied_if_lead(from_email: str, received_at: str = "", body_text: str = "") -> Optional[Dict[str, Any]]:
    """Reply-detectie: inkomende mail van een benaderde lead → status 'replied'.

    Matcht op het hoofdemail én op e-mails in de contacts-JSON. Alleen leads
    die daadwerkelijk benaderd zijn (contacted_at gezet) en waarvan de mail
    ná dat moment binnenkwam tellen — een oude mail in de inbox van vóór de
    outreach is geen reactie.

    Uitzondering: bevat de reply een afmeldverzoek ('STOP', 'afmelden', ...),
    dan wordt het adres (én domein) geblokkeerd via de opt-out-blocklist en
    gaat de lead naar 'lost' — wettelijk verplicht (Telecommunicatiewet 11.7)."""
    email = (from_email or "").strip().lower()
    if not email:
        return None
    # Opt-out heeft voorrang op reply-tellen: iemand die afmeldt is géén lead meer.
    if body_text and detect_opt_out(body_text):
        from . import opt_out as _opt_out
        _opt_out.record_opt_out(email, source="reply", raw_snippet=body_text[:500])
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE contacted_at != '' AND replied_at = '' "
            "AND (LOWER(email) = ? OR LOWER(contacts) LIKE ?) LIMIT 1",
            (email, f'%"{email}"%'),
        ).fetchone()
    if not row:
        return None
    lead = dict(row)
    if received_at and received_at < lead["contacted_at"]:
        return None
    updated = advance_lead(lead["id"], "replied")
    log_outcome(
        "Leads", "lead_replied",
        f"{lead['org_name']} heeft gereageerd op je outreach ({email})",
        next_step="Antwoord en plan een gesprek — dit is de output waar de formule om draait.",
    )
    logger.info("[funnel] Reply gedetecteerd: %s (%s) → replied", lead["org_name"], email)
    return updated


def funnel_stats() -> Dict[str, Any]:
    """De conversieformule: cumulatief bereikte stappen + ratio's.

    'Bereikt' is gebaseerd op de eenmalige tijdstempels, niet op de huidige
    status — een lead die al op 'won' staat telt dus ook mee als contacted
    en replied."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM leads GROUP BY status"
        ).fetchall())
        reached = {
            stage: conn.execute(
                f"SELECT COUNT(*) FROM leads WHERE {col} != ''"
            ).fetchone()[0]
            for stage, col in STAGE_TIMESTAMP.items()
        }

    def _ratio(a: int, b: int) -> Optional[float]:
        return round(b / a * 100, 1) if a else None

    conversions = {
        "contacted_to_replied": _ratio(reached["contacted"], reached["replied"]),
        "replied_to_call":      _ratio(reached["replied"], reached["call"]),
        "call_to_won":          _ratio(reached["call"], reached["won"]),
    }

    # De formule in mensentaal: "~N mails → 1 reactie" zodra er data is.
    formula = None
    if reached["replied"] > 0:
        per_reply = round(reached["contacted"] / reached["replied"], 1)
        formula = f"~{per_reply:g} verstuurde mails → 1 reactie"
        if reached["won"] > 0:
            per_won = round(reached["contacted"] / reached["won"], 1)
            formula += f" · ~{per_won:g} mails → 1 klant"
    elif reached["contacted"] > 0:
        formula = (
            f"{reached['contacted']} verstuurd, nog geen reactie — "
            "formule wordt zichtbaar zodra de eerste reply binnenkomt"
        )

    return {
        "total_leads": total,
        "by_status": by_status,
        "reached": reached,
        "conversions": conversions,
        "formula": formula,
    }


def input_stats(days: int = 7) -> Dict[str, Any]:
    """Geleverde inputs in de afgelopen periode, afgezet tegen de targets.

    Dit is het 'heb ik vandaag mijn proposals gedaan'-overzicht: concepten
    klaargezet, mails daadwerkelijk verstuurd, content gepubliceerd/gestaged."""
    from ...shared.config import OUTREACH_DAILY_TARGET
    window = f"-{days} day"
    with get_conn() as conn:
        drafts_ready = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = 'outreach_review'"
        ).fetchone()[0]
        drafted = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE outreach_drafted_at != '' "
            "AND outreach_drafted_at > datetime('now', ?)", (window,),
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE contacted_at != '' "
            "AND contacted_at > datetime('now', ?)", (window,),
        ).fetchone()[0]
        replies = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE replied_at != '' "
            "AND replied_at > datetime('now', ?)", (window,),
        ).fetchone()[0]
        content_live = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'live' "
            "AND status = 'ok' AND created_at > datetime('now', ?)", (window,),
        ).fetchone()[0]
        content_staged = conn.execute(
            "SELECT COUNT(*) FROM content_jobs WHERE created_at > datetime('now', ?)",
            (window,),
        ).fetchone()[0]

    workdays = max(1, round(days * 5 / 7))
    outreach_target = OUTREACH_DAILY_TARGET * workdays
    return {
        "days": days,
        "outreach_drafted": drafted,
        "outreach_drafts_ready": drafts_ready,
        "outreach_sent": sent,
        "outreach_target": outreach_target,
        "replies": replies,
        "content_live": content_live,
        "content_staged": content_staged,
    }

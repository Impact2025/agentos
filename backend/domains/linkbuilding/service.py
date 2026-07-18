"""Linkbuilding-funnel — de boekhouding van linkkans tot geverifieerde backlink.

Linkbuilding als gemeten conversieformule, zusje van de acquisitieformule:

  new → qualified → outreach_review → contacted → replied → agreed
      → link_live → verified                    (zijuitgang: lost)

Elke stap vanaf 'contacted' krijgt een eenmalige tijdstempel op de prospect,
zodat de formule ("X mails → 1 link live") berekenbaar is — ook als de status
daarna verder schuift. Er wordt in dit domein NOOIT automatisch verstuurd of
gepubliceerd; versturen kan alleen via de approve-endpoint (review-gate).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

FUNNEL_STAGES = [
    "new", "qualified", "outreach_review",
    "contacted", "replied", "agreed", "link_live", "verified",
]

# Stappen met een eigen eenmalige tijdstempel-kolom op link_prospects.
STAGE_TIMESTAMP = {
    "contacted": "contacted_at",
    "replied":   "replied_at",
    "agreed":    "agreed_at",
    "link_live": "link_live_at",
    "verified":  "verified_at",
    "lost":      "lost_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_domain(url_or_domain: str) -> str:
    """Normaliseer naar kaal domein: 'https://www.Voorbeeld.nl/pad' → 'voorbeeld.nl'."""
    s = (url_or_domain or "").strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    else:
        s = s.split("/")[0]
    return s[4:] if s.startswith("www.") else s


def get_prospect(prospect_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM link_prospects WHERE id = ?", (prospect_id,)
        ).fetchone()
    return dict(row) if row else None


def list_prospects(site_id: str = "", status: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM link_prospects WHERE 1=1"
    params: list = []
    if site_id:
        q += " AND site_id = ?"
        params.append(site_id)
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY relevance_score DESC, created_at DESC"
    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def create_prospect(site_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sla een linkkans op; dedupe op (site, domein). Returns None bij duplicaat."""
    domain = norm_domain(data.get("domain") or data.get("url") or "")
    if not domain:
        return None
    now = _now()
    pid = str(uuid.uuid4())
    with get_conn() as conn:
        dup = conn.execute(
            "SELECT 1 FROM link_prospects WHERE site_id = ? AND domain = ?",
            (site_id, domain),
        ).fetchone()
        if dup:
            return None
        conn.execute(
            "INSERT INTO link_prospects (id, site_id, domain, url, page_title, "
            "prospect_type, relevance_score, rationale, contact_email, "
            "target_url, anchor_text, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, site_id, domain,
             (data.get("url") or "")[:500],
             (data.get("page_title") or "")[:200],
             data.get("prospect_type") or "overig",
             int(data.get("relevance_score") or 0),
             (data.get("rationale") or "")[:500],
             (data.get("contact_email") or "").lower(),
             (data.get("target_url") or "")[:500],
             (data.get("anchor_text") or "")[:120],
             data.get("status") or "new",
             now, now),
        )
    return get_prospect(pid)


def advance_prospect(prospect_id: str, new_status: str) -> Optional[Dict[str, Any]]:
    """Zet een prospect naar een funnel-stap en stempel de bijbehorende tijd.

    Tijdstempels worden maar één keer gezet — terug- en weer vooruitzetten
    vervuilt de conversiecijfers niet (zelfde regel als de acquisitie-funnel)."""
    if new_status not in FUNNEL_STAGES and new_status != "lost":
        raise ValueError(f"Onbekende funnel-stap: {new_status}")
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM link_prospects WHERE id = ?", (prospect_id,)
        ).fetchone()
        if not row:
            return None
        prospect = dict(row)
        sets = ["status = ?", "updated_at = ?"]
        vals: list = [new_status, now]
        ts_col = STAGE_TIMESTAMP.get(new_status)
        if ts_col and not (prospect.get(ts_col) or ""):
            sets.append(f"{ts_col} = ?")
            vals.append(now)
        vals.append(prospect_id)
        conn.execute(f"UPDATE link_prospects SET {', '.join(sets)} WHERE id = ?", vals)
    return get_prospect(prospect_id)


def mark_replied_if_prospect(from_email: str, received_at: str = "") -> Optional[Dict[str, Any]]:
    """Reply-detectie: inkomende mail van een benaderde linkpartner → 'replied'.

    Alleen prospects die daadwerkelijk benaderd zijn (contacted_at gezet) en
    waarvan de mail ná dat moment binnenkwam tellen mee."""
    email = (from_email or "").strip().lower()
    if not email:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM link_prospects WHERE contacted_at != '' "
            "AND replied_at = '' AND LOWER(contact_email) = ? LIMIT 1",
            (email,),
        ).fetchone()
    if not row:
        return None
    prospect = dict(row)
    if received_at and received_at < prospect["contacted_at"]:
        return None
    updated = advance_prospect(prospect["id"], "replied")
    log_outcome(
        "Linkbuilding", "link_prospect_replied",
        f"{prospect['domain']} heeft gereageerd op je link-outreach ({email})",
        next_step="Antwoord en maak de linkafspraak concreet — daarna checkt de monitor of hij live komt.",
    )
    logger.info("[linkbuilding] Reply gedetecteerd: %s (%s) → replied",
                prospect["domain"], email)
    return updated


def funnel_stats(site_id: str = "") -> Dict[str, Any]:
    """De linkbuilding-formule: cumulatief bereikte stappen + ratio's.

    'Bereikt' is gebaseerd op de eenmalige tijdstempels, niet op de huidige
    status — een geverifieerde link telt dus ook mee als contacted."""
    where, params = ("WHERE site_id = ?", [site_id]) if site_id else ("", [])
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM link_prospects {where}", params
        ).fetchone()[0]
        by_status = dict(conn.execute(
            f"SELECT status, COUNT(*) FROM link_prospects {where} GROUP BY status",
            params,
        ).fetchall())
        reached = {
            stage: conn.execute(
                f"SELECT COUNT(*) FROM link_prospects {where}"
                f"{' AND ' if where else ' WHERE '}{col} != ''", params
            ).fetchone()[0]
            for stage, col in STAGE_TIMESTAMP.items()
        }
        placements = dict(conn.execute(
            f"SELECT status, COUNT(*) FROM link_placements {where} GROUP BY status",
            params,
        ).fetchall())
        dofollow_live = conn.execute(
            f"SELECT COUNT(*) FROM link_placements "
            f"{where}{' AND ' if where else ' WHERE '}"
            "status = 'live' AND rel = ''", params
        ).fetchone()[0]

    def _ratio(a: int, b: int) -> Optional[float]:
        return round(b / a * 100, 1) if a else None

    conversions = {
        "contacted_to_replied":  _ratio(reached["contacted"], reached["replied"]),
        "replied_to_agreed":     _ratio(reached["replied"], reached["agreed"]),
        "contacted_to_link_live": _ratio(reached["contacted"], reached["link_live"]),
    }

    formula = None
    if reached["link_live"] > 0:
        per_link = round(reached["contacted"] / reached["link_live"], 1)
        formula = f"~{per_link:g} verstuurde mails → 1 link live"
    elif reached["contacted"] > 0:
        formula = (
            f"{reached['contacted']} verstuurd, nog geen link live — "
            "de formule wordt zichtbaar zodra de eerste link staat"
        )

    return {
        "total_prospects": total,
        "by_status": by_status,
        "reached": reached,
        "conversions": conversions,
        "placements": placements,
        "dofollow_live": dofollow_live,
        "formula": formula,
    }

"""Opt-out registratie — wettelijk verplicht voor ongevraagde B2B-outreach.

Telecommunicatiewet art. 11.7 + e-Privacy: wie een 'STOP'-antwoord krijgt op
een commercial mail MOET dat adres direct en blijvend uit het bestand halen.
Deze module houdt een blocklist van afgemelde adressen (en domeinen) bij en
voorziet een `is_opted_out()`-check die de verzend-gate gebruikt zodat een
afgemelde lead nooit opnieuw gemaild wordt.

Blokkade is op adres én domein: meld je 'info@x.nl' af, dan wordt ook
'ceo@x.nl' nooit meer benaderd (zelfde organisatie). Bewust ruim: we willen
NIET per ongeluk hermailen naar iemand die expliciet 'STOP' zei.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Set

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

# Simpele patronen die in een reply duiden op een afmeldverzoek. Bewust ruim —
# bij twijfel honoreren we de afmelding (veiliger dan hermailen).
_OPT_OUT_PATTERNS = [
    r"\bstop\b", r"\buit\s*geschreven\b", r"\bafmelden\b", r"\baf\s*melden\b",
    r"\bgeen\s*interesse\b", r"\buitschrijven\b", r"\bremove\s*me\b",
    r"\bunsubscribe\b", r"\bno\s*thanks\b", r"\bniet\s*meer\s*mailen\b",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _domain(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1].strip().lower()


def record_opt_out(email: str, source: str = "reply", raw_snippet: str = "") -> bool:
    """Blokkeer een adres (én zijn domein) voor toekomstige outreach.

    Schrijft naar `lead_opt_outs` en zet eventuele bestaande leads op dat
    adres/domein naar status 'lost' met reden 'opt_out'. Returns True als er
    iets nieuws geblokkeerd is.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    dom = _domain(email)
    now = _now()

    with get_conn() as conn:
        # Idempotent: bestaat 'ie al, dan niets nieuws.
        existing = conn.execute(
            "SELECT 1 FROM lead_opt_outs WHERE email = ?", (email,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO lead_opt_outs (email, domain, source, snippet, created_at) "
                "VALUES (?,?,?,?,?)",
                (email, dom, source, raw_snippet[:500], now),
            )
        # Bestaande leads op dit adres of domein → lost.
        updated = conn.execute(
            "UPDATE leads SET status='lost', lost_at=?, updated_at=? "
            "WHERE (LOWER(email)=? OR LOWER(email) LIKE ? OR LOWER(contacts) LIKE ? "
            "OR LOWER(website) LIKE ?) AND status NOT IN ('replied','won','call')",
            (now, now, email, f"%@{dom}", f'%"{email}"%', f"%{dom}%"),
        ).rowcount

    logger.info("[opt_out] %s geblokkeerd (domein %s); %d lead(s) → lost", email, dom, updated)
    return not existing


def is_opted_out(email: str) -> bool:
    """Check of een adres (of zijn domein) op de blocklist staat.

    Wordt aangeroepen door de verzend-gate vóórdat een mail de deur uitgaat."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    dom = _domain(email)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM lead_opt_outs WHERE email = ? OR domain = ? LIMIT 1",
            (email, dom),
        ).fetchone()
    return row is not None


def detect_opt_out(text: str) -> bool:
    """Herkent een afmeldverzoek in vrije reply-tekst."""
    if not text:
        return False
    low = text.lower()
    return any(re.search(p, low) for p in _OPT_OUT_PATTERNS)


def opted_out_domains() -> Set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT domain FROM lead_opt_outs WHERE domain != ''").fetchall()
    return {r["domain"] for r in rows}


def list_opt_outs() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT email, domain, source, created_at FROM lead_opt_outs ORDER BY created_at DESC"
        ).fetchall()]

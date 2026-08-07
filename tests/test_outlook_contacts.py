"""Deterministische contact-lookup (agenda 'let op'-regel) + de budget-rem op
het vooraf genereren van conceptantwoorden voor urgente postvakmail.

lookup_contact() is puur SQL (geen LLM) — dezelfde afweging als build_pulse()
en opportunity_quality: een toets die zelf een gateway nodig heeft valt stil
precies wanneer je hem nodig hebt. ensure_suggested_replies() ís een LLM-call
en moet daarom dezelfde budget-/quota-rem eren als content_improver
(test_llm_budget.py) — dat is hier het zwaartepunt van de test.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from backend.domains.outlook import service as outlook
from backend.shared import outcomes
from backend.shared.database import get_conn


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _insert_email(conn, **overrides):
    email_id = overrides.pop("id", uuid.uuid4().hex)
    row = {
        "id": email_id, "subject": "Test", "from_email": "contact@example.com",
        "from_name": "Contact", "to_email": "vincent@weareimpact.nl",
        "received_at": _iso(1), "folder": "inbox", "is_replied": 0, "is_read": 0,
        "priority": 80, "ai_summary": "", "synced_at": _iso(0),
        "suggested_reply": "", "suggested_reply_dismissed": 0,
    }
    row.update(overrides)
    cols = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO outlook_emails ({cols}) VALUES ({placeholders})", list(row.values()))
    return email_id


def test_text_to_html_preserves_paragraphs_and_escapes():
    text = "Beste Jan,\n\nBedankt voor je mail. Ik kom er <snel> op terug.\n\nGroet, Vincent"
    out = outlook.text_to_html(text)
    assert out.count("<p>") == 3
    assert "&lt;snel&gt;" in out  # geen kapotte/geïnjecteerde HTML uit een concept
    assert "<br>" not in out.split("<p>")[1]  # één regel per alinea hier, geen <br> nodig


def test_lookup_contact_no_signal_returns_none_fields():
    info = outlook.lookup_contact("nobody-known@example.com")
    assert info == {"lead": None, "open_email": None, "last_heard_from": None}


def test_lookup_contact_finds_open_unanswered_email():
    email = f"open-{uuid.uuid4().hex}@example.com"
    with get_conn() as conn:
        _insert_email(conn, from_email=email, subject="Wacht op antwoord",
                       received_at=_iso(3), is_replied=0)
    info = outlook.lookup_contact(email)
    assert info["open_email"] is not None
    assert info["open_email"]["subject"] == "Wacht op antwoord"
    assert info["open_email"]["days"] in (2, 3)  # afronding


def test_lookup_contact_finds_lead_status():
    email = f"lead-{uuid.uuid4().hex}@example.com"
    now = _iso(0)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO leads (id, org_name, status, email, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, "Testbedrijf", "contacted", email, now, now),
        )
    info = outlook.lookup_contact(email)
    assert info["lead"] is not None
    assert info["lead"]["status"] == "contacted"


def test_ensure_suggested_replies_skips_when_budget_exceeded(monkeypatch):
    """Zelfde rem als content_improver (test_llm_budget.py): budget op =
    geen LLM-call, de mail blijft zonder concept staan i.p.v. te crashen."""
    monkeypatch.setattr(outcomes, "daily_llm_tokens", lambda: 9_999_999)
    monkeypatch.setattr("backend.shared.config.DAILY_TOKEN_BUDGET", 1_000_000)

    calls = {"n": 0}

    async def fake_generate(email_id, instructions=""):
        calls["n"] += 1
        return "zou nooit aangeroepen moeten worden"

    monkeypatch.setattr(outlook, "_generate_draft_text", fake_generate)

    with get_conn() as conn:
        email_id = _insert_email(conn, priority=90, received_at=_iso(0))

    made = asyncio.run(outlook.ensure_suggested_replies(limit=3))

    assert made == 0
    assert calls["n"] == 0, "geen LLM-call bij uitgeputte budget"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT suggested_reply FROM outlook_emails WHERE id = ?", (email_id,)
        ).fetchone()
    assert not row["suggested_reply"]


def test_ensure_suggested_replies_generates_within_budget(monkeypatch):
    monkeypatch.setattr(outcomes, "daily_llm_tokens", lambda: 0)
    monkeypatch.setattr("backend.shared.config.DAILY_TOKEN_BUDGET", 1_000_000)

    async def fake_generate(email_id, instructions=""):
        return "Concept: bedankt voor je mail, ik kom hierop terug."

    monkeypatch.setattr(outlook, "_generate_draft_text", fake_generate)

    with get_conn() as conn:
        email_id = _insert_email(conn, priority=95, received_at=_iso(0))

    # >=1, niet ==1: andere tests in dezelfde sessie-DB kunnen ook nog
    # onbehandelde urgente mails hebben liggen (limit=3 pakt er meerdere).
    made = asyncio.run(outlook.ensure_suggested_replies(limit=3))

    assert made >= 1
    with get_conn() as conn:
        row = conn.execute(
            "SELECT suggested_reply FROM outlook_emails WHERE id = ?", (email_id,)
        ).fetchone()
    assert row["suggested_reply"] == "Concept: bedankt voor je mail, ik kom hierop terug."

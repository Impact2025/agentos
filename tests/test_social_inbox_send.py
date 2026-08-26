"""Tests voor het social-inbox verzendpad (msg/{id}/approve + reject + mark-sent).

CLAUDE.md noemt publiceer/verstuur-gates expliciet als het meest kritieke
soort code in dit systeem — dit pad verstuurt écht (of markeert een
handmatige goedkeuring als klaar-om-te-plaatsen) en had tot nu toe geen
enkele test. Netwerk wordt nooit echt geraakt: svc.post_reply wordt
gemonkeypatcht per test.
"""
import asyncio
import uuid

import pytest

from backend.domains.social_inbox import router as social_inbox_router
from backend.shared import social_inbox as svc
from backend.shared.database import get_conn


def _make_inbox(platform="facebook"):
    iid = f"si_test_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO social_inboxes(id,project,platform,label,creds_json,"
            "brand_context,poll_minutes,enabled,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
            (iid, "TestProject", platform, "test", "{}", "TestProject", 30, 1),
        )
    return iid


def _make_msg(inbox_id, draft_body="Concept-antwoord.", status="pending_review",
              platform="facebook", manual=0):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO social_inbox_msg(inbox_id,platform,external_id,author_name,"
            "text,kind,draft_body,status,manual) VALUES(?,?,?,?,?,?,?,?,?)",
            (inbox_id, platform, uuid.uuid4().hex[:12], "Klant", "Vraag?",
             "question", draft_body, status, manual),
        )
        return cur.lastrowid


def _get_msg(msg_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM social_inbox_msg WHERE id=?", (msg_id,)
        ).fetchone()
    return dict(row) if row else None


# ── approve: echte verzending ───────────────────────────────────────────────

def test_approve_verstuurt_en_zet_status_op_sent(monkeypatch):
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id, draft_body="Bedankt voor je bericht!")

    calls = {}

    async def fake_post_reply(inbox, msg, text):
        calls["inbox"] = inbox
        calls["text"] = text
        return {"success": True, "url": "https://facebook.com/comment/123"}

    monkeypatch.setattr(social_inbox_router.svc, "post_reply", fake_post_reply)

    result = asyncio.run(social_inbox_router.approve_msg(msg_id))

    assert result["success"] is True
    assert result["url"] == "https://facebook.com/comment/123"
    assert calls["text"] == "Bedankt voor je bericht!"
    row = _get_msg(msg_id)
    assert row["status"] == "sent"
    assert row["sent_at"]


def test_approve_gebruikt_bewerkte_tekst_boven_concept(monkeypatch):
    inbox_id = _make_inbox("facebook")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO social_inbox_msg(inbox_id,platform,external_id,author_name,"
            "text,kind,draft_body,edited_body,status) VALUES(?,?,?,?,?,?,?,?,?)",
            (inbox_id, "facebook", uuid.uuid4().hex[:12], "Klant", "Vraag?",
             "question", "Origineel concept.", "Handmatig bewerkte tekst.", "edited"),
        )
        msg_id = cur.lastrowid

    seen = {}

    async def fake_post_reply(inbox, msg, text):
        seen["text"] = text
        return {"success": True, "url": ""}

    monkeypatch.setattr(social_inbox_router.svc, "post_reply", fake_post_reply)
    asyncio.run(social_inbox_router.approve_msg(msg_id))
    assert seen["text"] == "Handmatig bewerkte tekst."


# ── approve: manual-kanaal (LinkedIn/TikTok) ────────────────────────────────

def test_approve_op_manual_kanaal_zet_approved_niet_sent(monkeypatch):
    """Een kanaal zonder API-antwoordmogelijkheid mag nooit 'sent' beweren
    voordat de browserautomatisering het echt heeft geplaatst."""
    inbox_id = _make_inbox("linkedin")
    msg_id = _make_msg(inbox_id, draft_body="Reactie op LinkedIn.")

    async def fake_post_reply(inbox, msg, text):
        return {"success": False, "error": "manual", "manual": True}

    monkeypatch.setattr(social_inbox_router.svc, "post_reply", fake_post_reply)
    result = asyncio.run(social_inbox_router.approve_msg(msg_id))

    assert result["success"] is True
    assert result["manual"] is True
    row = _get_msg(msg_id)
    assert row["status"] == "approved"
    assert row["manual"] == 1
    assert not row["sent_at"]


def test_manual_approved_verschijnt_in_queued_en_mark_sent_maakt_het_waar():
    inbox_id = _make_inbox("linkedin")
    msg_id = _make_msg(inbox_id, status="approved", platform="linkedin", manual=1)

    queued = social_inbox_router.queued(project="TestProject", platform=None)
    assert any(m["id"] == msg_id for m in queued)

    result = social_inbox_router.mark_sent(msg_id)
    assert result["updated"] == 1
    row = _get_msg(msg_id)
    assert row["status"] == "sent"
    assert row["sent_at"]

    # Idempotent: een tweede bevestiging op een al-verzonden bericht raakt niets.
    result2 = social_inbox_router.mark_sent(msg_id)
    assert result2["updated"] == 0


# ── approve: falen ───────────────────────────────────────────────────────────

def test_approve_zonder_concept_stuurt_niets(monkeypatch):
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id, draft_body="")

    called = []

    async def fake_post_reply(inbox, msg, text):
        called.append(1)
        return {"success": True}

    monkeypatch.setattr(social_inbox_router.svc, "post_reply", fake_post_reply)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(social_inbox_router.approve_msg(msg_id))
    assert exc.value.status_code == 400
    assert not called
    row = _get_msg(msg_id)
    assert row["status"] == "pending_review"  # ongewijzigd, niets verzonden


def test_approve_bij_mislukte_verzending_blijft_status_ongewijzigd(monkeypatch):
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id, draft_body="Concept.")

    async def fake_post_reply(inbox, msg, text):
        return {"success": False, "error": "Token verlopen"}

    monkeypatch.setattr(social_inbox_router.svc, "post_reply", fake_post_reply)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(social_inbox_router.approve_msg(msg_id))
    assert exc.value.status_code == 400
    assert "Token verlopen" in exc.value.detail
    row = _get_msg(msg_id)
    assert row["status"] == "pending_review"  # geen valse 'sent'-belofte


def test_approve_op_reeds_verzonden_bericht_verstuurt_niet_opnieuw(monkeypatch):
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id, draft_body="Concept.", status="sent")

    called = []

    async def fake_post_reply(inbox, msg, text):
        called.append(1)
        return {"success": True}

    monkeypatch.setattr(social_inbox_router.svc, "post_reply", fake_post_reply)
    result = asyncio.run(social_inbox_router.approve_msg(msg_id))

    assert result == {"success": True, "detail": "Al verzonden"}
    assert not called  # geen dubbele plaatsing


def test_approve_onbekend_bericht_geeft_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(social_inbox_router.approve_msg(999999999))
    assert exc.value.status_code == 404


# ── reject / edit ────────────────────────────────────────────────────────────

def test_reject_zet_status_op_rejected():
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id)
    social_inbox_router.reject_msg(msg_id)
    assert _get_msg(msg_id)["status"] == "rejected"


def test_edit_zonder_tekst_faalt():
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        social_inbox_router.edit_msg(msg_id, {"text": "   "})


def test_edit_zet_draft_en_edited_body_en_status():
    inbox_id = _make_inbox("facebook")
    msg_id = _make_msg(inbox_id)
    social_inbox_router.edit_msg(msg_id, {"text": "Nieuwe tekst"})
    row = _get_msg(msg_id)
    assert row["draft_body"] == "Nieuwe tekst"
    assert row["edited_body"] == "Nieuwe tekst"
    assert row["status"] == "edited"


# ── dispatcher: linkedin/tiktok blijven 'manual', nooit een stille crash ───

def test_post_reply_dispatcher_linkedin_is_altijd_manual():
    result = asyncio.run(svc.post_reply({"platform": "linkedin"}, {}, "tekst"))
    assert result["manual"] is True
    assert result["success"] is False


def test_post_reply_dispatcher_onbekend_platform_faalt_expliciet():
    result = asyncio.run(svc.post_reply({"platform": "onbekend-kanaal"}, {}, "tekst"))
    assert result["success"] is False
    assert "Onbekend platform" in result["error"]

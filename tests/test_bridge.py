"""Bridge (cloud-companion): whitelist, payload-opbouw en de sync-cyclus.

De remote-kant (Vercel/Neon) wordt gemockt via httpx.MockTransport — deze tests
bewijzen het lokale contract: wat we pushen, dat besluiten alleen via de
whitelist lopen, en dat elk besluit een ack met uitslag krijgt.
"""
import json

import httpx
import pytest


# ── Whitelist (actions.apply_decision) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_action_is_refused():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision(
        {"item_kind": "content", "item_id": "x", "action": "publish_everything"})
    assert not ok
    assert "whitelist" in msg


@pytest.mark.asyncio
async def test_incomplete_decision_is_refused():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision({"item_kind": "content", "action": "approve"})
    assert not ok


@pytest.mark.asyncio
async def test_dismiss_unknown_kind_refused():
    from backend.domains.bridge import actions
    ok, _ = await actions.apply_decision(
        {"item_kind": "verzonnen", "item_id": "1", "action": "dismiss"})
    assert not ok


@pytest.mark.asyncio
async def test_dismiss_writes_inbox_dismissal(clean_tables):
    from backend.domains.bridge import actions
    from backend.shared.database import get_conn
    ok, _ = await actions.apply_decision(
        {"item_kind": "error", "item_id": "err-42", "action": "dismiss"})
    assert ok
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inbox_dismissals WHERE kind='error' AND ref_id='err-42'"
        ).fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_mail_edit_requires_text():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision(
        {"item_kind": "mail", "item_id": "1", "action": "edit", "payload": {"text": "  "}})
    assert not ok


@pytest.mark.asyncio
async def test_content_approve_routes_to_pipeline(monkeypatch):
    from backend.domains.bridge import actions
    from backend.domains.publish import content_pipeline
    calls = {}

    async def fake_approve(job_id, social_channels=None):
        calls["job_id"] = job_id
        calls["channels"] = social_channels
        return {"published": True}

    monkeypatch.setattr(content_pipeline, "approve_and_publish", fake_approve)
    ok, msg = await actions.apply_decision(
        {"item_kind": "content", "item_id": "job-1", "action": "approve",
         "payload": {"channels": ["LinkedIn"]}})
    assert ok
    assert calls == {"job_id": "job-1", "channels": ["linkedin"]}


@pytest.mark.asyncio
async def test_handler_exception_becomes_failed_ack(monkeypatch):
    from backend.domains.bridge import actions
    from backend.domains.publish import content_pipeline

    async def boom(job_id, social_channels=None):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(content_pipeline, "approve_and_publish", boom)
    ok, msg = await actions.apply_decision(
        {"item_kind": "content", "item_id": "job-1", "action": "approve"})
    assert not ok
    assert "gateway down" in msg


# ── Payload-opbouw ──────────────────────────────────────────────────────────

def test_build_push_payload_shape(clean_tables):
    from backend.domains.bridge import service
    payload = service.build_push_payload()
    assert "generated_at" in payload
    assert isinstance(payload["items"], list)
    assert isinstance(payload["briefing"], dict)
    for it in payload["items"]:
        assert it["key"] == f"{it['dismiss_kind']}:{it['item_id']}"


# ── Sync-cyclus tegen een gemockte remote ───────────────────────────────────

def _mock_remote(decisions, log):
    def handler(request: httpx.Request) -> httpx.Response:
        op = request.url.params.get("op")
        log.append((request.method, op))
        if op == "push":
            return httpx.Response(200, json={"ok": True})
        if op == "decisions":
            return httpx.Response(200, json={"decisions": decisions})
        if op == "ack":
            log.append(("acks", json.loads(request.content)["acks"]))
            return httpx.Response(200, json={"ok": True})
        if op == "notes":
            return httpx.Response(200, json={"notes": []})
        if op == "notes-ack":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(400, json={"error": "?"})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_sync_once_applies_and_acks(monkeypatch, clean_tables):
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "https://remote.test")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "t0ken")

    log = []
    decisions = [{"id": 9, "item_kind": "error", "item_id": "e-1",
                  "action": "dismiss", "payload": {}}]
    transport = _mock_remote(decisions, log)
    real_client = httpx.AsyncClient

    def client_factory(**kw):
        kw["transport"] = transport
        return real_client(**kw)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
    summary = await service.sync_once()

    assert summary["ok"], summary
    assert summary["applied"] == 1 and summary["failed"] == 0
    acks = next(v for k, v in log if k == "acks")
    assert acks == [{"id": 9, "status": "applied", "result": "Weggeklikt"}]
    # het dismiss-besluit is echt lokaal geland
    from backend.shared.database import get_conn
    with get_conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM inbox_dismissals WHERE kind='error' AND ref_id='e-1'"
        ).fetchone()


@pytest.mark.asyncio
async def test_sync_once_failed_decision_logs_error_card(monkeypatch, clean_tables):
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "https://remote.test")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "t0ken")

    log = []
    decisions = [{"id": 3, "item_kind": "content", "item_id": "nope",
                  "action": "niet_bestaand", "payload": {}}]
    transport = _mock_remote(decisions, log)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=transport, **kw))
    summary = await service.sync_once()

    assert summary["failed"] == 1
    acks = next(v for k, v in log if k == "acks")
    assert acks[0]["status"] == "failed"
    from backend.shared.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM activity_log WHERE action='remote_decision_failed' "
            "AND status='error'").fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_sync_disabled_without_config(monkeypatch):
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "")
    result = await service.sync_once()
    assert not result["ok"]

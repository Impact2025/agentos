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
async def test_content_approve_without_channels_posts_no_social(monkeypatch):
    """Zonder expliciete kanaalkeuze publiceert de cloud alleen de website."""
    from backend.domains.bridge import actions
    from backend.domains.publish import content_pipeline
    calls = {}

    async def fake_approve(job_id, social_channels=None):
        calls["channels"] = social_channels
        return {"published": True}

    monkeypatch.setattr(content_pipeline, "approve_and_publish", fake_approve)
    ok, _ = await actions.apply_decision(
        {"item_kind": "content", "item_id": "job-1", "action": "approve", "payload": {}})
    assert ok
    assert calls["channels"] == []


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


# ── Commando's: werk aanzwengelen vanaf de telefoon ─────────────────────────

@pytest.mark.asyncio
async def test_unknown_command_is_refused():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision(
        {"item_kind": "command", "item_id": "x", "action": "rm_rf", "payload": {}})
    assert not ok
    assert "Onbekend commando" in msg


@pytest.mark.asyncio
async def test_command_routes_to_iris_action(monkeypatch):
    """Commando's hergebruiken Iris' hendels — inclusief hun klemmen."""
    from backend.domains.bridge import actions
    from backend.domains.iris import actions as iris_actions
    calls = {}

    async def fake_content_run(site, count, reason):
        calls.update(site=site, count=count, reason=reason)
        return "2 artikelen in de Wachtrij"

    monkeypatch.setattr(iris_actions, "content_run", fake_content_run)
    ok, msg = await actions.apply_decision(
        {"item_kind": "command", "item_id": "content_run", "action": "content_run",
         "payload": {"site": "weareimpact", "count": 2}})
    assert ok and "Wachtrij" in msg
    assert calls["site"] == "weareimpact" and calls["count"] == 2


@pytest.mark.asyncio
async def test_command_without_site_is_refused():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision(
        {"item_kind": "command", "item_id": "content_run", "action": "content_run",
         "payload": {}})
    assert not ok
    assert "site" in msg.lower()


@pytest.mark.asyncio
async def test_command_failure_becomes_failed_ack(monkeypatch):
    from backend.domains.bridge import actions
    from backend.domains.iris import actions as iris_actions

    async def boom(count, reason):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(iris_actions, "outreach_run", boom)
    ok, msg = await actions.apply_decision(
        {"item_kind": "command", "item_id": "outreach_run", "action": "outreach_run"})
    assert not ok
    assert "gateway down" in msg


@pytest.mark.asyncio
async def test_context_refresh_clears_cache(clean_tables):
    """Verse cijfers afdwingen onderweg = de cache legen, niets meer."""
    from backend.domains.bridge import actions
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bridge_context_cache (key, payload, updated_at) VALUES (?,?,?)",
            ("analytics", "{}", "2026-07-23T10:00:00+00:00"))
    ok, _ = await actions.apply_decision(
        {"item_kind": "command", "item_id": "context_refresh",
         "action": "context_refresh", "payload": {"sections": ["analytics"]}})
    assert ok
    with get_conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM bridge_context_cache WHERE key='analytics'").fetchone() is None


@pytest.mark.asyncio
async def test_scheduler_error_can_be_dismissed(clean_tables):
    """Scheduler-kaarten stonden wél in de inbox maar niet in de whitelist —
    zelfs 'Wegklikken' gaf daardoor een fout op de telefoon."""
    from backend.domains.bridge import actions
    ok, _ = await actions.apply_decision(
        {"item_kind": "scheduler", "item_id": "gsc_sync", "action": "dismiss"})
    assert ok


# ── Contextopbouw ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_sections_carry_status(clean_tables):
    from backend.domains.bridge import context
    ctx = await context.build_context()
    for name in ("mail", "agenda", "analytics", "seo"):
        assert ctx[name].get("status") in ("ok", "off", "error"), name
    assert "good" in ctx["pulse"] and "bad" in ctx["pulse"]


@pytest.mark.asyncio
async def test_context_section_falls_back_to_stale_cache(clean_tables, monkeypatch):
    """Een integratie die hapert mag de vorige cijfers niet wissen — oude
    cijfers mét datum zijn bruikbaar, een leeg scherm niet."""
    from backend.domains.bridge import context
    context._cache_write("proef", {"status": "ok", "waarde": 42})
    monkeypatch.setattr(context, "_cache_read",
                        lambda key, ttl: ({"status": "ok", "waarde": 42}, False))

    def boom():
        raise RuntimeError("api plat")

    out = await context._section("proef", 60, boom)
    assert out["waarde"] == 42 and out["stale"] is True


def test_pulse_mail_backlog_no_longer_flagged():
    # Mail-achterstand heeft een eigen scherm (Postvak, 14c/14d in CLAUDE.md);
    # de pulse dupliceerde die twee cijfers als losse waarschuwingen zonder
    # nieuwe informatie. Vincent liet ze op 25 aug 2026 expliciet verwijderen.
    from backend.domains.bridge import context
    pulse = context.build_pulse({
        "mail": {"status": "ok", "backlog": 20,
                 "oldest_open": {"days": 9, "from": "klant", "subject": "offerte"},
                 "week": {}},
    })
    assert not any(b["area"] == "mail" for b in pulse["bad"])


def test_pulse_marks_unavailable_sections():
    from backend.domains.bridge import context
    pulse = context.build_pulse({"mail": {"status": "off"}, "agenda": {"status": "ok"}})
    assert "mail" in pulse["unavailable"]


def test_free_gaps_ignore_the_past():
    """Een gat van 09:00–11:00 is om 14:00 geen aanbod meer."""
    from datetime import datetime
    from backend.domains.bridge import context
    day = datetime(2026, 7, 23, 14, 0, tzinfo=context.TZ)
    gaps = context._free_gaps([], day, not_before=day)
    assert gaps == [{"start": "14:00", "end": "18:00"}]


def test_free_gaps_skip_short_windows():
    from datetime import datetime
    from backend.domains.bridge import context
    day = datetime(2026, 7, 23, 8, 0, tzinfo=context.TZ)
    events = [{"start": "2026-07-23T08:30:00+02:00", "end": "2026-07-23T17:45:00+02:00",
               "all_day": False, "declined": False}]
    # 08:00-08:30 (30 min) en 17:45-18:00 (15 min) zijn allebei te kort.
    assert context._free_gaps(events, day) == []


# ── Payload-opbouw ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_push_payload_shape(clean_tables):
    from backend.domains.bridge import service
    payload = await service.build_push_payload()
    assert "generated_at" in payload
    assert isinstance(payload["items"], list)
    assert isinstance(payload["briefing"], dict)
    assert isinstance(payload["context"], dict)
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


# ── Falen: nooit stil ───────────────────────────────────────────────────────
#
# Aanleiding (27 jul 2026): BRIDGE_REMOTE_URL stond leeg terwijl BRIDGE_TOKEN
# gevuld was. De scheduler-job sloeg elke 3 minuten stil over, en het énige
# spoor was op de telefoon: "171u offline" boven een week oude lijst die er
# precies zo uitzag als een verse. Deze tests leggen vast dat een kapotte
# bridge lokaal zichtbaar wordt — en dat een blip dat níét doet.

@pytest.fixture
def clean_streaks():
    from backend.shared.database import get_conn
    yield
    with get_conn() as c:
        c.execute("DELETE FROM agent_failure_streaks WHERE key LIKE 'bridge:%'")


def test_config_state_kent_drie_standen(monkeypatch):
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "")
    assert service.config_state() == "off"
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "t0ken")
    assert service.config_state() == "partial"   # dit was de storing
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "https://remote.test")
    assert service.config_state() == "on"


def test_half_ingevulde_bridge_meldt_zich(monkeypatch, clean_tables, clean_streaks):
    """Mens-alleen fout: meteen melden, en niet elke ronde opnieuw."""
    from backend.domains.bridge import service
    from backend.shared.database import get_conn
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "t0ken")

    service.report_misconfiguration()
    service.report_misconfiguration()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE action='niet_geconfigureerd' "
            "AND status='error'").fetchall()
    assert len(rows) == 1, "één storing = één kaart, ook na herhaalde rondes"
    assert "BRIDGE_REMOTE_URL" in rows[0]["detail"]
    assert "BRIDGE_REMOTE_URL" in (rows[0]["next_step"] or "")


# De echte client vastleggen vóór er gepatcht wordt: een test die twee keer
# achter elkaar patcht (falen → herstel) zou anders de mock om de mock wikkelen.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _use_transport(monkeypatch, service, transport):
    monkeypatch.setattr(service.httpx, "AsyncClient",
                        lambda **kw: _REAL_ASYNC_CLIENT(transport=transport, **kw))


def _failing_remote(monkeypatch, service, *, status=None, exc=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if exc:
            raise exc
        return httpx.Response(status, json={"error": "nope"})
    _use_transport(monkeypatch, service, httpx.MockTransport(handler))


def _error_cards(action="sync_failed"):
    from backend.shared.database import get_conn
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM activity_log WHERE action=? AND status='error'",
            (action,)).fetchall()


@pytest.mark.asyncio
async def test_blip_escaleert_pas_na_drie_pogingen(monkeypatch, clean_tables, clean_streaks):
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "https://remote.test")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "t0ken")
    _failing_remote(monkeypatch, service, exc=httpx.ConnectError("geen netwerk"))

    for _ in range(2):
        summary = await service.sync_once()
        assert not summary["ok"]
    assert _error_cards() == [], "wifi weg op de trein is geen inbox-item"

    await service.sync_once()
    cards = _error_cards()
    assert len(cards) == 1, "maar aanhoudend falen wél"
    assert cards[0]["detail"].strip().endswith("geen netwerk") or "geen netwerk" in cards[0]["detail"]


@pytest.mark.asyncio
async def test_verkeerd_token_meldt_meteen_met_concrete_stap(monkeypatch, clean_tables, clean_streaks):
    """Wachten repareert geen token-mismatch, dus escaleert die direct."""
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "https://remote.test")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "fout")
    _failing_remote(monkeypatch, service, status=401)

    summary = await service.sync_once()
    assert not summary["ok"] and summary["failure_class"] == "auth"
    cards = _error_cards()
    assert len(cards) == 1
    assert "BRIDGE_TOKEN" in (cards[0]["next_step"] or "")


@pytest.mark.asyncio
async def test_herstel_meldt_zich_en_wist_de_reeks(monkeypatch, clean_tables, clean_streaks):
    from backend.domains.bridge import service
    monkeypatch.setattr(service, "BRIDGE_REMOTE_URL", "https://remote.test")
    monkeypatch.setattr(service, "BRIDGE_TOKEN", "t0ken")
    _failing_remote(monkeypatch, service, exc=httpx.ConnectError("geen netwerk"))
    for _ in range(3):
        await service.sync_once()
    assert service.failure_streak().get("fail_count") == 3

    log = []
    _use_transport(monkeypatch, service, _mock_remote([], log))
    summary = await service.sync_once()

    assert summary["ok"], summary
    assert service.failure_streak() == {}, "storing voorbij = reeks leeg"
    from backend.shared.database import get_conn
    with get_conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM activity_log WHERE action='sync_hersteld'").fetchone()

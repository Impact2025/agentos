"""Tests voor de 'snel toevoegen'-flow in de Agenda-tab: calendar/agent.py:
propose_from_text() (gedeeld door de webUI, de WhatsApp-bridge en klant-Iris)
en de reistijd-module (travel.py), plus de nieuwe /api/calendar/command-route.
"""
import asyncio

import pytest


def _client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_proposals():
    from backend.shared.database import get_conn
    with get_conn() as c:
        c.execute("DELETE FROM calendar_proposals WHERE mailbox_id='iris-command'")
    yield
    with get_conn() as c:
        c.execute("DELETE FROM calendar_proposals WHERE mailbox_id='iris-command'")


# ── travel.py ────────────────────────────────────────────────────────────

def test_travel_niet_geconfigureerd_geeft_none(monkeypatch):
    from backend.domains.calendar import travel
    from backend.shared import config
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setattr(config, "AGENDA_HOME_ADDRESS", "")
    assert travel.configured() is False
    assert travel.travel_minutes_sync("Amsterdam") is None


def test_travel_async_wrapper_zonder_configuratie(monkeypatch):
    from backend.domains.calendar import travel
    from backend.shared import config
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "")
    result = asyncio.run(travel.travel_minutes("Amsterdam"))
    assert result is None


def test_travel_mislukte_call_geeft_none_geen_crash(monkeypatch):
    from backend.domains.calendar import travel
    from backend.shared import config
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "AGENDA_HOME_ADDRESS", "Amsterdam")

    class _BoomClient:
        def __init__(self, *a, **k):
            raise RuntimeError("netwerk weg")

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("netwerk weg")))
    assert travel.travel_minutes_sync("Rotterdam") is None


# ── propose_from_text ────────────────────────────────────────────────────

def test_propose_from_text_leeg_geeft_foutmelding():
    from backend.domains.calendar import agent as cal_agent
    ok, msg, pid = asyncio.run(cal_agent.propose_from_text(""))
    assert ok is False
    assert pid is None


def test_propose_from_text_maakt_pending_review_voorstel():
    from backend.domains.calendar import agent as cal_agent
    from backend.shared.database import get_conn

    ok, msg, pid = asyncio.run(cal_agent.propose_from_text("dinsdag 14 uur bij de tandarts"))
    assert ok is True
    assert pid is not None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM calendar_proposals WHERE id=?", (pid,)).fetchone()
    assert row is not None
    assert row["status"] == "pending_review"
    assert row["mailbox_id"] == "iris-command"


def test_propose_from_text_dedupe_binnen_15_minuten():
    from backend.domains.calendar import agent as cal_agent

    ok1, _, pid1 = asyncio.run(cal_agent.propose_from_text("vrijdag 10 uur teamoverleg"))
    assert ok1 is True
    ok2, msg2, pid2 = asyncio.run(cal_agent.propose_from_text("vrijdag 10 uur teamoverleg"))
    assert ok2 is False
    assert pid2 is None
    assert "bestaat al" in msg2


def test_propose_from_text_gebruikt_vaste_buffer_zonder_maps_configuratie(monkeypatch):
    """Zonder GOOGLE_MAPS_API_KEY moet de vaste 30-minuten-buffer gelden voor
    een niet-thuis-locatie, exact zoals de mail-flow al deed."""
    from backend.domains.calendar import agent as cal_agent
    from backend.shared.database import get_conn

    ok, _, pid = asyncio.run(cal_agent.propose_from_text("donderdag 15 uur bij de notaris"))
    assert ok is True
    with get_conn() as conn:
        row = conn.execute("SELECT travel_buffer_min FROM calendar_proposals WHERE id=?", (pid,)).fetchone()
    assert row["travel_buffer_min"] == cal_agent._TRAVEL_BUFFER_MIN


def test_propose_from_text_geen_buffer_voor_thuisbasis():
    from backend.domains.calendar import agent as cal_agent
    from backend.shared.database import get_conn

    ok, _, pid = asyncio.run(cal_agent.propose_from_text("maandag 09 uur online overleg"))
    assert ok is True
    with get_conn() as conn:
        row = conn.execute("SELECT travel_buffer_min FROM calendar_proposals WHERE id=?", (pid,)).fetchone()
    assert row["travel_buffer_min"] == 0


# ── bridge/actions.py wrapper ────────────────────────────────────────────

def test_bridge_cmd_calendar_add_is_dunne_wrapper():
    """Regressie: dit commando mag niet meer zijn eigen parse/insert-logica
    hebben — dat gaf op 25 aug 2026 twee kopieen van dezelfde ~90 regels."""
    from backend.domains.bridge import actions as bridge_actions

    ok, msg = asyncio.run(bridge_actions._cmd_calendar_add({"text": "woensdag 11 uur klantgesprek"}))
    assert ok is True
    assert "Keur goed" in msg


# ── HTTP-route ───────────────────────────────────────────────────────────

def test_command_route_maakt_voorstel():
    r = _client().post("/api/calendar/command", json={"text": "zaterdag 12 uur lunch"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["proposal_id"] is not None


def test_command_route_geeft_400_op_onleesbare_tekst():
    r = _client().post("/api/calendar/command", json={"text": ""})
    assert r.status_code == 400


def test_proposals_route_toont_nieuw_voorstel():
    from backend.domains.calendar import agent as cal_agent
    asyncio.run(cal_agent.propose_from_text("zondag 16 uur verjaardag"))
    r = _client().get("/api/calendar/proposals")
    assert r.status_code == 200
    titles = [p["title"] for p in r.json()["proposals"]]
    assert any("verjaardag" in t.lower() for t in titles)

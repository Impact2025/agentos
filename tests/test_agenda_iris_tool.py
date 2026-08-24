"""End-to-end test: Iris' agenda-tool (cloud plan_agenda -> local bridge).

Spiegelt de echte flow:
  1. De cloud-tool zet een 'command'/'calendar_add'-decision in de rij.
  2. De lokale bridge haalt die op via apply_decision -> _cmd_calendar_add.
  3. Resultaat: een calendar_proposals-rij met correct geparste velden
     (incl. recur_weekday + recur_count voor een eindige reeks).

'Nu' wordt bevroren op dinsdag 11 augustus 2026 — zelfde mechanisme en datum
als tests/test_agenda_opdracht.py — zodat "dinsdag 18 augustus" niet
date-flaky wordt zodra de kalender voorbij die datum schuift. `_cmd_calendar_
add` importeert `nl_command` lazy binnen de functie, maar dat resolvet naar
hetzelfde modulepad (backend.domains.calendar.nl_command) als hier gepatcht
wordt, dus de monkeypatch werkt ook via de bridge-laag heen.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.domains.bridge import actions as bridge_actions
from backend.domains.calendar import nl_command as nlc
from backend.shared.database import get_conn

TZ = ZoneInfo("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def dinsdag(monkeypatch):
    """Bevries 'nu' op dinsdag 11 augustus 2026, 11:00 (zomertijd)."""
    monkeypatch.setattr(nlc, "_amsterdam_now",
                        lambda: datetime(2026, 8, 11, 11, 0, tzinfo=TZ))


def test_iris_tool_eindige_reeks_landt_als_voorstel():
    text = "Ik wil de komende 6 weken op maandag van 08.30 t/m 10.00 blokken voor Focustijd"
    decision = {
        "item_kind": "command",
        "action": "calendar_add",
        "item_id": "calendar_add",
        "payload": {"text": text},
    }
    ok, msg = asyncio.run(bridge_actions.apply_decision(decision))
    assert ok, msg

    with get_conn() as conn:
        row = conn.execute(
            "SELECT title, proposed_start, proposed_end, recur_weekday, recur_count, status "
            "FROM calendar_proposals ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row["title"] == "Focustijd (wekelijks)", row["title"]
    assert row["recur_weekday"] == 0, row["recur_weekday"]      # maandag
    assert row["recur_count"] == 6, row["recur_count"]          # komende 6 weken
    assert row["status"] == "pending_review"
    # Tijdvak 08:30-10:00, niet 08:30-09:00 (de oude bug)
    assert "08:30" in row["proposed_start"], row["proposed_start"]
    assert "10:00" in row["proposed_end"], row["proposed_end"]


def test_enkele_afspraak_zonder_recur():
    text = "dinsdag 18 augustus om 12.15 naar de tandarts"
    decision = {"item_kind": "command", "action": "calendar_add",
                "item_id": "calendar_add", "payload": {"text": text}}
    ok, _ = asyncio.run(bridge_actions.apply_decision(decision))
    assert ok
    with get_conn() as conn:
        row = conn.execute(
            "SELECT title, recur_weekday, recur_count FROM calendar_proposals "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["title"] == "Tandarts"
    assert row["recur_weekday"] == -1
    assert row["recur_count"] == -1


def test_dezelfde_opdracht_twee_keer_geeft_geen_dubbel_voorstel():
    """Gemeten 11 aug 2026: 'blok alle dinsdagen tussen 09.00 en 10.00' twee
    minuten na elkaar ingediend leverde twee losse voorstellen op, allebei
    later goedgekeurd — een wekelijkse dinsdagblokkade stond dubbel geboekt."""
    with get_conn() as conn:
        voor = conn.execute("SELECT COUNT(*) FROM calendar_proposals").fetchone()[0]

    text = "blok alle dinsdagen tussen 09.00 en 10.00"
    decision = {"item_kind": "command", "action": "calendar_add",
                "item_id": "calendar_add", "payload": {"text": text}}
    ok1, msg1 = asyncio.run(bridge_actions.apply_decision(decision))
    assert ok1, msg1
    ok2, msg2 = asyncio.run(bridge_actions.apply_decision(decision))
    assert not ok2
    assert "bestaat al" in msg2

    with get_conn() as conn:
        na = conn.execute("SELECT COUNT(*) FROM calendar_proposals").fetchone()[0]
    assert na - voor == 1

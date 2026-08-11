"""End-to-end test: Iris' agenda-tool (cloud plan_agenda -> local bridge).

Spiegelt de echte flow:
  1. De cloud-tool zet een 'command'/'calendar_add'-decision in de rij.
  2. De lokale bridge haalt die op via apply_decision -> _cmd_calendar_add.
  3. Resultaat: een calendar_proposals-rij met correct geparste velden
     (incl. recur_weekday + recur_count voor een eindige reeks).
"""
import asyncio
from backend.domains.bridge import actions as bridge_actions
from backend.shared.database import get_conn


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

"""Focusblok-uitzondering (Vincent, 26 aug 2026): een Focusblok mag wijken voor
een afspraak met een klant zolang dat >=24u vooraf wordt vastgelegd — binnen
24u blijft het blok beschermd. Ziet dezelfde regel toegepast op zowel het
lokale-overlap-pad (al goedgekeurde, wekelijkse Focusblokken in onze eigen
calendar_proposals-tabel) als het Google-freeBusy-pad (echte agenda-conflicten,
waar de titel via get_events_range moet worden nageslagen).
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.domains.calendar import agent as A
from backend.domains.calendar import focus_rules
from backend.domains.calendar import service as svc
from backend.shared.database import get_conn

TZ = ZoneInfo("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def _schone_agenda_tabel():
    with get_conn() as c:
        c.execute("DELETE FROM calendar_proposals")
    yield


def _boek_wekelijks_focusblok(weekday: int, start_hhmm: tuple, end_hhmm: tuple,
                              eerste_week: datetime, titel="Focusblok II (wekelijks)"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calendar_proposals (mailbox_id, inbox_id, from_addr, "
            "subject, title, proposed_start, proposed_end, recur_weekday, "
            "recur_count, status, conflict_checked, rationale, created_at) "
            "VALUES ('iris-command', 0, 'iris-command', 'x', ?, ?, ?, ?, -1, "
            "'booked', 'ok', '', datetime('now'))",
            (titel,
             eerste_week.replace(hour=start_hhmm[0], minute=start_hhmm[1]).isoformat(),
             eerste_week.replace(hour=end_hhmm[0], minute=end_hhmm[1]).isoformat(),
             weekday),
        )


# ── focus_rules zelf ─────────────────────────────────────────────────────────

def test_is_focus_title_herkent_beide_bestaande_namen():
    assert focus_rules.is_focus_title("Focustijd Blok I")
    assert focus_rules.is_focus_title("Focusblok II (wekelijks)")
    assert not focus_rules.is_focus_title("Afspraak met Marleen")
    assert not focus_rules.is_focus_title(None)


def test_overridable_grens_op_precies_24_uur():
    now = datetime(2026, 9, 1, 9, 0, tzinfo=TZ)
    assert focus_rules.overridable(now + timedelta(hours=24), now) is True
    assert focus_rules.overridable(now + timedelta(hours=23, minutes=59), now) is False
    assert focus_rules.overridable(None, now) is False


# ── Lokaal-overlap-pad (al geboekte wekelijkse Focusblokken) ────────────────

def test_focusblok_wijkt_bij_voldoende_voorbereidingstijd(monkeypatch):
    """Maandag-focusblok 12:30-14:00. Een klantafspraak op een toekomstige
    maandag, >=24u van tevoren vastgelegd, mag het blok overschrijven."""
    async def vrij(start, end):
        return []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "get_busy_times", vrij)

    eerste_week = datetime(2026, 8, 24, tzinfo=TZ)  # maandag
    _boek_wekelijks_focusblok(0, (12, 30), (14, 0), eerste_week)

    # 'Nu' is ruim een week vóór de botsende maandag -> >=24u vooraf.
    monkeypatch.setattr(A, "_amsterdam_now",
                        lambda: datetime(2026, 9, 1, 9, 0, tzinfo=TZ))
    doel_maandag = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)  # maandag, valt in het blok

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO calendar_proposals (mailbox_id, inbox_id, from_addr, "
            "subject, title, proposed_start, proposed_end, status, "
            "conflict_checked, rationale, created_at) VALUES "
            "('iris-command', 0, 'iris-command', 'x', 'Afspraak met David Witte', "
            "?, ?, 'pending_review', 'ok', '', datetime('now'))",
            (doel_maandag.isoformat(), (doel_maandag + timedelta(hours=1)).isoformat()),
        )
        pid = cur.lastrowid

    async def fake_block(**kw):
        return {"event_id": "evt-focus-override", "html_link": "https://cal/x"}
    monkeypatch.setattr(svc, "block_time", fake_block)

    res = A.approve_proposal(pid)
    assert res["ok"] is True, res.get("error")


def test_focusblok_blijft_staan_binnen_24_uur(monkeypatch):
    """Dezelfde botsing, maar nu wordt de afspraak minder dan 24u vóór het
    focusblok voorgesteld -> het blok blijft beschermd en goedkeuren blokkeert."""
    async def vrij(start, end):
        return []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "get_busy_times", vrij)

    eerste_week = datetime(2026, 8, 24, tzinfo=TZ)
    _boek_wekelijks_focusblok(0, (12, 30), (14, 0), eerste_week)

    doel_maandag = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    # 'Nu' ligt slechts 10 uur voor het blok.
    monkeypatch.setattr(A, "_amsterdam_now",
                        lambda: doel_maandag - timedelta(hours=10))

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO calendar_proposals (mailbox_id, inbox_id, from_addr, "
            "subject, title, proposed_start, proposed_end, status, "
            "conflict_checked, rationale, created_at) VALUES "
            "('iris-command', 0, 'iris-command', 'x', 'Afspraak met David Witte', "
            "?, ?, 'pending_review', 'ok', '', datetime('now'))",
            (doel_maandag.isoformat(), (doel_maandag + timedelta(hours=1)).isoformat()),
        )
        pid = cur.lastrowid

    res = A.approve_proposal(pid)
    assert res["ok"] is False
    assert res["code"] == "conflict_found"


def test_niet_focusblok_lokale_overlap_blokkeert_altijd(monkeypatch):
    """Een gewone (niet-Focus) al geboekte afspraak blijft hard blokkeren, ook
    ruim vooraf — de uitzondering geldt uitsluitend voor Focusblokken."""
    async def vrij(start, end):
        return []
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "get_busy_times", vrij)

    eerste_week = datetime(2026, 8, 24, tzinfo=TZ)
    _boek_wekelijks_focusblok(0, (12, 30), (14, 0), eerste_week, titel="Teamoverleg (wekelijks)")

    monkeypatch.setattr(A, "_amsterdam_now",
                        lambda: datetime(2026, 9, 1, 9, 0, tzinfo=TZ))
    doel_maandag = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO calendar_proposals (mailbox_id, inbox_id, from_addr, "
            "subject, title, proposed_start, proposed_end, status, "
            "conflict_checked, rationale, created_at) VALUES "
            "('iris-command', 0, 'iris-command', 'x', 'Afspraak met David Witte', "
            "?, ?, 'pending_review', 'ok', '', datetime('now'))",
            (doel_maandag.isoformat(), (doel_maandag + timedelta(hours=1)).isoformat()),
        )
        pid = cur.lastrowid

    res = A.approve_proposal(pid)
    assert res["ok"] is False
    assert res["code"] == "conflict_found"


# ── Google-freeBusy-pad (titel via get_events_range) ────────────────────────

def test_google_conflict_met_focusblok_titel_wijkt_op_tijd(monkeypatch):
    start = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    async def bezet(s, e):
        return [{"start": start.isoformat(), "end": end.isoformat()}]

    async def events_range(s, e):
        return {"events": [{"start": start.isoformat(), "end": end.isoformat(),
                            "summary": "Focusblok II (wekelijks)"}],
                "calendars": [], "unreachable": []}

    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "get_busy_times", bezet)
    monkeypatch.setattr(svc, "get_events_range", events_range)
    monkeypatch.setattr(A, "_amsterdam_now", lambda: start - timedelta(days=3))

    status, overlaps = A._free_busy_conflict(start, end)
    assert status == "ok"
    assert overlaps == []


def test_google_conflict_zonder_focusblok_titel_blijft_conflict(monkeypatch):
    start = datetime(2026, 9, 7, 13, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    async def bezet(s, e):
        return [{"start": start.isoformat(), "end": end.isoformat()}]

    async def events_range(s, e):
        return {"events": [{"start": start.isoformat(), "end": end.isoformat(),
                            "summary": "Kennismaking Vincent | Wouter"}],
                "calendars": [], "unreachable": []}

    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "get_busy_times", bezet)
    monkeypatch.setattr(svc, "get_events_range", events_range)
    monkeypatch.setattr(A, "_amsterdam_now", lambda: start - timedelta(days=3))

    status, overlaps = A._free_busy_conflict(start, end)
    assert status == "ok"
    assert len(overlaps) == 1

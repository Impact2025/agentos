"""Persoonlijke rituelen — upsert-op-datum/week, streak-berekening en de
briefing-context die Iris leest.

`get_briefing_context()` mag nooit een exception laten ontsnappen: `iris/
service.py:gather_context()` roept hem aan in een try/except, maar de functie
zelf moet ook zonder ingevulde data een bruikbaar (leeg) dict teruggeven —
anders krijgt Iris een halve state in plaats van "nog niets ingevuld".
"""
from datetime import datetime, timedelta

import pytest

from backend.domains.rituals.models import ensure_schema
from backend.domains.rituals.service import RitualsService


@pytest.fixture
def svc(clean_tables):
    ensure_schema()
    from backend.shared.database import get_conn
    with get_conn() as conn:
        for t in ("ritual_morning", "ritual_evening", "ritual_weekly_start",
                  "ritual_weekly_review", "ritual_wins", "ritual_focus_sessions",
                  "ritual_goals"):
            conn.execute(f"DELETE FROM {t}")
    return RitualsService()


def test_save_morning_is_upsert_on_date(svc):
    svc.save_morning("2026-08-10", {"intentie": "eerste versie", "energyLevel": 6})
    svc.save_morning("2026-08-10", {"intentie": "overschreven", "energyLevel": 9})

    row = svc.get_morning("2026-08-10")
    assert row["intentie"] == "overschreven"
    assert row["energy_level"] == 9

    from backend.shared.database import get_conn
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM ritual_morning WHERE date = ?", ("2026-08-10",)
        ).fetchone()["n"]
    assert count == 1


def test_save_evening_roundtrips_json_fields(svc):
    svc.save_evening("2026-08-10", {
        "whatWentWell": "goed", "biggestWin": "win", "tomorrowTop3": ["a", "b", "c"],
        "adhdScores": {"onrust": 2},
    })
    row = svc.get_evening("2026-08-10")
    assert row["tomorrow_top3"] == ["a", "b", "c"]
    assert row["adhd_scores"] == {"onrust": 2}


def test_weekly_start_and_review_upsert_on_year_week(svc):
    svc.save_weekly_start(2026, 32, {"weekIntention": "focus op X", "mainGoals": ["a"]})
    svc.save_weekly_start(2026, 32, {"weekIntention": "focus op Y", "mainGoals": ["a", "b"]})
    row = svc.get_weekly_start(2026, 32)
    assert row["week_intention"] == "focus op Y"
    assert row["main_goals"] == ["a", "b"]

    svc.save_weekly_review(2026, 32, {"wins": ["overwinning 1"], "productivityScore": 8})
    review = svc.get_weekly_review(2026, 32)
    assert review["wins"] == ["overwinning 1"]
    assert review["productivity_score"] == 8


def test_streak_counts_consecutive_days_from_today(svc):
    today = datetime.now()
    for offset in (0, 1, 2):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        svc.save_morning(d, {"intentie": f"dag -{offset}"})
    # Gat op dag -3 (niet ingevuld)
    d4 = (today - timedelta(days=4)).strftime("%Y-%m-%d")
    svc.save_morning(d4, {"intentie": "te oud, telt niet mee"})

    assert svc.get_streaks()["morning"] == 3


def test_streak_is_zero_when_nothing_logged(svc):
    assert svc.get_streaks() == {"morning": 0, "evening": 0}


def test_streak_still_counts_yesterday_if_today_not_yet_done(svc):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    svc.save_evening(yesterday, {"whatWentWell": "gisteren wel gedaan"})
    assert svc.get_streaks()["evening"] == 1


def test_wins_and_goals_crud(svc):
    win = svc.add_win({"title": "Eerste klant", "category": "business", "impactLevel": 5})
    assert win["title"] == "Eerste klant"
    assert svc.list_wins()[0]["title"] == "Eerste klant"


def test_add_win_defaults_date_when_payload_has_explicit_none(svc):
    # De FastAPI-payload stuurt altijd een 'date'-sleutel mee (None als niet
    # ingevuld) — data.get("date", _today()) zou dan de None teruggeven i.p.v.
    # de default, en de NOT NULL-kolom laten knappen.
    win = svc.add_win({"title": "Zonder datum", "date": None})
    assert win["date"] == datetime.now().strftime("%Y-%m-%d")


def test_start_focus_session_defaults_date_when_none(svc):
    session = svc.start_focus_session({"startTime": "09:00", "date": None})
    assert session["date"] == datetime.now().strftime("%Y-%m-%d")

    goal = svc.add_goal({"title": "Marathon lopen", "why": "gezondheid",
                          "nextActions": ["schema kiezen", ""]})
    assert goal["next_actions"] == ["schema kiezen"]  # lege actie wordt niet meegenomen

    updated = svc.update_goal(goal["id"], {"progress": 40})
    assert updated["progress"] == 40
    assert updated["title"] == "Marathon lopen"  # ongewijzigde velden blijven staan


def test_briefing_context_never_raises_and_reflects_empty_state(svc):
    ctx = svc.get_briefing_context()
    assert ctx["status"]["morning_done"] is False
    assert ctx["streaks"] == {"morning": 0, "evening": 0}
    assert ctx["recent_wins"] == []
    assert ctx["open_goals"] == []
    assert ctx["energy_today"] is None


def test_briefing_context_reflects_todays_energy(svc):
    today = datetime.now().strftime("%Y-%m-%d")
    svc.save_morning(today, {"intentie": "x", "energyLevel": 8})
    ctx = svc.get_briefing_context()
    assert ctx["status"]["morning_done"] is True
    assert ctx["energy_today"] == 8

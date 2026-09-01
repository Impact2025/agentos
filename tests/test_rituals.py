"""Persoonlijke rituelen — proxy naar mijn-ondernemers-os (bridge), plus de
lokale doelen-CRUD (ritual_goals, geen bridge-equivalent) en de streak-/
briefing-afgeleiden.

`get_briefing_context()` mag nooit een exception laten ontsnappen: `iris/
service.py:gather_context()` roept hem aan in een try/except, maar de functie
zelf moet ook zonder ingevulde data een bruikbaar (leeg) dict teruggeven —
anders krijgt Iris een halve state in plaats van "nog niets ingevuld".

Geen echte HTTP-calls hier: `call_mijn_ondernemers_os` wordt gemonkeypatcht
met een kleine in-memory fake die zich gedraagt als /api/logs, /api/wins,
/api/focus en /api/weekly-reviews in mijn-ondernemers-os — zo test dit zowel
de request-vorm die RitualsService verstuurt als de camelCase->snake_case-
normalisatie van wat terugkomt, zonder netwerk of een draaiende Next.js-app
nodig te hebben."""
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from backend.domains.rituals.models import ensure_schema
from backend.domains.rituals.service import RitualsService
import backend.domains.rituals.service as rituals_service_module


class FakeBridge:
    """Simuleert mijn-ondernemers-os' /api/logs, /api/wins, /api/focus en
    /api/weekly-reviews ver genoeg om RitualsService' vorm-aannames te toetsen."""

    def __init__(self):
        self.logs: dict[tuple[str, str], dict] = {}  # (type, date) -> flat fields
        self.wins: list[dict] = []
        self.focus: list[dict] = []
        self.weekly: list[dict] = []  # elk: {week_number, data}
        self.calls: list[tuple[str, str, dict | None]] = []

    async def __call__(self, method: str, path: str, json: dict | None = None) -> dict:
        self.calls.append((method, path, json))
        url = urlparse(path)
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path == "/api/logs":
            if method == "POST":
                ltype, date = json["type"], json["date"]
                fields = {k: v for k, v in json.items() if k not in ("type", "date")}
                self.logs[(ltype, date)] = fields
                return {"data": fields}
            ltype, date = qs.get("type"), qs.get("date")
            if date is not None:
                key = (ltype, date)
                if key in self.logs:
                    return [{"data": self.logs[key], "date_string": key[1]}]
                return []
            # Geen datumfilter (zoals _streak() gebruikt): alle rijen van dit type.
            return [{"data": v, "date_string": k[1]} for k, v in self.logs.items() if k[0] == ltype]

        if url.path == "/api/wins":
            if method == "POST":
                win = {"id": len(self.wins) + 1, "title": json["title"],
                       "description": json.get("description", ""),
                       "category": json.get("category", "personal"),
                       "impact_level": json.get("impactLevel", 1),
                       "date": json.get("date"), "tags": json.get("tags", [])}
                self.wins.append(win)
                return win
            limit = int(qs.get("limit", 50))
            category = qs.get("category")
            rows = [w for w in self.wins if not category or w["category"] == category]
            return rows[:limit]

        if url.path.startswith("/api/wins/"):
            win_id = int(url.path.rsplit("/", 1)[-1])
            self.wins = [w for w in self.wins if w["id"] != win_id]
            return {"success": True}

        if url.path == "/api/focus":
            if method == "POST":
                row = {"id": len(self.focus) + 1, "date": json["date"],
                       "start_time": json["startTime"], "goal": json.get("goal", ""),
                       "completed": False, "session_type": "work"}
                self.focus.append(row)
                return row
            date = qs.get("date")
            rows = [f for f in self.focus if not date or f["date"] == date]
            return rows

        if url.path.startswith("/api/focus/"):
            focus_id = int(url.path.rsplit("/", 1)[-1])
            for f in self.focus:
                if f["id"] == focus_id:
                    f.update(json or {})
                    return f
            raise HTTPException(status_code=404, detail="not found")

        if url.path == "/api/weekly-reviews":
            if method == "POST":
                rest = {k: v for k, v in json.items() if k != "weekNumber"}
                row = {"week_number": str(json["weekNumber"]), "data": rest}
                self.weekly.append(row)
                return row
            week = qs.get("weekNumber")
            # Echte route sorteert ORDER BY timestamp DESC — nieuwste eerst, zodat
            # get_weekly_start/get_weekly_review de laatst opgeslagen versie pakken.
            return list(reversed([w for w in self.weekly if w["week_number"] == week]))

        raise HTTPException(status_code=404, detail=f"onbekend pad in fake bridge: {path}")


class AlwaysUnreachable:
    """Simuleert een onbereikbare mijn-ondernemers-os — precies wat
    bridge_client.call_mijn_ondernemers_os gooit bij een netwerkfout."""

    async def __call__(self, method: str, path: str, json: dict | None = None) -> dict:
        raise HTTPException(status_code=502, detail="mijn-ondernemers-os is nu niet bereikbaar.")


@pytest.fixture
def bridge(monkeypatch):
    fake = FakeBridge()
    monkeypatch.setattr(rituals_service_module, "call_mijn_ondernemers_os", fake)
    return fake


@pytest.fixture
def svc(clean_tables, bridge):
    ensure_schema()
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM ritual_goals")
    return RitualsService()


@pytest.mark.asyncio
async def test_save_morning_is_upsert_on_date(svc):
    await svc.save_morning("2026-08-10", {"intentie": "eerste versie", "energyLevel": 6})
    await svc.save_morning("2026-08-10", {"intentie": "overschreven", "energyLevel": 9})

    row = await svc.get_morning("2026-08-10")
    assert row["intentie"] == "overschreven"
    assert row["energy_level"] == 9  # camelCase van de bridge -> snake_case, zoals vóór de migratie


@pytest.mark.asyncio
async def test_save_evening_roundtrips_json_fields(svc):
    await svc.save_evening("2026-08-10", {
        "whatWentWell": "goed", "biggestWin": "win", "tomorrowTop3": ["a", "b", "c"],
        "adhdScores": {"onrust": 2},
    })
    row = await svc.get_evening("2026-08-10")
    assert row["tomorrow_top3"] == ["a", "b", "c"]
    assert row["adhd_scores"] == {"onrust": 2}


@pytest.mark.asyncio
async def test_weekly_start_and_review_upsert_on_year_week(svc):
    await svc.save_weekly_start(2026, 32, {"weekIntention": "focus op X", "mainGoals": ["a"]})
    await svc.save_weekly_start(2026, 32, {"weekIntention": "focus op Y", "mainGoals": ["a", "b"]})
    row = await svc.get_weekly_start(2026, 32)
    assert row["weekIntention"] == "focus op Y"
    assert row["mainGoals"] == ["a", "b"]

    await svc.save_weekly_review(2026, 32, {"wins": ["overwinning 1"], "productivityScore": 8})
    review = await svc.get_weekly_review(2026, 32)
    assert review["wins"] == ["overwinning 1"]
    assert review["productivityScore"] == 8


@pytest.mark.asyncio
async def test_weekly_start_and_review_dont_leak_into_each_other(svc, bridge):
    """Beide slaan op in dezelfde /api/weekly-reviews-rijen voor hetzelfde weeknummer —
    get_weekly_start/get_weekly_review moeten op het 'type'-veld filteren, niet zomaar
    de eerste/laatste rij pakken."""
    await svc.save_weekly_start(2026, 32, {"weekIntention": "start"})
    await svc.save_weekly_review(2026, 32, {"wins": ["iets"]})

    start = await svc.get_weekly_start(2026, 32)
    review = await svc.get_weekly_review(2026, 32)
    assert start["weekIntention"] == "start"
    assert "weekIntention" not in review
    assert review["wins"] == ["iets"]


@pytest.mark.asyncio
async def test_streak_counts_consecutive_days_from_today(svc):
    today = datetime.now()
    for offset in (0, 1, 2):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        await svc.save_morning(d, {"intentie": f"dag -{offset}"})
    # Gat op dag -3 (niet ingevuld)
    d4 = (today - timedelta(days=4)).strftime("%Y-%m-%d")
    await svc.save_morning(d4, {"intentie": "te oud, telt niet mee"})

    assert (await svc.get_streaks())["morning"] == 3


@pytest.mark.asyncio
async def test_streak_is_zero_when_nothing_logged(svc):
    assert await svc.get_streaks() == {"morning": 0, "evening": 0}


@pytest.mark.asyncio
async def test_streak_still_counts_yesterday_if_today_not_yet_done(svc):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    await svc.save_evening(yesterday, {"whatWentWell": "gisteren wel gedaan"})
    assert (await svc.get_streaks())["evening"] == 1


@pytest.mark.asyncio
async def test_wins_and_goals_crud(svc):
    win = await svc.add_win({"title": "Eerste klant", "category": "business", "impactLevel": 5})
    assert win["title"] == "Eerste klant"
    assert (await svc.list_wins())[0]["title"] == "Eerste klant"


@pytest.mark.asyncio
async def test_start_focus_session_and_local_goals(svc):
    session = await svc.start_focus_session({"startTime": "09:00", "date": "2026-08-10"})
    assert session["date"] == "2026-08-10"

    # Doelen blijven lokaal (geen bridge-equivalent) — sync, geen await.
    goal = svc.add_goal({"title": "Marathon lopen", "why": "gezondheid",
                          "nextActions": ["schema kiezen", ""]})
    assert goal["next_actions"] == ["schema kiezen"]  # lege actie wordt niet meegenomen

    updated = svc.update_goal(goal["id"], {"progress": 40})
    assert updated["progress"] == 40
    assert updated["title"] == "Marathon lopen"  # ongewijzigde velden blijven staan


@pytest.mark.asyncio
async def test_briefing_context_never_raises_and_reflects_empty_state(svc):
    ctx = await svc.get_briefing_context()
    assert ctx["status"]["morning_done"] is False
    assert ctx["streaks"] == {"morning": 0, "evening": 0}
    assert ctx["recent_wins"] == []
    assert ctx["open_goals"] == []
    assert ctx["energy_today"] is None


@pytest.mark.asyncio
async def test_briefing_context_reflects_todays_energy(svc):
    today = datetime.now().strftime("%Y-%m-%d")
    await svc.save_morning(today, {"intentie": "x", "energyLevel": 8})
    ctx = await svc.get_briefing_context()
    assert ctx["status"]["morning_done"] is True
    assert ctx["energy_today"] == 8


@pytest.mark.asyncio
async def test_read_paths_fail_open_when_bridge_unreachable(clean_tables, monkeypatch):
    """De verplichte ritueel-gate mag Vincent nooit buitensluiten uit zijn eigen
    Control Room omdat mijn-ondernemers-os even niet bereikbaar is."""
    monkeypatch.setattr(rituals_service_module, "call_mijn_ondernemers_os", AlwaysUnreachable())
    ensure_schema()
    svc = RitualsService()

    status = await svc.get_today_status()
    assert status["morning_done"] is False
    assert status["evening_done"] is False

    next_required = await svc.get_next_required()
    assert next_required["isRequired"] is True  # valt terug op "nog niets gedaan", crasht niet

    ctx = await svc.get_briefing_context()
    assert ctx["status"]["morning_done"] is False


@pytest.mark.asyncio
async def test_write_paths_fail_loud_when_bridge_unreachable(clean_tables, monkeypatch):
    """In tegenstelling tot de leespaden: een save die niet aankomt moet zichtbaar
    falen, niet stil net doen alsof het gelukt is."""
    monkeypatch.setattr(rituals_service_module, "call_mijn_ondernemers_os", AlwaysUnreachable())
    ensure_schema()
    svc = RitualsService()

    with pytest.raises(HTTPException):
        await svc.save_morning("2026-08-10", {"intentie": "x"})

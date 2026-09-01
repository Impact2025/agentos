"""Tests voor De Sparringpartner — de reflectie zelf (run_analysis, lessons,
energy-log) draait native tegen backend/domains/rituals (single-user SQLite).

Uitzondering: check_and_send_whatsapp() haalt het proactieve signaal sinds
29-08-2026 weer via de bridge op bij mijn-ondernemers-os (coach_bridge/
whatsapp.py) — Vincents rituelen leven sinds de multi-tenant-migratie daar
in Neon, niet meer lokaal, dus de eigen detect_proactive_signal() hieronder
werkte tegen verouderde data. Zie test_check_and_send_whatsapp_gebruikt_bridge."""
import asyncio

import pytest

from backend.domains.coach import service as coach_service
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def clean_coach_tables():
    coach_service.ensure_schema()
    with get_conn() as c:
        c.execute("DELETE FROM coach_lessons")
        c.execute("DELETE FROM coach_energy_log")
        c.execute("DELETE FROM coach_whatsapp_sent")
        c.execute("DELETE FROM ritual_morning")
    yield
    with get_conn() as c:
        c.execute("DELETE FROM coach_lessons")
        c.execute("DELETE FROM coach_energy_log")
        c.execute("DELETE FROM coach_whatsapp_sent")
        c.execute("DELETE FROM ritual_morning")


def _ctx(today_energy=6, yesterday_energy=6, streak=5, energy_log=None, holding=None):
    return {
        "today": {"energy_level": today_energy, "sleep_quality": 6, "wake_time": "07:00", "intentie": "Focus"},
        "yesterday": {"energy_level": yesterday_energy} if yesterday_energy is not None else None,
        "streak": streak,
        "energy_log": energy_log or [],
        "lessons": [],
        "holding": holding,
    }


def test_choose_technique_oplossingsgericht_bij_lage_energie_met_streak():
    ctx = _ctx(today_energy=2, yesterday_energy=5, streak=4)
    technique, _ = coach_service.choose_technique(ctx)
    assert technique == "oplossingsgericht"


def test_choose_technique_cgt_bij_scherpe_val():
    ctx = _ctx(today_energy=4, yesterday_energy=8, streak=5)
    technique, _ = coach_service.choose_technique(ctx)
    assert technique == "cgt"


def test_choose_technique_mi_bij_meer_kosten_dan_geven():
    log = [
        {"date": "1", "activity": "a", "category": "", "direction": "cost"},
        {"date": "2", "activity": "b", "category": "", "direction": "cost"},
        {"date": "3", "activity": "c", "category": "", "direction": "cost"},
        {"date": "4", "activity": "d", "category": "", "direction": "gain"},
    ]
    ctx = _ctx(energy_log=log)
    technique, _ = coach_service.choose_technique(ctx)
    assert technique == "mi"


def test_choose_technique_strengths_bij_nieuwe_streak_hoge_energie():
    ctx = _ctx(today_energy=8, yesterday_energy=None, streak=1)
    technique, _ = coach_service.choose_technique(ctx)
    assert technique == "strengths"


def test_choose_technique_systemisch_bij_holding_onder_druk():
    ctx = _ctx(holding={"waarheidsaudit": {"blokkerend": 6}, "gemiste_runs": {"aantal_jobs": 0}})
    technique, _ = coach_service.choose_technique(ctx)
    assert technique == "systemisch"


def test_choose_technique_grow_als_default():
    technique, _ = coach_service.choose_technique(_ctx())
    assert technique == "grow"


def test_remember_lesson_dedupe_en_confidence_groeit():
    coach_service.remember_lesson("grow:test", "grow", "Een testles")
    lessons = coach_service.list_lessons()
    assert len(lessons) == 1
    assert lessons[0]["times_confirmed"] == 1
    first_confidence = lessons[0]["confidence"]

    coach_service.remember_lesson("grow:test", "grow", "Een testles, opnieuw gezien")
    lessons = coach_service.list_lessons()
    assert len(lessons) == 1  # dedupe op pattern_key, geen tweede rij
    assert lessons[0]["times_confirmed"] == 2
    assert lessons[0]["confidence"] > first_confidence


def test_energy_log_roundtrip():
    import datetime
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    created = coach_service.save_energy_log(today_str, [
        {"activity": "Workshop gegeven", "category": "", "direction": "gain"},
        {"activity": "Lang gewacht op iets", "category": "", "direction": "cost"},
        {"activity": "", "category": "", "direction": "gain"},  # lege activiteit: geen rij
    ])
    assert created == 2
    rows = coach_service.list_energy_log(days=7)
    assert len(rows) == 2


def test_build_prompt_crasht_niet_op_windows():
    """Regressie: strftime("%-d %B") is een Unix-only extensie en gaf op deze
    Windows-backend `ValueError: Invalid format string` zodra iemand écht op
    'Vraag reflectie' klikte — geen enkele test met een gemockte datum ving dit,
    want de crash zat in de systeemklok-aanroep zelf, niet in de logica eromheen."""
    ctx = _ctx()
    for technique in coach_service.TECHNIQUE_LABELS:
        prompt = coach_service.build_prompt(ctx, technique)
        assert isinstance(prompt, str) and len(prompt) > 100


def test_run_analysis_weigert_zonder_ochtendritueel(monkeypatch):
    # rituals leest sinds de bridge-migratie niet meer ritual_morning (lokaal), maar
    # mijn-ondernemers-os — dus hier de bridge zelf mocken i.p.v. de lokale tabel legen.
    import backend.domains.rituals.service as rituals_service_module

    async def fake_no_data(method, path, json=None):
        return []

    monkeypatch.setattr(rituals_service_module, "call_mijn_ondernemers_os", fake_no_data)

    result = asyncio.run(coach_service.run_analysis())
    assert result["ok"] is False
    assert result["status"] == 409


def test_whatsapp_dedupe_per_dag():
    assert coach_service._whatsapp_already_sent("mi:test") is False
    coach_service._whatsapp_mark_sent("mi:test")
    assert coach_service._whatsapp_already_sent("mi:test") is True


def test_router_gemonteerd():
    from backend.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/coach/analyse" in paths
    assert "/api/coach/lessons" in paths
    assert "/api/coach/energy-log" in paths


def test_scheduler_job_delegeert_aan_coach_service():
    """De scheduler-job zelf kent coach_bridge niet — dat blijft een
    implementatiedetail van check_and_send_whatsapp(), niet iets wat de job
    zelf hoeft te weten. (Eerder heette deze test '...niet_de_bridge' en
    beweerde dat er nérgens in het pad een bridge zat — dat klopt sinds
    29-08-2026 niet meer, zie test_check_and_send_whatsapp_gebruikt_bridge.)"""
    import inspect
    from backend import scheduler as S
    src = inspect.getsource(S.coach_whatsapp_check_job)
    assert "from .domains.coach import service" in src


def test_check_and_send_whatsapp_gebruikt_bridge(monkeypatch):
    """Regressie voor de 29-08-2026-wijziging: het proactieve signaal moet uit
    mijn-ondernemers-os komen (de echte rituelen-data). De eigen lokale
    detect_proactive_signal() bestaat niet meer (verwijderd toen rituals zelf
    ook naar de bridge verhuisde) — dit test alleen de signaal-bron, geen
    echt WhatsApp-bericht."""
    calls = {"remote": 0}

    async def fake_remote_signal():
        calls["remote"] += 1
        return {"signal": True, "pattern_key": "test:vanaf-bridge", "message": "test"}

    class FakeBridgeService:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        async def send_whatsapp_reminder(text):
            return True

    monkeypatch.setattr(
        "backend.domains.coach_bridge.whatsapp.fetch_remote_signal", fake_remote_signal
    )
    monkeypatch.setitem(
        __import__("sys").modules, "backend.domains.bridge.service", FakeBridgeService
    )

    sent = asyncio.run(coach_service.check_and_send_whatsapp())

    assert sent is True
    assert calls["remote"] == 1, "check_and_send_whatsapp moet het signaal via de bridge ophalen"

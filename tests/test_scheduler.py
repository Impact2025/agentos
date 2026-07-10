"""Tests voor de scheduler-inhaalslag.

De kern: een cron-job die vuurt vóórdat de server draait wordt door APScheduler
nooit ingepland. Deze tests leggen vast wat er dan alsnog moet gebeuren — en
vooral wat er níét mag gebeuren (gisteren nog eens overdoen, of bij een verse
installatie meteen een ochtendrapport de deur uit doen).
"""
import asyncio
import datetime as dt
from functools import partial

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend import scheduler as S
from backend.shared.database import get_conn


BOOT = S._TZ.localize(dt.datetime(2026, 7, 10, 6, 57, 32))  # vrijdagochtend, ná Iris' 06:45


@pytest.fixture(autouse=True)
def clean_runs():
    with get_conn() as c:
        c.execute("DELETE FROM scheduler_runs")
    yield
    with get_conn() as c:
        c.execute("DELETE FROM scheduler_runs")


def _set_last_ok(job_id: str, moment: dt.datetime) -> None:
    S._record_run(job_id, "ok", None)
    with get_conn() as c:
        c.execute(
            "UPDATE scheduler_runs SET last_run_at = ?, last_ok_at = ? WHERE job_id = ?",
            (moment.isoformat(), moment.isoformat(), job_id),
        )


def _baseline(moment: dt.datetime) -> None:
    S._record_run(S._BASELINE_ID, "ok", None, source="seed")
    with get_conn() as c:
        c.execute(
            "UPDATE scheduler_runs SET last_run_at = ?, last_ok_at = ? WHERE job_id = ?",
            (moment.isoformat(), moment.isoformat(), S._BASELINE_ID),
        )


def _ids(pending) -> list[str]:
    return [spec.id for _, spec in pending]


# ── _last_fire_before ──────────────────────────────────────────────────────

def test_last_fire_before_vindt_de_run_van_vanochtend():
    trigger = CronTrigger(hour=6, minute=45, timezone=S._TZ)
    fire = S._last_fire_before(trigger, BOOT, S._CATCHUP_WINDOW)
    assert fire == S._TZ.localize(dt.datetime(2026, 7, 10, 6, 45))


def test_last_fire_before_negeert_een_toekomstige_run():
    trigger = CronTrigger(hour=9, minute=0, timezone=S._TZ)  # vandaag pas om 09:00
    fire = S._last_fire_before(trigger, BOOT, S._CATCHUP_WINDOW)
    assert fire == S._TZ.localize(dt.datetime(2026, 7, 9, 9, 0))  # dus die van gisteren


def test_interval_triggers_hebben_geen_vuurmoment_in_het_verleden():
    # Hun start_date ligt per definitie in de toekomst; een interval-job hoeft
    # nooit ingehaald te worden.
    assert S._last_fire_before(IntervalTrigger(hours=4), BOOT, S._CATCHUP_WINDOW) is None


# ── _pending_catchups ──────────────────────────────────────────────────────

def test_verse_installatie_haalt_niets_in_maar_zet_een_nulmeting():
    assert S._pending_catchups(BOOT) == []
    runs = S._load_runs()
    assert runs[S._BASELINE_ID]["source"] == "seed"


def test_boot_na_iris_haalt_gsc_en_briefing_in_op_volgorde():
    _baseline(BOOT - dt.timedelta(days=7))
    for job in ("gsc_sync", "iris_briefing"):
        _set_last_ok(job, BOOT - dt.timedelta(days=1))

    pending = S._pending_catchups(BOOT)

    # Chronologisch: Iris hoort de GSC-cijfers van vanochtend te zien.
    assert _ids(pending) == ["gsc_sync", "iris_briefing"]
    assert pending[0][0] < pending[1][0]


def test_run_van_gisteren_wordt_niet_ingehaald():
    # Om 06:57 is het laatste vuurmoment van het ochtendrapport gisteren 07:00.
    # Dat alsnog mailen, drie minuten voordat dat van vandaag draait, is onzin.
    _baseline(BOOT - dt.timedelta(days=7))
    assert "daily_digest" not in _ids(S._pending_catchups(BOOT))


def test_late_boot_haalt_de_hele_ochtendketen_in():
    _baseline(BOOT - dt.timedelta(days=7))
    laat = S._TZ.localize(dt.datetime(2026, 7, 10, 10, 0))

    ids = _ids(S._pending_catchups(laat))

    assert ids[:3] == ["gsc_sync", "iris_briefing", "daily_digest"]
    assert "biweekly_content" in ids  # vrijdag 09:00 hoort erbij


def test_al_gedraaide_run_wordt_niet_herhaald():
    _baseline(BOOT - dt.timedelta(days=7))
    _set_last_ok("gsc_sync", S._TZ.localize(dt.datetime(2026, 7, 10, 6, 30, 12)))
    _set_last_ok("iris_briefing", S._TZ.localize(dt.datetime(2026, 7, 10, 6, 45, 9)))

    assert S._pending_catchups(BOOT) == []


def test_runs_van_voor_de_nulmeting_tellen_niet_mee():
    # Nulmeting om 06:40: de GSC-sync (06:30) lag ervóór en telt niet mee;
    # Iris (06:45) lag erna en wordt wel ingehaald.
    _baseline(S._TZ.localize(dt.datetime(2026, 7, 10, 6, 40)))
    assert _ids(S._pending_catchups(BOOT)) == ["iris_briefing"]


def test_jobs_met_blijvend_neveneffect_halen_nooit_in():
    # Maandelijkse doel-jobs maken goals aan; die mag je niet met terugwerkende
    # kracht afvuren. Interval-jobs komen uit zichzelf snel genoeg langs.
    nooit = {"ictusgo_monthly_content_goal", "weareimpact_monthly_content_goal",
             "radar_sky_scan", "content_improver", "goal_autoheal"}
    assert {s.id for s in S._SPECS if s.catch_up} & nooit == set()


# ── run-historie ───────────────────────────────────────────────────────────

def test_mislukte_run_wist_de_laatste_geslaagde_niet():
    # Anders zou de inhaalslag hem bij elke start opnieuw proberen.
    _set_last_ok("gsc_sync", BOOT - dt.timedelta(days=1))
    S._record_run("gsc_sync", "error", "boem")

    run = S._load_runs()["gsc_sync"]
    assert run["status"] == "error"
    assert run["error"] == "boem"
    assert run["last_ok_at"] is not None


def test_geslaagde_run_werkt_beide_tijdstempels_bij():
    S._record_run("gsc_sync", "error", "boem")
    assert S._load_runs()["gsc_sync"]["last_ok_at"] is None
    S._record_run("gsc_sync", "ok", None)
    run = S._load_runs()["gsc_sync"]
    assert run["last_ok_at"] == run["last_run_at"]
    assert run["error"] is None


# ── _invoke ────────────────────────────────────────────────────────────────

def test_invoke_draait_sync_async_en_partial():
    seen = []

    def sync_job():
        seen.append("sync")

    async def async_job():
        seen.append("async")

    async def async_arg(x):
        seen.append(x)

    async def main():
        await S._invoke(sync_job)
        await S._invoke(async_job)
        await S._invoke(partial(async_arg, "partial"))

    asyncio.run(main())
    assert seen == ["sync", "async", "partial"]


# ── _run_catchups: orkestratie ─────────────────────────────────────────────

def _fake_spec(job_id: str, func) -> S.JobSpec:
    return S.JobSpec(job_id, f"nep {job_id}", func, CronTrigger(hour=6, timezone=S._TZ),
                     catch_up=True)


def test_catchups_draaien_sequentieel_en_op_volgorde(monkeypatch):
    volgorde = []

    def job(job_id, vertraging):
        async def run():
            volgorde.append(f"start {job_id}")
            await asyncio.sleep(vertraging)
            volgorde.append(f"klaar {job_id}")
        return run

    vroeg = S._TZ.localize(dt.datetime(2026, 7, 10, 6, 30))
    laat = S._TZ.localize(dt.datetime(2026, 7, 10, 6, 45))
    monkeypatch.setattr(S, "_pending_catchups", lambda now: [
        (vroeg, _fake_spec("gsc_sync", job("gsc_sync", 0.05))),   # traag
        (laat, _fake_spec("iris_briefing", job("iris_briefing", 0))),
    ])

    asyncio.run(S._run_catchups())

    # De trage GSC-sync moet áf zijn voordat Iris begint — anders draait de
    # briefing op de cijfers van gisteren.
    assert volgorde == ["start gsc_sync", "klaar gsc_sync",
                        "start iris_briefing", "klaar iris_briefing"]


def test_een_gestruikelde_catchup_houdt_de_rest_niet_tegen(monkeypatch):
    gedraaid = []

    async def stuk():
        raise RuntimeError("boem")

    async def goed():
        gedraaid.append("iris")

    moment = S._TZ.localize(dt.datetime(2026, 7, 10, 6, 30))
    monkeypatch.setattr(S, "_pending_catchups", lambda now: [
        (moment, _fake_spec("gsc_sync", stuk)),
        (moment + dt.timedelta(minutes=15), _fake_spec("iris_briefing", goed)),
    ])

    asyncio.run(S._run_catchups())

    assert gedraaid == ["iris"]
    runs = S._load_runs()
    assert runs["gsc_sync"]["status"] == "error"
    assert runs["gsc_sync"]["error"] == "boem"
    assert runs["gsc_sync"]["source"] == "catchup"
    assert runs["iris_briefing"]["status"] == "ok"


# ── pauze/hervat rond de inhaalslag ────────────────────────────────────────

def test_apscheduler_haalt_een_vuurmoment_in_dat_tijdens_de_pauze_verstreek():
    """De aanname waar `_startup_catchup` op leunt: wat vuurt terwijl de
    scheduler gepauzeerd staat, draait alsnog zodra hij hervat wordt."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.date import DateTrigger

    gedraaid = asyncio.Event()

    async def main():
        sched = AsyncIOScheduler(timezone=S._TZ)
        sched.add_job(gedraaid.set, DateTrigger(run_date=dt.datetime.now(S._TZ)),
                      misfire_grace_time=60, coalesce=True)
        sched.start(paused=True)
        try:
            await asyncio.sleep(0.2)  # het vuurmoment verstrijkt tijdens de pauze
            assert not gedraaid.is_set(), "gepauzeerd hoort niets te vuren"
            sched.resume()
            await asyncio.wait_for(gedraaid.wait(), timeout=5)
        finally:
            sched.shutdown(wait=False)

    asyncio.run(main())


def test_startup_catchup_hervat_de_scheduler_ook_als_de_inhaalslag_faalt(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.schedulers.base import STATE_PAUSED

    async def main():
        sched = AsyncIOScheduler(timezone=S._TZ)
        sched.start(paused=True)
        monkeypatch.setattr(S, "_scheduler", sched)

        async def kapot():
            raise RuntimeError("de hele inhaalslag klapt")
        monkeypatch.setattr(S, "_run_catchups", kapot)

        assert sched.state == STATE_PAUSED
        try:
            await S._startup_catchup()
            assert sched.state != STATE_PAUSED, "scheduler moet hoe dan ook hervatten"
        finally:
            sched.shutdown(wait=False)

    asyncio.run(main())


def test_startup_catchup_hervat_de_scheduler_na_een_timeout(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.schedulers.base import STATE_PAUSED

    async def main():
        sched = AsyncIOScheduler(timezone=S._TZ)
        sched.start(paused=True)
        monkeypatch.setattr(S, "_scheduler", sched)
        monkeypatch.setattr(S, "_CATCHUP_TIMEOUT", dt.timedelta(milliseconds=50))

        async def hangt():
            await asyncio.sleep(30)
        monkeypatch.setattr(S, "_run_catchups", hangt)

        assert sched.state == STATE_PAUSED
        try:
            await S._startup_catchup()
            assert sched.state != STATE_PAUSED, "een hangende job mag de planning niet gijzelen"
        finally:
            sched.shutdown(wait=False)

    asyncio.run(main())


def test_scheduler_schrijft_het_resultaat_van_een_run_naar_de_database():
    """De listener is de enige plek waar run-historie ontstaat; zonder die
    schakel weet de volgende start niet wat er al gedraaid heeft."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    from apscheduler.triggers.date import DateTrigger

    gedraaid = asyncio.Event()

    async def main():
        sched = AsyncIOScheduler(timezone=S._TZ)
        sched.add_listener(S._on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        sched.add_listener(lambda e: gedraaid.set(), EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        sched.add_job(lambda: None, DateTrigger(run_date=dt.datetime.now(S._TZ)), id="gsc_sync")
        sched.start()
        try:
            await asyncio.wait_for(gedraaid.wait(), timeout=5)
        finally:
            sched.shutdown(wait=False)

    asyncio.run(main())

    run = S._load_runs()["gsc_sync"]
    assert run["status"] == "ok"
    assert run["source"] == "schedule"
    assert run["last_ok_at"] is not None


def test_iedere_job_heeft_een_label_en_unieke_id():
    ids = [s.id for s in S._SPECS]
    assert len(ids) == len(set(ids))
    assert all(s.label and s.label != s.id for s in S._SPECS)

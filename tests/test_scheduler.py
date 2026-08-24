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

    # Chronologisch: Iris hoort de GSC-cijfers van vanochtend te zien, en de
    # waarheidsaudit (06:40) hoort vóór haar briefing te draaien zodat stille
    # bevindingen in het oordeel van vandaag meewegen. (Er kunnen jobs van
    # eerdere dagen vóór staan — zie de sectie over `gap_cost` hieronder — dus
    # we toetsen de kéten, niet de hele lijst.)
    keten = ("gsc_sync", "waarheidsaudit", "iris_briefing")
    vandaag = [spec.id for fire, spec in pending
               if fire.date() == BOOT.date() and spec.id in keten]
    assert vandaag == list(keten)
    momenten = [fire for fire, _ in pending]
    assert momenten == sorted(momenten)


def test_run_van_gisteren_wordt_niet_ingehaald():
    # Om 06:57 is het laatste vuurmoment van het ochtendrapport gisteren 07:00.
    # Dat alsnog mailen, drie minuten voordat dat van vandaag draait, is onzin.
    _baseline(BOOT - dt.timedelta(days=7))
    assert "daily_digest" not in _ids(S._pending_catchups(BOOT))


def test_late_boot_haalt_de_hele_ochtendketen_in():
    _baseline(BOOT - dt.timedelta(days=7))
    laat = S._TZ.localize(dt.datetime(2026, 7, 10, 10, 0))

    pending = S._pending_catchups(laat)
    ids = _ids(pending)
    vandaag = [spec.id for fire, spec in pending if fire.date() == laat.date()]

    # De keten moet in deze volgorde staan; jobs met `priority=0` (finance,
    # beurs, kennisronde) mogen ervóór springen, want die zijn onafhankelijk en
    # anders de eerste slachtoffers van de 20-minutengrens.
    keten = [j for j in vandaag
             if j in ("gsc_sync", "waarheidsaudit", "iris_briefing", "daily_digest")]
    assert keten == ["gsc_sync", "waarheidsaudit", "iris_briefing", "daily_digest"]
    assert "biweekly_content" in ids  # vrijdag 09:00 hoort erbij


def test_al_gedraaide_run_wordt_niet_herhaald():
    _baseline(BOOT - dt.timedelta(days=7))
    _set_last_ok("gsc_sync", S._TZ.localize(dt.datetime(2026, 7, 10, 6, 30, 12)))
    _set_last_ok("waarheidsaudit", S._TZ.localize(dt.datetime(2026, 7, 10, 6, 40, 3)))
    _set_last_ok("iris_briefing", S._TZ.localize(dt.datetime(2026, 7, 10, 6, 45, 9)))

    ids = _ids(S._pending_catchups(BOOT))
    assert "gsc_sync" not in ids
    assert "waarheidsaudit" not in ids
    assert "iris_briefing" not in ids


def test_runs_van_voor_de_nulmeting_tellen_niet_mee():
    # Nulmeting om 06:42: de GSC-sync (06:30) en de waarheidsaudit (06:40) lagen
    # ervóór en tellen niet mee; Iris (06:45) lag erna en wordt wel ingehaald.
    _baseline(S._TZ.localize(dt.datetime(2026, 7, 10, 6, 42)))
    assert _ids(S._pending_catchups(BOOT)) == ["iris_briefing"]


# ── Inhalen over meerdere dagen (`gap_cost`) ───────────────────────────────
#
# Aanleiding: de machine stond 28-31 juli 2026 vier werkdagen uit. De
# outreach-batch vuurde vier keer niet en de vacaturescan sloeg over; dat werd
# wél geteld, maar er gebeurde pas iets als iemand de knop "Nu alsnog draaien"
# aanklikte. Voor werk waarvan de dag niet terugkomt is dat te weinig — en voor
# een rapport dat per dag veroudert is inhalen juist schadelijk.

ZATERDAG = S._TZ.localize(dt.datetime(2026, 7, 11, 10, 0))  # machine uit sinds do


def test_gemiste_dag_van_gisteren_wordt_ingehaald_als_de_dag_niet_terugkomt():
    """De content-batch draait di/vr 09:00. Boot je zaterdag, dan komt vrijdag
    niet meer terug — die batch is werk dat anders nooit gebeurt."""
    _baseline(ZATERDAG - dt.timedelta(days=14))
    pending = dict((spec.id, fire) for fire, spec in S._pending_catchups(ZATERDAG))

    assert "biweekly_content" in pending
    assert pending["biweekly_content"].date() == dt.date(2026, 7, 10)  # vrijdag


def test_rapport_van_gisteren_blijft_liggen():
    """Zonder `gap_cost` veroudert de opbrengst per dag: het ochtendrapport van
    vrijdag op zaterdag mailen maakt de inbox onbetrouwbaar."""
    _baseline(ZATERDAG - dt.timedelta(days=14))
    pending = S._pending_catchups(ZATERDAG)
    # Zaterdag 10:00: het rapport van vandaag (07:00) is gemist en wordt terecht
    # ingehaald. Wat er níét mag gebeuren is dat van vrijdag alsnog versturen.
    for fire, spec in pending:
        if not spec.gap_cost:
            assert fire.date() == ZATERDAG.date(), f"{spec.id} haalt een oudere dag in"


def test_job_die_vandaag_toch_nog_vuurt_wordt_niet_ingehaald():
    """Boot om 06:57: het laatste vuurmoment van de outreach-batch is gisteren
    07:15, maar die van vandaag komt over achttien minuten. Inhalen zou twee
    batches binnen het uur betekenen."""
    _baseline(BOOT - dt.timedelta(days=7))
    assert "daily_outreach_batch" not in _ids(S._pending_catchups(BOOT))


def test_vier_gemiste_dagen_leveren_een_inhaalrun_op():
    """Vier keer niet gedraaid is niet vier keer inhalen: dat is vier stapels
    concepten en vier keer LLM-kosten voor werk dat één keer hoort te gebeuren."""
    _baseline(ZATERDAG - dt.timedelta(days=30))
    ids = _ids(S._pending_catchups(ZATERDAG))
    assert ids.count("biweekly_content") == 1
    assert ids.count("vacancy_scan") == 1


def test_inhalen_stopt_bij_het_terugkijkvenster():
    """Twee weken is de grens; wie langer weg was, krijgt geen stapel werk van
    een maand geleden alsnog over zich heen."""
    lang_weg = ZATERDAG + dt.timedelta(days=40)
    _baseline(lang_weg - dt.timedelta(days=90))
    for fire, spec in S._pending_catchups(lang_weg):
        assert fire >= lang_weg - S._GAP_CATCHUP_WINDOW


def test_nieuwe_job_haalt_niets_in_van_voor_zijn_bestaan():
    """Wat voor een verse installatie geldt, geldt per job: een JobSpec die
    gisteren is toegevoegd hoort geen week aan gemiste runs op te halen."""
    _baseline(ZATERDAG - dt.timedelta(days=30))
    S._record_run("biweekly_content", "ok", None, source="seed")
    with get_conn() as c:
        c.execute(
            "UPDATE scheduler_runs SET first_seen_at = ?, last_run_at = '', last_ok_at = '' "
            "WHERE job_id = 'biweekly_content'",
            (ZATERDAG.isoformat(),),
        )
    assert "biweekly_content" not in _ids(S._pending_catchups(ZATERDAG))


def test_afgekapte_inhaalslag_wordt_gemeld(monkeypatch):
    """De tijdgrens blijft — onbeperkt wachten houdt de planning van de hele dag
    op — maar afkappen zonder melden is een taak die stil overgeslagen wordt."""
    from backend.shared.database import get_conn as _conn
    with _conn() as c:
        c.execute("DELETE FROM activity_log WHERE action = 'inhaalslag_afgekapt'")
    S._catchup_rest.clear()
    S._catchup_rest.extend(["Outreach-batch", "Finance dagrapport"])
    S._meld_afgekapte_inhaalslag()
    with _conn() as c:
        rij = c.execute(
            "SELECT detail, status, next_step FROM activity_log "
            "WHERE action = 'inhaalslag_afgekapt'").fetchone()
    assert rij is not None
    assert rij["status"] == "error"          # dit hoort in het Actiecentrum
    assert "Outreach-batch" in rij["detail"]  # mét wat er dus níét is gebeurd
    assert rij["next_step"]
    with _conn() as c:
        c.execute("DELETE FROM activity_log WHERE action = 'inhaalslag_afgekapt'")


def test_zonder_rest_geen_melding():
    """Een inhaalslag die gewoon klaar was, meldt niets."""
    from backend.shared.database import get_conn as _conn
    S._catchup_rest.clear()
    S._meld_afgekapte_inhaalslag()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM activity_log "
                      "WHERE action = 'inhaalslag_afgekapt'").fetchone()[0]
    assert n == 0


def test_jobs_met_blijvend_neveneffect_halen_nooit_in():
    # Maandelijkse doel-jobs maken goals aan; die mag je niet met terugwerkende
    # kracht afvuren. Interval-jobs komen uit zichzelf snel genoeg langs.
    nooit = {"ictusgo_monthly_content_goal", "weareimpact_monthly_content_goal",
             "bewaardvoorjou_monthly_content_goal",
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


# ── Gemiste runs zijn pas een aandachtspunt als ze niet vanzelf herstellen ──

def _job(status, next_run_over: dt.timedelta | None):
    now = dt.datetime.now(S._TZ)
    return {
        "id": "x", "label": "X",
        "next_run": (now + next_run_over).isoformat() if next_run_over else None,
        "last_run": {"status": status, "time": now.isoformat(), "error": None},
    }


def test_gemiste_poll_job_die_zo_weer_draait_is_geen_aandachtspunt():
    from backend.domains.strategist.service import _job_needs_attention
    assert not _job_needs_attention(_job("missed", dt.timedelta(minutes=12)))


def test_gemiste_cron_job_die_pas_morgen_draait_blijft_zichtbaar():
    from backend.domains.strategist.service import _job_needs_attention
    assert _job_needs_attention(_job("missed", dt.timedelta(hours=20)))


def test_gemiste_job_zonder_volgende_run_blijft_zichtbaar():
    from backend.domains.strategist.service import _job_needs_attention
    assert _job_needs_attention(_job("missed", None))


def test_echte_fout_blijft_altijd_een_aandachtspunt():
    from backend.domains.strategist.service import _job_needs_attention
    assert _job_needs_attention(_job("error", dt.timedelta(minutes=1)))


def test_geslaagde_job_is_geen_aandachtspunt():
    from backend.domains.strategist.service import _job_needs_attention
    assert not _job_needs_attention(_job("ok", dt.timedelta(minutes=1)))


# ── Eén uitvoer-poort: dagslot, ontwaken, ketenafhankelijkheid ─────────────
#
# Aanleiding (7 aug 2026): de laptop stond in slaapstand en werd om 08:33
# gewekt. Geen koude start, dus geen beschermde inhaalslag — APScheduler
# speelde de gemiste vuurmomenten in zijn eigen volgorde af en het
# ochtendrapport (08:33:49) ging de deur uit vóór Iris' briefing (08:38:49).

def _spec(job_id: str):
    return S._BY_ID[job_id]


def test_dagslot_laat_een_inhaalbare_job_maar_een_keer_slagen():
    """Twee mechanismen die allebei werken (misfire-herhaling én inhaalslag)
    zijn anders twee LLM-briefings en twee ochtendrapporten."""
    _set_last_ok("iris_briefing", S._now())
    gedraaid = []

    async def nep():
        gedraaid.append(1)

    import dataclasses
    spec = dataclasses.replace(_spec("iris_briefing"), func=nep)

    assert asyncio.run(S.run_spec_once(spec, source="test")) is False
    assert gedraaid == []


def test_de_menselijke_knop_negeert_het_dagslot():
    """Wie bewust op 'Nu alsnog draaien' klikt, krijgt hem — de rem is er tegen
    mechanismen die elkaar dubbelen, niet tegen Vincent."""
    import dataclasses
    _set_last_ok("iris_briefing", S._now())
    gedraaid = []

    async def nep():
        gedraaid.append(1)

    spec = dataclasses.replace(_spec("iris_briefing"), func=nep)
    assert asyncio.run(S.run_spec_once(spec, source="test", force=True)) is True
    assert gedraaid == [1]


def test_job_die_vandaag_nog_niet_slaagde_draait_gewoon():
    import dataclasses
    gedraaid = []

    async def nep():
        gedraaid.append(1)

    spec = dataclasses.replace(_spec("iris_briefing"), func=nep)
    assert asyncio.run(S.run_spec_once(spec, source="test")) is True
    assert gedraaid == [1]


def test_ochtendrapport_zorgt_zelf_dat_de_briefing_er_is(monkeypatch):
    """De ketenbreuk van 7 aug 2026, omgedraaid: het rapport wacht op de
    briefing in plaats van te hopen op de juiste volgorde."""
    import dataclasses
    _baseline(S._now() - dt.timedelta(days=7))
    gedraaid = []

    async def nep_briefing():
        gedraaid.append("briefing")

    monkeypatch.setitem(S._BY_ID, "iris_briefing",
                        dataclasses.replace(_spec("iris_briefing"), func=nep_briefing))
    # Ná 06:45 op een gewone werkdag: de briefing was aan de beurt.
    monkeypatch.setattr(S, "_now", lambda: S._TZ.localize(dt.datetime(2026, 7, 10, 8, 33)))

    assert asyncio.run(S.ensure_ran_today("iris_briefing")) is True
    assert gedraaid == ["briefing"]


def test_afhankelijkheid_trekt_niets_naar_voren_dat_nog_niet_aan_de_beurt_was(monkeypatch):
    """Om 05:00 het rapport opvragen hoort geen briefing van 06:45 te starten."""
    import dataclasses
    gedraaid = []

    async def nep_briefing():
        gedraaid.append("briefing")

    monkeypatch.setitem(S._BY_ID, "iris_briefing",
                        dataclasses.replace(_spec("iris_briefing"), func=nep_briefing))
    monkeypatch.setattr(S, "_now", lambda: S._TZ.localize(dt.datetime(2026, 7, 10, 5, 0)))
    monkeypatch.setattr(S, "_last_fire_before", lambda *a, **k: None)

    assert asyncio.run(S.ensure_ran_today("iris_briefing")) is False
    assert gedraaid == []


def test_hartslag_merkt_een_slaapstand_en_start_de_inhaalslag(monkeypatch):
    gestart = []
    monkeypatch.setattr(S, "_start_catchup_ronde", lambda: gestart.append(1))
    monkeypatch.setattr(S, "_catchup_task", None)
    tijden = iter([
        S._TZ.localize(dt.datetime(2026, 7, 9, 22, 30)),   # laatste tik voor het dichtklappen
        S._TZ.localize(dt.datetime(2026, 7, 10, 8, 33)),   # eerste tik na het ontwaken
    ])
    monkeypatch.setattr(S, "_now", lambda: next(tijden))
    S._laatste_hartslag = None

    asyncio.run(S._hartslag())      # eerste tik: alleen vastleggen
    assert gestart == []
    asyncio.run(S._hartslag())      # tien uur later: dat is een ontwaken
    assert gestart == [1]


def test_hartslag_zwijgt_bij_een_normale_tik(monkeypatch):
    gestart = []
    monkeypatch.setattr(S, "_start_catchup_ronde", lambda: gestart.append(1))
    tijden = iter([
        S._TZ.localize(dt.datetime(2026, 7, 10, 8, 33, 0)),
        S._TZ.localize(dt.datetime(2026, 7, 10, 8, 34, 2)),   # 62 seconden later
    ])
    monkeypatch.setattr(S, "_now", lambda: next(tijden))
    S._laatste_hartslag = None

    asyncio.run(S._hartslag())
    asyncio.run(S._hartslag())
    assert gestart == []

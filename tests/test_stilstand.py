"""Werk dat niet gebeurde omdat de machine uit stond, moet zichtbaar zijn.

Aanleiding (2 aug 2026): het dashboard toonde één gemiste run van 22 juli met
de tekst "draait bij de volgende geplande run vanzelf". Ondertussen was de
machine van 28 t/m 31 juli vier werkdagen uit geweest: de outreach-batch vuurde
vier keer niet, de vacaturescan van donderdag ging over, en de
linkbuilding-weekrun miste zijn tweede woensdag op rij — een taak die volgens
`scheduler_runs.last_ok_at` nog nóóit was geslaagd. Nergens stond dat.

De regels die deze tests vastleggen:

  * alleen werk dat waarde houdt wordt gemeld (een ochtendrapport van gisteren
    niet, een outreach-batch wel);
  * een gat sluit zichzelf zodra de taak weer slaagt;
  * "draait vanzelf vanzelf" wordt niet beweerd over een taak die nog nooit
    slaagde;
  * de melding draagt de knop die hem repareert, en verschijnt langs precies
    één weg — twee kaarten voor één beslissing maken de inbox onleesbaar;
  * een gemist vuurmoment is geen uitvoering, dus geen bewijs van een defect.
"""
from datetime import datetime, timedelta

import pytest

from backend import scheduler
from backend.shared import downtime
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def _schoon():
    with get_conn() as conn:
        conn.execute("DELETE FROM scheduler_gaps")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM scheduler_gaps")


def _moment(dagen_geleden: int, uur: int = 7) -> datetime:
    return (datetime.now().astimezone() - timedelta(days=dagen_geleden)).replace(
        hour=uur, minute=15, second=0, microsecond=0)


def test_gat_wordt_maar_een_keer_vastgelegd():
    m = _moment(3)
    assert downtime.record_gap("job_a", "Taak A", m, cost="geen concepten", recoverable=True)
    assert not downtime.record_gap("job_a", "Taak A", m, cost="geen concepten", recoverable=True)
    assert len(downtime.open_gaps()) == 1


def test_zonder_kosten_geen_melding():
    """Een gemiste run waarvan de opbrengst morgen vanzelf weer vers is (een
    ochtendrapport, een sync met terugkijkvenster) wordt wél vastgelegd maar
    nooit gemeld. Anders staat het Actiecentrum na elk weekend vol met ruis
    waar niemand iets aan kan doen."""
    downtime.record_gap("daily_digest", "Ochtendrapport", _moment(2))
    assert downtime.open_gaps(only_reportable=True) == []
    assert len(downtime.open_gaps(only_reportable=False)) == 1
    assert downtime.summary() == []


def test_meerdere_gemiste_runs_worden_een_regel():
    """Vier kaarten voor vier dagen dezelfde stilstand zeggen niets extra's en
    verdringen wel vier andere dingen van het scherm."""
    for d in (4, 3, 2, 1):
        downtime.record_gap("daily_outreach_batch", "Outreach-batch", _moment(d),
                            cost="geen outreach-concepten klaargezet", recoverable=True)
    samenvatting = downtime.summary()
    assert len(samenvatting) == 1
    entry = samenvatting[0]
    assert entry["missed"] == 4
    assert "4×" in entry["detail"]
    assert "geen outreach-concepten klaargezet" in entry["detail"]


def test_geslaagde_run_sluit_de_gaten():
    """Een rode kaart die blijft staan voor iets dat weer werkt is dezelfde
    ruis als een kaart die nooit kwam."""
    downtime.record_gap("job_b", "Taak B", _moment(2), cost="iets", recoverable=True)
    downtime.record_gap("job_b", "Taak B", _moment(1), cost="iets", recoverable=True)
    assert len(downtime.open_gaps()) == 2
    assert downtime.mark_recovered("job_b") == 2
    assert downtime.open_gaps() == []
    assert downtime.summary() == []


def test_scheduler_ok_run_sluit_de_gaten():
    """Niet via de downtime-API maar via het echte pad: elke geslaagde run,
    ook een gewone geplande, hoort de gaten te dichten."""
    from backend import scheduler
    downtime.record_gap("job_c", "Taak C", _moment(1), cost="iets", recoverable=True)
    scheduler._record_run("job_c", "ok", None, source="test")
    assert downtime.open_gaps() == []


def test_mislukte_run_laat_het_gat_open():
    from backend import scheduler
    downtime.record_gap("job_d", "Taak D", _moment(1), cost="iets", recoverable=True)
    scheduler._record_run("job_d", "error", "stuk", source="test")
    assert len(downtime.open_gaps()) == 1


def test_nooit_geslaagde_taak_wordt_herkend():
    """`last_ok_at IS NULL` bij een taak die wél heeft gevuurd is het
    duidelijkste defectsignaal dat dit systeem kent — en het stond nergens."""
    runs = {
        "kapot": {"last_run_at": "2026-07-23T11:39:04+02:00", "last_ok_at": None},
        "gezond": {"last_run_at": "2026-08-02T07:09:17+02:00",
                   "last_ok_at": "2026-08-02T07:09:17+02:00"},
        "nooit_gevuurd": {"last_run_at": None, "last_ok_at": None},
    }
    kapot = downtime.never_succeeded(runs, ["kapot", "gezond", "nooit_gevuurd"])
    assert kapot == ["kapot"]


def test_stilstand_escaleert_niet_naar_een_tweede_kaart():
    """Eén stilstand, één kaart.

    Het Actiecentrum rendert `scheduler_gaps` rechtstreeks, mét de inhaalknop.
    Schreef deze module er óók een uitkomstkaart bij, dan stond elke gemiste
    taak dubbel in de inbox — woordelijk identiek, en de tweede zonder de knop
    die het werk terughaalt (2 aug 2026).
    """
    downtime.record_gap("daily_outreach_batch", "Outreach-batch", _moment(2),
                        cost="geen outreach-concepten klaargezet", recoverable=True)
    with get_conn() as conn:
        voor = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_log WHERE action = 'gemiste_runs'"
        ).fetchone()["n"]
    scheduler._report_downtime(_moment(0))
    with get_conn() as conn:
        na = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_log WHERE action = 'gemiste_runs'"
        ).fetchone()["n"]
    assert na == voor


def test_oude_stilstand_kaart_verdwijnt_uit_de_inbox():
    """De `gemiste_runs`-rijen die er nog liggen zijn dubbelingen van een kaart
    die er al staat, niet openstaand werk — ze horen de hygiëne-pijler niet meer
    te drukken en niet meer in het Actiecentrum te verschijnen."""
    from backend.domains.iris.metrics import _error_resolved

    downtime.record_gap("daily_outreach_batch", "Outreach-batch", _moment(2),
                        cost="geen concepten", recoverable=True)
    kaart = {"action": "gemiste_runs", "created_at": "2026-08-02T07:00:00",
             "detail": "daily_outreach_batch| Outreach-batch draaide 1× niet", "project": "Scheduler"}
    with get_conn() as conn:
        assert _error_resolved(conn, kaart) is True


def test_nooit_geslaagd_kaart_sluit_bij_eerste_succes():
    from backend import scheduler
    from backend.domains.iris.metrics import _error_resolved

    kaart = {"action": "job_nooit_geslaagd", "created_at": "2026-08-02T07:00:00",
             "detail": "test_kapot| Geplande taak is nog nooit geslaagd", "project": "Scheduler"}
    with get_conn() as conn:
        conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_kapot'")
    scheduler._record_run("test_kapot", "error", "stuk", source="test")
    try:
        with get_conn() as conn:
            assert _error_resolved(conn, kaart) is False
        scheduler._record_run("test_kapot", "ok", None, source="test")
        with get_conn() as conn:
            assert _error_resolved(conn, kaart) is True
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_kapot'")


def test_niet_inhaalbaar_werk_wordt_niet_gemeld():
    """Geregistreerd, maar niet meldenswaardig: de opbrengst van een
    ochtendrapport is morgen vanzelf weer vers."""
    downtime.record_gap("daily_digest", "Ochtendrapport", _moment(2))
    assert [e for e in downtime.summary() if e["recoverable"]] == []


def test_gemiste_run_is_geen_run():
    """Een misfire heeft niets uitgevoerd, dus mag hij geen `last_run_at` zetten.

    Deed hij dat wél (tot 2 aug 2026), dan voldeed een taak die tijdens een
    uitgezette machine overging aan de definitie van 'heeft gevuurd, nooit
    geslaagd' en meldde het Actiecentrum hem als defécte taak — de verkeerde
    diagnose met de verkeerde oplossing eronder.
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_gemist'")
    try:
        scheduler._record_run("test_gemist", "missed", "vuurmoment overgeslagen",
                              source="test")
        with get_conn() as conn:
            rij = dict(conn.execute(
                "SELECT * FROM scheduler_runs WHERE job_id = 'test_gemist'").fetchone())
        assert rij["last_run_at"] is None
        assert rij["last_missed_at"]
        assert downtime.never_succeeded({"test_gemist": rij}, ["test_gemist"]) == []

        # Draait hij daarna écht en faalt hij, dán is het wél een defect.
        scheduler._record_run("test_gemist", "error", "stuk", source="test")
        with get_conn() as conn:
            rij = dict(conn.execute(
                "SELECT * FROM scheduler_runs WHERE job_id = 'test_gemist'").fetchone())
        assert rij["last_run_at"]
        assert downtime.never_succeeded({"test_gemist": rij}, ["test_gemist"]) == ["test_gemist"]
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_gemist'")


# ── Het pad van scheduler-spec naar gat ─────────────────────────────────────

def test_fires_between_telt_alle_gemiste_momenten():
    """`scheduler_runs` houdt één rij per taak bij, dus 'vier keer niet
    gedraaid' is daaruit niet af te leiden. De trigger weet het wel."""
    from apscheduler.triggers.cron import CronTrigger
    from backend.scheduler import _fires_between, _TZ

    trigger = CronTrigger(day_of_week="mon-fri", hour=7, minute=15, timezone=_TZ)
    # Een volledige werkweek: maandag t/m vrijdag = 5 vuurmomenten.
    start = datetime(2026, 7, 27, 0, 0, tzinfo=_TZ)   # maandag
    eind = datetime(2026, 8, 1, 0, 0, tzinfo=_TZ)     # zaterdag 00:00
    assert len(_fires_between(trigger, start, eind)) == 5


def test_alleen_catchup_taken_zijn_handmatig_in_te_halen():
    """Een taak met een blijvend neveneffect (de maandelijkse doel-jobs maken
    doelen aan) twee keer draaien laat werk dubbel lopen."""
    import asyncio
    from backend.scheduler import run_job_now, _BY_ID

    niet_inhaalbaar = next(s.id for s in _BY_ID.values() if not s.catch_up)
    with pytest.raises(ValueError):
        asyncio.run(run_job_now(niet_inhaalbaar))
    with pytest.raises(KeyError):
        asyncio.run(run_job_now("bestaat-niet"))


def test_achtergrondrun_wordt_niet_opgeruimd_en_sluit_het_gat():
    """De hele keten in het klein: knop → achtergrondtaak → run → gat dicht.

    De taakreferentie moet vastgehouden worden. De event loop bewaart er maar
    een zwakke, dus zonder eigen verwijzing kan de garbage collector de run
    halverwege opruimen: "gestart" in de log, geen spoor van een run, en een
    gat dat open blijft zonder dat iemand weet waarom.
    """
    import asyncio
    from backend import scheduler

    gedraaid = []

    async def nep_job():
        await asyncio.sleep(0)
        gedraaid.append(1)

    spec = scheduler.JobSpec("test_inhaal", "Testtaak", nep_job,
                             scheduler._cron(hour=7), catch_up=True,
                             gap_cost="testwerk")
    scheduler._BY_ID["test_inhaal"] = spec
    try:
        downtime.record_gap("test_inhaal", "Testtaak", _moment(1),
                            cost="testwerk", recoverable=True)

        async def scenario():
            res = await scheduler.run_job_now("test_inhaal")
            assert res["started"] is True
            # Referentie moet bestaan zolang de taak loopt.
            assert scheduler._manual_tasks, "achtergrondtaak wordt niet vastgehouden"
            await asyncio.gather(*list(scheduler._manual_tasks))

        asyncio.run(scenario())
        assert gedraaid == [1]
        assert downtime.open_gaps() == [], "een geslaagde run hoort het gat te sluiten"
    finally:
        scheduler._BY_ID.pop("test_inhaal", None)
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_inhaal'")


def test_actiecentrum_toont_stilstand_met_een_knop():
    from backend.domains.action_center.service import build_inbox

    downtime.record_gap("daily_outreach_batch", "Outreach-batch", _moment(2),
                        cost="geen outreach-concepten klaargezet", recoverable=True)
    items = build_inbox()["items"]
    kaart = next((i for i in items
                  if i.get("dismiss_kind") == "scheduler_gap"), None)
    assert kaart is not None, "stilstand hoort in het Actiecentrum te staan"
    assert "Outreach-batch" in kaart["title"]
    labels = [a["type"] for a in kaart["actions"]]
    assert "run_job" in labels, "een melding zonder reparatieknop is een mededeling"


# ── Een nieuwe taak heeft het verleden niet gemist ─────────────────────────

def test_nieuwe_job_krijgt_geen_stilstand_uit_het_verleden():
    """Aanleiding (3 aug 2026): het toevoegen van 'invest_daily_cycle' leverde
    meteen de kaart "draaide 9× tussen 21-07 en 31-07 niet — stops en
    koersdoelen van de open posities zijn niet getoetst" op. Er waren op 21 juli
    geen posities, geen stops en geen job.

    `_baseline` doet dit al voor de hele installatie ("een verse installatie zou
    meteen twee weken stilstand rapporteren over runs die nooit hadden hoeven
    draaien"); dezelfde redenering geldt per job. Een JobSpec die vandaag wordt
    toegevoegd, is voor zichzelf een verse installatie.
    """
    from apscheduler.triggers.cron import CronTrigger

    nieuw = scheduler.JobSpec(
        "test_verse_job", "Verse taak", lambda: None,
        CronTrigger(hour=7, minute=0, timezone=scheduler._TZ),
        catch_up=True, gap_cost="werk dat waarde houdt",
    )
    origineel = list(scheduler._SPECS)
    scheduler._SPECS.append(nieuw)
    try:
        now = scheduler._now()
        # Nulmeting van twee weken terug: zonder ondergrens per job zou de
        # teller élk vuurmoment sindsdien aan deze verse job toerekenen.
        baseline = now - timedelta(days=14)
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_verse_job'")

        scheduler._record_downtime_gaps(now, scheduler._load_runs(), baseline)

        gaten = [g for g in downtime.open_gaps(only_reportable=False)
                 if g["job_id"] == "test_verse_job"]
        assert gaten == [], (
            "een taak die vandaag is toegevoegd, heeft de vuurmomenten van vorige "
            f"week niet gemist — toch werden er {len(gaten)} geregistreerd")
    finally:
        scheduler._SPECS[:] = origineel
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_verse_job'")
            conn.execute("DELETE FROM scheduler_gaps WHERE job_id = 'test_verse_job'")


def test_bekende_job_blijft_wel_stilstand_melden():
    """De ondergrens mag geen stilzwijgen worden: een taak die er al was, hoort
    zijn gemiste vuurmomenten gewoon te melden."""
    from apscheduler.triggers.cron import CronTrigger

    bekend = scheduler.JobSpec(
        "test_oude_job", "Oude taak", lambda: None,
        CronTrigger(hour=7, minute=0, timezone=scheduler._TZ),
        catch_up=True, gap_cost="werk dat waarde houdt",
    )
    origineel = list(scheduler._SPECS)
    scheduler._SPECS.append(bekend)
    try:
        now = scheduler._now()
        baseline = now - timedelta(days=14)
        # Deze job bestond al: eerst gezien op de nulmeting.
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_runs (job_id, status, first_seen_at, source) "
                "VALUES ('test_oude_job', 'onbekend', ?, 'seed')", (baseline.isoformat(),))

        scheduler._record_downtime_gaps(now, scheduler._load_runs(), baseline)

        gaten = [g for g in downtime.open_gaps(only_reportable=False)
                 if g["job_id"] == "test_oude_job"]
        assert len(gaten) >= 5, "een bestaande taak hoort zijn gemiste runs wél te melden"
    finally:
        scheduler._SPECS[:] = origineel
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_oude_job'")
            conn.execute("DELETE FROM scheduler_gaps WHERE job_id = 'test_oude_job'")


def test_seed_rij_leest_niet_als_uitvoering():
    """Een rij die alleen `first_seen_at` draagt registreert dát de job bestaat,
    niet dat hij heeft gedraaid. Zou hij als run lezen, dan is "nooit gedraaid"
    na een herstart niet meer te onderscheiden — en daar hangt de inhaalslag aan."""
    now = scheduler._now()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scheduler_runs (job_id, status, first_seen_at, source) "
            "VALUES ('test_seed_only', 'onbekend', ?, 'seed')", (now.isoformat(),))
    try:
        runs = scheduler._load_runs()
        rij = runs["test_seed_only"]
        assert not rij["last_run_at"] and not rij["last_missed_at"]
        # never_succeeded mag hem niet als defect bestempelen: hij heeft nooit gevuurd.
        assert downtime.never_succeeded(runs, ["test_seed_only"]) == []
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_seed_only'")


def test_invariant_vindt_stilstand_ouder_dan_de_job():
    from backend.domains.iris import integrity

    now = scheduler._now()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scheduler_runs (job_id, status, first_seen_at, source) "
            "VALUES ('test_jong', 'onbekend', ?, 'seed')", (now.isoformat(),))
    downtime.record_gap("test_jong", "Jonge taak", now - timedelta(days=5),
                        cost="iets", recoverable=True)
    try:
        bevindingen = integrity.invariant("stilstand_ouder_dan_de_job").check()
        assert any(b.subject.startswith("gap:test_jong") for b in bevindingen)
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM scheduler_runs WHERE job_id = 'test_jong'")
            conn.execute("DELETE FROM scheduler_gaps WHERE job_id = 'test_jong'")

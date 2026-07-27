"""Tests voor Iris' zelfherstel: wat lost ze zelf op, en wat meldt ze?

De aanleiding (25 jul 2026) staat model voor het hele mechanisme. Er lagen drie
rode kaarten in het Actiecentrum:

  1. "Ophalen van instagram mislukt:" — leeg, want `str(httpx.ConnectError())`
     is een lege string. Oorzaak: een TLS-blip om 01:41 's nachts.
  2. Dezelfde kaart nog eens, van de nacht ervoor.
  3. "Google Agenda-sync faalde: ('Connection aborted.', RemoteDisconnected)".

Alle drie waren voorbij op het moment dat Vincent ze las. Ondertussen was het
IG-token op 13 juli verlopen (HTTP 400, OAuthException 190) — het énige echte
probleem, en juist dát was onzichtbaar omdat de fetch bij een non-200 stil `[]`
teruggaf.

Deze tests leggen die twee kanten vast: blips gaan vanzelf dicht, een verlopen
token komt hard bovendrijven en blijft staan tot een mens ingrijpt.
"""
import asyncio
import uuid

import pytest

from backend.domains.iris import selfheal
from backend.shared import failures as fail
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def clean():
    for tbl in ("activity_log", "iris_heal_log", "iris_error_fixes",
                "agent_failure_streaks", "inbox_dismissals"):
        with get_conn() as c:
            c.execute(f"DELETE FROM {tbl}")
    yield


def _error_card(action: str, detail: str, project: str = "BewaardVoorJou") -> str:
    cid = str(uuid.uuid4())
    with get_conn() as c:
        c.execute(
            "INSERT INTO activity_log (id, project, action, detail, artifact, next_step, "
            "status, created_at) VALUES (?,?,?,?,'','Controleer de tokens','error', "
            "datetime('now', '-1 hour'))",
            (cid, project, action, detail),
        )
    return cid


def _is_dismissed(cid: str) -> bool:
    with get_conn() as c:
        return bool(c.execute(
            "SELECT 1 FROM inbox_dismissals WHERE kind='error' AND ref_id=?", (cid,)
        ).fetchone())


# ── Classificatie: het onderscheid waar alles op hangt ─────────────────────

def test_lege_exception_krijgt_altijd_een_leesbare_tekst():
    """De bug uit de screenshot: 'mislukt:' zonder enige uitleg."""
    import httpx
    assert str(httpx.ConnectError("")) == ""
    beschrijving = fail.describe_exception(httpx.ConnectError(""))
    assert beschrijving
    assert "verbinding" in beschrijving.lower()


def test_netwerkblip_is_transient_en_verlopen_token_is_auth():
    import httpx
    assert fail.classify(httpx.ConnectError("")) == fail.CLASS_TRANSIENT
    assert fail.classify(
        "('Connection aborted.', RemoteDisconnected('Remote end closed connection'))"
    ) == fail.CLASS_TRANSIENT
    assert fail.classify(
        "Error validating access token: Session has expired on Monday, 13-Jul-26"
    ) == fail.CLASS_AUTH
    # Mens-alleen: hier heeft nóg een poging geen enkele zin.
    assert fail.is_human_only("OAuthException: token expired")
    assert not fail.is_human_only(httpx.ReadTimeout(""))


def test_faalreeks_escaleert_pas_na_drie_pogingen():
    key = "social_fetch:test"
    blip = "geen verbinding met de server"
    for verwacht in (1, 2):
        assert fail.note_failure(key, blip, fail.CLASS_TRANSIENT) == verwacht
        assert not fail.should_escalate(key, blip)
    assert fail.note_failure(key, blip, fail.CLASS_TRANSIENT) == 3
    assert fail.should_escalate(key, blip)
    # Geslaagde poll wist de reeks — anders escaleert de volgende losse blip
    # meteen omdat de teller nog hoog staat.
    assert fail.note_success(key) == 3
    assert not fail.should_escalate(key, blip)


def test_verlopen_token_escaleert_direct_ondanks_lege_reeks():
    key = "social_fetch:auth"
    auth = "Error validating access token: Session has expired"
    fail.note_failure(key, auth, fail.CLASS_AUTH)
    assert fail.should_escalate(key, auth)


# ── De ronde zelf ──────────────────────────────────────────────────────────

def test_netwerkblip_wordt_zelf_opgelost_en_verdwijnt_uit_de_inbox(monkeypatch):
    cid = _error_card("social_fetch", "Ophalen van instagram mislukt: verbinding brak af (TLS)")

    async def _fetch_lukt_weer(case):
        return True, "instagram: ok (0 nieuw)"

    monkeypatch.setattr(selfheal, "_probe_social_fetch", _fetch_lukt_weer)
    monkeypatch.setattr(selfheal, "_probe_for", lambda case: _fetch_lukt_weer)

    report = asyncio.run(selfheal.run_selfheal(source="test"))

    assert report["healed"] == 1
    assert _is_dismissed(cid), "een aantoonbaar opgeloste fout hoort niet meer in de inbox"
    with get_conn() as c:
        heal = c.execute(
            "SELECT detail FROM activity_log WHERE action='iris_zelfherstel' "
            "AND status='ok'").fetchone()
    assert heal and "zelf op" in heal["detail"]


def test_verlopen_token_wordt_meteen_gemeld_met_een_bruikbare_stap(monkeypatch):
    """Geen probe, geen uitstel: alleen een mens kan een token vernieuwen."""
    cid = _error_card(
        "social_fetch",
        "Het instagram-kanaal wijst ons af: Error validating access token: "
        "Session has expired on Monday, 13-Jul-26.",
    )

    def _geen_probes(case):
        raise AssertionError("bij een verlopen token hoort niet geprobeerd te worden")

    monkeypatch.setattr(selfheal, "_probe_for", _geen_probes)

    report = asyncio.run(selfheal.run_selfheal(source="test"))

    assert report["escalated"] == 1
    assert not _is_dismissed(cid), "deze kaart moet blijven staan tot Vincent hem oplost"
    with get_conn() as c:
        row = c.execute("SELECT next_step FROM activity_log WHERE id=?", (cid,)).fetchone()
    # De oude tekst ("Controleer de tokens") was niet fout maar wél nutteloos:
    # er moet staan wáár en waaróm.
    assert "Social-tab" in row["next_step"]
    assert "token" in row["next_step"].lower()


def test_aanhoudende_storing_wordt_na_drie_pogingen_alsnog_gemeld(monkeypatch):
    _error_card("social_fetch", "Ophalen van instagram mislukt: netwerk weg")

    async def _blijft_falen(case):
        return False, "instagram: geen verbinding met de server"

    monkeypatch.setattr(selfheal, "_probe_for", lambda case: _blijft_falen)

    for ronde in range(3):
        report = asyncio.run(selfheal.run_selfheal(source="test"))
        assert report["escalated"] == 0, f"ronde {ronde}: te vroeg gemeld"
        assert report["results"][0]["result"] == "retry_later"

    report = asyncio.run(selfheal.run_selfheal(source="test"))
    assert report["escalated"] == 1, "na drie mislukte pogingen hoort het gemeld te worden"


def test_probe_die_de_echte_oorzaak_vindt_meldt_meteen(monkeypatch):
    """Precies het geval uit de screenshot: de kaart zegt niets ("mislukt: "),
    maar één verse poging legt een verlopen token bloot. Dan hoort Iris niet
    twee rondes door te proberen, maar meteen te melden — mét die oorzaak."""
    cid = _error_card("social_fetch", "Ophalen van instagram mislukt: ")

    async def _vindt_dood_token(case):
        return False, ("instagram: Error validating access token: Session has "
                       "expired on Monday, 13-Jul-26")

    monkeypatch.setattr(selfheal, "_probe_for", lambda case: _vindt_dood_token)
    report = asyncio.run(selfheal.run_selfheal(source="test"))

    assert report["escalated"] == 1
    assert report["results"][0]["class"] == fail.CLASS_AUTH
    with get_conn() as c:
        row = c.execute("SELECT detail, next_step FROM activity_log WHERE id=?",
                        (cid,)).fetchone()
    assert "access token" in row["detail"], "de lege kaart hoort aangevuld te zijn"
    assert "Social-tab" in row["next_step"]


def test_leert_van_verwante_fouten(monkeypatch):
    """Een nieuwe fout in dezelfde hoek erft wat een eerdere opleverde."""
    _error_card("social_fetch", "Ophalen van instagram mislukt: netwerk weg")

    async def _lukt(case):
        return True, "ok"

    monkeypatch.setattr(selfheal, "_probe_for", lambda case: _lukt)
    asyncio.run(selfheal.run_selfheal(source="test"))

    # Andere tekst (andere handtekening), zelfde actie + faalklasse.
    selfheal._close_case  # noqa: B018 — leesbaarheid: dit is het pad hierboven
    geleerd = selfheal._learned(
        "social_fetch::iets heel anders", "social_fetch", fail.CLASS_TRANSIENT
    )
    assert geleerd is not None, "verwante fout hoort de bewezen remedie te erven"
    assert geleerd.get("_inherited") is True
    assert geleerd["remedy_type"] == "probe"


def test_zware_jobs_worden_nooit_zomaar_opnieuw_gedraaid():
    """Een probe mag lezen/synchroniseren, nooit werk aanmaken — anders
    passeert zelfherstel via een omweg een review-gate."""
    case = {"kind": "scheduler", "id": "biweekly_content", "project": "Scheduler",
            "action": "scheduler:biweekly_content", "detail": "timeout"}
    ok, note = asyncio.run(selfheal._probe_scheduler_job(case))
    assert ok is False
    assert "niet automatisch herhaald" in note


def test_synchrone_job_met_eigen_event_loop_wordt_correct_geprobeerd(monkeypatch):
    """`calendar_sync_job` is synchroon en doet intern `asyncio.run()`. Roep je
    hem rechtstreeks aan vanuit de (async) zelfherstel-ronde, dan valt hij om op
    "cannot be called from a running event loop" — en zou Iris een gezonde job
    als kapot rapporteren. Daarom draait een synchrone job in een thread."""
    from backend import scheduler as S

    gedraaid = []

    def _sync_job_met_eigen_loop():
        async def _werk():
            return "klaar"
        gedraaid.append(asyncio.run(_werk()))

    spec = S.JobSpec("calendar_sync", "test", _sync_job_met_eigen_loop,
                     S.IntervalTrigger(minutes=15))
    monkeypatch.setitem(S._BY_ID, "calendar_sync", spec)
    monkeypatch.setattr(S, "_record_run", lambda *a, **k: None)

    case = {"kind": "scheduler", "id": "calendar_sync", "project": "Scheduler",
            "action": "scheduler:calendar_sync", "detail": "Connection aborted"}
    ok, note = asyncio.run(selfheal._probe_scheduler_job(case))
    assert ok is True, note
    assert gedraaid == ["klaar"]


def test_herstelde_schedulerjob_wordt_nooit_permanent_weggeklikt():
    """Een scheduler-item spiegelt `scheduler_runs`; wegklikken is voorgoed
    (inbox_dismissals kent geen verval). Doe je dat bij zelfherstel, dan is de
    job ook onzichtbaar als hij volgende week écht stukgaat."""
    case = {"kind": "scheduler", "id": "calendar_sync", "project": "Scheduler",
            "action": "scheduler:calendar_sync", "detail": "Connection aborted"}
    selfheal._close_case(case, "job opnieuw gedraaid en geslaagd")
    with get_conn() as c:
        weggeklikt = c.execute(
            "SELECT 1 FROM inbox_dismissals WHERE ref_id = 'calendar_sync'").fetchone()
        gemeld = c.execute(
            "SELECT detail FROM activity_log WHERE action='iris_zelfherstel'").fetchone()
    assert weggeklikt is None
    assert gemeld and "zelf op" in gemeld["detail"]


def test_ronde_valt_nooit_om_op_een_kapotte_probe(monkeypatch):
    _error_card("social_fetch", "Ophalen van instagram mislukt: netwerk weg")

    async def _explodeert(case):
        raise RuntimeError("probe zelf stuk")

    monkeypatch.setattr(selfheal, "_probe_for", lambda case: _explodeert)
    report = asyncio.run(selfheal.run_selfheal(source="test"))
    assert report["ok"] is True
    assert report["results"][0]["result"] == "retry_later"

"""Opschoonronde naar wereldklasse (85+) — `backend/domains/publish/upgrade.py`.

Waar deze tests over gaan: de reviewer varieert flink op identieke invoer
(65-92 waargenomen op hetzelfde artikel, zie CLAUDE.md punt 6). Een lus die
stopt zodra hij één keer boven de lat meet, levert daarom een lijst op die op
papier 85+ is en in werkelijkheid rond het gemiddelde hangt — er is dan alleen
geselecteerd op mázzel. De kern van deze module is dat een score pas telt als
twee onafhankelijke metingen hem halen, en dat de laagste wordt opgeslagen.

Deze tests bewijzen dat, plus de harde grens die er omheen staat: de
opschoonronde publiceert nooit.
"""
import asyncio
from datetime import datetime

import pytest

from backend.domains.publish import content_pipeline as cp
from backend.domains.publish import upgrade


BODY = "<h1>Test Artikel</h1><p>" + ("inhoud " * 40) + "</p>"


@pytest.fixture()
def seeded():
    """Eén site + jobs, schoon per test."""
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sites (id,name,base_url,auto_content_enabled,created_at) "
            "VALUES (?,?,?,?,?)", ("up1", "UpSite", "http://up.test", 0,
                                   datetime.now().isoformat()))
        conn.execute("DELETE FROM content_jobs WHERE site_id='up1'")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM content_jobs WHERE site_id='up1'")
        conn.execute("DELETE FROM activity_log WHERE action='content-opschoonronde'")


def _job(job_id, score, status="pending_review", keyword="test keyword", body=BODY):
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO content_jobs (id,site_id,title,keyword,status,blog_html,seo_score,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, "up1", "Test Artikel", keyword, status, body, score,
             datetime.now().isoformat()))
    return job_id


def _patch(monkeypatch, improve_scores, confirm_scores):
    """Mock de twee LLM-stappen met een vaste reeks scores.

    `improve_scores` is wat `review_and_improve` teruggeeft, `confirm_scores`
    wat de onafhankelijke hermeting geeft — precies de twee metingen waarvan
    deze module beweert dat ze niet dezelfde zijn.
    """
    calls = {"improve": 0, "confirm": 0}

    async def fake_improve(site, kw, html, max_rounds=6, target_score=None,
                           exclude_job_id=None):
        i = min(calls["improve"], len(improve_scores) - 1)
        calls["improve"] += 1
        return html + "<p>verbeterd</p>", {"score": improve_scores[i], "feedback": "f"}

    async def fake_review(site, kw, html):
        i = min(calls["confirm"], len(confirm_scores) - 1)
        calls["confirm"] += 1
        s = confirm_scores[i]
        return None if s is None else {"score": s, "feedback": "f"}

    monkeypatch.setattr(cp, "review_and_improve", fake_improve)
    monkeypatch.setattr(cp, "_review_article", fake_review)
    return calls


# ── De kern: één meting boven de lat is geen bewijs ──────────────────────────

def test_enkele_hoge_meting_telt_niet_als_gehaald(seeded, monkeypatch):
    """88 bij de verbeter-loop, 80 bij de hermeting → niet gehaald, 80 opgeslagen.

    Dit is de mázzel-meting die de hele opschoonronde waardeloos zou maken:
    zonder bevestiging zou dit artikel als 'wereldklasse' in de lijst staan.
    """
    _job("u_luck", 82)
    _patch(monkeypatch, improve_scores=[88, 84], confirm_scores=[80, 82])

    rep = asyncio.run(upgrade.upgrade_job("u_luck", target=85))

    assert rep["reached"] is False, "één hoge meting mag niet als gehaald tellen"
    assert rep["confirmed"] is False
    # De laagste van de twee metingen wordt opgeslagen, niet de hoogste.
    assert rep["after"] <= 84, f"opgeslagen score {rep['after']} is te optimistisch"
    assert cp.get_job("u_luck")["seo_score"] <= 84


def test_twee_metingen_boven_de_lat_telt_wel(seeded, monkeypatch):
    _job("u_ok", 82)
    calls = _patch(monkeypatch, improve_scores=[88], confirm_scores=[87])

    rep = asyncio.run(upgrade.upgrade_job("u_ok", target=85))

    assert rep["reached"] is True
    assert rep["confirmed"] is True
    # Het minimum van de twee: 87, niet 88.
    assert rep["after"] == 87
    assert calls["confirm"] == 1, "precies één bevestiging, geen extra kosten"


def test_mislukte_bevestiging_claimt_niets(seeded, monkeypatch):
    """Reviewer geeft niets bruikbaars terug bij de hermeting.

    Onbekend is niet geslaagd: zonder tweede meting blijft het een enkele
    waarneming, en die claim doen we niet.
    """
    _job("u_none", 82)
    _patch(monkeypatch, improve_scores=[90], confirm_scores=[None])

    rep = asyncio.run(upgrade.upgrade_job("u_none", target=85))

    assert rep["reached"] is False
    assert "één waarneming" in rep["note"]


def test_tegenvallende_bevestiging_krijgt_nog_een_ronde(seeded, monkeypatch):
    """Eerste bevestiging valt tegen → nog één verbeterronde, dan alsnog gehaald."""
    _job("u_retry", 82)
    calls = _patch(monkeypatch, improve_scores=[86, 90], confirm_scores=[81, 88])

    rep = asyncio.run(upgrade.upgrade_job("u_retry", target=85))

    assert calls["improve"] == 2, "een tegenvallende bevestiging hoort een ronde te triggeren"
    assert rep["reached"] is True
    assert rep["after"] == 88


def test_hoogstens_twee_rondes(seeded, monkeypatch):
    """De kosten per artikel blijven begrensd, ook als bevestigen blijft mislukken."""
    _job("u_cap", 82)
    calls = _patch(monkeypatch, improve_scores=[90], confirm_scores=[70])

    rep = asyncio.run(upgrade.upgrade_job("u_cap", target=85))

    assert calls["improve"] == 2, f"te veel verbeterrondes: {calls['improve']}"
    assert rep["reached"] is False


# ── De grens eromheen: publiceren gebeurt hier niet ──────────────────────────

def test_publiceert_nooit(seeded, monkeypatch):
    """Geen enkele uitkomst van de opschoonronde mag een artikel live zetten."""
    _job("u_pub", 82)
    _patch(monkeypatch, improve_scores=[95], confirm_scores=[95])

    called = {"publish": 0}

    async def boom(*a, **k):
        called["publish"] += 1
        raise AssertionError("approve_and_publish mag hier nooit aangeroepen worden")

    monkeypatch.setattr(cp, "approve_and_publish", boom)
    asyncio.run(upgrade.upgrade_job("u_pub", target=85))

    assert called["publish"] == 0
    assert cp.get_job("u_pub")["status"] == "pending_review", "status mag niet naar published"


def test_verse_meting_onder_de_gate_degradeert_naar_needs_work(seeded, monkeypatch):
    """Een opgeslagen 82 die vers op 74 uitkomt is geen publiceerbaar artikel.

    De oude score laten staan omdat hij mooier was, is precies de stille leugen
    waarop `approve_and_publish` zou publiceren op een cijfer dat niemand meer
    gemeten heeft.
    """
    _job("u_drop", 82)
    _patch(monkeypatch, improve_scores=[74], confirm_scores=[74])

    rep = asyncio.run(upgrade.upgrade_job("u_drop", target=85))

    assert rep["after"] == 74
    assert cp.get_job("u_drop")["status"] == "needs_work"


def test_hersteld_artikel_gaat_terug_naar_de_wachtrij(seeded, monkeypatch):
    _job("u_fix", 62, status="needs_work")
    _patch(monkeypatch, improve_scores=[88], confirm_scores=[86])

    asyncio.run(upgrade.upgrade_job("u_fix", target=85))

    assert cp.get_job("u_fix")["status"] == "pending_review"


# ── Batch ────────────────────────────────────────────────────────────────────

def test_batch_slaat_over_wat_de_lat_al_haalt(seeded, monkeypatch):
    _job("b_low", 82)
    _job("b_high", 88)
    calls = _patch(monkeypatch, improve_scores=[86], confirm_scores=[86])

    res = asyncio.run(upgrade.upgrade_batch(target=85, site_id="up1"))

    assert res["considered"] == 1, "alleen het artikel onder de lat hoort opgepakt"
    assert calls["improve"] == 1
    assert [r["job_id"] for r in res["results"]] == ["b_low"]


def test_batch_raakt_gepubliceerd_en_afgewezen_niet_aan(seeded, monkeypatch):
    """'published' staat live (andere operatie), 'rejected' is een mensbesluit."""
    _job("b_pub", 81, status="published")
    _job("b_rej", 40, status="rejected")
    _patch(monkeypatch, improve_scores=[90], confirm_scores=[90])

    res = asyncio.run(upgrade.upgrade_batch(target=85, site_id="up1"))

    assert res["considered"] == 0
    assert cp.get_job("b_pub")["seo_score"] == 81
    assert cp.get_job("b_rej")["seo_score"] == 40


def test_batch_stopt_bij_uitgeputte_quota(seeded, monkeypatch):
    """Halverwege stoppen mag, stilletjes doorbranden niet."""
    for i in range(4):
        _job(f"b_q{i}", 82)
    _patch(monkeypatch, improve_scores=[88], confirm_scores=[88])

    seen = {"n": 0}

    def budget_op():
        seen["n"] += 1
        return seen["n"] > 2  # eerste twee artikelen mogen, daarna op

    monkeypatch.setattr("backend.shared.outcomes.llm_budget_exceeded", budget_op)
    res = asyncio.run(upgrade.upgrade_batch(target=85, site_id="up1"))

    assert res["considered"] == 4
    assert res["processed"] < 4, "de batch hoort te stoppen als het budget op is"


def test_batch_is_hervatbaar(seeded, monkeypatch):
    """Wat bevestigd boven de lat staat, valt bij een tweede run buiten de selectie."""
    _job("b_r1", 82)
    _patch(monkeypatch, improve_scores=[90], confirm_scores=[89])

    first = asyncio.run(upgrade.upgrade_batch(target=85, site_id="up1"))
    second = asyncio.run(upgrade.upgrade_batch(target=85, site_id="up1"))

    assert first["reached"] == 1
    assert second["considered"] == 0, "af is af — geen tweede ronde tokens"


def test_batch_logt_wat_aantoonbaar_gehaald_is(seeded, monkeypatch):
    """Activiteit is geen effect: de kaart meldt bevestigde treffers, niet 'behandeld'."""
    _job("b_a", 82)
    _job("b_b", 83)
    # De eerste haalt het, de tweede niet.
    _patch(monkeypatch, improve_scores=[90, 82, 82], confirm_scores=[88, 80, 80])

    res = asyncio.run(upgrade.upgrade_batch(target=85, site_id="up1"))

    from backend.shared.database import get_conn
    with get_conn() as c:
        row = c.execute(
            "SELECT detail, status FROM activity_log WHERE action='content-opschoonronde' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None, "een batch hoort een uitkomstkaart te loggen"
    assert str(res["reached"]) in row["detail"]
    assert "eronder" in row["detail"], "wat niet gehaald is hoort expliciet in de kaart"


def test_leeg_artikel_verbruikt_geen_tokens(seeded, monkeypatch):
    _job("u_empty", 82, body="")
    calls = _patch(monkeypatch, improve_scores=[90], confirm_scores=[90])

    rep = asyncio.run(upgrade.upgrade_job("u_empty", target=85))

    assert calls["improve"] == 0
    assert rep["reached"] is False
    assert "Geen artikeltekst" in rep["note"]


def test_leeg_keyword_valt_terug_op_de_titel(seeded, monkeypatch):
    """Goal-engine-jobs hebben een leeg keyword; zonder terugval trekt de
    reviewer elke ronde punten af voor een gebrek dat geen herschrijving fikst."""
    _job("u_nokw", 82, keyword="")
    seen = {}

    async def fake_improve(site, kw, html, max_rounds=6, target_score=None,
                           exclude_job_id=None):
        seen["kw"] = kw
        return html, {"score": 90, "feedback": "f"}

    async def fake_review(site, kw, html):
        return {"score": 90, "feedback": "f"}

    monkeypatch.setattr(cp, "review_and_improve", fake_improve)
    monkeypatch.setattr(cp, "_review_article", fake_review)
    asyncio.run(upgrade.upgrade_job("u_nokw", target=85))

    assert seen["kw"] == "Test Artikel", "leeg keyword hoort terug te vallen op de titel"

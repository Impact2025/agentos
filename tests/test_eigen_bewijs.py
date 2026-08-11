"""Information gain: publiceren we iets dat alleen wíj kunnen schrijven?

Aanleiding (5 aug 2026, gemeten op `data/agentos.db`): de kennisbank-haak
bestaat al sinds de Goldie-pipeline — `_make_outline` eist dat één sectie de
casestudy als bewijs gebruikt — maar `case_studies` bevatte 4 rijen op één van
de twaalf sites, en van de 138 artikelen met een QC-rapport hadden er 7 een écht
gekoppelde casestudy. Ruwweg 95% van wat we publiceren is dus reproduceerbare
AI-tekst. De kwaliteitsgate (80) ziet daar niets van: die meet vorm, en generiek
scoort probleemloos 84.

Wat deze tests vastleggen:

  * de bewijs-toets is deterministisch en meet herkomst, niet stijl;
  * hij blokkeert niets — hij vult `qc_report`, zodat een lege kennisbank de
    contentmotor niet stilzet (dat zou de verkeerde straf voor het verkeerde
    onderdeel zijn);
  * "geen casestudy in de kennisbank" en "casestudy niet verwerkt" blijven
    gescheiden: die twee wijzen naar verschillende mensen;
  * de invariant telt per site, en 'niet gemeten' leest nooit als 'geen bewijs';
  * de contentleerlus die 'ok' meldt en nul lessen oplevert wordt zichtbaar,
    mét de gemeten oorzaak — anders is "nog niets te leren" niet te
    onderscheiden van "de meting werkt niet".
"""
import json

import pytest

from backend.domains.iris import integrity
from backend.domains.publish import article_writer
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def _schoon():
    def leeg():
        with get_conn() as conn:
            for t in ("case_studies", "content_jobs", "sites", "agent_lessons",
                      "scheduler_runs"):
                conn.execute(f"DELETE FROM {t}")
    leeg()
    yield
    leeg()


CASE = {
    "id": "cs-1",
    "title": "Gemeente Haarlem: wachtlijst jeugdzorg gehalveerd",
    "summary": "In 14 weken van 340 naar 165 wachtenden.",
    "body": "De doorlooptijd daalde met 42% en de kosten per traject met 1.250 euro.",
    "tags": "jeugdzorg, wachtlijst, gemeente",
    "source_url": "https://weareimpact.nl/cases/haarlem",
}


# ── De bewijs-toets zelf ────────────────────────────────────────────────────

def test_cijfer_uit_de_casestudy_telt_als_bewijs():
    """Het hardste signaal: een getal dat alleen uit ons eigen dossier komt."""
    html = "<h1>Wachtlijsten</h1><p>Bij een gemeente daalde de doorlooptijd met 42%.</p>"
    oordeel = article_writer.check_own_evidence(html, CASE)
    assert oordeel["pass"] is True
    assert any(s.startswith("cijfer:") for s in oordeel["signalen"])


def test_bronlink_telt_als_bewijs():
    html = '<p>Lees de <a href="https://weareimpact.nl/cases/haarlem">casebeschrijving</a>.</p>'
    assert article_writer.check_own_evidence(html, CASE)["pass"] is True


def test_kenmerkende_woorden_tellen_pas_vanaf_twee():
    """Eén gedeeld woord is bij een casestudy over hetzelfde vakgebied vrijwel
    gegarandeerd — dan zou élk artikel slagen en meet de vlag niets."""
    een = "<p>Dit artikel gaat over jeugdzorg in het algemeen.</p>"
    assert article_writer.check_own_evidence(een, CASE)["pass"] is False
    twee = "<p>Over de wachtlijst in de jeugdzorg bij een gemeente.</p>"
    assert article_writer.check_own_evidence(twee, CASE)["pass"] is True


def test_generiek_artikel_zakt_ook_met_gekoppelde_casestudy():
    html = ("<h1>Digitale transformatie</h1><p>Organisaties staan voor de opgave om "
            "processen slimmer in te richten. Dat vraagt om visie en samenwerking.</p>")
    oordeel = article_writer.check_own_evidence(html, CASE)
    assert oordeel["pass"] is False
    assert oordeel["reden"] == "casestudy-niet-verwerkt"


def test_lege_kennisbank_en_ongebruikte_kennisbank_zijn_twee_redenen():
    """Ze wijzen naar verschillende mensen: de eerste is werk voor Vincent, de
    tweede een gat in de schrijfketen."""
    leeg = article_writer.check_own_evidence("<p>tekst</p>", None,
                                             site_has_case_studies=False)
    assert leeg["reden"] == "geen-casestudy-in-kennisbank"
    ongekoppeld = article_writer.check_own_evidence("<p>tekst</p>", None,
                                                    site_has_case_studies=True)
    assert ongekoppeld["reden"] == "geen-casestudy-gekoppeld"


def test_losse_kleine_getallen_zijn_geen_bewijs():
    """'3 tips' en '2026' staan in elk artikel; alleen een percentage of een
    getal uit het dossier zelf mag meetellen."""
    case = dict(CASE, summary="Wij deden 3 sessies.", body="", source_url="", tags="")
    html = "<p>In 3 stappen naar beter beleid.</p>"
    assert article_writer.check_own_evidence(html, case)["pass"] is False


# ── De invariant ────────────────────────────────────────────────────────────

_NU = "2026-08-05T09:00:00+00:00"


def _site(conn, site_id, naam):
    conn.execute("INSERT INTO sites (id, name, created_at) VALUES (?, ?, ?)",
                 (site_id, naam, _NU))


def _job(conn, site_id, job_id, qc=None):
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, status, slug, qc_report, created_at) "
        "VALUES (?, ?, ?, 'published', ?, ?, ?)",
        (job_id, site_id, f"Artikel {job_id}", f"artikel-{job_id}",
         json.dumps(qc) if qc is not None else "", _NU),
    )


def test_site_zonder_casestudies_wordt_gemeld():
    with get_conn() as conn:
        _site(conn, "s1", "WeAreImpact")
        for i in range(4):
            _job(conn, "s1", f"j{i}")
    bevindingen = integrity._check_artikel_zonder_eigen_bewijs()
    assert [b.subject for b in bevindingen] == ["bewijs:s1"]
    assert "4 artikelen" in bevindingen[0].detail


def test_site_met_nauwelijks_artikelen_wordt_niet_gemeld():
    """Onder een handvol artikelen zegt 'geen eigen bewijs' niets over de site."""
    with get_conn() as conn:
        _site(conn, "s1", "Verse site")
        _job(conn, "s1", "j0")
    assert integrity._check_artikel_zonder_eigen_bewijs() == []


def test_site_met_casestudies_wordt_niet_gemeld_zolang_niets_is_gemeten():
    """'Niet gemeten' mag nooit als 'geen bewijs' worden gelezen — anders telt
    de invariant vooral de artikelen van vóór deze toets."""
    with get_conn() as conn:
        _site(conn, "s1", "Steentje")
        conn.execute(
            "INSERT INTO case_studies (id, site_id, title, status, created_at, updated_at) "
            "VALUES ('cs1', 's1', 'Case', 'active', ?, ?)", (_NU, _NU))
        for i in range(5):
            _job(conn, "s1", f"j{i}")
    assert integrity._check_artikel_zonder_eigen_bewijs() == []


def test_ongebruikte_kennisbank_levert_een_eigen_bevinding():
    with get_conn() as conn:
        _site(conn, "s1", "Steentje")
        conn.execute(
            "INSERT INTO case_studies (id, site_id, title, status, created_at, updated_at) "
            "VALUES ('cs1', 's1', 'Case', 'active', ?, ?)", (_NU, _NU))
        for i in range(3):
            _job(conn, "s1", f"j{i}", qc={"eigen_bewijs": {"pass": False}})
        _job(conn, "s1", "j9", qc={"eigen_bewijs": {"pass": True}})
    bevindingen = integrity._check_artikel_zonder_eigen_bewijs()
    assert [b.subject for b in bevindingen] == ["bewijs-ongebruikt:s1"]
    assert "3 van 4" in bevindingen[0].detail


def test_kennisbank_die_wel_gebruikt_wordt_zwijgt():
    with get_conn() as conn:
        _site(conn, "s1", "Steentje")
        conn.execute(
            "INSERT INTO case_studies (id, site_id, title, status, created_at, updated_at) "
            "VALUES ('cs1', 's1', 'Case', 'active', ?, ?)", (_NU, _NU))
        for i in range(4):
            _job(conn, "s1", f"j{i}", qc={"eigen_bewijs": {"pass": True}})
    assert integrity._check_artikel_zonder_eigen_bewijs() == []


# ── De contentleerlus ───────────────────────────────────────────────────────

def _leerlus_draaide(conn, wanneer="2026-08-03T07:40:00+00:00"):
    conn.execute(
        "INSERT INTO scheduler_runs (job_id, last_run_at, last_ok_at, status, source) "
        "VALUES (?, ?, ?, 'ok', 'scheduler')",
        ("content_learning_eval", wanneer, wanneer))


def test_leerlus_zonder_lessen_wordt_gemeld_met_gemeten_oorzaak():
    with get_conn() as conn:
        _leerlus_draaide(conn)
    bevindingen = integrity._check_contentleerlus_zonder_lessen()
    assert len(bevindingen) == 1
    # De oorzaak moet in de tekst staan: zonder dat is 'nog niets te leren' niet
    # te onderscheiden van 'de meting werkt niet'.
    assert "gerijpt" in bevindingen[0].detail
    assert "2026-08-03" in bevindingen[0].detail


def test_leerlus_met_lessen_zwijgt():
    with get_conn() as conn:
        _leerlus_draaide(conn)
        conn.execute(
            "INSERT INTO agent_lessons (id, agent, lesson, category, active, "
            "created_at, updated_at) VALUES ('l1', 'content', "
            "'Lijstartikelen halen meer clicks.', 'content-vorm', 1, ?, ?)", (_NU, _NU))
    assert integrity._check_contentleerlus_zonder_lessen() == []


def test_nooit_geslaagde_leerlus_is_geen_stille_leerlus():
    """Dan is het een kapotte job, en dáár gaat `job_nooit_geslaagd` over —
    twee kaarten voor één storing maakt het Actiecentrum onbruikbaar."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scheduler_runs (job_id, last_run_at, last_ok_at, status, source) "
            "VALUES (?, ?, '', 'error', 'scheduler')",
            ("content_learning_eval", "2026-08-03T07:40:00+00:00"))
    assert integrity._check_contentleerlus_zonder_lessen() == []


def test_beide_invarianten_staan_in_het_register_met_incident():
    keys = {i.key: i for i in integrity.INVARIANTEN}
    for key in ("artikel_zonder_eigen_bewijs", "contentleerlus_zonder_lessen"):
        assert key in keys
        # Een toets zonder herkomst wordt bij de eerste ongelegen melding weggeklikt.
        assert "2026" in keys[key].incident
        assert keys[key].severity == integrity.STIL

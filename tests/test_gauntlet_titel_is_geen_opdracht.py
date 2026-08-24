"""De Wachtrij mag de opdracht niet voor het resultaat aanzien.

Incident 15 aug 2026. Het dashboard toonde "Artikel klaar (SEO 88/100) —
goedkeuren publiceert echt op de site" onder de kop:

    Herschrijf het artikel 'Zo vind je als organisatie sneller vrijwilligers'
    tot wereldklasse SEO-content (1200-1500 woorden)

Het artikel zelf was af en droeg de juiste H1 in de body; alleen de
administratie eromheen beschreef de opdracht in plaats van het resultaat. En
omdat `job_slug` van `job_title` wordt afgeleid, was dát de URL geworden —
dezelfde storing als 'schrijf-meta-titel-en-description-voor-pagina-c', die als
LIVE in het logboek stond.

Drie dingen worden hier vastgelegd:

1. `publish_run_to_wachtrij` zonder `title` pakt de kop uit het geschreven stuk,
   niet de objective van de run.
2. Een MEEGEGEVEN titel die zelf een werkbon is, wordt óók opgeschoond — de
   Orchestrator neemt de titel van het bronrecord over, en dat bronrecord is
   vaak zelf een eerder Gauntlet-product. Zonder deze stap plant de opdracht
   zich generatie op generatie voort.
3. `is_internal_document` weigert die vormen, zodat een opdracht nooit stil als
   pagina live kan gaan als er ooit een vierde weg naar de Wachtrij bijkomt.

Geen LLM-calls: we schrijven de run + deeltaak rechtstreeks in de wegwerp-DB.
"""
import os
import tempfile

import pytest


@pytest.fixture()
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db", prefix="agentos_titel_")
    os.close(fd)
    monkeypatch.setenv("IMPACTOS_DB_PATH", path)
    from backend.shared import database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", path, raising=False)
    db_mod.init_db()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


TEST_SITE_ID = "site-test-titel"

ARTIKEL = (
    "## Hoofdtaak\n\n"
    "# Zo vind je als organisatie sneller vrijwilligers\n\n"
    "Wie sneller vrijwilligers wil vinden, stopt met brede oproepen.\n"
)

OPDRACHT = (
    "Herschrijf het artikel 'Zo vind je als organisatie sneller vrijwilligers' "
    "tot wereldklasse SEO-content (1200-1500 woorden, E-E-A-T)."
)


def _run_met_stuk(service, objective, tekst=ARTIKEL, score=88):
    """Leg een geslaagde run met één deeltaak in de DB en geef het run_id terug."""
    from datetime import datetime, timezone
    from backend.shared.database import get_conn
    run_id = "gaunt-test-titel"
    nu = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        # De content_job heeft een bestaande site nodig (FK).
        conn.execute(
            "INSERT OR IGNORE INTO sites (id, name, base_url, created_at) VALUES (?,?,?,?)",
            (TEST_SITE_ID, "Testsite", "https://test.invalid", nu),
        )
        conn.execute(
            "INSERT INTO gauntlet_runs (id, objective, benchmark, status, threshold, "
            "max_iterations, subtask_count, best_overall_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, objective, "BENCHMARK", "passed", 85, 3, 1, score, nu, nu),
        )
        conn.execute(
            "INSERT INTO gauntlet_subtasks (run_id, position, role, goal, status, "
            "best_output, best_score, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, 0, "Hoofdtaak", "doel", "done", tekst, score, nu, nu),
        )
    return run_id


def test_titel_komt_uit_het_stuk_niet_uit_de_opdracht(temp_db):
    """Zonder expliciete titel wint de H1 van het artikel, niet de objective."""
    from backend.domains.gauntlet import service
    from backend.domains.publish import content_pipeline

    run_id = _run_met_stuk(service, OPDRACHT)
    res = service.publish_run_to_wachtrij(run_id, site_id=TEST_SITE_ID)

    job = content_pipeline.get_job(res["job_id"])
    assert job["title"] == "Zo vind je als organisatie sneller vrijwilligers"
    assert not job["title"].lower().startswith("herschrijf")
    # En dus ook geen werkbon in de URL.
    assert "herschrijf" not in (job["slug"] or "").lower()


def test_meegegeven_opdrachttitel_wordt_opgeschoond(temp_db):
    """De Orchestrator geeft de bron-titel mee; is die een werkbon, dan wint het stuk.

    Dit is het geval dat de storm liet doorwerken: het bronrecord droeg al een
    opdracht-titel uit een eerdere ronde, die werd overgenomen, en de volgende
    ronde nam hém weer over.
    """
    from backend.domains.gauntlet import service
    from backend.domains.publish import content_pipeline

    run_id = _run_met_stuk(service, OPDRACHT)
    res = service.publish_run_to_wachtrij(
        run_id,
        site_id=TEST_SITE_ID,
        title=OPDRACHT,                      # zoals de Orchestrator hem doorgeeft
        slug="herschrijf-het-artikel-zo-vind-je-als-organisatie-sneller",
    )

    job = content_pipeline.get_job(res["job_id"])
    assert job["title"] == "Zo vind je als organisatie sneller vrijwilligers"
    # De meegegeven slug is van dezelfde opdracht afgeleid en mag niet blijven.
    assert "herschrijf" not in (job["slug"] or "").lower()


def test_stuk_zonder_kop_wordt_geen_blog_met_opdracht_url(temp_db):
    """Geen bruikbare kop → als 'hook' parkeren, nooit als blog met werkbon-URL.

    Een hook krijgt geen Publiceer-knop en wordt geen pagina; dat is de veilige
    kant om op te falen.
    """
    from backend.domains.gauntlet import service
    from backend.domains.publish import content_pipeline

    run_id = _run_met_stuk(service, "Verzin iets moois", tekst="## Hoofdtaak\n\nLosse tekst zonder kop.")
    res = service.publish_run_to_wachtrij(run_id, site_id=TEST_SITE_ID)

    job = content_pipeline.get_job(res["job_id"])
    assert job["content_type"] == "hook"


@pytest.mark.parametrize("titel", [
    "Herschrijf het artikel 'Zo vind je als organisatie sneller vrijwilligers' tot wereldklasse SEO-content",
    "Herschrijf het artikel voor Bijeen (vrijwilligersdag-organiseren-de-complete-checklist-2026) tot wereldklasse",
    "Herschrijf het artikel '5 do's bij het organiseren van een relatiedag' (project Bijeen) naar een wereldklasse versie",
    "[SEO Copywriter] Schrijf 1 nieuw SEO-artikel (>=900 woorden, E-E-A-T) voor Virgin",
    "[SEO Editor] Optimaliseer de 3 zwakst scorende pagina's van de site",
])
def test_gate_weigert_werkbon_als_artikeltitel(temp_db, titel):
    from backend.domains.publish.content_pipeline import is_internal_document
    assert is_internal_document(titel), f"werkbon werd doorgelaten: {titel!r}"


@pytest.mark.parametrize("titel", [
    "Zo vind je als organisatie sneller vrijwilligers",
    "Netwerkbijeenkomst organiseren in 5 stappen",
    "7 flexibele manieren om vrijwilliger te worden zonder vast rooster",
    "Maak kennis met onze nieuwe vrijwilligers",
    "Levensverhaal vastleggen: complete gids voor 2026",
    "Waarom teambuilding meer is dan een uitje: 7 redenen uit de praktijk",
])
def test_gate_laat_echte_artikelkoppen_door(temp_db, titel):
    """Te streng is herstelbaar, maar niet ten koste van gewone koppen.

    'Maak kennis met…' staat er bewust bij: 'maak' is een opdracht-werkwoord,
    maar zonder een object als 'het artikel' erachter is het gewoon een kop.
    """
    from backend.domains.publish.content_pipeline import is_internal_document
    assert is_internal_document(titel) is None, f"echte kop geweigerd: {titel!r}"

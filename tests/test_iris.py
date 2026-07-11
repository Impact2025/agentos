"""Iris — de manager-agent: cijfers, leer-loop en veilige bijsturing."""
import json
import uuid

import pytest


def _seed_site(conn, site_id="testsite", name="Testsite", gsc="sc-domain:test.nl",
               batch=2):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, created_at) "
        "VALUES (?, ?, 'https://test.nl', ?, 1, ?, datetime('now'))",
        (site_id, name, gsc, batch),
    )


def _cleanup_iris(conn):
    for t in ("iris_reports", "iris_lessons", "sites", "published_pages",
              "seo_suggestions"):
        conn.execute(f"DELETE FROM {t}")


@pytest.fixture()
def iris_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        _cleanup_iris(c)


def test_project_scores_bevat_pijlers_en_cijfer(conn, iris_clean):
    from backend.domains.iris import metrics

    _seed_site(conn)
    # Eén gepubliceerd artikel + één te lang wachtende review.
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, status, created_at) "
        "VALUES (?, 'testsite', 'Live artikel', 'published', datetime('now', '-2 days'))",
        (str(uuid.uuid4()),),
    )
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, status, created_at) "
        "VALUES (?, 'testsite', 'Oude review', 'pending_review', datetime('now', '-5 days'))",
        (str(uuid.uuid4()),),
    )
    conn.execute(
        "INSERT INTO published_pages (id, site_id, slug, gsc_clicks, gsc_impressions, "
        "gsc_position, created_at, updated_at) "
        "VALUES (?, 'testsite', 'live-artikel', 12, 300, 8.0, datetime('now'), datetime('now'))",
        (str(uuid.uuid4()),),
    )
    conn.commit()

    scores = metrics.project_scores()
    site = next(p for p in scores if p["site_id"] == "testsite")
    assert 0 <= site["score"] <= 100
    assert site["grade"] == round(site["score"] / 10, 1)
    pil = site["pillars"]
    assert pil["content"]["live_30d"] == 1
    assert pil["content"]["stale_review"] == 1
    assert pil["seo"]["clicks"] == 12
    assert pil["seo"]["avg_position"] == 8.0


def test_seo_pijler_zonder_gsc_max_10(conn, iris_clean):
    from backend.domains.iris import metrics

    _seed_site(conn, site_id="nogsc", name="ZonderGSC", gsc="")
    conn.commit()
    site = next(p for p in metrics.project_scores() if p["site_id"] == "nogsc")
    assert site["pillars"]["seo"]["score"] <= 10
    assert "GSC" in site["pillars"]["seo"]["note"]


def test_lessen_dedupliceren_en_wegen_zwaarder(conn, iris_clean):
    from backend.domains.iris import service

    service._upsert_lessons([{"les": "Listicles scoren beter dan losse blogs", "categorie": "seo"}])
    service._upsert_lessons([{"les": "listicles scoren beter dan losse blogs", "categorie": "seo"}])
    lessons = service.active_lessons()
    assert len(lessons) == 1
    assert lessons[0]["times_confirmed"] == 2


@pytest.mark.asyncio
async def test_dagbriefing_slaat_op_en_stuurt_bij(conn, iris_clean, monkeypatch):
    from backend.domains.iris import service
    from backend.shared.database import get_conn

    _seed_site(conn, batch=2)
    conn.commit()

    canned = json.dumps({
        "oordeel_per_project": {"Testsite": "Contentmotor draait, SEO blijft achter."},
        "evaluatie_gisteren": None,
        "geleerd": ["Zonder GSC-data is vindbaarheid niet te sturen."],
        "verbeteringen": [
            {"type": "batch_size", "site_id": "testsite", "waarde": 99,
             "reden": "meer volume nodig"},
            {"type": "aanbeveling", "tekst": "Koppel GSC voor Testsite",
             "reden": "geen meetdata"},
        ],
        "advies": [{"prio": 1, "actie": "Keur de Wachtrij goed", "waarom": "content blijft liggen"}],
        "lessen": [{"les": "Batch verhogen zonder reviews werkt averechts", "categorie": "content"}],
    })

    async def fake_llm(system, prompt, max_tokens=3000):
        return canned
    monkeypatch.setattr(service, "_llm", fake_llm)

    result = await service.run_morning_briefing()

    assert result["llm_used"] is True
    assert "Iris — dagbriefing" in result["markdown"]
    assert "Testsite" in result["grades"]
    # Whitelist: batch_size wordt geklemd op het maximum (5), nooit 99.
    with get_conn() as c:
        row = c.execute("SELECT content_batch_size FROM sites WHERE id='testsite'").fetchone()
        assert row["content_batch_size"] == 5
        # Rapport opgeslagen + les onthouden + bijsturing gelogd als uitkomst.
        assert c.execute("SELECT COUNT(*) FROM iris_reports").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM iris_lessons").fetchone()[0] >= 1
        acties = c.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='iris_bijsturing'"
        ).fetchone()[0]
        assert acties == 1
    # De aanbeveling is advies gebleven, geen actie.
    assert any("Aanbeveling" in line for line in result["markdown"].splitlines())


@pytest.mark.asyncio
async def test_dagbriefing_zonder_llm_valt_terug_op_cijfers(conn, iris_clean, monkeypatch):
    from backend.domains.iris import service

    _seed_site(conn)
    conn.commit()

    async def no_llm(system, prompt, max_tokens=3000):
        return ""
    monkeypatch.setattr(service, "_llm", no_llm)

    result = await service.run_morning_briefing()
    assert result["llm_used"] is False
    assert "cijfermatige briefing" in result["markdown"]
    # Ook zonder LLM: rapport bewaard en advies (terugval) aanwezig.
    assert service.latest_report() is not None
    # Brein offline = fout in het Actiecentrum, niet alleen een logregel.
    from backend.shared.database import get_conn
    with get_conn() as c:
        errors = c.execute(
            "SELECT COUNT(*) FROM activity_log WHERE project='Iris' AND status='error'"
        ).fetchone()[0]
    assert errors >= 1
    # De Oordeel-kolom is ook zonder LLM gevuld (deterministisch oordeel).
    table_rows = [l for l in result["markdown"].splitlines()
                  if l.startswith("| Testsite")]
    assert table_rows and not table_rows[0].rstrip().endswith("|  |")


@pytest.mark.asyncio
async def test_llm_retry_redt_afgekapte_json(conn, iris_clean, monkeypatch):
    """Een wispelturig backend (leeg, dan afgekapt, dan goed) mag de analyse
    niet kosten: _ask_iris probeert de keten opnieuw tot er geldige JSON is."""
    from backend.domains.iris import service

    _seed_site(conn)
    conn.commit()

    canned = json.dumps({"oordeel_per_project": {"Testsite": "prima"},
                         "geleerd": [], "verbeteringen": [], "advies": [],
                         "lessen": [], "voorspellingen": []})
    responses = iter(["", '{"oordeel_per_project": {"Testsite": "afgekapt...', canned])

    async def flaky_llm(system, prompt, max_tokens=3000):
        return next(responses)
    monkeypatch.setattr(service, "_llm", flaky_llm)

    result = await service.run_morning_briefing()
    assert result["llm_used"] is True


@pytest.mark.asyncio
async def test_mislukte_herrun_degradeert_volwaardige_briefing_niet(conn, iris_clean, monkeypatch):
    """Ochtendrun mét LLM, herrun zonder: de volwaardige briefing blijft staan."""
    from backend.domains.iris import service

    _seed_site(conn)
    conn.commit()

    canned = json.dumps({"oordeel_per_project": {"Testsite": "uniek-oordeel-xyz"},
                         "geleerd": [], "verbeteringen": [], "advies": [],
                         "lessen": [], "voorspellingen": []})

    async def good_llm(system, prompt, max_tokens=3000):
        return canned
    monkeypatch.setattr(service, "_llm", good_llm)
    first = await service.run_morning_briefing()
    assert first["llm_used"] is True

    async def no_llm(system, prompt, max_tokens=3000):
        return ""
    monkeypatch.setattr(service, "_llm", no_llm)
    second = await service.run_morning_briefing()

    assert second.get("kept_existing") is True
    stored = service.latest_report()
    assert "uniek-oordeel-xyz" in stored["markdown"]
    assert "_LLM niet beschikbaar" not in stored["markdown"]


def test_iris_endpoints(conn, iris_clean):
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    r = client.get("/api/iris/briefing")
    assert r.status_code == 200
    r = client.get("/api/iris/scores")
    assert r.status_code == 200
    assert "projects" in r.json()
    r = client.get("/api/iris/history")
    assert r.status_code == 200


def test_briefing_needs_retry(conn, iris_clean):
    """De herkanselaar draait alleen bij een terugval-briefing van vandaag."""
    from backend.domains.iris import service

    # Geen rapport vandaag → niets te herkansen (dat is aan de 06:45-run)
    assert not service.briefing_needs_retry()

    # Terugval-briefing (llm_ok=0) → herkansen
    conn.execute(
        "INSERT INTO iris_reports (id, report_date, markdown, created_at, llm_ok) "
        "VALUES ('r-retry', ?, 'alleen cijfers', '2026-01-01T06:45:00', 0)",
        (service._today(),),
    )
    conn.commit()
    assert service.briefing_needs_retry()

    # Volwaardige briefing → klaar, niet meer herkansen
    conn.execute("UPDATE iris_reports SET llm_ok=1, markdown='volwaardige analyse' "
                 "WHERE id='r-retry'")
    conn.commit()
    assert not service.briefing_needs_retry()

    # Terugval-rij van vóór de llm_ok-kolom: vlag staat op 1, maar de vaste
    # marker in de markdown verraadt hem alsnog
    conn.execute("UPDATE iris_reports SET markdown='x _LLM niet beschikbaar y' "
                 "WHERE id='r-retry'")
    conn.commit()
    assert service.briefing_needs_retry()

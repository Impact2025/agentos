"""Iris' uitvoer-acties: van advies naar agents aan het werk — veilig geklemd."""
import json
import uuid

import pytest


def _seed_site(conn, site_id="testsite", name="Testsite"):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, created_at) "
        "VALUES (?, ?, 'https://test.nl', 'sc-domain:test.nl', 1, 2, datetime('now'))",
        (site_id, name),
    )
    conn.commit()


@pytest.fixture()
def actions_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        for t in ("iris_reports", "iris_lessons", "sites", "seo_suggestions"):
            c.execute(f"DELETE FROM {t}")


@pytest.mark.asyncio
async def test_content_run_start_batch_en_logt_uitkomst(conn, actions_clean, monkeypatch):
    from backend.domains.iris import actions
    from backend.shared.database import get_conn

    _seed_site(conn)
    calls = []

    async def fake_batch(site, count=None, light_mode=False):
        calls.append((site["id"], count))
        return ["job-1", "job-2"]
    monkeypatch.setattr("backend.domains.publish.content_pipeline.run_content_batch", fake_batch)

    done = await actions.content_run("Testsite", 99, "0 live pagina's")
    assert done and "2 artikel(en)" in done
    # Klem: nooit meer dan het maximum, ook al vraagt de LLM om 99.
    assert calls == [("testsite", 3)]
    with get_conn() as c:
        row = c.execute(
            "SELECT artifact, next_step FROM activity_log WHERE action='iris_actie'"
        ).fetchone()
    assert row and "content-queue" in row["artifact"] and "Wachtrij" in row["next_step"]

    # Dedupe: dezelfde site nogmaals op dezelfde dag → overgeslagen. Geen tweede
    # batch, maar wél een benigne uitkomst-string (geen None → geen valse HTTP 400).
    again = await actions.content_run("testsite", 1, "nogmaals")
    assert again and "draaide vandaag al" in again
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_content_run_onbekende_site_doet_niets(conn, actions_clean):
    from backend.domains.iris import actions
    assert await actions.content_run("BestaatNiet", 1, "x") is None


@pytest.mark.asyncio
async def test_outreach_run_zet_concepten_klaar_zonder_te_versturen(conn, actions_clean, monkeypatch):
    from backend.domains.iris import actions

    seen = []

    async def fake_prepare(count=0):
        seen.append(count)
        return {"drafted": count, "skipped": 0, "leads": []}
    monkeypatch.setattr("backend.domains.prospecting.outreach.prepare_outreach_batch", fake_prepare)

    done = await actions.outreach_run(10, "62 enriched leads staan stil")
    assert done and "10 concept(en)" in done
    assert seen == [10]
    # Dedupe binnen dezelfde dag: benigne uitkomst-string, geen None (geen valse HTTP 400).
    again = await actions.outreach_run(5, "nogmaals")
    assert again and "draaide vandaag al" in again
    assert seen == [10]


@pytest.mark.asyncio
async def test_seo_refresh_pakt_topsuggesties_naar_wachtrij(conn, actions_clean, monkeypatch):
    from backend.domains.iris import actions
    from backend.domains.seo import optimizer

    _seed_site(conn)
    optimizer._store_suggestions("testsite", "refresh", [
        {"page": "https://test.nl/a", "title": "5 klikken verloren", "score": 20, "data": {}},
        {"page": "https://test.nl/b", "title": "3 posities gezakt", "score": 10, "data": {}},
        {"page": "https://test.nl/c", "title": "klein verlies", "score": 1, "data": {}},
    ])

    refreshed = []

    async def fake_refresh(sug, site):
        refreshed.append(sug["page"])
        return f"job-{len(refreshed)}"
    monkeypatch.setattr(optimizer, "refresh_article", fake_refresh)

    done = await actions.seo_refresh("Testsite", 99, "decay op 3 pagina's")
    assert done and "2 wegzakkende" in done
    # Klem op 2, hoogste score eerst.
    assert refreshed == ["https://test.nl/a", "https://test.nl/b"]


@pytest.mark.asyncio
async def test_seo_refresh_zonder_suggesties_doet_niets(conn, actions_clean):
    from backend.domains.iris import actions
    _seed_site(conn)
    # Geen open refresh-suggesties: benigne uitkomst-string, geen None
    # (None → valse HTTP 400 in de fix-knop).
    done = await actions.seo_refresh("Testsite", 1, "x")
    assert done and "geen open refresh-kandidaten" in done


@pytest.mark.asyncio
async def test_apply_improvements_dispatch_en_caps(conn, actions_clean, monkeypatch):
    from backend.domains.iris import actions, service

    started = []

    async def fake_content_run(ref, count, reason):
        started.append(ref)
        return f"Contentmotor gestart voor {ref}"
    monkeypatch.setattr(actions, "content_run", fake_content_run)

    async def fake_outreach_run(count, reason):
        return "Outreach-batch gestart"
    monkeypatch.setattr(actions, "outreach_run", fake_outreach_run)

    applied = await service._apply_improvements([
        {"type": "content_run", "site_id": "a", "aantal": 1, "reden": "r"},
        {"type": "content_run", "site_id": "b", "aantal": 1, "reden": "r"},
        {"type": "content_run", "site_id": "c", "aantal": 1, "reden": "r"},
        {"type": "outreach_run", "aantal": 5, "reden": "r"},
        {"type": "aanbeveling", "tekst": "blijft advies"},
        {"type": "onzin", "reden": "genegeerd"},
    ])
    # Cap: maximaal 2 content_runs per briefing; aanbeveling/onbekend nooit uitgevoerd.
    assert started == ["a", "b"]
    assert applied == ["Contentmotor gestart voor a", "Contentmotor gestart voor b",
                       "Outreach-batch gestart"]


@pytest.mark.asyncio
async def test_briefing_toont_opgepakt_werk(conn, actions_clean, monkeypatch):
    from backend.domains.iris import service

    _seed_site(conn)

    async def fake_batch(site, count=None, light_mode=False):
        return ["job-x"]
    monkeypatch.setattr("backend.domains.publish.content_pipeline.run_content_batch", fake_batch)

    canned = json.dumps({
        "oordeel_per_project": {"Testsite": "0 live pagina's — publiceren."},
        "geleerd": [], "advies": [], "lessen": [], "voorspellingen": [],
        "verbeteringen": [{"type": "content_run", "site_id": "testsite",
                           "aantal": 1, "reden": "0 live pagina's ondanks completed taken"}],
    })

    async def fake_llm(system, prompt, max_tokens=3000):
        return canned
    monkeypatch.setattr(service, "_llm", fake_llm)

    result = await service.run_morning_briefing()
    assert any("Contentmotor gestart voor Testsite" in a for a in result["applied"])
    assert "Wat ik heb opgepakt" in result["markdown"]

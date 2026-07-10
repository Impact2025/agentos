"""Demand Engine cold-start: kansen voor sites zonder rankings."""
import json

import pytest


def _seed_site(conn, site_id="freshsite", profile=None):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, profile, "
        "auto_content_enabled, content_batch_size, created_at) "
        "VALUES (?, 'Freshsite', 'https://fresh.nl', 'sc-domain:fresh.nl', ?, 1, 1, datetime('now'))",
        (site_id, profile if profile is not None else
         "Freshsite helpt vrijwilligersorganisaties met het vinden en binden van "
         "vrijwilligers via een slim matchingplatform."),
    )
    conn.commit()


@pytest.fixture()
def coldstart_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        for t in ("opportunities", "sites"):
            c.execute(f"DELETE FROM {t}")


def _site(site_id="freshsite"):
    from backend.domains.seo import sites as sites_service
    return sites_service.get_site(site_id)


def test_cold_start_maakt_kansen_uit_profiel(conn, coldstart_clean, monkeypatch):
    from backend.domains.seo import engine

    _seed_site(conn)
    # Bestaande kans: mag niet dubbel worden aangemaakt.
    engine.create_manual_opportunity("freshsite", "vrijwilligers vinden tips", "x", "y")

    canned = json.dumps([
        {"query": "Vrijwilligers Vinden Tips", "angle": "dubbel", "rationale": "dubbel"},
        {"query": "vrijwilligers werven kleine stichting", "angle": "stappenplan",
         "rationale": "long-tail, lage concurrentie"},
        {"query": "vrijwilligersovereenkomst voorbeeld", "angle": "gratis template",
         "rationale": "transactionele zoekintentie"},
    ])
    monkeypatch.setattr(engine, "_claude_complete", lambda s, p, max_tokens=2000: canned)
    monkeypatch.setattr(engine, "_llm_available", lambda: True)

    created = engine.cold_start_opportunities(_site())
    assert len(created) == 2  # dubbel (case-insensitive) overgeslagen
    assert all(c["opportunity_score"] == engine._COLD_START_SCORE for c in created)
    open_now = engine.list_opportunities(site_id="freshsite", status="new")
    assert len(open_now) == 3


def test_cold_start_zonder_profiel_doet_niets(conn, coldstart_clean, monkeypatch):
    from backend.domains.seo import engine
    _seed_site(conn, site_id="leeg", profile="")
    monkeypatch.setattr(engine, "_llm_available", lambda: True)
    assert engine.cold_start_opportunities(_site("leeg")) == []


def test_scan_site_valt_terug_op_cold_start(conn, coldstart_clean, monkeypatch):
    """GSC leeg + niets open → scan_site zwengelt de cold-start aan."""
    from backend.domains.seo import engine

    _seed_site(conn)
    monkeypatch.setattr(engine, "fetch_query_performance", lambda prop, days=90: [])
    canned = json.dumps([{"query": "vrijwilligers app vergelijken",
                          "angle": "eerlijke vergelijking", "rationale": "niche"}])
    monkeypatch.setattr(engine, "_claude_complete", lambda s, p, max_tokens=2000: canned)
    monkeypatch.setattr(engine, "_llm_available", lambda: True)

    result = engine.scan_site(_site())
    assert result["found"] == 0
    assert result["cold_start"] == 1
    assert result["new"] == 1

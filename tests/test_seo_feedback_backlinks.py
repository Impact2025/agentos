"""Tests voor de SEO-feedback-loop, backlink-ARM en JSON-LD-validator."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.shared.database import get_conn


def _reset():
    import backend.domains.radar.models as rmodels
    rmodels.ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM published_pages")
        conn.execute("DELETE FROM radar_signals")
        conn.execute("INSERT OR REPLACE INTO sites (id, name, base_url, created_at) "
                     "VALUES ('s1','Bijeen','https://bijeen.app','2026-07-08')")


def _add_page(site_id, slug, title, html):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO published_pages (site_id, slug, title, html, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (site_id, slug, title, html, "2026-07-08", "2026-07-08"),
        )


def test_validate_json_ld_valid():
    from backend.domains.seo.enhancements import validate_json_ld
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Article","headline":"X",'
        '"publisher":{"@type":"Organization","name":"Bijeen"},'
        '"mainEntity":{"@type":"FAQPage","mainEntity":['
        '{"@type":"Question","name":"Wat is X?","acceptedAnswer":'
        '{"@type":"Answer","text":"Y"}}]}}</script>'
    )
    r = validate_json_ld(html)
    assert r["valid"] is True
    assert r["has_faq"] is True


def test_validate_json_ld_missing_publisher():
    from backend.domains.seo.enhancements import validate_json_ld
    html = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Article","headline":"X"}</script>'
    )
    r = validate_json_ld(html)
    assert r["valid"] is False
    assert any("publisher" in e for e in r["errors"])


def test_validate_json_ld_broken_json():
    from backend.domains.seo.enhancements import validate_json_ld
    html = '<script type="application/ld+json">{bad json</script>'
    r = validate_json_ld(html)
    assert r["valid"] is False


def test_validate_json_ld_absent():
    from backend.domains.seo.enhancements import validate_json_ld
    assert validate_json_ld("<p>geen schema</p>")["valid"] is False


def test_backlink_arm_injects_natural_link():
    _reset()
    # Bestaande pagina over "vrijwilligers werven" met een natuurlijke anker.
    _add_page("s1", "vrijwilligers-werven", "Vrijwilligers werven tijdens een event",
              "<h1>Vrijwilligers werven</h1><p>De snelste manier om vrijwilligers werven "
              "te doen is tijdens een bijeenkomst.</p>")
    _add_page("s1", "nieuw-artikel", "Vrijwilligers werven in de zorg",
              "<h1>Vrijwilligers werven in de zorg</h1><p>Specifieke tips.</p>")
    from backend.domains.seo.enhancements import apply_backlinks
    res = apply_backlinks(
        {"id": "s1"}, "nieuw-artikel", "Vrijwilligers werven in de zorg",
        "https://bijeen.app/nieuuw-artikel/",
    )
    assert res["added"] == 1, res
    with get_conn() as conn:
        row = dict(conn.execute(
            "SELECT html FROM published_pages WHERE slug='vrijwilligers-werven'").fetchone())
    assert "bijeen.app/nieuuw-artikel" in row["html"]


def test_backlink_arm_no_overlap_skips():
    _reset()
    _add_page("s1", "onderhoud-tuin", "Tuinonderhoud in het voorjaar",
              "<h1>Tuinonderhoud</h1><p>Snoei de rozen.</p>")
    from backend.domains.seo.enhancements import apply_backlinks
    res = apply_backlinks({"id": "s1"}, "nieuw-artikel",
                          "Vrijwilligers werven in de zorg", "https://x/n/")
    assert res["added"] == 0
    assert res["skipped"] >= 1


def test_backlink_arm_idempotent():
    _reset()
    _add_page("s1", "vrijwilligers-werven", "Vrijwilligers werven tijdens een event",
              "<h1>Vrijwilligers werven</h1><p>De snelste manier om vrijwilligers te "
              "werven is tijdens een bijeenkomst.</p>")
    _add_page("s1", "nieuw-artikel", "Vrijwilligers werven in de zorg",
              "<h1>Vrijwilligers werven in de zorg</h1><p>Specifieke tips.</p>")
    from backend.domains.seo.enhancements import apply_backlinks
    apply_backlinks({"id": "s1"}, "nieuw-artikel", "Vrijwilligers werven in de zorg",
                    "https://bijeen.app/n/")
    res2 = apply_backlinks({"id": "s1"}, "nieuw-artikel", "Vrijwilligers werven in de zorg",
                           "https://bijeen.app/n/")
    assert res2["added"] == 0, res2


def test_growth_signal_feed_creates_signal():
    import backend.domains.seo.feedback as fb
    import backend.domains.radar.models as rmodels
    rmodels.ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM radar_signals")
    # Echte feed_radar met gepatchte GSC-data; daarna lezen in een eigen
    # connectie (geen conftest-`conn`-fixture open houden — anders lock).
    fake = [{"slug": "vrijwilligers-werven", "query": "vrijwilligers werven",
             "position": 12, "impressions": 800, "clicks": 4, "ctr": 0.5,
             "kind": "striking_distance"}]
    orig = fb.collect_growth_signals
    fb.collect_growth_signals = lambda site: fake
    try:
        n = fb.feed_radar({"id": "s1", "name": "Bijeen", "gsc_property": "https://bijeen.app"},
                          project="Bijeen")
        # feed_radar zet GSC-growth-data om in Radar-signalen en rapporteert
        # hoeveel er nieuw ingevoerd zijn (idempotent: geen dubbele bij een
        # tweede run).
        assert n >= 1, n
        n2 = fb.feed_radar({"id": "s1", "name": "Bijeen", "gsc_property": "https://bijeen.app"},
                           project="Bijeen")
        assert n2 == 0, "feed_radar is niet idempotent"
    finally:
        fb.collect_growth_signals = orig


def test_list_signals_source_filter():
    """De Radar 'Growth'-tab filtert op source='gsc-growth' via de backend."""
    import backend.domains.radar.models as rmodels
    import backend.domains.radar.service as rsvc
    rmodels.ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM radar_signals")
        conn.execute(
            "INSERT INTO radar_signals (id, watch_id, project, keyword, title, url, "
            "source, snippet, signal_score, ai_angle, status, scanned_at, created_at, "
            "updated_at) VALUES ('g1','', 'Bijeen','q','Boost','gsc://growth/x','gsc-growth',"
            "'', 80,'','new','2026-07-08','2026-07-08','2026-07-08')")
        conn.execute(
            "INSERT INTO radar_signals (id, watch_id, project, keyword, title, url, "
            "source, snippet, signal_score, ai_angle, status, scanned_at, created_at, "
            "updated_at) VALUES ('s1','', 'Bijeen','q2','Scan','https://reddit.com/r/x','reddit',"
            "'', 70,'','new','2026-07-08','2026-07-08','2026-07-08')")
    svc = rsvc.RadarService()
    growth = svc.list_signals(project="Bijeen", source="gsc-growth")
    assert len(growth) == 1 and growth[0]["id"] == "g1", growth
    alls = svc.list_signals(project="Bijeen")
    assert len(alls) == 2, alls

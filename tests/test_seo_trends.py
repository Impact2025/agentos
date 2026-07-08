"""Trend-brug (Mission Radar → Demand Engine) + IndexNow-verificatie:
de deterministische, LLM-vrije onderdelen."""
import uuid

import pytest


def _make_site(name="TrendSite", base_url=""):
    from backend.domains.seo import sites as sites_service
    s = sites_service.create_site({"name": name, "base_url": base_url})
    return sites_service.get_site(s["id"])


def _insert_signal(project, keyword, score, status="new", title="Bron-titel",
                   ai_angle="Unieke invalshoek", ai_hook="Sterke hook"):
    from backend.domains.radar.models import ensure_schema
    from backend.shared.database import get_conn
    ensure_schema()
    sig_id = str(uuid.uuid4())
    now = "2026-07-07T07:00:00+00:00"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO radar_signals
               (id, project, keyword, title, url, source, signal_score,
                ai_hook, ai_angle, status, scanned_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'news', ?, ?, ?, ?, ?, ?, ?)""",
            (sig_id, project, keyword, title, f"https://bron.nl/{sig_id}",
             score, ai_hook, ai_angle, status, now, now, now),
        )
    return sig_id


@pytest.fixture()
def trend_site():
    from backend.domains.seo import sites as sites_service
    from backend.shared.database import get_conn
    site = _make_site()
    yield site
    with get_conn() as c:
        c.execute("DELETE FROM opportunities WHERE site_id = ?", (site["id"],))
        c.execute("DELETE FROM radar_signals")
    sites_service.delete_site(site["id"])


def test_trend_sync_converts_top_signal(trend_site):
    from backend.domains.seo.trends import sync_trend_opportunities
    from backend.domains.seo.engine import list_opportunities
    from backend.shared.database import get_conn

    sig_id = _insert_signal("TrendSite", "hermes desktop installatie", 82)
    result = sync_trend_opportunities(trend_site)
    assert result["created"] == 1

    kansen = list_opportunities(site_id=trend_site["id"], status="new")
    assert len(kansen) == 1
    kans = kansen[0]
    assert kans["query"] == "hermes desktop installatie"
    assert kans["opportunity_score"] == 82
    assert kans["action"] == "nieuwe-content"
    assert kans["rationale"].startswith("Trending (Radar-score 82)")

    with get_conn() as c:
        row = c.execute("SELECT status FROM radar_signals WHERE id = ?", (sig_id,)).fetchone()
    assert row["status"] == "targeted"

    # Idempotent: tweede sync maakt geen duplicaat.
    assert sync_trend_opportunities(trend_site)["created"] == 0


def test_trend_sync_skips_low_scores_and_other_projects(trend_site):
    from backend.domains.seo.trends import sync_trend_opportunities
    _insert_signal("TrendSite", "zwak signaal", 40)           # onder de drempel
    _insert_signal("AnderProject", "andermans trend", 90)     # ander project
    assert sync_trend_opportunities(trend_site)["created"] == 0


def test_trend_sync_dedupes_against_existing_opportunity(trend_site):
    from backend.domains.seo.engine import create_manual_opportunity
    from backend.domains.seo.trends import sync_trend_opportunities
    create_manual_opportunity(trend_site["id"], "Dubbele Trend", "angle", "why")
    _insert_signal("TrendSite", "dubbele trend", 75)
    result = sync_trend_opportunities(trend_site)
    assert result["created"] == 0
    assert result["skipped"] >= 1


def test_trend_sync_caps_per_run(trend_site):
    from backend.domains.seo import trends
    for i in range(trends.TREND_MAX_PER_SYNC + 2):
        _insert_signal("TrendSite", f"trend nummer {i}", 90 - i)
    assert trends.sync_trend_opportunities(trend_site)["created"] == trends.TREND_MAX_PER_SYNC


def test_signal_query_falls_back_to_title():
    from backend.domains.seo.trends import _signal_query
    assert _signal_query({"keyword": "ai agents", "title": "x"}) == "ai agents"
    assert _signal_query({"keyword": "site:concurrent.nl", "title": "Concurrent lanceert tool"}) \
        == "Concurrent lanceert tool"
    assert _signal_query({"keyword": "", "title": "  Lange   titel  "}) == "Lange titel"


# ── IndexNow-verificatie ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_indexnow_without_base_url_or_key():
    from backend.domains.publish.indexing import verify_indexnow
    assert (await verify_indexnow({"base_url": "", "indexnow_key": "k"}))["status"] == "geen-base-url"
    assert (await verify_indexnow({"base_url": "https://x.nl", "indexnow_key": ""}))["status"] == "geen-key"

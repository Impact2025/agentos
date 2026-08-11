"""GSC-historie + trend-delta's: opslaan van dagreeksen en week-over-week-delta's."""
import uuid
from datetime import date, timedelta

import pytest


def _seed_site(conn, site_id="trendsite", name="Trendsite", gsc="sc-domain:trend.nl"):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, created_at) "
        "VALUES (?, ?, 'https://trend.nl', ?, 1, 2, datetime('now'))",
        (site_id, name, gsc),
    )


def _seed_site_day(conn, site_id, day, clicks, impressions, position):
    conn.execute(
        "INSERT INTO gsc_history (id, site_id, scope, page_url, date, clicks, "
        "impressions, ctr, position, created_at) "
        "VALUES (?, ?, 'site', '', ?, ?, ?, 0, ?, datetime('now'))",
        (str(uuid.uuid4()), site_id, day, clicks, impressions, position),
    )


def _seed_page_snapshot(conn, site_id, page_url, day, clicks, position, query=""):
    conn.execute(
        "INSERT INTO gsc_history (id, site_id, scope, page_url, date, clicks, "
        "impressions, ctr, position, top_query, created_at) "
        "VALUES (?, ?, 'page', ?, ?, ?, 0, 0, ?, ?, datetime('now'))",
        (str(uuid.uuid4()), site_id, page_url, day, clicks, position, query),
    )


@pytest.fixture()
def hist_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        for t in ("gsc_history", "sites"):
            c.execute(f"DELETE FROM {t}")


def test_site_trend_zonder_historie_is_none(conn, hist_clean):
    from backend.domains.seo import history

    _seed_site(conn)
    conn.commit()
    assert history.site_trend("trendsite") is None


def test_site_trend_week_over_week(conn, hist_clean):
    from backend.domains.seo import history

    _seed_site(conn)
    today = date.today()
    # Vorige 7 dagen: 10 clicks/dag, positie 12. Recente 7: 20 clicks/dag, positie 9.
    for i in range(7, 14):
        _seed_site_day(conn, "trendsite", (today - timedelta(days=i)).isoformat(), 10, 100, 12.0)
    for i in range(0, 7):
        _seed_site_day(conn, "trendsite", (today - timedelta(days=i)).isoformat(), 20, 150, 9.0)
    conn.commit()

    trend = history.site_trend("trendsite")
    assert trend["last7"]["clicks"] == 140
    assert trend["prev7"]["clicks"] == 70
    assert trend["delta_clicks"] == 70
    assert trend["clicks_pct"] == 100.0
    # Positie van 12 → 9 is winst: delta negatief.
    assert trend["delta_position"] == -3.0


def test_page_movers_stijgers_en_dalers(conn, hist_clean):
    from backend.domains.seo import history

    _seed_site(conn)
    today = today_iso = date.today()
    latest = today.isoformat()
    base = (today - timedelta(days=7)).isoformat()

    # Stijger: /a van 5 → 25 clicks. Daler: /b van 30 → 10 clicks.
    _seed_page_snapshot(conn, "trendsite", "https://trend.nl/a/", base, 5, 15.0, "keyword a")
    _seed_page_snapshot(conn, "trendsite", "https://trend.nl/a/", latest, 25, 8.0, "keyword a")
    _seed_page_snapshot(conn, "trendsite", "https://trend.nl/b/", base, 30, 6.0, "keyword b")
    _seed_page_snapshot(conn, "trendsite", "https://trend.nl/b/", latest, 10, 9.0, "keyword b")
    conn.commit()

    movers = history.page_movers("trendsite", limit=5)
    assert movers["risers"][0]["page_url"] == "https://trend.nl/a/"
    assert movers["risers"][0]["delta_clicks"] == 20
    assert movers["fallers"][0]["page_url"] == "https://trend.nl/b/"
    assert movers["fallers"][0]["delta_clicks"] == -20


def test_page_movers_zonder_basis_leeg(conn, hist_clean):
    from backend.domains.seo import history

    _seed_site(conn)
    # Alleen vandaag, geen oudere snapshot om mee te vergelijken.
    _seed_page_snapshot(conn, "trendsite", "https://trend.nl/a/", date.today().isoformat(), 25, 8.0)
    conn.commit()
    movers = history.page_movers("trendsite")
    assert movers["risers"] == [] and movers["fallers"] == []


def test_record_page_snapshots_is_idempotent(conn, hist_clean):
    from backend.domains.seo import history

    _seed_site(conn)
    conn.commit()
    by_page = {"https://trend.nl/x/": {"clicks": 5, "impressions": 50, "ctr": 10.0,
                                       "position": 7.0, "query": "x"}}
    history.record_page_snapshots("trendsite", by_page)
    # Zelfde dag opnieuw met andere cijfers → update, geen dubbele rij.
    by_page["https://trend.nl/x/"]["clicks"] = 9
    history.record_page_snapshots("trendsite", by_page)

    from backend.shared.database import get_conn
    with get_conn() as c:
        rows = c.execute(
            "SELECT clicks FROM gsc_history WHERE site_id='trendsite' AND scope='page'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["clicks"] == 9


def test_record_site_daily_backfill_en_upsert(conn, hist_clean, monkeypatch):
    from backend.domains.seo import history, gsc

    _seed_site(conn)
    conn.commit()

    monkeypatch.setattr(gsc, "is_configured", lambda: True)
    today = date.today()
    day1 = (today - timedelta(days=3)).isoformat()
    day2 = (today - timedelta(days=2)).isoformat()

    calls = {"days": []}

    def fake_daily(prop, days=28, end_offset=0, site_id=None):
        calls["days"].append(days)
        return [
            {"date": day1, "clicks": 10, "impressions": 100, "ctr": 10.0, "position": 5.0},
            {"date": day2, "clicks": 12, "impressions": 110, "ctr": 10.9, "position": 4.8},
        ]
    monkeypatch.setattr(gsc, "fetch_daily_performance", fake_daily)

    site = {"id": "trendsite", "gsc_property": "sc-domain:trend.nl"}
    r1 = history.record_site_daily(site)
    assert r1["ok"] and r1["rows"] == 2
    assert calls["days"][0] == 90  # eerste keer: 90 dagen backfill

    # Tweede run: 28-dagen-venster, en day2 wordt geüpdatet (geen duplicaat).
    r2 = history.record_site_daily(site)
    assert calls["days"][1] == 28
    from backend.shared.database import get_conn
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM gsc_history WHERE site_id='trendsite' AND scope='site'").fetchone()[0]
    assert n == 2


def test_iris_snapshot_bevat_trend(conn, hist_clean):
    from backend.domains.seo import history  # noqa
    from backend.domains.iris import metrics

    _seed_site(conn)
    today = date.today()
    for i in range(0, 14):
        _seed_site_day(conn, "trendsite", (today - timedelta(days=i)).isoformat(),
                       20 if i < 7 else 10, 150, 9.0)
    conn.commit()

    proj = next(p for p in metrics.project_scores() if p["site_id"] == "trendsite")
    assert proj["trend"] is not None
    assert proj["trend"]["site"]["delta_clicks"] == 70


def test_iris_trends_endpoint(conn, hist_clean):
    from fastapi.testclient import TestClient
    from backend.main import app

    _seed_site(conn)
    conn.commit()
    client = TestClient(app)
    r = client.get("/api/iris/trends")
    assert r.status_code == 200
    assert "projects" in r.json()

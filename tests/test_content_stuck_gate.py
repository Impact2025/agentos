"""Regressietest voor de 'quota in één dag leeg'-bug (2026-07-10).

Eén artikel dat oscilleert (score nooit >= CONTENT_MIN_SCORE) werd elke 30 min
door de content-verbeteraar opnieuw opgepakt omdat de per-run cap (12 rondes)
bij elke run resette. Fix: een cross-run teller (improve_attempts); na
CONTENT_IMPROVER_MAX_ATTEMPTS pogingen wordt de job 'stuck' en stopt elke
verdere LLM-cyclus. Deze test verzekert dat die fix blijft werken.
"""
import asyncio
from datetime import datetime

from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import sites as sites_service
from backend.shared.config import CONTENT_MIN_SCORE, CONTENT_IMPROVER_MAX_ATTEMPTS


def _seed_site_and_job(conn, site_id="t1", job_id="j1", score=62):
    conn.execute(
        "INSERT OR IGNORE INTO sites (id,name,base_url,auto_content_enabled,created_at) "
        "VALUES (?,?,?,?,?)", (site_id, "TestSite", "http://x", 0, datetime.now().isoformat()))
    conn.execute("DELETE FROM content_jobs WHERE site_id=?", (site_id,))
    conn.execute(
        "INSERT INTO content_jobs (id,site_id,title,keyword,status,blog_html,seo_score,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (job_id, site_id, "Test Artikel", "test keyword", "needs_work",
         "<h1>Test</h1><p>body</p>", score, datetime.now().isoformat()))


def test_oscillating_article_gets_stuck_after_max_attempts():
    # Direct tegen de echte DB (geïsoleerd via conftest).
    from backend.shared.database import get_conn
    with get_conn() as conn:
        _seed_site_and_job(conn)

    calls = {"n": 0}

    async def fake_write(site, kw, angle, rat):
        return "<h1>Test</h1><p>body</p>", {}, ""
    cp._write_article_best = fake_write

    async def fake_social(site, title, kw, html):
        return {"linkedin": "", "facebook": "", "instagram": "", "twitter": ""}
    cp._generate_social_copy = fake_social
    cp.generate_quote_card = lambda *a, **k: b""
    cp._generate_article_infographic = lambda *a, **k: asyncio.sleep(0, result=None)

    async def fake_review(site, kw, html, max_rounds=6):
        calls["n"] += 1
        # Altijd onder de grens -> nooit een passerende score.
        return html, {"score": 62, "feedback": "fake"}

    cp.review_and_improve = fake_review

    async def run():
        for _ in range(CONTENT_IMPROVER_MAX_ATTEMPTS + 3):
            await cp.regenerate_job("j1")
            refreshed = cp.get_job("j1")
            if refreshed["status"] == "stuck":
                return refreshed
        return cp.get_job("j1")

    final = asyncio.run(run())

    # Na maximaal CONTENT_IMPROVER_MAX_ATTEMPTS pogingen: stuck.
    assert final["status"] == "stuck", f"verwacht stuck, got {final['status']}"
    assert final["improve_attempts"] <= CONTENT_IMPROVER_MAX_ATTEMPTS
    # En niet oneindig veel LLM-rondes verbruikt:
    assert calls["n"] <= CONTENT_IMPROVER_MAX_ATTEMPTS + 1, \
        f"te veel review-aanroepen: {calls['n']}"


def test_stuck_job_excluded_from_scheduler_queue():
    from backend.shared.database import get_conn
    with get_conn() as conn:
        _seed_site_and_job(conn, job_id="j2", score=40)
        # Zet 'm direct op stuck (simuleert de cap die gegrepen heeft).
        conn.execute("UPDATE content_jobs SET status='stuck', improve_attempts=? WHERE id=?",
                     (CONTENT_IMPROVER_MAX_ATTEMPTS, "j2"))

    # De scheduler-job pakt alleen needs_work < grens; stuck hoort er niet bij.
    eligible = [j for j in cp.list_jobs(status="needs_work")
                if int(j.get("seo_score") or 0) < CONTENT_MIN_SCORE]
    assert all(j["id"] != "j2" for j in eligible), "stuck job mag niet opnieuw verbeterd worden"

    # run_content_improver_job meldt 'm wel in de stuck-lijst, zonder LLM-calls.
    calls = {"n": 0}

    async def fake_review(site, kw, html, max_rounds=6):
        calls["n"] += 1
        return html, {"score": 40, "feedback": "x"}
    cp.review_and_improve = fake_review

    async def run():
        return await cp.run_content_improver_job()
    summary = asyncio.run(run())
    stuck_labels = summary.get("stuck", [])
    assert any("Test Artikel" in s for s in stuck_labels), \
        f"stuck job hoort gerapporteerd te worden, got {stuck_labels}"
    assert calls["n"] == 0, "stuck job mag geen LLM-ronde meer trigger"

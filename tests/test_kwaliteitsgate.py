"""Kwaliteitsgate: onder CONTENT_MIN_SCORE wordt er nooit gepubliceerd."""
import pytest


def _seed(conn, score, status="pending_review", job_id="gate-job-1"):
    conn.execute(
        "INSERT OR IGNORE INTO sites (id, name, created_at) VALUES ('site-g', 'GateSite', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, keyword, status, seo_score, "
        "blog_html, social_copy, publish_result, created_at) "
        "VALUES (?, 'site-g', 'Gate-artikel', 'kw', ?, ?, '<h1>x</h1>', '{}', '{}', datetime('now'))",
        (job_id, status, score),
    )
    conn.commit()


@pytest.mark.asyncio
async def test_approve_weigert_onder_de_grens(conn, clean_tables):
    from backend.domains.publish import content_pipeline
    from backend.shared.config import CONTENT_MIN_SCORE

    _seed(conn, score=50)
    with pytest.raises(ValueError) as exc:
        await content_pipeline.approve_and_publish("gate-job-1")
    assert str(CONTENT_MIN_SCORE) in str(exc.value)


def test_grens_is_85_default():
    from backend.shared.config import CONTENT_MIN_SCORE
    assert CONTENT_MIN_SCORE == 85

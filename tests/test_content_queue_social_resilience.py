"""Regression: content-queue approve_and_publish must publish to the website
even when a social platform (LinkedIn) fails to resolve its member ID, and must
never let that failure block the job reaching 'published'.

Covers the Bijeen case: project-site publish via {PROJECT}_PUBLISH_URL/_KEY
(no Netlify publish_api_url), LinkedIn in 'Review in progress' (member ID
resolution raises).
"""
import json
from unittest import mock

import pytest

from backend.domains.publish import content_pipeline as cp
from backend.domains.seo import sites as sites_service
from backend.shared.database import get_conn


@pytest.fixture
def bijeen_site():
    """Create a Bijeen site (no Netlify publish_api_url)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sites (id, name, base_url, publish_api_url, linkedin_user_urn, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("site-bijeen", "Bijeen", "https://bijeen.app", "", "", "2026-01-01T00:00:00"),
        )
    yield "site-bijeen"
    with get_conn() as conn:
        conn.execute("DELETE FROM sites WHERE id='site-bijeen'")
        conn.execute("DELETE FROM content_jobs WHERE site_id='site-bijeen'")


@pytest.fixture
def bijeen_job(bijeen_site):
    job_id = "job-1"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO content_jobs "
            "(id, site_id, title, keyword, rationale, status, blog_html, seo_score, "
            "social_copy, image_path, slug, publish_result, error, created_at, "
            "reviewed_at, qc_report, case_study_id, infographic_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, bijeen_site, "SROI berekenen per evenement", "sroi", "rationale",
             "pending_review",
             "<p>Eerste alinea over SROI.</p><h2>Stap 1</h2><p>Uitleg.</p>",
             92.0,
             json.dumps({"linkedin": "LinkedIn copy hier", "facebook": "",
                         "instagram": "", "twitter": ""}),
             "", "sroi-berekenen-per-evenement", "", "", "2026-01-01T00:00:00",
             "", "{}", "", ""),
        )
    yield job_id
    with get_conn() as conn:
        conn.execute("DELETE FROM content_jobs WHERE id=?", (job_id,))


async def test_website_publishes_even_when_linkedin_fails(bijeen_job, monkeypatch):
    # Configure a fake Bijeen project publish endpoint (env, read at call time).
    monkeypatch.setenv("BIJEEN_PUBLISH_URL", "https://bijeen.app/api/blog")
    monkeypatch.setenv("BIJEEN_PUBLISH_KEY", "test-key")
    # LinkedIn token present (is_configured True) but no URN; API resolve fails.
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("LINKEDIN_USER_URN", "")

    captured = {}

    class FakeResp:
        def __init__(self, status_code=201, json_data=None, text=""):
            self.status_code = status_code
            self._json = json_data or {}
            self.text = text
        def json(self):
            return self._json

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return FakeResp(201, {"post": {"slug": "sroi-berekenen-per-evenement"}})

    with mock.patch("httpx.post", side_effect=fake_post), \
         mock.patch.object(cp.linkedin_service, "get_member_id",
                           side_effect=ValueError("Kan LinkedIn member ID niet ophalen via API")):
        result = await cp.approve_and_publish(bijeen_job)

    # 1) Website published via the Bijeen project endpoint.
    assert result.get("site", {}).get("success") is True, result
    assert result["site"]["url"].startswith("https://bijeen.app/blog/")
    assert captured["url"] == "https://bijeen.app/api/blog"
    assert captured["json"]["status"] == "published"

    # 2) LinkedIn failure recorded but non-blocking.
    linkedin = result["social"].get("linkedin", {})
    assert linkedin.get("success") is False
    assert "member ID" in (linkedin.get("error") or "")

    # 3) Job reached published status.
    with get_conn() as conn:
        status = conn.execute(
            "SELECT status FROM content_jobs WHERE id=?", (bijeen_job,)).fetchone()[0]
    assert status == "published"


async def test_no_project_endpoint_marks_publish_failed(bijeen_job, monkeypatch):
    """Defensive: if NEITHER netlify nor a project endpoint is configured, the
    publish is skipped (not crashed) AND the job is marked 'publish_failed' —
    never 'published', because nothing actually went live."""
    monkeypatch.delenv("BIJEEN_PUBLISH_URL", raising=False)
    monkeypatch.delenv("BIJEEN_PUBLISH_KEY", raising=False)

    result = await cp.approve_and_publish(bijeen_job)
    assert result.get("site", {}).get("success") is False  # skipped, not crashed

    with get_conn() as conn:
        status = conn.execute(
            "SELECT status FROM content_jobs WHERE id=?", (bijeen_job,)).fetchone()[0]
    assert status == "publish_failed"

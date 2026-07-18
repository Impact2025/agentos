"""Regressie voor twee gedragingen die bij de Daar-publicatiefix zijn toegevoegd:

1. `website_only`-sites (bv. Daar) publiceren wél naar de website + zoekmachines,
   maar doen GEEN social-fan-out en GEEN Content Multiplier. Zo geen (dubbele)
   LinkedIn-post.
2. `approve_and_publish` accepteert ook een job met status 'publish_failed' — dat
   is de "Opnieuw publiceren"-knop in het Actiecentrum. Anders gaf die een harde
   400 en was de retry dood.
"""
import json
from unittest import mock

import pytest

from backend.domains.publish import content_pipeline as cp
from backend.shared.database import get_conn


def _insert_site(site_id, name, website_only=0):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sites (id, name, base_url, publish_api_url, linkedin_user_urn, "
            "website_only, created_at) VALUES (?,?,?,?,?,?,?)",
            (site_id, name, "https://daar.nl", "", "urn:li:person:x",
             website_only, "2026-01-01T00:00:00"),
        )


def _insert_job(job_id, site_id, status="pending_review"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO content_jobs "
            "(id, site_id, title, keyword, rationale, status, blog_html, seo_score, "
            "social_copy, image_path, slug, publish_result, error, created_at, "
            "reviewed_at, qc_report, case_study_id, infographic_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, site_id, "Impact meten voor subsidie", "impact meten", "rationale",
             status,
             "<p>Eerste alinea.</p><h2>Stap 1</h2><p>Uitleg over impact meten.</p>",
             90.0,
             json.dumps({"linkedin": "LinkedIn copy", "facebook": "", "instagram": "",
                         "twitter": ""}),
             "", "impact-meten-voor-subsidie", "", "", "2026-01-01T00:00:00",
             "", "{}", "", ""),
        )


@pytest.fixture
def daar_env(monkeypatch):
    monkeypatch.setenv("DAAR_PUBLISH_URL", "https://www.daar.nl/api/publish")
    monkeypatch.setenv("DAAR_PUBLISH_KEY", "test-key")
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("LINKEDIN_USER_URN", "urn:li:person:x")

    async def _no_live_check(url):
        return None
    monkeypatch.setattr(cp, "_verify_live", _no_live_check)
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM content_jobs WHERE site_id LIKE 'site-daar%'")
        conn.execute("DELETE FROM sites WHERE id LIKE 'site-daar%'")


class _FakeResp:
    def __init__(self, status_code=201, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = ""
        self.headers = {}
    def json(self):
        return self._json


async def test_website_only_skips_social_and_multiplier(daar_env):
    _insert_site("site-daar", "Daar", website_only=1)
    _insert_job("job-daar", "site-daar", status="pending_review")

    posts = []

    def fake_post(url, **kwargs):
        posts.append(url)
        return _FakeResp(201, {"url": "https://daar.nl/blog/impact-meten-voor-subsidie"})

    with mock.patch("httpx.post", side_effect=fake_post), \
         mock.patch.object(cp.linkedin_service, "post_update") as li_post:
        result = await cp.approve_and_publish("job-daar")

    # Website wél gepubliceerd.
    assert result["site"]["success"] is True, result
    # Social bewust overgeslagen — en LinkedIn is nooit aangeroepen.
    assert result["social"] == {"skipped": "website_only — social uitgeschakeld voor deze site"}
    li_post.assert_not_called()
    # Multiplier overgeslagen.
    assert result["multiplier"] == "overgeslagen (website_only)"
    # Enige httpx-post ging naar de website-endpoint (geen social-calls via httpx).
    assert posts == ["https://www.daar.nl/api/publish"]

    with get_conn() as conn:
        status = conn.execute(
            "SELECT status FROM content_jobs WHERE id='job-daar'").fetchone()[0]
    assert status == "published"


async def test_normal_site_still_posts_social(daar_env):
    """Contra-test: zonder website_only loopt de social-fan-out gewoon."""
    _insert_site("site-daar2", "Daar", website_only=0)
    _insert_job("job-daar2", "site-daar2", status="pending_review")

    def fake_post(url, **kwargs):
        return _FakeResp(201, {"url": "https://daar.nl/blog/impact-meten-voor-subsidie"})

    with mock.patch("httpx.post", side_effect=fake_post), \
         mock.patch.object(cp.linkedin_service, "post_update",
                           return_value={"success": True}) as li_post:
        result = await cp.approve_and_publish("job-daar2")

    assert result["site"]["success"] is True
    assert "skipped" not in result["social"]
    li_post.assert_called_once()


async def test_approve_accepts_publish_failed(daar_env):
    """De 'Opnieuw publiceren'-knop: een job op 'publish_failed' mag opnieuw
    gepubliceerd worden (geen 400)."""
    _insert_site("site-daar3", "Daar", website_only=1)
    _insert_job("job-daar3", "site-daar3", status="publish_failed")

    def fake_post(url, **kwargs):
        return _FakeResp(201, {"url": "https://daar.nl/blog/impact-meten-voor-subsidie"})

    with mock.patch("httpx.post", side_effect=fake_post):
        # Mag NIET raisen ondanks status 'publish_failed'.
        result = await cp.approve_and_publish("job-daar3")

    assert result["site"]["success"] is True
    with get_conn() as conn:
        status = conn.execute(
            "SELECT status FROM content_jobs WHERE id='job-daar3'").fetchone()[0]
    assert status == "published"

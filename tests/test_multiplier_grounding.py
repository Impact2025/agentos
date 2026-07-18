"""Tests voor de pro-keten: X-platform in social packs, Content Multiplier
(format-waaier achter de gates) en de Demand→Researcher-grounding-brug."""
import json
import uuid

import pytest


# ── X (Twitter) in de social-pack-laag ──────────────────────────────────────

def test_platforms_bevat_twitter():
    from backend.shared import social_content as sc
    assert "twitter" in sc.PLATFORMS
    assert "twitter" in sc._PLATFORM_TONE


def test_parse_platform_blocks_herkent_x_header():
    from backend.shared.social_content import _parse_platform_blocks
    raw = (
        "LinkedIn: Zakelijke post hier.\n"
        "X: Korte scherpe tweet.\n"
        "Facebook: Warme post.\n"
    )
    out = _parse_platform_blocks(raw, ["linkedin", "twitter", "facebook"])
    assert out["twitter"] == "Korte scherpe tweet."
    assert out["linkedin"] == "Zakelijke post hier."


@pytest.mark.asyncio
async def test_publish_pack_accepteert_twitter_platform(database):
    """De twitter-tak in publish_pack was onbereikbaar zolang 'twitter' niet
    in PLATFORMS zat — regressietest op de platform-gate zelf."""
    from backend.shared import social_content as sc
    # Geen pack → we verwachten 'Pack niet gevonden', NIET 'Onbekend platform'.
    res = await sc.publish_pack("bestaat-niet", "twitter")
    assert res.get("error") != "Onbekend platform: twitter"


# ── Content Multiplier ───────────────────────────────────────────────────────

def _ensure_site(site_id, name="Testproject"):
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sites (id, name, created_at) "
            "VALUES (?, ?, datetime('now'))",
            (site_id, name),
        )


def _insert_content_job(status="published", video_path="", title="Testartikel",
                        site_id="site-x"):
    from backend.shared.database import get_conn
    _ensure_site(site_id)
    job_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO content_jobs (id, site_id, title, keyword, rationale, "
            "status, blog_html, seo_score, social_copy, image_path, slug, "
            "publish_result, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (job_id, site_id, title, "test keyword", "", status,
             "<h1>Test</h1><p>Body</p>", 90, "{}", "", "testartikel", "{}"),
        )
        if video_path:
            try:
                conn.execute("ALTER TABLE content_jobs ADD COLUMN video_path TEXT")
            except Exception:
                pass
            conn.execute("UPDATE content_jobs SET video_path=? WHERE id=?",
                         (video_path, job_id))
    return job_id


@pytest.mark.asyncio
async def test_multiplier_weigert_niet_gepubliceerde_job(database):
    from backend.domains.publish import multiplier
    job_id = _insert_content_job(status="pending_review")
    with pytest.raises(ValueError, match="gepubliceerde"):
        await multiplier.multiply_job(job_id)


@pytest.mark.asyncio
async def test_multiplier_dedupet_pack_en_bestaande_video(database):
    """Bestaat er al een (niet-afgewezen) pack voor dit artikel én een video,
    dan doet de multiplier niets — geen dubbele packs, geen LLM-calls."""
    from backend.shared.database import get_conn
    from backend.domains.publish import multiplier

    title = "Uniek artikel " + uuid.uuid4().hex[:6]
    job_id = _insert_content_job(status="published", video_path="projects/x/v.mp4",
                                 title=title)
    project = multiplier._project_for_job({"site_id": "site-x"})
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO social_posts (id, project, theme, angle, brand_context, "
            "copy_json, image_brief_json, tiktok_pack_json, status, concept, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (f"sp_{uuid.uuid4().hex[:12]}", project, title, "", "",
             "{}", "{}", "{}", "pending_review", 0),
        )

    res = await multiplier.multiply_job(job_id)
    assert res["social_pack"]["skipped"] is True
    assert res["video"]["skipped"] is True


# ── Demand→Researcher-grounding ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grounding_dedupet_en_respecteert_cap(database, monkeypatch):
    from backend.domains.researcher import service as rs
    from backend.domains.seo import engine as demand_engine

    monkeypatch.setattr(rs, "NOTEBOOKLM_ENABLED", True)
    svc = rs.ResearcherService()
    site_id = "site-ground-" + uuid.uuid4().hex[:6]
    site = {"id": site_id, "name": "Testproject"}
    _ensure_site(site_id)

    for i in range(4):
        demand_engine.create_manual_opportunity(
            site_id=site_id, query=f"zoekwoord nummer {i}",
            angle="invalshoek", rationale="", opportunity_score=100 - i,
        )

    # 'zoekwoord nummer 0' is al gegrond (afgerond onderzoek met de query erin).
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO researcher_jobs (id, project, question, notebook_id, "
            "status, created_at, updated_at) VALUES (?,?,?,?,'done',datetime('now'),datetime('now'))",
            (str(uuid.uuid4()), "Testproject",
             "Onderzoek naar 'zoekwoord nummer 0' met verdieping", ""),
        )

    asked = []

    async def fake_run_research(project, question, notebook_id=None):
        asked.append(question)
        return {"id": "fake"}

    monkeypatch.setattr(svc, "run_research", fake_run_research)
    n = await svc.ground_new_opportunities(site, max_questions=2)

    assert n == 2
    assert len(asked) == 2
    # De al-gegronde query is overgeslagen; de volgende twee kansen zijn gepakt.
    assert not any("zoekwoord nummer 0" in q for q in asked)
    assert any("zoekwoord nummer 1" in q for q in asked)


@pytest.mark.asyncio
async def test_grounding_uit_als_notebooklm_uit(database, monkeypatch):
    from backend.domains.researcher import service as rs
    monkeypatch.setattr(rs, "NOTEBOOKLM_ENABLED", False)
    svc = rs.ResearcherService()
    n = await svc.ground_new_opportunities({"id": "x", "name": "Y"})
    assert n == 0

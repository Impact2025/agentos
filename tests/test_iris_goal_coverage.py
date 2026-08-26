"""Iris' doel-vloer: het systeem mag niet op 0 actieve doelen blijven staan
als er een bewezen kans ligt — maar ook nooit een doel verzinnen als die er
niet is. Zie CLAUDE.md sectie 3/9 en iris/service.py:_ensure_goal_coverage."""
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


def _seed_goal(conn, project="Testsite", status="running"):
    gid = f"goal-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO goals (id, title, objective, project, status, created_at, updated_at) "
        "VALUES (?, 'x', 'x', ?, ?, datetime('now'), datetime('now'))",
        (gid, project, status),
    )
    conn.commit()
    return gid


@pytest.fixture()
def coverage_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        for t in ("sites", "opportunities"):
            c.execute(f"DELETE FROM {t}")


@pytest.mark.asyncio
async def test_geen_doel_als_er_al_een_actief_doel_is(conn, coverage_clean):
    from backend.domains.iris import service

    _seed_goal(conn, project="AnderProject", status="draft")
    result = await service._ensure_goal_coverage(goal_created_this_run=False)
    assert result is None


@pytest.mark.asyncio
async def test_geen_doel_als_deze_ronde_er_al_een_is_gemaakt(conn, coverage_clean, monkeypatch):
    from backend.domains.iris import service

    # Geen actief doel in de DB, maar Iris' eigen advies maakte er deze
    # ronde al een — de vloer mag daar niet nóg eentje overheen zetten.
    monkeypatch.setattr(service, "_active_goal_count", lambda: 0)
    result = await service._ensure_goal_coverage(goal_created_this_run=True)
    assert result is None


@pytest.mark.asyncio
async def test_gepauzeerde_site_krijgt_geen_vloer_doel(conn, coverage_clean, monkeypatch):
    # Workshop/demo-modus (26 aug 2026): een gepauzeerd project mag geen
    # nieuw doel krijgen, ook niet als het de enige site met een bewezen
    # kans is — anders start Iris iets op precies het project dat stil moet
    # blijven staan.
    from backend.domains.iris import service
    from backend.domains.seo import engine as demand_engine

    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, paused, created_at) "
        "VALUES ('paused-site', 'GepauzeerdProject', 'https://x.nl', "
        "'sc-domain:x.nl', 1, 2, 1, datetime('now'))"
    )
    conn.commit()
    monkeypatch.setattr(
        demand_engine, "list_opportunities_truth",
        lambda **kw: (_ for _ in ()).throw(
            AssertionError("mag niet aangeroepen worden voor een gepauzeerde site")))
    result = await service._ensure_goal_coverage(goal_created_this_run=False)
    assert result is None


@pytest.mark.asyncio
async def test_geen_kans_geen_doel(conn, coverage_clean, monkeypatch):
    from backend.domains.iris import service
    from backend.domains.seo import engine as demand_engine

    _seed_site(conn)
    monkeypatch.setattr(demand_engine, "list_opportunities_truth", lambda **kw: [])
    result = await service._ensure_goal_coverage(goal_created_this_run=False)
    assert result is None


@pytest.mark.asyncio
async def test_volle_wachtrij_slaat_site_over(conn, coverage_clean, monkeypatch):
    from backend.domains.iris import service
    from backend.domains.seo import engine as demand_engine
    from backend.domains.iris import actions as iris_actions

    _seed_site(conn)
    monkeypatch.setattr(demand_engine, "list_opportunities_truth",
                         lambda **kw: [{"query": "een echte kans", "impressions": 50,
                                        "position": 8.0, "clicks": 1}])
    monkeypatch.setattr(iris_actions, "pending_review_count", lambda site_id: 99)
    result = await service._ensure_goal_coverage(goal_created_this_run=False)
    assert result is None


@pytest.mark.asyncio
async def test_stelt_doel_voor_en_start_hem_op_de_sterkste_kans(conn, coverage_clean, monkeypatch):
    from backend.domains.iris import service
    from backend.domains.seo import engine as demand_engine

    _seed_site(conn)
    monkeypatch.setattr(demand_engine, "list_opportunities_truth",
                         lambda **kw: [{"query": "kleine kans", "impressions": 20,
                                        "position": 15.0, "clicks": 0},
                                       {"query": "grote kans", "impressions": 200,
                                        "position": 6.0, "clicks": 2}])

    created = {}

    async def fake_create_and_plan(title, objective, project):
        created["title"] = title
        created["project"] = project
        assert "grote kans" in objective
        return {"goal_id": "goal-xyz", "title": title, "objective": objective, "plan": {}}

    confirmed, started = [], []
    monkeypatch.setattr("backend.domains.goal.service.create_and_plan", fake_create_and_plan)
    monkeypatch.setattr("backend.domains.goal.service.confirm_plan", lambda gid: confirmed.append(gid))

    async def fake_start(gid):
        started.append(gid)
        return {}
    monkeypatch.setattr("backend.domains.goal.service.start_goal_async", fake_start)

    result = await service._ensure_goal_coverage(goal_created_this_run=False)
    assert result and "gestart" in result and "Testsite" in result
    assert "grote kans" in created["title"]
    assert created["project"] == "Testsite"
    assert confirmed == ["goal-xyz"]
    assert started == ["goal-xyz"]

    with service.get_conn() as c:
        row = c.execute(
            "SELECT artifact FROM activity_log WHERE action='iris_bijsturing' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row and row["artifact"] == "/api/goals/goal-xyz"


@pytest.mark.asyncio
async def test_autostart_uit_laat_doel_als_concept_staan(conn, coverage_clean, monkeypatch):
    from backend.domains.iris import service
    from backend.domains.seo import engine as demand_engine

    _seed_site(conn)
    monkeypatch.setattr(service, "_GOAL_FLOOR_AUTOSTART", False)
    monkeypatch.setattr(demand_engine, "list_opportunities_truth",
                         lambda **kw: [{"query": "een echte kans", "impressions": 50,
                                        "position": 8.0, "clicks": 1}])

    async def fake_create_and_plan(title, objective, project):
        return {"goal_id": "goal-abc", "title": title, "objective": objective, "plan": {}}

    called = {"confirm": 0, "start": 0}
    monkeypatch.setattr("backend.domains.goal.service.create_and_plan", fake_create_and_plan)
    monkeypatch.setattr("backend.domains.goal.service.confirm_plan",
                         lambda gid: called.__setitem__("confirm", called["confirm"] + 1))

    async def fake_start(gid):
        called["start"] += 1
    monkeypatch.setattr("backend.domains.goal.service.start_goal_async", fake_start)

    result = await service._ensure_goal_coverage(goal_created_this_run=False)
    assert result and "voorgesteld" in result
    assert called == {"confirm": 0, "start": 0}

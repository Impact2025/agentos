"""Agent Control's 'Voer allemaal uit' — elke pijler routeert naar zijn eigen
echte mechanisme i.p.v. een generieke Gauntlet-opstel zonder tool-access.

Aanleiding (13 aug 2026): een klik op 'Voer allemaal uit' spawnde 13 Gauntlet-
runs die nergens landden (geen Wachtrij, geen outcome-kaart) — en bij nameting
bleek een deel van die runs, dankzij een te vage generieke benchmark, alsnog
via een bestaand auto-queue-mechanisme (gauntlet/service.py:_auto_queue_run)
als 'hook'-content in de Wachtrij te belanden: essays over "optimaliseer 3
pagina's" of "hervat vastgelopen doelen", gestaged als reviewbaar artikel.

Deze suite bewaakt drie dingen:
1. seo/uitvoering/hygiene spawnen GEEN Gauntlet-run — ze roepen het echte
   mechanisme voor dat werk rechtstreeks aan.
2. content spawnt wél een Gauntlet-run, maar met een expliciete artikel-
   benchmark (niet de generieke terugval) en dedupliceert tegen het bestaande
   auto-queue-mechanisme i.p.v. er een tweede publicatie bovenop te zetten.
3. dedupe + de nieuwe integrity-invariant vangen een deploy die nooit wordt
   afgesloten.
"""
import uuid

from backend.domains.agentctl import suggest as agentctl_suggest
from backend.domains.iris import integrity as ig
from backend.shared.database import get_conn


def _project(prefix="AgentctlTest"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _rows_for(project):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM agentctl_deploys WHERE project = ? ORDER BY id", (project,)
        ).fetchall()]


# ── seo-pijler: geen Gauntlet, wel de echte CTR-optimizer ──────────────────

def test_execute_seo_calls_real_optimizer_not_gauntlet(monkeypatch):
    project = _project()
    fake_site = {"id": "site-1", "name": project, "gsc_property": "sc-domain:test"}
    calls = []

    monkeypatch.setattr(
        "backend.domains.seo.sites.find_site_by_project",
        lambda p: fake_site if p == project else None,
    )
    monkeypatch.setattr(
        "backend.domains.analytics.insights.quick_wins_for",
        lambda p, limit=3: [{"query": "test zoekwoord", "position": 8.0}] if p == project else [],
    )

    async def fake_optimize_query(site, query):
        calls.append((site, query))
        return {"outcome": "varianten", "query": query, "page": "https://test/x"}

    monkeypatch.setattr("backend.domains.seo.optimizer.optimize_query", fake_optimize_query)

    def _boom(*a, **kw):
        raise AssertionError("seo-pijler mag geen Gauntlet-run spawnen")

    monkeypatch.setattr("backend.domains.gauntlet.service.spawn_gauntlet", _boom)

    import asyncio
    result = asyncio.run(agentctl_suggest._execute_seo(project, "SEO Editor", "taak"))

    assert result["ok"] is True
    assert calls == [(fake_site, "test zoekwoord")]
    rows = _rows_for(project)
    assert len(rows) == 1
    assert rows[0]["status"] == "staged"
    assert rows[0]["run_id"] == ""


def test_execute_seo_no_effect_when_no_quick_wins(monkeypatch):
    project = _project()
    monkeypatch.setattr(
        "backend.domains.seo.sites.find_site_by_project",
        lambda p: {"id": "site-1", "name": p, "gsc_property": "sc-domain:test"},
    )
    monkeypatch.setattr(
        "backend.domains.analytics.insights.quick_wins_for", lambda p, limit=3: [],
    )
    import asyncio
    result = asyncio.run(agentctl_suggest._execute_seo(project, "SEO Editor", "taak"))
    assert result["ok"] is False
    rows = _rows_for(project)
    assert rows[0]["status"] == "no_effect"


# ── uitvoering-pijler: geen Gauntlet, wel autoheal_goals() ──────────────────

def test_execute_uitvoering_calls_autoheal_not_gauntlet(monkeypatch):
    project = _project()

    def fake_autoheal():
        return {
            "resumed": [{"goal_id": "g1", "project": project, "title": "x"}],
            "deleted": [],
            "skipped": [],
        }

    monkeypatch.setattr("backend.domains.strategist.service.autoheal_goals", fake_autoheal)

    def _boom(*a, **kw):
        raise AssertionError("uitvoering-pijler mag geen Gauntlet-run spawnen")

    monkeypatch.setattr("backend.domains.gauntlet.service.spawn_gauntlet", _boom)

    result = agentctl_suggest._execute_uitvoering(project, "Content Editor", "taak")

    assert result["ok"] is True
    assert "1 vastgelopen doel(en) hervat" in result["detail"]
    rows = _rows_for(project)
    assert rows[0]["status"] == "staged"


def test_execute_uitvoering_no_effect_when_nothing_stuck(monkeypatch):
    project = _project()
    monkeypatch.setattr(
        "backend.domains.strategist.service.autoheal_goals",
        lambda: {"resumed": [], "deleted": [], "skipped": []},
    )
    result = agentctl_suggest._execute_uitvoering(project, "Content Editor", "taak")
    assert result["ok"] is True
    assert result["detail"] == "niets te hervatten"
    rows = _rows_for(project)
    assert rows[0]["status"] == "no_effect"


# ── hygiene-pijler: geen losse Gauntlet-spawn, wel process_one_under_threshold ──

def test_execute_hygiene_calls_orchestrator_not_gauntlet(monkeypatch):
    project = _project()
    captured = {}

    async def fake_process_one(threshold=80, max_wait_s=600, project=None):
        captured["project"] = project
        return {"processed": True, "published_job_id": "job-123"}

    monkeypatch.setattr(
        "backend.domains.orchestrator.service.process_one_under_threshold", fake_process_one,
    )

    def _boom(*a, **kw):
        raise AssertionError("hygiene-pijler mag geen losse Gauntlet-run spawnen")

    monkeypatch.setattr("backend.domains.gauntlet.service.spawn_gauntlet", _boom)

    import asyncio
    result = asyncio.run(agentctl_suggest._execute_hygiene(project, "Content Judge", "taak"))

    assert result["ok"] is True
    assert result["artifact"] == "/api/content-queue/job-123"
    assert captured["project"] == project
    rows = _rows_for(project)
    assert rows[0]["status"] == "staged"


def test_execute_hygiene_no_effect_when_nothing_stuck(monkeypatch):
    project = _project()

    async def fake_process_one(threshold=80, max_wait_s=600, project=None):
        return {"processed": False, "reason": "geen stukken onder de grens"}

    monkeypatch.setattr(
        "backend.domains.orchestrator.service.process_one_under_threshold", fake_process_one,
    )
    import asyncio
    result = asyncio.run(agentctl_suggest._execute_hygiene(project, "Content Judge", "taak"))
    assert result["ok"] is True
    assert result["detail"] == "geen stukken onder de grens"
    rows = _rows_for(project)
    assert rows[0]["status"] == "no_effect"


# ── content-pijler: spawnt wél Gauntlet, met een échte artikel-benchmark ───

def test_execute_content_passes_explicit_article_benchmark(monkeypatch):
    project = _project()
    captured = {}

    def fake_deploy_agent(agent_id, task, project=None, benchmark=None):
        captured["agent_id"] = agent_id
        captured["benchmark"] = benchmark
        return {"ok": True, "run_id": "run-abc"}

    monkeypatch.setattr(
        "backend.domains.agentctl.service.deploy_agent", fake_deploy_agent,
    )

    async def fake_poller(deploy_id, run_id, project):
        return None

    monkeypatch.setattr(agentctl_suggest, "_poll_content_run", fake_poller)

    import asyncio

    async def _run():
        r = agentctl_suggest._execute_content(project, "SEO Copywriter", "taak", 42)
        await asyncio.sleep(0)  # laat de geplande poller-taak (no-op) uitlopen
        return r

    result = asyncio.run(_run())

    assert result["ok"] is True
    assert result["run_id"] == "run-abc"
    # Bevat de exacte frase die gauntlet/service.py:_auto_queue_run met een
    # regex leest om de juiste site te resolven — zonder deze frase publiceert
    # auto-queue (als hij vóór onze eigen poller zou vuren) naar de verkeerde site.
    assert f"project '{project}'" in captured["benchmark"]
    # Niet de generieke terugval-tekst van deploy_agent — die liet elke vage
    # pijler-taak de drempel halen (zie de docstring in suggest.py).
    assert "lever concreet" not in captured["benchmark"]
    rows = _rows_for(project)
    assert rows[0]["status"] == "running"
    assert rows[0]["run_id"] == "run-abc"


def test_execute_content_without_agent_id_fails_cleanly(monkeypatch):
    project = _project()
    result = agentctl_suggest._execute_content(project, "SEO Copywriter", "taak", None)
    assert result["ok"] is False
    assert result["reason"] == "geen agent-id"
    assert _rows_for(project) == []


# ── dedupe: max 1x/dag per (project, pijler) ────────────────────────────────

def test_today_has_deploy_blocks_running_and_same_day_resolved():
    project = _project()
    assert agentctl_suggest._today_has_deploy(project, "seo") is False

    deploy_id = agentctl_suggest._record_deploy(
        project=project, pillar="seo", agent="SEO Editor", task="t", status="running",
    )
    assert agentctl_suggest._today_has_deploy(project, "seo") is True
    # Andere pijler voor hetzelfde project is niet geblokkeerd.
    assert agentctl_suggest._today_has_deploy(project, "content") is False

    agentctl_suggest._resolve_deploy(deploy_id, status="staged", artifact="/x")
    # Vandaag afgerond telt nog steeds als 'al gedaan'.
    assert agentctl_suggest._today_has_deploy(project, "seo") is True


def test_execute_one_skips_when_already_deployed_today():
    project = _project()
    agentctl_suggest._record_deploy(
        project=project, pillar="hygiene", agent="Content Judge", task="t", status="running",
    )
    import asyncio
    result = asyncio.run(agentctl_suggest._execute_one({
        "project": project, "pillar_key": "hygiene", "agent": "Content Judge", "task": "t",
    }))
    assert result["ok"] is False
    assert "al bezig" in result["reason"] or "al gedaan" in result["reason"]


# ── integrity-invariant: een deploy die nooit wordt afgesloten ─────────────

def test_agentctl_run_zonder_effect_flags_stale_running_row():
    project = _project()
    with get_conn() as c:
        c.execute(
            "INSERT INTO agentctl_deploys (run_id, project, pillar, agent, task, status, "
            "artifact, created_at, resolved_at) VALUES "
            "('run-stale', ?, 'content', 'SEO Copywriter', 't', 'running', '', "
            "datetime('now', '-2 hour'), '')",
            (project,),
        )
    bevindingen = [b for b in ig._check_agentctl_run_zonder_effect() if b.project == project]
    assert len(bevindingen) == 1
    assert "run-stale" in bevindingen[0].detail


def test_agentctl_run_zonder_effect_ignores_fresh_running_row():
    project = _project()
    agentctl_suggest._record_deploy(
        project=project, pillar="content", agent="SEO Copywriter", task="t",
        status="running", run_id="run-fresh",
    )
    bevindingen = [b for b in ig._check_agentctl_run_zonder_effect() if b.project == project]
    assert bevindingen == []


def test_agentctl_run_zonder_effect_ignores_resolved_row():
    project = _project()
    deploy_id = agentctl_suggest._record_deploy(
        project=project, pillar="seo", agent="SEO Editor", task="t", status="running",
    )
    agentctl_suggest._resolve_deploy(deploy_id, status="staged", artifact="/x")
    with get_conn() as c:
        c.execute(
            "UPDATE agentctl_deploys SET created_at = datetime('now', '-2 hour') WHERE id = ?",
            (deploy_id,),
        )
    bevindingen = [b for b in ig._check_agentctl_run_zonder_effect() if b.project == project]
    assert bevindingen == []

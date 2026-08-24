"""Regressietest voor de 'zelfde artikel 20+ keer herschreven'-bug (14 aug 2026).

`orchestrator.process_one_under_threshold` vond bij een succesvolle Gauntlet-
herschrijving een NIEUW content_job aan, maar liet het bronrecord ('rejected'/
'stuck') gewoon staan. De volgende aanroep vond dat bronrecord dus opnieuw en
herschreef het nogmaals — geen fout, geen crash, gewoon een oneindige, dure
lus die op één dag de hele LLM-dagbudget opsoupeerde. Fix: `mark_superseded`
sluit het bronrecord af bij succes, en een cross-run cap
(`ORCHESTRATOR_MAX_ATTEMPTS`) stopt de lus ook als het artikel telkens onder
de grens blijft steken. Deze test bewijst beide paden.
"""
import asyncio
from datetime import datetime

from backend.domains.publish import content_pipeline as cp
from backend.shared.config import ORCHESTRATOR_MAX_ATTEMPTS

_real_sleep = asyncio.sleep


async def _instant_sleep(*_a, **_k):
    await _real_sleep(0)


def _seed_site_and_job(conn, site_id="orch-t1", job_id="oj1", score=60, status="rejected",
                        title="Test Artikel", attempts=0, site_name="OrchTestSite"):
    conn.execute(
        "INSERT OR IGNORE INTO sites (id,name,base_url,auto_content_enabled,created_at) "
        "VALUES (?,?,?,?,?)", (site_id, site_name, "http://x", 0, datetime.now().isoformat()))
    conn.execute(
        "INSERT INTO content_jobs (id,site_id,title,keyword,status,blog_html,seo_score,"
        "created_at,orchestrator_attempts) VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, site_id, title, "test keyword", status,
         "<h1>Test</h1><p>body</p>", score, datetime.now().isoformat(), attempts))


def test_successful_rewrite_supersedes_source_job(monkeypatch):
    """Bij een geslaagde Gauntlet-run mag het bronrecord niet opnieuw vindbaar
    zijn — anders herschrijft de volgende aanroep hetzelfde artikel weer."""
    from backend.shared.database import get_conn
    from backend.domains.orchestrator import service as orch
    from backend.domains.gauntlet import service as gauntlet_service

    with get_conn() as conn:
        _seed_site_and_job(conn, site_id="orch-t1", job_id="oj1")

    monkeypatch.setattr(orch.asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr(gauntlet_service, "spawn_gauntlet",
                         lambda **_k: {"run_id": "run-1"})
    monkeypatch.setattr(gauntlet_service, "get_run",
                         lambda run_id: {"status": "passed", "threshold": 80})
    monkeypatch.setattr(gauntlet_service, "publish_run_to_wachtrij",
                         lambda *a, **k: {"job_id": "new-job-1"})

    result = asyncio.run(orch.process_one_under_threshold(threshold=80, project="OrchTestSite"))
    assert result["processed"] is True
    assert result["published_job_id"] == "new-job-1"

    source = cp.get_job("oj1")
    assert source["status"] == "superseded", \
        f"bronrecord had moeten sluiten, staat nog op {source['status']!r}"
    assert source["superseded_by"] == "new-job-1"

    # De volgende aanroep vindt niets meer — het bronrecord is geen 'rejected'/
    # 'stuck' meer en de nieuwe job staat op 'pending_review' (buiten bereik).
    result2 = asyncio.run(orch.process_one_under_threshold(threshold=80, project="OrchTestSite"))
    assert result2["processed"] is False
    assert result2["reason"] == "geen stukken onder de grens"


def test_repeated_failure_stops_after_cross_run_cap(monkeypatch):
    """Een artikel dat de grens telkens niet haalt, mag maar
    ORCHESTRATOR_MAX_ATTEMPTS keer een zware Gauntlet-run kosten — daarna
    moet de picker 'm overslaan i.p.v. voor altijd opnieuw te proberen."""
    from backend.shared.database import get_conn
    from backend.domains.orchestrator import service as orch
    from backend.domains.gauntlet import service as gauntlet_service

    with get_conn() as conn:
        _seed_site_and_job(conn, site_id="orch-t2", job_id="oj2", title="Vastloper",
                            site_name="OrchTestSite2")

    monkeypatch.setattr(orch.asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr(gauntlet_service, "spawn_gauntlet",
                         lambda **_k: {"run_id": "run-x"})
    monkeypatch.setattr(gauntlet_service, "get_run",
                         lambda run_id: {"status": "failed", "threshold": 80})

    spawn_calls = {"n": 0}
    real_spawn = gauntlet_service.spawn_gauntlet

    def counting_spawn(**k):
        spawn_calls["n"] += 1
        return real_spawn(**k)
    monkeypatch.setattr(gauntlet_service, "spawn_gauntlet", counting_spawn)

    for _ in range(ORCHESTRATOR_MAX_ATTEMPTS + 3):
        asyncio.run(orch.process_one_under_threshold(threshold=80, project="OrchTestSite2"))

    assert spawn_calls["n"] == ORCHESTRATOR_MAX_ATTEMPTS, \
        f"verwacht precies {ORCHESTRATOR_MAX_ATTEMPTS} Gauntlet-runs, kreeg {spawn_calls['n']}"

    final = cp.get_job("oj2")
    assert final["orchestrator_attempts"] == ORCHESTRATOR_MAX_ATTEMPTS
    assert final["status"] == "rejected"  # niet opgelost, maar ook niet verder verbrand


# ── Eén lopende run per artikel (15 aug 2026) ──────────────────────────────

def test_tweede_gelijktijdige_aanroep_pakt_hetzelfde_stuk_niet(monkeypatch):
    """'process ONE' pakte hetzelfde artikel twee keer tegelijk op.

    Gemeten: vijf bronrecords van één artikel wezen naar dezelfde opvolger, en
    twee Gauntlet-runs startten 0,3 seconde na elkaar. Tussen 'lees de
    kandidaten' en 'hoog de teller op' past een tweede aanroep, en elke
    gelijktijdige run is een volle 3-criticus-ronde dubbel betaald.
    """
    from backend.shared.database import get_conn
    from backend.domains.orchestrator import service as orch

    with get_conn() as conn:
        _seed_site_and_job(conn, site_id="orch-lock", job_id="lock1", score=50)

    gestart: list = []
    losser = asyncio.Event()

    def _spawn(**kw):
        gestart.append(kw)
        return {"run_id": f"run-{len(gestart)}"}

    def _get_run(_rid):
        # Blijf 'running' tot de test het slot wil zien werken.
        return {"status": "running" if not losser.is_set() else "stopped"}

    from backend.domains.gauntlet import service as gs
    monkeypatch.setattr(gs, "spawn_gauntlet", _spawn)
    monkeypatch.setattr(gs, "get_run", _get_run)
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    async def _scenario():
        eerste = asyncio.create_task(
            orch.process_one_under_threshold(threshold=85, max_wait_s=1))
        await _real_sleep(0)  # laat de eerste tot in het slot komen
        tweede = await orch.process_one_under_threshold(threshold=85, max_wait_s=1)
        losser.set()
        await eerste
        return tweede

    tweede = asyncio.run(_scenario())

    assert tweede["processed"] is False
    assert "al een Gauntlet-run" in tweede["reason"]
    assert len(gestart) == 1, "er hoort maar één Gauntlet-run gestart te zijn"


def test_slot_wordt_vrijgegeven_na_een_fout(monkeypatch):
    """Een mislukte run mag het artikel niet tot de herstart blokkeren."""
    from backend.shared.database import get_conn
    from backend.domains.orchestrator import service as orch
    from backend.domains.gauntlet import service as gs

    with get_conn() as conn:
        _seed_site_and_job(conn, site_id="orch-lock2", job_id="lock2", score=50)

    def _knalt(**_kw):
        raise RuntimeError("gateway plat")

    monkeypatch.setattr(gs, "spawn_gauntlet", _knalt)
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    asyncio.run(orch.process_one_under_threshold(threshold=85, max_wait_s=1))
    assert "lock2" not in orch._LOPEND


def test_benchmark_matcht_exact_en_niet_op_deelnaam():
    """'Impact' zit in twee sitenamen; een substring-match liet de dict-volgorde
    beslissen welke huisstijl een artikel kreeg."""
    from backend.domains.orchestrator.service import _benchmark_for_project

    wai = _benchmark_for_project("WeAreImpact")
    tmi = _benchmark_for_project("TeambuildingMetImpact")
    assert "WeAreImpact-stijl" in wai
    assert wai != tmi, "TeambuildingMetImpact mag niet de WeAreImpact-benchmark krijgen"
    assert "TeambuildingMetImpact" in tmi  # generieke terugval noemt het eigen project


def test_alle_hardcoded_benchmarks_noemen_hun_project():
    """Regressietest (19 aug 2026): alle vier hardcoded stijlgidsen in
    `_PROJECT_BENCHMARKS` misten de frase "project 'X'". `_auto_queue_run`
    in gauntlet/service.py leest die frase met een regex om te weten welke
    site een geslaagde run mag ontvangen; zonder match gooit hij
    `OnbekendProject` en verdwijnt een voltooide Gauntlet-herschrijving in een
    foutkaart in plaats van de Wachtrij. Deze test toetst elke hardcoded
    benchmark tegen exact dezelfde regex, zodat een nieuwe of gewijzigde
    stijlgids die de frase vergeet een falende test geeft in plaats van een
    stil weggegooide run."""
    from backend.domains.orchestrator.service import _PROJECT_BENCHMARKS
    from backend.domains.gauntlet.service import _PROJECT_IN_BENCHMARK_RE

    for project, benchmark in _PROJECT_BENCHMARKS.items():
        m = _PROJECT_IN_BENCHMARK_RE.search(benchmark)
        assert m, (
            f"benchmark voor '{project}' mist de frase \"project '{project}'\" — "
            "een geslaagde Gauntlet-run zou hier niet automatisch gepubliceerd worden"
        )
        assert m.group(1).strip() == project, (
            f"benchmark voor '{project}' noemt een ander project ({m.group(1)!r}) — "
            "dat resolvet naar de verkeerde site"
        )

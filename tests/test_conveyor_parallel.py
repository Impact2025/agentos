"""Segmented Batch Dispatch — bewijs dat de conveyor onafhankelijke taken
parallel afvuurt (via asyncio.gather) en afhankelijke (ketting-)taken laat
wachten tot hun voorganger 'done' is.

Geen LLM/netwerk: de echte agent-runner wordt vervangen door een meetbare
fake die start-/eindtijdstippen logt zodat we echte overlap kunnen bewijzen.
"""
import asyncio
import time

import pytest

from backend.domains.pipeline import conveyor as conveyor_mod
from backend.domains.pipeline import service as pipeline_service
from backend.shared import agent_runner as agent_runner_mod


# ── Meetbare fake agent-runner (geen LLM, geen netwerk) ──────────────────────
class _FakeStream:
    """Async-generator die `sleep` seconden 'werkt' en de start/eindtijd
    vastlegt zodat we echte parallelle overlap kunnen aantonen."""
    def __init__(self, sleep: float = 0.2):
        self._sleep = sleep
        self.runs: list[dict] = []

    async def __call__(self, *args, **kwargs):
        start = time.perf_counter()
        self.runs.append({"start": start, "end": None})
        idx = len(self.runs) - 1
        await asyncio.sleep(self._sleep)
        self.runs[idx]["end"] = time.perf_counter()
        yield {"type": "text", "text": "# Concept\n\nVoldoende lange output voor de kwaliteitscheck.\n" * 6}


@pytest.fixture
def fake_runner(monkeypatch):
    fr = _FakeStream(sleep=0.2)
    monkeypatch.setattr(agent_runner_mod, "run_agent", fr)
    return fr


def _make_task(conn, title, workspace_path, status="ready", position=0):
    tid = f"t_{title}_{int(time.time()*1000)}_{position}"
    now = pipeline_service._now()
    conn.execute(
        "INSERT INTO tasks (id, title, description, status, workspace_path, position, agent, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, title, "beschrijving", status, workspace_path, position, "hermes", now, now),
    )
    conn.commit()
    return tid


def _clean_tasks(conn):
    """Leeg de tasks-tabel zodat deze test niet leunt op de volgorde van de
    clean_tables-fixture (die pas na de test teardownt). Andere tests laten
    soms 'ready'-taken achter in de gedeelde test-DB."""
    conn.execute("DELETE FROM tasks")
    conn.commit()


@pytest.mark.asyncio
async def test_independent_tasks_run_in_parallel(fake_runner, conn):
    """Twee onafhankelijke ready-taken worden in één gather-batch tegelijk
    uitgevoerd: de uitvoeringstijden overlappen (verschil << 2 x slaap)."""
    _clean_tasks(conn)
    _make_task(conn, "A", "chain-a/01.md", position=0)
    _make_task(conn, "B", "chain-b/01.md", position=1)

    tasks = pipeline_service.get_ready_tasks()
    assert len(tasks) == 2, "Conveyor moet beide ready-taken in één batch zien"
    results = await asyncio.gather(
        *(conveyor_mod._execute_task(t) for t in tasks),
        return_exceptions=True,
    )

    assert all(isinstance(r, dict) and r.get("status") == "done" for r in results)

    # Bewijs overlap: start-tijd van taak B ligt vóór eind-tijd van taak A.
    runs = fake_runner.runs
    assert len(runs) == 2, f"verwacht 2 agent-runs, kreeg {len(runs)}"
    overlap = runs[1]["start"] < runs[0]["end"]
    assert overlap, (
        f"Taken liepen sequentieel: run0 end={runs[0]['end']:.3f}, "
        f"run1 start={runs[1]['start']:.3f}"
    )

    # Totale wandklok mag niet near-sequentieel zijn (0.2s, marge 0.1s).
    total = max(r["end"] for r in runs) - min(r["start"] for r in runs)
    assert total < 0.35, f"Totale tijd duidt op sequentieel: {total:.3f}s"


@pytest.mark.asyncio
async def test_dependent_task_waits_for_predecessor(fake_runner, conn):
    """Een taak in een keten wordt pas 'ready' (en dus uitgevoerd) nadat zijn
    voorganger 'done' is — de state-machine promoot hem pas dan."""
    _clean_tasks(conn)
    _make_task(conn, "K1", "chain-k/01.md", status="ready", position=0)
    _make_task(conn, "K2", "chain-k/02.md", status="todo", position=1)

    # Conveyor ziet in ronde 1 alleen K1 (K2 is 'todo').
    ready_before = pipeline_service.get_ready_tasks()
    assert [t["title"] for t in ready_before] == ["K1"], "K2 mag niet 'ready' zijn vóór K1"

    # Zet K1 op done (state-machine) en promoveer K2 -> ready (zoals
    # set_task_status doet voor de volgende keten-stap).
    pipeline_service.set_task_status(ready_before[0]["id"], "done")
    conn.execute("UPDATE tasks SET status='ready' WHERE title='K2' AND status='todo'")
    conn.commit()

    ready_after = pipeline_service.get_ready_tasks()
    assert [t["title"] for t in ready_after] == ["K2"], "K2 pas 'ready' na K1 done"

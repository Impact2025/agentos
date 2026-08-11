"""End-to-end test van de Gauntlet Loop ZONDER echte LLM-calls (geen tokens).

We monkeypatchen backend.shared.agent_runner.run_agent met een fake die
deterministische tekst/json teruggeeft, zodat we de volledige orchestratie
(decompose -> parallelle builder + blinde criticus -> stop/verdict) kunnen
bewijzen zonder de cloud aan te roeren. Gebruikt een wegwerp-DB in %TEMP%.

Opmerking: spawn_gauntlet() roept asyncio.create_task() en heeft dus een lopende
event loop nodig (die er in de server wél is). In de test draaien we de interne
_run_gauntlet() coroutine rechtstreeks via asyncio.run().
"""
import asyncio
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.shared import database as db_mod
from backend.shared import agent_runner as agent_mod


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db", prefix="gauntlet_test_")
    os.close(fd)
    for ext in ("", "-wal", "-shm"):
        p = path + ext
        if os.path.exists(p):
            os.remove(p)
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    db_mod.init_db()
    yield path
    for ext in ("", "-wal", "-shm"):
        p = path + ext
        if os.path.exists(p):
            os.remove(p)


def _make_fake_run_agent():
    call_state = {"n": 0}

    async def fake_run_agent(messages, system_prompt="", **kwargs):
        call_state["n"] += 1
        if "Lead Agent" in system_prompt:
            yield {"type": "text", "text": json.dumps({
                "subtasks": [
                    {"role": "Belichting", "goal": "Maak de belichting warm en filmisch."},
                    {"role": "Fysica", "goal": "Maak de voertuigfysica strak en responsief."},
                ]
            })}
            return
        if "BLINDE" in system_prompt:
            score = 60 + min(call_state["n"] % 3, 2) * 15  # 60, 75, 90, ...
            verdict = "pass" if score >= 80 else "revise"
            yield {"type": "text", "text": json.dumps({
                "score": score, "verdict": verdict,
                "feedback": f"Ronde-{call_state['n']} feedback: scherper maken.",
            })}
            return
        yield {"type": "text", "text": f"# Concept ronde {call_state['n']}\n\n(pseudo-uitvoer)"}

    return fake_run_agent


@pytest.fixture
def patch_agent(monkeypatch):
    monkeypatch.setattr(agent_mod, "run_agent", _make_fake_run_agent())
    yield


def test_spawn_requires_benchmark(temp_db, patch_agent):
    from backend.domains.gauntlet import service
    with pytest.raises(ValueError):
        service.spawn_gauntlet(objective="Doe iets", benchmark="", threshold=85)


def test_gauntlet_runs_end_to_end(temp_db, patch_agent):
    from backend.domains.gauntlet import service

    run_id = service._create_run("Bouw F1-game", "ref-screenshot", "", 80, 3)
    stop_flag = {}
    asyncio.run(service._run_gauntlet(
        run_id, "Bouw F1-game", "ref-screenshot", 80, 3, "", stop_flag))
    run = service.get_run(run_id)

    assert run["subtask_count"] == 2, f"Verwacht 2 deeltaken, kreeg {run['subtask_count']}"
    assert len(run["subtasks"]) == 2
    roles = {s["role"] for s in run["subtasks"]}
    assert {"Belichting", "Fysica"}.issubset(roles), f"Deeltaken ontbreken: {roles}"
    assert run["status"] in ("passed", "partial", "stopped", "stopped_by_user", "failed")
    assert len(run["iterations"]) >= 2, "Geen iteraties opgeslagen"
    # De blinde criticus heeft echt gescoord (geen 0-default overal)
    assert any(it["score"] >= 80 for it in run["iterations"]), "Geen enkele ronde haalde de benchmark"


def test_gauntlet_stop_flag(temp_db, patch_agent):
    from backend.domains.gauntlet import service

    run_id = service._create_run("Lange opdracht", "ref", "", 95, 10)
    stop_flag = {"stop": True}  # meteen stoppen bij de eerste ronde
    asyncio.run(service._run_gauntlet(
        run_id, "Lange opdracht", "ref", 95, 10, "", stop_flag))
    run = service.get_run(run_id)
    assert run["status"] == "stopped_by_user"


def test_gauntlet_human_verdict(temp_db, patch_agent):
    from backend.domains.gauntlet import service

    run_id = service._create_run("Korte opdracht", "ref", "", 999, 1)
    asyncio.run(service._run_gauntlet(
        run_id, "Korte opdracht", "ref", 999, 1, "", {}))
    run = service.get_run(run_id)
    assert run["status"] in ("stopped", "partial", "failed")  # threshold 999 haalt niets

    ok = service.record_verdict(run_id, "goedgekeurd", "Menselijke eindjurat: prima.")
    assert ok is True
    run = service.get_run(run_id)
    assert run["human_verdict"] == "goedgekeurd"
    assert run["human_note"] == "Menselijke eindjurat: prima."

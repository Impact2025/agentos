"""Zelfhelende conveyor: stale 'running' taken mogen nooit eeuwig hangen.

Bewijst dat:
  - _sweep_stale_running() oude 'running' taken -> 'todo' zet, verse intact laat
  - recover_orphans() (agentctl) de tasks-tabel meeneemt bij opstart
  - de FastAPI lifespan (main.py) recover_orphans() ook ECHT aanroept bij
    het opstarten van de server zelf — niet alleen bereikbaar via de
    handmatige POST /api/agentctl/recover-route. Dit is precies het gat dat
    de 26 zombies (oudste sinds 20 jul) liet liggen: het herstelmechanisme
    bestond, maar niets riep het bij een boot aan.

Draait tegen een tijdelijke DB (IMPACTOS_DB_PATH), raakt de live DB nooit.
De env-var moet GEZET zijn vóórdat shared.database zijn DB_PATH bindt, dus
doen we dat module-niveau vóór de eerste app-import.
"""
import os
import sys
import tempfile

# Frisse tijdelijke DB per test-run-sessie; shared.database leest DB_PATH bij
# import, dus deze env-set moet vóór `import backend.shared.database` komen.
# Repo-root op het pad zodat `backend` een top-level package blijft (de app
# draait als `backend.domains...`, niet als `domains...`).
_TMP = tempfile.mktemp(suffix=".db")
os.environ["IMPACTOS_DB_PATH"] = _TMP

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _ROOT + "/backend" not in sys.path:
    sys.path.insert(0, _ROOT + "/backend")

from backend.shared.database import init_db, get_conn  # noqa: E402


def _seed():
    # Alleen bindend als dit bestand écht de eerste importeur van
    # shared.database is in het hele testproces — in een volledige pytest-run
    # kan een andere testfile al eerder hebben geïmporteerd en dan wint diens
    # IMPACTOS_DB_PATH (het pad wordt eenmalig gebonden bij import). Verwijderen
    # van _TMP raakt dan een ongebruikt bestand terwijl get_conn()/init_db()
    # tegen de écht gebonden (persistente) DB draaien — met de rijen van de
    # vorige test er nog in, wat een UNIQUE-constraint-crash gaf. Daarom niet
    # op het bestand vertrouwen: expliciet de eigen testrijen opruimen i.p.v.
    # aannemen dat de hele DB leeg is.
    if os.path.exists(_TMP):
        try:
            os.remove(_TMP)
        except OSError:
            pass
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id IN ('z1','z2','ok1')")
    now_iso = "CURRENT_TIMESTAMP"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id,title,status,agent,position,workspace_path,"
            "created_at,updated_at,started_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("z1", "zombie oud", "running", "hermes", 0, "x/01.md",
             "2026-07-20T06:14:00+00:00", "2026-07-20T06:14:00+00:00",
             "2026-07-20T06:14:00+00:00"),
        )
        conn.execute(
            "INSERT INTO tasks (id,title,status,agent,position,workspace_path,"
            "created_at,updated_at,started_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("z2", "zombie 8 dagen", "running", "hermes", 0, "y/01.md",
             "2026-08-13T06:14:00+00:00", "2026-08-13T06:14:00+00:00",
             "2026-08-13T06:14:00+00:00"),
        )
        conn.execute(
            "INSERT INTO tasks (id,title,status,agent,position,workspace_path,"
            "created_at,updated_at,started_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("ok1", "verse taak", "running", "hermes", 0, "z/01.md",
             now_iso, now_iso, now_iso),
        )


def test_sweep_stale_running():
    _seed()
    from backend.domains.pipeline.conveyor import _sweep_stale_running, STALE_RUNNING_HOURS
    assert STALE_RUNNING_HOURS == 6
    swept = _sweep_stale_running(stale_hours=6)
    assert swept == 2, f"verwacht 2 geraakte zombies, kreeg {swept}"
    with get_conn() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
    assert rows["z1"]["status"] == "todo", "oude zombie moet naar todo"
    assert rows["z2"]["status"] == "todo", "8-dagen zombie moet naar todo"
    assert rows["ok1"]["status"] == "running", "verse taak blijft running"
    assert "Stale 'running' gereset" in (rows["z1"]["error"] or "")


def test_recover_orphans_covers_tasks():
    _seed()
    from backend.domains.agentctl.service import recover_orphans
    res = recover_orphans(stale_running_hours=6)
    assert res.get("tasks", 0) == 2, f"recover moet 2 taken resetten, kreeg {res}"
    with get_conn() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
    assert rows["z1"]["status"] == "todo"
    assert rows["ok1"]["status"] == "running"


def test_boot_lifespan_calls_recover_orphans():
    """De server-boot zelf moet de zombies opruimen, niet alleen een aanroepbare functie.

    Eerdere versie van deze fix voegde recover_orphans() toe aan agentctl/service.py
    maar vergat 'm daadwerkelijk in main.py:lifespan() aan te roepen — de 26 live
    zombies zouden dan bij een herstart gewoon blijven liggen. Deze test start de
    ECHTE FastAPI-app (lifespan incluis) tegen de tijdelijke DB en bewijst dat een
    vooraf geplante zombie na de boot naar 'todo' is gezet.
    """
    _seed()
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/healthcheck")
        assert r.status_code == 200

    with get_conn() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
    assert rows["z1"]["status"] == "todo", "boot-lifespan moet de oude zombie herstellen"
    assert rows["z2"]["status"] == "todo", "boot-lifespan moet de 8-dagen-zombie herstellen"
    assert rows["ok1"]["status"] == "running", "verse taak mag niet worden aangeraakt"

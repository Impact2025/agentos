"""Runtime smoke-test: draai de echte FastAPI-app in-process tegen een
tijdelijke DB en oefen de nieuwe autonomie-paden uit (auto-AEO selectie +
aeo-attack endpoint + conveyor quality) zonder de live server te raken.
"""
import os, tempfile, uuid, json

_tmp = os.path.join(tempfile.gettempdir(), f"impactos-smoke-{uuid.uuid4().hex[:8]}.db")
os.environ["IMPACTOS_DB_PATH"] = _tmp
os.environ["AEO_AUTO_ATTACK"] = "1"
os.environ["AEO_AUTO_MIN_SCORE"] = "75"
os.environ["AEO_AUTO_MAX_PER_SCAN"] = "3"

from backend.shared.database import init_db, get_conn
from backend.domains.radar.models import ensure_schema
from fastapi.testclient import TestClient
from backend.main import app

init_db()
ensure_schema()

client = TestClient(app)

# 1) stats endpoint antwoordt (app laadt met nieuwe code)
r = client.get("/api/radar/stats")
print("stats status:", r.status_code)
assert r.status_code == 200, r.text

# 2) stop een 'new' top-signaal in de DB en roep de auto-AEO-logica direct aan
from backend.domains.radar.service import RadarService
svc = RadarService()
sid = str(uuid.uuid4())
with get_conn() as c:
    c.execute(
        "INSERT INTO radar_signals "
        "(id, watch_id, project, keyword, title, url, source, snippet, "
        "published_days_ago, signal_score, ai_hook, ai_angle, ai_titles, "
        "ai_match_score, status, obsidian_path, scanned_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, str(uuid.uuid4()), "bijeen", "vrijwilligers werven",
         "test signaal hoog", "https://reddit.com/" + sid, "reddit", "snip",
         2, 90, "hook", "angle", json.dumps(["Titel A"]), 80, "new", "",
         "2026-07-08T00:00:00Z", "2026-07-08T00:00:00Z", "2026-07-08T00:00:00Z"),
    )

attacked = svc._auto_aeo_top_signals()
print("auto-AEO aanvallen:", attacked)
assert attacked, "verwacht minstens 1 auto-AEO aanval"

# 3) de aeo-attack endpoint moet 3 taken aanmaken in de conveyor
with get_conn() as c:
    n = len(c.execute("SELECT id FROM tasks WHERE workspace_path LIKE 'radar-aeo-%'").fetchall())
print("aangemaakte AEO-taken:", n)
assert n == 3, f"verwacht 3 taken, kreeg {n}"

# 4) conveyor quality-assessment bestaat en werkt
from backend.domains.pipeline.conveyor import _assess_output
assert _assess_output("# K\n\n## S\n" + "x" * 250, {"workspace_path": "x/listicle.md"})["ok"]
print("conveyor quality-assessment: OK")

# 5) fallback-template levert concept zonder crash
import asyncio
from backend.shared.agent_runner import _local_template_fill
ev = []
async def _c():
    async for e in _local_template_fill([{"role":"user","content":"# Smoke titel\n\nbeschrijving"}], "sys"):
        ev.append(e)
asyncio.run(_c())
assert any("Smoke titel" in (e.get("text","") or "") for e in ev)
print("lokale fallback: OK")

print("\n✅ SMOKE-TEST GESLAAGD — alle nieuwe autonomie-paden werken runtime")

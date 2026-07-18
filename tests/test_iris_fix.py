"""Tests voor Iris' actie-executor (fix.py) en de /api/iris/suggestions-endpoints."""
import json

import pytest


def _seed_site(conn, site_id="fixsite", name="Fixsite"):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, created_at) "
        "VALUES (?, ?, 'https://fix.nl', 'sc-domain:fix.nl', 1, 2, datetime('now'))",
        (site_id, name),
    )
    conn.commit()


@pytest.fixture()
def fix_clean(conn, clean_tables):
    from backend.domains.iris import fix as fix_mod
    from backend.shared.database import get_conn
    # Ook vóóraf leegmaken: de terugval-route van run_morning_briefing (elders
    # getest) legt tegenwoordig zelf fix-aanbiedingen klaar.
    with get_conn() as c:
        c.execute("DELETE FROM iris_suggestions")
    yield fix_mod
    with get_conn() as c:
        c.execute("DELETE FROM iris_suggestions")


def test_upsert_dedupes_per_report(fix_clean, conn):
    from backend.shared.database import get_conn
    _seed_site(conn)
    sugs = [
        {"type": "content_run", "title": "Schrijf 2 artikelen voor Fixsite",
         "target": "fixsite", "detail": "0 live pagina's", "priority": 1},
        {"type": "content_run", "title": "Schrijf 2 artikelen voor Fixsite",
         "target": "fixsite", "detail": "dup", "priority": 1},
        {"type": "onbekend", "title": "nope", "target": "x"},
    ]
    n = fix_clean.upsert_suggestions("2026-07-13", sugs)
    assert n == 1  # alleen de geldige, niet-gedupeerde
    with get_conn() as c:
        rows = c.execute("SELECT status FROM iris_suggestions").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_approve_then_apply_content_run(fix_clean, conn, monkeypatch):
    from backend.domains.iris import fix as fix_mod
    from backend.shared.database import get_conn
    _seed_site(conn)
    called = {}

    async def fake_apply(sid):
        # Volledig zelfstandige fake: schrijft de DB zonder de echte
        # GSC-agent aan te roepen (tests raken anders de live API).
        from backend.shared.database import get_conn
        with get_conn() as c:
            row = c.execute(
                "SELECT status FROM iris_suggestions WHERE id=?", (sid,)
            ).fetchone()
            if not row or row["status"] != "approved":
                return {"ok": False, "error": "Actie is nog niet goedgekeurd (status: pending)"}
            c.execute(
                "UPDATE iris_suggestions SET status='applied', applied_detail=?, "
                "applied_at='2026-07-13T06:50:00' WHERE id=?",
                ("Contentmotor gestart voor fixsite: 2 artikel(en) geschreven", sid),
            )
        return {"ok": True, "detail": "Contentmotor gestart voor fixsite: 2 artikel(en)",
                "type": "content_run", "target": "fixsite"}
    monkeypatch.setattr(fix_mod, "apply", fake_apply)
    sid = "sug-x"
    conn.execute(
        "INSERT INTO iris_suggestions (id, report_date, scope, type, title, target, "
        "payload, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, "2026-07-13", "all", "content_run",
         "Schrijf 2 artikelen voor Fixsite", "fixsite",
         json.dumps({"aantal": 2}), "pending", "2026-07-13T06:45:00"),
    )
    conn.commit()
    # Pending -> kan NIET direct applyen (veiligheidshek) via de echte executor.
    res = await fix_mod.apply(sid)
    assert res["ok"] is False and "nog niet goedgekeurd" in res["error"]
    assert fix_mod.approve(sid) is True
    res = await fix_mod.apply(sid)
    assert res["ok"] is True
    with get_conn() as c:
        st = c.execute("SELECT status FROM iris_suggestions WHERE id=?", (sid,)).fetchone()["status"]
    assert st == "applied"
    # Idempotent: tweede apply faalt (al applied).
    res2 = await fix_mod.apply(sid)
    assert res2["ok"] is False


@pytest.mark.asyncio
async def test_reject_sluit_actie(fix_clean, conn):
    from backend.domains.iris import fix as fix_mod
    from backend.shared.database import get_conn
    conn.execute(
        "INSERT INTO iris_suggestions (id, report_date, scope, type, title, target, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sug-r", "2026-07-13", "all", "gsc_connect",
         "Koppel GSC voor Fixsite", "fixsite", "pending", "2026-07-13T06:45:00"),
    )
    conn.commit()
    assert fix_mod.reject("sug-r") is True
    with get_conn() as c:
        st = c.execute("SELECT status FROM iris_suggestions WHERE id=?", ("sug-r",)).fetchone()["status"]
    assert st == "rejected"


@pytest.mark.asyncio
async def test_apply_gsc_connect_is_menselijke_stap(fix_clean, conn):
    from backend.domains.iris import fix as fix_mod
    from backend.shared.database import get_conn
    conn.execute(
        "INSERT INTO iris_suggestions (id, report_date, scope, type, title, target, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sug-g", "2026-07-13", "all", "gsc_connect",
         "Koppel GSC voor Fixsite", "sc-domain:fix.nl", "approved", "2026-07-13T06:45:00"),
    )
    conn.commit()
    res = await fix_mod.apply("sug-g")
    assert res["ok"] is True
    assert "GSC" in res["detail"]
    # GEEN agent aangezwengeld — alleen een kaart gelogd.
    with get_conn() as c:
        row = c.execute(
            "SELECT status, artifact FROM activity_log WHERE action='iris_actie' "
            "AND detail LIKE '%GSC%' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["status"] == "ok"


@pytest.mark.asyncio
async def test_apply_goal_draft_links_created_goal(fix_clean, conn, monkeypatch):
    """Een goal_draft-suggestie die toegepast wordt, maakt een concept-doel aan
    en koppelt dat doel terug via goal_id. Zo voorkomen we een dubbele,
    tegenstrijdige kaart op het dashboard (applied én draft voor dezelfde intentie)."""
    import backend.domains.iris.fix as fix_mod
    from backend.shared.database import get_conn

    async def fake_draft_goal(project, title, objective, reason):
        # Simuleer het aanmaken van een concept-doel (geen echte goal-service).
        gid = "goal-test-linked-001"
        with get_conn() as c:
            c.execute(
                "INSERT INTO goals (id, title, objective, project, status, "
                "created_at, updated_at) VALUES (?,?,?,?, 'draft', datetime('now'), datetime('now'))",
                (gid, f"[Iris] {title}", objective, project),
            )
        return {"detail": f"Concept-doel voorgesteld voor {project}: {title}.", "goal_id": gid}

    monkeypatch.setattr(
        "backend.domains.iris.service._apply_draft_goal", fake_draft_goal
    )

    conn.execute(
        "INSERT INTO iris_suggestions (id, report_date, scope, type, title, target, "
        "payload, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("sug-gd", "2026-07-13", "all", "goal_draft",
         "Doel: CTR 3% in 7 dagen", "Bewaard voor Jou",
         json.dumps({"doelstelling": "Bereik 3% CTR binnen 7 dagen."}),
         "pending", "2026-07-13T06:45:00"),
    )
    conn.commit()
    assert fix_mod.approve("sug-gd") is True
    res = await fix_mod.apply("sug-gd")
    assert res["ok"] is True
    with get_conn() as c:
        row = c.execute(
            "SELECT status, goal_id, applied_detail FROM iris_suggestions WHERE id=?",
            ("sug-gd",),
        ).fetchone()
    # De suggestie is 'applied' én het gekoppelde doel-id staat erop.
    assert row["status"] == "applied"
    assert row["goal_id"] == "goal-test-linked-001"
    assert "Concept-doel" in row["applied_detail"]
    # En het concept-doel bestaat écht (daarom toont het Actiecentrum de draft-card).
    with get_conn() as c:
        g = c.execute(
            "SELECT status FROM goals WHERE id=?", ("goal-test-linked-001",)
        ).fetchone()
    assert g is not None and g["status"] == "draft"


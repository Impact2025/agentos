"""Iris' gesloten leer-lus: voorspellen, afrekenen tegen echte cijfers,
lessen op bewijs wegen en foute lessen intrekken."""
import json
import uuid
from datetime import date, timedelta

import pytest


def _snap_project(project="X", site_id="s1", clicks=10, impressions=200,
                  position=9.0, live_30d=2):
    return {
        "project": project, "site_id": site_id, "score": 50, "grade": 5.0,
        "auto_content": True,
        "pillars": {
            "content": {"score": 15.0, "live_30d": live_30d, "stale_review": 0,
                        "needs_work": 0, "pending_review": 0},
            "seo": {"score": 10.0, "pages": 3, "note": ""},
            "uitvoering": {"score": 20.0}, "hygiene": {"score": 18.0},
        },
        "trend": {"site": {"last7": {"clicks": clicks, "impressions": impressions,
                                     "avg_position": position}},
                  "risers": [], "fallers": []},
    }


@pytest.fixture()
def pred_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        for t in ("iris_predictions", "iris_lessons", "iris_reports", "sites"):
            c.execute(f"DELETE FROM {t}")


def test_metric_value_extractie(pred_clean):
    from backend.domains.iris import predictions
    p = _snap_project(clicks=12, impressions=300, position=8.0, live_30d=4)
    assert predictions.metric_value(p, "clicks") == 12.0
    assert predictions.metric_value(p, "impressions") == 300.0
    assert predictions.metric_value(p, "position") == 8.0
    assert predictions.metric_value(p, "ctr") == 4.0
    assert predictions.metric_value(p, "live_content") == 4.0
    # Geen trend → clicks niet meetbaar, live_content nog wel.
    p2 = {"pillars": {"content": {"live_30d": 1}}, "trend": None}
    assert predictions.metric_value(p2, "clicks") is None
    assert predictions.metric_value(p2, "live_content") == 1.0


def test_create_prediction_valideert(pred_clean):
    from backend.domains.iris import predictions
    assert predictions.create_prediction(
        report_date="2026-07-09", project="X", site_id="s1", metric="onzin",
        direction="up", baseline=1.0, statement="x") is None
    assert predictions.create_prediction(
        report_date="2026-07-09", project="X", site_id="s1", metric="clicks",
        direction="zijwaarts", baseline=1.0, statement="x") is None
    assert predictions.create_prediction(
        report_date="2026-07-09", project="X", site_id="s1", metric="clicks",
        direction="up", baseline=None, statement="x") is None
    pid = predictions.create_prediction(
        report_date="2026-07-09", project="X", site_id="s1", metric="clicks",
        direction="up", baseline=10.0, statement="meer clicks", horizon_days=7)
    assert pid
    from backend.shared.database import get_conn
    with get_conn() as c:
        row = c.execute("SELECT due_date, status FROM iris_predictions WHERE id=?", (pid,)).fetchone()
    assert row["due_date"] == "2026-07-16"
    assert row["status"] == "open"


def test_judge_positie_lager_is_beter(pred_clean):
    from backend.domains.iris import predictions
    # 'up' = verbeteren = positie daalt. Van 20 naar 12 met richting up = correct.
    assert predictions._judge("position", "up", 20.0, None, 12.0)[0] == "correct"
    # Positie steeg (slechter) terwijl up gevraagd = wrong.
    assert predictions._judge("position", "up", 12.0, None, 20.0)[0] == "wrong"
    # Clicks omhoog gevraagd en gestegen = correct.
    assert predictions._judge("clicks", "up", 10.0, None, 25.0)[0] == "correct"
    # Nauwelijks bewogen = unclear.
    assert predictions._judge("clicks", "up", 10.0, None, 10.0)[0] == "unclear"
    # Met target: juiste kant maar doel niet gehaald = unclear.
    assert predictions._judge("clicks", "up", 10.0, 50.0, 25.0)[0] == "unclear"
    assert predictions._judge("clicks", "up", 10.0, 20.0, 25.0)[0] == "correct"


def test_lesson_confidence_en_intrekken(conn, pred_clean):
    from backend.domains.iris import predictions
    from backend.shared.database import get_conn
    now = "2026-07-09T00:00:00"
    lid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO iris_lessons (id, lesson, category, source, created_at, updated_at) "
        "VALUES (?, 'Test-les over batchgrootte', 'content', 'test', ?, ?)", (lid, now, now))
    conn.commit()
    # Drie foute voorspellingen achter elkaar → les wordt ingetrokken.
    for _ in range(3):
        predictions._update_lesson_confidence(lid, correct=False)
    with get_conn() as c:
        row = c.execute("SELECT predictions_made, predictions_correct, confidence, active "
                        "FROM iris_lessons WHERE id=?", (lid,)).fetchone()
    assert row["predictions_made"] == 3
    assert row["predictions_correct"] == 0
    assert row["confidence"] < 0.34
    assert row["active"] == 0


def test_evaluate_due_rekent_af_en_leert(conn, pred_clean):
    from backend.domains.iris import predictions
    from backend.shared.database import get_conn

    now = "2026-07-01T00:00:00"
    lid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO iris_lessons (id, lesson, category, source, created_at, updated_at) "
        "VALUES (?, 'Interne links tillen striking-distance pagina de top-10 in', 'seo', 'test', ?, ?)",
        (lid, now, now))
    # Eén voorspelling die al afgerekend mag worden (due in het verleden):
    # positie zou verbeteren (up). Baseline 18, we leveren straks 9 → correct.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO iris_predictions (id, report_date, project, site_id, metric, "
        "direction, baseline, target, horizon_days, due_date, lesson_id, statement, "
        "status, created_at) VALUES (?, '2026-06-24', 'X', 's1', 'position', 'up', "
        "18.0, NULL, 7, ?, ?, 'pos verbetert', 'open', ?)",
        (pid, yesterday, lid, now))
    conn.commit()

    projects = [_snap_project(project="X", site_id="s1", position=9.0)]
    result = predictions.evaluate_due(projects, today=date.today().isoformat())

    assert result["correct"] == 1
    assert result["accuracy"] == 100.0
    with get_conn() as c:
        pred = c.execute("SELECT status, outcome_value FROM iris_predictions WHERE id=?", (pid,)).fetchone()
        les = c.execute("SELECT predictions_made, predictions_correct, confidence FROM iris_lessons WHERE id=?", (lid,)).fetchone()
    assert pred["status"] == "correct"
    assert pred["outcome_value"] == 9.0
    assert les["predictions_made"] == 1 and les["predictions_correct"] == 1
    assert les["confidence"] > 0.5


def test_evaluate_due_niet_meetbaar_is_untested(conn, pred_clean):
    from backend.domains.iris import predictions
    now = "2026-07-01T00:00:00"
    pid = str(uuid.uuid4())
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO iris_predictions (id, report_date, project, site_id, metric, "
        "direction, baseline, target, horizon_days, due_date, lesson_id, statement, "
        "status, created_at) VALUES (?, '2026-06-24', 'X', 's1', 'clicks', 'up', "
        "10.0, NULL, 7, ?, '', 'meer clicks', 'open', ?)", (pid, yesterday, now))
    conn.commit()
    # Project zonder trend → clicks niet meetbaar → untested (geen straf).
    #
    # Sinds 27-07-2026 'untested' i.p.v. 'unclear'. Reden: 'unclear' hoort te
    # betekenen "wél gemeten, geen uitsluitsel" (nauwelijks bewogen, of de
    # juiste kant op zonder het doel te halen). Een metriek die niet te meten
    # was, zegt niets over Iris' trefzekerheid. Op één hoop gegooid stonden er
    # 12 uitkomsten als 'unclear' geboekt waarvan er 6 puur opruimwerk waren,
    # en dat laat de leerlus slechter lijken dan hij is.
    projects = [{"project": "X", "site_id": "s1",
                 "pillars": {"content": {"live_30d": 0}, "seo": {"pages": 0, "note": ""}},
                 "trend": None}]
    result = predictions.evaluate_due(projects, today=date.today().isoformat())
    assert result["untested"] == 1
    assert result["unclear"] == 0 and result["correct"] == 0 and result["wrong"] == 0


@pytest.mark.asyncio
async def test_volledige_lus_voorspelt_en_toetst(conn, pred_clean, monkeypatch):
    """End-to-end: dag 1 voorspelt, dag 2 rekent af — via run_morning_briefing."""
    from backend.domains.iris import service, metrics
    from backend.shared.database import get_conn

    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, created_at) "
        "VALUES ('s1', 'X', 'https://x.nl', 'sc-domain:x.nl', 1, 2, datetime('now'))")
    conn.commit()

    # Snapshot met een toetsbare trend forceren (clicks meetbaar).
    def fake_snapshot():
        return {"projects": [_snap_project(project="X", site_id="s1", clicks=10)],
                "global": {"errors_24h": 0, "delivered_24h": 0, "pending_review_total": 0,
                           "scheduler_failures": [], "funnel": {}, "inputs_7d": {}}}
    monkeypatch.setattr(metrics, "snapshot", fake_snapshot)

    day1 = json.dumps({
        "oordeel_per_project": {"X": "ok"}, "evaluatie_gisteren": None,
        "geleerd": [], "verbeteringen": [], "advies": [],
        "lessen": [{"les": "Meer interne links geeft meer clicks", "categorie": "seo"}],
        "voorspellingen": [{"project": "X", "metric": "clicks", "richting": "up",
                            "horizon_dagen": 7, "les": "Meer interne links geeft meer clicks",
                            "uitspraak": "clicks stijgen komende week"}],
    })

    async def llm_day1(system, prompt, max_tokens=3000):
        return day1
    monkeypatch.setattr(service, "_llm", llm_day1)

    r1 = await service.run_morning_briefing()
    assert r1["predicted"] == 1
    with get_conn() as c:
        p = c.execute("SELECT * FROM iris_predictions").fetchone()
        assert p["status"] == "open"
        assert p["baseline"] == 10.0  # baseline uit de échte snapshot, niet de LLM
        assert p["lesson_id"]  # gekoppeld aan de les
        # Forceer de horizon naar het verleden zodat dag 2 hem afrekent.
        c.execute("UPDATE iris_predictions SET due_date = date('now','-1 day')")

    # Dag 2: clicks gestegen naar 40 → voorspelling correct, les wint vertrouwen.
    def fake_snapshot2():
        return {"projects": [_snap_project(project="X", site_id="s1", clicks=40)],
                "global": {"errors_24h": 0, "delivered_24h": 0, "pending_review_total": 0,
                           "scheduler_failures": [], "funnel": {}, "inputs_7d": {}}}
    monkeypatch.setattr(metrics, "snapshot", fake_snapshot2)

    async def llm_day2(system, prompt, max_tokens=3000):
        # Bevestig dat de toetsing in de prompt zit.
        assert "voorspellingen" in prompt.lower()
        return json.dumps({"oordeel_per_project": {}, "geleerd": [], "verbeteringen": [],
                           "advies": [], "lessen": [], "voorspellingen": []})
    monkeypatch.setattr(service, "_llm", llm_day2)

    r2 = await service.run_morning_briefing()
    assert r2["validation"]["correct"] == 1
    assert r2["validation"]["accuracy"] == 100.0
    with get_conn() as c:
        les = c.execute("SELECT predictions_made, predictions_correct FROM iris_lessons").fetchone()
    assert les["predictions_made"] == 1 and les["predictions_correct"] == 1


def test_predictions_endpoint(conn, pred_clean):
    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)
    r = client.get("/api/iris/predictions")
    assert r.status_code == 200
    body = r.json()
    assert "track_record" in body and "open" in body

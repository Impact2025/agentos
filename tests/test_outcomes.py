"""Uitkomst-kaarten: log_outcome schrijft artifact/next_step/status weg."""


def test_log_outcome_schrijft_alle_velden(clean_tables):
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.service import outcome_feed

    oid = log_outcome(
        "TestProject", "task_done", "Testtaak afgerond",
        artifact="D:/vault/task-x.md",
        next_step="Bekijk het resultaat",
    )
    assert oid

    feed = outcome_feed(limit=100)
    row = next(r for r in feed if r["id"] == oid)
    assert row["project"] == "TestProject"
    assert row["artifact"] == "D:/vault/task-x.md"
    assert row["next_step"] == "Bekijk het resultaat"
    assert row["status"] == "ok"


def test_log_outcome_error_status(clean_tables):
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.service import outcome_feed

    oid = log_outcome("TestProject", "live-fout", "Publish gaf 401", status="error")
    row = next(r for r in outcome_feed(limit=5) if r["id"] == oid)
    assert row["status"] == "error"

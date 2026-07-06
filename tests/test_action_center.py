"""Actiecentrum: de inbox pikt alles op wat op een mens wacht."""


def _seed_goal(conn, goal_id="goal-test-1", status="draft", project="WeAreImpact"):
    conn.execute(
        "INSERT INTO goals (id, title, objective, status, project, created_at, updated_at) "
        "VALUES (?, 'Testdoel', 'Test', ?, ?, datetime('now'), datetime('now'))",
        (goal_id, status, project),
    )


def test_draft_goal_verschijnt_met_acties(conn, clean_tables):
    from backend.domains.action_center.service import build_inbox

    _seed_goal(conn, status="draft")
    conn.commit()

    inbox = build_inbox()
    drafts = [i for i in inbox["items"] if i["kind"] == "goal_draft"]
    assert len(drafts) == 1
    types = {a["type"] for a in drafts[0]["actions"]}
    assert "goal_confirm_start" in types
    assert "goal_delete" in types


def test_error_activiteit_wordt_inbox_item(conn, clean_tables):
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.service import build_inbox

    log_outcome("Bijeen", "live-fout", "publish-API gaf 401", status="error")
    inbox = build_inbox()
    errors = [i for i in inbox["items"] if i["kind"] == "error"]
    assert any("401" in e["summary"] for e in errors)
    assert inbox["counts"]["errors"] >= 1


def test_dismiss_verbergt_item(conn, clean_tables):
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.service import build_inbox, dismiss

    oid = log_outcome("Bijeen", "live-fout", "tijdelijke fout", status="error")
    assert any(i["id"] == oid for i in build_inbox()["items"])

    dismiss("error", oid)
    assert not any(i["id"] == oid for i in build_inbox()["items"])


def _seed_site(conn, site_id="site-x"):
    conn.execute(
        "INSERT OR IGNORE INTO sites (id, name, created_at) "
        "VALUES (?, 'TestSite', datetime('now'))",
        (site_id,),
    )


def test_pending_review_content_job(conn, clean_tables):
    from backend.domains.action_center.service import build_inbox

    _seed_site(conn)
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, keyword, status, seo_score, created_at) "
        "VALUES ('job-test-1', 'site-x', 'Testartikel', 'kw', 'pending_review', 88, datetime('now'))"
    )
    conn.commit()

    reviews = [i for i in build_inbox()["items"] if i["kind"] == "content_review"]
    assert len(reviews) == 1
    types = {a["type"] for a in reviews[0]["actions"]}
    assert {"content_approve", "content_reject"} <= types


def test_mislukte_publish_job_heeft_retry(conn, clean_tables):
    from backend.domains.action_center.service import build_inbox

    _seed_site(conn)
    conn.execute(
        "INSERT INTO content_jobs (id, site_id, title, keyword, status, error, created_at) "
        "VALUES ('job-test-2', 'site-x', 'Faalartikel', 'kw', 'error', '401 Unauthorized', datetime('now'))"
    )
    conn.commit()

    items = [i for i in build_inbox()["items"] if i["id"] == "job-test-2"]
    assert len(items) == 1
    assert items[0]["kind"] == "error"
    assert any(a["type"] == "content_approve" for a in items[0]["actions"])


def test_opgeloste_live_fout_verdwijnt_uit_inbox(conn, clean_tables):
    """Een live-fout gevolgd door een geslaagde 'live' van hetzelfde artikel
    is opgelost en hoort niet meer in de inbox."""
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.service import build_inbox

    fout_id = log_outcome("Bijeen", "live-fout",
                          "'Testartikel X': publish-API gaf 401", status="error")
    assert any(i["id"] == fout_id for i in build_inbox()["items"])

    log_outcome("Bijeen", "live", "'Testartikel X' LIVE op https://bijeen.app/blog/x",
                artifact="https://bijeen.app/blog/x")
    assert not any(i["id"] == fout_id for i in build_inbox()["items"])


def test_onopgeloste_live_fout_blijft_staan(conn, clean_tables):
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.service import build_inbox

    fout_id = log_outcome("Bijeen", "live-fout",
                          "'Ander artikel': publish-API gaf 401", status="error")
    # Een 'live' van een ANDER artikel lost deze fout niet op
    log_outcome("Bijeen", "live", "'Los artikel' LIVE op https://bijeen.app/blog/y")
    assert any(i["id"] == fout_id for i in build_inbox()["items"])

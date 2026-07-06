"""Ochtendrapport: digest bevat de juiste secties en tellingen."""


def test_digest_bevat_secties_en_kloppende_tellingen(conn, clean_tables):
    from backend.shared.outcomes import log_outcome
    from backend.domains.action_center.digest import build_digest

    log_outcome("Bijeen", "live", "'Artikel X' LIVE", artifact="https://bijeen.app/blog/x")
    log_outcome("WeAreImpact", "live-fout", "publish gaf 401", status="error")

    digest = build_digest()
    md = digest["markdown"]

    assert md.startswith("# Ochtendrapport")
    assert "Wacht op jou" in md or digest["counts"]["waiting"] == 0
    assert "Gisteren opgeleverd" in md
    assert digest["counts"]["errors"] >= 1
    # De artefact-link van het opgeleverde artikel staat in het rapport
    assert "https://bijeen.app/blog/x" in md


def test_digest_endpoint(clean_tables):
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    r = client.get("/api/action-center/digest")
    assert r.status_code == 200
    body = r.json()
    assert "markdown" in body and "counts" in body

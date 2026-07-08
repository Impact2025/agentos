"""Integratietest: JSON-LD uit de article body belandt in <head> bij render."""
import pytest


@pytest.fixture()
def site_row():
    # Minimale site-rij zoals build_site_files die indirect nodig heeft.
    return {
        "id": "s1",
        "name": "Bijeen",
        "base_url": "https://bijeen.app",
        "publish_api_url": "",
        "publish_api_key": "",
    }


def test_publish_moves_json_ld_to_head():
    from backend.domains.publish import service as pub
    from backend.shared.database import get_conn

    sid = "test-site-jsonld"
    # published_pages.site_id is FK naar sites.id — maak de site eerst aan.
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO sites (id, name, base_url, created_at) VALUES (?,?,?,?)",
            (sid, "Bijeen", "https://bijeen.app", "2026-07-08"),
        )
    # Maak een pagina met JSON-LD onderaan de body.
    html = (
        "<h1>Test artikel</h1><p>Intro die de zoekintentie beantwoordt.</p>"
        "<h2>Veelgestelde vragen</h2>"
        "<p><strong>Vraag?</strong> Antwoord.</p>"
        '<script type="application/ld+json">\n{"@context":"https://schema.org"}\n</script>'
    )
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO published_pages "
            "(site_id, slug, title, html, url, image_b64, infographic_b64, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, "test-artikel", "Test artikel", html, "", "", "", "2026-07-08", "2026-07-08"),
        )

    files = pub.build_site_files(sid, "Bijeen", base_url="https://bijeen.app")
    page = files["test-artikel/index.html"]

    head_idx = page.find("</head>")
    body_idx = page.find("<body>")
    json_ld_idx = page.find("application/ld+json")
    assert json_ld_idx != -1
    # JSON-LD moet vóór </head> staan (dus in head), niet in body.
    assert json_ld_idx < head_idx
    # En de body mag de ruwe <script>-tag niet meer dubbel bevatten.
    body_section = page[body_idx:]
    # (de body bevat hem niet meer; de head wel)
    assert body_section.count("application/ld+json") == 0

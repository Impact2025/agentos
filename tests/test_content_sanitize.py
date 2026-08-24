"""Rol-labels die als kop lekken: `_sanitize_blog_body` moet ze generiek vangen.

Aanleiding (18 aug 2026): op bijeen.app stonden "Inleiding-redacteur",
"Stap-1-schrijver" t/m "Stap-3-schrijver" en "Conclusie-schrijver" letterlijk
als <h2> live in een gepubliceerd artikel. De Gauntlet genereert de deeltaak-rol
dynamisch per run (het model kiest zelf een "korte specialist-rol"), maar
`_ROLE_LABEL_HTML_RE`/`_ROLE_LABEL_RE` matchten alleen een vaste lijst bekende
rolnamen — geen van deze combinaties zat erin, dus lekten ze door. De regex
matcht nu generiek op elk voorvoegsel gevolgd door een rol-woord.
"""

from backend.domains.publish import content_pipeline as cp


def test_dynamische_rolnamen_worden_gestript():
    html = (
        "<h2>Inleiding-redacteur</h2><p>Tekst.</p>"
        "<h2>Stap-1-schrijver</h2><p>Meer tekst.</p>"
        "<h2>Stap-2-schrijver</h2><p>Nog meer.</p>"
        "<h2>Stap-3-schrijver</h2><p>Zelfs meer.</p>"
        "<h2>Conclusie-schrijver</h2><p>Slot.</p>"
    )
    cleaned, n_removed = cp._sanitize_blog_body(html)
    assert n_removed == 5
    for label in ("Inleiding-redacteur", "Stap-1-schrijver", "Stap-2-schrijver",
                  "Stap-3-schrijver", "Conclusie-schrijver"):
        assert label not in cleaned


def test_echte_stap_kop_blijft_staan():
    html = "<h2>Stap 3: maak de cijfers betekenisvol</h2><p>Tekst.</p>"
    cleaned, n_removed = cp._sanitize_blog_body(html)
    assert n_removed == 0
    assert "Stap 3: maak de cijfers betekenisvol" in cleaned


def test_bekende_vaste_rolnamen_blijven_gevangen():
    html = "<h2>Content Redactie-schrijver</h2><p>Tekst.</p><h2>Eindredacteur</h2><p>Meer.</p>"
    cleaned, n_removed = cp._sanitize_blog_body(html)
    assert n_removed == 2
    assert "Content Redactie-schrijver" not in cleaned
    assert "Eindredacteur" not in cleaned

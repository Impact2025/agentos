"""Bewaking op de verbeterronde (`_optimize_article`).

Achtergrond (20 jul 2026): het systeemprompt van de optimalisatiestap was het
volledige SEO-Editor-profiel, dat eindigt met "ANTWOORD UITSLUITEND met één
JSON-object". Die instructie won het van de toegevoegde "lever HTML"-zin, dus
het model stuurde regelmatig een beoordeling terug — die werd als artikeltekst
weggeschreven en kostte in één ronde ~85% van de inhoud (8647 → 1353 tekens).
De enige controle was `len(out) > 50`. Andersom plakte het model soms een tweede
kopie van de staart eronder; de reviewer scoorde zo'n dubbel artikel gewoon 82.
"""

from backend.domains.publish import content_pipeline as cp

ARTIKEL = ("<h1>Kop</h1>" + "<h2>Deel een</h2><p>" + "Zin over het onderwerp. " * 40
           + "</p><h2>Deel twee</h2><p>" + "Nog een zin hierover. " * 40 + "</p>")


def test_review_json_wordt_niet_als_artikel_geaccepteerd():
    json_out = '{"score": 82, "verdict": "revise", "feedback": ["1. Doe dit.", "2. En dat."]}'
    assert not cp._looks_like_article(json_out, ARTIKEL)


def test_json_met_html_eromheen_wordt_ook_geweigerd():
    # Kwam voor: een <p> vooraf, daarna alsnog het beoordelingsobject.
    out = '<p>Hier is de beoordeling.</p> "score": 78, "feedback": ["punt"]' + "x" * 200
    assert not cp._looks_like_article(out, ARTIKEL)


def test_gehalveerd_artikel_wordt_geweigerd():
    kort = "<h2>Deel een</h2><p>" + "Zin over het onderwerp. " * 5 + "</p>"
    assert not cp._looks_like_article(kort, ARTIKEL)


def test_gedupliceerde_staart_wordt_geweigerd():
    dubbel = ARTIKEL + "<h2>Deel twee</h2><p>" + "Nog een zin hierover. " * 40 + "</p>"
    assert cp._duplicate_headings(dubbel) == 1
    assert not cp._looks_like_article(dubbel, ARTIKEL)


def test_reeds_bestaande_duplicatie_blokkeert_een_ronde_niet():
    # Anders kan een artikel dat de duplicatie al bevat nooit meer verbeterd
    # worden: elke ronde zou op de bestaande dubbeling worden afgerekend.
    dubbel = ARTIKEL + "<h2>Deel twee</h2><p>" + "Nog een zin hierover. " * 40 + "</p>"
    assert cp._looks_like_article(dubbel, dubbel)


def test_nette_herschrijving_wordt_geaccepteerd():
    beter = ARTIKEL.replace("Zin over het onderwerp.", "Scherpere zin over het onderwerp.")
    assert cp._looks_like_article(beter, ARTIKEL)


def test_duplicate_headings_telt_genormaliseerd():
    html = "<h2>Veelgestelde vragen</h2><h2>  veelgestelde   VRAGEN </h2><h2>Anders</h2>"
    assert cp._duplicate_headings(html) == 1

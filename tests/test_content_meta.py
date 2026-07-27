"""Meta-titel/-description: parsen én afleiden.

Achtergrond (jul 2026): de twee HTML-commentaar-regexes bevatten `\\s` in plaats
van `\\s` als regex-klasse (letterlijke backslash + 's'), waardoor ze nooit
matchten. Meta in commentaarvorm werd stil weggegooid en de publisher viel terug
op het afkappen van de eerste alinea — een description die met de titel begon en
midden in een woord eindigde. De SEO-reviewer trok daar elke verbeterronde
punten voor af, een aftrek die geen herschrijving van het artikel kon repareren.
"""

from backend.domains.publish import content_pipeline as cp


def test_meta_uit_commentaar_dubbelepunt():
    html = ('<p>Body.</p>\n<!-- Meta-titel: Mijn titel -->\n'
            '<!-- Meta-description: Mijn omschrijving. -->')
    cleaned, title, desc = cp._strip_meta_and_suggestions(html)
    assert title == "Mijn titel"
    assert desc == "Mijn omschrijving."
    assert "Meta-titel" not in cleaned


def test_meta_uit_commentaar_attribuutvorm():
    html = '<p>Body.</p><!-- META title="T" description="D" -->'
    cleaned, title, desc = cp._strip_meta_and_suggestions(html)
    assert (title, desc) == ("T", "D")
    assert "META" not in cleaned


def test_afgeleide_description_laat_de_kop_weg_en_past_binnen_155():
    # H1 in een wrapper-div: `_strip_duplicate_header` haakt alleen aan het begin
    # van de body aan en mist hem dan, waardoor de description met de titel begon.
    html = ('<div class="artikel"><h1>Vier trends in vrijwilligerswerk</h1>'
            '<p>' + "Flexibiliteit staat centraal. " * 20 + '</p></div>')
    desc = cp._derive_meta_desc(html)
    assert not desc.startswith("Vier trends")
    assert len(desc) <= 155
    # Woordgrens: nooit midden in een woord afkappen.
    assert desc.endswith("…") and not desc.rstrip("…").endswith(" ")
    assert "Flexibilitei…" not in desc


def test_byline_alinea_belandt_niet_in_de_description():
    html = ('<div class="artikel"><h1>Kop</h1>'
            '<p>Auteur: Redactie WeAreImpact | Publicatiedatum: 1 juni 2025</p>'
            '<p>Vrijwilligerswerk draait om flexibiliteit en korte impact.</p></div>')
    desc = cp._derive_meta_desc(html)
    assert desc.startswith("Vrijwilligerswerk draait")
    assert "Auteur" not in desc and "Publicatiedatum" not in desc


def test_korte_body_krijgt_geen_ellipsis():
    desc = cp._derive_meta_desc("<h1>Kop</h1><p>Kort en klaar.</p>")
    assert desc == "Kort en klaar."


def test_preview_meta_gebruikt_expliciet_blok_boven_afleiding():
    html = ('<div><h1>Kop</h1><p>Lopende tekst.</p></div>'
            '<!-- Meta-titel: Echte titel -->'
            '<!-- Meta-description: Echte description. -->')
    title, desc = cp._preview_meta(html)
    assert title == "Echte titel"
    assert desc == "Echte description."

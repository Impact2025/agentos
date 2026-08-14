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


# ── Meta-titel: één definitie voor reviewer én publisher ────────────────────
#
# 2 aug 2026: 47 van 103 artikelen droegen een meta-titel die op exact 60 tekens
# midden in een woord was afgekapt ('... Jouw teambeleving in de l'), waarvan 15
# al live. De harde `[:60]` stond op vier plekken: de review-preview én de drie
# publicatieroutes. Voor slugs was die les al geleerd (`slugify_title` knipt op
# een woordgrens); de meta-titel had de fix nooit gekregen.

def test_titel_wordt_op_een_woordgrens_geknipt():
    lang = "Bedrijfsuitje Hoofddorp Schiphol - Jouw teambeleving in de luchthavenregio"
    uit = cp.meta_title_for(lang)
    assert len(uit) <= 60
    assert not uit.endswith(" ")
    assert lang.startswith(uit), "de kop van de titel hoort ongewijzigd te blijven"
    # Het afgekapte woord mag niet half meekomen.
    assert not uit.endswith("de l")
    assert uit.split()[-1] in lang.split(), "laatste woord moet een heel woord zijn"


def test_instructie_echo_van_het_model_gaat_eraf():
    """Het model schrijft zijn eigen tekenaantal in de titel; dat ging live mee."""
    assert cp.meta_title_for("Zo val je op als interimmer (54 tekens)") == \
        "Zo val je op als interimmer"
    assert cp.meta_title_for("Stappenplan en voorbeeld (48 chars)") == \
        "Stappenplan en voorbeeld"


def test_html_entiteiten_horen_niet_in_een_title():
    assert "&amp;" not in cp.meta_title_for("Data, SEO &amp; analytics")
    assert cp.meta_title_for("Data, SEO &amp; analytics") == "Data, SEO & analytics"


def test_korte_titel_blijft_ongemoeid():
    assert cp.meta_title_for("Korte titel") == "Korte titel"


def test_een_woord_zonder_spaties_wordt_alsnog_ingekort():
    """Liever hard afgekapt dan een titel die de limiet overschrijdt."""
    uit = cp.meta_title_for("A" * 90)
    assert len(uit) == 60


def test_lege_invoer_geeft_lege_string():
    assert cp.meta_title_for("") == ""
    assert cp.meta_title_for(None) == ""


def test_preview_en_publisher_leveren_dezelfde_titel():
    """Wijken die af, dan beoordeelt de reviewer een titel die nooit bestaat —
    en is zijn aftrek per definitie onrepareerbaar."""
    body = ("<h1>Bedrijfsuitje Hoofddorp Schiphol - Jouw teambeleving in de "
            "luchthavenregio</h1><p>" + "tekst " * 40 + "</p>")
    preview_title, _ = cp._preview_meta(body)
    titel = cp._extract_title(body, fallback="")
    assert preview_title == cp.meta_title_for(titel)


# ── Markdown-Metadata-blok: mag NOOIT zichtbaar op de live pagina belanden ──
#
# aug 2026: de schrijver leverde soms een zichtbaar markdown-blokje
# (`**Metadata**` + bullet-list `- Focus keyword:` …). Alle HTML-gebaseerde
# strippers grepen náást dit formaat, waardoor het VOLLEDIG zichtbaar op
# bijeen.app stond (incl. een URL-slug die niet eens klopte met de echte URL).
def test_markdown_metadata_wordt_gestript_en_meta_hergebruikt():
    html = (
        "<h1>Sociale cohesie versterken met een evenement</h1>\n"
        "<p>De evenementen die ik heb zien slagen waren niet de grootste.</p>\n"
        "<p>**Metadata**</p>\n"
        "<p>- Focus keyword: sociale cohesie versterken met een evenement</p>\n"
        "<p>- URL-slug: /sociale-cohesie-evenement-aanpakken</p>\n"
        "<p>- Meta-title: Sociale cohesie versterken met een evenement: 6 bewijzen</p>\n"
        "<p>- Meta-description: 6 concrete aanpakken om sociale cohesie te versterken.</p>\n"
    )
    cleaned, title, desc = cp._strip_meta_and_suggestions(html)
    assert "Metadata" not in cleaned
    assert "Focus keyword" not in cleaned
    assert "URL-slug" not in cleaned
    # De bruikbare meta-waarden worden wél teruggegeven voor de head.
    assert title == "Sociale cohesie versterken met een evenement: 6 bewijzen"
    assert desc == "6 concrete aanpakken om sociale cohesie te versterken."
    # De echte artikeltekst blijft intact.
    assert "De evenementen die ik heb zien slagen" in cleaned


def test_markdown_metadata_zonder_meta_blijft_ongemoeid():
    html = "<h1>Kop</h1><p>Lopende tekst zonder metadata-blok.</p>"
    cleaned, title, desc = cp._strip_meta_and_suggestions(html)
    assert cleaned == html
    assert title == "" and desc == ""

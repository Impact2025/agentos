"""Tests voor de wereldklasse-SEO-enhancements (deterministisch, geen LLM)."""
import pytest


@pytest.fixture()
def site():
    return {
        "name": "Bijeen",
        "base_url": "https://bijeen.app",
        "author": "Vincent van Munster",
        "ctas": ["Plan een demo", "Neem contact op"],
    }


ARTICLE_HTML = """
<h1>Vrijwilligers werven tijdens een bijeenkomst</h1>
<p>De snelste manier om vrijwilligers te werven is tijdens een bestaande
bijeenkomst: je bereikt mensen die al betrokken zijn en de sfeer voelt.
Spreek ze persoonlijk aan en koppel een concrete taak, dan stijgt de kans
dat ze ja zeggen aanzienlijk. Een warme, persoonlijke vraag werkt beter
dan een algemene oproep aan het eind van een bijeenkomst. Begin klein en
concreet, zodat de drempel om mee te doen laag blijft voor nieuwe mensen.</p>
<h2>1. Begin met een persoonlijk verhaal</h2>
<p>Deel waarom vrijwilligerswerk voor jou waardevol is en welk verschil het
maakt voor de bezoekers van je bijeenkomst. Volgens <a href="https://www.scp.nl">onderzoek van het SCP</a>
is persoonlijke betrokkenheid de sterkste voorspeller van blijvend
vrijwilligerswerk. Een verhaal roept herkenning op en nodigt anderen uit
om hun eigen motivatie te delen tijdens de bijeenkomst.</p>
<h2>2. Koppel een concrete taak aan de vraag</h2>
<p>Vraag niet zomaar om hulp, maar benoem precies wat iemand kan doen: de
koffie verzorgen, nieuwe gasten welkom heten of meehelpen bij de opbouw.
Een scherpe taak maakt het voor de ander makkelijker om ja te zeggen en
voorkomt dat je verzoek in de lucht blijft hangen na de bijeenkomst.</p>
<h2>3. Volg binnen een week op</h2>
<p>Stuur een korte, persoonlijke mail of app naar de mensen die toezegden
te helpen. Bedank ze en plan direct een eerste moment in. Zo verandert een
goede intentie tijdens de bijeenkomst in daadwerkelijke inzet met impact
voor de hele groep. Wie eenmaal ervaren heeft hoe leuk het is, blijft vaak
jarenlang betrokken en trekt op zijn beurt weer nieuwe mensen aan.</p>
<h2>4. Maak het laagdrempelig met een proefperiode</h2>
<p>Stel voor dat iemand één keer meedoet zonder verplichtingen. Een proef
werkt beter dan een lang contract, omdat mensen bang zijn voor een zware
verplichting. Na die eerste positieve ervaring is de stap naar vaste
inzet veel kleiner en groeit het team van vrijwilligers vanzelf mee.</p>
<h2>5. Meet en vier de resultaten</h2>
<p>Houd bij hoeveel nieuwe vrijwilligers er via bijeenkomsten instromen en
deel die winst openlijk. Erkenning werkt aanstekelijk: wie ziet dat anderen
het doen, voelt zich uitgenodigd om ook een steentje bij te dragen aan
dezelfde gezellige en betrokken sfeer tijdens elke bijeenkomst.</p>
<p>Klaar om zelf aan de slag te gaan? <strong>Plan een demo</strong> of
<strong>neem contact op</strong> en ontdek hoe je vrijwilligers werft
tijdens elke bijeenkomst zonder extra kosten of ingewikkeld gedoe.</p>
<h2>Veelgestelde vragen</h2>
<p><strong>Wat kost vrijwilligers werven tijdens een event?</strong> Meestal
niets extra — je gebruikt de aanwezige mensen en ruimte, en een persoonlijke
vraag kost alleen een paar minuten van je tijd tijdens de bijeenkomst.</p>
<p><strong>Hoeveel tijd kost het om vrijwilligers te werven?</strong> Een korte
pitch van twee minuten per bijeenkomst is genoeg om nieuwe aanmeldingen te
krijgen, plus een klein beetje opvolgwerk in de dagen erna per vrijwilliger.</p>
"""


def test_json_ld_generates_article_and_faq(site):
    from backend.domains.seo.enhancements import generate_json_ld, extract_faq
    faq = extract_faq(ARTICLE_HTML)
    assert len(faq) == 2
    ld = generate_json_ld(site, "vrijwilligers werven", ARTICLE_HTML, faq=faq)
    assert "application/ld+json" in ld
    assert '"@type": "Article"' in ld
    assert '"@type": "FAQPage"' in ld
    assert '"name": "Bijeen"' in ld  # publisher


def test_direct_answer_extracts_first_para():
    from backend.domains.seo.enhancements import build_direct_answer
    da = build_direct_answer("vrijwilligers werven", ARTICLE_HTML)
    assert "bijeenkomst" in da
    assert len(da.split()) <= 70  # binnen AEO-limiet


def test_faq_extract_finds_questions():
    from backend.domains.seo.enhancements import extract_faq
    faq = extract_faq(ARTICLE_HTML)
    assert faq[0]["question"].endswith("?")
    assert "kost" in faq[0]["question"].lower()
    assert len(faq[0]["answer"]) > 15


def test_ee_at_guard_flags_unfounded_claim():
    from backend.domains.seo.enhancements import ee_at_guard
    # Claim 'volgens onderzoek' zonder enige <a>/url → issue.
    bad = "<h1>x</h1><p>Volgens onderzoek is dit waar. " + "woord " * 300 + "</p>"
    issues = ee_at_guard(bad, "x", {"ctas": []})
    assert any("onderzoek" in i for i in issues)


def test_ee_at_guard_passes_with_source():
    from backend.domains.seo.enhancements import ee_at_guard
    good = ('<h1>x</h1><p>Volgens <a href="https://scp.nl/r">onderzoek</a> is dit waar. '
            + "woord " * 300 + "</p>")
    issues = ee_at_guard(good, "x", {"ctas": []})
    assert not any("onderzoek" in i for i in issues)


def test_assess_seo_worldclass_scores_high_for_good_article(site):
    from backend.domains.seo.enhancements import assess_seo_worldclass
    a = assess_seo_worldclass(ARTICLE_HTML, "vrijwilligers werven", site)
    assert a["has_direct_answer"]
    assert a["faq_count"] == 2
    assert a["score"] >= 80  # goed artikel scoort wereldklasse


def test_assess_seo_worldclass_penalizes_thin_content():
    from backend.domains.seo.enhancements import assess_seo_worldclass
    a = assess_seo_worldclass("<h1>x</h1><p>kort.</p>", "x", {"ctas": []})
    assert a["score"] < 60
    assert not a["worldclass"]


def test_ee_at_guard_folds_diacritics_in_keyword():
    """Regressie 25 jul 2026: een GSC-zoekwoord zonder accenten
    ('jubileum cadeau ideeen') matchte niet op de correct gespelde tekst
    ('ideeën'), waarna de guard 'zoekwoord komt niet voor in de body' meldde.
    Die melding kost in de kwaliteitsgate 5 punten — precies het verschil
    tussen 78 en 83 voor een artikel dat het zoekwoord overal gebruikt."""
    from backend.domains.seo.enhancements import ee_at_guard
    html = ("<h1>Jubileum cadeau ideeën</h1><p>De mooiste jubileum cadeau "
            "ideeën zijn ervaringen. " + "woord " * 300 + "</p>")
    issues = ee_at_guard(html, "jubileum cadeau ideeen", {"ctas": []})
    assert not any("komt niet voor" in i for i in issues), issues


def test_json_ld_faqpage_is_top_level_entity(site):
    """FAQPage moet naast Article staan, niet erin genest.

    Genest als `Article.mainEntity` leest Google het niet als FAQPage: het
    rich result bleef uit terwijl de QC 'json_ld: ok' rapporteerde.
    """
    import json as _json
    import re as _re
    from backend.domains.seo.enhancements import generate_json_ld, extract_faq

    faq = extract_faq(ARTICLE_HTML)
    assert faq, "testartikel moet een FAQ bevatten"
    ld = generate_json_ld(site, "vrijwilligers werven", ARTICLE_HTML, faq=faq)
    data = _json.loads(_re.search(r">\s*(\{.*\})\s*<", ld, _re.DOTALL).group(1))

    types = [n.get("@type") for n in data["@graph"]]
    assert "Article" in types and "FAQPage" in types
    article = next(n for n in data["@graph"] if n["@type"] == "Article")
    assert "mainEntity" not in article  # niet meer genest
    faq_node = next(n for n in data["@graph"] if n["@type"] == "FAQPage")
    assert faq_node["mainEntity"][0]["acceptedAnswer"]["text"]


def test_validate_json_ld_accepts_graph_form(site):
    from backend.domains.seo.enhancements import (
        generate_json_ld, extract_faq, validate_json_ld,
    )

    ld = generate_json_ld(site, "vrijwilligers werven", ARTICLE_HTML,
                          faq=extract_faq(ARTICLE_HTML))
    r = validate_json_ld(ld)
    assert r["valid"] is True, r["errors"]
    assert r["has_faq"] is True


def test_extract_faq_accepts_answers_without_paragraph_tags():
    """FAQ-antwoorden zonder <p> tellen ook mee.

    De schrijvers leveren de FAQ soms als kale tekst onder de vraag-kop. Dat
    telde als 'geen FAQ', wat 5 punten kostte in de kwaliteitsgate: artikelen
    mét een prima FAQ bleven zo onder de publicatiegrens hangen.
    """
    from backend.domains.seo.enhancements import extract_faq

    html = (
        "<h2>Veelgestelde vragen</h2>\n"
        "<h3>Wat kost een GPS-avontuur?</h3>\n"
        "De prijs hangt af van het aantal deelnemers en de gekozen route.\n"
        "<h3>Hoeveel begeleiders heb ik nodig?</h3>\n"
        "Eén volwassene per vijf tot zes kinderen is voldoende.\n"
    )
    faq = extract_faq(html)
    assert len(faq) == 2
    assert faq[0]["question"] == "Wat kost een GPS-avontuur?"
    assert "aantal deelnemers" in faq[0]["answer"]
    assert "vijf tot zes kinderen" in faq[1]["answer"]

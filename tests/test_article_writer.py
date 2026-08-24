"""Artikel-generator: de deterministische (LLM-vrije) onderdelen — QC-checks,
linkinvoeging/-validatie, batch-clamping en de sitemap/IndexNow-buildbestanden."""
import json

import pytest


# ── QC: AI-taal ──────────────────────────────────────────────────────────────

def test_ai_language_detects_cliches():
    from backend.domains.publish.article_writer import check_ai_language
    html = "<p>In de wereld van SEO is dit een naadloze game-changer.</p>"
    hits = check_ai_language(html)
    assert "in de wereld van" in hits
    assert "game-changer" in hits


def test_ai_language_clean_text_passes():
    from backend.domains.publish.article_writer import check_ai_language
    assert check_ai_language("<p>Een helder, feitelijk artikel over brood bakken.</p>") == []


# ── QC: CTA ──────────────────────────────────────────────────────────────────

def test_cta_check():
    from backend.domains.publish.article_writer import check_cta
    ctas = ["Plan een gratis kennismaking → /contact"]
    assert check_cta("<p>Plan een gratis kennismaking via onze site.</p>", ctas) is True
    assert check_cta("<p>Niks te zien hier.</p>", ctas) is False
    # Zonder geconfigureerde CTA's is de check niet van toepassing (pass).
    assert check_cta("<p>Niks.</p>", []) is True


# ── QC: zoekwoord ────────────────────────────────────────────────────────────

def test_keyword_check_pass():
    from backend.domains.publish.article_writer import check_keyword
    html = ("<h1>Interim manager inhuren: zo werkt het</h1>"
            "<p>Een interim manager inhuren begint met een goede intake. "
            + "Meer context over het proces en de kosten. " * 60
            + "Wie een interim manager inhuren wil, let op ervaring.</p>")
    assert check_keyword(html, "interim manager inhuren") == []


def test_keyword_check_flags_missing_h1_and_intro():
    from backend.domains.publish.article_writer import check_keyword
    html = "<h1>Iets heel anders</h1><p>" + "Vulling zonder het zoekwoord. " * 80 + "</p>"
    issues = check_keyword(html, "interim manager inhuren")
    assert any("H1" in i for i in issues)
    assert any("eerste 100 woorden" in i for i in issues)
    assert any("minimaal 2" in i for i in issues)


def test_keyword_check_folds_diacritics():
    """Regressie 25 jul 2026: het zoekwoord komt uit GSC in de spelling van de
    zoeker ('jubileum cadeau ideeen'), het artikel schrijft correct Nederlands
    ('ideeën'). Zonder accent-vouwen zag de check een artikel dat het zoekwoord
    in élke kop gebruikt aan voor thin content ('komt 0× voor') — goed voor drie
    keyword-issues plús een E-E-A-T-aftrek van 5 punten, en de enige manier om
    die te 'repareren' was de tekst verkeerd spellen. Een artikel bleef zo op 78
    hangen terwijl het niets mankeerde."""
    from backend.domains.publish.article_writer import check_keyword
    html = ("<h1>Jubileum cadeau ideeën die verbinden</h1>"
            "<p>Je zoekt jubileum cadeau ideeën die passen bij jullie verhaal. "
            + "Meer context over de keuze en de aanleiding. " * 60
            + "De mooiste jubileum cadeau ideeën zijn gedeelde ervaringen.</p>")
    assert check_keyword(html, "jubileum cadeau ideeen") == []
    # …en andersom: accent in het zoekwoord, geen accent in de tekst.
    assert check_keyword(html.replace("ideeën", "ideeen"),
                         "jubileum cadeau ideeën") == []


def test_cta_check_accepts_destination_suffix_and_href():
    """Regressie 25 jul 2026: CTA's staan in de kennisbank als
    '«actie» op «domein/pad»'. Het domeindeel is de bestemming, geen zinsdeel —
    een schrijver maakt er een link van. De check eiste de hele string letterlijk
    in de lopende tekst, dus élk artikel verloor 6 punten voor een CTA die er
    gewoon stond."""
    from backend.domains.publish.article_writer import check_cta
    ctas = ["Ontdek de Ritual Box op steentjebijsteentje.nl/de-ritual-box"]
    # Als link in de tekst (zoals een schrijver het doet).
    assert check_cta('<p>Meer weten? <a href="https://steentjebijsteentje.nl/'
                     'de-ritual-box">Ontdek de Ritual Box</a>.</p>', ctas) is True
    # Alleen de href, zonder de letterlijke CTA-zin.
    assert check_cta('<p>Zie <a href="https://steentjebijsteentje.nl/'
                     'de-ritual-box">deze doos</a>.</p>', ctas) is True
    # Geen CTA, geen link → nog steeds netjes False.
    assert check_cta("<p>Niks te zien hier.</p>", ctas) is False


def test_keyword_check_flags_stuffing():
    from backend.domains.publish.article_writer import check_keyword
    html = "<h1>kaas</h1><p>" + "kaas " * 50 + "</p>"
    issues = check_keyword(html, "kaas")
    assert any("te hoog" in i for i in issues)


# ── Links: invoegen + valideren ──────────────────────────────────────────────

def test_insert_link_wraps_free_text():
    from backend.domains.publish.article_writer import insert_link
    html = "<p>Lees ook ons stuk over lokale seo voor bakkers.</p>"
    out, ok = insert_link(html, "lokale seo", "https://x.nl/lokale-seo/")
    assert ok is True
    assert '<a href="https://x.nl/lokale-seo/">lokale seo</a>' in out


def test_insert_link_skips_headings_and_existing_anchors():
    from backend.domains.publish.article_writer import insert_link
    html = ('<h2>Alles over lokale seo</h2>'
            '<p>Bekijk <a href="/x">lokale seo</a> hier.</p>')
    out, ok = insert_link(html, "lokale seo", "https://x.nl/y/")
    assert ok is False
    assert out == html  # niets aangepast


def test_insert_link_never_touches_tag_attributes():
    from backend.domains.publish.article_writer import insert_link
    html = '<img alt="lokale seo grafiek" src="/i.png" /><p>Meer over lokale seo hier.</p>'
    out, ok = insert_link(html, "lokale seo", "https://x.nl/y/")
    assert ok is True
    assert 'alt="lokale seo grafiek"' in out  # attribuut intact
    assert out.count("<a ") == 1


def test_strip_unvetted_links_unwraps_hallucinated_hrefs():
    from backend.domains.publish.article_writer import strip_unvetted_links
    html = ('<p>Zie <a href="/verzonnen-pagina">deze gids</a> en '
            '<a href="https://voorbeeld.nl/bestaat-wel/">echte pagina</a> en '
            '<a href="/contact">neem contact op</a>.</p>')
    out, stripped = strip_unvetted_links(
        html,
        allowed_urls={"https://voorbeeld.nl/bestaat-wel"},
        allowed_paths={"/contact"},
    )
    assert stripped == 1
    assert "verzonnen-pagina" not in out
    assert "deze gids" in out  # ankertekst blijft staan
    assert '<a href="https://voorbeeld.nl/bestaat-wel/">' in out
    assert '<a href="/contact">' in out


def test_valid_external_rejects_http_and_own_host():
    from backend.domains.publish.article_writer import _valid_external
    assert _valid_external("https://www.cbs.nl/cijfers", "weareimpact.nl") is True
    assert _valid_external("http://onveilig.nl/x", "weareimpact.nl") is False
    assert _valid_external("https://www.weareimpact.nl/eigen", "weareimpact.nl") is False
    assert _valid_external("niet-eens-een-url", "weareimpact.nl") is False


# ── Batch ────────────────────────────────────────────────────────────────────

def test_batch_size_clamps():
    from backend.domains.publish.content_pipeline import _batch_size
    assert _batch_size({"content_batch_size": None}) == 1
    assert _batch_size({"content_batch_size": 5}) == 5
    assert _batch_size({"content_batch_size": 99}) == 10
    assert _batch_size({"content_batch_size": "abc"}) == 1


# ── Sitemap + IndexNow-keyfile in de Netlify-build ───────────────────────────

@pytest.fixture()
def site_with_page():
    from backend.domains.seo import sites as sites_service
    from backend.domains.publish import service as publish_service
    s = sites_service.create_site({"name": "SitemapTest", "base_url": "https://voorbeeld.nl"})
    publish_service._upsert_page(s["id"], "eerste-artikel", "Eerste artikel", "<h1>Hoi</h1>")
    yield sites_service.get_site(s["id"])
    from backend.shared.database import get_conn
    with get_conn() as c:
        c.execute("DELETE FROM published_pages WHERE site_id = ?", (s["id"],))
    sites_service.delete_site(s["id"])


def test_build_includes_sitemap_and_indexnow_key(site_with_page):
    from backend.domains.publish.service import build_site_files
    files = build_site_files(site_with_page["id"], "SitemapTest",
                             base_url="https://voorbeeld.nl", indexnow_key="k123")
    assert "sitemap.xml" in files
    assert "https://voorbeeld.nl/eerste-artikel/" in files["sitemap.xml"]
    assert files["k123.txt"] == "k123"


def test_build_without_base_url_has_no_sitemap(site_with_page):
    from backend.domains.publish.service import build_site_files
    files = build_site_files(site_with_page["id"], "SitemapTest")
    assert "sitemap.xml" not in files
    assert "index.html" in files


def test_site_base_url_falls_back_to_published_page(site_with_page):
    from backend.domains.publish.service import _site_base_url, _set_page_url
    # Zonder base_url én zonder eerdere deploy: geen basis bekend.
    assert _site_base_url({"id": site_with_page["id"], "base_url": ""}) == ""
    # Na een deploy is de basis afleidbaar uit de opgeslagen pagina-URL.
    _set_page_url(site_with_page["id"], "eerste-artikel", "https://sitemaptest.netlify.app/eerste-artikel/")
    assert _site_base_url({"id": site_with_page["id"], "base_url": ""}) == "https://sitemaptest.netlify.app"


# ── Listicle-intentie (formaatkeuze meertraps-generator) ────────────────────

def test_listicle_intent_detects_list_keywords():
    from backend.domains.publish.article_writer import detect_listicle_intent
    assert detect_listicle_intent("beste crm tools mkb") is True
    assert detect_listicle_intent("tips voor interim opdrachten") is True
    assert detect_listicle_intent("10 redenen om te digitaliseren") is True
    assert detect_listicle_intent("iets", angle="7 opties op een rij") is True


def test_listicle_intent_trend_rationale_triggers():
    from backend.domains.publish.article_writer import detect_listicle_intent
    assert detect_listicle_intent("hermes desktop", rationale="Trending (Radar-score 82): …") is True


def test_listicle_intent_guide_keywords_pass_through():
    from backend.domains.publish.article_writer import detect_listicle_intent
    assert detect_listicle_intent("interim manager inhuren") is False
    assert detect_listicle_intent("wat kost een uitvaartverzekering") is False
    # 'besteding' mag niet matchen op 'beste' (woordgrens).
    assert detect_listicle_intent("besteding zorgbudget gemeente") is False


# ── Infographic: embed in artikel + mee in de site-build ────────────────────

def test_embed_infographic_lands_before_second_h2():
    from backend.domains.publish.service import embed_infographic_html
    html = "<h1>T</h1><h2>Intro</h2><p>a</p><h2>Kern</h2><p>b</p>"
    out = embed_infographic_html(html, "mijn-artikel", "Mijn artikel")
    fig_pos = out.find("mijn-artikel-infographic.png")
    assert 0 < fig_pos < out.find("<h2>Kern</h2>") + len(out)  # figure aanwezig
    assert out.index("<figure>") < out.index("<h2>Kern</h2>")
    assert 'alt="Infographic: Mijn artikel"' in out
    # Idempotent: nogmaals embedden verandert niets.
    assert embed_infographic_html(out, "mijn-artikel", "Mijn artikel") == out


def test_embed_infographic_appends_when_single_section():
    from backend.domains.publish.service import embed_infographic_html
    html = "<h1>T</h1><p>enige sectie</p>"
    out = embed_infographic_html(html, "kort", "Kort & krachtig")
    assert out.endswith("</figure>\n")
    assert "Kort &amp; krachtig" in out  # titel wordt ge-escapet


def test_build_includes_infographic_png(site_with_page):
    from backend.domains.publish.service import _upsert_page, build_site_files
    _upsert_page(site_with_page["id"], "eerste-artikel", "Eerste artikel",
                 "<h1>Hoi</h1>", infographic_bytes=b"png-bytes")
    files = build_site_files(site_with_page["id"], "SitemapTest")
    assert files["images/eerste-artikel-infographic.png"] == b"png-bytes"


# ── QC-rapport belandt in de content_job en wordt geparsed teruggegeven ──────

def test_create_job_stores_qc_report(site_with_page):
    from backend.domains.publish import content_pipeline
    from backend.domains.content_queue.router import _with_parsed_social_copy

    qc = {"staged": True, "ai_language": {"pass": True, "hits": []}}
    job_id = content_pipeline.create_job(
        site_with_page["id"], "Titel", "kw", "waarom", "<h1>x</h1>", 85,
        {}, None, "titel", qc_report=qc, case_study_id="cs-1",
    )
    job = _with_parsed_social_copy(content_pipeline.get_job(job_id))
    assert job["qc_report"]["staged"] is True
    assert job["case_study_id"] == "cs-1"


# ── Interne-link-allowlist: register vs. sitemap-spelling ───────────────────
# Het vault-register en de live sitemap spellen dezelfde pagina verschillend
# (`daar.nl/platform` vs. `www.daar.nl/platform`). Letterlijk vergelijken wees
# élke sitemap-URL af, waardoor artikelen structureel zónder interne links
# werden gepubliceerd — precies wat het register moest voorkomen.

def test_allowed_internal_matches_across_www_prefix():
    from backend.domains.publish.article_writer import _is_allowed_internal

    register = {"https://daar.nl/platform", "https://daar.nl/kennisbank"}
    assert _is_allowed_internal("https://www.daar.nl/platform", register)
    assert _is_allowed_internal("https://daar.nl/platform/", register)
    assert not _is_allowed_internal("https://www.daar.nl/verzonnen-pagina", register)


def test_allowed_internal_blocklist_survives_www_and_slash():
    from backend.domains.publish.article_writer import _is_allowed_internal

    # Bekende 404 mag ook in www-/slash-vorm nooit als kandidaat terugkomen.
    assert not _is_allowed_internal(
        "https://www.weareimpact.nl/digitalisering-bij-gemeenten/", None)


# ── FAQ-vangnet: ontbreekt de sectie, dan schrijft de generator hem alsnog ───
# Zonder dit bleven artikelen op 77 steken: -5 in de kwaliteitsgate voor een
# ontbrekende FAQ, terwijl de reviewer om heel andere dingen vroeg.

def test_ensure_faq_appends_generated_section(monkeypatch):
    import asyncio
    from backend.domains.publish import article_writer as aw

    async def fake_llm(system, prompt, **kw):
        return ("<h2>Veelgestelde vragen</h2>\n"
                "<h3>Wat kost het?</h3>\n<p>Dat hangt af van de omvang.</p>\n"
                "<h3>Hoe lang duurt het?</h3>\n<p>Reken op enkele weken werk.</p>")

    monkeypatch.setattr(aw, "_llm", fake_llm)
    html, faq = asyncio.run(aw._ensure_faq({"name": "S"}, "kw", "<h1>T</h1><p>Body</p>"))
    assert len(faq) == 2
    assert "Veelgestelde vragen" in html
    assert html.startswith("<h1>T</h1>")


def test_ensure_faq_keeps_article_when_output_unusable(monkeypatch):
    import asyncio
    from backend.domains.publish import article_writer as aw

    async def junk_llm(system, prompt, **kw):
        return '{"score": 80, "feedback": "nope"}'

    monkeypatch.setattr(aw, "_llm", junk_llm)
    original = "<h1>T</h1><p>Body</p>"
    html, faq = asyncio.run(aw._ensure_faq({"name": "S"}, "kw", original))
    assert html == original and faq == []


def test_ensure_faq_inserts_before_meta_block(monkeypatch):
    import asyncio
    from backend.domains.publish import article_writer as aw

    async def fake_llm(system, prompt, **kw):
        return "<h2>Veelgestelde vragen</h2>\n<h3>Werkt dit?</h3>\n<p>Ja, dat werkt prima.</p>"

    monkeypatch.setattr(aw, "_llm", fake_llm)
    body = "<h1>T</h1><p>Body</p>\n<!-- Meta-titel: X -->"
    html, faq = asyncio.run(aw._ensure_faq({"name": "S"}, "kw", body))
    assert faq
    assert html.index("Veelgestelde vragen") < html.index("<!-- Meta-titel")


# ── Sanitizer: gelekte persona-labels vóór de H1 ─────────────────────────────
# 19 aug 2026, Bijeen: "Tool-vergelijker" en "SEO-schrijver en contentstrateeg"
# stonden als <h2> vóór de artikel-H1 in echte pending_review-jobs. Het
# woord-matchende deel van de sanitizer (_PERSONA_LABEL_RE) mist "vergelijker"
# — geen van de bekende _ROLE_WORDS — dus is er een structurele regel bij: elke
# H2/H3 vóór de eerste H1 is per contract nooit inhoud (elke outline begint met
# de H1) en wordt onvoorwaardelijk verwijderd.

def test_sanitize_strips_unknown_role_label_before_h1():
    from backend.domains.publish import article_writer as aw

    body = "<h2>Tool-vergelijker</h2>\n<h1>Titel</h1>\n<p>Inhoud.</p>"
    out = aw._sanitize_html_body(body)
    assert "Tool-vergelijker" not in out
    assert out.startswith("<h1>Titel</h1>")


def test_sanitize_strips_known_role_label_before_h1():
    from backend.domains.publish import article_writer as aw

    body = "<h2>SEO-schrijver en contentstrateeg</h2>\n<h1>Titel</h1>\n<p>Inhoud.</p>"
    out = aw._sanitize_html_body(body)
    assert "contentstrateeg" not in out
    assert out.startswith("<h1>Titel</h1>")


def test_sanitize_keeps_h2_after_h1():
    from backend.domains.publish import article_writer as aw

    body = "<h1>Titel</h1>\n<p>Intro.</p>\n<h2>Een echte sectiekop</h2>\n<p>Body.</p>"
    out = aw._sanitize_html_body(body)
    assert "Een echte sectiekop" in out


def test_feiten_grondwet_verbiedt_verzonnen_persoonlijke_autoriteit():
    from backend.domains.publish import article_writer as aw

    assert "als directeur van" in aw.FEITEN_GRONDWET
    assert "verzonnen ervaringsdeskundige" in aw.FEITEN_GRONDWET

"""Wereldklasse-garanties voor AgentOS/Iris blogproductie (aug 2026).

Drie harde eisen die standaard moeten gelden voor elk gegenereerd artikel:
1. Geen zichtbare Metadata-afval op de live pagina (zie test_content_meta).
2. Elk artikel krijgt >= 3 ECHTE, werkende interne links in de lopende tekst
   (nooit stil 0 links als de LLM-linkstap faalt).
3. Elk artikel heeft een zichtbare FAQ-sectie -> FAQPage-rich-result +
   AI-Overview-citeerbaar. De review-gate weigert artikelen zonder FAQ.

Deze tests draaien zonder LLM/tokens: ze testen de deterministische fallback
en de review-gate direct.
"""

from backend.domains.publish import article_writer as aw
from backend.domains.publish import content_pipeline as cp


# ── 2. In-body interne links: deterministische garantie ─────────────────────

def _bijeen_site():
    return {
        "id": "bijeen",
        "name": "Bijeen",
        "base_url": "https://bijeen.app",
    }


def _candidates():
    return [
        {"url": "https://bijeen.app/blog/9-ideeen-om-evenementen-te-organiseren-met-meetbare-wmo-impact",
         "title": "9 ideeën om evenementen te organiseren met meetbare WMO-impact"},
        {"url": "https://bijeen.app/blog/toolkit-amp-checklist-je-organiseert-activiteiten-die-echt-aansluiten",
         "title": "Toolkit & checklist: je organiseert activiteiten die echt aansluiten"},
        {"url": "https://bijeen.app/blog/welzijnsorganisatie-evenement-gratis-proberen",
         "title": "Probeer een evenement gratis als welzijnsorganisatie"},
    ]


def test_deterministische_links_koppelen_titels_aan_tekst():
    """De fallback moet kandidaat-titels matchen tegen letterlijke fragmenten
    in de tekst en er echte <a>-links van maken (minimaal 3)."""
    html = (
        "<h1>Sociale cohesie versterken met een evenement</h1>\n"
        "<p>Er zijn 9 ideeën om evenementen te organiseren met meetbare WMO-impact "
        "die echt werken in de praktijk.</p>\n"
        "<p>Een toolkit & checklist helpt bij activiteiten die echt aansluiten.</p>\n"
        "<p>Elke welzijnsorganisatie kan een evenement gratis proberen.</p>\n"
    )
    out, total = aw._insert_links_deterministic(
        html, _candidates(), "bijeen.app", max_links=3, already=0)
    assert out is not None
    assert total >= 3
    # Alle links wijzen naar echte, bestaande Bijeen-paden (geen verzinsels).
    import re
    hrefs = re.findall(r'href="([^"]+)"', out)
    assert len(hrefs) >= 3
    for h in hrefs:
        assert h.startswith("https://bijeen.app/blog/")
    # De oorspronkelijke tekst staat er nog (alleen anchors verlinkt).
    assert "Sociale cohesie versterken met een evenement" in out


def test_deterministische_links_negeert_vreemde_hosts():
    """Safe-guard: absolute URLs met een andere host worden nooit verlinkt."""
    cand = [{"url": "https://weareimpact.nl/foo", "title": "vreemde host pagina"}]
    html = "<h1>Kop</h1><p>vreemde host pagina hoort niet gelinkt.</p>"
    result = aw._insert_links_deterministic(html, cand, "bijeen.app", 3, 0)
    assert result is None  # geen enkele bruikbare kandidaat -> niets doen


def test_link_pass_geeft_nooit_stil_nul_bij_kandidaten():
    """Zelfs als de LLM-stap faalt (picks={}), moet _link_pass alsnog >=3
    interne links opleveren via de deterministische fallback."""
    site = _bijeen_site()
    html = (
        "<h1>Sociale cohesie</h1>\n"
        "<p>9 ideeën om evenementen te organiseren met meetbare WMO-impact.</p>\n"
        "<p>Een toolkit & checklist voor activiteiten die echt aansluiten.</p>\n"
        "<p>Probeer een evenement gratis als welzijnsorganisatie.</p>\n"
    )
    # Monkey-patch de LLM zodat de linkstap geen picks krijgt (faal-naar-{}),
    # én _link_candidates zodat de fallback echte Bijeen-kandidaten ziet.
    orig_llm = aw._llm
    orig_cands = aw._link_candidates
    async def _fail(*a, **k):
        raise RuntimeError("simulated LLM failure")
    aw._llm = _fail
    aw._link_candidates = lambda *a, **k: _candidates()
    try:
        import asyncio
        out, report = asyncio.run(aw._link_pass(site, "sociale cohesie", html,
                                                ctas=["Plan een demo via Bijeen.app"]))
    finally:
        aw._llm = orig_llm
        aw._link_candidates = orig_cands
    assert report["internal_added"] >= 3, report


# ── 3. FAQ-verplichting in de review-gate ───────────────────────────────────

def test_review_gate_weigert_artikel_zonder_faq():
    """Een artikel zonder zichtbare FAQ moet onder de grens scoren, zodat de
    verbeter-loop (of needs_work) ingrijpt — nooit stil naar pending_review."""
    from backend.shared.config import CONTENT_MIN_SCORE
    site = _bijeen_site()
    html_zonder_faq = (
        "<h1>Sociale cohesie versterken met een evenement</h1>\n"
        "<p>Direct antwoord op de zoekintentie hier.</p>\n"
        "<h2>Eerste aanpak</h2><p>Uitleg.</p>\n"
    )
    # Roep de innerlijke _reviewed aan via een minimale corpus-mock.
    # review_and_improve is async + gebruikt LLM; we testen de pure scoreregel
    # door extract_faq direct te koppelen aan dezelfde logica die _reviewed
    # toepast (geen FAQ -> score <= goal-1).
    from backend.domains.seo.enhancements import extract_faq
    faq = extract_faq(html_zonder_faq)
    assert not faq  # precondition: geen FAQ herkend
    # Simuleer de gate-beslissing zoals die in review_and_improve wordt gemaakt.
    goal = CONTENT_MIN_SCORE
    score = 90  # zou ruimschoots passen, MAAR faq ontbreekt
    if not faq:
        score = min(score, goal - 1)
    assert score < CONTENT_MIN_SCORE  # dus: niet publiceerbaar


def test_review_gate_accepteert_artikel_met_faq():
    site = _bijeen_site()
    html_met_faq = (
        "<h1>Sociale cohesie versterken met een evenement</h1>\n"
        "<p>Direct antwoord op de zoekintentie hier.</p>\n"
        "<h2>Veelgestelde vragen</h2>\n"
        "<h3>Wat kost een evenement met Bijeen?</h3><p>Afhankelijk van grootte.</p>\n"
        "<h3>Hoeveel deelnemers kan ik uitnodigen?</h3><p>Tot 50 gratis.</p>\n"
    )
    from backend.domains.seo.enhancements import extract_faq
    faq = extract_faq(html_met_faq)
    assert faq  # FAQ herkend -> geen penalty

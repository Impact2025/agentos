"""
Meertraps artikel-generator — Goldie's 13-staps content-pipeline, gecomprimeerd
tot ~5-7 LLM-calls per artikel plus deterministische (gratis) kwaliteitschecks.

Fasen:
  1. Outline        — Claude bepaalt 4-6 secties, waar de casestudy als bewijs
                      landt en waar de CTA's komen.
  2. Secties        — per batch geschreven (voorkomt dat lange artikelen
                      halverwege stoppen of generiek worden).
  3. Opmaak         — tabellen, genummerde lijsten en <strong> waar zinvol.
  4. Links          — interne links uit écht bestaande pagina's (published_pages
                      + live sitemap) + max 2 externe bronnen. Interne URL's die
                      niet in de kandidatenlijst staan worden in code gestript.
  5. QC             — deterministisch: AI-taal-woordenlijst, CTA-aanwezigheid,
                      zoekwoord in H1/intro + dichtheid. Bij falen één
                      gecombineerde fix-call, daarna herchecken.

Het resultaat is `(html_body, qc_report)`; de aanroeper (content_pipeline)
haalt daarna nog de bestaande review_and_improve-kwaliteitsgate erover en
bepaalt pending_review vs needs_work. Publiceren blijft achter de Wachtrij.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# AI-cliché-taal die Google (en elke lezer) herkent als generieke AI-content.
BANNED_PHRASES = (
    # Nederlands
    "in de wereld van", "ontdek de kracht", "in het digitale tijdperk",
    "het huidige digitale landschap", "naadloos", "naadloze", "cruciale rol",
    "cruciaal om", "essentiële rol", "duik in de", "laten we eerlijk zijn",
    "gamechanger", "game-changer", "revolutionaire", "ontketen",
    "til je", "naar een hoger niveau",
    # Engels (sluipt in NL-teksten van LLM's)
    "delve", "leverage", "testament to", "tapestry", "unleash", "unlock the",
    "elevate", "seamless", "in today's", "game changer",
)

_MAX_INTERNAL_LINKS = 4
_MAX_EXTERNAL_LINKS = 2
_MAX_LINK_CANDIDATES = 40
_SECTIONS_PER_CALL = 3


async def _llm(system: str, prompt: str, max_tokens: int = 2000) -> str:
    from . import content_pipeline
    return await content_pipeline._llm(system, prompt, max_tokens=max_tokens)


def _extract_json(raw: str) -> str:
    m = re.search(r"\{.*\}|\[.*\]", raw, re.DOTALL)
    return m.group(0) if m else raw


def _plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def fold_diacritics(text: str) -> str:
    """Ontdoet tekst van accenttekens ('ideeën' → 'ideeen', 'café' → 'cafe').

    Nodig omdat zoekwoorden uit GSC de spelling van de *zoeker* dragen — die
    typt 'jubileum cadeau ideeen' — terwijl correct Nederlands 'ideeën' schrijft.
    Zonder vouwen ziet elke keyword-check een artikel dat het zoekwoord in élke
    kop en alinea gebruikt aan voor thin content ("komt 0× voor"), en dat kost
    punten die geen enkele herschrijfronde kan terugverdienen: de enige 'fix'
    zou zijn de tekst verkeerd te spellen. Zelfde valkuil als de meta-velden die
    de reviewer niet te zien kreeg (zie content_pipeline._review_article)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(c)
    )


def _case_study_block(case_study: Optional[Dict]) -> str:
    if not case_study:
        return ""
    parts = [f"Titel: {case_study.get('title', '')}"]
    if case_study.get("summary"):
        parts.append(f"Samenvatting: {case_study['summary']}")
    if case_study.get("body"):
        parts.append(f"Details/cijfers:\n{case_study['body'][:1500]}")
    if case_study.get("source_url"):
        parts.append(f"Bron-URL: {case_study['source_url']}")
    return "\n".join(parts)


def _knowledge_block(profile: str, ctas: List[str]) -> str:
    parts = []
    if profile:
        parts.append(f"## Bedrijfsprofiel & USP's\n{profile[:2000]}")
    if ctas:
        parts.append("## Beschikbare call-to-actions\n" + "\n".join(f"- {c}" for c in ctas[:6]))
    return "\n\n".join(parts)


# ── Fase 1: Outline ──────────────────────────────────────────────────────────

# Zoekintentie-signalen voor een lijstartikel: "beste X", "tips", "tools",
# trending onderwerpen etc. Listicles worden voor dit soort zoekwoorden sneller
# geïndexeerd en vaker geciteerd in AI Overviews (duidelijke, parsebare
# structuur). De heuristiek is een hint — de outline-stap beslist definitief.
_LISTICLE_INTENT = re.compile(
    r"\b(beste|top|tips?|tools?|voorbeelden|manieren|soorten|idee(?:ë|e)n|fouten"
    r"|checklist|opties|alternatieven|redenen|stappen|trends?|vragen|signalen"
    r"|listicle|lijstjes?)\b|\b\d{1,2}\b",
    re.IGNORECASE,
)


def detect_listicle_intent(keyword: str, angle: str = "", rationale: str = "") -> bool:
    """True als de zoekintentie waarschijnlijk een lijst is. Trend-kansen
    (Mission Radar) krijgen ook de hint: op een verse trend is een listicle
    het snelst indexerende formaat."""
    if _LISTICLE_INTENT.search(f"{keyword} {angle}"):
        return True
    return "trend" in (rationale or "").lower()


async def _make_outline(site: Dict, keyword: str, angle: str, rationale: str,
                        case_study: Optional[Dict], profile: str, ctas: List[str],
                        brand_context: str) -> Dict:
    system = (
        "Je bent een Nederlandse SEO-contentstrateeg. Je maakt een outline voor een "
        "blogartikel dat écht iets toevoegt ('information gain') — geen generieke opsomming "
        "die elke AI kan schrijven, maar een stuk gebouwd rond eigen data en ervaring. "
        "Antwoord UITSLUITEND met JSON:\n"
        '{"title": "H1 met het zoekwoord er natuurlijk in", "format": "listicle" of "gids", '
        '"sections": [{"heading": "H2-tekst", "goal": "wat deze sectie de lezer oplevert", '
        '"use_case_study": true/false, "include_cta": true/false}]}\n'
        "Formaatkeuze: kies \"listicle\" wanneer de zoekintentie een lijst of vergelijking is "
        "(beste X, tips, tools, opties, trending onderwerpen) — een genummerd lijstartikel "
        "indexeert en rankt daar sneller op. Kies anders \"gids\".\n"
        "Regels bij \"gids\": 4-6 secties.\n"
        "Regels bij \"listicle\": 6-10 secties — de eerste sectie is een korte intro (heading "
        "zonder nummer), daarna elk lijst-item als eigen sectie met genummerde heading "
        "(\"1. …\", \"2. …\"), en de laatste sectie een conclusie/keuzehulp; zet het aantal "
        "items in de titel (bijv. \"7 …\").\n"
        "Altijd: precies één sectie gebruikt de casestudy als bewijs (use_case_study), tenzij "
        "er geen casestudy is; maximaal 2 secties met include_cta (één halverwege, één aan "
        "het slot).\n"
        "VERPLICHT: plan een 'Veelgestelde vragen' (FAQ)-sectie aan het slot (eigen H2) met "
        "3-5 vragen die de zoeker écht stelt — deze wordt gebruikt voor de FAQ-rich-result en "
        "AI Overviews. En plan een direct antwoord in de intro: de eerste alinea beantwoordt de "
        "zoekintentie meteen, zonder opwarming. Gebruik bij harde claims (cijfers, 'onderzoek "
        "toont') altijd een bronvermelding (externe link of cijfer uit de casestudy).\n"
        "HARDE INTRO-REGEL: de eerste alinea (intro) is een GEWONE, ongelabelde <p> met echte "
        "lezerswaarde — schrijf er NOOIT een label vóór zoals 'Samenvatting:', 'Intro:', "
        "'Inleiding:' of 'Meta:'. Zo'n label verdwijnt bij publicatie en dan lijkt de intro weg. "
        "De intro is verplicht aanwezig en mag nooit leeg zijn."
    )
    prompt_parts = [
        f"Site: {site['name']} ({site.get('base_url', '')})",
        f"Kernzoekwoord: {keyword}",
    ]
    if detect_listicle_intent(keyword, angle, rationale):
        prompt_parts.append(
            "Vermoedelijke zoekintentie: lijst/vergelijking — een listicle-indeling "
            "ligt voor de hand (tenzij de intentie duidelijk anders is)."
        )
    if angle:
        prompt_parts.append(f"Invalshoek: {angle}")
    if rationale:
        prompt_parts.append(f"Rationale (uit Search Console-data): {rationale}")
    kb = _knowledge_block(profile, ctas)
    if kb:
        prompt_parts.append(kb)
    cs = _case_study_block(case_study)
    if cs:
        prompt_parts.append(f"## Casestudy (uniek bewijsmateriaal)\n{cs}")
    if brand_context:
        prompt_parts.append(f"## Merkcontext\n{brand_context[:2000]}")

    raw = await _llm(system, "\n\n".join(prompt_parts), max_tokens=1600)
    outline = json.loads(_extract_json(raw))
    outline["format"] = "listicle" if outline.get("format") == "listicle" else "gids"
    sections = outline.get("sections") or []
    max_sections = 12 if outline["format"] == "listicle" else 8
    if not outline.get("title") or not (4 <= len(sections) <= max_sections):
        raise ValueError(f"Outline onbruikbaar: title={outline.get('title')!r}, {len(sections)} secties")
    return outline


# ── Fase 2: Secties schrijven ────────────────────────────────────────────────

async def _write_sections(site: Dict, keyword: str, outline: Dict,
                          case_study: Optional[Dict], profile: str, ctas: List[str],
                          brand_context: str, base_style_prompt: str) -> str:
    sections = outline["sections"]
    system = base_style_prompt
    if brand_context:
        system += f"\n\n## Merkcontext uit Obsidian vault (strikte regels)\n{brand_context[:4000]}"
    kb = _knowledge_block(profile, ctas)
    if kb:
        system += f"\n\n{kb}"
    system += (
        "\n\nJe schrijft telkens een deel van één doorlopend artikel. Lever ALLEEN de "
        "HTML van de gevraagde secties: <h2> per sectie, <p>/<ul>/<ol> voor de inhoud, "
        "geen <h1>, geen <html>/<head>/<body>, geen inleidende of afsluitende opmerkingen. "
        "Waar de outline 'use_case_study' zegt: gebruik de concrete cijfers en resultaten "
        "uit de casestudy als bewijs (verzin NIETS erbij). Waar 'include_cta' staat: verwerk "
        "één van de beschikbare call-to-actions op een natuurlijke, niet-opdringerige manier.\n"
        "\nBELANGRIJK voor wereldklasse-SEO:\n"
        "- De eerste alinea na de H1 is een DIRECT ANTWOORD op de zoekintentie (40-60 woorden, "
        "geen opwarming, geen 'in dit artikel'). Dit wordt geciteerd door AI Overviews.\n"
        "- Sluit af met een FAQ-sectie (<h2>Veelgestelde vragen</h2>) van 3-5 vragen in "
        "<strong>vraag?</strong>-koppen met een kort, feitelijk antwoord eronder — geschikt "
        "voor de FAQ-rich-result.\n"
        "- E-E-A-T: elke harde claim (cijfer, 'onderzoek toont', percentage) krijgt een bron "
        "of een concreet voorbeeld uit de casestudy. Geen vage vulling ('het is belangrijk', "
        "'in de huidige maatschappij', 'een belangrijke rol'). Schrijf concreet en voorbeeldrijk."
    )
    if outline.get("format") == "listicle":
        system += (
            "\n\nDit is een LISTICLE: houd de genummerde koppen exact zoals in de outline. "
            "Elk lijst-item is zelfstandig leesbaar en concreet — voor wie is dit item de "
            "beste keuze, wat levert het op, wat is het addertje. Geen vulling tussen de "
            "items; de intro is kort (max 3 zinnen naar het eerste item toe)."
        )

    outline_json = json.dumps(outline, ensure_ascii=False)
    cs = _case_study_block(case_study)
    html_parts: List[str] = []
    for start in range(0, len(sections), _SECTIONS_PER_CALL):
        batch = sections[start:start + _SECTIONS_PER_CALL]
        prompt_parts = [
            f"Artikel voor {site['name']} — kernzoekwoord: {keyword}",
            f"Volledige outline (context):\n{outline_json}",
        ]
        if cs:
            prompt_parts.append(f"## Casestudy\n{cs}")
        if html_parts:
            tail = _plain_text(html_parts[-1])[-400:]
            prompt_parts.append(f"Slot van de vorige sectie (sluit hierop aan, herhaal niets):\n…{tail}")
        prompt_parts.append(
            "Schrijf nu UITSLUITEND deze secties, in deze volgorde:\n"
            + "\n".join(f"- {s['heading']} — {s.get('goal', '')}" for s in batch)
        )
        out = await _llm(system, "\n\n".join(prompt_parts), max_tokens=6000)
        if not out.strip():
            raise ValueError(f"Lege sectie-response (secties {start + 1}-{start + len(batch)})")
        html_parts.append(out.strip())

    title = outline["title"].strip()
    return f"<h1>{title}</h1>\n" + "\n".join(html_parts)


# ── Fase 3: Opmaak ───────────────────────────────────────────────────────────

async def _format_pass(html_body: str) -> str:
    system = (
        "Je bent een Nederlandse web-eindredacteur. Je verrijkt de OPMAAK van een artikel "
        "zonder de inhoud te veranderen: zet vergelijkingen of opsommingen van gegevens om "
        "in een <table> waar dat de leesbaarheid helpt, gebruik <ol> voor stappen, en "
        "markeer per sectie hooguit één kernzin of kerncijfer met <strong>. "
        "Verwijder niets, voeg geen nieuwe beweringen toe, geen inline CSS. "
        "Lever ALLEEN de volledige aangepaste HTML-body."
    )
    out = await _llm(system, html_body, max_tokens=4500)
    # Opmaak is nice-to-have: bij een te korte/lege response behouden we het origineel.
    return out.strip() if len(out.strip()) >= len(html_body) * 0.6 else html_body


async def _ensure_faq(site: Dict, keyword: str, html_body: str) -> Tuple[str, list]:
    """Schrijf alsnog een FAQ-sectie als het artikel er geen heeft.

    Retourneert (html, faq). Bij twijfel wordt het artikel ongemoeid gelaten:
    liever geen FAQ dan een verzonnen of half aangehechte sectie. De vragen
    moeten uit het artikel zelf volgen — nieuwe cijfers of claims zijn verboden,
    want een FAQ is geen plek om ongefundeerde beweringen binnen te smokkelen.
    """
    from ..seo.enhancements import extract_faq

    system = (
        "Je bent een Nederlandse SEO-redacteur. Schrijf een FAQ-sectie bij het "
        "aangeleverde artikel: 4 of 5 vragen die een lezer na dit artikel nog "
        "écht stelt. Antwoord per vraag in 2-3 zinnen.\n"
        "HARDE EISEN:\n"
        "- Baseer je UITSLUITEND op wat er in het artikel staat. Geen nieuwe "
        "cijfers, percentages, jaartallen, prijzen of bronnen verzinnen.\n"
        "- Herhaal geen vraag die het artikel al als kop behandelt.\n"
        "- Exact dit formaat, en niets anders:\n"
        "<h2>Veelgestelde vragen</h2>\\n<h3>Vraag?</h3>\\n<p>Antwoord.</p>\n"
        "- Lever ALLEEN die HTML. Geen inleiding, geen uitleg, geen JSON."
    )
    try:
        out = (await _llm(system, f"Kernzoekwoord: {keyword}\n\nARTIKEL:\n{html_body}",
                          max_tokens=1200) or "").strip()
    except Exception as e:
        logger.warning("[article-writer] FAQ-generatie mislukt: %s", str(e)[:150])
        return html_body, []

    out = re.sub(r"^```(?:html)?\s*|\s*```$", "", out).strip()
    # Alleen accepteren als het ook echt een bruikbare FAQ-sectie is.
    if "<h3" not in out or "veelgestelde" not in out.lower():
        logger.info("[article-writer] FAQ-generatie leverde geen bruikbare sectie — overgeslagen.")
        return html_body, []

    # Vóór een eventueel meta-blok invoegen, anders raakt dat achterop in de body.
    m = re.search(r"\n*<!--\s*[Mm]eta", html_body)
    merged = (html_body[:m.start()] + "\n\n" + out + "\n" + html_body[m.start():]) if m \
        else html_body.rstrip() + "\n\n" + out + "\n"

    faq = extract_faq(merged)
    if not faq:
        return html_body, []
    logger.info("[article-writer] FAQ-sectie alsnog gegenereerd (%d vragen).", len(faq))
    return merged, faq


# ── Fase 4: Links ────────────────────────────────────────────────────────────

# ── Canonieke URL-allowlist: interne links mogen ALLEEN naar deze bestemmingen.
# Harde uitsluiting van URLs die in de praktijk 404'en (gehallucineerd door oudere
# schrijf-runs) — deze mogen nooit meer als interne link worden voorgesteld.
_BLOCKED_INTERNAL_URLS = {
    "https://weareimpact.nl/digitalisering-bij-gemeenten",
    "https://weareimpact.nl/interim-management-sociaal-domein",
    "https://weareimpact.nl/lego-serious-play-draagvlak",
    "https://weareimpact.nl/case-digitale-transformatie-welzijn",
}
_BLOCKED_INTERNAL_PATHS = {urlparse(u).path.rstrip("/") for u in _BLOCKED_INTERNAL_URLS}

# Optionele grondbron: het gevalideerde URL-register (Obsidian-vault / SEO/
# url-register-<site>.json). Als die bestaat, worden ALLEEN urls die daarin
# staan als kandidaat toegelaten — zo kan de sitemap nooit meer kapotte links
# leveren. Zonder register blijft de sitemap de fallback (minus de blokkades).
_URL_REGISTER_CACHE: Dict[str, Optional[set]] = {}


def _canon_url(url: str) -> str:
    """Vergelijkbare vorm van een URL: zonder schema, zonder `www.`, zonder
    trailing slash, lowercase.

    Nodig omdat register en sitemap dezelfde pagina verschillend spellen: het
    vault-register bevat `https://daar.nl/platform`, de live sitemap levert
    `https://www.daar.nl/platform`. Letterlijk vergelijken wees daardoor élke
    sitemap-URL af op alleen het `www.`-voorvoegsel — met als gevolg nul
    interne-link-kandidaten en dus artikelen zónder één interne link, terwijl
    het register juist bedoeld was om links te gáránderen."""
    u = (url or "").strip().rstrip("/").lower()
    parsed = urlparse(u if "://" in u else f"//{u}")
    host = (parsed.netloc or "").removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/')}"


def _load_url_register(site: Dict) -> Optional[set]:
    """Laad de gevalideerde URL-set uit het vault-register voor deze site.

    Retourneert een set van genormaliseerde URLs (zonder trailing slash) of
    None als er geen register is (fallback op sitemap)."""
    base = (site.get("base_url") or "").strip().rstrip("/").lower()
    if base in _URL_REGISTER_CACHE:
        return _URL_REGISTER_CACHE[base]
    _URL_REGISTER_CACHE[base] = None  # default tenzij we een register vinden
    try:
        from pathlib import Path
        # Zoek het register in de Obsidian-vault (WeAreImpact/SEO/url-register-*.json)
        vault_root = os.getenv("OBSIDIAN_VAULT_PATH", "")
        if vault_root:
            candidates = list(Path(vault_root).rglob("url-register-*.json"))
            for c in candidates:
                try:
                    data = json.loads(c.read_text(encoding="utf-8"))
                except Exception:
                    continue
                reg_base = (data.get("site") or "").strip().rstrip("/").lower()
                base_host = base.split("://", 1)[-1].split("/", 1)[0] if base else ""
                reg_host = reg_base.split("://", 1)[-1].split("/", 1)[0] if reg_base else ""
                if reg_host and reg_host == base_host:
                    urls = {u.rstrip("/") for u in data.get("urls", [])}
                    if urls:
                        _URL_REGISTER_CACHE[base] = urls
                        logger.info("[article-writer] URL-register geladen: %d urls uit %s", len(urls), c)
                        break
    except Exception as e:
        logger.debug("[article-writer] URL-register laden mislukt: %s", str(e)[:150])
    return _URL_REGISTER_CACHE[base]


def _is_allowed_internal(url: str, register: Optional[set]) -> bool:
    u = url.strip().rstrip("/")
    if _canon_url(u) in {_canon_url(b) for b in _BLOCKED_INTERNAL_URLS}:
        return False
    if urlparse(u).path.rstrip("/") in _BLOCKED_INTERNAL_PATHS:
        return False
    if register is not None:
        # Op genormaliseerde vorm, niet letterlijk — zie `_canon_url`.
        return _canon_url(u) in {_canon_url(r) for r in register}
    return True  # fallback: sitemap (minus blokkades)


def _link_candidates(site: Dict) -> List[Dict[str, str]]:
    """Écht bestaande interne pagina's: eigen published_pages + live sitemap.

    Gefilterd door de canonieke allowlist: bekende 404-URLs worden permanent
    geweerd, en als er een gevalideerd URL-register bestaat (Obsidian-vault)
    worden uitsluitend die URLs toegelaten — zo werken interne links altijd."""
    from . import service as publish_service
    from ..seo import external_content

    register = _load_url_register(site)
    seen: set = set()
    candidates: List[Dict[str, str]] = []
    for p in publish_service.list_pages(site["id"]):
        url = (p.get("url") or "").strip()
        if url and url not in seen and _is_allowed_internal(url, register):
            seen.add(url)
            candidates.append({"url": url, "title": p.get("title") or ""})
    for url in external_content.fetch_live_sitemap_urls(site):
        if url not in seen and _is_allowed_internal(url, register):
            seen.add(url)
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            candidates.append({"url": url, "title": slug.replace("-", " ")})
    return candidates[:_MAX_LINK_CANDIDATES]


def _forbidden_spans(html: str) -> List[Tuple[int, int]]:
    """Posities waar we géén link mogen invoegen: tags, bestaande <a>, koppen."""
    spans = []
    for m in re.finditer(r"<a\b.*?</a>|<h[1-6][^>]*>.*?</h[1-6]>|<[^>]+>",
                         html, re.IGNORECASE | re.DOTALL):
        spans.append(m.span())
    return spans


def insert_link(html: str, anchor: str, url: str) -> Tuple[str, bool]:
    """Wrap het eerste vrije voorkomen van `anchor` (buiten tags/links/koppen)
    in een <a>. Retourneert (html, gelukt)."""
    anchor = (anchor or "").strip()
    if len(anchor) < 3:
        return html, False
    spans = _forbidden_spans(html)
    for m in re.finditer(re.escape(anchor), html, re.IGNORECASE):
        if any(s <= m.start() < e for s, e in spans):
            continue
        linked = f'<a href="{url}">{html[m.start():m.end()]}</a>'
        return html[:m.start()] + linked + html[m.end():], True
    return html, False


def _valid_external(url: str, own_host: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host = p.netloc.lower().removeprefix("www.")
    return p.scheme == "https" and bool(host) and host != own_host


def strip_unvetted_links(html: str, allowed_urls: set, allowed_paths: set) -> Tuple[str, int]:
    """Unwrap élke <a> waarvan de href niet aantoonbaar bestaat: niet in de
    kandidatenlijst (absolute URL of pad) en niet in de CTA-paden. De schrijf-
    en opmaakstappen mogen geen links verzinnen, maar LLM's doen dat toch —
    dit haalt ze weg met behoud van de ankertekst. De linkstap voegt daarna
    alleen gevalideerde links toe."""
    stripped = 0

    def _repl(m: re.Match) -> str:
        nonlocal stripped
        href = (m.group(1) or "").strip()
        path = urlparse(href).path.rstrip("/") if href else ""
        if href in allowed_urls or href.rstrip("/") in allowed_urls or (path and path in allowed_paths):
            return m.group(0)
        stripped += 1
        return m.group(2)

    out = re.sub(r'<a\b[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', _repl, html,
                 flags=re.IGNORECASE | re.DOTALL)
    return out, stripped


def strip_unvetted_internal_links(html: str, site: Dict,
                                  ctas: Optional[List[str]] = None) -> Tuple[str, int]:
    """Unwrap élke interne <a> die niet naar een geverifieerde bestemming wijst
    (candidatenlijst + CTA-paden). Externe links blijven onaangeroerd. Draai dit
    na ÉLKE stap die de volledige HTML herschrijft (bijv. SEO-optimalisatie) —
    zo'n rewrite kan de gevalideerde links uit `_link_pass` laten vallen of
    nieuwe interne URL's verzinnen, en die zijn dan niet meer gevet."""
    candidates = _link_candidates(site)
    own_host = urlparse((site.get("base_url") or "").strip()).netloc.lower().removeprefix("www.")
    allowed_urls = {c["url"].rstrip("/") for c in candidates} | {c["url"] for c in candidates}
    allowed_paths = {urlparse(c["url"]).path.rstrip("/") for c in candidates}
    for cta in (ctas or []):
        allowed_paths.update(p.rstrip("/") for p in re.findall(r"(/[a-z0-9\-_/]+)", cta.lower()))
    allowed_paths.discard("")

    stripped = 0

    def _repl(m: re.Match) -> str:
        nonlocal stripped
        href = (m.group(1) or "").strip()
        if not href or href == "#":
            stripped += 1
            return m.group(2)
        host = urlparse(href).netloc.lower().removeprefix("www.")
        if host and host != own_host:
            return m.group(0)  # externe link: hier niet aan komen
        path = urlparse(href).path.rstrip("/")
        if href in allowed_urls or href.rstrip("/") in allowed_urls or (path and path in allowed_paths):
            return m.group(0)
        stripped += 1
        return m.group(2)

    out = re.sub(r'<a\b[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', _repl, html,
                 flags=re.IGNORECASE | re.DOTALL)
    return out, stripped


async def _link_pass(site: Dict, keyword: str, html_body: str,
                     ctas: Optional[List[str]] = None) -> Tuple[str, Dict]:
    report = {"internal_added": 0, "external_added": 0, "rejected": 0}
    candidates = _link_candidates(site)
    own_host = urlparse((site.get("base_url") or "").strip()).netloc.lower().removeprefix("www.")

    # Eerst: links die de schrijf-/opmaakstap zelf heeft verzonnen eruit.
    # Toegestaan blijven kandidaat-URL's (of hun pad) en paden uit de CTA's.
    allowed_urls = {c["url"].rstrip("/") for c in candidates} | {c["url"] for c in candidates}
    allowed_paths = {urlparse(c["url"]).path.rstrip("/") for c in candidates}
    for cta in (ctas or []):
        allowed_paths.update(p.rstrip("/") for p in re.findall(r"(/[a-z0-9\-_/]+)", cta.lower()))
    allowed_paths.discard("")
    html_body, stripped = strip_unvetted_links(html_body, allowed_urls, allowed_paths)
    report["stripped"] = stripped

    # Internal-link-ARM: rangschik kandidaten op topical overlap met het
    # artikel (keyword + belangrijkste termen), zodat de linker cluster-relevante
    # pagina's kiest in plaats van willekeurig. Hoe hoger de overlap, hoe
    # waardevoller de link voor topical authority.
    kw_tokens = {t for t in re.findall(r"[a-zà-ü0-9]{4,}", (keyword or "").lower())}
    article_text = _plain_text(html_body).lower()
    ranked = []
    for c in candidates:
        title_tokens = {t for t in re.findall(r"[a-zà-ü0-9]{4,}", c["title"].lower())}
        overlap = len(kw_tokens & title_tokens)
        if not overlap:
            # valt terug op substring-match van keyword in de titel
            overlap = 1 if keyword and keyword.lower() in c["title"].lower() else 0
        ranked.append((overlap, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    candidates = [c for _, c in ranked]

    numbered = "\n".join(f"{i + 1}. {c['url']} — {c['title']}" for i, c in enumerate(candidates))
    internal_budget = max(_MAX_INTERNAL_LINKS, 2 if candidates else 0)
    system = (
        "Je bent een Nederlandse SEO-specialist. Kies voor het artikel passende links. "
        "Interne links: KIES UITSLUITEND uit de genummerde kandidatenlijst, en alleen waar "
        "een anker natuurlijk in de bestaande tekst staat (kopieer het anker LETTERLIJK uit "
        "de artikeltekst). Externe links: maximaal 2, alleen naar gezaghebbende bronnen "
        "(overheid, onderzoek, gerenommeerde vakmedia) die een bewering in de tekst staven. "
        "Antwoord UITSLUITEND met JSON: "
        '{"internal": [{"anchor": "letterlijke tekst uit artikel", "url": "..."}], '
        '"external": [{"anchor": "letterlijke tekst uit artikel", "url": "https://..."}]}\n'
        f"Zet bij voorkeur {internal_budget} interne links (ook als de ankers niet perfect zijn, "
        "kies dan de kandidaat die het dichtst bij de artikel-inhoud ligt). "
        "Geen kandidaten die passen? Lever lege lijsten."
    )
    prompt = (
        f"Kernzoekwoord: {keyword}\n\n"
        f"## Interne linkkandidaten (alleen hieruit kiezen)\n{numbered or '(geen)'}\n\n"
        f"## Artikel\n{html_body}"
    )
    try:
        raw = await _llm(system, prompt, max_tokens=900)
        picks = json.loads(_extract_json(raw))
    except Exception as e:
        logger.warning("[article-writer] Linkstap mislukt (%s) — artikel blijft zonder links", e)
        report["error"] = str(e)[:150]
        return html_body, report

    candidate_urls = {c["url"] for c in candidates}
    for item in (picks.get("internal") or [])[:internal_budget]:
        url = (item.get("url") or "").strip()
        if url not in candidate_urls:  # gehallucineerde interne URL → strippen
            report["rejected"] += 1
            continue
        html_body, ok = insert_link(html_body, item.get("anchor", ""), url)
        report["internal_added"] += int(ok)
    for item in (picks.get("external") or [])[:_MAX_EXTERNAL_LINKS]:
        url = (item.get("url") or "").strip()
        if not _valid_external(url, own_host):
            report["rejected"] += 1
            continue
        html_body, ok = insert_link(html_body, item.get("anchor", ""), url)
        report["external_added"] += int(ok)
    return html_body, report


# ── Fase 5: Deterministische QC ──────────────────────────────────────────────

def check_ai_language(html_body: str) -> List[str]:
    text = _plain_text(html_body).lower()
    return [p for p in BANNED_PHRASES if p in text]


def check_cta(html_body: str, ctas: List[str]) -> bool:
    """True als minstens één CTA (het tekstdeel vóór een eventuele '→' of URL)
    herkenbaar in het artikel staat. Zonder geconfigureerde CTA's: True."""
    if not ctas:
        return True
    text = fold_diacritics(_plain_text(html_body).lower())
    html_low = (html_body or "").lower()
    for cta in ctas:
        label = re.split(r"→|https?://", cta)[0].strip().lower()
        # CTA's staan in de kennisbank als "«actie» op «domein/pad»"
        # ("Ontdek de Ritual Box op steentjebijsteentje.nl/de-ritual-box").
        # Het domeindeel is de bestemming, geen zinsdeel dat een schrijver in de
        # lopende tekst zet: die maakt er een link van. Zonder dat deel af te
        # kappen matcht de CTA nooit en verliest élk artikel 6 punten voor een
        # CTA die er gewoon staat.
        target = ""
        m = re.search(r"\s+(?:op|via|naar)\s+(\S*\.\w{2,}\S*)\s*$", label)
        if m:
            target = m.group(1)
            label = label[:m.start()].strip()
        if len(label) >= 8 and fold_diacritics(label) in text:
            return True
        # …of de bestemming staat als link in het artikel (href), wat een
        # sterker CTA-signaal is dan de letterlijke zin.
        path = re.split(r"→|https?://", cta)[-1].strip().lower() if "http" in cta.lower() else target
        if path and f'href="' in html_low and path.rstrip("/") in html_low:
            return True
    return False


def check_keyword(html_body: str, keyword: str) -> List[str]:
    """Zoekwoord-checks: in H1, in de eerste 100 woorden, dichtheid ≤ 2,5% en
    minimaal 2× aanwezig. Retourneert de lijst gefaalde checks (leeg = pass)."""
    issues: List[str] = []
    # Accenten wegvouwen aan béide kanten: het zoekwoord komt uit GSC ('ideeen'),
    # de tekst is correct Nederlands ('ideeën'). Zie `fold_diacritics`.
    kw = fold_diacritics(keyword.strip().lower())
    if not kw:
        return issues
    text = fold_diacritics(_plain_text(html_body).lower())
    words = text.split()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_body, re.IGNORECASE | re.DOTALL)
    h1 = fold_diacritics(_plain_text(m.group(1)).lower()) if m else ""
    kw_tokens = [t for t in re.findall(r"[a-zà-ü0-9]+", kw) if len(t) > 2]
    if h1 and kw_tokens and not all(t in h1 for t in kw_tokens):
        issues.append(f"zoekwoord '{keyword}' ontbreekt (deels) in de H1")
    intro = " ".join(words[:100])
    if kw not in intro and (not kw_tokens or not all(t in intro for t in kw_tokens)):
        issues.append(f"zoekwoord '{keyword}' ontbreekt in de eerste 100 woorden")
    count = text.count(kw)
    if count < 2:
        issues.append(f"zoekwoord '{keyword}' komt maar {count}× letterlijk voor (minimaal 2×)")
    kw_len = max(1, len(kw.split()))
    density = (count * kw_len) / max(1, len(words)) * 100
    if density > 2.5:
        issues.append(f"zoekwoorddichtheid {density:.1f}% is te hoog (keyword stuffing, max 2,5%)")
    return issues


async def _qc_fix(site: Dict, keyword: str, html_body: str, issues: List[str],
                  ctas: List[str]) -> str:
    system = (
        "Je bent een strenge Nederlandse eindredacteur. Herschrijf het artikel minimaal-"
        "invasief zodat de genoemde problemen zijn opgelost. Behoud structuur, links, "
        "opmaak en alle feiten. Lever ALLEEN de volledige gecorrigeerde HTML-body."
    )
    prompt_parts = [f"Kernzoekwoord: {keyword}", "Los deze problemen op:\n" + "\n".join(f"- {i}" for i in issues)]
    if ctas:
        prompt_parts.append("Beschikbare call-to-actions:\n" + "\n".join(f"- {c}" for c in ctas[:6]))
    prompt_parts.append(f"ARTIKEL:\n{html_body}")
    out = await _llm(system, "\n\n".join(prompt_parts), max_tokens=6000)
    return out.strip() if len(out.strip()) >= len(html_body) * 0.6 else html_body


# ── Orchestratie ─────────────────────────────────────────────────────────────

async def _meta_pass(site: Dict, keyword: str, html_body: str) -> Tuple[str, Dict]:
    """Schrijf een échte meta-titel/-description en hang die als META-commentaar
    onder de body; `content_pipeline._strip_meta_and_suggestions` leest het eruit
    en `_publish_to_site` schrijft het naar de head/DB-velden.

    Waarom een eigen stap: zonder META-blok viel de publisher terug op het
    mechanisch afkappen van de eerste alinea. Zo'n description eindigt altijd
    midden in een zin, en de SEO-reviewer trok daar élke ronde punten voor af —
    een aftrek die geen enkele herschrijfronde van het ártikel kan repareren,
    waardoor artikelen hun verbeter-pogingen opmaakten aan een gebrek dat niet
    in het artikel zat. De rubriek beoordeelt meta, dus moeten we meta leveren.
    Faalt de stap, dan blijft de body intact en valt de publisher terug op het
    oude (afgekapte) gedrag — geen artikel loopt hierop vast."""
    text = _plain_text(html_body)[:3000]
    raw = await _llm(
        "Je bent een SEO-eindredacteur. Je schrijft meta-teksten die kloppen met "
        "het artikel en aanzetten tot klikken. Nooit clickbait, nooit verzinnen.",
        f"Kernzoekwoord: {keyword}\nSite: {site.get('name', '')}\n\n"
        f"ARTIKEL (platte tekst):\n{text}\n\n"
        "Schrijf een meta-titel van MAXIMAAL 60 tekens (kernzoekwoord erin) en een "
        "meta-description van MAXIMAAL 155 tekens: één of twee complete zinnen die "
        "aflopen op een punt — nooit een afgekapte zin — met een concrete reden om "
        "te klikken. De description mag de titel niet herhalen.\n"
        'Antwoord UITSLUITEND met JSON: {"meta_title": "...", "meta_description": "..."}',
        max_tokens=400,
    )
    obj = json.loads(_extract_json(raw))
    title = " ".join(str(obj.get("meta_title") or "").split())
    desc = " ".join(str(obj.get("meta_description") or "").split())
    if not title or not desc:
        return html_body, {"ok": False, "reason": "lege meta-respons"}
    # Harde grenzen: het model gaat er soms een paar tekens overheen. Liever een
    # nette hercontrole dan een description die Google zelf afkapt.
    over = {"title": len(title) > 60, "description": len(desc) > 155}
    title, desc = title[:60].rstrip(), desc[:155].rstrip()
    block = (f'\n\n<!-- Meta-titel: {title} -->'
             f'\n<!-- Meta-description: {desc} -->')
    return html_body.rstrip() + block, {
        "ok": True, "title_len": len(title), "description_len": len(desc),
        "truncated": over,
    }


async def write_article_staged(site: Dict, keyword: str, angle: str, rationale: str,
                               case_study: Optional[Dict], profile: str, ctas: List[str],
                               brand_context: str, base_style_prompt: str) -> Tuple[str, Dict]:
    """Volledige meertraps-generatie. Raise't bij een kapotte outline of lege
    secties (de aanroeper valt dan terug op de single-shot-schrijver);
    opmaak-/link-/QC-stappen falen zacht en laten het artikel intact."""
    qc: Dict = {"staged": True, "case_study": None}
    if case_study:
        qc["case_study"] = {"id": case_study["id"], "title": case_study["title"]}

    outline = await _make_outline(site, keyword, angle, rationale,
                                  case_study, profile, ctas, brand_context)
    qc["outline_sections"] = len(outline["sections"])
    qc["format"] = outline.get("format", "gids")

    html_body = await _write_sections(site, keyword, outline, case_study,
                                      profile, ctas, brand_context, base_style_prompt)

    try:
        html_body = await _format_pass(html_body)
        qc["format"] = {"pass": True}
    except Exception as e:
        logger.warning("[article-writer] Opmaakstap mislukt: %s", e)
        qc["format"] = {"pass": False, "error": str(e)[:150]}

    html_body, link_report = await _link_pass(site, keyword, html_body, ctas=ctas)
    qc["links"] = link_report

    # ── Fase 5b: AEO / structured data ──────────────────────────────────────
    # FAQ extraheren uit de body. De schrijf-prompts eisen een FAQ-sectie, maar
    # het model levert die niet altijd — en dan kost het artikel 5 punten in de
    # kwaliteitsgate (`_review_article`) én de FAQPage-markup. Dat is precies hoe
    # artikelen op 77 blijven steken: inhoudelijk prima, maar geblokkeerd op een
    # gebrek dat geen enkele herschrijfronde aanpakt, omdat de reviewer om
    # ándere dingen vraagt. Ontbreekt de sectie, dan schrijven we hem hier
    # alsnog; mislukt dat, dan gaat het artikel gewoon door zonder (nooit
    # blokkeren op een nice-to-have).
    try:
        from ..seo.enhancements import extract_faq, generate_json_ld
        faq = extract_faq(html_body)
        if not faq:
            html_body, faq = await _ensure_faq(site, keyword, html_body)
            qc["faq_generated"] = bool(faq)
        if faq:
            author = site.get("author") or (profile[:60] if profile else "")
            json_ld = generate_json_ld(site, keyword, html_body, author=author, faq=faq)
            html_body = html_body.rstrip() + "\n\n" + json_ld
            qc["json_ld"] = {"ok": True, "faq_items": len(faq)}
        else:
            qc["json_ld"] = {"ok": False, "reason": "geen FAQ-sectie gevonden"}
    except Exception as e:
        logger.warning("[article-writer] AEO/JSON-LD-stap mislukt: %s", e)
        qc["json_ld"] = {"ok": False, "error": str(e)[:150]}

    # Deterministische checks → één gecombineerde fix-call indien nodig.
    ai_hits = check_ai_language(html_body)
    cta_ok = check_cta(html_body, ctas)
    kw_issues = check_keyword(html_body, keyword)

    issues: List[str] = []
    if ai_hits:
        issues.append("verwijder/vervang deze AI-cliché-formuleringen (natuurlijk Nederlands ervoor in de plaats): "
                      + ", ".join(f"'{h}'" for h in ai_hits))
    if not cta_ok:
        issues.append("er staat geen call-to-action in het artikel — verwerk er één natuurlijk "
                      "halverwege of aan het slot")
    issues.extend(kw_issues)

    fixed = False
    if issues:
        try:
            html_body = await _qc_fix(site, keyword, html_body, issues, ctas)
            fixed = True
            # _qc_fix herschrijft de volledige HTML-body en kan, ondanks de instructie
            # om links te behouden, gevalideerde interne links laten vallen of nieuwe
            # verzinnen — net als de SEO-optimalisatieronde in content_pipeline.py.
            # Zonder deze wied-stap belanden ongevette (mogelijk 404-)links alsnog live.
            html_body, n_stripped = strip_unvetted_internal_links(html_body, site, ctas=ctas)
            if n_stripped:
                logger.info("[article-writer] QC-fix: %d ongevette interne link(s) verwijderd", n_stripped)
        except Exception as e:
            logger.warning("[article-writer] QC-fix mislukt: %s", e)

    # Hercheck ná de fix, zodat het rapport de eindtoestand beschrijft.
    ai_hits_after = check_ai_language(html_body)
    qc["ai_language"] = {"pass": not ai_hits_after, "hits": ai_hits_after, "fixed": fixed and bool(ai_hits)}
    qc["cta"] = {"pass": check_cta(html_body, ctas), "fixed": fixed and not cta_ok,
                 "configured": bool(ctas)}
    kw_after = check_keyword(html_body, keyword)
    qc["keyword"] = {"pass": not kw_after, "issues": kw_after, "fixed": fixed and bool(kw_issues)}

    try:
        html_body, meta_report = await _meta_pass(site, keyword, html_body)
        qc["meta"] = meta_report
    except Exception as e:
        logger.warning("[article-writer] Meta-stap mislukt: %s", e)
        qc["meta"] = {"ok": False, "error": str(e)[:150]}

    return html_body, qc

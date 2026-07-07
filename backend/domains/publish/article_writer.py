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
import re
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

async def _make_outline(site: Dict, keyword: str, angle: str, rationale: str,
                        case_study: Optional[Dict], profile: str, ctas: List[str],
                        brand_context: str) -> Dict:
    system = (
        "Je bent een Nederlandse SEO-contentstrateeg. Je maakt een outline voor een "
        "blogartikel dat écht iets toevoegt ('information gain') — geen generieke opsomming "
        "die elke AI kan schrijven, maar een stuk gebouwd rond eigen data en ervaring. "
        "Antwoord UITSLUITEND met JSON:\n"
        '{"title": "H1 met het zoekwoord er natuurlijk in", "sections": ['
        '{"heading": "H2-tekst", "goal": "wat deze sectie de lezer oplevert", '
        '"use_case_study": true/false, "include_cta": true/false}]}\n'
        "Regels: 4-6 secties; precies één sectie gebruikt de casestudy als bewijs "
        "(use_case_study), tenzij er geen casestudy is; maximaal 2 secties met include_cta "
        "(één halverwege, één aan het slot)."
    )
    prompt_parts = [
        f"Site: {site['name']} ({site.get('base_url', '')})",
        f"Kernzoekwoord: {keyword}",
    ]
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

    raw = await _llm(system, "\n\n".join(prompt_parts), max_tokens=1200)
    outline = json.loads(_extract_json(raw))
    sections = outline.get("sections") or []
    if not outline.get("title") or not (4 <= len(sections) <= 8):
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
        "één van de beschikbare call-to-actions op een natuurlijke, niet-opdringerige manier."
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
        out = await _llm(system, "\n\n".join(prompt_parts), max_tokens=3000)
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


# ── Fase 4: Links ────────────────────────────────────────────────────────────

def _link_candidates(site: Dict) -> List[Dict[str, str]]:
    """Écht bestaande interne pagina's: eigen published_pages + live sitemap."""
    from . import service as publish_service
    from ..seo import external_content

    seen: set = set()
    candidates: List[Dict[str, str]] = []
    for p in publish_service.list_pages(site["id"]):
        url = (p.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            candidates.append({"url": url, "title": p.get("title") or ""})
    for url in external_content.fetch_live_sitemap_urls(site):
        if url not in seen:
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

    numbered = "\n".join(f"{i + 1}. {c['url']} — {c['title']}" for i, c in enumerate(candidates))
    system = (
        "Je bent een Nederlandse SEO-specialist. Kies voor het artikel passende links. "
        "Interne links: KIES UITSLUITEND uit de genummerde kandidatenlijst, en alleen waar "
        "een anker natuurlijk in de bestaande tekst staat (kopieer het anker LETTERLIJK uit "
        "de artikeltekst). Externe links: maximaal 2, alleen naar gezaghebbende bronnen "
        "(overheid, onderzoek, gerenommeerde vakmedia) die een bewering in de tekst staven. "
        "Antwoord UITSLUITEND met JSON: "
        '{"internal": [{"anchor": "letterlijke tekst uit artikel", "url": "..."}], '
        '"external": [{"anchor": "letterlijke tekst uit artikel", "url": "https://..."}]}\n'
        f"Maximaal {_MAX_INTERNAL_LINKS} interne links. Geen kandidaten die passen? Lever lege lijsten."
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
    for item in (picks.get("internal") or [])[:_MAX_INTERNAL_LINKS]:
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
    text = _plain_text(html_body).lower()
    for cta in ctas:
        label = re.split(r"→|https?://", cta)[0].strip().lower()
        if len(label) >= 8 and label in text:
            return True
    return False


def check_keyword(html_body: str, keyword: str) -> List[str]:
    """Zoekwoord-checks: in H1, in de eerste 100 woorden, dichtheid ≤ 2,5% en
    minimaal 2× aanwezig. Retourneert de lijst gefaalde checks (leeg = pass)."""
    issues: List[str] = []
    kw = keyword.strip().lower()
    if not kw:
        return issues
    text = _plain_text(html_body).lower()
    words = text.split()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_body, re.IGNORECASE | re.DOTALL)
    h1 = _plain_text(m.group(1)).lower() if m else ""
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
    out = await _llm(system, "\n\n".join(prompt_parts), max_tokens=4500)
    return out.strip() if len(out.strip()) >= len(html_body) * 0.6 else html_body


# ── Orchestratie ─────────────────────────────────────────────────────────────

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
        except Exception as e:
            logger.warning("[article-writer] QC-fix mislukt: %s", e)

    # Hercheck ná de fix, zodat het rapport de eindtoestand beschrijft.
    ai_hits_after = check_ai_language(html_body)
    qc["ai_language"] = {"pass": not ai_hits_after, "hits": ai_hits_after, "fixed": fixed and bool(ai_hits)}
    qc["cta"] = {"pass": check_cta(html_body, ctas), "fixed": fixed and not cta_ok,
                 "configured": bool(ctas)}
    kw_after = check_keyword(html_body, keyword)
    qc["keyword"] = {"pass": not kw_after, "issues": kw_after, "fixed": fixed and bool(kw_issues)}

    return html_body, qc

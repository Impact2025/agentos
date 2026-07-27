"""SEO-wereldklasse-enhancer.

Vier componenten die het verschil maken tussen "een artikel dat rankt" en
"een artikel dat de SERP domineert (inclusief AI Overviews en rich results)":

  1. generate_json_ld()  — Article + FAQPage structured data (JSON-LD).
                           Zonder dit mis je featured-snippet- en rich-result-
                           kansen volledig. De publisher/organisatie komt uit de
                           site-config zodat het JSON-LD klikt met de rest van
                           de site.
  2. build_direct_answer() — een compacte, zelfstandig leesbare antwoord-
                           paragraaf (40-60 woorden) die direct de zoekintentie
                           beantwoordt. Dit is wat Google en ChatGPT citeerbaar
                           maken voor AI Overviews / SGE.
  3. extract_faq()        — FAQ-sectie (vraag + antwoord) geschikt voor de
                           FAQPage-schema en voor de "People Also Ask"-carrousel.
  4. ee_at_guard()        — deterministische E-E-A-T / information-gain-check:
                           ontbrekende bronvermelding bij harde claims, geen
                           'volgens onderzoek' zonder bron, generieke vulling.

Alles is deterministisch (geen LLM) zodat de kwaliteitsgate het ook bij
fallback-content kan afdwingen. De LLM-stappen (prompts) worden versterkt in
article_writer.py zelf; deze module levert de afdwingbare, parseerbare output.
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Dict, List, Optional, Tuple

from ..publish.article_writer import _plain_text


# ── 1. JSON-LD structured data ────────────────────────────────────────────────

def generate_json_ld(site: Dict, keyword: str, html_body: str,
                     author: str = "", faq: Optional[List[Dict[str, str]]] = None) -> str:
    """Bouw een JSON-LD script-blok (Article + optioneel FAQPage).

    `faq` is een lijst van {"question": "...", "answer": "..."}. Als die er is,
    wordt een FAQPage-graph toegevoegd — die wordt door Google uitgelezen voor
    de FAQ-rich-result en door AI-modellen voor het beantwoorden van PAA.
    """
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html_body, re.IGNORECASE | re.DOTALL)
    title = _plain_text(title_m.group(1)).strip() if title_m else (keyword or site.get("name", ""))
    text = _plain_text(html_body)
    words = text.split()
    description = " ".join(words[:30]) + ("…" if len(words) > 30 else "")
    base_url = (site.get("base_url") or "").rstrip("/")
    publisher = site.get("name", "")

    article = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title[:110],
        "description": description[:160],
        "inLanguage": "nl-NL",
        "keywords": keyword,
        "articleSection": keyword,
        "mainEntityOfPage": {"@type": "WebPage", "@id": base_url},
        "publisher": {
            "@type": "Organization",
            "name": publisher,
            "url": base_url or None,
        },
    }
    if author:
        article["author"] = {"@type": "Person", "name": author[:80]}
    if faq:
        article["mainEntity"] = {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q.get("question", ""),
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": _plain_text(q.get("answer", "")),
                    },
                }
                for q in faq
            ],
        }

    return "<script type=\"application/ld+json\">\n" + json.dumps(article, ensure_ascii=False, indent=2) + "\n</script>"


# ── 2. Direct-answer paragraaf (AI Overview / SGE) ──────────────────────────────

def build_direct_answer(keyword: str, html_body: str, max_words: int = 55) -> str:
    """Haal de eerste, op de zoekintentie gerichte alinea uit het artikel.

    Een goede direct-answer staat in de eerste 2 alinea's en beantwoordt de
    vraag zónder 'opwarming'. Als de intro te kort/te lang is, kappen we op
    max_words en eindigen we op een zinpauze.
    """
    # Neem de tekst na de H1 tot aan het einde van de eerste <p> (of 400 tekens).
    body_after_h1 = re.sub(r"^.*?</h1>", "", html_body, flags=re.IGNORECASE | re.DOTALL)
    first_para = re.search(r"<p[^>]*>(.*?)</p>", body_after_h1, re.IGNORECASE | re.DOTALL)
    candidate = _plain_text(first_para.group(1)) if first_para else _plain_text(body_after_h1)
    candidate = candidate.strip()
    if not candidate:
        return ""
    words = candidate.split()
    if len(words) > max_words:
        trimmed = " ".join(words[:max_words])
        # Knip netjes af op een zinpauze als die er is.
        for punct in [".", "!", "?"]:
            idx = trimmed.rfind(punct)
            if idx > len(trimmed) * 0.5:
                return trimmed[:idx + 1]
        return trimmed + "…"
    return candidate


# ── 3. FAQ-extract ────────────────────────────────────────────────────────────

def extract_faq(html_body: str) -> List[Dict[str, str]]:
    """Vind een FAQ-sectie in het artikel (kop met 'FAQ'/'veelgestelde' en
    daaropvolgende vraag-antwoord-paren) en geef die gestructureerd terug.

    Accepteert zowel <h2>Vraag</h2><p>antwoord</p>-patronen als een
    <ul><li><strong>Vraag?</strong> antwoord</li></ul>-patroon.
    """
    faq: List[Dict[str, str]] = []
    # Patroon A: H2/H3 als vraag, directe <p> als antwoord.
    blocks = re.split(r"(<h[23][^>]*>.*?</h[23]>)", html_body, flags=re.IGNORECASE | re.DOTALL)
    heading = None
    in_faq_section = False
    faq_heading_re = re.compile(r"veelgestelde vragen|faq|frequently asked", re.IGNORECASE)
    for chunk in blocks:
        hm = re.match(r"<h[23][^>]*>(.*?)</h[23]>", chunk, re.IGNORECASE | re.DOTALL)
        if hm:
            heading = _plain_text(hm.group(1)).strip()
            in_faq_section = bool(faq_heading_re.search(heading))
            continue
        if heading and re.search(r"\?$", heading):
            pm = re.search(r"<p[^>]*>(.*?)</p>", chunk, re.IGNORECASE | re.DOTALL)
            if pm:
                ans = _plain_text(pm.group(1)).strip()
                if len(ans) > 15:
                    faq.append({"question": heading, "answer": ans})
            heading = None
        elif in_faq_section:
            # Binnen een FAQ-sectie: vang <p><strong>Vraag?</strong> antwoord</p>
            for qm in re.finditer(
                r"<p[^>]*>\s*<strong>(.*?\?)</strong>\s*(.*?)</p>",
                chunk, re.IGNORECASE | re.DOTALL,
            ):
                q = _plain_text(qm.group(1)).strip()
                a = _plain_text(qm.group(2)).strip()
                if len(a) > 15:
                    faq.append({"question": q, "answer": a})
    if faq:
        return faq[:6]

    # Patroon B: <li><strong>Vraag?</strong> rest</li>
    for m in re.finditer(r"<li[^>]*>\s*<strong>(.*?\?)</strong>\s*(.*?)</li>",
                         html_body, re.IGNORECASE | re.DOTALL):
        q = _plain_text(m.group(1)).strip()
        a = _plain_text(m.group(2)).strip()
        if len(a) > 15:
            faq.append({"question": q, "answer": a})
    return faq[:6]


# ── 4. E-E-A-T / information-gain guard ────────────────────────────────────────

# Claim-woorden die een bron of concreet cijfer eisen.
_CLAIM_WORDS = (
    "volgens onderzoek", "uit onderzoek blijkt", "studie toont", "rapport toont",
    "onderzoek wijst uit", "cijfers tonen", "gemiddeld", "80%", "90%", "70%",
    "twee derde", "de helft", "wetenschap", "bewezen", "aangetoond",
)
# Vage, generieke vulling die information-gain ondermijnt.
_GENERIC_FILLER = (
    "het is belangrijk om", "tegenwoordig", "tegenwoordig is", "tegenwoordig zijn er",
    "een rol spelen", "een belangrijke rol", "in de huidige maatschappij",
    "het speelt een rol", "steeds vaker", "steeds meer", "in de huidige tijd",
    "algemeen bekend", "over het algemeen", "kortom is het", "het is duidelijk dat",
)

_BARE_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def ee_at_guard(html_body: str, keyword: str, site: Optional[Dict] = None) -> List[str]:
    """Deterministische E-E-A-T / information-gain-check. Retourneert een lijst
    van gevonden problemen (leeg = schoon). Deze check draait in de
    kwaliteitsgate en blokkeert publicatie bij harde issues.

    Regels:
      - Claims zonder bron: "volgens onderzoek" maar geen enkele <a href> of
        url in de buurt → verzin geen wetenschap.
      - Generieke vulling: te veel vage zinnen → geen information gain.
      - Lege/te korte artikelen leveren sowieso een issue.
    """
    issues: List[str] = []
    text = _plain_text(html_body)
    low = text.lower()
    words = text.split()
    if len(words) < 250:
        issues.append(f"artikel te kort ({len(words)} woorden) — minimaal 250 voor autoriteit")
    if not low.strip():
        issues.append("artikel is leeg")

    # Claims zonder bron.
    has_source = bool(_BARE_URL_RE.search(text)) or ("href=" in html_body.lower())
    for cw in _CLAIM_WORDS:
        if cw in low:
            if not has_source:
                issues.append(f"claim '{cw}' zonder bronvermelding — voeg een gezaghebbende bron toe of haal de claim weg")
            break  # één melding is genoeg

    # Generieke vulling-telling (information-gain).
    filler_hits = sum(1 for f in _GENERIC_FILLER if f in low)
    if filler_hits >= 3:
        issues.append(f"te veel vage, generieke vulling ({filler_hits}×) — voeg concrete voorbeelden/cases toe (information gain)")

    # Keyword moet zinnig gedekt zijn (geen topical-thin content).
    if keyword:
        # Accenten vouwen: 'jubileum cadeau ideeen' (GSC-spelling) moet matchen
        # op 'jubileum cadeau ideeën' (correct Nederlands). Zie fold_diacritics.
        from ..publish.article_writer import fold_diacritics
        kw = fold_diacritics(keyword.strip().lower())
        low = fold_diacritics(low)
        if kw and kw not in low:
            issues.append(f"zoekwoord '{keyword}' komt niet voor in de body")
    return issues


def assess_seo_worldclass(html_body: str, keyword: str, site: Optional[Dict] = None) -> Dict:
    """Gecombineerde SEO-score (0-100) die wereldklasse afdwingt.

    Combineert de bestaande deterministische checks (AI-taal, CTA, keyword)
    met de nieuwe E-E-A-T-guard en de aanwezigheid van AEO-bouwstenen
    (direct-answer + FAQ). Gebruikt door de kwaliteitsgate om 'needs_work'
    af te dwingen wanneer het stuk niet rich-result/AI-Overview-klaar is.
    """
    from ..publish.article_writer import check_ai_language, check_cta, check_keyword

    ctas = (site or {}).get("ctas", []) or []
    ai = check_ai_language(html_body)
    cta_ok = check_cta(html_body, ctas)
    kw_issues = check_keyword(html_body, keyword)
    ee = ee_at_guard(html_body, keyword, site)

    da = build_direct_answer(keyword, html_body)
    faq = extract_faq(html_body)

    # Harde lengte-eis: dunne content krijgt een zware, niet-te-ontlopen
    # penalty — topical authority komt niet van 100 woorden.
    w = len(_plain_text(html_body).split())
    word_penalty = 40 if w < 250 else (20 if w < 400 else 0)

    penalties = 0
    penalties += min(len(ai), 3) * 4          # AI-clichés
    penalties += 0 if cta_ok else 6           # geen CTA
    penalties += min(len(kw_issues), 3) * 3   # keyword-issues
    penalties += min(len(ee), 4) * 5         # E-E-A-T issues (zwaarst)
    penalties += word_penalty                  # dunne content
    if not da:
        penalties += 5                        # geen direct-answer voor AEO
    if not faq:
        penalties += 5                        # geen FAQ voor rich-results

    score = max(0, 100 - penalties)
    return {
        "score": score,
        "ai_language": ai,
        "cta_ok": cta_ok,
        "keyword_issues": kw_issues,
        "ee_at_issues": ee,
        "has_direct_answer": bool(da),
        "faq_count": len(faq),
        "worldclass": score >= 85,
    }


# ── 5. Backlink-ARM (topical authority) ──────────────────────────────────────

def apply_backlinks(site: Dict, new_slug: str, new_title: str, new_url: str) -> Dict:
    """Terug-link-ARM: geef bestaande gepubliceerde pagina's (die hetzelfde
    cluster raken) een link naar het nieuwe artikel, zodat de link-equity
    die je al hebt gaat stromen naar verse content.

    Veilig & idempotent:
      - alleen pagina's != nieuw artikel, met lexicale overlap op titel/slug
      - alleen bij een NATUURLIJKE anker in de bestaande tekst (kopieer
        letterlijk een bestaande woordgroep als anker)
      - slaat over als de link er al zit
      - nooit crashen; rapporteert alleen wat het deed
    """
    from ..publish.article_writer import insert_link
    from ...shared.database import get_conn

    new_tokens = {t for t in re.findall(r"[a-zà-ü0-9]{4,}", (new_title or "").lower())}
    if not new_tokens:
        return {"added": 0, "checked": 0, "skipped": 0}

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slug, title, html FROM published_pages "
            "WHERE site_id = ? AND slug != ? AND html != ''",
            (site["id"], new_slug),
        ).fetchall()

        added = 0
        checked = 0
        skipped = 0
        for r in rows:
            r = dict(r)
            checked += 1
            html = r["html"]
            # Al gelinkt?
            if new_url.rstrip("/") in html or f"/{new_slug}/" in html:
                skipped += 1
                continue
            # Overlap op titel/slug — anders geen zinnige backlink.
            page_tokens = {t for t in re.findall(r"[a-zà-ü0-9]{4,}",
                                                  ((r["title"] or "") + " " + r["slug"]).lower())}
            if not (new_tokens & page_tokens):
                skipped += 1
                continue
            # Zoek een natuurlijke anker: een woordgroep in de body die overeenkomt
            # met een 2-3-gram uit de nieuwe titel.
            anchor = _find_natural_anchor(html, new_title)
            if not anchor:
                skipped += 1
                continue
            new_html, ok = insert_link(html, anchor, new_url)
            if ok:
                conn.execute(
                    "UPDATE published_pages SET html = ?, updated_at = ? WHERE slug = ? AND site_id = ?",
                    (new_html, _now_iso(), r["slug"], site["id"]),
                )
                added += 1
    return {"added": added, "checked": checked, "skipped": skipped}


def _find_natural_anchor(html_body: str, target_title: str) -> Optional[str]:
    """Vind een bestaande woordgroep in de body die als anker kan dienen voor
    een link naar `target_title`. Zoekt 2-3-grams uit de titel die letterlijk
    in de body voorkomen. Leeg = geen natuurlijke plek gevonden."""
    text = _plain_text(html_body)
    low = text.lower()
    words = re.findall(r"[a-zà-ü0-9]+", (target_title or "").lower())
    grams = []
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            grams.append(" ".join(words[i:i + n]))
    for g in grams:
        if len(g) >= 6 and re.search(r"\b" + re.escape(g) + r"\b", low):
            m = re.search(r"\b" + re.escape(g) + r"\b", text, re.IGNORECASE)
            return m.group(0) if m else None
    return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_json_ld(html_body: str) -> Dict:
    """Controleer of het artikel valide JSON-LD bevat (Article + FAQPage).

    Geen externe lijvige schema-validator — we doen een pragmatische,
    afdwingbare check: vindt het <script type=application/ld+json>-blok,
    parset het als JSON (crash = ongeldig), en verifieert de minimale
    eisen die Google stelt aan rich results:
      - @context + @type Article
      - headline aanwezig
      - publisher.name aanwezig
      - bij FAQPage: minimaal 1 Question met naam + acceptedAnswer.text
    Retourneert {valid, errors, has_faq}.
    """
    errors: List[str] = []
    m = re.search(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_body, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return {"valid": False, "errors": ["geen JSON-LD script-blok gevonden"], "has_faq": False}
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        return {"valid": False, "errors": [f"JSON-LD is geen geldig JSON: {str(e)[:80]}"], "has_faq": False}
    if data.get("@type") != "Article" and not (
        isinstance(data.get("@type"), list) and "Article" in data.get("@type")
    ):
        errors.append("JSON-LD @type is niet 'Article'")
    if not (data.get("headline") or "").strip():
        errors.append("JSON-LD mist een 'headline'")
    pub = data.get("publisher") or {}
    if not (pub.get("name") or "").strip():
        errors.append("JSON-LD mist publisher.name")
    has_faq = False
    me = data.get("mainEntity")
    if me and me.get("@type") == "FAQPage":
        qs = me.get("mainEntity") or []
        has_faq = len(qs) > 0
        if not qs:
            errors.append("FAQPage heeft geen vragen")
        else:
            for i, q in enumerate(qs):
                if not (q.get("name") or "").strip():
                    errors.append(f"FAQ-vraag {i+1} mist een 'name'")
                    break
                ans = q.get("acceptedAnswer") or {}
                if not (ans.get("text") or "").strip():
                    errors.append(f"FAQ-antwoord {i+1} mist 'text'")
                    break
    return {"valid": len(errors) == 0, "errors": errors, "has_faq": has_faq}

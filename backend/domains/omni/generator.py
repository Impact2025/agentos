"""SERP-Omni asset-generator.

Genereert per aanbevolen platform-asset (uit serp.analyze_serp) een kant-en-
klaar concept. Elk asset gaat door dezelfde kwaliteitsdiscipline als de rest
van AgentOS:

  - tekst komt via `article_writer._llm`, die FEITEN_GRONDWET forceert
    (geen verzonnen awards/bedrijven/cijfers — zie de anti-verzinsel-regel);
  - social posts worden gecheckt op AI-clichés via `check_ai_language`;
  - het AEO-snippet wordt gescoord met `assess_seo_worldclass` zodat het
    écht antwoord geeft op de zoekintentie (direct-answer + FAQ), precies
    wat Google/ChatGPT citeerbaar maakt voor AI Overviews.

Output-per-asset:
    {"asset_type", "platform", "title", "body", "status", "score", "note"}
`status` is altijd 'staged' — NIETS wordt automatisch gepost. De router zet
ze in omni_queue en Vincent keurt ze goed (net als de Wachtrij).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .serp import analyze_serp

logger = logging.getLogger(__name__)


# ── Platform-specifieke promptbouw ───────────────────────────────────────────
def _build_prompt(asset_type: str, keyword: str, site: Dict,
                  angle: str, serp_profile: Dict) -> tuple[str, str]:
    """Geef (system, user) terug voor één asset-type.

    Alle prompts eisen: waardevol/géén spam, Nederlands, geen verzonnen claims,
    geen 'cruciaal/naadloos/in de wereld van'-clichés. FEITEN_GRONDWET wordt
    door _llm automatisch toegevoegd.
    """
    brand = (site.get("name") or "onze organisatie").strip()
    dom = (site.get("base_url") or "").rstrip("/")
    serp_note = ""
    if serp_profile.get("dominant"):
        serp_note = (f"SERP-signaal: voor '{keyword}' domineren vooral "
                     f"{', '.join(serp_profile['dominant'])}. Sluit aan op de "
                     f"toon die daar werkt, maar wees authentiek.")

    system = (
        f"Je bent een senior redacteur voor {brand} ({dom}). "
        f"Schrijf in vlot, natuurlijk Nederlands. Geen marketingpraat, "
        f"geen clickbait, geen verzonnen feiten of bronnen. Wees concreet en "
        f"geef échte waarde. {serp_note}"
    )

    if asset_type == "reddit_post":
        user = (
            f"Schrijf een Reddit-post (title + body) over '{keyword}'.\n"
            f"Invalshoek: {angle or 'praktische ervaring / vraag aan de community'}.\n"
            f"Regels: géén self-promo in de titel, géén link bij de eerste post "
            f"( Reddit bestraft dat), wél een oprechte, specifieke bijdrage of "
            f"vraag waar mensen op reageren. Body max 280 woorden. "
            f"Geef terug als JSON: {{\"title\": \"...\", \"body\": \"...\"}}."
        )
    elif asset_type == "youtube_script":
        user = (
            f"Schrijf een YouTube-script-outline over '{keyword}' voor {brand}.\n"
            f"Invalshoek: {angle or 'heldere uitleg'}.\n"
            f"Geef terug als JSON: {{\"title\": \"...\", \"hook\": \"...\", "
            f"\"outline\": [\"stap 1\", ...], \"description\": \"...\"}}. "
            f"Title <60 tekens, description <155 tekens met het zoekwoord vooraan."
        )
    elif asset_type == "linkedin_article":
        user = (
            f"Schrijf een LinkedIn-artikel (kop + body) over '{keyword}' voor "
            f"{brand}.\nInvalshoek: {angle or 'professional inzicht'}.\n"
            f"Body max 400 woorden, start met een prikkelende zin, eindig met "
            f"een vraag om engagement. Geen emoji-bommen. "
            f"JSON: {{\"title\": \"...\", \"body\": \"...\"}}."
        )
    elif asset_type == "x_post":
        user = (
            f"Schrijf een korte X/Twitter-post over '{keyword}' voor {brand}.\n"
            f"Invalshoek: {angle or 'scherp inzicht'}.\n"
            f"Max 240 tekens, één heldere gedachte, geen hashtag-spam. "
            f"JSON: {{\"title\": \"\", \"body\": \"...\"}}."
        )
    elif asset_type == "aeo_snippet":
        user = (
            f"Schrijf een AEO-klaar antwoordblok over '{keyword}' voor {brand}.\n"
            f"Eis: een direct antwoord (40-60 woorden) dat de zoekintentie "
            f"beantwoordt, gevolgd door 3 FAQ-vragen mét korte antwoorden. "
            f"Geen verzonnen cijfers. JSON: {{\"title\": \"...\", "
            f"\"direct_answer\": \"...\", \"faq\": [{{\"q\":\"...\",\"a\":\"...\"}}]}}."
        )
    else:
        user = f"Schrijf een korte bijdrage over '{keyword}'."
    return system, user


def _parse_json(text: str) -> Dict:
    """Haal een JSON-object uit LLM-output (ook als er prose omheen zit)."""
    import json
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def generate_asset(asset_type: str, keyword: str, site: Dict,
                   angle: str = "", serp_profile: Optional[Dict] = None) -> Dict:
    """Genereer één asset. Geeft altijd een dict met status/score terug."""
    from ..publish.article_writer import _llm, check_ai_language
    from ..seo.enhancements import assess_seo_worldclass

    serp = serp_profile or {}
    system, user = _build_prompt(asset_type, keyword, site, angle, serp)
    try:
        raw = _llm(system, user, max_tokens=1200)
    except Exception as e:  # noqa: BLE001
        logger.error("[omni] generatie faalde voor %s/%s: %s", asset_type, keyword, e)
        return {"asset_type": asset_type, "platform": asset_type.replace("_", ""),
                "title": "", "body": "", "status": "error", "score": 0,
                "note": f"LLM-fout: {str(e)[:120]}"}

    data = _parse_json(raw)
    if not data:
        return {"asset_type": asset_type, "platform": asset_type.replace("_", ""),
                "title": "", "body": raw[:500], "status": "error", "score": 0,
                "note": "Geen geldige JSON van LLM"}

    # Normeer titel/body per type.
    if asset_type == "aeo_snippet":
        title = data.get("title", keyword)
        body = data.get("direct_answer", "")
        faq = data.get("faq", [])
        faq_html = "".join(
            f"<h3>{q.get('q','')}</h3><p>{q.get('a','')}</p>" for q in faq)
        html_body = f"<h1>{title}</h1><p>{body}</p>{faq_html}"
        score_obj = assess_seo_worldclass(html_body, keyword, site)
        score = score_obj.get("score", 0)
        note = "AEO-score {}/100".format(score)
        status = "staged" if score >= 70 else "needs_work"
    else:
        title = data.get("title", "") or data.get("hook", "") or keyword
        body = data.get("body", "") or data.get("description", "")
        # Social-post kwaliteit: geen AI-clichés, minimale lengte.
        cliches = check_ai_language(body)
        score = max(0, 100 - min(len(cliches), 3) * 8)
        note = ("clichés: " + ", ".join(cliches[:3])) if cliches else "clean"
        status = "staged" if len(body.split()) >= 15 else "needs_work"

    return {
        "asset_type": asset_type,
        "platform": asset_type.replace("_", ""),
        "title": title,
        "body": body,
        "status": status,
        "score": score,
        "note": note,
    }


def generate_for_keyword(keyword: str, site: Dict, angle: str = "",
                         owned_domains: Optional[List[str]] = None) -> Dict:
    """Volledige Omni-run: SERP analyseren + alle aanbevolen assets genereren.

    Dit is de entrypoint die de router/scheduler aanroept. Geen side-effects
    buiten de LLM-call en de cache; de router schrijft naar omni_queue.
    """
    serp = analyze_serp(keyword, owned_domains=owned_domains)
    assets = [generate_asset(a, keyword, site, angle, serp)
              for a in serp.get("recommended_assets", [])]
    return {"keyword": keyword, "serp": serp, "assets": assets}

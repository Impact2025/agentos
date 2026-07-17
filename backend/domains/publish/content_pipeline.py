"""
Content Pipeline — project-onafhankelijke schrijf- en publish-keten.

Kern van de 2x/week auto-publisher: kiest per site een onderwerp (uit de
Demand Engine-kansen), schrijft het artikel, laat het SEO-beoordelen en zo
nodig optimaliseren, genereert per-platform social copy + een quote-card-
afbeelding, en zet alles klaar in de `content_jobs`-wachtrij met status
`pending_review`. Er wordt NOOIT automatisch gepubliceerd of gepost — dat
gebeurt pas via `approve_and_publish()`, expliciet getriggerd door een mens
(zie `backend/domains/content_queue/router.py`).

In tegenstelling tot de oorspronkelijke WeAreImpact-only pijplijn
(`domains/projects/weareimpact.py`) zijn hier geen merk-specifieke prompts
hardcoded: de schrijfstijl komt uit de Obsidian-vault (`vault_reader
.get_core_context(project)`, de `[NNN] PROJECT_CORE/`-map per project) plus de
generieke SEO Copywriter/Editor-expertprofielen (`backend/expert/team.py`).
Zonder vault-context (bijv. Pootgelukkig, nog geen CORE-map) valt het terug op
een neutrale schrijfstijl-instructie.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn
from ..chat import hermes as hermes_service
from . import article_writer
from . import service as publish_service
from ..seo import engine as demand_engine
from ..seo import external_content as external_content_service
from ..seo import gsc as gsc_service
from ..seo import knowledge as knowledge_service
from ..seo import sites as sites_service
from ...shared import facebook as facebook_service
from ...shared import linkedin as linkedin_service
from ...shared import instagram as instagram_service
from ...shared import twitter as twitter_service
from ...shared.image_gen import generate_infographic, generate_quote_card

logger = logging.getLogger(__name__)

_FALLBACK_WRITE_PROMPT = (
    "Je bent een ervaren Nederlandse SEO-copywriter. Schrijf heldere, feitelijke, "
    "praktische content op B1-niveau. Geen AI-cliches, geen verzonnen cijfers of bronnen. "
    "SCHRIJF MINIMAAL 1000 WOORDEN — een wereldklasse-artikel heeft diepgang: uitgewerkte "
    "voorbeelden, herkenbare situaties uit de praktijk, een concreet stappenplan en per "
    "hoofdpunt verdiepende uitleg. Voeg GEEN vulling toe, maar behandel het onderwerp "
    "grondig en volledig. Structureer met H2/H3-koppen en een FAQ-sectie."
)
_FALLBACK_REVIEW_PROMPT = (
    "Je bent een strenge Nederlandse SEO-eindredacteur. Beoordeel het artikel eerlijk."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_activity(project: str, action: str, detail: str = "",
                  artifact: str = "", next_step: str = "", status: str = "ok") -> None:
    from ...shared.outcomes import log_outcome
    log_outcome(project, action, detail, artifact=artifact, next_step=next_step, status=status)


# ── Herbruikbare generatie-helpers ──────────────────────────────────────────

async def _stream_hermes(system: str, prompt: str, max_tokens: int = 2000,
                         purpose: str = "content") -> str:
    full = ""
    async for chunk in hermes_service.stream_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system,
        max_tokens=max_tokens,
        purpose=purpose,
    ):
        full += chunk
    return full.strip()


async def _llm(system: str, prompt: str, max_tokens: int = 2000,
               purpose: str = "content") -> str:
    """Beste beschikbare model voor schrijf-/reviewwerk: Claude eerst (direct
    of via OpenRouter), Hermes als terugval. De lage Wachtrij-scores kwamen
    grotendeels doordat een klein gratis model zowel schreef als beoordeelde."""
    from ..chat import claude as claude_service
    if claude_service.is_configured():
        try:
            out = (await claude_service.get_response(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=max_tokens,
                purpose=purpose,
            )).strip()
            if out:
                return out
        except Exception as e:
            logger.warning("[content-pipeline] Claude niet beschikbaar (%s) — terugval op Hermes", e)
    return await _stream_hermes_retry(system, prompt, max_tokens, purpose=purpose)


async def _stream_hermes_retry(system: str, prompt: str, max_tokens: int = 2000, retries: int = 2,
                               purpose: str = "content") -> str:
    """Zoals _stream_hermes, maar probeert opnieuw bij een lege response.

    Kleinere/budget-modellen geven soms een 200-response met 0 output-tokens
    terug op strikte JSON-prompts (geen exception, gewoon niks). Vooral de
    review/social-copy-stappen hebben betrouwbare output nodig omdat ze
    verderop geparsed worden — een enkele lege poging mag niet meteen
    terugvallen op de fallback-tekst."""
    for attempt in range(retries + 1):
        out = await _stream_hermes(system, prompt, max_tokens, purpose=purpose)
        if out:
            return out
        if attempt < retries:
            logger.warning("[content-pipeline] Lege Hermes-response (poging %s/%s), opnieuw proberen...",
                           attempt + 1, retries + 1)
            await asyncio.sleep(4)
    return ""


def slugify_title(title: str) -> str:
    slug = title.strip().lower() \
        .replace(" ", "-") \
        .replace("é", "e").replace("ë", "e").replace("è", "e") \
        .replace("á", "a").replace("à", "a") \
        .replace("í", "i").replace("ï", "i") \
        .replace("ó", "o").replace("ö", "o") \
        .replace("ú", "u").replace("ü", "u") \
        .replace("'", "").replace('"', "") \
        .replace("?", "").replace("!", "") \
        .replace(",", "").replace(".", "") \
        .replace(":", "").replace(";", "") \
        .replace("--", "-") \
        .strip("-")[:60]
    return re.sub(r"-+", "-", slug)


def _extract_json(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s


def _profile_prompt(name: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT system_prompt FROM agent_profiles WHERE name = ?", (name,)).fetchone()
    return row["system_prompt"] if row else ""


def _vault_context(project_name: str, keyword: str = "") -> str:
    parts = []
    try:
        from ...shared.vault_reader import VaultReader
        vr = VaultReader()
        if vr.is_configured:
            core = vr.get_core_context(project_name)
            if core:
                parts.append(core)
    except Exception:
        pass
    # NIEUW: verse NotebookLM-onderzoeksrapporten optellen als context
    # zodat blogs automatisch gevoed worden door diepte-onderzoek. Met een
    # `keyword` krijgen rapporten over dát zoekwoord voorrang (de Demand→
    # Researcher-brug grondt per kans; zonder deze match pakt een artikel
    # willekeurig de laatste rapporten i.p.v. zijn eigen onderzoek).
    try:
        from ...domains.researcher.service import _slugify  # noqa
        from ...shared.config import OBSIDIAN_VAULT_PATH
        from pathlib import Path
        kw_tokens = _slug_tokens(keyword)

        def _matches_keyword(md) -> bool:
            if not kw_tokens:
                return False
            stem_tokens = set(md.stem.lower().split("-"))
            return len(kw_tokens & stem_tokens) / len(kw_tokens) >= 0.5

        if OBSIDIAN_VAULT_PATH:
            vault = Path(OBSIDIAN_VAULT_PATH)
            # 1) project-specifiek: 10_Projects/{project}/onderzoek/
            if project_name:
                pdir = vault / "10_Projects" / project_name / "onderzoek"
                if pdir.exists():
                    mds = sorted(pdir.glob("*.md"))
                    matched = [m for m in mds if _matches_keyword(m)]
                    for md in (matched[-3:] or mds[-3:]):
                        parts.append(f"## Onderzoek ({md.stem})\n{md.read_text('utf-8', errors='ignore')[:2500]}")
            # 2) project-loos: 30_Resources/Onderzoek/
            rdir = vault / "30_Resources" / "Onderzoek"
            if rdir.exists():
                for md in sorted(rdir.glob("*.md"))[-2:]:
                    parts.append(f"## Onderzoek ({md.stem})\n{md.read_text('utf-8', errors='ignore')[:2000]}")
    except Exception:
        pass
    return "\n\n".join(parts)


def _iris_writing_guidance(project_name: str) -> str:
    """Kennisbank-principes van Iris (GEO/AEO/SEO/merk) voor de schrijf-agent.
    Defensief: geen kennis of fout = lege string, nooit een crash."""
    try:
        from ..iris import knowledge as iris_knowledge
        return iris_knowledge.guidance_for_writing(project_name)
    except Exception:
        return ""


def _skill_body(project_name: str) -> str:
    try:
        from ..projects.router import _find_project_dir
        project_dir = _find_project_dir(project_name)
        if project_dir:
            skill_path = project_dir / "SKILL.md"
            if skill_path.exists():
                return skill_path.read_text("utf-8")[:2000]
    except Exception:
        pass
    return ""


# ── Onderwerp kiezen ─────────────────────────────────────────────────────────

def _slug_tokens(text: str) -> set:
    return set(slugify_title(text or "").split("-")) - {""}


def _topic_already_covered(keyword: str, external_titles: List[Dict[str, str]]) -> bool:
    """True als `keyword` (vrijwel) identiek is aan een titel/slug die al écht
    op de site staat. Voorkomt dubbele content, maar is bewust strikt: een
    échte zoekintentie-variant (bv. 'levensverhaal laten schrijven KOSTEN' vs
    'levensverhaal vastleggen') telt NIET als covered — die verdient een eigen
    pagina. Alleen exacte slug-gelijkheid of >=80% token-overlap dismissed."""
    kw_slug = slugify_title(keyword)
    kw_tokens = _slug_tokens(keyword)
    if not kw_slug and not kw_tokens:
        return False
    for item in external_titles:
        ext_slug = slugify_title(item.get("slug") or item.get("title") or "")
        if not ext_slug:
            continue
        # Exacte slug-gelijkheid → altijd covered.
        if kw_slug and kw_slug == ext_slug:
            return True
        # Token-overlap: pas dismissen als >=80% van de keyword-tokens ook in de
        # externe titel zitten (en omgekeerd). Subset-match alleen is te bot:
        # 'kosten' / 'prijs' / 'ervaringen' zijn echte intentie-verschillen.
        ext_tokens = _slug_tokens(item.get("title") or "") | _slug_tokens(
            item.get("slug") or ""
        )
        if kw_tokens and ext_tokens:
            overlap = kw_tokens & ext_tokens
            if overlap and len(overlap) / len(kw_tokens) >= 0.8 \
                    and len(overlap) / len(ext_tokens) >= 0.8:
                return True
    return False


def select_topic(site: Dict) -> Optional[Dict]:
    """Kies het eerstvolgende onderwerp voor een site uit de Demand Engine-kansen.

    Pakt de kans met de hoogste opportunity_score en status 'new', en zet 'm op
    'in_progress' zodat een volgende run 'm niet opnieuw pakt. Geeft None als er
    geen kansen klaarstaan (bijv. nog geen scan gedraaid, of alles al in gebruik).

    Kansen die overlappen met content die al écht op de site staat (via
    `external_db_url`, buiten Agent OS' eigen `published_pages` om) worden
    overgeslagen en op 'dismissed' gezet i.p.v. verspild te worden aan een
    dubbel artikel."""
    kansen = demand_engine.list_opportunities(site_id=site["id"], status="new")
    if not kansen:
        return None
    # Externe CMS-DB (indien geconfigureerd) + live sitemap (zero-config) —
    # zodat ook content die buiten Agent OS om is gepubliceerd meetelt.
    external_titles = external_content_service.fetch_all_known_content(site)
    for kans in kansen:
        if external_titles and _topic_already_covered(kans["query"], external_titles):
            demand_engine.update_opportunity_status(kans["id"], "dismissed")
            _log_activity(site["name"], "kans-overgeslagen-dubbel",
                          f"'{kans['query']}' staat al op de site — overgeslagen i.p.v. dubbel geschreven.")
            continue
        demand_engine.update_opportunity_status(kans["id"], "in_progress")
        return kans
    return None


# ── Schrijven + SEO-review + optimaliseren ──────────────────────────────────

async def _write_article(site: Dict, keyword: str, angle: str, rationale: str) -> str:
    project_name = site["name"]
    vault_context = _vault_context(project_name, keyword)
    skill_body = _skill_body(project_name)
    base_prompt = _profile_prompt("SEO Copywriter") or _FALLBACK_WRITE_PROMPT

    existing_titles = [p["title"] for p in publish_service.list_pages(site["id"]) if p.get("title")]
    existing_titles += [t["title"] for t in external_content_service.fetch_external_titles(site) if t.get("title")]

    write_system = base_prompt
    if vault_context:
        write_system += f"\n\n## Merkcontext uit Obsidian vault (strikte regels)\n{vault_context[:4000]}"
    knowledge = knowledge_service.get_site_knowledge(site)
    if knowledge["profile"]:
        write_system += f"\n\n## Bedrijfsprofiel & USP's\n{knowledge['profile'][:2000]}"
    if knowledge["ctas"]:
        write_system += ("\n\n## Call-to-actions (verwerk er één natuurlijk)\n"
                         + "\n".join(f"- {c}" for c in knowledge["ctas"][:6]))
    # Iris' kennisbank: onderzoek dat Vincent aanleverde (GEO/AEO/SEO) bereikt
    # hier de schrijf-agent — zo stuurt de manager de agents inhoudelijk aan.
    iris_guidance = _iris_writing_guidance(project_name)
    if iris_guidance:
        write_system += f"\n\n## Kennisbank-principes (Iris — pas toe)\n{iris_guidance}"

    write_prompt = (
        f"Schrijf een compleet blogartikel voor {project_name}.\n\n"
        f"Kernzoekwoord: {keyword}\n"
        f"Invalshoek: {angle}\n"
        f"Rationale: {rationale}\n\n"
        f"Project-context (SKILL.md):\n{skill_body}\n\n"
        f"Bestaande artikelen (vermijd overlap): {', '.join(existing_titles[:15])}\n\n"
        "Lever ALLEEN de HTML-body zonder <html>/<head>/<body>. Gebruik <h1> voor de titel, "
        "<h2>/<h3> voor tussenkoppen, <p> voor alinea's, <ul>/<li> voor lijsten. "
        "Geen inline CSS of styles."
    )
    # 8000 tokens: een volledig artikel van 1200+ woorden inclusief HTML-markup
    # werd op 4000 regelmatig mid-zin afgekapt — de reviewer keurde dat terecht af.
    html_body = await _llm(write_system, write_prompt, max_tokens=8000)
    if not html_body or not html_body.strip():
        # Lege schrijf-response — geef een duidelijke fout zodat de meertraps-
        # generator netjes naar de single-shot-fallback terugvalt (en de batch
        # niet met een onverwachte None crasht).
        raise RuntimeError("Lege schrijf-response van het model voor '%s'" % keyword)
    return html_body


async def _review_article(site: Dict, keyword: str, html_body: str) -> Dict:
    review_system = _profile_prompt("SEO Editor") or _FALLBACK_REVIEW_PROMPT
    review_prompt = (
        f"Beoordeel onderstaand blogartikel voor {site['name']}.\n\n"
        f"Kernzoekwoord: {keyword}\n\n"
        "Houd de feedback beknopt: maximaal 6 genummerde punten van elk 1-2 zinnen.\n\n"
        f"ARTIKEL:\n{html_body}"
    )
    raw = await _llm(review_system, review_prompt, max_tokens=2500)
    if not raw:
        # Lege LLM-response (model gaf niks terug) — nooit crashen, geef een
        # veilige 0-score zodat het artikel de kwaliteitsgate niet per ongeluk
        # passeert en de batch niet stilvalt.
        return {"score": 0, "feedback": "Lege review-response van het model."}
    try:
        obj = json.loads(_extract_json(raw))
        score = max(0, min(100, int(round(float(obj.get("score", 50))))))
        feedback = str(obj.get("feedback") or "").strip()
    except Exception:
        # JSON kapot (meestal: afgekapte lange feedback — de score staat vooraan
        # en is dan meestal nog intact; regex-redding zodat een geldig oordeel
        # niet verloren gaat). Geen score vindbaar → 0, zodat het artikel NOOIT
        # per ongeluk door de kwaliteitsgate glipt (voorheen: stille 50).
        safe_raw = raw or ""
        m = re.search(r'"score"\s*:\s*(\d{1,3})', safe_raw)
        score = max(0, min(100, int(m.group(1)))) if m else 0
        fm = re.search(r'"feedback"\s*:\s*"(.*)', safe_raw, re.DOTALL)
        feedback = (fm.group(1).strip() if fm else safe_raw)[:800]

    # ── Deterministische E-E-A-T / AEO-correctie ────────────────────────────
    # Voorkomt dat een te milde LLM-beoordeling een niet-wereldklasse-artikel
    # door de gate loopt. We trekken alleen af (nooit op), en alleen op harde,
    # parseerbare tekortkomingen. Draait op ELKE review — zowel de schone
    # JSON-parse als de fallback-bocht — zodat de gate altijd sluit. (Voorheen
    # stond deze correctie per ongeluk binnen de except-branch en werd hij bij
    # een wél-geldige review nooit uitgevoerd, waardoor te zachte scores de
    # kwaliteitsgrens passeerden.)
    try:
        from ..seo.enhancements import assess_seo_worldclass
        a = assess_seo_worldclass(html_body, keyword, site)
        deductions: List[str] = []
        if not a["has_direct_answer"]:
            score -= 5; deductions.append("geen direct antwoord voor AEO")
        if a["faq_count"] == 0:
            score -= 5; deductions.append("geen FAQ-sectie (rich result)")
        if a["ai_language"]:
            score -= min(len(a["ai_language"]), 3) * 4
            deductions.append("AI-clichés aanwezig")
        if a["ee_at_issues"]:
            score -= min(len(a["ee_at_issues"]), 4) * 5
            deductions.append("E-E-A-T/bronissue: " + "; ".join(a["ee_at_issues"][:2]))
        score = max(0, min(100, score))
        if deductions and not feedback:
            feedback = "Deterministische SEO-check: " + "; ".join(deductions)
        elif deductions:
            feedback = feedback + " | Deterministische SEO-check: " + "; ".join(deductions)
    except Exception as e:
        logger.debug("[content-pipeline] E-E-A-T-correctie overgeslagen: %s", str(e)[:120])

    return {"score": score, "feedback": feedback}


async def review_and_improve(site: Dict, keyword: str, html_body: str,
                             max_rounds: int = 6) -> tuple:
    """Review → verbeter → review, net zo lang tot de kwaliteitsgate
    (CONTENT_MIN_SCORE) is gehaald. Nooit eerder opgeven dan nodig: een artikel
    onder de grens mag het dashboard (en Vincents ogen) niet bereiken — de agent
    blijft zélf aanscherpen. `max_rounds` is alleen een harde veiligheidslimiet
    (tegen eindeloze LLM-loops); bij de biweekly/regenerate-routes wordt die
    ruim gezet (zie CONTENT_MAX_ROUNDS) zodat de grens in de praktijk wél gehaald
    wordt. Retourneert (html_body, review)."""
    from ...shared.config import CONTENT_MIN_SCORE, CONTENT_MAX_ROUNDS
    effective_max = max(max_rounds, CONTENT_MAX_ROUNDS)
    review = await _review_article(site, keyword, html_body)
    if not review:
        # _review_article gaf None terug (model-fout) — val niet stil, geef een
        # minimale review zodat het artikel alsnog de wachtrij in gaat.
        review = {"score": 0, "feedback": "Review kon niet worden uitgevoerd."}
    best_html, best_review = html_body, review
    rounds = 0
    while review["score"] < CONTENT_MIN_SCORE and rounds < effective_max and review["feedback"]:
        # Circuit-breaker midden in de verbeter-loop: als de provider-quota
        # (of het dagbudget) opraakt, stoppen we direct — anders blijft één
        # run de hele quota leegzuigen (incident 2026-07-10/11: 46 calls in
        # 32 min, escalerend). Elke ronde is 2 LLM-calls.
        from ...shared.outcomes import llm_budget_exceeded
        if llm_budget_exceeded():
            logger.warning(
                "[content-pipeline] LLM-budget op midden in verbeter-loop (%s rondes, "
                "score %s) — stop, behoud beste versie.", rounds, best_review["score"]
            )
            break
        rounds += 1
        logger.info("[content-pipeline] Verbeterronde %s (score %s < %s) — %s",
                    rounds, review["score"], CONTENT_MIN_SCORE, site["name"])
        html_body = await _optimize_article(site, keyword, html_body, review["feedback"])
        # Een herschrijfronde kan gevalideerde interne links laten vallen of nieuwe
        # verzinnen — die zijn dan niet meer gevet, dus opnieuw wieden vóór de
        # volgende beoordeling (anders belanden 404-links op de live site).
        html_body, n_stripped = article_writer.strip_unvetted_internal_links(html_body, site)
        if n_stripped:
            logger.info("[content-pipeline] Optimalisatieronde %s: %d ongevette interne link(s) verwijderd",
                        rounds, n_stripped)
        review = await _review_article(site, keyword, html_body)
        # Houd de beste versie vast: een herschrijfronde kan ook verslechteren,
        # en dan willen we niet de mindere laatste versie opleveren.
        if review["score"] > best_review["score"]:
            best_html, best_review = html_body, review
    if best_review["score"] < CONTENT_MIN_SCORE:
        # Ook na de maximale verbeterrondes nog onder de grens: log het expliciet
        # en laat de learning-loop er een les van trekken (zie record_under85).
        logger.warning("[content-pipeline] Artikel '%s' blijft onder grens na %s rondes "
                       "(beste score %s < %s) — naar needs_work, agent leert hiervan.",
                       keyword, rounds, best_review["score"], CONTENT_MIN_SCORE)
        try:
            from ...domains.publish import learning
            learning.record_under85(site, keyword, best_review)
        except Exception as e:
            logger.debug("[content-pipeline] learning.record_under85 overgeslagen: %s", str(e)[:120])
    return best_html, best_review


async def _optimize_article(site: Dict, keyword: str, html_body: str, feedback: str) -> str:
    optimize_system = (_profile_prompt("SEO Editor") or _FALLBACK_REVIEW_PROMPT) + (
        "\n\nJe herschrijft nu zelf het artikel op basis van je eigen feedback — lever "
        "de verbeterde HTML-body, geen JSON, geen beoordeling."
    )
    prompt = (
        f"Herschrijf dit artikel voor {site['name']} zodat het de onderstaande feedback "
        f"verwerkt. Behoud toon en stijl.\n\nFeedback:\n{feedback}\n\n"
        f"Kernzoekwoord: {keyword}\n\nORIGINEEL:\n{html_body}\n\n"
        "Lever ALLEEN de verbeterde HTML-body zonder <html>/<head>/<body>."
    )
    out = await _llm(optimize_system, prompt, max_tokens=16000)
    return out if len(out) > 50 else html_body


async def _write_article_best(site: Dict, keyword: str, angle: str,
                              rationale: str) -> tuple:
    """Meertraps-generator (outline → secties → opmaak → links → QC) met
    terugval op de single-shot-schrijver als de pipeline stukloopt.
    Retourneert (html_body, qc_report, case_study_id)."""
    knowledge = knowledge_service.get_site_knowledge(site)
    case_study = knowledge_service.match_case_study(site["id"], keyword, angle)
    # Iris' kennisbank-principes mee in de merkcontext, zodat de meertraps-
    # schrijver (outline → secties) de GEO/AEO/SEO-kennis vanaf de eerste stap toepast.
    brand_context = _vault_context(site["name"], keyword)
    iris_guidance = _iris_writing_guidance(site["name"])
    if iris_guidance:
        brand_context = (brand_context + "\n\n## Kennisbank-principes (Iris — pas toe)\n"
                         + iris_guidance).strip()
    try:
        html_body, qc_report = await article_writer.write_article_staged(
            site, keyword, angle, rationale,
            case_study=case_study,
            profile=knowledge["profile"], ctas=knowledge["ctas"],
            brand_context=brand_context,
            base_style_prompt=_profile_prompt("SEO Copywriter") or _FALLBACK_WRITE_PROMPT,
        )
        return html_body, qc_report, (case_study or {}).get("id", "")
    except Exception as e:
        logger.warning("[content-pipeline] Meertraps-generator mislukt (%s) — "
                       "terugval op single-shot-schrijver", e)
        try:
            html_body = await _write_article(site, keyword, angle, rationale)
        except Exception as e2:
            logger.error("[content-pipeline] Ook single-shot-schrijver faalde voor "
                         "'%s': %s", keyword, e2)
            raise
        # De single-shot-schrijver doorloopt nooit _link_pass — de LLM kan dus
        # ongehinderd interne URL's verzinnen. Zonder deze wied-stap belandden
        # die hallucinaties (bv. /iris, /avg-zorg) rechtstreeks live als 404's.
        if html_body:
            html_body, n_stripped = article_writer.strip_unvetted_internal_links(html_body, site)
            if n_stripped:
                logger.info("[content-pipeline] Single-shot-fallback: %d ongevette interne link(s) verwijderd",
                            n_stripped)
        return html_body, {"staged": False, "fallback_reason": str(e)[:200]}, ""


def _extract_title(html_body: str, fallback: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_body, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return fallback


# ── Social copy + afbeelding ─────────────────────────────────────────────────

async def _generate_social_copy(site: Dict, title: str, keyword: str, html_body: str) -> Dict[str, str]:
    system = _profile_prompt("Social Media Copywriter")
    vault_context = _vault_context(site["name"])
    if vault_context:
        system += f"\n\n## Merkcontext uit Obsidian vault\n{vault_context[:2500]}"

    plain = re.sub(r"<[^>]+>", " ", html_body)
    plain = re.sub(r"\s+", " ", plain).strip()[:3000]

    prompt = (
        f"Titel: {title}\nKernzoekwoord: {keyword}\n\nArtikel (platte tekst):\n{plain}"
    )
    raw = await _llm(system, prompt, max_tokens=1500)
    try:
        obj = json.loads(_extract_json(raw))
        return {
            "linkedin": str(obj.get("linkedin") or "").strip(),
            "facebook": str(obj.get("facebook") or "").strip(),
            "instagram": str(obj.get("instagram") or "").strip(),
            "twitter": str(obj.get("twitter") or "").strip(),
        }
    except Exception:
        logger.warning("Social-copy JSON-parse mislukt, val terug op titel-only captions")
        return {"linkedin": title, "facebook": title, "instagram": title, "twitter": title[:260]}


async def _generate_article_infographic(site: Dict, title: str, keyword: str,
                                        html_body: str) -> Optional[bytes]:
    """Vat het artikel samen in 5-7 infographic-blokken en render die als PNG
    (1080x1350, per-project stijl). Onderdeel van de 'all bases covered'-
    aanpak: de afbeelding gaat bij goedkeuring mee de pagina in en rankt in
    Google Afbeeldingen. Faalt zacht (None) — een artikel zonder infographic
    is gewoon publiceerbaar."""
    plain = re.sub(r"<[^>]+>", " ", html_body)
    plain = re.sub(r"\s+", " ", plain).strip()[:3000]
    system = (
        "Je bent een Nederlandstalige infographic-ontwerper. Vat het artikel samen in "
        "kernpunten. Antwoord UITSLUITEND met JSON, geen markdown of uitleg:\n"
        '{"title": "infographic-titel (max 60 tekens)", "blocks": [{"heading": "korte kop '
        '(max 40 tekens)", "text": "één concrete boodschap of tip (max 90 tekens)"}]}\n'
        "Precies 5 tot 7 blocks. Feitelijk, B1-niveau, alleen punten die écht in het "
        "artikel staan — geen verzonnen cijfers of bronnen."
    )
    prompt = f"Titel: {title}\nKernzoekwoord: {keyword}\n\nArtikel (platte tekst):\n{plain}"
    try:
        raw = await _llm(system, prompt, max_tokens=1200)
        obj = json.loads(_extract_json(raw))
        blocks = [b for b in (obj.get("blocks") or [])
                  if isinstance(b, dict) and (b.get("heading") or b.get("text"))][:7]
        if len(blocks) < 3:
            raise ValueError(f"te weinig blokken ({len(blocks)})")
        info_title = str(obj.get("title") or title).strip()
        return generate_infographic(info_title, blocks, site["name"])
    except Exception as e:
        logger.warning("[content-pipeline] Infographic genereren mislukt voor '%s': %s",
                       title, str(e)[:200])
        return None


# ── Content-jobs CRUD ────────────────────────────────────────────────────────

def create_job(site_id: str, title: str, keyword: str, rationale: str, blog_html: str,
                seo_score: float, social_copy: Dict[str, str], image_bytes: Optional[bytes],
                slug: str, status: str = "pending_review",
                qc_report: Optional[Dict] = None, case_study_id: str = "",
                infographic_bytes: Optional[bytes] = None,
                dedupe: bool = True) -> str:
    """status 'pending_review' = klaar om goed te keuren (score ≥ gate);
    'needs_work' = onder de kwaliteitsgate — eerst verbeteren of afwijzen.

    dedupe=True (default): als er al een job voor (site_id, slug) bestaat met
    status in ('pending_review','needs_work','published','approved','publish_failed') wordt die
    bijgewerkt in plaats van een nieuwe rij aangemaakt. Voorkomt dat een
    content-goal in een oneindige loop hetzelfde artikel tientallen keren in de
    wachtrij dumpt (zie de 17x 'gelukkige hond'-incident)."""
    import base64
    with get_conn() as conn:
        if dedupe:
            existing = conn.execute(
                "SELECT id, status FROM content_jobs "
                "WHERE site_id=? AND slug=? AND status IN "
                "('pending_review','needs_work','published','approved','publish_failed') "
                "ORDER BY created_at DESC LIMIT 1",
                (site_id, slug),
            ).fetchone()
            if existing:
                # Bijwerken: nieuwe body/score, status naar meegegeven waarde
                # (behalve als de bestaande al 'published' is — dan niet
                # terugzetten naar pending_review).
                new_status = status
                if existing["status"] == "published" and status != "published":
                    new_status = "published"
                conn.execute(
                    "UPDATE content_jobs SET title=?, keyword=?, rationale=?, "
                    "status=?, blog_html=?, seo_score=?, social_copy=?, slug=?, "
                    "qc_report=?, case_study_id=? WHERE id=?",
                    (title, keyword, rationale, new_status, blog_html, seo_score,
                     json.dumps(social_copy), slug,
                     json.dumps(qc_report or {}, ensure_ascii=False),
                     case_study_id, existing["id"]),
                )
                return existing["id"]

        job_id = str(uuid.uuid4())
        image_path = base64.b64encode(image_bytes).decode("ascii") if image_bytes else ""
        infographic_path = base64.b64encode(infographic_bytes).decode("ascii") if infographic_bytes else ""
        conn.execute(
            """INSERT INTO content_jobs
               (id, site_id, title, keyword, rationale, status, blog_html, seo_score,
                social_copy, image_path, slug, publish_result, qc_report, case_study_id,
                infographic_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?)""",
            (job_id, site_id, title, keyword, rationale, status, blog_html, seo_score,
             json.dumps(social_copy), image_path, slug,
             json.dumps(qc_report or {}, ensure_ascii=False), case_study_id,
             infographic_path, _now()),
        )
    return job_id


def get_job(job_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM content_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(site_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
    clauses, params = [], []
    if site_id:
        clauses.append("site_id = ?")
        params.append(site_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM content_jobs{where} ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(r) for r in rows]


def _update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE content_jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


# ── Genereer één content-job voor één site ──────────────────────────────────

async def generate_content_job(site: Dict, keyword: Optional[str] = None,
                                angle: str = "", rationale: str = "",
                                light_mode: bool = False) -> Optional[str]:
    """Schrijf + review + social copy + afbeelding voor één site, opslaan als
    pending_review content_job. Retourneert het job-id, of None als er geen
    onderwerp beschikbaar was.

    light_mode=True: alleen schrijven + één lichte review (geen verbeterrondes,
    geen social/infographic). Bedoeld voor de batch-blitz op trage cloud-LLM's
    (OpenModel) waar de volledige cyclus per artikel te lang duurt.
    """
    if keyword is None:
        topic = select_topic(site)
        if not topic:
            _log_activity(site["name"], "auto-content-overslagen",
                          "Geen nieuwe kansen — voer eerst een Demand Engine-scan uit.")
            return None
        keyword, angle, rationale = topic["query"], topic.get("angle", ""), topic.get("rationale", "")

    logger.info("[content-pipeline] Schrijven — %s / '%s'", site["name"], keyword)
    html_body, qc_report, case_study_id = await _write_article_best(site, keyword, angle, rationale)
    if not html_body.strip():
        _log_activity(site["name"], "auto-content-mislukt", f"Lege schrijf-response voor '{keyword}'",
                      status="error")
        return None

    html_body, review = await review_and_improve(site, keyword, html_body,
                                                  max_rounds=0 if light_mode else 3)

    title = _extract_title(html_body, fallback=angle or keyword)
    slug = slugify_title(title)

    if light_mode:
        # Snel pad voor batch-blitz op trage cloud-LLM: alleen schrijven +
        # review, geen social/infographic. Een artikel dat de kwaliteitsgrens
        # (CONTENT_MIN_SCORE) niet haalt, wordt als 'needs_work' aangeboden —
        # nooit als 'pending_review' (publiceerbaar voorstel). "Alleen voorstellen
        # boven de 85%" betekent: onder de grens staat het in de verbeter-queue,
        # niet in de goedkeuringsqueue.
        from ...shared.config import CONTENT_MIN_SCORE
        passed = review["score"] >= CONTENT_MIN_SCORE
        social_copy: Dict[str, str] = {}
        image_bytes = None
        infographic_bytes = None
        create_job(site["id"], title, keyword, rationale, html_body,
                    review["score"], social_copy, image_bytes, slug,
                    status="pending_review" if passed else "needs_work",
                    qc_report=qc_report,
                    case_study_id=case_study_id)
        _log_activity(site["name"], "auto-content-klaar",
                      f"'{title}' (SEO-score {review['score']}) klaar voor review [light-mode]"
                      + ("" if passed else f" — onder kwaliteitsgrens {CONTENT_MIN_SCORE}, naar needs_work"),
                      next_step=("Keur goed of wijs af in de Wachtrij"
                                 if passed else "Laat de agent eerst verbeteren"))
        # Haal het zojuist aangemaakte job-id op voor batch-tracking
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM content_jobs WHERE site_id=? AND slug=? "
                "ORDER BY created_at DESC LIMIT 1",
                (site["id"], slug),
            ).fetchone()
        return row["id"] if row else None

    # ── Volledige modus: social copy + quote-card + infographic, dan opslaan ──
    # (light_mode retourneert hierboven; deze tak draait alleen voor de volledige
    # cyclus die de UI-knop, de biweekly scheduler en Iris' content_run gebruiken.)
    social_copy = await _generate_social_copy(site, title, keyword, html_body)
    image_bytes = generate_quote_card(title, site["name"])

    from ...shared.config import CONTENT_MIN_SCORE
    passed = review["score"] >= CONTENT_MIN_SCORE
    # Infographic alleen voor artikelen die de gate halen — anders verspilde LLM-calls.
    infographic_bytes = (await _generate_article_infographic(site, title, keyword, html_body)
                         if passed else None)
    job_id = create_job(site["id"], title, keyword, rationale, html_body,
                        review["score"], social_copy, image_bytes, slug,
                        status="pending_review" if passed else "needs_work",
                        qc_report=qc_report, case_study_id=case_study_id,
                        infographic_bytes=infographic_bytes)
    if passed:
        _log_activity(site["name"], "auto-content-klaar",
                      f"'{title}' (SEO-score {review['score']}) klaar voor review",
                      next_step="Keur goed of wijs af in de Wachtrij")
    else:
        _log_activity(site["name"], "auto-content-onder-grens",
                      f"'{title}' haalde na verbeterrondes {review['score']}/100 "
                      f"(grens {CONTENT_MIN_SCORE}) — niet publiceerbaar",
                      next_step="Laat de agent het opnieuw proberen of wijs af (Actiecentrum)")
    return job_id



# ── Listicle-instroom vanuit Mission Radar ──────────────────────────────────

def _split_listicle_meta(md_text: str) -> tuple:
    """Splits het meta-blok (meta-titel + meta-description, per prompt-eis
    onderaan de AEO-listicle) van de body af. Retourneert (body_md, meta_title).
    Als er geen meta-blok in de tweede helft staat, blijft de tekst intact."""
    last = None
    for m in re.finditer(r"(?im)^.{0,10}meta[- ]?titel\b.*$", md_text):
        last = m
    if not last or last.start() < len(md_text) * 0.5:
        return md_text, ""
    body = md_text[:last.start()].rstrip(" \n\r-*#")
    tail = md_text[last.start():]
    # "(max 60 tekens)" e.d. zijn geëchode prompt-eisen, geen titelwaarde.
    title_m = re.search(r"(?i)meta[- ]?titel\s*(?:\([^)]*\))?\s*[:*_\-]*\s*(.+)", tail)
    meta_title = re.sub(r"[*_`#]", "", title_m.group(1)).strip() if title_m else ""
    if re.fullmatch(r"\([^)]*\)", meta_title):
        meta_title = ""
    return body, meta_title


# ── Meta/suggestie-blokken uit de body zuiveren (voor publicatie) ───────────
# De SEO Copywriter-prompt dwingt de AI om meta-data en interne-link-suggesties
# onderaan de body te zetten. Die horen NIET in de leesbare body — meta gaat
# naar de head/DB-velden (meta_title/meta_description), de interne-link-
# suggesties zijn een schrijfhulp die we weggooien (de échte interne links
# zitten inline in de tekst). In de praktijk komen vier varianten voor:
#   1. HTML-commentaar met dubbele punt:  <!-- Meta-titel: tekst -->
#   2. HTML-commentaar attribuutvorm:     <!--META title="..." description="..."-->
#   3. Zichtbare paragraaf:  <p><strong>Meta-titel:</strong> tekst</p>
#   4. Zichtbare kop:        <h2>Meta-titel</h2><p>tekst</p>
# Deze functie haalt ze allemaal uit de body en geeft de gevonden meta-waarden
# terug, zodat de publish-route ze naar de juiste velden kan routeren.
_META_BLOCK_RE = re.compile(
    r"<h2[^>]*>\s*(?:meta[- ]?titel|meta[- ]?title|meta[- ]?beschrijving|"
    r"meta[- ]?description|suggesties? (?:voor )?interne links?)\s*</h2>.*?"
    r"(?=<h2|$)"
    r"|<h3[^>]*>\s*(?:meta[- ]?titel|meta[- ]?title|meta[- ]?beschrijving|"
    r"meta[- ]?description|suggesties? (?:voor )?interne links?)\s*</h3>.*?"
    r"(?=<h2|<h3|$)"
    r"|<!--[^-]*\bmeta[- ]?(?:titel|title|beschrijving|description)[^-]*-->"
    r"|<p>\s*<strong>\s*meta[- ]?(?:titel|title|beschrijving|description)\s*:"
    r".*?</p>",
    re.IGNORECASE | re.DOTALL,
)
# HTML-commentaar attribuutvorm: <!--META title="..." description="..."-->
_META_COMMENT_ATTR_RE = re.compile(
    r"<!\\s*--\\s*META\s+title=\"([^\"]*)\"\s+description=\"([^\"]*)\"\\s*--\\s*>",
    re.IGNORECASE | re.DOTALL,
)
# HTML-commentaar dubbele-puntvorm: <!-- Meta-titel: tekst --> / <!-- Meta-description: tekst -->
_META_COMMENT_COLON_RE = re.compile(
    r"<!\\s*--\\s*meta[- ]?(titel|title|beschrijving|description)\s*:\\s*(.*?)\\s*--\\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Zichtbare paragraaf: <p><strong>Meta-titel:</strong> tekst</p>
_META_P_COLON_RE = re.compile(
    r"<p>\s*<strong>\s*meta[- ]?(titel|title|beschrijving|description)\s*:\s*</strong>"
    r"\s*(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
# Zichtbare kop: <h2>Meta-titel</h2><p>tekst</p>
_META_H_COLON_RE = re.compile(
    r"<h[23][^>]*>\s*meta[- ]?(titel|title|beschrijving|description)\s*</h[23]>\s*<p>(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_meta_and_suggestions(html_body: str) -> tuple:
    """Verwijdert alle Meta-/Suggestie-blokken uit de body en geeft
    (schone_html, meta_title, meta_description) terug. Als er geen blokken
    staan, blijft de body ongewijzigd."""
    if not html_body:
        return html_body, "", ""
    meta_title = ""
    meta_desc = ""

    # 1) attribuutvorm commentaar (nieuwe stijl)
    mc = _META_COMMENT_ATTR_RE.search(html_body)
    if mc:
        meta_title = mc.group(1).strip()
        meta_desc = mc.group(2).strip()
        html_body = html_body.replace(mc.group(0), "")

    # 2) dubbele-punt commentaar + 3) zichtbare <p> + 4) zichtbare <h2>/<h3>
    for kind, val in _META_COMMENT_COLON_RE.findall(html_body):
        if "titel" in kind.lower() or "title" in kind.lower():
            meta_title = meta_title or val.strip()
        else:
            meta_desc = meta_desc or val.strip()
    html_body = _META_COMMENT_COLON_RE.sub("", html_body)

    for kind, val in _META_P_COLON_RE.findall(html_body):
        if "titel" in kind.lower() or "title" in kind.lower():
            meta_title = meta_title or val.strip()
        else:
            meta_desc = meta_desc or val.strip()
    html_body = _META_P_COLON_RE.sub("", html_body)

    for kind, val in _META_H_COLON_RE.findall(html_body):
        if "titel" in kind.lower() or "title" in kind.lower():
            meta_title = meta_title or val.strip()
        else:
            meta_desc = meta_desc or val.strip()
    html_body = _META_H_COLON_RE.sub("", html_body)

    # Laatste vangnet: elke resterende zichtbare/suggestie-kop
    cleaned = _META_BLOCK_RE.sub("", html_body).strip()
    return cleaned, meta_title, meta_desc


# ── Centrale publish-cleaner ──────────────────────────────────────────────
# Verwijdert zowel Meta-/Suggestie-blokken als eventuele ```html ... ```
# code-fences uit de body vóórdat die naar de live site gaat. Zonder deze
# stap belandt een code-fence (die de schrijver soms meelevert) letterlijk
# zichtbaar op de pagina — bv. een naakte "html" midden in de intro.
_CODE_FENCE_RE = re.compile(
    r"```[a-z]*\s*"          # opening fence + optionele taal-tag (html/md/...)
    r"(.*?)"                  # de echte inhoud (non-greedy)
    r"```",                   # sluitende fence
    re.IGNORECASE | re.DOTALL,
)


def _unwrap_code_fence(html_body: str) -> str:
    """Haal een eventuele ```html ... ``` (of ``` ... ```) code-fence eraf.

    Robuust tegen varianten die de oude regex miste: de taal-tag kan direct
    gevolgd worden door een spatie i.p.v. een newline (bv. '```html <h1>').
    Als er géén fence staat, blijft de input ongewijzigd.

    BUG-FIX: de oude implementatie zocht de sluitende fence via
    ``s.rfind("\\n```")`` — maar de gegenereerde HTML bevat ZÉLF een
    ``\\n``` `` binnen de JSON-LD/script (bv. in een code-voorbeeld), waardoor
    het artikel abrupt werd afgekapt bij die valse sluiting. Nieuwe regel:
    een fence wordt alleen als zodanig herkend als hij écht VOORAAN staat én
    (correct) aan het EINDE sluit. Staat de openende fence er wel maar sluit
    de generator hem niet (geen `` ``` `` aan het eind), dan strippen we
    alleen de openende fence en houden de rest intact — we kappen nooit
    middenin de content af op een valse ``\\n``` ``.
    """
    s = (html_body or "").strip()
    # Opening: '```' + optionele taal-tag + willekeurige witruimte (incl. spatie)
    m = re.match(r"^```[a-z]*\s*", s, re.IGNORECASE)
    if not m:
        return s  # geen fence -> ongewijzigd
    body = s[m.end():]
    # Sluiting: alleen een ECHTE fence telt, d.w.z. aan het allerlaatste eind.
    if body.endswith("```"):
        body = body[:-3].rstrip()
    # (geen sluiting aan het eind -> generator zette enkel de openende fence;
    #  de eventuele '\\n```' binnen de content is geen echte sluiting -> negeren)
    return body.strip()


def _smart_truncate(text: str, max_len: int) -> str:
    """Kap af op een woordgrens en voeg een ellipsis toe (geen '... woordE')."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.5:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


def _strip_duplicate_header(html: str) -> str:
    """Verwijder het dubbele headerblok dat sommige generators bovenaan de
    body zetten. De blogpagina rendert titel, datum en samenvatting ZELF uit
    de DB, dus een eigen <h1>, 'Door ... Gepubliceerd op ...' byline en
    '<strong>Samenvatting:</strong>'-paragraaf moeten eruit."""
    if not html:
        return html
    out = html.strip()
    # 1) HTML-comment meta-blok (<!-- Meta-titel: ... -->) bovenaan.
    out = re.sub(r"^\s*<!--[\s\S]*?-->\s*", "", out, flags=re.I)
    # 2) Eerste <h1>...</h1> (site toont titel zelf).
    out = re.sub(r"^\s*<h1\b[^>]*>[\s\S]*?<\/h1>\s*", "", out, flags=re.I)
    # 3) Byline-paragraaf met "Gepubliceerd op".
    out = re.sub(
        r"^\s*<p\b[^>]*>(?:(?!<\/p>)[\s\S])*?gepubliceerd op[\s\S]*?<\/p>\s*",
        "",
        out,
        flags=re.I,
    )
    # 4) Samenvatting-paragraaf (tekst wordt elders als excerpt gebruikt).
    out = re.sub(
        r"^\s*<p\b[^>]*>\s*(?:<strong>)?\s*samenvatting\s*:?\s*(?:<\/strong>)?\s*[\s\S]*?<\/p>\s*",
        "",
        out,
        flags=re.I,
    )
    # 5) Losse <hr> aan het begin.
    out = re.sub(r"^\s*(?:<hr\s*/?>\s*)+", "", out, flags=re.I)
    # 6) Generator-meta-regels bovenaan de body (overblijfselen zoals
    #    "Publicatiedatum: 15 juli 2026 Project: WeAreImpact Auteur: …").
    #    Deze horen niet in de leesbare tekst en verpesten anders de excerpt.
    for _ in range(4):
        before = out
        out = re.sub(
            r"^\s*<p\b[^>]*>\s*(?:publicatiedatum|project|auteur|datum|door)\b[^\n<]*?</p>\s*",
            "",
            out,
            flags=re.I,
        )
        out = re.sub(
            r"^\s*(?:publicatiedatum|project|auteur|datum)\s*:\s*[^\n]*\n",
            "",
            out,
            flags=re.I,
        )
        if out == before:
            break
    return out.strip()


def clean_for_publish(html_body: str) -> str:
    """Volledige reiniging vóór publicatie: code-fences én meta/suggestie-blokken."""
    if not html_body:
        return html_body
    # 1) code-fences eraf (kan meerdere keren voorkomen)
    stripped = _CODE_FENCE_RE.sub(r"\1", html_body.strip())
    stripped = _unwrap_code_fence(stripped)
    # 2) meta/suggestie-blokken eraf
    cleaned, _, _ = _strip_meta_and_suggestions(stripped)
    return cleaned


async def create_job_from_listicle(site: Dict, keyword: str, rationale: str,
                                   listicle_md: str) -> str:
    """Zet een afgeronde AEO-listicle (Mission Radar → conveyor-concept) om in
    een content_job in de wachtrij: markdown → HTML, SEO-review (+ optimalisatie
    onder de 75), social copy en quote-card. Publiceren gebeurt — zoals overal —
    pas na menselijke goedkeuring via approve_and_publish()."""
    import markdown as md_lib

    body_md, meta_title = _split_listicle_meta(listicle_md)
    html_body = md_lib.markdown(body_md, extensions=["tables", "sane_lists"]).strip()
    if not html_body:
        raise ValueError("Listicle-tekst is leeg — niets om in de wachtrij te zetten.")

    html_body, review = await review_and_improve(site, keyword, html_body)

    title = _extract_title(html_body, fallback=meta_title or keyword)
    if "<h1" not in html_body.lower():
        # De publish-templates verwachten een H1 in de body (zelfde eis als
        # _write_article); listicles beginnen soms direct met H2's.
        html_body = f"<h1>{title}</h1>\n{html_body}"
    slug = slugify_title(title)

    social_copy = await _generate_social_copy(site, title, keyword, html_body)
    image_bytes = generate_quote_card(title, site["name"])

    from ...shared.config import CONTENT_MIN_SCORE
    passed = review["score"] >= CONTENT_MIN_SCORE
    infographic_bytes = (await _generate_article_infographic(site, title, keyword, html_body)
                         if passed else None)
    job_id = create_job(site["id"], title, keyword, rationale, html_body,
                        review["score"], social_copy, image_bytes, slug,
                        status="pending_review" if passed else "needs_work",
                        infographic_bytes=infographic_bytes)
    _log_activity(site["name"], "radar-listicle-in-wachtrij",
                  f"'{title}' (SEO-score {review['score']}) vanuit Mission Radar "
                  + ("klaar voor review" if passed else f"onder kwaliteitsgrens {CONTENT_MIN_SCORE} — eerst verbeteren"))
    return job_id


# ── 2x/week scheduler-job ───────────────────────────────────────────────────

def _batch_size(site: Dict) -> int:
    """Aantal artikelen per run voor een site (1-10, default 1)."""
    try:
        return max(1, min(10, int(site.get("content_batch_size") or 1)))
    except (TypeError, ValueError):
        return 1


async def run_content_batch(site: Dict, count: Optional[int] = None,
                             light_mode: bool = False) -> List[str]:
    """Genereer `count` content-jobs voor één site (default: de site-instelling
    content_batch_size). Sequentieel — kostenbeheersing en rate-limits. Stopt
    zodra de Demand Engine-kansen op zijn.

    light_mode=True: alleen schrijven + review (zie generate_content_job)."""
    count = max(1, min(10, count)) if count else _batch_size(site)
    job_ids: List[str] = []
    for _ in range(count):
        job_id = await generate_content_job(site, light_mode=light_mode)
        if not job_id:
            break  # geen kansen meer — niet blijven proberen
        job_ids.append(job_id)
    return job_ids


async def run_biweekly_content_job() -> Dict:
    """Draai voor elke site met auto_content_enabled=1 een content-batch
    (content_batch_size artikelen, default 1)."""
    # Circuit-breaker: stop de hele batch als de dagbudget op is.
    from ...shared.outcomes import require_llm_budget
    try:
        require_llm_budget("biweekly-content")
    except Exception as e:
        logger.warning("[content-pipeline] Biweekly content-run overgeslagen: %s", e)
        return {"_budget_exceeded": True}
    results: Dict[str, str] = {}
    for site in sites_service.list_sites():
        full_site = sites_service.get_site(site["id"])
        if not full_site or not full_site.get("auto_content_enabled"):
            continue
        try:
            job_ids = await run_content_batch(full_site)
            results[site["name"]] = f"{len(job_ids)} jobs" if job_ids else "geen kansen"
        except Exception as e:
            logger.exception("[content-pipeline] Auto-content mislukt voor %s", site["name"])
            _log_activity(site["name"], "auto-content-fout", str(e)[:300], status="error")
            results[site["name"]] = f"fout: {e}"
    logger.info("[content-pipeline] Biweekly content-run klaar: %s", results)
    return results


async def run_content_improver_job() -> Dict:
    """Autonome verbeter-ronde: pak alle artikelen die onder de kwaliteitsgrens
    zitten (status 'needs_work') en scherp ze zélf aan tot ≥ CONTENT_MIN_SCORE
    via de verbeter-loop (regenerate_job). De mens ziet ze pas terug als ze wél
    goed genoeg zijn.

    Werkt incrementeel en defensief: per job proberen we één regenerate; als die
    de score niet verhoogt, laten we de job staan (de agent leert er in de
    learning-loop van). Nooit meer dan een beperkt aantal jobs per run, zodat een
    opstopping de event loop niet blokkeert. Retourneert een kort verslag."""
    from ...shared.config import CONTENT_MIN_SCORE, CONTENT_IMPROVER_MAX_PER_RUN
    MAX_JOBS_PER_RUN = CONTENT_IMPROVER_MAX_PER_RUN
    improved, still_low, failed, stuck = [], [], [], []
    # Circuit-breaker: geen LLM-verkeer meer als de dagbudget op is.
    from ...shared.outcomes import require_llm_budget
    try:
        require_llm_budget("content-improver")
    except Exception as e:  # BudgetExceeded — zachte stop, geen crash
        logger.warning("[content-pipeline] Verbeter-ronde overgeslagen: %s", e)
        return {"improved": [], "still_under_threshold": [], "failed": [],
                "stuck": [], "queue_remaining": 0, "budget_exceeded": True}
    # Naast 'needs_work' ook 'pending_review'-jobs ónder de grens meenemen:
    # het Actiecentrum verbergt die voor de mens ("agent moet verbeteren"),
    # dus als de verbeteraar ze ook overslaat hangen ze voorgoed in een limbo
    # (incident 2026-07-16: twee jobs op 72/83 spamden dagenlang het log).
    jobs = [j for j in (list_jobs(status="needs_work")
                        + list_jobs(status="pending_review"))
            if int(j.get("seo_score") or 0) < CONTENT_MIN_SCORE]
    # Sla jobs die al op 'stuck' staan over vóór de LLM-dans — die zijn al
    # CONTENT_IMPROVER_MAX_ATTEMPTS keer vastgelopen en horen niet opnieuw
    # verbrand te worden (incident 2026-07-10).
    stuck_ids = {j["id"] for j in list_jobs(status="stuck")}
    stuck_jobs = list_jobs(status="stuck")
    # Oudste eerst — die wachten het langst op verbetering.
    jobs.sort(key=lambda j: j.get("created_at") or "")
    for j in jobs[:MAX_JOBS_PER_RUN]:
        if j["id"] in stuck_ids:
            continue  # al vastgelopen — niet opnieuw verbranden
        try:
            await regenerate_job(j["id"])
            refreshed = get_job(j["id"])
            new_score = int(refreshed.get("seo_score") or 0) if refreshed else 0
            if refreshed and refreshed.get("status") == "stuck":
                # Cross-run cap geraakt: niet verder proberen, wel melden.
                stuck.append(f"{j['title']} (na {refreshed.get('improve_attempts')} pogingen)")
                continue
            if refreshed and refreshed.get("status") == "pending_review" and new_score >= CONTENT_MIN_SCORE:
                improved.append(f"{j['title']} ({new_score})")
                _log_activity(
                    refreshed.get("site_id") or "?", "content-verbeterd",
                    f"'{j['title']}' aangescherpt van {int(j.get('seo_score') or 0)} "
                    f"naar {new_score} — boven grens, klaar voor review.",
                    next_step="Wacht in de Wachtrij op Vincents publiceer-klik.",
                )
            elif new_score < CONTENT_MIN_SCORE:
                still_low.append(f"{j['title']} ({new_score})")
            else:
                still_low.append(f"{j['title']} (status {refreshed.get('status') if refreshed else '?'})")
        except Exception as e:
            logger.warning("[content-pipeline] Verbeter-ronde mislukt voor %s: %s",
                           j["id"], str(e)[:160])
            failed.append(f"{j['title']}: {str(e)[:80]}")
    summary = {
        "improved": improved,
        "still_under_threshold": still_low,
        "failed": failed,
        "stuck": stuck,
        "queue_remaining": max(0, len(jobs) - MAX_JOBS_PER_RUN),
    }
    logger.info("[content-pipeline] Verbeter-ronde klaar: +%d boven grens, %d nog onder, %d fout, %d vast, %d in wacht.",
                len(improved), len(still_low), len(failed), len(stuck), summary["queue_remaining"])
    # Stuck-jobs uit een eerdere run apart melden (ze horen niet in 'still_low'
    # en worden niet opnieuw geprobeerd — de cross-run cap blokkeert ze).
    for j in stuck_jobs:
        label = f"{j['title']} (bestaand, {j.get('improve_attempts')} pogingen)"
        if label not in stuck:
            stuck.append(label)
    return summary


async def _publish_to_project_site(site: Dict, title: str, html_body: str,
                                    keyword: str, slug: str, seo_score: int) -> Dict:
    """Publiceer naar de eigen site van een project via de per-project
    publish-endpoint ({PROJECT}_PUBLISH_URL/_PUBLISH_KEY in .env).

    Dit is dezelfde route als de strategist-service (weareimpact.py) gebruikt
    voor de 'schrijf artikel'-flow. De content-wachtrij deed die tot nu toe
    over (die deed alleen Netlify-sites), waardoor Bijeen-artikelen bij een
    'Goedkeuren & publiceren' níet op de website kwamen — ze bleven hangen in
    'Te reviewen'.

    Bij een mislukte (of niet-geconfigureerde) publish wordt altijd een dict
    met 'success': False teruggegeven, nooit een exception, zodat de aanroeper
    de website-publicatie nooit blokkeert."""
    import os
    name = site.get("name", "")
    env_prefix = re.sub(r"[^A-Z0-9]", "", name.upper())
    publish_url = os.getenv(f"{env_prefix}_PUBLISH_URL", "").strip()
    publish_key = os.getenv(f"{env_prefix}_PUBLISH_KEY", "").strip()
    if not publish_url or not publish_key:
        return {"success": False,
                "error": f"Geen {env_prefix}_PUBLISH_URL/_PUBLISH_KEY — site-publicatie overgeslagen"}

    base_url = (site.get("base_url") or "").rstrip("/")
    # Haal de zichtbare Meta-/Suggestie-blokken uit de body vóórdat we
    # publiceren — die horen niet in de leesbare tekst. Als de AI bruikbare
    # meta-waarden meeleverde, gebruiken we die liever dan ze uit de body te
    # halen (die bevat de Meta-titel/-description-koppen zelf).
    html_body, parsed_title, parsed_desc = _strip_meta_and_suggestions(html_body)
    # ── Extra header-strip (site-rendert titel/byline/samenvatting ZELF) ──
    # Sommige generators leveren bovenaan de body een eigen <h1>, een
    # "Door ... – Gepubliceerd op ..." byline en een "<strong>Samenvatting:</strong>"
    # paragraaf. De DatingAssistent-blogpagina rendert die velden zelf uit de
    # DB, dus anders verschijnt alles dubbel. Strip ze hier (deploy-onafhankelijk).
    html_body = _strip_duplicate_header(html_body)
    # meta-description + excerpt uit de (gezuiverde) HTML halen
    text = re.sub(r"<[^>]+>", " ", html_body or "")
    text = re.sub(r"\s+", " ", text).strip()
    meta_desc = parsed_desc or ((text[:155].rstrip() + "…") if len(text) > 155 else text)
    first_p = re.search(r"<p>(.*?)</p>", html_body or "", re.S)
    raw_excerpt = re.sub(r"<[^>]+>", "", first_p.group(1)).strip() if first_p else ""
    # Woordgrens-afkap zodat een excerpt nooit midden in een woord afbreekt
    # (voorkomt "... toepassen. E").
    excerpt = _smart_truncate(raw_excerpt, 200)

    if env_prefix == "BIJEEN":
        payload = {
            "title": title,
            "content": (html_body or "").strip(),
            "excerpt": excerpt,
            "metaTitle": (parsed_title or title)[:60],
            "metaDescription": meta_desc,
            "tags": [keyword] if keyword else [],
            "status": "published",
        }
    else:
        payload = {
            "title": title,
            "content": (html_body or "").strip(),
            "slug": slug,
            "seoTitle": (parsed_title or title)[:60],
            "seoDescription": meta_desc,
            "tags": [keyword] if keyword else [],
            "source": "agent-os",
        }

    try:
        import httpx
        # follow_redirects=True: Vercel stuurt non-www → www met een 308; zonder
        # deze vlag faalt de POST ("HTTP 308: Redirecting") en komt het artikel
        # niet op de site (terwijl AgentOS de job wél op 'published' zet).
        resp = await asyncio.to_thread(
            httpx.post, publish_url, json=payload,
            headers={"Authorization": f"Bearer {publish_key}"}, timeout=90,
            follow_redirects=True,
        )
        if resp.status_code in (200, 201):
            try:
                data = resp.json()
            except Exception:
                data = {}
            if isinstance(data, dict) and "post" in data:
                url = f"{base_url}/blog/{data['post'].get('slug', slug)}"
            elif isinstance(data, dict) and data.get("url"):
                url = data["url"]
            else:
                url = f"{base_url}/blog/{slug}"
            _log_activity(name, "live", f"'{title}' LIVE op {url}", artifact=url)
            return {"success": True, "url": url, "status_code": resp.status_code}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        logger.warning(f"Project-site publicatie op {publish_url} mislukt: {e}")
        return {"success": False, "error": str(e)[:200]}


# ── Goedkeuren → publiceren + posten ────────────────────────────────────────

async def approve_and_publish(job_id: str) -> Dict:
    """Publiceer naar de website van de site (Netlify óf de per-project
    publish-endpoint), dien de sitemap in bij Google Search Console, en post
    naar elk platform waarvoor de site credentials heeft. Wordt uitsluitend
    getriggerd door een menselijke goedkeuring (nooit automatisch).

    Een falend social-platform (bv. LinkedIn in 'Review in progress') blokkeert
    de website-publicatie nooit — het wordt als mislukt genoteerd en overgeslagen.
    """
    job = get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    if job["status"] != "pending_review":
        raise ValueError(f"Job heeft status '{job['status']}', niet 'pending_review'.")

    # Harde kwaliteitsgate: onder de grens wordt er níet gepubliceerd —
    # ook niet met een handmatige goedkeuring. Eerst verbeteren (regenerate).
    from ...shared.config import CONTENT_MIN_SCORE
    if int(job.get("seo_score") or 0) < CONTENT_MIN_SCORE:
        raise ValueError(
            f"SEO-score {job.get('seo_score')}/100 ligt onder de kwaliteitsgrens "
            f"({CONTENT_MIN_SCORE}) — laat de agent het artikel eerst verbeteren of wijs het af."
        )

    site = sites_service.get_site(job["site_id"])
    if not site:
        raise ValueError("Site niet gevonden.")

    social_copy = json.loads(job["social_copy"] or "{}")
    import base64
    image_bytes = base64.b64decode(job["image_path"]) if job.get("image_path") else None
    infographic_bytes = (base64.b64decode(job["infographic_path"])
                         if job.get("infographic_path") else None)

    result: Dict = {"netlify": None, "gsc": None, "bing": None, "social": {}}
    article_url = None
    image_url = None
    base_url = (site.get("base_url") or "").rstrip("/")

    # ── Website-publicatie ───────────────────────────────────────────────────
    # Twee routes: Netlify-sites (publish_api_url gevuld) en project-sites die
    # een eigen {PROJECT}_PUBLISH_URL/_PUBLISH_KEY hebben (bv. bijeen.app).
    if site.get("publish_api_url"):
        try:
            netlify_result = await publish_service.publish_article(
                site_id=site["id"], title=job["title"], html_body=job["blog_html"],
                slug=job["slug"], image_bytes=image_bytes,
                infographic_bytes=infographic_bytes,
            )
            result["netlify"] = netlify_result
            article_url = netlify_result.get("url")
            image_url = netlify_result.get("image_url")
        except Exception as e:
            result["netlify"] = {"error": str(e)[:300]}
    else:
        # Project-site via de per-project publish-endpoint (nooit een crash).
        try:
            site_result = await _publish_to_project_site(
                site, job["title"], job["blog_html"], job["keyword"],
                job["slug"], int(job.get("seo_score") or 0))
            result["site"] = site_result
            if site_result.get("url"):
                article_url = site_result["url"]
                image_url = site_result.get("image_url")
        except Exception as e:
            result["site"] = {"success": False, "error": str(e)[:300]}
    if not article_url and base_url:
        # Best-effort link naar de (elders gehoste) live pagina, voor social-posts.
        article_url = f"{base_url}/blog/{job['slug']}"

    # ── Google Search Console: échte sitemap-indiening (niet het uitgefaseerde
    #    google.com/ping-endpoint) ──
    gsc_property = (site.get("gsc_property") or "").strip()
    if gsc_property and base_url and gsc_service.is_configured():
        sitemap_url = f"{base_url}/sitemap.xml"
        ok, detail = gsc_service.submit_sitemap(gsc_property, sitemap_url)
        result["gsc"] = {"status": "ingediend" if ok else "fout", "detail": detail, "sitemap": sitemap_url}

    # ── Bing ping (nog wel functioneel, i.t.t. Google's ping-endpoint) ──
    if base_url:
        try:
            import httpx
            resp = httpx.get(f"https://www.bing.com/ping?sitemap={base_url}/sitemap.xml", timeout=10)
            result["bing"] = {"status_code": resp.status_code}
        except Exception as e:
            result["bing"] = {"error": str(e)[:100]}

    # ── Directe indexering van de nieuwe URL (IndexNow + optioneel Google) ──
    from . import indexing as indexing_service
    if article_url and article_url.startswith("http"):
        # Verse site-rij: publish_article kan zojuist een IndexNow-key hebben aangemaakt.
        fresh_site = sites_service.get_site(site["id"]) or site
        result["indexnow"] = await indexing_service.submit_indexnow(fresh_site, [article_url])
        google_result = await indexing_service.submit_google_indexing(article_url)
        if google_result.get("status") != "uitgeschakeld":
            result["google_indexing"] = google_result

    # ── Social fan-out — best-effort, nooit blokkerend ───────────────────────
    # Een falend platform (bv. LinkedIn in 'Review in progress' → geen member ID
    # op te halen) mag de website-publicatie niet afbreken: we noteren het als
    # mislukt en slaan het over. De mens doet die socials zelf handmatig.
    site_name = site["name"]

    async def _post(platform: str, coro):
        try:
            return await coro
        except Exception as e:
            logger.warning("Social-post %s overgeslagen (mislukt): %s", platform, e)
            return {"success": False, "error": str(e)[:200]}

    if social_copy.get("linkedin") and linkedin_service.is_configured(site_name):
        result["social"]["linkedin"] = await _post(
            "linkedin",
            linkedin_service.post_update(
                social_copy["linkedin"], article_url=article_url, site_name=site_name))
    if social_copy.get("facebook") and facebook_service.is_configured(site_name):
        result["social"]["facebook"] = await _post(
            "facebook",
            facebook_service.post_update(
                social_copy["facebook"], article_url=article_url, site_name=site_name))
    if social_copy.get("instagram") and instagram_service.is_configured(site_name):
        if image_url:
            result["social"]["instagram"] = await _post(
                "instagram",
                instagram_service.post_image(
                    image_url, social_copy["instagram"], site_name=site_name))
        else:
            result["social"]["instagram"] = {"success": False,
                "error": "Geen publieke image-url (site publish geeft geen image_url)"}
    if social_copy.get("twitter") and twitter_service.is_configured(site_name):
        result["social"]["twitter"] = await _post(
            "twitter",
            twitter_service.post_update(
                social_copy["twitter"], article_url=article_url, site_name=site_name))

    # Status correct weerspiegelen: pas 'published' als de site-publicatie écht
    # gelukt is. Mislukt die (geen env, HTTP-fout, exception), zet dan
    # 'publish_failed' — anders staat de job op 'published' terwijl er niets
    # online staat (de oorspronkelijke IctusGo-bug: 7 jobs 'published' maar 0 live).
    site_ok = bool(result.get("site", {}).get("success"))
    job_status = "published" if site_ok else "publish_failed"
    _update_job(job_id, status=job_status, publish_result=json.dumps(result), reviewed_at=_now())
    _log_activity(site_name, "publicatie", f"'{job['title']}' goedgekeurd en gepubliceerd",
                  artifact=article_url or "")

    # ── Content Multiplier: format-waaier als achtergrondtaak ────────────────
    # Uit één goedgekeurd artikel automatisch social-pack + video genereren.
    # Achter de review-gates (pending_review) — er wordt niets gepost. Als
    # create_task hier draait, leeft de taak op de uvicorn-event-loop door
    # nadat deze request al beantwoord is.
    from ...shared.config import CONTENT_MULTIPLIER_ENABLED
    if CONTENT_MULTIPLIER_ENABLED:
        try:
            from . import multiplier
            asyncio.create_task(multiplier.multiply_job_safe(job_id))
            result["multiplier"] = "gestart (achtergrond)"
        except Exception as e:
            logger.warning("[content-pipeline] Multiplier starten mislukt: %s", e)
            result["multiplier"] = f"niet gestart: {str(e)[:120]}"

    return result


def reject_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    _update_job(job_id, status="rejected", reviewed_at=_now())
    site = sites_service.get_site(job["site_id"])
    if site:
        _log_activity(site["name"], "afgekeurd", f"'{job['title']}' afgewezen")


async def regenerate_job(job_id: str) -> str:
    """Herschrijf hetzelfde onderwerp opnieuw en overschrijf de bestaande job."""
    job = get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    if job["status"] not in ("pending_review", "needs_work"):
        raise ValueError(f"Job heeft status '{job['status']}', kan niet opnieuw gegenereerd worden.")

    site = sites_service.get_site(job["site_id"])
    if not site:
        raise ValueError("Site niet gevonden.")

    # Cross-run cap: als dit artikel al CONTENT_IMPROVER_MAX_ATTEMPTS keer
    # verbeterd is (over alle 30-min-runs heen) zonder de grens te halen,
    # zetten we 'm op 'stuck' en laten we de mens beslissen — in plaats van
    # eindeloos LLM-calls te blijven verbranden (incident 2026-07-10: één
    # oscillerend artikel liep de hele dag door en leegde de OpenModel-quota).
    from ...shared.config import (CONTENT_MIN_SCORE, CONTENT_IMPROVER_MAX_ATTEMPTS)
    attempts = int(job.get("improve_attempts") or 0)
    if attempts >= CONTENT_IMPROVER_MAX_ATTEMPTS:
        _update_job(job_id, status="stuck")
        logger.warning(
            "[content-pipeline] Job '%s' na %s verbeter-pogingen nog steeds onder grens "
            "— op 'stuck' gezet, escaleert naar mens (geen verdere LLM-runs).",
            job["keyword"], attempts,
        )
        try:
            from ...shared.outcomes import log_outcome
            log_outcome(
                site["name"],
                "content-stuck",
                f"'{job['title']}' ({job['keyword']}) haalt na {attempts} verbeter-pogingen "
                f"de kwaliteitsgrens ({CONTENT_MIN_SCORE}) niet — vastgezet voor menselijke review.",
                artifact=job_id,
                next_step="Bekijk het artikel en herschrijf/keur handmatig, of verlaag de grens.",
                status="error",
            )
        except Exception as e:
            logger.debug("[content-pipeline] log_outcome(stuck) overgeslagen: %s", str(e)[:120])
        return job_id

    old_score = float(job.get("seo_score") or 0)
    qc_report, case_study_id = {}, None
    if job["status"] == "needs_work" and (job.get("blog_html") or "").strip() and old_score > 0:
        # Doorverbeteren vanaf de bestaande versie: een artikel dat al 78 scoort
        # blanco herschrijven is dobbelen (kan lager uitkomen). De verbeter-loop
        # start hier met de huidige tekst + verse reviewer-feedback.
        html_body = job["blog_html"]
    else:
        html_body, qc_report, case_study_id = await _write_article_best(
            site, job["keyword"], "", job["rationale"])
    html_body, review = await review_and_improve(site, job["keyword"], html_body)

    from ...shared.config import CONTENT_MIN_SCORE
    passed = review["score"] >= CONTENT_MIN_SCORE
    # Teller pas optellen als we écht een verbeter-cyclus hebben gedraaid; een
    # no-op (bestaande versie behouden) telt niet als nieuwe poging.
    new_attempts = attempts + (1 if review["score"] != old_score or review["score"] < CONTENT_MIN_SCORE else 0)
    if review["score"] < old_score:
        # Nooit een slechtere versie terugschrijven dan er al stond.
        logger.info("[content-pipeline] Regenerate leverde %s (< bestaande %s) — bestaande versie behouden",
                    review["score"], old_score)
        _update_job(job_id, improve_attempts=new_attempts)
        return job_id

    title = _extract_title(html_body, fallback=job["title"])
    slug = slugify_title(title)
    social_copy = await _generate_social_copy(site, title, job["keyword"], html_body)
    image_bytes = generate_quote_card(title, site["name"])
    import base64
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    infographic_bytes = (await _generate_article_infographic(site, title, job["keyword"], html_body)
                         if passed else None)

    updates = dict(
        title=title, blog_html=html_body, seo_score=review["score"],
        social_copy=json.dumps(social_copy), image_path=image_b64, slug=slug,
        status="pending_review" if passed else "needs_work",
        improve_attempts=new_attempts,
        infographic_path=(base64.b64encode(infographic_bytes).decode("ascii")
                          if infographic_bytes else ""),
    )
    if qc_report or case_study_id:
        updates["qc_report"] = json.dumps(qc_report, ensure_ascii=False)
        updates["case_study_id"] = case_study_id
    _update_job(job_id, **updates)
    return job_id


async def save_manual_edit(job_id: str, html_body: str) -> Dict:
    """Sla een handmatig (in Claude/Gemini of inline) bewerkte artikel-body terug
    op. De body wordt opnieuw door dezelfde kwaliteitsgate gehaald als automatische
    content; haalt die de grens, dan gaat de job naar 'pending_review' (klaar om te
    publiceren), anders blijft 'needs_work' staan en krijgt de mens de feedback terug.
    De herscoreslag is time-out-beschermd: als de LLM (Claude/Hermes) in quota-backoff
    hangt, wordt de body wél opgeslagen en krijgt de caller scored=False terug i.p.v.
    dat de request eeuwig blijft hangen.
    """
    import asyncio
    job = get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    html_body = (html_body or "").strip()
    if len(html_body) < 80:
        raise ValueError("Body te kort — plak de volledige (verbeterde) HTML terug.")

    site = sites_service.get_site(job["site_id"]) or {}
    from ...shared.config import CONTENT_MIN_SCORE
    title = _extract_title(html_body, fallback=job["title"])
    slug = slugify_title(title)
    # Body altijd eerst terugschrijven, ongeacht of scoren lukt.
    _update_job(job_id, title=title, blog_html=html_body, slug=slug, reviewed_at=_now())

    scored = False
    score = int(float(job.get("seo_score") or 0))
    feedback = ""
    try:
        review = await asyncio.wait_for(
            _review_article(site, job["keyword"], html_body), timeout=45)
        score = int(review["score"])
        feedback = review.get("feedback", "")
        scored = True
    except Exception as e:  # timeout, quota-403, lege response — niet blokkeren
        logger.warning("[content-pipeline] Handmatig-edit scoren overgeslagen voor job %s: %s",
                       job_id, str(e)[:160])
        feedback = ("Scoren mislukt (LLM tijdelijk niet bereikbaar — waarschijnlijk "
                    "quota-backoff). Body is opgeslagen; klik later opnieuw op "
                    "'Handmatig aanpassen' → 'Opslaan' om alsnog te scoren.")

    passed = scored and score >= CONTENT_MIN_SCORE
    status = "pending_review" if passed else "needs_work"
    _update_job(job_id, seo_score=score, status=status)
    if scored:
        if passed:
            _log_activity(
                site.get("name", "?"), "content-handmatig-verbeterd",
                f"'{title}' handmatig aangepast en haalt nu de grens ({score} ≥ {CONTENT_MIN_SCORE}) — klaar om te publiceren.",
                artifact=job_id, status="ok",
            )
        else:
            _log_activity(
                site.get("name", "?"), "content-handmatig-verbeterd",
                f"'{title}' handmatig aangepast maar zit nog onder de grens ({score} < {CONTENT_MIN_SCORE}). Feedback: {feedback[:160]}",
                artifact=job_id, status="error",
            )
    return {"job_id": job_id, "score": score, "passed": passed, "scored": scored,
            "feedback": feedback, "status": status}

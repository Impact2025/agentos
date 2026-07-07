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
from ...shared.image_gen import generate_quote_card

logger = logging.getLogger(__name__)

_FALLBACK_WRITE_PROMPT = (
    "Je bent een ervaren Nederlandse SEO-copywriter. Schrijf heldere, feitelijke, "
    "praktische content op B1-niveau. Geen AI-cliches, geen verzonnen cijfers of bronnen."
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

async def _stream_hermes(system: str, prompt: str, max_tokens: int = 2000) -> str:
    full = ""
    async for chunk in hermes_service.stream_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system,
        max_tokens=max_tokens,
    ):
        full += chunk
    return full.strip()


async def _llm(system: str, prompt: str, max_tokens: int = 2000) -> str:
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
            )).strip()
            if out:
                return out
        except Exception as e:
            logger.warning("[content-pipeline] Claude niet beschikbaar (%s) — terugval op Hermes", e)
    return await _stream_hermes_retry(system, prompt, max_tokens)


async def _stream_hermes_retry(system: str, prompt: str, max_tokens: int = 2000, retries: int = 2) -> str:
    """Zoals _stream_hermes, maar probeert opnieuw bij een lege response.

    Kleinere/budget-modellen geven soms een 200-response met 0 output-tokens
    terug op strikte JSON-prompts (geen exception, gewoon niks). Vooral de
    review/social-copy-stappen hebben betrouwbare output nodig omdat ze
    verderop geparsed worden — een enkele lege poging mag niet meteen
    terugvallen op de fallback-tekst."""
    for attempt in range(retries + 1):
        out = await _stream_hermes(system, prompt, max_tokens)
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


def _vault_context(project_name: str) -> str:
    try:
        from ...shared.vault_reader import VaultReader
        vr = VaultReader()
        if vr.is_configured:
            return vr.get_core_context(project_name)
    except Exception:
        pass
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
    """True als `keyword` overlapt met een titel/slug die al écht op de site
    staat (buiten Agent OS om gepubliceerd, bijv. Bijeen/Steentjebijsteentje se
    eigen Next.js-CMS). Voorkomt dat de Demand Engine een 'nieuwe kans' oppakt
    die feitelijk al een bestaand artikel is."""
    kw_slug = slugify_title(keyword)
    kw_tokens = _slug_tokens(keyword)
    if not kw_slug and not kw_tokens:
        return False
    for item in external_titles:
        ext_slug = slugify_title(item.get("slug") or item.get("title") or "")
        if not ext_slug:
            continue
        if kw_slug and (kw_slug in ext_slug or ext_slug in kw_slug):
            return True
        ext_tokens = _slug_tokens(item.get("title") or "")
        if kw_tokens and ext_tokens and kw_tokens <= ext_tokens:
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
    vault_context = _vault_context(project_name)
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
    return await _llm(write_system, write_prompt, max_tokens=8000)


async def _review_article(site: Dict, keyword: str, html_body: str) -> Dict:
    review_system = _profile_prompt("SEO Editor") or _FALLBACK_REVIEW_PROMPT
    review_prompt = (
        f"Beoordeel onderstaand blogartikel voor {site['name']}.\n\n"
        f"Kernzoekwoord: {keyword}\n\n"
        "Houd de feedback beknopt: maximaal 6 genummerde punten van elk 1-2 zinnen.\n\n"
        f"ARTIKEL:\n{html_body}"
    )
    raw = await _llm(review_system, review_prompt, max_tokens=2500)
    try:
        obj = json.loads(_extract_json(raw))
        score = max(0, min(100, int(round(float(obj.get("score", 50))))))
        feedback = str(obj.get("feedback") or "").strip()
    except Exception:
        # JSON kapot (meestal: afgekapte lange feedback — de score staat vooraan
        # en is dan meestal nog intact; regex-redding zodat een geldig oordeel
        # niet verloren gaat). Geen score vindbaar → 0, zodat het artikel NOOIT
        # per ongeluk door de kwaliteitsgate glipt (voorheen: stille 50).
        m = re.search(r'"score"\s*:\s*(\d{1,3})', raw)
        score = max(0, min(100, int(m.group(1)))) if m else 0
        fm = re.search(r'"feedback"\s*:\s*"(.*)', raw, re.DOTALL)
        feedback = (fm.group(1).strip() if fm else raw)[:800]
    return {"score": score, "feedback": feedback}


async def review_and_improve(site: Dict, keyword: str, html_body: str,
                             max_rounds: int = 3) -> tuple:
    """Review → verbeter → review, tot de kwaliteitsgate (CONTENT_MIN_SCORE)
    is gehaald of de rondes op zijn. Retourneert (html_body, review)."""
    from ...shared.config import CONTENT_MIN_SCORE
    review = await _review_article(site, keyword, html_body)
    best_html, best_review = html_body, review
    rounds = 0
    while review["score"] < CONTENT_MIN_SCORE and rounds < max_rounds and review["feedback"]:
        rounds += 1
        logger.info("[content-pipeline] Verbeterronde %s/%s (score %s < %s) — %s",
                    rounds, max_rounds, review["score"], CONTENT_MIN_SCORE, site["name"])
        html_body = await _optimize_article(site, keyword, html_body, review["feedback"])
        review = await _review_article(site, keyword, html_body)
        # Houd de beste versie vast: een herschrijfronde kan ook verslechteren,
        # en dan willen we niet de mindere laatste versie opleveren.
        if review["score"] > best_review["score"]:
            best_html, best_review = html_body, review
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
    out = await _llm(optimize_system, prompt, max_tokens=8000)
    return out if len(out) > 50 else html_body


async def _write_article_best(site: Dict, keyword: str, angle: str,
                              rationale: str) -> tuple:
    """Meertraps-generator (outline → secties → opmaak → links → QC) met
    terugval op de single-shot-schrijver als de pipeline stukloopt.
    Retourneert (html_body, qc_report, case_study_id)."""
    knowledge = knowledge_service.get_site_knowledge(site)
    case_study = knowledge_service.match_case_study(site["id"], keyword, angle)
    try:
        html_body, qc_report = await article_writer.write_article_staged(
            site, keyword, angle, rationale,
            case_study=case_study,
            profile=knowledge["profile"], ctas=knowledge["ctas"],
            brand_context=_vault_context(site["name"]),
            base_style_prompt=_profile_prompt("SEO Copywriter") or _FALLBACK_WRITE_PROMPT,
        )
        return html_body, qc_report, (case_study or {}).get("id", "")
    except Exception as e:
        logger.warning("[content-pipeline] Meertraps-generator mislukt (%s) — "
                       "terugval op single-shot-schrijver", e)
        html_body = await _write_article(site, keyword, angle, rationale)
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


# ── Content-jobs CRUD ────────────────────────────────────────────────────────

def create_job(site_id: str, title: str, keyword: str, rationale: str, blog_html: str,
                seo_score: float, social_copy: Dict[str, str], image_bytes: Optional[bytes],
                slug: str, status: str = "pending_review",
                qc_report: Optional[Dict] = None, case_study_id: str = "") -> str:
    """status 'pending_review' = klaar om goed te keuren (score ≥ gate);
    'needs_work' = onder de kwaliteitsgate — eerst verbeteren of afwijzen."""
    job_id = str(uuid.uuid4())
    image_path = ""
    if image_bytes:
        import base64
        image_path = base64.b64encode(image_bytes).decode("ascii")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO content_jobs
               (id, site_id, title, keyword, rationale, status, blog_html, seo_score,
                social_copy, image_path, slug, publish_result, qc_report, case_study_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?)""",
            (job_id, site_id, title, keyword, rationale, status, blog_html, seo_score,
             json.dumps(social_copy), image_path, slug,
             json.dumps(qc_report or {}, ensure_ascii=False), case_study_id, _now()),
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
                                angle: str = "", rationale: str = "") -> Optional[str]:
    """Schrijf + review + social copy + afbeelding voor één site, opslaan als
    pending_review content_job. Retourneert het job-id, of None als er geen
    onderwerp beschikbaar was."""
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

    html_body, review = await review_and_improve(site, keyword, html_body)

    title = _extract_title(html_body, fallback=angle or keyword)
    slug = slugify_title(title)

    social_copy = await _generate_social_copy(site, title, keyword, html_body)
    image_bytes = generate_quote_card(title, site["name"])

    from ...shared.config import CONTENT_MIN_SCORE
    passed = review["score"] >= CONTENT_MIN_SCORE
    job_id = create_job(site["id"], title, keyword, rationale, html_body,
                        review["score"], social_copy, image_bytes, slug,
                        status="pending_review" if passed else "needs_work",
                        qc_report=qc_report, case_study_id=case_study_id)
    if passed:
        _log_activity(site["name"], "auto-content-klaar",
                      f"'{title}' (SEO-score {review['score']}) klaar voor review",
                      next_step="Keur goed of wijs af in de Wachtrij")
    else:
        _log_activity(site["name"], "auto-content-onder-grens",
                      f"'{title}' haalde na 3 verbeterrondes {review['score']}/100 "
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
    job_id = create_job(site["id"], title, keyword, rationale, html_body,
                        review["score"], social_copy, image_bytes, slug,
                        status="pending_review" if passed else "needs_work")
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


async def run_content_batch(site: Dict, count: Optional[int] = None) -> List[str]:
    """Genereer `count` content-jobs voor één site (default: de site-instelling
    content_batch_size). Sequentieel — kostenbeheersing en rate-limits. Stopt
    zodra de Demand Engine-kansen op zijn."""
    count = max(1, min(10, count)) if count else _batch_size(site)
    job_ids: List[str] = []
    for _ in range(count):
        job_id = await generate_content_job(site)
        if not job_id:
            break  # geen kansen meer — niet blijven proberen
        job_ids.append(job_id)
    return job_ids


async def run_biweekly_content_job() -> Dict:
    """Draai voor elke site met auto_content_enabled=1 een content-batch
    (content_batch_size artikelen, default 1)."""
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


# ── Goedkeuren → publiceren + posten ────────────────────────────────────────

async def approve_and_publish(job_id: str) -> Dict:
    """Publiceer naar Netlify (indien geconfigureerd), dien de sitemap in bij
    Google Search Console, en post naar elk platform waarvoor de site
    credentials heeft. Wordt uitsluitend getriggerd door een menselijke
    goedkeuring (nooit automatisch)."""
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

    result: Dict = {"netlify": None, "gsc": None, "bing": None, "social": {}}
    article_url = None
    image_url = None
    base_url = (site.get("base_url") or "").rstrip("/")

    # ── Netlify (optioneel — sommige sites publiceren elders, bijv. Vercel) ──
    if site.get("publish_api_url"):
        try:
            netlify_result = await publish_service.publish_article(
                site_id=site["id"], title=job["title"], html_body=job["blog_html"],
                slug=job["slug"], image_bytes=image_bytes,
            )
            result["netlify"] = netlify_result
            article_url = netlify_result.get("url")
            image_url = netlify_result.get("image_url")
        except Exception as e:
            result["netlify"] = {"error": str(e)[:300]}
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

    # ── Social fan-out — alleen platformen met geldige credentials voor deze site ──
    site_name = site["name"]
    if social_copy.get("linkedin") and linkedin_service.is_configured(site_name):
        result["social"]["linkedin"] = await linkedin_service.post_update(
            social_copy["linkedin"], article_url=article_url, site_name=site_name)
    if social_copy.get("facebook") and facebook_service.is_configured(site_name):
        result["social"]["facebook"] = await facebook_service.post_update(
            social_copy["facebook"], article_url=article_url, site_name=site_name)
    if social_copy.get("instagram") and instagram_service.is_configured(site_name):
        if image_url:
            result["social"]["instagram"] = await instagram_service.post_image(
                image_url, social_copy["instagram"], site_name=site_name)
        else:
            result["social"]["instagram"] = {"success": False, "error": "Geen publieke image-url (Netlify niet geconfigureerd)"}
    if social_copy.get("twitter") and twitter_service.is_configured(site_name):
        result["social"]["twitter"] = await twitter_service.post_update(
            social_copy["twitter"], article_url=article_url, site_name=site_name)

    _update_job(job_id, status="published", publish_result=json.dumps(result), reviewed_at=_now())
    _log_activity(site_name, "publicatie", f"'{job['title']}' goedgekeurd en gepubliceerd",
                  artifact=article_url or "")
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

    html_body, qc_report, case_study_id = await _write_article_best(
        site, job["keyword"], "", job["rationale"])
    html_body, review = await review_and_improve(site, job["keyword"], html_body)

    title = _extract_title(html_body, fallback=job["title"])
    slug = slugify_title(title)
    social_copy = await _generate_social_copy(site, title, job["keyword"], html_body)
    image_bytes = generate_quote_card(title, site["name"])
    import base64
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    from ...shared.config import CONTENT_MIN_SCORE
    _update_job(
        job_id, title=title, blog_html=html_body, seo_score=review["score"],
        social_copy=json.dumps(social_copy), image_path=image_b64, slug=slug,
        status="pending_review" if review["score"] >= CONTENT_MIN_SCORE else "needs_work",
        qc_report=json.dumps(qc_report, ensure_ascii=False), case_study_id=case_study_id,
    )
    return job_id

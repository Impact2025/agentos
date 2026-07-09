# ── WeAreImpact — Activity, Content, Blog workflow ──────────────────────
# Toegevoegd aan de bestaande projects router.

import json, logging, uuid, asyncio, os, re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from fastapi import Query, HTTPException
from pydantic import BaseModel

# Importeer dezelfde router uit dit bestand
from .router import router, PROJECTS_DIR, _find_project_dir

from ...shared.database import get_conn
from ...shared import config as app_config
from ..chat import hermes as hermes_service
from ..publish import service as publish_service
from ..publish import content_pipeline
from ..seo import sites as sites_service
from ..seo import gsc as gsc_service

logger = logging.getLogger(__name__)

# Streefscore (0-100) voor automatische SEO-optimalisatie, en het maximum aantal
# optimalisatierondes voordat we stoppen (kostenbeheersing + garantie dat het ooit eindigt).
# Agent OS-eis: de agent MOET de 85%-grens altijd halen. Daarom itereren we door
# tot de score WORLD_CLASS_SCORE bereikt of het rondemaximum is bereikt. 6 rondes
# geeft voldoende marge om van ~70 naar 85+ te komen; de harde publish-gate
# (PUBLISH_MIN_SCORE) blokkeert alsnog alles wat onder de 85 blijft, dus er kan
# nooit een sub-85 blog live gaan.
WORLD_CLASS_SCORE = 85
MAX_OPTIMIZE_ROUNDS = 6

# Harde publicatie-gate (0-10): onder deze score wordt een artikel alleen als
# concept opgeslagen — géén Netlify-deploy en géén zoekmachine-indiening.
# Agent OS-eis: GEEN enkel blog mag onder de 85% (0-100) live gaan in welk
# project dan ook. De review-score is 0-100; we tonen hem als 0-10
# (seo_score = score / 10). De gate checkt daarom op 8.5 (== 85/100).
# Een artikel dat na MAX_OPTIMIZE_ROUNDS de 85 niet haalt, blijft als
# CONCEPT staan en wordt niet gepubliceerd — de agent moet de lat halen.
PUBLISH_MIN_SCORE = 8.5

# Frontmatter-velden die met aanhalingstekens worden opgeslagen (vrije tekst met
# spaties/leestekens); de rest is een kaal getal/woord en blijft ongequote.
_QUOTED_FRONTMATTER_FIELDS = {"title", "keyword", "description"}

def _render_frontmatter(meta: Dict[str, str]) -> str:
    lines = []
    for k, v in meta.items():
        lines.append(f'{k}: "{v}"' if k in _QUOTED_FRONTMATTER_FIELDS else f'{k}: {v}')
    return "\n".join(lines)

# ── Activity Log ─────────────────────────────────────────────────────────

def _log_activity(project: str, action: str, detail: str = "",
                  artifact: str = "", next_step: str = "", status: str = "ok"):
    from ...shared.outcomes import log_outcome
    # Fouten krijgen status='error' zodat het Actiecentrum ze als inbox-item toont.
    if status == "ok" and ("fout" in action or action == "error"):
        status = "error"
    log_outcome(project, action, detail, artifact=artifact, next_step=next_step, status=status)

def _resolve_site(name: str):
    norm = lambda x: x.lower().replace(" ", "").replace("-", "")
    target = norm(name)
    for s in sites_service.list_sites():
        if norm(s["name"]) == target:
            return s
    return None

# ── Global Activity Log endpoint ──
from fastapi import APIRouter, Query as Q2

activity_router = APIRouter(prefix="/api/activity", tags=["activity"])

@activity_router.get("")
def global_activity(limit: int = Q2(30, ge=1, le=100)):
    """Haal recente activiteit op (alle projecten)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.id, a.project, a.action, a.detail, a.created_at FROM activity_log a ORDER BY a.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Exclude /api/projects/activity from weareimpact to avoid route collision
# (The {name} wildcard in projects router catches 'activity' as a project name)


@router.get("/{name}/activity")
def project_activity(name: str, limit: int = Query(20, ge=1, le=100)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, action, detail, created_at FROM activity_log WHERE project = ? ORDER BY created_at DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    return [dict(r) for r in rows]

# ── Content Inventory ────────────────────────────────────────────────────

@router.get("/{name}/content")
def project_content(name: str):
    """Retourneer bestaande content: gepubliceerde paginas + logboek."""
    site = _resolve_site(name)

    project_dir = _find_project_dir(name)
    if not project_dir:
        return {"seo_files": [], "log_files": [], "zzp_files": [], "content_files": [], "published": [], "gsc_pages": []}

    content_dir = project_dir / "SEO"
    seo_files = []
    if content_dir.exists():
        for f in sorted(content_dir.glob("*.md")):
            seo_files.append({"name": f.stem, "path": str(f), "size": f.stat().st_size})

    # Logboek en ZZP bestanden
    log_files = []
    log_dir = project_dir / "log"
    if log_dir.exists():
        for f in sorted(log_dir.glob("*.md")):
            log_files.append({"name": f.stem, "path": str(f), "size": f.stat().st_size})

    zzp_files = []
    zzp_dir = project_dir / "zzp-opdrachten"
    if zzp_dir.exists():
        for f in sorted(zzp_dir.glob("*.md")):
            zzp_files.append({"name": f.stem, "path": str(f), "size": f.stat().st_size})

    # Content bestanden (.md en .html)
    content_files = []
    cont_dir = project_dir / "content"
    if cont_dir.exists():
        for f in sorted(list(cont_dir.glob("*.md")) + list(cont_dir.glob("*.html"))):
            content_files.append({"name": f.stem, "path": str(f), "size": f.stat().st_size})

    published = []
    if site:
        published = publish_service.list_pages(site["id"])

    # GSC top-paginas als gepubliceerde content (voor WeAreImpact die via Vercel publiceert)
    gsc_pages = []
    if site:
        gsc_prop = site.get("gsc_property", "")
        if gsc_prop:
            try:
                from ..seo.gsc import fetch_page_performance, is_configured
                if is_configured():
                    pages = fetch_page_performance(gsc_prop, days=28, row_limit=50)
                    seen = set()
                    gsc_pages = []
                    for p in pages:
                        raw_title = p.get("page", "")
                        title = raw_title \
                            .replace("https://www.weareimpact.nl", "") \
                            .replace("https://weareimpact.nl", "") \
                            .rstrip("/") or "/"
                        # Duplicate titles skippen (www vs non-www)
                        if title in seen:
                            continue
                        seen.add(title)
                        gsc_pages.append({
                            "title": title,
                            "url": raw_title,
                            "clicks": p.get("clicks", 0),
                            "impressions": p.get("impressions", 0),
                            "position": p.get("position", 0),
                        })
            except Exception as e:
                logger.warning(f"GSC fetch for content inventory: {e}")

    return {
        "published": published,
        "gsc_pages": gsc_pages,
        "seo_analyses": seo_files,
        "log_entries": log_files,
        "zzp_opdrachten": zzp_files,
        "content_files": content_files,
    }

def _resolve_content_file(project_dir: Path, kind: str, file: str) -> Path:
    dirs = {
        "content": project_dir / "content",
        "log": project_dir / "log",
        "zzp": project_dir / "zzp-opdrachten",
    }
    base_dir = dirs.get(kind)
    if not base_dir:
        raise HTTPException(400, "Onbekend bestandstype")
    # Alleen de kale bestandsnaam gebruiken (geen paden) om directory traversal te voorkomen
    safe_name = Path(file).name
    for ext in (".html", ".md"):
        candidate = base_dir / f"{safe_name}{ext}"
        if candidate.exists():
            return candidate
    raise HTTPException(404, "Bestand niet gevonden")


def _split_frontmatter(raw: str) -> tuple[Dict[str, str], str]:
    """Split het eenvoudige 'key: value'-frontmatter-blok (tussen --- markers) van de body."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta: Dict[str, str] = {}
    for line in raw[3:end].strip("\n").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    body = raw[end + 4:].lstrip("\n")
    return meta, body


@router.get("/{name}/content-file")
def project_content_file(name: str, kind: str = Query(...), file: str = Query(...)):
    """Lees de ruwe inhoud van een lokaal concept/log/zzp-bestand, om te tonen in de UI."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(404, "Project niet gevonden")
    match = _resolve_content_file(project_dir, kind, file)
    return {"name": match.stem, "extension": match.suffix, "content": match.read_text(encoding="utf-8")}


@router.post("/{name}/content-file/analyze")
async def analyze_content_file(name: str, kind: str = Query(...), file: str = Query(...)):
    """Laat de SEO Editor-expert een lokaal concept beoordelen (zelfde profiel/logica als de content-wachtrij)."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(404, "Project niet gevonden")
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden als site")
    match = _resolve_content_file(project_dir, kind, file)
    meta, body = _split_frontmatter(match.read_text(encoding="utf-8"))
    review = await content_pipeline._review_article(site, meta.get("keyword", ""), body)
    return {"name": match.stem, "keyword": meta.get("keyword", ""), **review}


@router.post("/{name}/content-file/social-copy")
async def generate_social_copy_for_file(name: str, kind: str = Query(...), file: str = Query(...)):
    """Genereer per-platform social copy voor een lokaal concept (zelfde profiel/logica als de content-wachtrij).
    Post niets — levert alleen de tekst, kopieerbaar in de UI."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(404, "Project niet gevonden")
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden als site")
    match = _resolve_content_file(project_dir, kind, file)
    meta, body = _split_frontmatter(match.read_text(encoding="utf-8"))
    title = meta.get("title") or match.stem.replace("-", " ")
    social_copy = await content_pipeline._generate_social_copy(site, title, meta.get("keyword", ""), body)
    return {"name": match.stem, "title": title, "social_copy": social_copy}


@router.post("/{name}/content-file/optimize")
async def optimize_content_file(name: str, kind: str = Query(...), file: str = Query(...)):
    """Pas de SEO-feedback direct toe ('Pas toe'-knop): optimaliseert het concept in-place,
    itereert tot wereldklasse-niveau of het rondemaximum, en overschrijft het bestand."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(404, "Project niet gevonden")
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden als site")
    match = _resolve_content_file(project_dir, kind, file)
    meta, body = _split_frontmatter(match.read_text(encoding="utf-8"))
    keyword = meta.get("keyword", "")

    review = await content_pipeline._review_article(site, keyword, body)
    optimized_html = body
    rounds = 0
    while review["score"] < WORLD_CLASS_SCORE and rounds < MAX_OPTIMIZE_ROUNDS:
        rounds += 1
        optimized_html = await content_pipeline._optimize_article(site, keyword, optimized_html, review["feedback"])
        optimized_html, _ = content_pipeline.article_writer.strip_unvetted_internal_links(optimized_html, site)
        review = await content_pipeline._review_article(site, keyword, optimized_html)

    if meta:
        meta["seo_score"] = f"{review['score']/10:.1f}"
        meta["word_count"] = str(len(optimized_html.split()))
        new_raw = f"---\n{_render_frontmatter(meta)}\n---\n\n{optimized_html.strip()}\n"
    else:
        new_raw = optimized_html
    match.write_text(new_raw, encoding="utf-8")

    return {
        "name": match.stem,
        "extension": match.suffix,
        "content": new_raw,
        "score": review["score"],
        "feedback": review["feedback"],
        "rounds": rounds,
        "world_class": review["score"] >= WORLD_CLASS_SCORE,
    }

# ── Blog Suggestions via Hermes AI ──────────────────────────────────────

@router.post("/{name}/suggest-blogs")
async def suggest_blogs(name: str, days: int = Query(28)):
    """Laat Hermes 3 blog-onderwerpen suggereren op basis van:
       - Bestaande content (SKILL.md, gepubliceerd, GSC top queries)
       - Huidige data/trends
       - Project-SKILL.md toon en stijl
    """
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")

    # ── Bouw context uit SKILL.md + Obsidian vault ──
    project_dir = _find_project_dir(name)
    skill_body = "(geen)"
    if project_dir:
        skill_path = project_dir / "SKILL.md"
        skill_body = skill_path.read_text("utf-8")[:2000] if skill_path.exists() else "(geen)"

    # Vault context: brand, SEO-strategie, content memory
    vault_context = ""
    try:
        from ...shared.vault_reader import VaultReader
        vr = VaultReader()
        if vr.is_configured:
            core = vr.get_core_context(site["name"])
            if core:
                vault_context = f"\n\n## Context uit Obsidian vault\n{core[:3000]}"
    except Exception:
        pass

    published = []
    if site:
        published = publish_service.list_pages(site["id"])

    # Bestaande titels
    existing = [p["title"] for p in published if p.get("title")]

    # Top queries uit GSC
    top_queries = []
    gsc_prop = site.get("gsc_property", "")
    if gsc_prop:
        try:
            from ..seo.gsc import fetch_query_performance, is_configured
            if is_configured():
                qs = fetch_query_performance(gsc_prop, days=days, row_limit=50)
                top_queries = [q["query"] for q in qs if q["clicks"] > 0][:20]
        except Exception as e:
            logger.warning(f"GSC fetch for suggestions: {e}")

    now = datetime.now().strftime("%A %d %B %Y")

    system_prompt = (
        "Je bent de content-strategie assistent voor WeAreImpact.nl. "
        "Je kent de doelgroep: gemeenten, zorg- en welzijnsorganisaties in Nederland. "
        "Je schrijft zoals Vincent van Munster — nuchter, deskundig, eerste persoon, geen jargon."
        f"{vault_context}"
    )

    user_prompt = (
        f"Vandaag is {now}.\n\n"
        f"Project SKILL.md:\n{skill_body}\n\n"
        f"Bestaande artikelen: {', '.join(existing[:20]) if existing else '(nog geen)'}\n\n"
        f"Top zoekwoorden (meeste klikken): {', '.join(top_queries[:10]) if top_queries else '(nog geen GSC-data)'}\n\n"
        "Gebaseerd op deze context, de huidige trends in AI/zorg/welzijn en het sociaal domein in Nederland:\n"
        "Stel 3 concrete blog-onderwerpen voor voor deze week.\n\n"
        "Let op de merkidentiteit en schrijfstijl uit de vault context.\n\n"
        "Geef elk onderwerp met:\n"
        "1. Titel (pakkkend, SEO-vriendelijk)\n"
        "2. Korte rationale (waarom dit onderwerp nu relevant is, 1-2 zinnen)\n"
        "3. Belangrijkste zoekwoord\n"
        "4. Geschatte schrijftijd (bijv. '2 uur')\n\n"
        "Antwoord ALLEEN in JSON-formaat, als een array van 3 objects met keys: title, rationale, keyword, estimated_hours."
    )

    full_response = ""
    async for chunk in hermes_service.stream_response(
        messages=[{"role": "user", "content": user_prompt}],
        system_prompt=system_prompt,
        max_tokens=2000,
    ):
        full_response += chunk

    # Parse JSON uit response
    try:
        # Zoek naar geldige JSON in de response
        start = full_response.find("[")
        end = full_response.rfind("]") + 1
        if start >= 0 and end > start:
            suggestions = json.loads(full_response[start:end])
        else:
            suggestions = [{"title": full_response[:200], "rationale": "", "keyword": "", "estimated_hours": ""}]
    except json.JSONDecodeError:
        suggestions = [{"title": full_response[:200], "rationale": "", "keyword": "", "estimated_hours": ""}]

    _log_activity(name, "suggestie", f"{len(suggestions)} blog-onderwerpen gegenereerd")
    return {"suggestions": suggestions, "existing_count": len(existing), "queries_count": len(top_queries)}


# ── Write & Publish blog (pro SEO pipeline) ─────────────────────────────

class WritePublishRequest(BaseModel):
    title: str
    rationale: str = ""
    keyword: str = ""

# Schrijf/review/optimize/slugify komen nu uit content_pipeline.py (vault-gedreven,
# projectonafhankelijk) i.p.v. hier hardcoded WeAreImpact-only prompts te dupliceren.
_slugify = content_pipeline.slugify_title


# ── Voortgang van artikel-schrijf-jobs (in-memory, gepolld door frontend) ──
_ARTICLE_JOBS: Dict[str, dict] = {}
_ARTICLE_BG_TASKS: "set[asyncio.Task]" = set()

def _set_job(job_id: str, **fields):
    job = _ARTICLE_JOBS.get(job_id)
    if job:
        job.update(fields)

@router.post("/{name}/write-and-publish")
async def write_and_publish(name: str, body: WritePublishRequest):
    """Start de SEO-schrijfpipeline op de achtergrond en geef direct een job_id terug."""
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")

    job_id = str(uuid.uuid4())
    _ARTICLE_JOBS[job_id] = {"status": "running", "phase": "Artikel schrijven...", "percent": 5, "result": None, "error": None}
    task = asyncio.create_task(_run_write_and_publish_job(job_id, name, site, body))
    _ARTICLE_BG_TASKS.add(task)
    task.add_done_callback(_ARTICLE_BG_TASKS.discard)
    return {"job_id": job_id}


@router.get("/{name}/write-and-publish/{job_id}")
def write_and_publish_status(name: str, job_id: str):
    job = _ARTICLE_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job niet gevonden")
    return job


async def _run_write_and_publish_job(job_id: str, name: str, site: dict, body: "WritePublishRequest"):
    try:
        result = await _write_and_publish_pipeline(job_id, name, site, body)
        _set_job(job_id, status="done", percent=100, phase="Klaar", result=result)
    except HTTPException as e:
        _set_job(job_id, status="error", error=e.detail)
    except Exception as e:
        logger.exception(f"[SEO-pipeline] Job {job_id} mislukt")
        _set_job(job_id, status="error", error=str(e))


async def _write_and_publish_pipeline(job_id: str, name: str, site: dict, body: "WritePublishRequest"):
    """Pro SEO pipeline: schrijf → SEO review → optimaliseer → opslaan → GSC-indienen.

    Schrijf-/review-/optimaliseer-stappen komen uit content_pipeline.py: vault-gedreven
    merkstem (`[NNN] PROJECT_CORE/` in de Obsidian-vault) + de SEO Copywriter/Editor-
    expertprofielen, i.p.v. hier hardcoded WeAreImpact-only prompts te dupliceren.
    """
    site_id = site["id"]
    project_dir = _find_project_dir(name)
    content_dir = (project_dir / "content") if project_dir else Path(os.getenv("TEMP", "")) / "agentos-content"
    content_dir.mkdir(parents=True, exist_ok=True)

    # ── FASE 1: Schrijven (meertraps-generator: outline → secties → opmaak →
    #    gevalideerde links → QC; valt zelf terug op single-shot bij falen) ──
    logger.info(f"[SEO-pipeline] Fase 1: Schrijven — '{body.title}'")

    keyword = (body.keyword or body.title).strip()
    html_body, qc_report, _case_study_id = await content_pipeline._write_article_best(
        site, keyword, body.title, body.rationale)
    if not html_body:
        raise HTTPException(500, "Kon geen artikel genereren — lege response van Hermes")

    _set_job(job_id, phase="Concept klaar, SEO-review voorbereiden...", percent=35)
    # Pauze om 429-rate-limit te voorkomen
    await asyncio.sleep(10)

    # ── FASE 2: SEO Review ─────────────────────────────────────────
    logger.info(f"[SEO-pipeline] Fase 2: SEO review — '{body.title}'")
    _set_job(job_id, phase="SEO-kwaliteit beoordelen...", percent=45)

    review = await content_pipeline._review_article(site, keyword, html_body)
    seo_score = review["score"] / 10.0  # content_pipeline gebruikt 0-100, hier tonen we 0-10

    _set_job(job_id, phase=f"SEO-score {seo_score:.1f}/10 — voorbereiden volgende stap...", percent=60)
    # Pauze om 429-rate-limit te voorkomen
    await asyncio.sleep(10)

    # ── FASE 3: Optimaliseren tot wereldklasse-niveau (score >= 85/100) ──────
    # Eén optimalisatieronde was vaak niet genoeg om echt hoog te scoren — dus
    # blijf itereren op de laatste feedback tot de score de lat haalt of het
    # rondemaximum bereikt is (kostenbeheersing + garantie dat het ooit stopt).
    optimized_html = html_body
    rounds = 0
    while review["score"] < WORLD_CLASS_SCORE and rounds < MAX_OPTIMIZE_ROUNDS:
        rounds += 1
        logger.info(f"[SEO-pipeline] Fase 3: Optimaliseren ronde {rounds}/{MAX_OPTIMIZE_ROUNDS} "
                    f"(score {review['score']}/100) — '{body.title}'")
        _set_job(job_id, phase=f"Optimaliseren voor wereldklasse-score (ronde {rounds}/{MAX_OPTIMIZE_ROUNDS}, "
                                f"huidige score {review['score']}/100)...", percent=min(60 + rounds * 10, 90))

        optimized_html = await content_pipeline._optimize_article(
            site, keyword, optimized_html, review["feedback"]
        )
        # Een herschrijfronde kan gevalideerde interne links laten vallen of nieuwe
        # verzinnen — die zijn dan niet meer gevet, dus opnieuw wieden vóór de
        # volgende beoordeling (anders belanden 404-links op de live site).
        optimized_html, n_stripped = content_pipeline.article_writer.strip_unvetted_internal_links(
            optimized_html, site
        )
        if n_stripped:
            logger.info(f"[SEO-pipeline] Optimalisatieronde {rounds}: {n_stripped} ongevette interne link(s) verwijderd")

        _set_job(job_id, phase=f"Herbeoordelen na optimalisatieronde {rounds}...", percent=min(65 + rounds * 10, 92))
        # Pauze om 429-rate-limit te voorkomen
        await asyncio.sleep(10)

        review = await content_pipeline._review_article(site, keyword, optimized_html)
        seo_score = review["score"] / 10.0

    if rounds:
        logger.info(f"[SEO-pipeline] Optimalisatie klaar na {rounds} ronde(s) — eindscore {review['score']}/100")

    # Feedback van de LAATSTE beoordeling (na optimalisatie, indien van toepassing) —
    # zo ziet de gebruiker de resterende aandachtspunten, niet de allang-opgeloste.
    seo_review = {
        "score": seo_score,
        "verbeterpunten": [
            l.strip("-*0123456789. ").strip()
            for l in review["feedback"].split("\n") if l.strip()
        ][:5],
    }

    # ── FASE 4: Opslaan met SEO metadata ──────────────────────────
    _set_job(job_id, phase="Opslaan en publiceren...", percent=95)
    from datetime import date
    today = date.today().isoformat()

    # Titel = de H1 die de generator schreef (pakkender dan het kale zoekwoord).
    final_title = content_pipeline._extract_title(optimized_html, fallback=body.title.strip())
    # Slug pas NU bepalen (uit final_title, niet uit het vroege body.title): body.title
    # wordt hierboven als "angle" aan de schrijver doorgegeven en kan een interne
    # analysezin zijn (bv. een content-gap-observatie), geen publiceerbare titel — een
    # slug daarop gebaseerd gaf onleesbare URL's die niets met de uiteindelijke H1 te maken hadden.
    slug = _slugify(final_title)

    # Meta description = eerste alinea van het artikel zelf. NOOIT de rationale —
    # dat is interne Demand-Engine-analyse ("SEO-kans uit GSC: ...") en die stond
    # eerder letterlijk als intro op de live site.
    first_p = re.search(r"<p[^>]*>(.*?)</p>", optimized_html, re.S)
    meta_desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", first_p.group(1))).strip() if first_p else ""
    if len(meta_desc) < 40:
        meta_desc = f"Praktische gids over {keyword} — met concrete stappen en voorbeelden."
    meta_desc = meta_desc[:157].rsplit(" ", 1)[0] + "…" if len(meta_desc) > 157 else meta_desc

    word_count = len(optimized_html.split())

    full_content = (
        "---\n"
        f"title: \"{final_title}\"\n"
        f"slug: {slug}\n"
        f"keyword: \"{keyword}\"\n"
        f"description: \"{meta_desc}\"\n"
        f"created_at: {today}\n"
        f"word_count: {word_count}\n"
        f"seo_score: {seo_score:.1f}\n"
        f"source: hermes-suggestie\n"
        "---\n\n"
        f"{optimized_html.strip()}\n"
    )

    filepath = content_dir / f"{slug}.html"
    filepath.write_text(full_content, encoding="utf-8")

    # ── Obsidian opslag ──────────────────────────────────────────────
    obsidian_path = None
    try:
        from ...domains.chat.obsidian import ObsidianService
        from ...shared.config import OBSIDIAN_VAULT_PATH
        _obsidian = ObsidianService(OBSIDIAN_VAULT_PATH)
        obsidian_path = _obsidian.write_note(
            project_name=name,
            slug=slug,
            title=final_title,
            content_html=optimized_html.strip(),
            metadata={
                "keyword": keyword,
                "seo_score": round(seo_score, 1),
                "word_count": word_count,
                "source": "hermes-suggestie",
            },
        )
        if obsidian_path:
            logger.info(f"[SEO-pipeline] Ook opgeslagen in Obsidian vault: {obsidian_path}")
    except Exception as e:
        logger.warning(f"[SEO-pipeline] Obsidian opslag mislukt (niet kritisch): {e}")

    # ── Publicatie-gate: onder de drempel blijft het een concept ────
    # De score was voorheen alleen een logregel; nu blokkeert hij écht de
    # live-publicatie en de zoekmachine-indiening.
    passed_gate = seo_score >= PUBLISH_MIN_SCORE

    # ── FASE 4b: Live publiceren op de eigen site van het project ────
    # Publish-config komt per project uit de env: {PROJECT}_PUBLISH_URL/_PUBLISH_KEY
    # (bv. WEAREIMPACT_PUBLISH_URL, BIJEEN_PUBLISH_URL). Zonder config wordt er
    # bewust NIET gepubliceerd — voorheen ging alles hardcoded naar weareimpact.nl,
    # waardoor Bijeen-content op de verkeerde site terecht kon komen.
    live_result = None
    if passed_gate:
        env_prefix = re.sub(r"[^A-Z0-9]", "", name.upper())
        publish_url = os.getenv(f"{env_prefix}_PUBLISH_URL", "").strip()
        publish_key = os.getenv(f"{env_prefix}_PUBLISH_KEY", "").strip()
        if not publish_url or not publish_key:
            _log_activity(name, "live-overgeslagen",
                          f"'{body.title}': geen {env_prefix}_PUBLISH_URL/_PUBLISH_KEY geconfigureerd — alleen lokaal opgeslagen")
        else:
            base_url = site.get("base_url", "").rstrip("/")
            _set_job(job_id, phase=f"Live zetten op {base_url or publish_url}...", percent=97)
            # Verwijder zichtbare Meta-/Suggestie-blokken uit de body vóórdat
            # we publiceren (de AI levert die soms als H2's onderaan de tekst).
            optimized_html, parsed_title, parsed_desc = \
                content_pipeline._strip_meta_and_suggestions(optimized_html)
            # De blog-API's verschillen per site: weareimpact.nl heeft een
            # dedicated /api/publish (incl. socials + indexing), bijeen.app een
            # generieke /api/blog met status-veld.
            if env_prefix == "BIJEEN":
                # Ook hier eerst de meta-/suggestie-blokken uit de body halen
                # (anders komen ze als zichtbare H2's onderaan het artikel op
                # bijeen.app terecht — net als eerder bij weareimpact.nl).
                bijeen_html, bijeen_meta_title, bijeen_meta_desc = \
                    content_pipeline._strip_meta_and_suggestions(optimized_html)
                first_p = re.search(r"<p>(.*?)</p>", bijeen_html, re.S)
                excerpt = re.sub(r"<[^>]+>", "", first_p.group(1)).strip()[:200] if first_p else ""
                payload = {
                    "title": final_title,
                    "content": bijeen_html.strip(),
                    "excerpt": excerpt,
                    "metaTitle": (bijeen_meta_title or final_title)[:60],
                    "metaDescription": bijeen_meta_desc or meta_desc,
                    "tags": [keyword] if keyword else [],
                    "status": "published",
                }
            else:
                payload = {
                    "title": final_title,
                    "content": optimized_html.strip(),
                    "slug": slug,
                    "seoDescription": meta_desc,
                    "tags": [keyword] if keyword else [],
                    "source": "agent-os",
                }
            try:
                import httpx
                resp = await asyncio.to_thread(
                    httpx.post,
                    publish_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {publish_key}"},
                    timeout=90,
                )
                if resp.status_code == 201:
                    live_result = resp.json()
                    if "post" in live_result:  # generieke blog-API (bijeen.app)
                        post = live_result["post"]
                        live_result.setdefault("url", f"{base_url}/blog/{post.get('slug', slug)}")
                    socials = live_result.get("socials", [])
                    per_status = {
                        label: "/".join(s["platform"] for s in socials if s.get("status") == status_key)
                        for label, status_key in (("geplaatst", "posted"), ("concept", "draft"), ("mislukt", "failed"))
                    }
                    social_note = ", ".join(f"{k}: {v}" for k, v in per_status.items() if v) or "geen"
                    indexing = live_result.get("indexing", {})
                    _log_activity(
                        name, "live",
                        f"'{body.title}' LIVE op {live_result.get('url', '?')} — "
                        f"IndexNow: {indexing.get('indexnow', '?')}, Google: {indexing.get('google', '?')}, "
                        f"socials → {social_note}",
                        artifact=live_result.get("url", ""),
                    )
                else:
                    _log_activity(name, "live-fout",
                                  f"'{body.title}': publish-API gaf {resp.status_code} — {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Live-publicatie op {publish_url} mislukt: {e}")
                _log_activity(name, "live-fout", f"'{body.title}': {str(e)[:200]}")

    # ── FASE 5: Google Search Console-indiening + Bing-ping (alleen als artikel live staat) ──
    # Google's oude google.com/ping-endpoint is sinds juni 2023 uitgefaseerd en werkt niet meer —
    # dit gebruikt in plaats daarvan de échte Search Console sitemaps.submit-API (gsc.py).
    ping_results = {}
    if not passed_gate:
        ping_results = {
            "status": "geblokkeerd_door_gate",
            "note": f"SEO-score {seo_score:.1f}/10 onder drempel {PUBLISH_MIN_SCORE} — niet ingediend bij zoekmachines",
        }
    else:
        try:
            import httpx
            base_url = site.get("base_url", "").rstrip("/")
            page_url = f"{base_url}/blog/{slug}"

            # Check of de pagina al live is (anders heeft indienen geen zin)
            try:
                check = httpx.get(page_url, timeout=5)
                page_is_live = check.status_code < 400
            except Exception:
                page_is_live = False

            if page_is_live:
                gsc_property = (site.get("gsc_property") or "").strip()
                if gsc_property and gsc_service.is_configured():
                    ok, detail = gsc_service.submit_sitemap(gsc_property, f"{base_url}/sitemap.xml")
                    ping_results["google_search_console"] = "ingediend" if ok else f"fout: {detail[:100]}"
                else:
                    ping_results["google_search_console"] = "overgeslagen (geen gsc_property/service-account)"

                try:
                    resp = httpx.get(f"https://www.bing.com/ping?sitemap={base_url}/sitemap.xml", timeout=10)
                    ping_results["bing"] = resp.status_code
                except Exception as e:
                    ping_results["bing"] = str(e)[:50]
            else:
                ping_results = {"status": "pagina_nog_niet_live", "note": "Indienen overgeslagen — artikel eerst handmatig publiceren op Vercel/Next.js"}

        except Exception as e:
            logger.warning(f"Indienen bij zoekmachines mislukt: {e}")
            ping_results = {"error": str(e)[:100]}

    # ── Log activiteit ─────────────────────────────────────────────
    if passed_gate:
        act_detail = f"'{body.title}' (SEO-score: {seo_score:.1f}/10, {word_count}w, opgeslagen in content/{slug}.html)"
    else:
        act_detail = (
            f"'{body.title}' (SEO-score: {seo_score:.1f}/10 — onder drempel {PUBLISH_MIN_SCORE}, "
            f"{word_count}w, als CONCEPT opgeslagen in content/{slug}.html — NIET gepubliceerd)"
        )
    _log_activity(name, "publicatie", act_detail)

    # Probeer Netlify publicatie als credentials aanwezig zijn — alleen boven de gate
    netlify_result = None
    if not passed_gate:
        logger.info(
            f"Netlify publish geblokkeerd voor '{body.title}': score {seo_score:.1f} < {PUBLISH_MIN_SCORE}"
        )
    elif site.get("publish_api_url") and site.get("publish_api_key_set"):
        try:
            netlify_result = await publish_service.publish_article(
                site_id=site_id, title=final_title,
                html_body=optimized_html.strip(), slug=slug,
            )
        except Exception as e:
            logger.info(f"Netlify publish skipped (niet geconfigureerd voor {name}): {e}")
            netlify_result = None

    # ── Kans terugkoppelen met de echte live-URL ─────────────────────
    # Zodra het artikel daadwerkelijk live staat, koppelen we de URL terug aan de
    # bijbehorende Demand-Engine-kans (via site_id + query) en zetten die op
    # 'published' — mét published_at. Zo toont de Kansen-card in de UI een
    # klikbare live-link ipv alleen de handmatige 'Gepubliceerd'-vink.
    live_url_final = live_result.get("url") if live_result else None
    if live_url_final and passed_gate:
        try:
            from ..seo import engine as demand_engine
            with demand_engine.get_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM opportunities "
                    "WHERE site_id = ? AND (query = ? OR query LIKE ?) "
                    "ORDER BY scanned_at DESC LIMIT 1",
                    (site_id, keyword, f"{keyword}%"),
                ).fetchone()
            if row:
                demand_engine.update_opportunity(
                    row["id"],
                    status="published",
                    live_url=live_url_final,
                    published_at=demand_engine._now(),
                )
                _log_activity(name, "kans-gelinkt",
                              f"'{body.title}' gekoppeld aan kans (live: {live_url_final})",
                              artifact=live_url_final)
        except Exception as e:
            logger.warning(f"[SEO-pipeline] Kans-terugkoppeling mislukt (niet kritisch): {e}")

    return {
        "success": True,
        "title": final_title,
        "local_path": f"content/{slug}.html",
        "slug": slug,
        "word_count": word_count,
        "seo_score": round(seo_score, 1),
        "seo_review": seo_review,
        "qc_report": qc_report,
        "optimized": rounds > 0,
        "optimization_rounds": rounds,
        "world_class": review["score"] >= WORLD_CLASS_SCORE,
        "passed_gate": passed_gate,
        "gate_note": None if passed_gate else (
            f"SEO-score {seo_score:.1f}/10 onder drempel {PUBLISH_MIN_SCORE} — "
            "als concept opgeslagen, niet gepubliceerd of ingediend"
        ),
        "netlify_url": netlify_result.get("url") if netlify_result else None,
        "live_url": live_result.get("url") if live_result else None,
        "socials": live_result.get("socials", []) if live_result else [],
        "ping_results": ping_results,
    }


# ── Content-wachtrij (2x/week auto-content, review-gate) ──────────────
# Dunne per-project proxy naar backend/domains/content_queue — de canonieke
# (site_id-gebaseerde) API blijft bruikbaar voor cross-project views.

@router.get("/{name}/content-queue")
def project_content_queue(name: str, status: Optional[str] = Query(None)):
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")
    from ..content_queue.router import _with_parsed_social_copy
    jobs = content_pipeline.list_jobs(site_id=site["id"], status=status)
    return [_with_parsed_social_copy(j) for j in jobs]


@router.post("/{name}/content-queue/run-now")
async def project_content_queue_run_now(name: str, count: Optional[int] = Query(None, ge=1, le=10)):
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")
    full_site = sites_service.get_site(site["id"])
    job_ids = await content_pipeline.run_content_batch(full_site, count=count)
    if not job_ids:
        return {"success": False, "detail": "Geen nieuwe kansen — voer eerst een Demand Engine-scan uit."}
    return {"success": True, "job_ids": job_ids, "job_id": job_ids[0]}


@router.post("/{name}/content-queue/{job_id}/approve")
async def project_content_queue_approve(name: str, job_id: str):
    try:
        result = await content_pipeline.approve_and_publish(job_id)
        return {"success": True, "result": result}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/{name}/content-queue/{job_id}/reject")
def project_content_queue_reject(name: str, job_id: str):
    try:
        content_pipeline.reject_job(job_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/{name}/content-queue/{job_id}/regenerate")
async def project_content_queue_regenerate(name: str, job_id: str):
    from ..content_queue.router import _with_parsed_social_copy
    try:
        new_id = await content_pipeline.regenerate_job(job_id)
        return _with_parsed_social_copy(content_pipeline.get_job(new_id))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


# ── Striking Distance Kansen (via Demand Engine) ─────────────────────

@router.get("/{name}/kansen")
def project_kansen(name: str, status: Optional[str] = Query(None)):
    """Haal striking-distance kansen voor een project via de Demand Engine."""
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")

    from ..seo import engine as demand_engine
    kansen = demand_engine.list_opportunities(site_id=site["id"], status=status)
    return {
        "site": {"name": site["name"], "gsc_property": site.get("gsc_property", "")},
        "count": len(kansen),
        "kansen": kansen,
    }


# ── Technische SEO (via GSC) ────────────────────────────────────────

@router.get("/{name}/tech-seo")
def project_tech_seo(name: str):
    """Haal technische SEO-data: index coverage, sitemaps, crawl stats."""
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")

    gsc_prop = site.get("gsc_property", "")
    if not gsc_prop:
        return {"error": "Geen GSC property ingesteld"}

    try:
        from ..seo.gsc import fetch_page_performance, fetch_query_performance, is_configured
        if not is_configured():
            return {"error": "GSC niet geconfigureerd"}
    except Exception as e:
        return {"error": str(e)[:200]}

    # Index coverage via page performance (laatste 7 dagen, hoog row_limit)
    try:
        pages = fetch_page_performance(gsc_prop, days=7, row_limit=1000)
        queries = fetch_query_performance(gsc_prop, days=28, row_limit=1000)
        prev_pages = fetch_page_performance(gsc_prop, days=7, row_limit=1000, end_offset=7)
    except Exception as e:
        return {"error": str(e)[:200]}

    # Index coverage: simpelweg totaal unieke pagina's in de laatste 7 dagen
    total_indexed = len(pages)
    prev_indexed = len(prev_pages)

    # Pagina's per type afleiden uit URL-patroon
    page_types = {"kennisbank": 0, "blog": 0, "diensten": 0, "overig": 0}
    for p in pages:
        url = p.get("page", "").lower()
        if "/kennisbank/" in url:
            page_types["kennisbank"] += 1
        elif "/blog/" in url:
            page_types["blog"] += 1
        elif "/diensten/" in url or "/service/" in url:
            page_types["diensten"] += 1
        else:
            page_types["overig"] += 1

    # Top queries voor inzicht
    top_queries = sorted(queries, key=lambda x: x["clicks"], reverse=True)[:10]

    return {
        "site": {"name": site["name"], "gsc_property": gsc_prop},
        "index_coverage": {
            "total": total_indexed,
            "prev": prev_indexed,
            "change": total_indexed - prev_indexed,
            "by_type": page_types,
        },
        "sitemap": {
            "url": f"https://{gsc_prop.replace('sc-domain:', '')}/sitemap.xml" if gsc_prop.startswith("sc-domain:") else "",
        },
        "top_queries_28d": top_queries,
    }


# ── Daily Trends (voor grafieken) ────────────────────────────────────

@router.get("/{name}/trends")
def project_trends(name: str, days: int = Query(28, ge=7, le=90)):
    """Dagelijkse trenddata voor grafieken: clicks, impressies, positie."""
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")
    gsc_prop = site.get("gsc_property", "")
    if not gsc_prop:
        return {"error": "Geen GSC property ingesteld"}

    try:
        from ..seo.gsc import fetch_daily_performance, is_configured
        if not is_configured():
            return {"error": "GSC niet geconfigureerd"}
        daily = fetch_daily_performance(gsc_prop, days=days)
        prev = fetch_daily_performance(gsc_prop, days=days, end_offset=days)
    except Exception as e:
        return {"error": str(e)[:200]}

    return {
        "daily": daily,
        "prev_period": prev,
        "totals": {
            "clicks": sum(d["clicks"] for d in daily),
            "impressions": sum(d["impressions"] for d in daily),
            "avg_position": round(sum(d["position"] * d["impressions"] for d in daily) / max(sum(d["impressions"] for d in daily), 1), 1),
        },
    }


# ── PageSpeed Insights ───────────────────────────────────────────────

@router.get("/{name}/pagespeed")
def project_pagespeed(name: str, strategy: str = Query("mobile", pattern="^(mobile|desktop)$")):
    """Core Web Vitals via PageSpeed Insights API."""
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")
    base_url = site.get("base_url", "").rstrip("/")
    if not base_url:
        return {"error": "Geen base_url voor dit project"}

    test_url = base_url  # test homepage
    api_key = os.environ.get("PAGESPEED_API_KEY", "")
    url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={test_url}&strategy={strategy}"
    if api_key:
        url += f"&key={api_key}"

    try:
        import httpx
        resp = httpx.get(url, timeout=30)
        data = resp.json()
        lh = data.get("lighthouseResult", {})
        audits = lh.get("audits", {})
        categories = lh.get("categories", {})

        def _score(cat):
            s = categories.get(cat, {}).get("score")
            return round(s * 100) if s is not None else None

        def _audit_val(aid):
            a = audits.get(aid, {})
            return a.get("displayValue") or (a.get("numericValue") and f"{a['numericValue']:.1f}") or "-"

        return {
            "url": test_url,
            "strategy": strategy,
            "scores": {
                "performance": _score("performance"),
                "accessibility": _score("accessibility"),
                "seo": _score("seo"),
                "best_practices": _score("best-practices"),
            },
            "metrics": {
                "lcp": _audit_val("largest-contentful-paint"),
                "fcp": _audit_val("first-contentful-paint"),
                "tbt": _audit_val("total-blocking-time"),
                "cls": _audit_val("cumulative-layout-shift"),
                "si": _audit_val("speed-index"),
            },
        }
    except Exception as e:
        return {"error": str(e)[:200]}


# ── Keyword Research / Gaps ──────────────────────────────────────────

@router.get("/{name}/keyword-gaps")
def project_keyword_gaps(name: str, days: int = Query(28, ge=7, le=90)):
    """Analyseer keyword-gaps: queries met hoge impressies maar lage CTR + suggesties."""
    site = _resolve_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")
    gsc_prop = site.get("gsc_property", "")
    if not gsc_prop:
        return {"error": "Geen GSC property ingesteld"}

    try:
        from ..seo.gsc import fetch_query_performance, is_configured
        if not is_configured():
            return {"error": "GSC niet geconfigureerd"}
        queries = fetch_query_performance(gsc_prop, days=days, row_limit=500)
    except Exception as e:
        return {"error": str(e)[:200]}

    # Hoge impressies, lage CTR = potentie
    gaps = [q for q in queries if q["impressions"] >= 50 and q["ctr"] < 3.0]
    gaps.sort(key=lambda x: x["impressions"], reverse=True)

    # Hoge positie (4-20) maar weinig clicks = striking distance
    striking = [q for q in queries if 4 <= q["position"] <= 20 and q["impressions"] >= 20]
    striking.sort(key=lambda x: x["impressions"], reverse=True)

    # Best presterend
    best = sorted(queries, key=lambda x: x["clicks"], reverse=True)[:20]

    return {
        "total_queries": len(queries),
        "gaps": gaps[:15],
        "striking_distance": striking[:15],
        "best_performers": best[:10],
        "categories": {
            "clicks": sum(q["clicks"] for q in queries),
            "impressions": sum(q["impressions"] for q in queries),
            "avg_ctr": round(sum(q["ctr"] for q in queries) / max(len(queries), 1), 2),
            "avg_position": round(sum(q["position"] * q["impressions"] for q in queries) / max(sum(q["impressions"] for q in queries), 1), 1),
        },
    }

"""
Goal Mode Service — autonome doeldecompositie, executie-loop & self-correctie.

Architectuur:
┌─────────────────────────────────────────────────────────────────┐
│  Goal Mode Execution Loop                                       │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ Fase 1:  │ → │ Fase 2:  │ → │ Fase 3:  │ → │ Fase 4:  │    │
│  │ Research │   │ Content  │   │ Launch   │   │ Analyse  │    │
│  │ ┌──────┐ │   │ ┌──────┐ │   │ ┌──────┐ │   │ ┌──────┐ │    │
│  │ │Task A│ │   │ │Task C│ │   │ │Task E│ │   │ │Task G│ │    │
│  │ │Task B│ │   │ │Task D│ │   │ │Task F│ │   │ │Task H│ │    │
│  │ └──────┘ │   │ └──────┘ │   │ └──────┘ │   │ └──────┘ │    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘    │
│                                                                │
│  Self-correctie: retry → alternative → mark failed → continue   │
└─────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...shared.config import BASE_DIR, OBSIDIAN_VAULT_PATH, hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...domains.delegate import event_bus
from ...infinite_context import InfiniteContextEngine

logger = logging.getLogger(__name__)

_BG_TASKS: set[asyncio.Task] = set()
_ACTIVE_GOAL_IDS: set[str] = set()
GOALS_WORKSPACE = BASE_DIR / "projects" / "_goals"

# ── Infinite Context Engine (Obsidian-bridge) ────────────────────────
_infinite_ctx = InfiniteContextEngine(OBSIDIAN_VAULT_PATH)

# ── Skill → Profile mapping (uitbreidbaar) ──────────────────────────
SKILL_PROFILES: Dict[str, str] = {
    "research":       "SEO Specialist",
    "content-writer": "Content Writer",
    "content-editor": "Content Editor",
    "content-judge":  "Content Judge",
    "seo":            "SEO Specialist",
    "video-builder":  "Video Creator",
    "video-director": "Video Director",
    "outreach":       "Outreach Agent",
    "publisher":      "Content Writer",
    "analyst":        "SEO Specialist",
    "designer":       "Content Writer",
}

SKILL_DESCRIPTIONS: Dict[str, str] = {
    "research":       "Markt-, doelgroep- en concurrentie-onderzoek",
    "content-writer": "Schrijven van blogartikelen, landingspagina's en copy",
    "content-editor": "Eindredactie, kwaliteitscontrole en optimalisatie van content",
    "content-judge":  "Strenge beoordeling van content tegen SEO- en kwaliteitsstandaarden",
    "seo":            "Zoekwoordanalyse, optimalisatie en technische SEO",
    "video-builder":  "Scripts en storyboards voor video-content",
    "video-director": "Regisseren van videoproductie: planning, stijlhandleiding en montage",
    "outreach":       "Prospecting, lead-generatie en outreach-teksten",
    "publisher":      "Publiceren naar website en pingen naar zoekmachines",
    "analyst":        "Data-analyse, KPI-rapportage en trendanalyse",
    "designer":       "Visuele content, infographics en wireframes",
}

# ── Anti-fabricatie-guardrail ─────────────────────────────────────────
# Deze skills vereisen in werkelijkheid een extern systeem dat de
# goal-engine NIET heeft (publiceren, versturen, echte metingen). Zonder
# guardrail "voltooit" het model zulke taken met verzonnen resultaten
# die er als echte rapportage uitzien.
_CONCEPT_ONLY_SKILLS = {"publisher", "outreach"}
_DATA_SKILLS = {"analyst"}

# Synthese-taken (schrijven, redigeren, beoordelen) gaan naar Claude — het
# sterkste beschikbare model — met terugval op Hermes als Claude onbereikbaar
# is. Research/analyse op Hermes krijgt échte tools (websearch, GA, Obsidian)
# in plaats van alleen een prompt. Uitschakelen kan met GOAL_USE_CLAUDE=0.
import os as _os
_GOAL_USE_CLAUDE = _os.getenv("GOAL_USE_CLAUDE", "1") not in ("0", "false", "no")

_NO_FABRICATION_RULE = (
    "\n\nHARDE REGELS (verplicht, gaan boven alles):\n"
    "- Verzin NOOIT cijfers, statistieken, meetresultaten of uitkomsten die niet "
    "letterlijk in de meegegeven context staan.\n"
    "- Claim NOOIT dat je iets hebt gepubliceerd, verstuurd, gedeployed of gemeten — "
    "je hebt geen toegang tot externe systemen. Beschrijf wat er klaarligt en wat een "
    "mens nog moet doen.\n"
    "- Ontbreekt data die je nodig hebt? Zeg dat expliciet en lever een concreet "
    "plan of checklist in plaats van een verzonnen resultaat."
)

_CONCEPT_BANNER = (
    "> ⚠️ **CONCEPT — geen echte actie uitgevoerd.** Deze taak is door een AI "
    "voorbereid; er is niets gepubliceerd, verstuurd of gemeten. Menselijke "
    "uitvoering en controle vereist.\n\n"
)

_NO_DATA_BANNER = (
    "> ⚠️ **MEETPLAN — geen echte data beschikbaar.** Voor dit project is geen "
    "analytics-koppeling gevonden; dit document bevat geen echte metingen.\n\n"
)


_RESEARCH_SKILLS = {"research"}


async def _is_topic_relevant(site: dict, title: str, html_body: str) -> tuple:
    """Guard: hoort dit artikel inhoudelijk bij deze site? Voorkomt dat een
    publisher-taak content uit een andere goal/project of een agent-tussenstap
    (bv. 'Eindredactie', 'Rapport: Status', 'Bestanden opslaan in vault') als
    echt artikel op de verkeerde site publiceert.

    Twee lagen:
      1. Snelle heuristiek — blokkeer duidelijke NIET-artikel/tussenstap-titels.
      2. LLM-oordeel — past het onderwerp bij de site (naam + domein + bestaande
         artikelen)? Bij twijfel of LLM-fout: TOELATEN (fail-open), zodat we geen
         legitieme content tegenhouden; de heuristiek vangt de grofste fouten.

    Retourneert (relevant: bool, reden: str).
    """
    t = (title or "").strip().lower()
    # Laag 1: tussenstap-/procestitels die nooit een publiceerbaar artikel zijn.
    _NON_ARTICLE = (
        "eindredactie", "consistentiecheck", "rapport:", "status aanpassing",
        "bestanden opslaan", "publicatieklaar", "opslaan in vault", "archiveren",
        "content redactie", "review", "quality check", "qc-rapport",
    )
    if any(k in t for k in _NON_ARTICLE):
        return (False, f"titel lijkt een agent-tussenstap, geen artikel: '{title}'")

    # Laag 2: LLM-relevantie tegen de site-context.
    try:
        from ..publish import content_pipeline
        from ..seo import sites as sites_service
        # Enkele bestaande paginatitels als onderwerp-context.
        try:
            cand = content_pipeline.article_writer._link_candidates(site)[:8]
            voorbeelden = "; ".join(c.get("title", "") for c in cand if c.get("title"))
        except Exception:
            voorbeelden = ""
        naam = site.get("name", "")
        domein = site.get("base_url", "")
        plain = re.sub(r"<[^>]+>", " ", html_body or "")
        plain = re.sub(r"\s+", " ", plain).strip()[:1200]
        system = (
            "Je bent een strenge redactie-gatekeeper. Bepaal of een artikel "
            "inhoudelijk THUISHOORT op een specifieke website. Antwoord met "
            "alleen JA of NEE, gevolgd door een korte reden."
        )
        prompt = (
            f"Website: {naam} ({domein}).\n"
            f"Voorbeelden van bestaande artikelen op deze site: {voorbeelden or '(onbekend)'}.\n\n"
            f"Kandidaat-artikel titel: {title}\n"
            f"Kandidaat-artikel begin: {plain}\n\n"
            "Hoort dit artikel qua onderwerp op deze website? Antwoord JA of NEE + reden."
        )
        ans = (await content_pipeline._llm(system, prompt, max_tokens=120)).strip()
        if re.match(r"^\s*nee\b", ans, re.IGNORECASE):
            return (False, f"LLM: past niet bij {naam} — {ans[:160]}")
        return (True, "relevant")
    except Exception as e:
        logger.warning(f"Relevantie-check faalde voor '{title}' ({e}) — toegelaten (fail-open)")
        return (True, "relevantie-check overgeslagen (fout)")


async def _stage_to_wachtrij(goal_id: str, task_title: str, project: str
                             ) -> Tuple[Optional[Tuple[str, str, int]], str]:
    """ECHTE actie voor publisher-taken: pak het artikel uit eerdere
    content-taken van deze goal en zet het als review-job in de Wachtrij
    (content_jobs, status pending_review). Goedkeuren in de Wachtrij-tab
    publiceert het daarna écht (Netlify + social) — de menselijke
    review-gate blijft dus van kracht.

    Retourneert ((job_id, artikel_titel, seo_score), "") bij succes, of
    (None, reden) als er niets te stagen viel. Die reden is geen detail: de
    publisher-taak faalt erop (zie `_route_by_skill`), want een taak die de
    échte actie niet uitvoert mag niet als 'voltooid' tellen — anders dempt
    een doel vol concepten de dashboard-alerts alsof het werk gedaan is."""
    try:
        from ..publish import content_pipeline
        from ..seo import sites as sites_service

        norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
        site = next(
            (s for s in sites_service.list_sites() if norm(s["name"]) == norm(project or "")),
            None,
        )
        if not site:
            return None, (f"geen site gekoppeld aan project '{project}' — "
                          "publiceren kan alleen naar een geregistreerde site")

        with get_conn() as conn:
            row = conn.execute(
                "SELECT title, result FROM goal_tasks WHERE goal_id = ? "
                "AND skill IN ('content-writer', 'content-editor', 'seo') "
                "AND status = 'completed' AND result IS NOT NULL AND length(result) > 400 "
                "ORDER BY updated_at DESC LIMIT 1",
                (goal_id,),
            ).fetchone()
        if not row:
            return None, ("geen publiceerbaar artikel in dit doel — eerdere "
                          "content-taken leverden niets van voldoende lengte op")

        import markdown as md_lib
        html_body = md_lib.markdown(row["result"], extensions=["tables"])

        title = row["title"] or task_title
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html_body, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip() or title

        full_site = sites_service.get_site(site["id"]) or site

        # Publiceerbaarheidsgate, vóór de kwaliteitsgate: een intern plan of
        # rapport is geen artikel, ongeacht hoe goed het geschreven is. Reken
        # hier niet op de score — "Plan: Directe antwoorden toevoegen aan alle
        # 28 pagina's" haalde 85/100 en stond daardoor live op bijeen.app.
        intern = content_pipeline.is_internal_document(title, html_body)
        if intern:
            _log_activity(
                goal_id, "wachtrij_geweigerd",
                f"'{title}' niet gestaged — {intern}; resultaat blijft een concept",
            )
            return None, f"'{title}' is geen artikel maar een intern document ({intern})"

        # Kwaliteitsgate: review → verbeter → review (max 3 rondes). Haalt het
        # resultaat de grens niet, dan wordt er NIETS gestaged — de taak valt
        # terug op een concept en de Wachtrij blijft vrij van niet-publiceerbare
        # items.
        from ...shared.config import CONTENT_MIN_SCORE
        import httpx as _httpx
        try:
            html_body, review = await content_pipeline.review_and_improve(full_site, "", html_body)
            seo_score = int(review.get("score", 0))
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.ConnectTimeout,
                _httpx.ReadTimeout, _httpx.WriteTimeout, _httpx.PoolTimeout) as e:
            # Tijdelijke LLM/network-outage (bijv. de openmodel-proxy is even
            # down). Niet stil naar score=0 vertalen en de taak als "slechte
            # content" blokkeren — dat is oneerlijk én dempt de alerts. De
            # goal-loop retry't deze fout (zie de except Exception daarboven),
            # dus de publish wordt later opnieuw geprobeerd zodra de LLM weer
            # bereikbaar is. Alleen een échte, bereikbare review die onder de
            # grens zit blokkeert permanent.
            logger.warning(f"Review-loop kon '{title}' niet scoren (LLM tijdelijk onbereikbaar): {e}")
            raise
        except Exception as e:
            logger.warning(f"Review/verbeter-loop mislukt voor '{title}': {e}")
            seo_score = 0
        if seo_score < CONTENT_MIN_SCORE:
            _log_activity(
                goal_id, "wachtrij_geweigerd",
                f"'{title}' haalde {seo_score}/100 (grens {CONTENT_MIN_SCORE}) — "
                "niet gestaged; resultaat blijft een concept",
            )
            return None, (f"'{title}' haalde de kwaliteitsgate niet "
                          f"({seo_score}/100, grens {CONTENT_MIN_SCORE})")

        # Relevantie-gate: hoort dit artikel inhoudelijk bij deze site? Blokkeert
        # off-topic content (uit een andere goal/project) en agent-tussenstappen
        # die anders — mits SEO-score hoog genoeg — op de verkeerde site belanden.
        relevant, reden = await _is_topic_relevant(full_site, title, html_body)
        if not relevant:
            _log_activity(
                goal_id, "wachtrij_geweigerd",
                f"'{title}' niet gestaged — {reden}",
            )
            return None, f"'{title}' hoort inhoudelijk niet bij deze site ({reden})"

        social: Dict[str, str] = {}
        try:
            social = await content_pipeline._generate_social_copy(full_site, title, "", html_body)
        except Exception:
            pass

        job_id = content_pipeline.create_job(
            site_id=site["id"],
            title=title,
            keyword="",
            rationale=f"Uit goal {goal_id} — publisher-taak '{task_title}'",
            blog_html=html_body,
            seo_score=seo_score,
            social_copy=social,
            image_bytes=None,
            slug=content_pipeline.slugify_title(title),
        )
        return (job_id, title, seo_score), ""
    except Exception as e:
        logger.warning(f"Wachtrij-staging mislukt voor goal {goal_id}: {e}")
        return None, f"staging naar de Wachtrij mislukte: {str(e)[:200]}"


async def _web_research_context(title: str, description: str) -> str:
    """Echte webresultaten (Tavily) voor research-taken — of leeg zonder API-key.

    Hiermee onderzoekt de agent écht in plaats van te putten uit trainingsdata:
    de resultaten (met bron-URL) gaan de prompt in en bronvermelding is verplicht.
    """
    from ...shared.config import TAVILY_API_KEY
    if not TAVILY_API_KEY:
        return ""
    query = f"{title} {description}".strip()[:380]
    if not query:
        return ""
    try:
        def _search():
            from tavily import TavilyClient
            client = TavilyClient(api_key=TAVILY_API_KEY)
            return client.search(query=query, max_results=6, search_depth="advanced")

        response = await asyncio.to_thread(_search)
        results = response.get("results", [])
        if not results:
            return ""
        lines = ["## ECHTE webresultaten (zojuist opgezocht via Tavily)"]
        for r in results:
            content = (r.get("content") or "").strip().replace("\n", " ")[:400]
            lines.append(f"- **{r.get('title', '')}** — {content}\n  Bron: {r.get('url', '')}")
        lines.append(
            "\nGebruik UITSLUITEND bovenstaande bronnen voor feiten en cijfers, en "
            "vermeld bij elke claim de bron-URL. Geen bronnen voor een bewering? "
            "Benoem dat dan expliciet in plaats van te gokken."
        )
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Web-research context mislukt: {e}")
        return ""


async def _real_analytics_context(project: str) -> str:
    """Echte GSC-cijfers voor analyst-taken — of leeg als niet beschikbaar."""
    if not project:
        return ""
    try:
        from ..seo import gsc, sites as sites_service

        if not gsc.is_configured():
            return ""
        norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
        site = next((s for s in sites_service.list_sites() if norm(s["name"]) == norm(project)), None)
        if not site or not site.get("gsc_property"):
            return ""
        prop = site["gsc_property"]
        pages = await asyncio.to_thread(lambda: gsc.fetch_page_performance(prop, days=28, row_limit=250))
        queries = await asyncio.to_thread(lambda: gsc.fetch_query_performance(prop, days=28, row_limit=250))
        total_clicks = sum(p["clicks"] for p in pages)
        total_imps = sum(p["impressions"] for p in pages)
        avg_pos = round(sum(p["position"] * p["impressions"] for p in pages) / total_imps, 1) if total_imps else 0
        lines = [
            "## ECHTE meetdata (Google Search Console, laatste 28 dagen)",
            f"- Site: {site.get('base_url') or site['name']}",
            f"- Totaal: {total_clicks} klikken · {total_imps} impressies · gem. positie {avg_pos}",
            f"- Aantal pagina's met vertoningen: {len(pages)}",
            "- Top zoekwoorden (klikken · impressies · positie):",
        ]
        for q in sorted(queries, key=lambda x: x["impressions"], reverse=True)[:10]:
            lines.append(f"  - \"{q['query']}\": {q['clicks']} · {q['impressions']} · {round(q['position'], 1)}")
        lines.append(
            "\nBaseer je analyse UITSLUITEND op bovenstaande cijfers. "
            "Er is geen andere meetdata beschikbaar (geen GA4-cijfers, geen conversies)."
        )
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Analyst-datacontext ophalen mislukt voor '{project}': {e}")
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _slugify(text: str) -> str:
    s = re.sub(r'[^a-z0-9\s-]', '', text.lower()).strip().replace(' ', '-')
    return re.sub(r'-+', '-', s)[:40]


# ═════════════════════════════════════════════════════════════════════
#  1. DOELDECOMPOSITIE  —  Hermes vertaalt goal → plan
# ═════════════════════════════════════════════════════════════════════

_DECOMPOSITION_SYSTEM = (
    "Je bent een senior projectmanager en strategisch planner. "
    "Je ontvangt een overkoepelend langetermijndoel en splitst dit op in "
    "chronologische fasen, elk met concrete, uitvoerbare sub-taken. "
    "Elke taak heeft een 'skill'-toewijzing (een van: research, content-writer, content-editor, content-judge, seo, video-builder, video-director, outreach, publisher, analyst, designer) en optionele "
    "dependencies (verwijs naar andere taak-id's binnen dezelfde fase)."
)

_DECOMPOSITION_PROMPT_TEMPLATE = (
    "Splits het volgende doel op in een maximaal uitvoerbaar plan:\n\n"
    "DOEL: {objective}\n"
    "PROJECT: {project}\n\n"
    "Regels:\n"
    "- Maximaal 4 fasen, elke fase 2-4 taken\n"
    "- Taken binnen een fase mogen afhankelijk zijn van elkaar (dependencies)\n"
    "- Fasen lopen altijd chronologisch (fase 2 start pas als fase 1 klaar is)\n"
    "- Elke taak heeft 1 skill (research | content-writer | content-editor | content-judge | seo | video-builder | video-director | outreach | publisher | analyst | designer)\n"
    "- Wees concreet: geen 'onderzoek doen' maar 'doelgroep-analyse uitvoeren op basis van GSC-data'\n"
    "- SPLITS grote deliverables op: elk artikel, elke blogpost, elke pagina, elke checklist en elk template is een EIGEN content-writer taak (geen enkele reuzentaak met alles erin)\n"
    "- Voor elk artikel/landingspagina/dat gepubliceerd moet worden, voeg een APARTE taak toe met skill 'publisher' (deze staged het klaar in de Wachtrij ter review - de mens keurt pas goed)\n"
    "- Zet afhankelijkheden tussen taken die elkaars output gebruiken (bv. content-editor na content-writer, analyst na research)\n\n"
    "Antwoord UITSLUITEND in JSON-formaat (geen markdown):\n"
    '{{\n'
    '  "plan_summary": "Korte samenvatting van het plan (1-2 zinnen)",\n'
    '  "estimated_duration": "Bijv. 2-3 dagen",\n'
    '  "phases": [\n'
    '    {{\n'
    '      "title": "Fase 1: Research & Strategie",\n'
    '      "description": "Overzicht van deze fase",\n'
    '      "tasks": [\n'
    '        {{\n'
    '          "title": "Doelgroepanalyse",\n'
    '          "description": "Analyseer de doelgroep via GSC-data",\n'
    '          "skill": "research",\n'
    '          "dependencies": []\n'
    '        }}\n'
    '      ]\n'
    '    }}\n'
    '  ]\n'
    '}}'
)


async def _stream_text(system_prompt: str, user_prompt: str, max_tokens: int = 3000,
                     model_override: Optional[str] = None) -> str:
    """Helper: stream response van Hermes.

    Optioneel model_override routeert naar een sterkere cloud-model (bv.
    deepseek-v4-flash via OpenModel) voor plannings- en decompositiewerk, ook
    als de standaard-backend lokaal/Ollama is. run_agent auto-routeert cloud-
    modellen naar de juiste gateway."""
    full = ""
    async for chunk in agent_service.run_agent(
        messages=[{"role": "user", "content": user_prompt}],
        system_prompt=system_prompt,
        agent="hermes",
        max_tokens=max_tokens,
        use_tools=False,
        model_override=model_override,
    ):
        if chunk.get("type") == "text":
            full += chunk["text"]
        elif chunk.get("type") == "error":
            raise RuntimeError(chunk.get("message", "Agent error"))
    return full.strip()


# ═════════════════════════════════════════════════════════════════════
#  2. PERSISTENTIE  —  CRUD voor goals, fasen, taken
# ═════════════════════════════════════════════════════════════════════

def _create_goal(title: str, objective: str, project: str) -> str:
    goal_id = f"goal-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{_id()}"
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goals (id, title, objective, project, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?)",
            (goal_id, title, objective, project, now, now),
        )
    return goal_id


def _create_phase(goal_id: str, title: str, description: str, ord: int) -> str:
    phase_id = f"ph-{_id()}"
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goal_phases (id, goal_id, title, description, ord, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (phase_id, goal_id, title, description, ord, now, now),
        )
    return phase_id


def _create_task(goal_id: str, phase_id: str, title: str, description: str,
                 skill: str, dependencies: List[str], position: int) -> str:
    task_id = f"t-{_id()}"
    now = _now()
    # Bepaal status: als dependencies leeg zijn = 'ready', anders 'pending'
    status = "ready" if not dependencies else "pending"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goal_tasks (id, goal_id, phase_id, title, description, skill, status, dependencies, retry_count, max_retries, created_at, updated_at, ord) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 3, ?, ?, ?)",
            (task_id, goal_id, phase_id, title, description, skill, status,
             json.dumps(dependencies), now, now, position),
        )
    return task_id


def _update_goal(goal_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE goals SET {set_clause} WHERE id = ?",
            list(fields.values()) + [goal_id],
        )


def _update_task(task_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE goal_tasks SET {set_clause} WHERE id = ?",
            list(fields.values()) + [task_id],
        )


def _update_phase(phase_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE goal_phases SET {set_clause} WHERE id = ?",
            list(fields.values()) + [phase_id],
        )


def _log_activity(goal_id: str, action: str, detail: str,
                  artifact: str = "", next_step: str = "", status: str = "ok") -> None:
    """Log een uitkomst-kaart naar activity_log: wat gedaan → waar staat het
    (artifact) → wat moet Vincent doen (next_step). status='error' maakt het
    een Actiecentrum-item."""
    from ...shared.outcomes import log_outcome
    try:
        log_outcome(f"goal:{goal_id}", action, detail,
                    artifact=artifact, next_step=next_step, status=status)
    except Exception:
        pass  # fallback: negeer log-error


# ═════════════════════════════════════════════════════════════════════
#  3. DECOMPOSITIE — API
# ═════════════════════════════════════════════════════════════════════

async def decompose_goal(objective: str, project: str = "WeAreImpact") -> Dict[str, Any]:
    """Gebruik Hermes om een goal op te splitsen in fasen en taken.

    Infinite Context: injecteert Obsidian-merkrichtlijnen & project-context
    in de system prompt zodat de LLM het plan op de juiste tone of voice
    en projectstructuur baseert.
    """
    # ── READ: Haal Obsidian context voor decompositie ──────────────
    if _infinite_ctx.is_configured:
        obsidian_ctx = _infinite_ctx.build_goal_context(objective, project)
        if obsidian_ctx:
            system = _DECOMPOSITION_SYSTEM + "\n\n" + obsidian_ctx
        else:
            system = _DECOMPOSITION_SYSTEM
    else:
        system = _DECOMPOSITION_SYSTEM

    prompt = _DECOMPOSITION_PROMPT_TEMPLATE.format(objective=objective, project=project)
    # Planning/decompositie is denkwerk: routeer naar een sterke cloud-model
    # (OpenModel smart-model) in plaats van de trage lokale Ollama, zodat het
    # plan snel én kwalitatief goed is — ook als de standaard-backend lokaal is.
    from ...shared.config import OPENMODEL_SMART_MODEL
    raw = await _stream_text(system, prompt, max_tokens=6000,
                             model_override=OPENMODEL_SMART_MODEL)

    logger.info(f"Goal decompositie RAW ({len(raw)} chars): {raw[:500]}")

    plan = _parse_llm_plan(raw, objective)
    # Safeguard: te algemene plannen (1 fase, 1 taak) bij complexe doelen
    # leveren een onleesbare blob op en geen publisher-taak. In plaats van
    # opnieuw de LLM te bellen (die opnieuw kan falen), bouwen we een
    # DETERMINISTISCH plan op basis van trefwoorden in de doelstelling.
    # Dat produceert GARANDEERD een bruikbare, gesplitste structuur.
    if _is_trivial_plan(plan, objective):
        logger.info("Plan te algemeen - deterministisch plan opbouwen uit doelstelling")
        plan = _deterministic_plan(objective, project)
    return plan


def _deterministic_plan(objective: str, project: str) -> Dict[str, Any]:
    """Bouw een gesplitst plan op basis van trefwoorden in de doelstelling.

    Deze fallback garandeert dat we nooit met een 1-blob eindigen: elk
    herkenbaar deliverable wordt een eigen taak, met publisher-taken voor
    alles dat gepubliceerd moet worden. Werkt zonder LLM, dus altijd betrouwbaar.

    Trefwoorden -> taken:
      - "artikel"/"blog"/"post"/"pagina"/"longread" -> per stuk content-writer + publisher
      - "checklist"/"lead-magnet"/"one-pager"/"gids"/"template"/"rapport" -> content-writer
      - "outreach"/"backlink"/"gastblog"/"gastcolumn" -> research + outreach
      - "analyseer"/"scan"/"inventariseer"/"meet" -> analyst of research
      - "redactioneel plan" -> content-editor
    """
    obj = (objective or "").lower()
    title_root = (project or "het project").strip()

    # Tel hoeveel artikelen/blogs/pagina's expliciet genoemd zijn
    import re as _re
    def _count(pattern):
        m = _re.search(pattern, obj)
        if not m:
            return 0
        num = m.group(1)
        if num in ("een", "1", "één"):
            return 1
        try:
            return int(num)
        except ValueError:
            return 1

    n_articles = _count(r"(\d+|\w+)\s+(?:artikel|blog|blogpost|pagina|post|longread)")
    n_articles = max(n_articles, 1 if any(w in obj for w in ("artikel", "blog", "blogpost", "pagina", "longread", "reeks", "series")) else 0)

    phases: List[Dict[str, Any]] = []
    tasks_research = []
    tasks_write = []
    tasks_publish = []
    tasks_outreach = []
    tasks_analyst = []
    tasks_editor = []

    # Research-fase (altijd zinvol bij content/outreach)
    research_title = ""
    if any(w in obj for w in ("gsc", "data", "analyse", "scan", "inventaris", "content-gap", "cannibal")):
        research_title = f"Analyseer beschikbare data voor {title_root}"
        tasks_research.append({
            "title": research_title,
            "description": f"Gebruik GSC-data, content-gap-analyse en bestaande kennisbank om de aanpak voor {title_root} te onderbouwen. Lever een beknopt meetplan/analyse (geen live analytics-calls).",
            "skill": "analyst", "dependencies": [],
        })

    # Artikelen / blogs / pagina's
    if n_articles > 0:
        for i in range(n_articles):
            write_title = f"Schrijf artikel {i+1} voor {title_root}"
            t = {
                "title": write_title,
                "description": f"Schrijf een publicabel artikel (inclusief FAQPage-sectie waar zinvol) voor {title_root}. Baseer op de research-output en bestaande kennisbank.",
                "skill": "content-writer",
                "dependencies": [research_title] if research_title else [],
            }
            tasks_write.append(t)
            tasks_publish.append({
                "title": f"Publiceer artikel {i+1} ({title_root})",
                "description": f"Stage artikel {i+1} in de Wachtrij ter review (mens keurt pas goed).",
                "skill": "publisher", "dependencies": [write_title],
            })

    # Checklists / lead-magnets / one-pagers / gidsen / templates / rapporten
    if any(w in obj for w in ("checklist", "lead-magnet", "lead magnet", "one-pager", "gids", "template", "rapport", "overzicht")):
        tasks_write.append({
            "title": f"Maak ondersteunend document (checklist/gids/template) voor {title_root}",
            "description": f"Schrijf het ondersteunende document (checklist, lead-magnet, one-pager, gids of template) dat hoort bij {title_root}.",
            "skill": "content-writer", "dependencies": [research_title] if research_title else [],
        })

    # Outreach / backlinks / gastblogs
    if any(w in obj for w in ("outreach", "backlink", "gastblog", "gastcolumn", "guest")):
        outreach_research_title = f"Identificeer backlink-bronnen voor {title_root}"
        tasks_research.append({
            "title": outreach_research_title,
            "description": f"Zoek 20 relevante, haalbare backlink-bronnen voor {title_root} (welzijnskoepels, VNG, brancheverenigingen, ANBI-directory's, partners).",
            "skill": "research", "dependencies": [],
        })
        tasks_outreach.append({
            "title": f"Schrijf outreach-concepten voor {title_root}",
            "description": f"Schrijf per bron een gepersonaliseerde outreach-tekst + gastblog-voorstel. Concepten voor menselijke goedkeuring, geen auto-versturen.",
            "skill": "outreach", "dependencies": [outreach_research_title],
        })

    # Redactioneel plan
    if "redactioneel" in obj or "editor" in obj:
        tasks_editor.append({
            "title": f"Redactioneel plan & kwaliteitscontrole ({title_root})",
            "description": f"Stel een redactioneel plan op en controleer de geschreven content op toon, spelfouten en consistentie voor {title_root}.",
            "skill": "content-editor", "dependencies": [],
        })

    # Bouw fasen op
    if tasks_research:
        phases.append({"title": "Fase 1: Research & analyse", "description": "", "tasks": tasks_research})
    if tasks_write:
        phases.append({"title": "Fase 2: Content creatie", "description": "", "tasks": tasks_write})
    if tasks_outreach:
        phases.append({"title": "Fase 3: Outreach", "description": "", "tasks": tasks_outreach})
    if tasks_editor:
        phases.append({"title": "Fase 4: Redactie & planning", "description": "", "tasks": tasks_editor})
    if tasks_publish:
        phases.append({"title": "Fase 5: Publicatie (review-gate)", "description": "Alle artikelen staged in de Wachtrij voor menselijke goedkeuring.", "tasks": tasks_publish})

    # Altijd minimaal 1 fase met 1 zinvolle taak (nooit een blob)
    if not phases:
        phases.append({
            "title": "Uitvoering",
            "description": "",
            "tasks": [{
                "title": f"Werk het doel uit voor {title_root}",
                "description": objective,
                "skill": "content-writer", "dependencies": [],
            }],
        })

    return {
        "plan_summary": f"Deterministisch plan voor: {objective[:160]}",
        "estimated_duration": "2-5 dagen",
        "phases": phases,
    }
    return plan


def _is_trivial_plan(plan: Dict[str, Any], objective: str) -> bool:
    """Detecteer een te algemeen plan (1 fase, 1 taak) bij een complex doel.

    Een enkele taak voor een doel met meerdere deliverables (artikelen, blogs,
    checklists, outreach, templates) leidt tot een onleesbare blob en geen
    publisher-taak -> niets belandt in de Wachtrij. We decomponeren dan over.
    """
    phases = plan.get("phases", [])
    if len(phases) != 1:
        return False
    tasks = phases[0].get("tasks", [])
    if len(tasks) != 1:
        return False
    obj = (objective or "").lower()
    indicators = ["artikel", "blog", "pagina", "reeks", "longread", "checklist",
                  "lead", "gids", "rapport", "outreach", "template", "series",
                  "kennis", "blogpost", "one-pager", "use-case", "conversie"]
    score = sum(1 for w in indicators if w in obj)
    return len(objective or "") > 120 or score >= 2


# ── Infinite Context helpers ─────────────────────────────────────────

def _resolve_goal_project(goal_id: str) -> str:
    """Haal het project van een goal uit de database."""
    if not goal_id:
        return ""
    with get_conn() as conn:
        row = conn.execute("SELECT project FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return row["project"] if row else ""


# ── Parser helpers (meerdere fallback-strategieën voor LLM-output) ───

def _try_parse_json(candidate: str) -> Optional[Dict[str, Any]]:
    """Probeer JSON te parsen met oplopende tolerantie voor veelgemaakte LLM-fouten."""
    # Level 1: strict — moet direct werken
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Level 2: trailing commas verwijderen ("key": "val",} → "key": "val"})
    try:
        cleaned = re.sub(r',\s*([}\]])', r'\1', candidate)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Level 3: single quotes → double + unquoted keys quoten
    try:
        cleaned = candidate
        # Vervang enkele quotes door dubbele (niet inside al bestaande double-quoted strings)
        cleaned = re.sub(r"(?<!\")'", '"', cleaned)
        # Quote ongedubbelde keys (woord gevolgd door colon, niet in string context)
        cleaned = re.sub(r'(?m)^\s*(\w+)(\s*):', r'"\1"\2:', cleaned)
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    return None


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Zorg dat een plan alle verwachte velden heeft — safe defaults."""
    plan.setdefault("plan_summary", "")
    plan.setdefault("estimated_duration", "1-2 dagen")
    for phase in plan.get("phases", []):
        phase.setdefault("description", "")
        for task in phase.get("tasks", []):
            task.setdefault("description", "")
            task.setdefault("skill", "content-writer")
            task.setdefault("dependencies", [])
    return plan


def _fallback_plan(objective: str) -> Dict[str, Any]:
    """Minimale fallback — garandeert altijd 1 fase met 1 taak."""
    return {
        "plan_summary": f"Plan voor: {objective}",
        "estimated_duration": "1-2 dagen",
        "phases": [{
            "title": "Uitvoering",
            "description": f"Automatisch gegenereerd plan voor: {objective}",
            "tasks": [{
                "title": objective,
                "description": f"Voer uit: {objective}",
                "skill": "content-writer",
                "dependencies": [],
            }],
        }],
    }


def _extract_tasks_from_block(block: str, objective: str, phase_num: int) -> List[Dict[str, Any]]:
    """Extraheer taken uit een fase-block.
    
    Herkent:
    - Genummerde items: 1. Titel - beschrijving
    - Bullet items: - Titel: beschrijving
    - JSON-achtige key:value regels
    - Eenvoudige tekstregels
    """
    if not block or not block.strip():
        return [{
            "title": f"Taak 1 (fase {phase_num})",
            "description": objective,
            "skill": "content-writer",
            "dependencies": [],
        }]

    # Split in items: bullets, nummers, of 'title:' key-regels
    lines = block.split("\n")
    items = []
    buf = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                items.append(buf)
                buf = ""
            continue
        if re.match(r'^\s*[\-\*]\s', stripped) or re.match(r'^\s*\d+[\.\)]\s', stripped) or stripped.startswith('"title"') or stripped.startswith("title"):
            if buf:
                items.append(buf)
            buf = stripped
        else:
            buf += " " + stripped
    if buf:
        items.append(buf)

    # Geen structuur gevonden? val terug op losse regels
    if not items:
        items = [l for l in lines if l.strip()]

    tasks = []
    for idx, item in enumerate(items):
        title = ""
        description = ""
        skill = "content-writer"

        # Extract skill uit [skill], (skill), of "skill": "x"
        skill_m = re.search(r'[\[\(]?(?:skill|rol|type)[:\s]+([a-z][a-z-]+)[\]\)]?', item, re.IGNORECASE)
        if skill_m:
            candidate = skill_m.group(1).strip().lower()
            if candidate in SKILL_PROFILES:
                skill = candidate

        # Strip opsommingsteken
        if re.match(r'^\s*[\-\*\d\.]', item):
            title = re.sub(r'^\s*[\-\*\d\.]+\s*', '', item).strip()
        elif '"title"' in item or item.startswith("title"):
            t_m = re.search(r'title["\s]*:?\s*["\']?([^"\'\\,]+)', item)
            title = t_m.group(1).strip() if t_m else ""
        else:
            title = item

        # Extract description na eerste '-' of ':'
        desc_m = re.search(r'\s[–\-—]\s+(.*)', title) or re.search(r':\s+(.*)', title)
        if desc_m:
            description = desc_m.group(1).strip()
            title = title.split(desc_m.group(0), 1)[0].strip()

        tasks.append({
            "title": (title or f"Taak {idx + 1}")[:200],
            "description": description[:500] if description else "",
            "skill": skill,
            "dependencies": [],
        })

    return tasks


def _extract_phases_from_text(s: str, objective: str) -> List[Dict[str, Any]]:
    """Extraheer fasen uit niet-JSON tekst.
    
    3 herkenningspatronen (oplopend tolerant):
    1. **Fase 1: Titel** of **Phase 1: Title** (bold markdown)
    2. Fase 1: Titel of Phase 1: Title / Stap 1: Titel
    3. Genummerde secties: 1. Titel
    """
    phase_patterns = [
        r'(?m)^\s*\*{1,2}\s*(?:Fase|Phase|Stap)\s+(\d+)\s*[:\-–]\s*(.*?)\s*\*{1,2}\s*$',
        r'(?m)^\s*(?:Fase|Phase|Stap)\s+(\d+)\s*[:\-–]\s*(.*?)\s*$',
        r'(?m)^\s*(\d+)\s*[.:\)]\s+(.*?)\s*$',
    ]

    phase_matches = None
    used_pattern = None
    for pat in phase_patterns:
        matches = list(re.finditer(pat, s, re.IGNORECASE))
        if matches and len(matches) >= 1:
            phase_matches = matches
            used_pattern = pat
            break

    if not phase_matches:
        return [_fallback_plan(objective)["phases"][0]]

    phases = []
    for i, m in enumerate(phase_matches):
        groups = m.groups()
        if used_pattern == phase_patterns[0]:
            phase_num, raw_title = int(groups[0]), groups[1].strip()
        elif used_pattern == phase_patterns[1]:
            phase_num, raw_title = int(groups[0]), groups[1].strip()
        else:
            phase_num, raw_title = int(groups[0]), groups[1].strip()

        start = m.end()
        end = phase_matches[i + 1].start() if i + 1 < len(phase_matches) else len(s)
        block = s[start:end].strip()
        tasks = _extract_tasks_from_block(block, objective, phase_num)

        phases.append({
            "title": f"Fase {phase_num}: {raw_title}",
            "description": raw_title,
            "tasks": tasks,
        })

    return phases


def _parse_semistructured(s: str, objective: str) -> Dict[str, Any]:
    """Parse niet-JSON LLM-output via key-value extractie + fase-herkenning."""
    kv = {}

    # Priority 1: quoted key:value pairs
    for m in re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', s):
        kv[m.group(1)] = m.group(2)

    # Priority 2: unquoted key:value op eigen regel
    for m in re.finditer(r'(?m)^\s*"?([a-z_]+)"?\s*[:=]\s*"?([^"\n,]+)"?,?\s*$', s, re.IGNORECASE):
        key, val = m.group(1).strip(), m.group(2).strip()
        if key not in kv and len(val) < 200:
            kv[key] = val

    phases = _extract_phases_from_text(s, objective)

    return {
        "plan_summary": kv.get("plan_summary", kv.get("summary", f"Plan voor: {objective}")),
        "estimated_duration": kv.get("estimated_duration", kv.get("duration", "1-2 dagen")),
        "phases": phases,
    }


def _parse_llm_plan(raw: str, objective: str) -> Dict[str, Any]:
    """Parse LLM-output naar een genormaliseerd plan-dict.
    
    Fallback-chain (hoogste kwaliteit eerst):
    1. Strict JSON (codeblock of raw) → directe parsed dict
    2. Hersteld JSON (trailing commas, unquoted keys, single quotes)
    3. Semi-gestructureerde key:value extractie + fase-herkenning
    4. Hardcoded fallback — garandeert altijd een bruikbaar plan
    
    Deze aanpak dekt ~95% van LLM-outputvarianten af zonder de LLM
    te dwingen tot een specifiek format.
    """
    if not raw or not raw.strip():
        return _fallback_plan(objective)

    s = raw.strip()

    # Strip markdown fences
    if s.startswith("```"):
        lines = s.split("\n")
        s = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    if not s:
        return _fallback_plan(objective)

    # ── Stap 1: JSON extractie (strict + tolerant) ────────────────
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = s.find(open_ch)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(s)):
            if s[i] == open_ch:
                depth += 1
            elif s[i] == close_ch:
                depth -= 1
            if depth == 0:
                candidate = s[start:i + 1]
                parsed = _try_parse_json(candidate)
                if parsed is None:
                    continue
                if isinstance(parsed, dict):
                    if "phases" in parsed or "plan_summary" in parsed:
                        return _normalize_plan(parsed)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return _normalize_plan({
                        "phases": parsed,
                        "plan_summary": "",
                        "estimated_duration": "",
                    })

    # ── Stap 2: Semi-gestructureerd (geen valide JSON gevonden) ──
    return _parse_semistructured(s, objective)


# Waarschuwing voor heel vage doelen — geen harde blokkade meer.
# De agent krijgt een hint in de context ipv dat de gebruiker wordt tegengehouden.
# Dit voorkomt frustratie (bv. "Backlog bijwerken" is legitiem) terwijl de agent
# nog steeds een seintje krijgt dat het doel algemeen is.
_VAGUE_WARNING_THRESHOLD = 8  # Pas waarschuwing bij extreem korte objectives (<8 chars)


def _vague_warning(objective: str) -> Optional[str]:
    """Geef een waarschuwing als het objective extreem kort is, maar blokkeer niet."""
    obj = (objective or "").strip()
    if len(obj) < _VAGUE_WARNING_THRESHOLD:
        return (
            f"Let op: '{obj}' is erg kort. Overweeg om concreter te zijn "
            "(onderwerp, doelgroep/site, gewenst eindresultaat) — anders vult de "
            "agent ontbrekende details in met eigen aannames."
        )
    return None


def _find_similar_open_goal(title: str, project: str) -> Optional[Dict[str, Any]]:
    """Vind een recent open goal (zelfde project) met een vrijwel gelijke titel.

    Voorkomt dat de strategist bij twee runs kort na elkaar hetzelfde doel
    dubbel aanmaakt. Vergelijkt genormaliseerde titels met difflib."""
    import difflib

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

    target = norm(title)
    if not target:
        return None
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, status FROM goals WHERE project = ? "
            "AND status IN ('draft', 'ready', 'running') "
            "AND created_at >= datetime('now', '-14 days')",
            (project,),
        ).fetchall()
    for row in rows:
        if difflib.SequenceMatcher(None, target, norm(row["title"])).ratio() >= 0.7:
            return dict(row)
    return None


async def create_and_plan(title: str, objective: str, project: str = "WeAreImpact") -> Dict[str, Any]:
    """Creëer een goal, voer decompositie uit, retourneer plan ter goedkeuring.

    Geeft een waarschuwing bij extreem vage objectives, maar blokkeert niet meer —
    de agent kan nog steeds aan de slag. Voorkomen is beter dan frustreren.
    """
    existing = _find_similar_open_goal(title, project)
    if existing:
        logger.info(
            "Goal-dedupe: '%s' lijkt op bestaand open goal %s ('%s') — geen nieuw goal aangemaakt",
            title[:60], existing["id"], existing["title"][:60],
        )
        return {
            "goal_id": existing["id"],
            "title": existing["title"],
            "objective": objective,
            "plan": {},
            "duplicate_of_existing": True,
        }

    warning = _vague_warning(objective)
    if warning:
        logger.info(f"Vage objective (niet geblokkeerd): {objective[:80]}")
    goal_id = _create_goal(title, objective, project)
    plan = await decompose_goal(objective, project)

    # Sla het ruwe plan op in een workspace-bestand voor de UI
    workspace_dir = GOALS_WORKSPACE / goal_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "goal_id": goal_id,
        "title": title,
        "objective": objective,
        "plan": plan,
    }


def _infer_missing_deps(goal_id: str) -> int:
    """Koppel publisher/editor-taken zonder deps aan hun content-writer taak.

    Voorkomt dat publishers lege content publiceren of editors
    niet-bestaande artikelen editen wanneer de LLM de dependencies
    wegliet. Matching op de gemeenschappelijke 'core' in de titel
    (bijv. "Publiceer artikel: Vrijwilligersbeheer..." <-> "Schrijf
    artikel: Vrijwilligersbeheer...").

    Return: aantal gekoppelde taken.
    """
    with get_conn() as conn:
        tasks = conn.execute(
            "SELECT id, title, skill, status, dependencies FROM goal_tasks WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()

        writers = {}  # core -> task_id
        for t in tasks:
            if t["skill"] == "content-writer":
                core = _article_core(t["title"])
                if core:
                    writers[core] = t["id"]

        linked = 0
        for t in tasks:
            if t["skill"] not in ("publisher", "content-editor", "content-judge"):
                continue
            deps = json.loads(t["dependencies"]) if t["dependencies"] else []
            if deps:
                continue  # al gekoppeld, niet overschrijven
            core = _article_core(t["title"])
            if not core:
                continue
            # Exacte match, anders substring-match op de kortste core
            target = writers.get(core)
            if not target:
                for wcore, wid in writers.items():
                    if core in wcore or wcore in core:
                        target = wid
                        break
            if target and target != t["id"]:
                conn.execute(
                    "UPDATE goal_tasks SET dependencies = ?, status = 'pending', updated_at = ? WHERE id = ?",
                    (json.dumps([target]), _now(), t["id"]),
                )
                linked += 1
    return linked


def _article_core(title: str) -> str:
    """Haal de artikel-core uit een taaktitel voor dependency-matching.

    'Schrijf artikel: Vrijwilligersbeheer in de praktijk' ->
    'vrijwilligersbeheer in de praktijk'. Werkt ook voor 'Publiceer
    artikel 1 voor WeAreImpact' -> 'artikel 1 voor weareimpact'.
    """
    if not title:
        return ""
    t = title.lower()
    # Verwijder veelgebruikte prefixes
    t = re.sub(r"^(schrijf|publiceer|redigeer|bepaal|review en redigeer|maak)\s*", "", t)
    t = re.sub(r"^(artikel|blog|post|pagina|gids|checklist|template|one-pager|document)\s*", "", t)
    t = t.replace("artikel:", "").replace(":", "").strip()
    # Verwijder project-suffix "voor weareimpact"/"voor bijeen" (niets
    # unieks) maar HOUD het nummer ("artikel 1") voor onderscheid
    return t


def _normalize_dep_refs(goal_id: str, phase_id: str, self_task_id: str,
                         refs: List[str], title_index: Dict[Tuple[str, str], str]) -> List[str]:
    """Los dependency-referenties op naar echte task-id's.

    refs kunnen zijn: een echte task-id, een placeholder ("t1", "f1t2"), of
    (een deel van) een taaktitel. Echte id's blijven; de rest proberen we op
    titel te matchen (eigen fase eerst, dan hele goal). Onopgeloste refs
    worden verwijderd zodat een taak nooit permanent geblokkeerd raakt.
    """
    out: List[str] = []
    for ref in refs or []:
        if not ref:
            continue
        ref = str(ref).strip()
        with get_conn() as conn:
            exists = conn.execute("SELECT 1 FROM goal_tasks WHERE id = ?", (ref,)).fetchone()
        if exists:
            out.append(ref)
            continue
        key = (phase_id, ref.lower())
        if key in title_index and title_index[key] != self_task_id:
            out.append(title_index[key])
            continue
        found = None
        for (pid, t), tid in title_index.items():
            if tid == self_task_id:
                continue
            if ref.lower() in t or t in ref.lower():
                found = tid
                break
        if found:
            out.append(found)
    return out


def confirm_plan(goal_id: str) -> Dict[str, Any]:
    """Gebruiker heeft het plan goedgekeurd → schrijf fasen/taken naar DB + start executie."""
    workspace_dir = GOALS_WORKSPACE / goal_id
    plan_file = workspace_dir / "plan.json"
    if not plan_file.exists():
        raise ValueError("Plan niet gevonden — voer eerst create_and_plan uit.")

    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    phases = plan.get("phases", [])

    phase_ids: List[str] = []
    total_tasks = 0
    # Verzamel (phase_id, task_title) -> task_id zodat dependency-refs
    # (die de LLM vaak als titel geeft) opgelost kunnen worden.
    _task_title_index: Dict[Tuple[str, str], str] = {}

    with get_conn() as conn:
        for p_idx, phase_data in enumerate(phases):
            phase_id = f"ph-{_id()}"
            now = _now()
            conn.execute(
                "INSERT INTO goal_phases (id, goal_id, title, description, ord, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (phase_id, goal_id, phase_data["title"], phase_data.get("description", ""), p_idx + 1, now, now),
            )
            phase_ids.append(phase_id)

            for t_idx, task_data in enumerate(phase_data.get("tasks", [])):
                task_id = f"t-{_id()}"
                deps = task_data.get("dependencies", [])
                deps = _normalize_dep_refs(goal_id, phase_id, task_id, deps, _task_title_index)
                status = "ready" if not deps else "pending"
                conn.execute(
                    "INSERT INTO goal_tasks (id, goal_id, phase_id, title, description, skill, status, dependencies, created_at, updated_at, ord) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, goal_id, phase_id, task_data["title"], task_data.get("description", ""),
                     task_data.get("skill", "content-writer"), status, json.dumps(deps), now, now, t_idx + 1),
                )
                _task_title_index[(phase_id, (task_data["title"] or "").strip().lower())] = task_id
                total_tasks += 1

        # Post-pass: publisher/editor-taken zonder dependencies automatisch
        # koppelen aan de bijbehorende content-writer taak. De LLM zet niet
        # altijd deps bij mooie multi-taak plannen, waardoor publishers
        # lege content publiceren en editors niet-bestaande artikelen
        # editen. Deze inferrence voorkomt dat (zie Use-case doel).
        _infer_missing_deps(goal_id)

        # Update goal metadata
        conn.execute(
            "UPDATE goals SET status='ready', phase_count=?, task_count=?, updated_at=? WHERE id=?",
            (len(phases), total_tasks, now, goal_id),
        )

    # Schrijf plan summary als workspace bestand
    summary = plan.get("plan_summary", "")
    (workspace_dir / "summary.txt").write_text(summary, encoding="utf-8")

    _log_activity(goal_id, "plan_approved", f"Plan goedgekeurd: {len(phases)} fasen, {total_tasks} taken")

    return {
        "goal_id": goal_id,
        "phase_count": len(phases),
        "task_count": total_tasks,
        "plan_summary": plan.get("plan_summary", ""),
    }


# ═════════════════════════════════════════════════════════════════════
#  4. EXECUTIE-LOOP — autonome achtergrond-loop
# ═════════════════════════════════════════════════════════════════════

def _get_ready_tasks(goal_id: str) -> List[Dict[str, Any]]:
    """Vind taken die 'ready' zijn — alle dependencies voldaan."""
    with get_conn() as conn:
        tasks = conn.execute(
            "SELECT * FROM goal_tasks WHERE goal_id = ? AND status = 'ready' ORDER BY ord ASC",
            (goal_id,),
        ).fetchall()
    return [dict(t) for t in tasks]


def _resolve_dep_by_title(goal_id: str, ref: str, dep_status: Dict[str, str]) -> Optional[str]:
    """Los een onherkenbare dependency-ref op via title-matching.

    De LLM-decompositie kent de echte task-id's niet en gebruikt vaak de
    taaktitel (of een deel ervan) als referentie. Match die tegen de titels
    van voltooide/actieve taken in dezelfde goal.
    """
    ref_l = (ref or "").strip().lower()
    if len(ref_l) < 3:
        return None
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, status FROM goal_tasks WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()
    best = None
    for r in rows:
        t = (r["title"] or "").lower()
        if ref_l == t or ref_l in t or t in ref_l:
            best = r["id"]
            break
    if best is None:
        return None
    return dep_status.get(best)


def _resolve_dependencies(task: Dict[str, Any]) -> bool:
    """Check of alle dependencies van een taak 'completed' zijn.

    De LLM-decompositie kent bij het schrijven van 'dependencies' de
    uiteindelijke taak-id's nog niet (die worden pas in confirm_plan
    gegenereerd) en verzint daarom vaak eigen placeholder-referenties
    ("f1t1", "t1", een taaktitel, ...). Zo'n onherkende referentie mag een
    taak niet permanent blokkeren — die wordt genegeerd (niet als 'failed'
    behandeld) zodat het plan altijd door kan lopen.
    """
    deps = json.loads(task.get("dependencies", "[]"))
    if not deps:
        return True

    with get_conn() as conn:
        dep_rows = conn.execute(
            f"SELECT id, status FROM goal_tasks WHERE id IN ({','.join('?' for _ in deps)})",
            deps,
        ).fetchall()
    dep_status = {r["id"]: r["status"] for r in dep_rows}

    for dep_id in deps:
        s = dep_status.get(dep_id)
        if s is None:
            # Onherkende referentie: probeer op titel te matchen (de LLM
            # verzint vaak een placeholder zoals "t1" of de taaktitel zelf).
            s = _resolve_dep_by_title(task["goal_id"], dep_id, dep_status)
            if s is None:
                continue  # Niet te herleiden - negeren, niet blokkeren
        if s == "failed" or s == "skipped":
            # Dependency failed — deze taak kan niet doorgaan
            return False
        if s != "completed":
            return False  # Nog niet klaar
    return True


def _count_tasks(goal_id: str) -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM goal_tasks WHERE goal_id = ? GROUP BY status",
            (goal_id,),
        ).fetchall()
    counts = {"completed": 0, "failed": 0, "pending": 0, "ready": 0, "running": 0, "skipped": 0, "total": 0}
    for r in rows:
        counts[r["status"]] = r["cnt"]
    counts["total"] = sum(counts.values())
    return counts


async def _execute_task(goal_id: str, task: Dict[str, Any]) -> str:
    """Voer één taak uit op basis van zijn skill-type."""
    task_id = task["id"]
    title = task["title"]
    description = task.get("description", "")
    skill = task.get("skill", "content-writer")

    _update_task(task_id, status="running", started_at=_now())
    event_bus.publish({
        "type": "goal_task_start", "goal_id": goal_id, "task_id": task_id,
        "title": title, "skill": skill,
    })
    _log_activity(goal_id, "task_start", f"'{title}' ({skill})")

    started = time.perf_counter()

    # Verzamel resultaten van eerdere taken in dit doel als context
    # (lost de "ontbrekende invoer"-fouten op bij afhankelijke taken).
    prior_results = _build_prior_results_context(goal_id, task_id)
    try:
        result, artifact, next_step = await _route_by_skill(
            skill, title, description, goal_id, prior_results=prior_results,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_task(task_id, status="completed", result=result, duration_ms=duration_ms, finished_at=_now())
        event_bus.publish({
            "type": "goal_task_done", "goal_id": goal_id, "task_id": task_id,
            "title": title, "skill": skill, "duration_ms": duration_ms,
        })
        _log_activity(goal_id, "task_done", f"'{title}' ({duration_ms}ms)",
                      artifact=artifact, next_step=next_step)
        return result

    except _NotExecuted as e:
        # Echte actie niet uitgevoerd → direct failed, zonder retry en zonder
        # alternatief. Retries lossen niets op (er is geen artikel) en het
        # alternatief zou de taak alsnog met LLM-tekst op 'completed' zetten.
        duration_ms = int((time.perf_counter() - started) * 1000)
        error_str = str(e)[:500]
        _update_task(task_id, status="failed", error=error_str,
                     duration_ms=duration_ms, finished_at=_now())
        event_bus.publish({
            "type": "goal_task_failed", "goal_id": goal_id, "task_id": task_id,
            "title": title, "error": error_str,
        })
        _log_activity(goal_id, "task_not_executed", f"'{title}': {error_str}",
                      status="error",
                      next_step="Lever eerst publiceerbare content aan, of laat deze taak vervallen")
        return ""

    except Exception as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        error_str = str(e)[:500]
        retry = task.get("retry_count", 0)
        max_retries = task.get("max_retries", 3)

        if retry < max_retries:
            # Self-correctie: retry met backoff
            _update_task(task_id, status="ready", retry_count=retry + 1, error=error_str,
                         duration_ms=duration_ms, finished_at=_now())
            wait = min(10 * (retry + 1), 60)
            event_bus.publish({
                "type": "goal_task_retry", "goal_id": goal_id, "task_id": task_id,
                "title": title, "attempt": retry + 1, "max_retries": max_retries,
                "wait": wait, "error": error_str,
            })
            _log_activity(goal_id, "task_retry", f"'{title}' poging {retry+1}/{max_retries}: {error_str}")
            await asyncio.sleep(wait)
            raise _RetryLater()  # Wordt opgevangen door de loop

        else:
            # Max retries bereikt → markeer als failed, probeer alternatief
            _update_task(task_id, status="failed", error=error_str,
                         duration_ms=duration_ms, finished_at=_now())
            event_bus.publish({
                "type": "goal_task_failed", "goal_id": goal_id, "task_id": task_id,
                "title": title, "error": error_str,
            })
            _log_activity(goal_id, "task_failed", f"'{title}' na {retry+1} pogingen: {error_str}",
                          status="error", next_step="Bekijk het doel in de Doelen-tab of laat de agent het opnieuw proberen")

            # Probeer alternatieve aanpak (self-correctie)
            try:
                alternative = await _find_alternative(skill, title, description, error_str)
                if alternative:
                    alt_duration = int((time.perf_counter() - started) * 1000)
                    _update_task(task_id, status="completed", result=alternative,
                                 duration_ms=alt_duration, finished_at=_now())
                    event_bus.publish({
                        "type": "goal_task_alternative", "goal_id": goal_id, "task_id": task_id,
                        "title": title, "note": "Uitgevoerd via alternatieve aanpak",
                    })
                    _log_activity(goal_id, "task_alternative", f"'{title}' via alternatieve aanpak")
                    return alternative
            except Exception:
                pass  # Alternatief faalde ook — blijf op failed staan

            raise


class _RetryLater(Exception):
    """Interne exception: taak moet opnieuw worden geprobeerd."""
    pass


class _NotExecuted(Exception):
    """De taak kón zijn échte actie niet uitvoeren (bv. publiceren zonder
    publiceerbaar artikel). Geen technische fout, dus opnieuw proberen of een
    'alternatieve aanpak' bedenken helpt niet — dat zou alleen een LLM-concept
    opleveren dat als voltooid werk wordt geteld. De taak faalt direct, het doel
    wordt daardoor 'partial', en dat is precies wat het Actiecentrum moet zien."""
    pass



def _build_prior_results_context(goal_id: str, current_task_id: str = "") -> str:
    """Verzamel resultaten van reeds-voltooide taken in dezelfde goal en
    zet ze om in een compacte, leesbare context-blok voor de volgende taak.

    Dit is de kernfix voor de "ontbrekende invoer"-fouten: een taak die de
    output van een eerdere taak (research, analyse, geschreven content)
    nodig heeft, krijgt die nu mee in de prompt in plaats van te moeten
    raden. Output wordt ingekort zodat de prompt niet explodeert.
    """
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, skill, result FROM goal_tasks "
                "WHERE goal_id = ? AND status = 'completed' AND result IS NOT NULL "
                "AND length(result) > 50 ORDER BY ord ASC",
                (goal_id,),
            ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    blocks = []
    for r in rows:
        rid = r["id"]
        if rid == current_task_id:
            continue
        res = r["result"] or ""
        # Strip de CONCEPT/MEETPLAN-banner zodat de ontvanger schone input krijgt.
        # De banner staat altijd op de eerste regel(s), voorafgegaan door "> ".
        # Verwijder alles vanaf de eerste "> "-regel tot aan de eerste lege regel.
        res = re.sub(r"^\s*> .*?(\n\n|\Z)", "", res, flags=re.DOTALL)
        # Inkrimpen: max 1200 chars per taak-resultaat, behoud kop + staart
        if len(res) > 1200:
            res = res[:900] + "\n\n[... midden weggelaten ...]\n\n" + res[-300:]
        blocks.append(f"### {r['skill']}: {r['title']}\n{res}")
    if not blocks:
        return ""
    return "\n\n".join(blocks)

async def _route_by_skill(
    skill: str, title: str, description: str, goal_id: str,
    prior_results: str = "",
) -> Tuple[str, str, str]:
    """Routeer een taak naar de juiste skill/agent op basis van het type.

    Retourneert (result, artifact, next_step):
      result    — het eindproduct (markdown)
      artifact  — waar het staat (URL of vault-pad; leeg = alleen in de DB)
      next_step — wat Vincent nog moet doen (leeg = niets)

    Infinite Context: injecteert Obsidian-context in de system prompt en
    logt het resultaat terug naar de vault (The Loop).
    """
    # ── READ: Haal context uit Obsidian ────────────────────────────
    project = _resolve_goal_project(goal_id)

    # Merk-/projectverankering: voorkomt dat een taak het verkeerde merk
    # noemt (bv. "Agent OS" i.p.v. Bijeen). Staat altijd bovenaan de prompt.
    brand_line = (
        f"\n\nMERK & PROJECT (verplicht, ga hier strikt van uit): je werkt voor "
        f"het merk/project **{project or 'het aangegeven project'}**. Noem "
        f"dit merk bij voorkeur bij naam; verzin geen ander merk of product.\n"
    )

    # ── Publisher: ECHTE actie — stage het artikel in de Wachtrij ──
    # In plaats van een LLM-concept dat nergens landt, gaat het artikel uit
    # eerdere content-taken als pending_review-job naar de Wachtrij-tab.
    if skill == "publisher":
        staged, blocked_reason = await _stage_to_wachtrij(goal_id, title, project)
        if not staged:
            # Geen echte actie = geen voltooide taak. Vroeger viel deze taak
            # terug op een LLM-concept met CONCEPT-banner en werd hij tóch
            # 'completed'; het doel heette dan afgerond terwijl er niets was
            # gepubliceerd — en dempte in die hoedanigheid de dashboard-alerts
            # (25 jul 2026). Falen is hier de eerlijke uitkomst.
            raise _NotExecuted(
                f"publiceren niet uitgevoerd — {blocked_reason or 'onbekende reden'}"
            )
        if staged:
            job_id, art_title, seo_score = staged
            result = (
                f"✅ **ECHTE ACTIE UITGEVOERD:** artikel \"{art_title}\" staat in de **Wachtrij** "
                f"ter review (job `{job_id}`, site {project}, SEO-score {seo_score}/100).\n\n"
                "Goedkeuren in de Wachtrij-tab publiceert het artikel écht (website + social). "
                "Er is nog niets live gezet — de menselijke review-gate blijft van kracht."
            )
            if _infinite_ctx.is_configured:
                _infinite_ctx.log_task_completion(
                    goal_id=goal_id, task_id=goal_id, title=title,
                    skill=skill, result=result, project=project, duration_ms=0,
                )
            next_step = f"Keur '{art_title}' goed of wijs af in de Wachtrij"
            _log_activity(goal_id, "wachtrij_staged",
                          f"'{art_title}' → Wachtrij (job {job_id}, score {seo_score})",
                          next_step=next_step)
            return result, "", next_step

    if _infinite_ctx.is_configured:
        obsidian_ctx = _infinite_ctx.build_task_context(
            title, description, goal_id, project
        )
    else:
        obsidian_ctx = ""

    system_prompt = (
        f"Je bent een {SKILL_DESCRIPTIONS.get(skill, 'AI-assistent')} die werkt voor het merk/project **{project or 'het aangegeven project'}**. "
        f"Voer de volgende taak uit. Lever een concreet, bruikbaar eindproduct in Markdown. "
        f"Schrijf in het Nederlands. Noem waar relevant het merk/project bij naam; verzin geen ander merk of product."
    )
    system_prompt += brand_line
    if prior_results:
        system_prompt += (
            "\n\n## Eerdere resultaten uit dit doel (verplichte context)\n"
            "Hieronder staan de uitkomsten van eerdere taken in HETZELFDE doel. "
            "Gebruik deze als bronmateriaal - baseer je werk hierop en herhaal ze niet:\n\n"
            + prior_results
        )
    if obsidian_ctx:
        system_prompt += "\n\n" + obsidian_ctx

    # ── Guardrail + echte tools: data en bronnen i.p.v. verzinsels ──
    analytics_ctx = ""
    if skill in _DATA_SKILLS:
        analytics_ctx = await _real_analytics_context(project)
        if analytics_ctx:
            system_prompt += "\n\n" + analytics_ctx
        else:
            system_prompt += (
                "\n\nJe hebt GEEN toegang tot echte analytics-data voor dit project. "
                "Lever daarom een meetplan (welke KPI's, welke tool, welke meetperiode, "
                "welk beslismoment) — GEEN rapportage met cijfers."
            )
    if skill == "seo":
        # SEO-taken krijgen dezelfde echte GSC-cijfers als analyst-taken,
        # zodat optimalisatie-advies op werkelijke posities is gebaseerd.
        seo_ctx = await _real_analytics_context(project)
        if seo_ctx:
            system_prompt += "\n\n" + seo_ctx
    if skill in _RESEARCH_SKILLS:
        web_ctx = await _web_research_context(title, description)
        if web_ctx:
            system_prompt += "\n\n" + web_ctx
        else:
            system_prompt += (
                "\n\nEr is geen live-webtoegang beschikbaar voor deze taak. Baseer je "
                "uitsluitend op de meegegeven context en benoem expliciet welke "
                "informatie je NIET hebt kunnen verifiëren."
            )
    if skill in _CONCEPT_ONLY_SKILLS:
        system_prompt += (
            f"\n\nJe kunt zelf NIETS {'publiceren of deployen' if skill == 'publisher' else 'versturen of verzenden'} — "
            "lever een kant-en-klaar concept plus een stappenlijst voor de menselijke uitvoering."
        )
    if skill in _CONCEPT_ONLY_SKILLS or skill in _DATA_SKILLS or skill in _RESEARCH_SKILLS or skill == "seo":
        system_prompt += _NO_FABRICATION_RULE

    user_prompt = f"## Taak: {title}\n\n{description}\n\n## Context\nDit is onderdeel van goal: {goal_id}\n\nLever je resultaat."

    # ── Synthese: Claude eerst (beste model), Hermes als terugval ──
    # De echte data (Tavily-webresearch, GSC-cijfers) zit al deterministisch
    # in de system prompt; het model hoeft alleen nog wereldklasse te schrijven.
    result = ""
    if _GOAL_USE_CLAUDE:
        from ..chat import claude as claude_service
        if claude_service.is_configured():
            try:
                result = (await claude_service.get_response(
                    messages=[{"role": "user", "content": user_prompt}],
                    system_prompt=system_prompt,
                    max_tokens=4096,
                    purpose="goal",
                )).strip()
            except Exception as e:
                logger.warning(f"Claude-synthese mislukt voor taak '{title}' — terugval op Hermes: {e}")

    if not result:
        # Hermes-terugval. Alleen research (websearch) en seo krijgen échte
        # tools; analyst en _DATA_SKILLS krijgen de data deterministisch via
        # _real_analytics_context (zie hierboven) en draaien tool-LOOS - zo
        # voorkomen we ConnectError/timeout-crashes bij het extern pollen van
        # analytics. Schrijfwerk blijft ook tool-loos (kleine modellen + tools = flaky).
        agentic = skill in _RESEARCH_SKILLS or skill == "seo"
        full = ""
        async for chunk in agent_service.run_agent(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=system_prompt,
            agent="hermes",
            use_tools=agentic,
        ):
            if chunk.get("type") == "text":
                full += chunk["text"]
            elif chunk.get("type") == "error":
                raise RuntimeError(chunk.get("message", "Agent error"))
        result = full.strip()
    result = result or "(geen output)"

    # Markeer concept-output onmiskenbaar als concept, zodat het resultaat
    # nooit voor een uitgevoerde actie of echt rapport kan doorgaan.
    if result != "(geen output)":
        if skill in _CONCEPT_ONLY_SKILLS:
            result = _CONCEPT_BANNER + result
        elif skill in _DATA_SKILLS and not analytics_ctx:
            result = _NO_DATA_BANNER + result

    # ── WRITE: Log resultaat terug naar Obsidian ───────────────────
    # Het vault-pad is het artefact: de plek waar Vincent het resultaat vindt.
    artifact = ""
    if _infinite_ctx.is_configured and result and result != "(geen output)":
        task_path = _infinite_ctx.log_task_completion(
            goal_id=goal_id,
            task_id=goal_id,  # approximate — caller has real task_id
            title=title,
            skill=skill,
            result=result,
            project=project,
            duration_ms=0,
        )
        if task_path:
            artifact = str(task_path)

    next_step = ""
    if skill in _CONCEPT_ONLY_SKILLS:
        next_step = "Concept klaar — verstuur/publiceer zelf of keur af"
    return result, artifact, next_step


async def _find_alternative(skill: str, title: str, description: str, error: str) -> Optional[str]:
    """Self-correctie: probeer een alternatieve aanpak bij falen."""
    system = (
        "Je bent een probleemoplosser. De oorspronkelijke taak is mislukt. "
        "Bedenk en voer een alternatieve, eenvoudigere aanpak uit die wél werkt. "
        "Wees pragmatisch — levere een bruikbaar resultaat, ook al is het minder uitgebreid."
        + _NO_FABRICATION_RULE
    )
    prompt = (
        f"Oorspronkelijke taak: {title}\n"
        f"Beschrijving: {description}\n"
        f"Foutmelding: {error}\n\n"
        f"Voer een alternatieve, eenvoudigere versie van deze taak uit. "
        f"Lever een concreet resultaat."
    )
    try:
        full = ""
        async for chunk in agent_service.run_agent(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system,
            agent="hermes",
            use_tools=False,
        ):
            if chunk.get("type") == "text":
                full += chunk["text"]
            elif chunk.get("type") == "error":
                return None
        return full.strip() or None
    except Exception:
        return None


async def _execution_loop(goal_id: str) -> None:
    """De centrale achtergrond-loop: verwerkt taken sequentieel per fase.
    
    Flow per tick:
    1. Check current phase status
    2. Find ready tasks (dependencies resolved)
    3. Execute next ready task
    4. On success → update counts, check for next phase
    5. On retry → loop picks it up again
    6. On terminal failure → mark phase, continue to next
    """
    _update_goal(goal_id, status="running", started_at=_now())

    event_bus.publish({"type": "goal_started", "goal_id": goal_id})

    try:
        while True:
            # Haal goal metadata op
            with get_conn() as conn:
                goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if not goal:
                break

            goal = dict(goal)

            # Check of alle fasen klaar zijn
            with get_conn() as conn:
                phases = conn.execute(
                    "SELECT * FROM goal_phases WHERE goal_id = ? ORDER BY ord ASC",
                    (goal_id,),
                ).fetchall()

            all_done = True
            current_phase_running = False

            for phase in phases:
                p = dict(phase)

                if p["status"] == "completed":
                    continue

                if p["status"] == "failed":
                    # Als een fase faalde, kijk of we door kunnen naar de volgende
                    continue

                all_done = False
                current_phase_running = True

                # Zet phase op 'running' als die nog 'pending' is
                if p["status"] == "pending":
                    _update_phase(p["id"], status="running")
                    _update_goal(goal_id, current_phase=p["ord"])
                    event_bus.publish({
                        "type": "goal_phase_start", "goal_id": goal_id,
                        "phase_id": p["id"], "phase_title": p["title"], "phase_num": p["ord"],
                    })
                    _log_activity(goal_id, "phase_start", f"Fase {p['ord']}: {p['title']}")

                # Check dependencies en voer ready-taken uit
                ready_tasks = _get_ready_tasks(goal_id)
                if not ready_tasks:
                    # Check of er nog pending taken zijn met (nog) niet-voldane dependencies
                    with get_conn() as conn2:
                        pending = conn2.execute(
                        "SELECT id, dependencies, status FROM goal_tasks "
                        "WHERE goal_id = ? AND phase_id = ? AND status IN ('pending','ready','running')",
                        (goal_id, p["id"]),
                    ).fetchall()

                    if not pending:
                        # Geen taken meer in deze fase — markeer als completed
                        can_complete = _mark_phase_done(goal_id, p["id"])
                        if can_complete:
                            _update_phase(p["id"], status="completed")
                            _update_goal(goal_id, completed_tasks=goal.get("completed_tasks", 0) + 1)
                            event_bus.publish({
                                "type": "goal_phase_done", "goal_id": goal_id,
                                "phase_id": p["id"], "phase_title": p["title"],
                            })
                            _log_activity(goal_id, "phase_done", f"Fase {p['ord']}: {p['title']}")
                    else:
                        # Update pending tasks — check of dependencies nu resolved zijn
                        for pt in pending:
                            pt_dict = dict(pt)
                            deps_ok = _resolve_dependencies(pt_dict)
                            if deps_ok and pt_dict["status"] == "pending":
                                _update_task(pt_dict["id"], status="ready")

                    break  # Herstart de while-loop

                # Pak de eerste ready taak
                task = ready_tasks[0]
                _update_goal(goal_id, current_task=task["id"])

                try:
                    await _execute_task(goal_id, task)
                except _RetryLater:
                    # Wordt opnieuw opgepakt in de volgende iteratie
                    await asyncio.sleep(2)
                    break
                except Exception as e:
                    # Fatale fout — markeer taak als failed
                    logger.error(f"Goal {goal_id} taak {task['id']} fatale fout: {e}")
                    _update_task(task["id"], status="failed", error=str(e)[:500])

                # Update goal counts
                counts = _count_tasks(goal_id)
                _update_goal(goal_id,
                             completed_tasks=counts["completed"],
                             failed_tasks=counts["failed"])
                event_bus.publish({
                    "type": "goal_progress", "goal_id": goal_id,
                    **counts,
                })

                await asyncio.sleep(1)  # Korte pauze tussen taken
                break  # Herstart de while-loop voor de volgende taak

            if all_done:
                counts = _count_tasks(goal_id)
                status = "completed" if counts["failed"] == 0 else "partial"
                _update_goal(goal_id, status=status, finished_at=_now(),
                             completed_tasks=counts["completed"], failed_tasks=counts["failed"])
                event_bus.publish({
                    "type": "goal_done", "goal_id": goal_id, "status": status, **counts,
                })
                _log_activity(goal_id, "goal_done",
                              f"Goal afgerond: {status}, {counts['completed']}/{counts['total']} taken",
                              next_step="Bekijk de taakresultaten in de Doelen-tab" if status == "completed" else "")

                # ── WRITE: Log goal-resultaat naar Obsidian (Infinite Context) ──
                if _infinite_ctx.is_configured:
                    try:
                        project = _resolve_goal_project(goal_id)
                        _infinite_ctx.log_goal_completion(
                            goal_id=goal_id,
                            title=goal.get("title", ""),
                            objective=goal.get("objective", ""),
                            project=project,
                            summary=goal.get("plan_summary", ""),
                            phase_count=len(phases),
                            task_count=counts.get("total", 0),
                            completed=counts.get("completed", 0),
                            failed=counts.get("failed", 0),
                        )
                        logger.info(f"Infinite Context: goal '{goal.get('title')}' gelogd naar Obsidian")
                    except Exception as e:
                        logger.warning(f"Infinite Context goal log mislukt: {e}")

                break

            await asyncio.sleep(2)  # Korte pauze tussen loop-iteraties

    except Exception as e:
        logger.exception(f"Goal {goal_id} execution loop crashed: {e}")
        _update_goal(goal_id, status="failed", finished_at=_now())
        event_bus.publish({"type": "goal_error", "goal_id": goal_id, "error": str(e)})


def _mark_phase_done(goal_id: str, phase_id: str) -> bool:
    """Check of een fase voltooid kan worden (geen pending/running/failed tasks zonder alternatief)."""
    with get_conn() as conn:
        tasks = conn.execute(
            "SELECT status FROM goal_tasks WHERE goal_id = ? AND phase_id = ?",
            (goal_id, phase_id),
        ).fetchall()

    has_any_done = any(t["status"] == "completed" for t in tasks)
    has_running = any(t["status"] in ("running", "pending", "ready") for t in tasks)

    if not has_running:
        return has_any_done  # True als er minimaal 1 completed taak is
    return False


def _spawn_execution(goal_id: str) -> None:
    """Start de achtergrond-loop voor een goal en houd bij dat hij actief is
    (nodig om na een server-restart 'weeskind'-goals te kunnen herkennen: hun
    status staat nog op 'running' in de DB, maar er draait geen asyncio-taak
    meer voor ze in dit proces)."""
    async def _run() -> None:
        try:
            await _execution_loop(goal_id)
        finally:
            _ACTIVE_GOAL_IDS.discard(goal_id)

    task = asyncio.create_task(_run())
    _ACTIVE_GOAL_IDS.add(goal_id)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def start_goal_async(goal_id: str) -> Dict[str, Any]:
    """Async variant — roept start_goal aan in de juiste event loop context."""
    # Delegeer naar de sync functie die nu in de juiste event loop draait
    with get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        raise ValueError(f"Goal '{goal_id}' niet gevonden")
    if goal["status"] not in ("draft", "ready"):
        raise ValueError(f"Goal '{goal_id}' heeft status '{goal['status']}', niet 'ready' of 'draft'")

    _spawn_execution(goal_id)
    _log_activity(goal_id, "goal_start", f"Goal '{goal['title']}' gestart")

    return {"goal_id": goal_id, "status": "running"}


def start_goal(goal_id: str) -> Dict[str, Any]:
    """Start de executie-loop voor een goal (na plan-goedkeuring)."""
    with get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        raise ValueError(f"Goal '{goal_id}' niet gevonden")
    if goal["status"] not in ("draft", "ready"):
        raise ValueError(f"Goal '{goal_id}' heeft status '{goal['status']}', niet 'ready' of 'draft'")

    _spawn_execution(goal_id)
    _log_activity(goal_id, "goal_start", f"Goal '{goal['title']}' gestart")

    return {"goal_id": goal_id, "status": "running"}


def delete_goal(goal_id: str) -> None:
    """Verwijder een goal (en via ON DELETE CASCADE zijn fasen/taken).

    Alleen bedoeld voor niet-actieve goals (draft/completed/failed) — een
    'running' goal moet eerst gepauzeerd worden.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT id, status FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not row:
            raise ValueError(f"Goal '{goal_id}' niet gevonden")
        if row["status"] == "running" and goal_id in _ACTIVE_GOAL_IDS:
            raise ValueError(f"Goal '{goal_id}' draait nog — pauzeer eerst")
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


def resume_stalled_goal(goal_id: str) -> Dict[str, Any]:
    """Herstart de achtergrond-loop voor een goal die in de DB nog op 'running'
    staat maar geen actieve asyncio-taak meer heeft — typisch na een
    server-restart, waarbij achtergrondtaken niet overleven."""
    with get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        raise ValueError(f"Goal '{goal_id}' niet gevonden")
    if goal["status"] != "running":
        raise ValueError(f"Goal '{goal_id}' heeft status '{goal['status']}', niet 'running'")
    if goal_id in _ACTIVE_GOAL_IDS:
        return {"goal_id": goal_id, "status": "running", "already_active": True}

    # Taken die nog op 'running' staan zijn wees geworden door de crash/herstart
    # zelf (er draait geen asyncio-taak meer die ze afmaakt) — zonder reset
    # worden ze nooit meer door _get_ready_tasks() opgepikt en blijft de goal
    # stil vastzitten, ook na deze resume.
    with get_conn() as conn:
        conn.execute(
            "UPDATE goal_tasks SET status='ready', started_at=NULL WHERE goal_id=? AND status='running'",
            (goal_id,),
        )

    _spawn_execution(goal_id)
    _log_activity(goal_id, "goal_resumed", f"Goal '{goal['title']}' hervat na herstart")

    return {"goal_id": goal_id, "status": "running"}


def is_goal_active(goal_id: str) -> bool:
    return goal_id in _ACTIVE_GOAL_IDS


async def retry_failed_goal(goal_id: str) -> Dict[str, Any]:
    """Reset een failed goal: zet alle failed taken terug naar ready, herstart."""
    with get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        raise ValueError(f"Goal '{goal_id}' niet gevonden")
    # 'partial' = deels voltooid, deels gefaald — precies het geval waarin je de
    # gefaalde taken opnieuw wil draaien; alleen 'failed' toestaan liet die goals
    # onherstelbaar hangen (incident 2026-07-15). Voltooide taken blijven staan
    # (de UPDATE hieronder raakt alleen status='failed'/'running').
    if goal["status"] not in ("failed", "partial"):
        raise ValueError(f"Goal '{goal_id}' heeft status '{goal['status']}', niet 'failed'/'partial'")

    with get_conn() as conn:
        # Reset failed tasks back to ready
        conn.execute(
            "UPDATE goal_tasks SET status='ready', retry_count=0, error=NULL, finished_at=NULL, result=NULL WHERE goal_id=? AND status='failed'",
            (goal_id,),
        )
        # Reset running tasks back to ready
        conn.execute(
            "UPDATE goal_tasks SET status='ready', retry_count=0, error=NULL, started_at=NULL, finished_at=NULL WHERE goal_id=? AND status='running'",
            (goal_id,),
        )
        # Reset ELKE fase die nog een niet-voltooide taak bevat terug naar
        # 'pending' — óók als de fase zelf 'completed' is. Een fase kan namelijk
        # als 'done' zijn gemarkeerd terwijl één taak faalde (_mark_phase_done
        # laat een fase sluiten zodra er geen ready/running-taken meer zijn,
        # dus met alleen een gefaalde taak erin). Zonder deze reset blijven de
        # zojuist naar 'ready' gezette taken in een completed-fase hangen en
        # pakt de executie-lus ze nooit op (incident 2026-07-15).
        conn.execute(
            "UPDATE goal_phases SET status='pending' WHERE goal_id=? AND id IN ("
            "  SELECT DISTINCT phase_id FROM goal_tasks "
            "  WHERE goal_id=? AND status <> 'completed')",
            (goal_id, goal_id),
        )
        # Reset goal back to ready
        conn.execute(
            "UPDATE goals SET status='ready', completed_tasks=0, failed_tasks=0 WHERE id=?",
            (goal_id,),
        )

    _log_activity(goal_id, "goal_retry", f"Goal '{goal['title']}' herstart via AI-retry")

    # Start execution
    _spawn_execution(goal_id)

    return {"goal_id": goal_id, "status": "running", "message": f"Doel '{goal['title']}' herstart"}


def pause_goal(goal_id: str) -> Dict[str, Any]:
    """Pauzeer een goal (lopende taken worden niet geannuleerd, maar nieuwe worden niet gestart)."""
    _update_goal(goal_id, status="paused")
    _ACTIVE_GOAL_IDS.discard(goal_id)
    _log_activity(goal_id, "goal_paused", "Goal gepauzeerd")
    return {"goal_id": goal_id, "status": "paused"}


def resume_goal(goal_id: str) -> Dict[str, Any]:
    """Hervat een gepauzeerde goal."""
    goal = get_goal(goal_id)
    if not goal or goal["status"] != "paused":
        raise ValueError("Goal is niet gepauzeerd")
    return start_goal(goal_id)


# ═════════════════════════════════════════════════════════════════════
#  5. QUERIES
# ═════════════════════════════════════════════════════════════════════

def get_goal(goal_id: str) -> Optional[Dict[str, Any]]:
    """Haal een goal op met alle fasen en taken."""
    with get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not goal:
            return None
        phases = conn.execute(
            "SELECT * FROM goal_phases WHERE goal_id = ? ORDER BY ord ASC",
            (goal_id,),
        ).fetchall()
        result = dict(goal)
        result["phases"] = []
        for phase in phases:
            p = dict(phase)
            tasks = conn.execute(
                "SELECT * FROM goal_tasks WHERE goal_id = ? AND phase_id = ? ORDER BY ord ASC",
                (goal_id, phase["id"]),
            ).fetchall()
            p["tasks"] = [dict(t) for t in tasks]
            result["phases"].append(p)
    return result


def list_goals(limit: int = 10, project: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lijst recente goals, optioneel gefilterd op project."""
    with get_conn() as conn:
        if project:
            rows = conn.execute(
                "SELECT g.id, g.title, g.objective, g.project, g.status, "
                "g.phase_count, g.task_count, g.completed_tasks, g.failed_tasks, "
                "g.created_at, g.started_at, g.finished_at "
                "FROM goals g WHERE LOWER(g.project) = LOWER(?) ORDER BY g.created_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT g.id, g.title, g.objective, g.project, g.status, "
                "g.phase_count, g.task_count, g.completed_tasks, g.failed_tasks, "
                "g.created_at, g.started_at, g.finished_at "
                "FROM goals g ORDER BY g.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        t = conn.execute("SELECT * FROM goal_tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(t) if t else None

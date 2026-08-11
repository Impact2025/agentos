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
import html
import json
import logging
import os
import re
import unicodedata
import uuid

import httpx
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ...shared.database import get_conn
from ...shared.hermes_context import build_hermes_context
from ..chat import hermes as hermes_service
from . import article_writer
from . import service as publish_service
from ..seo import engine as demand_engine
from ..seo import external_content as external_content_service
from ..seo import gsc as gsc_service
from ..seo import knowledge as knowledge_service
from ..seo import opportunity_quality as demand_quality
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
    """Maak een URL-veilige slug: alleen a-z, 0-9 en koppeltekens.

    Bewust een witte lijst en geen lijst-van-te-vervangen-tekens (27 jul 2026).
    De oude versie verving een handjevol leestekens en liet al het andere staan,
    waardoor er slugs live gingen als
    'levensverhaal-vastleggen-complete-gids-+-casestudy-anton-(12' en
    'schrijf-meta-titel-&-description-voor-pagina-2'. Beide gaven een harde 404:
    een '&' of '(' in een pad overleeft de route-matching van geen enkele site.
    Bij een zwarte lijst is elk teken dat je niet bedacht een toekomstige 404;
    bij een witte lijst kan er per definitie niets doorheen glippen.
    """
    # Accenten ontleden (é -> e) i.p.v. per teken opsommen — dekt ook ç, ñ, ø.
    normalised = unicodedata.normalize("NFKD", (title or "").strip().lower())
    stripped = "".join(c for c in normalised if not unicodedata.combining(c))
    # Alles wat geen letter/cijfer is wordt een koppelteken; daarna inklappen.
    slug = re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")
    # Kap af op een woordgrens. Een harde [:60] sneed midden in een woord
    # ("…-bouw-wat-woor") en leverde een URL op die nergens naar verwijst —
    # zo raakten de artikelen van 24 jul 2026 uit de sitemap (25 jul 2026).
    if len(slug) > 60:
        slug = slug[:60].rsplit("-", 1)[0] if "-" in slug[:60] else slug[:60]
    return slug.strip("-")


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


def _learned_writing_lessons() -> str:
    """Gemeten vorm-lessen uit de content-leerlus (welke artikel-kenmerken
    clicks opleveren). Defensief: geen lessen of fout = lege string."""
    try:
        from ...shared.learning import lessons_block
        return lessons_block("content")
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
    dubbel artikel.

    Sinds 2 aug 2026 loopt de keuze door dezelfde kwaliteitsgate als het
    Kansen-paneel (`opportunity_quality`). Anders schrijft de autonome motor
    's nachts alsnog het artikel dat Vincent overdag met reden weggefilterd
    ziet — en dan is het filter alleen cosmetiek."""
    kansen = demand_engine.list_opportunities(site_id=site["id"], status="new")
    if not kansen:
        return None
    # Externe CMS-DB (indien geconfigureerd) + live sitemap (zero-config) —
    # zodat ook content die buiten Agent OS om is gepubliceerd meetelt.
    external_titles = external_content_service.fetch_all_known_content(site)
    try:
        demand_quality.annotate(kansen, site)
    except Exception as e:  # noqa: BLE001
        logger.warning("[content-pipeline] Kwaliteitsgate op kansen overgeslagen: %s",
                       str(e)[:200])
    for kans in kansen:
        reason = kans.get("filter_reason")
        if reason:
            # 'in-wachtrij' laten we op 'new' staan noch dismissen: het artikel
            # is al onderweg, de kans wordt vanzelf 'published' of komt vrij als
            # het concept wordt afgewezen (`reconcile_opportunities`).
            if reason != "in-wachtrij":
                demand_engine.update_opportunity_status(kans["id"], "dismissed")
                _log_activity(site["name"], "kans-overgeslagen-" + reason,
                              f"'{kans['query']}' overgeslagen — "
                              f"{demand_quality.REASON_LABELS[reason]}: "
                              f"{kans.get('filter_detail') or ''}".strip())
            continue
        if external_titles and _topic_already_covered(kans["query"], external_titles):
            demand_engine.update_opportunity_status(kans["id"], "dismissed")
            _log_activity(site["name"], "kans-overgeslagen-dubbel",
                          f"'{kans['query']}' staat al op de site — overgeslagen i.p.v. dubbel geschreven.")
            continue
        demand_engine.update_opportunity_status(kans["id"], "in_progress")
        return kans
    return None


def _bijvullen_en_opnieuw_kiezen(site: Dict) -> Optional[Dict]:
    """De voorraad is op — vul hem hier en nu bij in plaats van over te slaan.

    Aanleiding (4 aug 2026): `run_weekly_demand_scan` draagt in zijn eigen
    docstring de belofte "zonder deze job raakt de kansen-voorraad op en valt de
    di/vr-contentmotor stil zonder dat iemand het ziet". Precies dat gebeurde
    tóch, want de belofte klopt maar de cadans niet: de scan draait maandag
    06:15, de motor dinsdag én vrijdag. Wat maandag wordt aangeboden is tegen
    donderdag opgebruikt of weggefilterd, en de vrijdagrun logt dan
    'auto-content-overslagen — voer eerst een Demand Engine-scan uit'. Dat is
    een instructie aan een mens, in een logregel die geen mens leest, van een
    motor die de scan zélf had kunnen draaien. WeAreImpact stond zo op nul open
    kansen terwijl er 1.727 vertoningen per 28 dagen binnenkwamen.

    Drie stappen, in deze volgorde — dezelfde als de weekscan, en om dezelfde
    reden: eerst teruggeven wat onterecht bezet is, dan pas nieuw zoeken.
    Anders halen we een kans van buiten terwijl er één vaststaat op
    'in_progress' van een artikel dat al is afgewezen.

    Faalt de bijvulling, dan is dat geen fout van de contentrun: de aanroeper
    logt 'overgeslagen' en de scheduler probeert het maandag opnieuw. Wel luid
    in de log, want een GSC-koppeling die stuk is hoort vindbaar te zijn.
    """
    naam = site.get("name") or site.get("id") or "?"
    try:
        demand_engine.reconcile_opportunities(site["id"])
    except Exception as e:  # noqa: BLE001
        logger.warning("[content-pipeline] Reconciliatie voor %s mislukt: %s", naam, str(e)[:200])

    gevonden = 0
    if (site.get("gsc_property") or "").strip():
        try:
            res = demand_engine.scan_site(site)
            gevonden = int(res.get("new", 0)) + int(res.get("cold_start", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("[content-pipeline] Demand-scan voor %s mislukt: %s", naam, str(e)[:200])

    opnieuw = select_topic(site)
    if opnieuw:
        _log_activity(naam, "kansen-bijgevuld",
                      f"Voorraad was op; verse Demand-scan leverde {gevonden} kans(en) op. "
                      f"De motor schrijft nu over '{opnieuw['query']}'.")
    return opnieuw


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
    # Content-leerlus: vorm-lessen gemeten uit de GSC-prestaties van eerdere
    # artikelen (zie publish/content_learning.py).
    learned = _learned_writing_lessons()
    if learned:
        write_system += f"\n\n## Gemeten vorm-lessen (pas toe waar passend)\n{learned}"

    # HERMES-context: als de vault-context hierboven leeg viel (geen PROJECT_CORE/
    # SCHRIJF-DNA via VaultReader), gebruik dan Vincent's Hermes-skills + de
    # vault SCHRIJF-DNA-note als robuuste, projectbewuste aanvulling. Opt-in via
    # AGENTOS_USE_HERMES_SKILLS. Nooit een crash, lege context = geen effect.
    hermes_ctx = build_hermes_context(project_name)
    if hermes_ctx:
        write_system += f"\n\n{hermes_ctx}"

    write_prompt = (
        f"Schrijf een compleet blogartikel voor {project_name}.\n\n"
        f"Kernzoekwoord: {keyword}\n"
        f"Invalshoek: {angle}\n"
        f"Rationale: {rationale}\n\n"
        f"Project-context (SKILL.md):\n{skill_body}\n\n"
        f"Bestaande artikelen (vermijd overlap): {', '.join(existing_titles[:15])}\n\n"
    )
    # Onderzoek is beschikbaar (NotebookLM-rapporten uit de vault): eis dat de
    # schrijf-agent minimaal één concreet, citeerbaar inzicht verwerkt. Dat
    # verhoogt de E-E-A-T-score én maakt het artikel uniek t.o.v. de 10 andere
    # sites die hetzelfde onderwerp beschrijven — en het duwt de score boven de
    # kwaliteitsgrens (GEO: een antwoord moet iets toevoegen, geen herhaling).
    if vault_context:
        write_prompt += (
            "VERPLICHTPOST: de 'Merkcontext uit Obsidian vault' hierboven bevat "
            "onderzoek (NotebookLM). Verwerk MINIMAAL ÉÉN specifiek, feitelijk "
            "inzicht uit dat onderzoek in het artikel — bij voorkeur met een "
            "concreet cijfer of bevinding — en benoem het als onderbouwing (bijv. "
            "een tussenkop 'Wat onderzoek laat zien' of een genummerd inzicht). "
            "Zonder die verwerking is het artikel niet uniek genoeg en wordt het "
            "afgekeurd. Verzin geen cijfers die niet in het onderzoek staan.\n\n"
        )
    write_prompt += (
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


_BYLINE_RE = re.compile(
    r"^\s*(auteur|door|publicatiedatum|gepubliceerd|laatst bijgewerkt|leestijd|samenvatting)\b"
    r"|^\s*\w[\w\s]{0,40}\|\s*(publicatiedatum|gepubliceerd)",
    re.IGNORECASE,
)


def _derive_meta_desc(html_body: str) -> str:
    """Meta-description afgeleid uit de body, voor artikelen zonder expliciet
    META-blok. Twee dingen die eerder misgingen en elke keer een reviewer-aftrek
    opleverden: de <h1> telde mee, waardoor de description letterlijk met de
    titel begon; en het afkappen op `[:155] + '…'` gaf 156 tekens én brak midden
    in een woord. Strip dus de kop en kap op woordgrens binnen de 155."""
    # Koppen eruit vóór het platslaan: `_strip_duplicate_header` haakt op een
    # <h1> aan het begín van de body en mist hem zodra de generator het artikel
    # in een wrapper-<div> zet — precies het geval hier. Een description hoort
    # sowieso uit de lopende tekst te komen, dus wieden we álle koppen, waar ze
    # ook staan.
    body = re.sub(r"<h[1-6]\b[^>]*>[\s\S]*?</h[1-6]>", " ", html_body or "", flags=re.I)
    paragraphs = re.findall(r"<p\b[^>]*>([\s\S]*?)</p>", body, flags=re.I)
    chunks = []
    for p in paragraphs:
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p)).strip()
        # Byline-/metaregels ("Auteur: ... | Publicatiedatum: ...", "Samenvatting:")
        # zijn geen lopende tekst en hoorden nooit in een meta-description.
        if not t or _BYLINE_RE.match(t):
            continue
        chunks.append(t)
        if len(" ".join(chunks)) >= 155:
            break
    text = " ".join(chunks).strip()
    if not text:  # geen bruikbare alinea's — val terug op de hele body
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return _smart_truncate(text, 155)


_META_TITLE_MAX = 60

# Het model schrijft met enige regelmaat zijn eigen tekenaantal ín de titel
# ("... zo val je op als interimmer (54 tekens)"). Dat is instructie-echo, geen
# titel, en het ging ongefilterd de <title> van live pagina's in.
_META_ANNOTATIE = re.compile(r"\s*[\(\[]\s*\d{1,3}\s*(?:tekens?|chars?|characters?)\s*[\)\]]\s*$",
                             re.IGNORECASE)


def meta_title_for(title: str, max_len: int = _META_TITLE_MAX) -> str:
    """De meta-titel zoals hij de <title> in hoort — één definitie voor het
    hele systeem.

    Aanleiding (2 aug 2026): 47 van 103 artikelen droegen een meta-titel die op
    exact 60 tekens midden in een woord was afgekapt ('... Jouw teambeleving in
    de l'), waarvan er 15 al live stonden. De reviewer trok daar élke meting
    punten voor af — terecht — en omdat de titel buiten de body valt kon geen
    enkele herschrijfronde het repareren. Zes verbeterrondes per artikel liepen
    zich daarop stuk: de score bewoog wel (ruis) maar steeg nooit.

    Voor slugs is deze les al in 2026 geleerd (zie `slugify_title`); de
    meta-titel had dezelfde fix nooit gekregen, op vier plekken tegelijk —
    de review-preview én de drie publicatieroutes. Vandaar één helper: vier
    kopieën van dezelfde regel is hoe ze uit elkaar gaan lopen.

    Drie bewerkingen, in deze volgorde:
      1. instructie-echo eraf ('(54 tekens)');
      2. HTML-entiteiten terug naar tekens — '&amp;' hoort niet in een <title>;
      3. inkorten op een woordgrens, nooit midden in een woord.
    """
    t = _META_ANNOTATIE.sub("", (title or "").strip())
    t = html.unescape(t).strip()
    if len(t) <= max_len:
        return t
    # Woordgrens: knip op de laatste spatie binnen de limiet. Levert dat niets
    # bruikbaars op (één lang woord), dan alsnog hard afkappen — een te lange
    # titel is erger dan een afgekapte.
    kort = t[:max_len].rsplit(" ", 1)[0].rstrip(" -–—|·,;:")
    return kort if len(kort) >= max_len // 2 else t[:max_len]


def _preview_meta(html_body: str) -> tuple:
    """De meta-titel/description zoals die bij publicatie daadwerkelijk wordt
    weggeschreven: uit expliciete META-blokken, anders afgeleid uit de body.
    Spiegelt `_publish_to_site` — zie de meta-afleiding daar.

    Let op: dit moet exact hetzelfde opleveren als wat de publisher wegschrijft.
    Wijkt het af, dan beoordeelt de reviewer een titel die nooit bestaat en is
    zijn aftrek per definitie onrepareerbaar — daarom lopen beide via
    `meta_title_for`.
    """
    cleaned, meta_title, meta_desc = _strip_meta_and_suggestions(html_body or "")
    title = meta_title or _extract_title(cleaned, fallback="")
    return meta_title_for(title), meta_desc or _derive_meta_desc(cleaned)


async def _review_article(site: Dict, keyword: str, html_body: str) -> Dict:
    review_system = _profile_prompt("SEO Editor") or _FALLBACK_REVIEW_PROMPT
    # De rubriek beoordeelt óók meta-titel/description, maar de schrijver levert
    # per opdracht alleen de HTML-body zonder <head> — die velden ontstaan pas bij
    # publicatie. Zonder ze mee te sturen trok de reviewer élke ronde punten af
    # voor "meta ontbreekt", een aftrek die geen enkele herschrijfronde kan
    # repareren: artikelen bleven daardoor structureel onder de gate hangen en
    # liepen hun verbeter-pogingen op aan een niet-bestaand gebrek. Toon dus wat
    # er echt gepubliceerd wordt.
    meta_title, meta_desc = _preview_meta(html_body)
    review_prompt = (
        f"Beoordeel onderstaand blogartikel voor {site['name']}.\n\n"
        f"Kernzoekwoord: {keyword}\n\n"
        "De meta-velden staan niet in de body; dit zijn de waarden die bij "
        "publicatie worden weggeschreven — beoordeel déze:\n"
        f"- Meta-titel ({len(meta_title)} tekens): {meta_title or '(leeg)'}\n"
        f"- Meta-description ({len(meta_desc)} tekens): {meta_desc or '(leeg)'}\n\n"
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

    # ── Verzinsel-gate (hard) ───────────────────────────────────────────────
    # Vincent, 04-08-2026: "nooit verzonnen bedrijven of andere fake info —
    # het beste of niets." Twee LIVE artikelen bevatten verzonnen bedrijven én
    # verzonnen prijzen/cijfers over echt bestaande partijen. Een systeemprompt
    # is een verzoek, geen garantie: dit is het vangnet dat wél sluit.
    #
    # Anders dan de aftrekpunten hierboven is dit géén weging maar een VETO:
    # een niet-onderbouwde harde claim mag nooit door de gate, hoe goed de rest
    # ook is. Liever een artikel dat wacht op een mens dan een verzinsel online.
    try:
        claims = _detect_unsupported_claims(html_body)
        if claims:
            score = min(score, _VERZINSEL_MAX_SCORE)
            melding = ("VERZINSEL-VETO — onderbouw of verwijder deze claims: "
                       + "; ".join(claims[:5]))
            feedback = (feedback + " | " + melding) if feedback else melding
            logger.warning("[content-pipeline] Verzinsel-veto (%d claim(s)): %s",
                           len(claims), "; ".join(claims[:3]))
    except Exception as e:
        logger.debug("[content-pipeline] Verzinsel-gate overgeslagen: %s", str(e)[:120])

    return {"score": score, "feedback": feedback}


# Score-plafond bij een niet-onderbouwde harde claim. Ligt bewust onder elke
# publicatiegate, zodat het artikel naar review gaat i.p.v. live.
_VERZINSEL_MAX_SCORE = 45

# Zinnen die een controleerbare, harde bewering doen. Bewust NAUW gehouden:
# een valse positieve blokkeert echt werk, dus we vangen alleen patronen die
# in de twee echte incidenten voorkwamen.
_CLAIM_PATRONEN = (
    # Prijzen: "vanaf 499 euro", "€1.250 per maand", "kost 89,-"
    (r"(?:€\s?\d[\d.,]*|(?:vanaf|kost|kosten|prijs|tarief)\s+\d[\d.,]*\s*(?:euro|eur|€))",
     "prijsclaim"),
    # Percentages met effect: "23% meer", "stijging van 40%"
    (r"\b\d{1,3}(?:[.,]\d+)?\s?%\s*(?:meer|minder|hoger|lager|stijging|daling|groei|toename|afname|van de)",
     "percentageclaim"),
    # Onderzoek zonder bron: "uit onderzoek blijkt", "studies tonen aan"
    (r"\b(?:uit onderzoek blijkt|onderzoek toont aan|studies tonen aan|volgens onderzoek|wetenschappelijk bewezen)\b",
     "onderzoeksclaim zonder bron"),
    # Superlatief-ranglijsten over derden: "de 7 beste partners/bureaus".
    # Let op: LLM's schrijven het telwoord vaak VOLUIT ("Zeven AI-partners
    # die bewezen hebben..."), en dat was precies het tweede incident. Vandaar
    # dat zowel cijfers als Nederlandse telwoorden matchen, en dat een
    # opsomming óók zonder "beste" telt als hij derde partijen rangschikt.
    (r"\b(?:de\s+)?(?:\d{1,2}|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf)"
     r"\s+(?:\w+[- ])?(?:beste|top|meest gerenommeerde|bewezen)?\s*"
     r"(?:partners|bureaus|bedrijven|aanbieders|leveranciers|specialisten)\b",
     "ranglijst over derde partijen"),
)

# Losse detectie voor de vraag "gaat dit artikel OVER derde partijen?".
# Ruimer dan het veto-patroon hierboven: hier volstaat een aanwijzing, want
# het gevolg is alleen dat prijs/percentage-claims strenger worden bekeken.
_DERDEN_PATROON = (
    r"\b(?:de\s+)?(?:\d{1,2}|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf)"
    r"\s+(?:\w+[- ])?(?:beste|top|meest gerenommeerde|bewezen)?\s*"
    r"(?:partners|bureaus|bedrijven|aanbieders|leveranciers|specialisten|tools|platforms|datingsites|apps)\b"
)


def _detect_unsupported_claims(html_body: str) -> List[str]:
    """Vind harde feitelijke claims die niet met een bron zijn onderbouwd.

    NUANCE (04-08-2026, na een scan over 138 artikelen): prijzen en
    percentages zijn NIET per definitie fout. Een artikel als "Wat kost een
    hond uit het asiel?" hóórt bedragen te noemen — dat is het onderwerp, en
    een veto daarop zou 60 goede artikelen blokkeren en precies de ruis
    opleveren die we willen wegnemen.

    Het echte risico dat de incidenten veroorzaakte, is een claim OVER EEN
    DERDE PARTIJ: "Bureau X werkt vanaf 8.000 euro", "de 9 beste partners".
    Dat is smaad-gevoelig en juridisch riskant. Eigen tarieven of algemene
    kostenindicaties zijn dat niet.

    Daarom veto'en we alleen:
      - ranglijsten over derde partijen (altijd),
      - onderzoeksclaims zonder bron (altijd),
      - prijs/percentage-claims ALLEEN in een artikel dat derde partijen
        opsomt (de gevaarlijke combinatie uit beide incidenten).
    """
    if not html_body:
        return []

    tekst_totaal = re.sub(r"<[^>]+>", " ", html_body)
    tekst_totaal = re.sub(r"\s+", " ", tekst_totaal)
    # Somt dit artikel derde partijen op? Dan is elke prijs/percentage een
    # uitspraak over iemand anders' bedrijf.
    over_derden = bool(re.search(_DERDEN_PATROON, tekst_totaal, re.I))

    gevonden: List[str] = []
    alineas = re.split(r"</(?:p|li|h[1-6]|td|blockquote)>", html_body, flags=re.I)
    for alinea in alineas:
        tekst = re.sub(r"<[^>]+>", " ", alinea)
        tekst = re.sub(r"\s+", " ", tekst).strip()
        if not tekst:
            continue
        # Externe link in dezelfde alinea = onderbouwd.
        if re.search(r'<a\b[^>]*href="https?://', alinea, re.I):
            continue
        for patroon, label in _CLAIM_PATRONEN:
            if label in ("prijsclaim", "percentageclaim") and not over_derden:
                continue  # eigen budget-artikel: bedragen zijn het onderwerp
            m = re.search(patroon, tekst, re.I)
            if m:
                fragment = tekst[max(0, m.start() - 40):m.end() + 40].strip()
                gevonden.append(f"{label}: \"...{fragment}...\"")
                break
    return gevonden


def _internal_link_count(html_body: str, site: Dict) -> int:
    """Aantal links naar de eigen site (absoluut of als pad)."""
    host = urlparse((site.get("base_url") or "").strip()).netloc.lower().removeprefix("www.")
    n = 0
    for href in re.findall(r'<a\b[^>]*href="([^"]+)"', html_body or "", re.I):
        h = href.strip().lower()
        if h.startswith("/") or (host and host in h):
            n += 1
    return n


# ── Uniciteitscheck ─────────────────────────────────────────────────────────
# Vergelijkt een nieuw artikel met eerder gepubliceerde artikelen van dezelfde
# site via Jaccard-overlap op 5-woord shingles (woordvolgorde-gevoelig, dus
# een herschreven kopie met dezelfde zinnen scoort hoog ook al is geen zin
# letterlijk gelijk). Los van de LLM-kwaliteitsgate: die beoordeelt of een
# artikel goed geschreven is, niet of het onderwerp al eerder is behandeld.
_SHINGLE_N = 5
_UNIQUENESS_SIMILARITY_THRESHOLD = 0.30  # empirisch: ongerelateerde artikelen <0.05, een bijna-kopie >0.4


def _shingles(text: str, n: int = _SHINGLE_N) -> set:
    words = re.findall(r"[a-zà-ü0-9]+", (text or "").lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def _fetch_published_texts(site_id: str, exclude_job_id: Optional[str] = None,
                            limit: int = 150) -> List[Tuple[str, str]]:
    """(titel, platte tekst) van eerder gepubliceerde artikelen van deze site —
    het vergelijkingscorpus voor de uniciteitscheck. Alleen 'published': een
    'pending_review'/'needs_work' concept mag zichzelf niet als duplicaat zien."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, blog_html FROM content_jobs "
            "WHERE site_id=? AND status='published' AND blog_html IS NOT NULL AND blog_html!='' "
            "ORDER BY created_at DESC LIMIT ?",
            (site_id, limit),
        ).fetchall()
    out: List[Tuple[str, str]] = []
    for r in rows:
        if exclude_job_id and r["id"] == exclude_job_id:
            continue
        text = article_writer._plain_text(r["blog_html"] or "")
        if text.strip():
            out.append((r["title"], text))
    return out


def check_uniqueness(html_body: str, corpus: List[Tuple[str, str]],
                      threshold: float = _UNIQUENESS_SIMILARITY_THRESHOLD) -> Dict:
    """Retourneert {'pass', 'best_match', 'similarity', 'checked_against'}."""
    shingles = _shingles(article_writer._plain_text(html_body))
    best_title, best_score = "", 0.0
    for title, other_text in corpus:
        score = _jaccard(shingles, _shingles(other_text))
        if score > best_score:
            best_title, best_score = title, score
    return {
        "pass": best_score < threshold,
        "best_match": best_title if best_score > 0 else "",
        "similarity": round(best_score, 3),
        "checked_against": len(corpus),
    }


async def review_and_improve(site: Dict, keyword: str, html_body: str,
                             max_rounds: int = 6,
                             target_score: Optional[int] = None,
                             exclude_job_id: Optional[str] = None) -> tuple:
    """Review → verbeter → review, net zo lang tot de kwaliteitsgate
    (CONTENT_MIN_SCORE) is gehaald. Nooit eerder opgeven dan nodig: een artikel
    onder de grens mag het dashboard (en Vincents ogen) niet bereiken — de agent
    blijft zélf aanscherpen. `max_rounds` is alleen een harde veiligheidslimiet
    (tegen eindeloze LLM-loops); bij de biweekly/regenerate-routes wordt die
    ruim gezet (zie CONTENT_MAX_ROUNDS) zodat de grens in de praktijk wél gehaald
    wordt.

    `target_score` tilt de lat alléén voor deze aanroep. Nodig omdat de loop
    stopt zodra de gate gehaald is: wie bestaande artikelen naar een hógere lat
    wil tillen (bv. de opschoonronde naar 85) kreeg met de gate op 80 een loop
    die na de eerste 80 al tevreden was en niets deed. De globale gate blijft
    ongemoeid — die verhogen zou élke run strenger maken.

    `exclude_job_id` sluit een job uit het uniciteitscorpus: nodig zodra deze
    functie een al-gepubliceerd artikel doorverbetert, anders vergelijkt de
    check het artikel met zichzelf en faalt hij per definitie.

    Retourneert (html_body, review)."""
    from ...shared.config import CONTENT_MIN_SCORE, CONTENT_MAX_ROUNDS
    goal = int(target_score or CONTENT_MIN_SCORE)
    effective_max = max(max_rounds, CONTENT_MAX_ROUNDS)
    corpus = _fetch_published_texts(site["id"], exclude_job_id=exclude_job_id)

    async def _reviewed(body: str) -> Dict:
        r = await _review_article(site, keyword, body)
        if not r:
            # _review_article gaf None terug (model-fout) — val niet stil, geef
            # een minimale review zodat het artikel alsnog de wachtrij in gaat.
            r = {"score": 0, "feedback": "Review kon niet worden uitgevoerd."}
        uniq = check_uniqueness(body, corpus)
        r["uniqueness"] = uniq
        if not uniq["pass"]:
            # Geen aparte gate — leunt op dezelfde verbeter-loop als de reviewer:
            # score onder de grens duwen dwingt een herschrijfronde af, met de
            # overlap als expliciete instructie voor de optimizer.
            r["score"] = min(r["score"], goal - 1)
            r["feedback"] = (
                (r.get("feedback") or "").strip()
                + f"\n\nUNIEKHEID: dit artikel overlapt {uniq['similarity']:.0%} met een eerder "
                f"gepubliceerd artikel op deze site ('{uniq['best_match']}'). Herschrijf de "
                "invalshoek, voorbeelden en structuur zodat het inhoudelijk onderscheidend is."
            ).strip()
        return r

    review = await _reviewed(html_body)
    best_html, best_review = html_body, review
    rounds = 0
    while review["score"] < goal and rounds < effective_max and review["feedback"]:
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
                    rounds, review["score"], goal, site["name"])
        html_body = await _optimize_article(site, keyword, html_body, review["feedback"])
        # Een herschrijfronde kan gevalideerde interne links laten vallen of nieuwe
        # verzinnen — die zijn dan niet meer gevet, dus opnieuw wieden vóór de
        # volgende beoordeling (anders belanden 404-links op de live site).
        html_body, n_stripped = article_writer.strip_unvetted_internal_links(html_body, site)
        if n_stripped:
            logger.info("[content-pipeline] Optimalisatieronde %s: %d ongevette interne link(s) verwijderd",
                        rounds, n_stripped)
        # Interne links kunnen alleen hier terugkomen. De optimalisatieronde krijgt
        # van de reviewer stelselmatig "voeg interne links toe" te horen, verzint er
        # dan een paar, en die worden hierboven (terecht) gestript omdat ze niet
        # gevet zijn — netto verdwijnen ze en blijft de aftrek elke ronde staan.
        # De optimizer kent de kandidatenlijst namelijk niet; de linkstap wel.
        # Alleen draaien als het artikel er te weinig heeft, want het kost een
        # LLM-call per ronde.
        if _internal_link_count(html_body, site) < 2:
            try:
                html_body, link_report = await article_writer._link_pass(
                    site, keyword, html_body, ctas=knowledge_service.get_site_knowledge(site)["ctas"])
                logger.info("[content-pipeline] Optimalisatieronde %s: linkstap voegde %s interne link(s) toe",
                            rounds, link_report.get("internal_added"))
            except Exception as e:
                logger.warning("[content-pipeline] Linkstap in verbeter-loop mislukt: %s", str(e)[:120])
        review = await _reviewed(html_body)
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


def _looks_like_article(out: str, original: str) -> bool:
    """Is dit een herschreven artikel, of het antwoord op een ándere opdracht?

    De optimalisatiestap leverde regelmatig het review-JSON-object terug
    ({"score": .., "feedback": [..]}) in plaats van HTML — dat werd dan als
    artikeltekst opgeslagen en kostte in één ronde ~85% van de inhoud. De enige
    controle was `len(out) > 50`, waar een JSON-blob probleemloos doorheen komt.
    """
    t = (out or "").strip()
    if len(t) < 200 or t.startswith("{") or t.startswith("["):
        return False
    if not re.search(r"<(p|h[1-6]|ul|ol|section)\b", t, re.I):
        return False
    if re.search(r'"(score|verdict|feedback)"\s*:', t):
        return False
    # Een verbeterronde mag inkorten, maar niet halveren: dat is inhoudsverlies,
    # geen redactie.
    orig_words = len(re.sub(r"<[^>]+>", " ", original or "").split())
    new_words = len(re.sub(r"<[^>]+>", " ", t).split())
    if orig_words and new_words < orig_words * 0.6:
        return False
    # Andersom plakt het model soms een tweede exemplaar van de staart eronder
    # (stappenplan + FAQ dubbel). De reviewer ziet dat niet betrouwbaar en gaf
    # zo'n artikel gewoon een 82 — het zou dus dubbel op de site belanden.
    if _duplicate_headings(t) > _duplicate_headings(original or ""):
        return False
    return True


def _duplicate_headings(html: str) -> int:
    """Aantal koppen dat meer dan één keer voorkomt (genormaliseerd)."""
    heads = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).strip().lower()
        for h in re.findall(r"<h[2-3][^>]*>([\s\S]*?)</h[2-3]>", html or "", re.I)
    ]
    heads = [h for h in heads if h]
    return len(heads) - len(set(heads))


async def _optimize_article(site: Dict, keyword: str, html_body: str, feedback: str) -> str:
    # Bewust NIET het volledige SEO-Editor-profiel: dat eindigt met "ANTWOORD
    # UITSLUITEND met één JSON-object {score, verdict, feedback}". Die harde
    # instructie won het van de toegevoegde "lever HTML"-zin, waardoor het model
    # een beoordeling terugstuurde die als artikel werd weggeschreven. We nemen
    # alleen de rubriek over, tot aan het antwoordformaat.
    editor = _profile_prompt("SEO Editor") or _FALLBACK_REVIEW_PROMPT
    rubric = re.split(r"\n\s*ANTWOORD UITSLUITEND", editor)[0].strip()
    optimize_system = (
        "Je bent een Nederlandse SEO-eindredacteur die artikelen herschrijft. Je "
        "levert ALTIJD een volledige HTML-body en NOOIT een beoordeling of JSON.\n\n"
        "Je hanteert deze kwaliteitsrubriek:\n" + rubric
    )
    prompt = (
        f"Herschrijf dit artikel voor {site['name']} zodat het de onderstaande feedback "
        f"verwerkt. Behoud toon, stijl, lengte en alle bestaande links.\n\n"
        f"Feedback:\n{feedback}\n\n"
        f"Kernzoekwoord: {keyword}\n\nORIGINEEL:\n{html_body}\n\n"
        "Lever ALLEEN de verbeterde HTML-body zonder <html>/<head>/<body>. "
        "Geen JSON, geen scores, geen toelichting."
    )
    out = await _llm(optimize_system, prompt, max_tokens=16000)
    # Het model verpakt de body soms in een ```html-fence; dat is een prima
    # herschrijving in een verkeerd jasje — uitpakken i.p.v. de ronde weggooien.
    out = re.sub(r"^\s*```(?:html)?\s*|\s*```\s*$", "", out or "", flags=re.I)
    if not _looks_like_article(out, html_body):
        logger.warning(
            "[content-pipeline] Optimalisatieronde leverde geen bruikbaar artikel "
            "(%d tekens, begint met %r) — originele versie behouden.",
            len(out or ""), (out or "")[:40],
        )
        return html_body
    return out


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
    # Content-leerlus: gemeten vorm-lessen mee in de merkcontext, zodat ook de
    # meertraps-schrijver (outline → secties) ze vanaf de eerste stap toepast.
    learned = _learned_writing_lessons()
    if learned:
        brand_context = (brand_context + "\n\n## Gemeten vorm-lessen (pas toe waar passend)\n"
                         + learned).strip()
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
        # Ook de terugval krijgt het bewijs-oordeel: zonder die sleutel is
        # "geen eigen bewijs" niet te onderscheiden van "niet gemeten", en dan
        # telt de invariant `artikel_zonder_eigen_bewijs` juist de artikelen
        # weg die er het slechtst aan toe zijn (de single-shot-schrijver krijgt
        # de casestudy namelijk helemaal niet mee).
        return html_body, {
            "staged": False,
            "fallback_reason": str(e)[:200],
            "eigen_bewijs": article_writer.check_own_evidence(
                html_body, None,
                site_has_case_studies=bool(
                    knowledge_service.list_case_studies(site["id"], status="active"))),
        }, ""


#: Een titel is een kop, geen alinea. Boven deze lengte is het bijna altijd een
#: fallback-tekst die per ongeluk als titel is doorgegeven.
_TITLE_MAX_LEN = 90


def _clean_title(raw: str) -> str:
    """Maak van willekeurige tekst een bruikbare titel: HTML eruit, witruimte
    genormaliseerd, en nooit langer dan één kop.

    Waarom: de fallback van `_extract_title` was het `angle`-veld, en dat is bij
    de Radar/trend-brug een hele alinea ("In tegenstelling tot het advies om
    ..."). Die belandde ongefilterd als titel én als slug in de wachtrij, en
    daarna in het Actiecentrum. Een alinea knippen we daarom terug tot de eerste
    zin, en anders hard af op woordgrens.
    """
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"\s+", " ", text).strip().strip('"“”')
    if len(text) <= _TITLE_MAX_LEN:
        return text
    first = re.split(r"(?<=[.!?])\s", text)[0].strip()
    if first and len(first) <= _TITLE_MAX_LEN:
        return first
    cut = text[:_TITLE_MAX_LEN].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut or text[:_TITLE_MAX_LEN]


def _extract_title(html_body: str, fallback: str) -> str:
    """Titel uit de kop van het artikel; pas daarna de fallback. Beide worden
    door `_clean_title` gehaald — een alinea mag nooit als titel doorglippen."""
    for tag in ("h1", "h2"):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html_body, re.IGNORECASE | re.DOTALL)
        if m:
            title = _clean_title(m.group(1))
            if title:
                return title
    return _clean_title(fallback)


# ── Social copy + afbeelding ─────────────────────────────────────────────────

# Kwaliteitsgrens voor het social-pack, analoog aan de artikel-gate: onder deze
# score krijgt de schrijver één verbeterronde met de reviewer-feedback.
_SOCIAL_MIN_SCORE = 80

_SOCIAL_PLATFORM_RULES = (
    "Je schrijft een wereldklasse social-pack voor één blogartikel: vier platformen, "
    "elk volgens de wetten van dát platform. Algemene eisen (alle platformen):\n"
    "- De eerste regel is een scroll-stopper: een prikkelende vraag, een verrassend "
    "inzicht of een herkenbaar pijnpunt uit het artikel. NOOIT de artikeltitel "
    "letterlijk als opener.\n"
    "- Concreet en menselijk (B1-niveau). Geen AI-clichés ('in de wereld van vandaag', "
    "'ontdek', 'naadloos', 'game-changer', 'duik in'), geen verzonnen cijfers — gebruik "
    "alleen wat écht in het artikel staat.\n"
    "- Sluit af met een duidelijke call-to-action richting het artikel. Plak NOOIT zelf "
    "een URL in de tekst — de link wordt bij het posten automatisch toegevoegd.\n"
    "- Hashtags: Nederlands en specifiek voor het onderwerp; nooit generiek of spammy "
    "(#succes, #blessed, #motivatie).\n\n"
    "Per platform:\n"
    "- linkedin (600-1300 tekens): professioneel maar persoonlijk. Korte alinea's met "
    "witregels; werk één kerninzicht uit het artikel uit (verklap niet alles); eindig "
    "met een vraag aan de lezer of een CTA; precies 3-5 hashtags op de laatste regel.\n"
    "- facebook (250-600 tekens): conversationeel en warm, alsof je het een bekende "
    "vertelt; hooguit 1-2 emoji's; CTA om verder te lezen; 1-3 hashtags.\n"
    "- instagram (400-1000 tekens): visueel geschreven caption met korte regels en "
    "witruimte; 2-4 passende emoji's; CTA 'link in bio'; 5-8 hashtags op de laatste "
    "regel (mix van brede en niche-tags).\n"
    "- twitter (max 250 tekens INCLUSIEF hashtags): één scherpe uitspraak of vraag uit "
    "het artikel; 1-2 hashtags; geen emoji-rijen.\n\n"
    "Antwoord UITSLUITEND met JSON: "
    '{"linkedin": "...", "facebook": "...", "instagram": "...", "twitter": "..."}'
)

_SOCIAL_REVIEW_SYSTEM = (
    "Je bent een strenge Nederlandse social-media-eindredacteur. Beoordeel het "
    "social-pack (vier platform-teksten bij één blogartikel) op: (1) scroll-stoppende "
    "eerste regel per platform, (2) platform-fit (toon, lengte, opmaak), (3) kwaliteit "
    "en specificiteit van de hashtags, (4) heldere CTA zonder geplakte URL, (5) geen "
    "AI-clichés of verzonnen feiten, (6) klopt inhoudelijk met het artikel. "
    "Wees eerlijk hard: middelmaat is een onvoldoende. Antwoord UITSLUITEND met JSON: "
    '{"score": <0-100>, "feedback": "concrete verbeterpunten per platform, kort"}'
)


def _derive_hashtags(keyword: str, site_name: str = "", max_tags: int = 3) -> str:
    """Deterministische hashtags uit het zoekwoord — vangnet als de LLM ze vergeet."""
    tags: List[str] = []
    words = [w for w in re.split(r"[^\w]+", keyword or "") if len(w) > 2]
    if words:
        tags.append("#" + "".join(w.capitalize() for w in words[:3]))
        if len(words) > 1:
            tags.append("#" + words[0].capitalize())
    if site_name:
        tags.append("#" + re.sub(r"[^\w]+", "", site_name.title()))
    seen: set = set()
    uniq = [t for t in tags if len(t) > 3 and not (t.lower() in seen or seen.add(t.lower()))]
    return " ".join(uniq[:max_tags])


def _polish_social_pack(pack: Dict[str, str], keyword: str, site_name: str) -> Dict[str, str]:
    """Harde validatie na de LLM: lengtes afdwingen, hashtags garanderen,
    rommel (fences, aanhalingstekens, geplakte URL's) opruimen."""
    out: Dict[str, str] = {}
    for platform, text in pack.items():
        t = (text or "").strip()
        t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t).strip().strip('"').strip()
        # De post-functies plakken de artikel-URL zelf achter de tekst — een
        # door de LLM verzonnen/geplakte URL zou dubbel of fout linken.
        t = re.sub(r"https?://\S+", "", t).strip()
        if not t:
            out[platform] = ""
            continue
        if "#" not in t:
            tags = _derive_hashtags(keyword, site_name)
            if tags:
                t = f"{t}\n\n{tags}" if platform != "twitter" else f"{t} {tags.split()[0]}"
        if platform == "twitter" and len(t) > 270:
            # Knip op woordgrens maar bewaar de hashtags aan het eind.
            tags = " ".join(w for w in t.split() if w.startswith("#"))
            body = " ".join(w for w in t.split() if not w.startswith("#"))
            room = 270 - (len(tags) + 1 if tags else 0)
            body = body[:room].rsplit(" ", 1)[0].rstrip(".,;: ")
            t = f"{body} {tags}".strip() if tags else body
        out[platform] = t
    return out


def _fallback_social_copy(title: str, keyword: str, site_name: str) -> Dict[str, str]:
    """Laatste redmiddel als élke LLM-poging faalt: nette, complete captions
    i.p.v. de kale titel (de oude terugval postte letterlijk alleen de titel)."""
    tags = _derive_hashtags(keyword, site_name)
    body = (f"Nieuw op de blog: {title}.\n\n"
            f"We zetten de belangrijkste inzichten over {keyword} op een rij — "
            "praktisch en zonder omwegen. Lees het volledige artikel via de link.")
    insta = (f"Nieuw artikel over {keyword} 📖\n\n{title}\n\n"
             f"Link in bio!\n\n{tags}")
    tweet = f"Nieuw: {title}"[:230] + (f" {tags.split()[0]}" if tags else "")
    return _polish_social_pack(
        {"linkedin": f"{body}\n\n{tags}", "facebook": f"{body}\n\n{tags}",
         "instagram": insta, "twitter": tweet},
        keyword, site_name)


def _parse_social_json(raw: str) -> Optional[Dict[str, str]]:
    try:
        obj = json.loads(_extract_json(raw))
        pack = {p: str(obj.get(p) or "").strip()
                for p in ("linkedin", "facebook", "instagram", "twitter")}
        return pack if any(pack.values()) else None
    except Exception:
        return None


async def _generate_social_copy(site: Dict, title: str, keyword: str, html_body: str) -> Dict[str, str]:
    """Wereldklasse social-pack: per-platform regels + kennisbank-principes,
    daarna een eigen review-ronde (score < grens → één verbeterronde met de
    feedback), en tot slot harde validatie op lengtes/hashtags/URL's."""
    system = (_profile_prompt("Social Media Copywriter") or
              "Je bent een Nederlandse social-media-copywriter van topniveau.")
    system += "\n\n" + _SOCIAL_PLATFORM_RULES
    vault_context = _vault_context(site["name"])
    if vault_context:
        system += f"\n\n## Merkcontext uit Obsidian vault\n{vault_context[:2500]}"
    iris_guidance = _iris_writing_guidance(site["name"])
    if iris_guidance:
        system += f"\n\n## Kennisbank-principes (Iris — pas toe)\n{iris_guidance[:1500]}"

    plain = re.sub(r"<[^>]+>", " ", html_body)
    plain = re.sub(r"\s+", " ", plain).strip()[:3000]
    prompt = (
        f"Titel: {title}\nKernzoekwoord: {keyword}\n\nArtikel (platte tekst):\n{plain}"
    )

    pack: Optional[Dict[str, str]] = None
    for attempt in range(2):
        raw = await _llm(system, prompt, max_tokens=2500)
        pack = _parse_social_json(raw)
        if pack:
            break
        logger.warning("Social-copy JSON-parse mislukt (poging %d/2)", attempt + 1)
    if not pack:
        logger.warning("Social-copy: alle schrijfpogingen faalden — deterministische terugval")
        return _fallback_social_copy(title, keyword, site["name"])

    # Review-ronde naar het patroon van de artikel-gate: één eerlijke score,
    # onder de grens één herschrijf met de concrete feedback. Faalt de review
    # zelf (parse/LLM), dan accepteren we het pack — de harde validatie
    # hieronder vangt de ergste gebreken alsnog af.
    try:
        review_raw = await _llm(
            _SOCIAL_REVIEW_SYSTEM,
            f"Artikel-titel: {title}\nKernzoekwoord: {keyword}\n\n"
            f"Artikel (platte tekst, ingekort):\n{plain[:1500]}\n\n"
            f"Social-pack:\n{json.dumps(pack, ensure_ascii=False)}",
            max_tokens=800)
        review = json.loads(_extract_json(review_raw))
        score = int(review.get("score") or 0)
        feedback = str(review.get("feedback") or "").strip()
        if score < _SOCIAL_MIN_SCORE and feedback:
            logger.info("Social-pack scoorde %d/<%d — één verbeterronde", score, _SOCIAL_MIN_SCORE)
            raw = await _llm(
                system,
                prompt + "\n\nJe eerdere versie scoorde onvoldoende. Verwerk deze "
                f"eindredacteur-feedback volledig:\n{feedback}\n\n"
                f"Eerdere versie:\n{json.dumps(pack, ensure_ascii=False)}",
                max_tokens=2500)
            improved = _parse_social_json(raw)
            if improved:
                pack = improved
    except Exception as e:
        logger.warning("Social-pack review-ronde overgeslagen: %s", str(e)[:150])

    return _polish_social_pack(pack, keyword, site["name"])


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

# Statussen waarin een job "bezet" is: hij hangt in de wachtrij, staat live, of
# wacht op een herpublicatie. Alleen 'rejected' en 'stuck' geven een zoekwoord
# weer vrij — die zijn bewust afgeschreven.
_JOB_ACTIVE_STATUSES = (
    "pending_review", "needs_work", "published", "approved", "publish_failed",
)


def _keyword_key(keyword: str) -> str:
    """Normaliseer een zoekwoord voor dedupe.

    GSC levert queries zoals de gebruiker ze typt: met vraagteken, hoofdletters
    en dubbele spaties. 'Beste partners voor AI-oplossingen …?' en 'beste
    partners voor ai-oplossingen …' zijn hetzelfde zoekwoord, en twee artikelen
    daarvoor kannibaliseren elkaar.
    """
    return " ".join((keyword or "").lower().replace("?", " ").split())


def create_job(site_id: str, title: str, keyword: str, rationale: str, blog_html: str,
                seo_score: float, social_copy: Dict[str, str], image_bytes: Optional[bytes],
                slug: str, status: str = "pending_review",
                qc_report: Optional[Dict] = None, case_study_id: str = "",
                infographic_bytes: Optional[bytes] = None,
                dedupe: bool = True) -> str:
    """status 'pending_review' = klaar om goed te keuren (score ≥ gate);
    'needs_work' = onder de kwaliteitsgate — eerst verbeteren of afwijzen.

    dedupe=True (default): als er al een job voor deze site bestaat met dezelfde
    slug óf hetzelfde zoekwoord (status in _JOB_ACTIVE_STATUSES) wordt die
    bijgewerkt in plaats van een nieuwe rij aangemaakt. Voorkomt dat een
    content-goal in een oneindige loop hetzelfde artikel tientallen keren in de
    wachtrij dumpt (zie de 17x 'gelukkige hond'-incident).

    Waarom óók op zoekwoord en niet alleen op slug (23 jul 2026): voor het
    zoekwoord 'beste partners voor AI-oplossingen in het sociale domein in
    Nederland?' stond één opportunity, maar liepen twee jobs met verschillende
    titels — '9 beste partners voor AI-oplossingen …' en 'Zeven AI-partners die
    bewezen hebben …'. Verschillende titel = verschillende slug, dus de
    slug-dedupe liet ze allebei door en beide zijn op weareimpact.nl live
    gegaan; ze kannibaliseren elkaar nu op hetzelfde zoekwoord. select_topic zet
    een kans op 'in_progress' en dekt daarmee alleen zijn eigen route af — Iris'
    content_run en de goal-publisher komen daar niet langs. create_job is de
    enige trechter die álle routes passeren, dus hoort de controle hier."""
    import base64
    kw_key = _keyword_key(keyword)
    with get_conn() as conn:
        if dedupe:
            placeholders = ",".join("?" * len(_JOB_ACTIVE_STATUSES))
            existing = conn.execute(
                f"SELECT id, status FROM content_jobs "
                f"WHERE site_id=? AND slug=? AND status IN ({placeholders}) "
                f"ORDER BY created_at DESC LIMIT 1",
                (site_id, slug, *_JOB_ACTIVE_STATUSES),
            ).fetchone()
            if not existing and kw_key:
                # Zelfde zoekwoord, andere titel: een herschrijving of een
                # tweede route die hetzelfde onderwerp oppakt. Werk de bestaande
                # rij bij (inclusief de nieuwe slug) i.p.v. een tweede artikel
                # naast het eerste te zetten. De vergelijking gebeurt in Python
                # met _keyword_key, zodat het normaliseren op één plek staat —
                # in SQL nabouwen (LOWER/TRIM/REPLACE) vangt bijvoorbeeld drie
                # opeenvolgende spaties niet en zou stil weer duplicaten laten
                # doorglippen.
                for row in conn.execute(
                    f"SELECT id, status, keyword FROM content_jobs "
                    f"WHERE site_id=? AND status IN ({placeholders}) "
                    f"ORDER BY created_at DESC",
                    (site_id, *_JOB_ACTIVE_STATUSES),
                ):
                    if _keyword_key(row["keyword"]) == kw_key:
                        existing = row
                        logger.info(
                            "[content-pipeline] Zoekwoord-dedupe: '%s' hoort bij "
                            "bestaande job %s — bijgewerkt i.p.v. tweede artikel.",
                            keyword, row["id"],
                        )
                        break
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
                demand_quality.invalidate(site_id)
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
    # De kwaliteitsgate van de Kansen-lijst vergelijkt tegen content_jobs; een
    # verse job moet meteen meetellen, anders biedt het paneel het zoekwoord dat
    # we zojuist zijn gaan schrijven nog vijf minuten als "nieuw" aan.
    demand_quality.invalidate(site_id)
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
        topic = select_topic(site) or _bijvullen_en_opnieuw_kiezen(site)
        if not topic:
            _log_activity(site["name"], "auto-content-overslagen",
                          "Geen nieuwe kansen — ook een verse Demand-scan leverde niets op. "
                          "Controleer de GSC-koppeling en het siteprofiel (cold-start vereist "
                          "een profiel van ≥40 tekens).")
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
    r"<!\s*--\s*META\s+title=\"([^\"]*)\"\s+description=\"([^\"]*)\"\s*--\s*>",
    re.IGNORECASE | re.DOTALL,
)
# HTML-commentaar dubbele-puntvorm: <!-- Meta-titel: tekst --> / <!-- Meta-description: tekst -->
_META_COMMENT_COLON_RE = re.compile(
    r"<!\s*--\s*meta[- ]?(titel|title|beschrijving|description)\s*:\s*(.*?)\s*--\s*>",
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


# Titel-voorvoegsels waarmee een agent een intern werkstuk aflevert: een plan,
# rapport of tussenstap voor Vincent — nooit een artikel voor bezoekers.
_INTERNAL_TITLE_PREFIXES = (
    "plan:", "plan ", "rapport:", "rapport ", "analyse:", "eindredactie:",
    "status:", "statusrapport", "voorstel:", "notitie:", "concept:",
    "samenvatting:", "onderzoek:", "audit:", "evaluatie:", "checklist:",
    "todo", "to-do", "actieplan:", "stappenplan voor het team",
    "briefing:", "memo:", "verslag:", "logboek:", "backlog",
)

# Een agent-taaktitel is een opdracht ("Schrijf …", "Publiceer …"), geen
# artikeltitel. Een enkel werkwoord aan het begin is te zwak als bewijs — een
# blog mág "Schrijf je eigen liefdesbrief" heten. Blokkeren doen we pas bij een
# tweede signaal: een placeholder-verwijzing of vakjargon uit het eigen
# werkproces. Aanleiding: 'Schrijf meta-titel en -description voor pagina C'
# haalde 82/100, passeerde alle gates en stond op 23-07-2026 als blogartikel op
# steentjebijsteentje.nl (ontdekt 25-07-2026).
_TASK_TITLE_VERBS = (
    "schrijf", "publiceer", "optimaliseer", "selecteer", "monitor", "voeg",
    "voer", "exporteer", "controleer", "analyseer", "verzamel", "bepaal",
    "implementeer", "redigeer", "review", "update", "werk ", "stel ", "maak ",
)

# Placeholders uit een taakomschrijving: "pagina A", "artikel 2", "pagina X".
_PLACEHOLDER_REF = re.compile(r"\b(pagina|artikel|url|blog)\s+[a-z0-9]\b", re.IGNORECASE)

# Vakjargon dat in een taaktitel thuishoort, niet in een artikeltitel.
_TASK_TITLE_JARGON = (
    "meta-titel", "meta titel", "meta-description", "meta description",
    "interne links", "interne linkstructuur", "zoekposities", "sitemap",
    "gsc", "search console", "seo-score", "striking distance", "canonical",
)

# Test-artefacten. Een titel die zegt dat hij een test is, is er een — en die
# hoort niet op de site van een klant. 'Agent OS end-to-end publicatietest'
# stond tot 1 aug 2026 als publicatiepoging in de historie: hij haalde de
# kwaliteitsgate niet als bezwaar tegen (het is technisch prima proza) en er
# was geen enkele regel die zei dat het een proefrit was.
# Bewust specifieke samenstellingen: kaal "test" zou 'Test je kennis van…'
# tegenhouden, en dát is wél een artikel.
_TEST_ARTIFACT_MARKERS = (
    "publicatietest", "publicatie-test", "testartikel", "test artikel",
    "testpublicatie", "end-to-end", "end to end", "e2e", "smoke test",
    "lorem ipsum", "dummy-artikel", "dummy artikel", "agent os", "agentos",
)

# Redactionele werktitels: de versie-aanduiding waarmee een mens zijn eigen
# bestanden uit elkaar houdt. 'Klantcases overzichtspagina Ictusgo –
# Definitieve versie (geredigeerd & SEO-geoptimaliseerd)' is een bestandsnaam,
# geen kop die een bezoeker hoort te zien. Deze staan mídden in de titel, niet
# vooraan, dus de prefix-lijst hierboven ving ze niet.
_WORKING_TITLE_MARKERS = (
    "definitieve versie", "definitieve v", "def. versie", "herziene versie",
    "geredigeerd", "geredigeerde versie", "concept versie", "conceptversie",
    "(concept)", "[concept]", "eindversie", "laatste versie", "versie 2",
    "versie 3", "final version", "seo-geoptimaliseerd)",
)

# Zinsneden die verraden dat de tekst over het eigen werkproces gaat in plaats
# van over het onderwerp van de site.
_INTERNAL_BODY_MARKERS = (
    "agent os", "agentos", "wachtrij", "content_jobs", "seo-score",
    "deze goal", "deze taak", "de agent", "onze website aanpassen",
    "pagina's aanpassen", "implementatieplan", "sprint", "ticket",
)


def is_internal_document(title: str, html_body: str = "") -> Optional[str]:
    """Is dit een intern werkstuk (plan/rapport/tussenstap) in plaats van een
    artikel voor bezoekers? Retourneert de reden, of None als het publiceerbaar is.

    Waarom deterministisch en niet via de score: de kwaliteitsgate meet
    schrijfkwaliteit en de relevantie-gate meet of het onderwerp bij de site
    past. Een intern SEO-plan over de eigen site scoort op beide hoog — en
    stond daardoor op 14-07-2026 als 'blog' live op bijeen.app ("Plan: Directe
    antwoorden toevoegen aan alle 28 pagina's", score 85). Publiceerbaarheid is
    een aparte vraag en hoort een aparte, harde gate te zijn."""
    t = (title or "").strip().lower()
    for prefix in _INTERNAL_TITLE_PREFIXES:
        if t.startswith(prefix):
            return f"titel begint met '{prefix.strip()}' — dit is een intern werkstuk, geen artikel"

    marker = next((m for m in _TEST_ARTIFACT_MARKERS if m in t), None)
    if marker:
        return (f"titel bevat '{marker}' — dit is een test-artefact, "
                "geen artikel voor bezoekers")

    marker = next((m for m in _WORKING_TITLE_MARKERS if m in t), None)
    if marker:
        return (f"titel bevat '{marker}' — dit is een redactionele werktitel, "
                "geen kop voor bezoekers")

    # Agent-taaktitel: opdracht-werkwoord vooraan + een tweede signaal.
    if t.startswith(_TASK_TITLE_VERBS):
        if _PLACEHOLDER_REF.search(t):
            return ("titel is een agent-taak met een placeholder-verwijzing "
                    f"('{_PLACEHOLDER_REF.search(t).group(0)}') — geen artikel")
        jargon = next((j for j in _TASK_TITLE_JARGON if j in t), None)
        if jargon:
            return (f"titel is een agent-taak over '{jargon}' — een opdracht, "
                    "geen artikel voor bezoekers")

    text = re.sub(r"<[^>]+>", " ", html_body or "")
    text = re.sub(r"\s+", " ", text).lower()
    if text:
        hits = [m for m in _INTERNAL_BODY_MARKERS if m in text]
        # Eén losse hit kan toeval zijn (een artikel mág 'sprint' noemen);
        # drie of meer betekent dat de tekst over het eigen werkproces gaat.
        if len(hits) >= 3:
            return ("tekst gaat over het eigen werkproces "
                    f"({', '.join(hits[:3])}) — geen artikel voor bezoekers")
    return None


async def _verify_live(url: str) -> Optional[str]:
    """Haal de zojuist gepubliceerde URL op. Retourneert een foutreden als de
    pagina niet echt live staat, anders None.

    Waarom: het publish-endpoint van een project meldt 201 Created, maar dat
    zegt alleen dat de rij is aangemaakt — niet dat de pagina rendert. Op
    07-07-2026 gaf bijeen.app 201 voor 'Rapport: Status aanpassingen templates
    en one-pager' terwijl de URL 404 gaf; de job stond een week op 'published'
    zonder dat er iets online stond. Vertrouw de statuscode niet, kijk zelf."""
    if not url:
        return None
    # Een 404 vlák na publicatie is meestal GEEN publicatiefout maar bouw-/
    # ISR-vertraging: de rij bestaat, de statische pagina moet nog gerenderd
    # worden. Op 02-08-2026 leverde dat 13 valse 'publicatie_mislukt'-kaarten
    # voor ictusgo.nl op die twee dagen later allemaal HTTP 200 gaven. Daarom:
    # geef de deploy tijd (3 pogingen, oplopende wachttijd) vóór we een 404 of
    # 5xx als bewijs van mislukking accepteren.
    _RETRY_WACHT = (10, 30, 60)
    laatste_reden: Optional[str] = None
    for poging, wacht in enumerate(_RETRY_WACHT, start=1):
        reden = await _verify_live_once(url)
        if reden is None:
            return None
        laatste_reden = reden
        if poging < len(_RETRY_WACHT):
            logger.info("[content-pipeline] Live-controle poging %d faalde (%s) — "
                        "%ds wachten op deploy", poging, reden, wacht)
            await asyncio.sleep(wacht)
    return laatste_reden


async def _verify_live_once(url: str) -> Optional[str]:
    """Eén live-controle (zie _verify_live voor de retry-laag)."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            headers = {"User-Agent": "AgentOS-publish-check"}
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return f"live-controle: {url} gaf HTTP {resp.status_code}"

            # HTTP 200 is niet genoeg. Een single-page app serveert voor élke
            # onbekende route dezelfde schil met status 200 — een zachte 404.
            # Zo stond 'schrijf-meta-titel-en-description-voor-pagina-c' als
            # gepubliceerd én 'LIVE' in het logboek terwijl de URL alleen de
            # homepage-schil teruggaf (ontdekt 25-07-2026). Vergelijk daarom met
            # een URL die gegarandeerd niet bestaat: lijken de antwoorden op
            # elkaar, dan rendert het artikel niet.
            base, _, _ = url.rpartition("/")
            probe_url = f"{base}/agentos-bestaat-niet-{uuid.uuid4().hex[:12]}"
            try:
                probe = await client.get(probe_url, headers=headers)
            except Exception:
                return None  # Geen vergelijking mogelijk — geen verdachtmaking.
        if probe.status_code != 200:
            return None  # De site geeft nette 404's; de 200 is dus echt.
        a, b = len(resp.text), len(probe.text)
        if a and abs(a - b) <= max(200, a * 0.02):
            return (f"live-controle: {url} geeft dezelfde pagina als een "
                    "niet-bestaande URL — het artikel rendert niet")
        return None
    except Exception as e:
        # Een mislukte controle is geen bewijs van een mislukte publicatie —
        # meld het, maar verklaar de publicatie niet ten onrechte mislukt.
        logger.warning("[content-pipeline] Live-controle onbeslist voor %s: %s", url, e)
        return None


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

    # Publiceerbaarheidsgate, óók hier — niet alleen in approve_and_publish.
    # Aanleiding (23 jul 2026): 'Schrijf meta-titel & -description voor Pagina 2'
    # ging live op bewaardvoorjou.nl terwijl approve_and_publish deze titel al
    # blokkeerde. De publicatie kwam van scripts/republish_bewaard_404.py, een
    # eenmalig reparatiescript dat jobs met een ongeldige slug opnieuw uitrolde
    # en daarbij rechtstreeks HTTP deed — langs elke gate heen. Precies déze
    # titel had een ongeldige slug (een '&'), dus het script pikte hem eruit.
    # Een gate die alleen op de nette route staat, beschermt alleen de nette
    # route. Dit is de laagste functie in de codebase die daadwerkelijk naar een
    # site pusht; hier staat hij op de plek waar het gebeurt.
    intern = is_internal_document(title, html_body)
    if intern:
        logger.warning("[content-pipeline] Publicatie geweigerd voor '%s': %s",
                       title, intern)
        return {"success": False,
                "error": f"niet publiceerbaar: {intern}"}

    env_prefix = re.sub(r"[^A-Z0-9]", "", name.upper())
    publish_url = os.getenv(f"{env_prefix}_PUBLISH_URL", "").strip()
    publish_key = os.getenv(f"{env_prefix}_PUBLISH_KEY", "").strip()
    if not publish_url or not publish_key:
        return {"success": False,
                "error": f"Geen {env_prefix}_PUBLISH_URL/_PUBLISH_KEY — site-publicatie overgeslagen"}

    base_url = (site.get("base_url") or "").rstrip("/")
    # Code-fences eraf vóórdat we verder reinigen — anders belandt een
    # letterlijke ```html-fence (en de erdoor verschoven/dubbele koppen)
    # zichtbaar op de live pagina.
    html_body = _CODE_FENCE_RE.sub(r"\1", (html_body or "").strip())
    html_body = _unwrap_code_fence(html_body)
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
    meta_desc = parsed_desc or _derive_meta_desc(html_body)
    first_p = re.search(r"<p>(.*?)</p>", html_body or "", re.S)
    raw_excerpt = re.sub(r"<[^>]+>", "", first_p.group(1)).strip() if first_p else ""
    # Woordgrens-afkap zodat een excerpt nooit midden in een woord afbreekt
    # (voorkomt "... toepassen. E").
    excerpt = _smart_truncate(raw_excerpt, 200)

    # TeambuildingMetImpact draait op dezelfde /api/blog-route als Bijeen
    # (bewust "Bijeen-compatibel" gebouwd, zie scripts/import_teambuilding_blogs.py)
    # en verwacht dus hetzelfde schema: metaTitle/metaDescription/status i.p.v.
    # seoTitle/source. Zonder deze branch stuurde de content-wachtrij het
    # /api/blog/agent-os-schema (seoTitle) naar een /api/blog-endpoint dat
    # metaTitle als verplicht string-veld valideert — Zod zag het veld nooit
    # en wees élke publicatie af met "Invalid input: expected string,
    # received undefined" (21 jul 2026).
    if env_prefix in ("BIJEEN", "TEAMBUILDINGMETIMPACT"):
        payload = {
            "title": title,
            "content": (html_body or "").strip(),
            "excerpt": excerpt,
            "metaTitle": meta_title_for(parsed_title or title),
            "metaDescription": meta_desc,
            "tags": [keyword] if keyword else [],
            "status": "published",
        }
    else:
        payload = {
            "title": title,
            "content": (html_body or "").strip(),
            "slug": slug,
            "seoTitle": meta_title_for(parsed_title or title),
            "seoDescription": meta_desc,
            "tags": [keyword] if keyword else [],
            "source": "agent-os",
        }

    try:
        import httpx
        # Volg redirects HANDMATIG en stuur de Authorization-header op ELKE hop
        # opnieuw mee. httpx (net als requests) stript bij follow_redirects=True de
        # Authorization-header op een cross-host redirect (apex→www of www→apex) —
        # en Vercel-sites doen precies zo'n 307/308. Zonder dit arriveert de POST bij
        # de www-host zónder token → 401, komt het artikel niet live, en eindigt de
        # job als 'publish_failed'. (De weareimpact-schrijfflow doet dit al zo met
        # _post_follow; dit trekt de content-wachtrij-publisher gelijk, zodat ook een
        # apex-URL werkt i.p.v. stil te falen op de redirect.)
        def _post_follow(url: str, body: dict, key: str):
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            cur, resp = url, None
            for _ in range(5):
                resp = httpx.post(cur, json=body, headers=headers, timeout=90,
                                  follow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    cur = str(httpx.URL(cur).join(loc))
                    continue
                break
            return resp
        resp = await asyncio.to_thread(_post_follow, publish_url, payload, publish_key)
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


async def unpublish_from_project_site(site: Dict, slug: str, reason: str = "") -> Dict:
    """Haal een gepubliceerd artikel OFFLINE via {PREFIX}_PUBLISH_URL → /api/unpublish.

    Aanleiding (04-08-2026): twee artikelen met verzonnen bedrijfsnamen en
    verzonnen pilotprijzen over echt bestaande partijen stonden live. AgentOS
    kon ze afkeuren in de eigen database, maar niet van de site halen — de
    actiekaart zei letterlijk "AgentOS kan niet depubliceren, de pagina staat
    nog live". Een reputatierisico dat op de gebruiker werd afgeschoven.

    De site zet de post op status 'draft' (geen harde delete), zodat de tekst
    te repareren en te herpubliceren blijft. Retourneert altijd een dict,
    nooit een exception — depubliceren mag een opruimronde niet laten crashen.
    """
    import os
    name = site.get("name", "")
    if not (slug or "").strip():
        return {"success": False, "error": "geen slug opgegeven"}

    env_prefix = re.sub(r"[^A-Z0-9]", "", name.upper())
    publish_url = os.getenv(f"{env_prefix}_PUBLISH_URL", "").strip()
    publish_key = os.getenv(f"{env_prefix}_PUBLISH_KEY", "").strip()
    if not publish_url or not publish_key:
        return {"success": False,
                "error": f"Geen {env_prefix}_PUBLISH_URL/_PUBLISH_KEY — depubliceren niet mogelijk"}

    # /api/publish → /api/unpublish op dezelfde host.
    unpublish_url = re.sub(r"/api/publish/?$", "/api/unpublish", publish_url)
    if unpublish_url == publish_url:
        unpublish_url = publish_url.rstrip("/") + "/../unpublish"

    try:
        import httpx

        def _post_follow(url: str, body: dict, key: str):
            # Zelfde redirect-afhandeling als de publisher: httpx stript de
            # Authorization-header op een cross-host redirect (apex→www).
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            cur, resp = url, None
            for _ in range(5):
                resp = httpx.post(cur, json=body, headers=headers, timeout=60,
                                  follow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    cur = str(httpx.URL(cur).join(loc))
                    continue
                break
            return resp

        resp = await asyncio.to_thread(
            _post_follow, unpublish_url, {"slug": slug, "reason": reason}, publish_key)
        if resp.status_code in (200, 201):
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass
            _log_activity(name, "offline",
                          f"'{slug}' offline gehaald{(' — ' + reason) if reason else ''}")
            return {"success": True, "slug": slug, "response": data}
        return {"success": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        logger.warning("Depubliceren van '%s' mislukt: %s", slug, str(e)[:200])
        return {"success": False, "error": str(e)[:200]}


def _export_for_manual_publish(site: Dict, title: str, html_body: str,
                                keyword: str, slug: str, seo_score: int) -> Dict:
    """Voor sites zónder publish-API (`sites.manual_publish`, bv. LiefdeVoorIedereen —
    content daar gaat via een Prisma-admin-sessie op datingsite2026, niet een
    publieke endpoint): schrijf het klaar-artikel als Markdown naar de vault
    i.p.v. te proberen op een {PREFIX}_PUBLISH_URL die nooit gaat bestaan.
    Vincent plakt het zelf over in de site-admin. Nooit een exception — net als
    `_publish_to_project_site` mag een mislukte export de rest van
    approve_and_publish niet blokkeren."""
    from ...shared.config import OBSIDIAN_VAULT_PATH
    from ..chat.obsidian import ObsidianService

    name = site.get("name", "")
    obs = ObsidianService(OBSIDIAN_VAULT_PATH)
    if not obs.is_configured:
        return {"success": False, "error": "Obsidian-vault niet geconfigureerd — export overgeslagen"}

    html_body = _CODE_FENCE_RE.sub(r"\1", (html_body or "").strip())
    html_body = _unwrap_code_fence(html_body)
    html_body, parsed_title, parsed_desc = _strip_meta_and_suggestions(html_body)
    html_body = _strip_duplicate_header(html_body)
    meta_desc = parsed_desc or _derive_meta_desc(html_body)

    front_matter = (
        "---\n"
        f"title: \"{(parsed_title or title).replace(chr(34), chr(39))}\"\n"
        f"slug: \"{slug}\"\n"
        f"meta_title: \"{meta_title_for(parsed_title or title).replace(chr(34), chr(39))}\"\n"
        f"meta_description: \"{meta_desc.replace(chr(34), chr(39))}\"\n"
        f"keyword: \"{keyword}\"\n"
        f"seo_score: {seo_score}\n"
        f"status: \"klaar voor handmatig publiceren\"\n"
        "---\n\n"
    )
    filename = f"{name}/Te-pushen/{slug}.md"
    try:
        path = obs.vault_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(front_matter + html_body, encoding="utf-8")
    except OSError as e:
        return {"success": False, "error": f"Vault-schrijffout: {e}"[:200]}

    vault_path = str(path)
    _log_activity(name, "klaar_voor_handmatig_publiceren",
                  f"'{title}' klaar voor handmatig publiceren — {filename}",
                  artifact=vault_path,
                  next_step=f"Kopieer '{filename}' naar de {name}-admin en publiceer daar zelf.")
    return {"success": True, "path": vault_path}


# ── Goedkeuren → publiceren + posten ────────────────────────────────────────

def publish_failure_reason(result: Optional[Dict]) -> str:
    """Leesbare oorzaak uit een publish_result. Wordt zowel bij het mislukken
    weggeschreven (`content_jobs.error`) als door het Actiecentrum gebruikt om
    oudere rijen alsnog te duiden — die hebben een lege `error`-kolom.

    Een kale "HTTP 404" zegt niets tegen een mens: bij het publish-endpoint
    betekent 404 vrijwel altijd dat dát endpoint niet (meer) bestaat, niet dat
    het artikel weg is. Die vertaling zit hier.
    """
    if not result:
        return ""
    raw = (result.get("live_check")
           or (result.get("site") or {}).get("error")
           or (result.get("netlify") or {}).get("error")
           or "")
    raw = re.sub(r"\s+", " ", str(raw)).strip()
    if not raw:
        return ""
    if raw.startswith("HTTP 404"):
        return ("Publicatie-endpoint van de site bestaat niet (HTTP 404) — "
                "controleer de publish-URL van dit project. " + raw)[:400]
    if raw.startswith("HTTP 5"):
        return ("De website gaf een serverfout bij publiceren — probeer opnieuw "
                "of controleer de site. " + raw)[:400]
    return raw[:400]



async def approve_and_publish(job_id: str,
                              social_channels: Optional[List[str]] = None) -> Dict:
    """Publiceer naar de website van de site (Netlify óf de per-project
    publish-endpoint), dien de sitemap in bij Google Search Console, en post
    naar elk platform waarvoor de site credentials heeft. Wordt uitsluitend
    getriggerd door een menselijke goedkeuring (nooit automatisch).

    `social_channels` is de per-artikel-keuze van de reviewer en is bewust
    opt-in: None of een lege lijst = alleen de website (geen social, ook geen
    Content Multiplier). Social gebeurt dus nooit vanzelf — voor géén enkel
    project. Alleen een expliciete lijst (bv. ["linkedin"]) post naar die
    platformen.

    Een falend social-platform (bv. LinkedIn in 'Review in progress') blokkeert
    de website-publicatie nooit — het wordt als mislukt genoteerd en overgeslagen.
    """
    job = get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    # 'publish_failed' is óók geldig: dat is een job die de review al passeerde en
    # alleen op de publicatie-stap struikelde (bv. een 404-endpoint). De
    # Actiecentrum-knop "Opnieuw publiceren" biedt exact die jobs aan; zonder
    # 'publish_failed' hier gaf die knop een harde 400. De kwaliteits- en
    # publiceerbaarheidsgates hieronder blijven onverkort gelden.
    if job["status"] not in ("pending_review", "publish_failed"):
        raise ValueError(
            f"Job heeft status '{job['status']}', niet 'pending_review' of 'publish_failed'.")

    # Harde kwaliteitsgate: onder de grens wordt er níet gepubliceerd —
    # ook niet met een handmatige goedkeuring. Eerst verbeteren (regenerate).
    from ...shared.config import CONTENT_MIN_SCORE
    if int(job.get("seo_score") or 0) < CONTENT_MIN_SCORE:
        raise ValueError(
            f"SEO-score {job.get('seo_score')}/100 ligt onder de kwaliteitsgrens "
            f"({CONTENT_MIN_SCORE}) — laat de agent het artikel eerst verbeteren of wijs het af."
        )

    # Publiceerbaarheidsgate: een intern plan/rapport gaat nooit live, hoe hoog
    # de SEO-score ook is (zie is_internal_document).
    intern = is_internal_document(job["title"], job.get("blog_html") or "")
    if intern:
        raise ValueError(
            f"'{job['title']}' is niet publiceerbaar: {intern}. "
            "Wijs het af of herschrijf het als artikel voor bezoekers."
        )

    site = sites_service.get_site(job["site_id"])
    if not site:
        raise ValueError("Site niet gevonden.")

    # Alleen-website-sites (bv. Daar): wél publiceren + zoekmachine-indiening,
    # géén social-fan-out en géén Content Multiplier. Handmatige sites (export
    # naar de vault, geen live URL) horen hier hetzelfde te doen — een social-
    # post of multiplier-video die naar een vault-pad linkt is zinloos.
    website_only = bool(site.get("website_only")) or bool(site.get("manual_publish"))

    social_copy = json.loads(job["social_copy"] or "{}")
    import base64
    image_bytes = base64.b64decode(job["image_path"]) if job.get("image_path") else None
    infographic_bytes = (base64.b64decode(job["infographic_path"])
                         if job.get("infographic_path") else None)

    result: Dict = {"netlify": None, "gsc": None, "social": {}}
    article_url = None
    image_url = None
    # De URL die de publish-route zélf teruggaf — het enige adres waarvan we
    # weten dát het de gepubliceerde pagina is. article_url kan verderop
    # terugvallen op een gegokt /blog/<slug>-adres; dat gokje mag nooit de
    # live-controle voeden (een 404 zou dan een geslaagde publicatie afkeuren).
    published_url = None
    base_url = (site.get("base_url") or "").rstrip("/")

    # ── Website-publicatie ───────────────────────────────────────────────────
    # Drie routes: Netlify-sites (publish_api_url gevuld MAAR zónder http-prefix
    # — het is een Netlify site-ID), project-sites met een eigen
    # {PROJECT}_PUBLISH_URL/_PUBLISH_KEY (bv. bijeen.app, ictusgo.nl — hun
    # publish_api_url is wél een volledige https-URL), en handmatige sites
    # (sites.manual_publish, bv. LiefdeVoorIedereen — content gaat via een
    # Prisma-admin-sessie, geen publieke publish-endpoint).
    pub_url = (site.get("publish_api_url") or "").strip()
    if pub_url and not pub_url.lower().startswith("http"):
        # Netlify site-ID → zip-deploy naar Netlify.
        try:
            netlify_result = await publish_service.publish_article(
                site_id=site["id"], title=job["title"], html_body=job["blog_html"],
                slug=job["slug"], image_bytes=image_bytes,
                infographic_bytes=infographic_bytes,
            )
            result["netlify"] = netlify_result
            article_url = netlify_result.get("url")
            published_url = article_url
            image_url = netlify_result.get("image_url")
        except Exception as e:
            result["netlify"] = {"error": str(e)[:300]}
    elif pub_url:
        # Echte publish-URL (https://...) → project-site endpoint.
        try:
            site_result = await _publish_to_project_site(
                site, job["title"], job["blog_html"], job["keyword"],
                job["slug"], int(job.get("seo_score") or 0))
            result["site"] = site_result
            if site_result.get("url"):
                article_url = site_result["url"]
                published_url = article_url
                image_url = site_result.get("image_url")
        except Exception as e:
            result["site"] = {"success": False, "error": str(e)[:300]}
    elif site.get("manual_publish"):
        site_result = _export_for_manual_publish(
            site, job["title"], job["blog_html"], job["keyword"],
            job["slug"], int(job.get("seo_score") or 0))
        result["site"] = site_result
        if site_result.get("path"):
            article_url = site_result["path"]
    else:
        # Project-site via de per-project publish-endpoint (nooit een crash).
        try:
            site_result = await _publish_to_project_site(
                site, job["title"], job["blog_html"], job["keyword"],
                job["slug"], int(job.get("seo_score") or 0))
            result["site"] = site_result
            if site_result.get("url"):
                article_url = site_result["url"]
                published_url = article_url
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

    # ── Bing: géén ping meer ─────────────────────────────────────────────────
    # Microsoft heeft het sitemap-ping-endpoint uitgezet, net als Google eerder
    # deed. Het antwoordt HTTP 410 (Gone). Dat is precies de faalmodus waar dit
    # bestand vol van staat: de call slaagde technisch, we schreven het
    # statuscijfer weg als resultaat, en 98 van de 102 publicaties droegen zo
    # een 'bing: 410' die als "ingediend bij de zoekmachine" gelezen werd
    # (4 aug 2026). Een dood endpoint aanroepen is geen indiening.
    #
    # Er is niets te vervangen: IndexNow hieronder dekt Bing (én Yandex, Seznam,
    # Naver) en is de route die Microsoft zélf aanwijst. De sleutel `bing`
    # verdwijnt daarom uit publish_result in plaats van op een vaste string te
    # blijven staan — een veld dat altijd hetzelfde zegt is ruis.

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

    # Opt-in: alleen wat de reviewer expliciet aanvinkte gaat naar social.
    chosen = {c.strip().lower() for c in (social_channels or [])}

    def _wants(platform: str) -> bool:
        return platform in chosen

    if website_only:
        # Bewust geen social voor deze site — alleen de website + zoekmachines.
        result["social"] = {"skipped": "website_only — social uitgeschakeld voor deze site"}
    elif not chosen:
        result["social"] = {"skipped": "geen social aangevinkt bij goedkeuren"}
    else:
        if social_copy.get("linkedin") and _wants("linkedin") and linkedin_service.is_configured(site_name):
            result["social"]["linkedin"] = await _post(
                "linkedin",
                linkedin_service.post_update(
                    social_copy["linkedin"], article_url=article_url, site_name=site_name))
        if social_copy.get("facebook") and _wants("facebook") and facebook_service.is_configured(site_name):
            result["social"]["facebook"] = await _post(
                "facebook",
                facebook_service.post_update(
                    social_copy["facebook"], article_url=article_url, site_name=site_name))
        if social_copy.get("instagram") and _wants("instagram") and instagram_service.is_configured(site_name):
            if image_url:
                result["social"]["instagram"] = await _post(
                    "instagram",
                    instagram_service.post_image(
                        image_url, social_copy["instagram"], site_name=site_name))
            else:
                result["social"]["instagram"] = {"success": False,
                    "error": "Geen publieke image-url (site publish geeft geen image_url)"}
        if social_copy.get("twitter") and _wants("twitter") and twitter_service.is_configured(site_name):
            result["social"]["twitter"] = await _post(
                "twitter",
                twitter_service.post_update(
                    social_copy["twitter"], article_url=article_url, site_name=site_name))

    # Status correct weerspiegelen: pas 'published' als de site-publicatie écht
    # gelukt is. Mislukt die (geen env, HTTP-fout, exception), zet dan
    # 'publish_failed' — anders staat de job op 'published' terwijl er niets
    # online staat (de oorspronkelijke IctusGo-bug: 7 jobs 'published' maar 0 live).
    # De twee routes rapporteren verschillend: de Netlify-route levert een dict
    # met een 'url' (of een 'error'), de project-route een expliciete 'success'.
    # Alleen op result["site"] kijken zette elke geslaagde Netlify-publicatie op
    # 'publish_failed' — die route vult de 'site'-sleutel namelijk nooit.
    if site.get("publish_api_url"):
        netlify = result.get("netlify") or {}
        site_ok = bool(netlify.get("url")) and not netlify.get("error")
    else:
        site_ok = bool((result.get("site") or {}).get("success"))

    # En vertrouw de statuscode niet: controleer dat de pagina écht rendert.
    if site_ok and published_url:
        reden = await _verify_live(published_url)
        if reden:
            site_ok = False
            result["live_check"] = reden
            logger.warning("[content-pipeline] %s", reden)

    # De reden hoort óók op de job zelf: het Actiecentrum leest `error` en toonde
    # anders "Onbekende fout" terwijl de echte oorzaak (HTTP 404/500 van het
    # publish-endpoint) alleen in publish_result verstopt zat.
    reden = "" if site_ok else publish_failure_reason(result)
    job_status = "published" if site_ok else "publish_failed"
    _update_job(job_id, status=job_status, publish_result=json.dumps(result),
                error=reden, reviewed_at=_now())
    if site_ok:
        _log_activity(site_name, "publicatie", f"'{job['title']}' goedgekeurd en gepubliceerd",
                      artifact=article_url or "")
    else:
        # status='error', want dit wacht op een mens: een artikel dat de
        # review-gate is gepasseerd en tóch niet live staat komt nergens anders
        # meer voorbij. Het stond hier tot 2 aug 2026 op de standaard 'ok' en
        # dan is de uitkomstkaart een logregel in plaats van een inbox-item —
        # Ictusgo's 404 kwam daardoor drie ochtenden terug als "les" in Iris'
        # briefing zonder één keer als beslissing op het scherm te staan.
        _log_activity(site_name, "publicatie_mislukt",
                      f"'{job['title']}' goedgekeurd maar NIET gepubliceerd: {reden}",
                      artifact="",
                      next_step=("Bekijk de fout in de Wachtrij en publiceer opnieuw; "
                                 "blijft het misgaan, dan zit het defect in de "
                                 "publicatieroute van deze site."),
                      status="error")

    # ── Content Multiplier: format-waaier als achtergrondtaak ────────────────
    # Uit één goedgekeurd artikel automatisch social-pack + video genereren.
    # Achter de review-gates (pending_review) — er wordt niets gepost. Als
    # create_task hier draait, leeft de taak op de uvicorn-event-loop door
    # nadat deze request al beantwoord is.
    # Alleen na een geslaagde publicatie: een social-pack en video die naar een
    # niet-bestaande pagina linken zijn waardeloos en kosten LLM-quota.
    from ...shared.config import CONTENT_MULTIPLIER_ENABLED
    if website_only:
        # Multiplier maakt social-pack + video — niet gewenst voor alleen-website-sites.
        result["multiplier"] = "overgeslagen (website_only)"
    elif not chosen:
        # De reviewer vinkte geen social aan — dan is een social-pack + video
        # genereren zinloos werk (en LLM-quota).
        result["multiplier"] = "overgeslagen (geen social aangevinkt)"
    elif CONTENT_MULTIPLIER_ENABLED and site_ok:
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
    # Stond dit artikel al live? Dan verandert 'rejected' alleen de rij, niet de
    # wereld: de pagina blijft staan en verdwijnt tegelijk uit élk overzicht,
    # want in de Wachtrij en de tellingen is hij netjes afgewezen. Zo stonden op
    # 2 aug 2026 negen pagina's live met een afgewezen job eronder, waaronder
    # 'Agent OS end-to-end publicatietest' op de site van een klant. De afwijzing
    # gaat gewoon door (dat is wat de mens bedoelt), maar het depubliceren is
    # werk dat blijft liggen — dus wordt het een beslissing in het Actiecentrum
    # in plaats van een stille statuswijziging.
    live_url = ""
    if job.get("status") == "published":
        try:
            # Twee vormen komen echt voor: het platte {"success", "url"} van de
            # directe publisher en het genestelde {"site": {...}} van de pipeline.
            data = json.loads(job.get("publish_result") or "{}") or {}
            live_url = data.get("url") or ""
            if not live_url and isinstance(data.get("site"), dict):
                live_url = data["site"].get("url") or ""
        except (ValueError, TypeError, AttributeError):
            live_url = ""

    _update_job(job_id, status="rejected", reviewed_at=_now())
    site = sites_service.get_site(job["site_id"])
    if not site:
        return
    if job.get("status") == "published":
        _log_activity(site["name"], "afgekeurd_maar_live",
                      f"'{job['title']}' is afgewezen maar stond al gepubliceerd"
                      + (f" op {live_url}" if live_url else ""),
                      artifact=live_url,
                      next_step="Haal deze pagina offline in het CMS en zet een 301 naar "
                                "een relevant artikel — afwijzen doet dat niet vanzelf.",
                      status="error")
        return
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
        continued_from_existing = True
    else:
        html_body, qc_report, case_study_id = await _write_article_best(
            site, job["keyword"], "", job["rationale"])
        continued_from_existing = False
    html_body, review = await review_and_improve(site, job["keyword"], html_body)

    from ...shared.config import CONTENT_MIN_SCORE
    passed = review["score"] >= CONTENT_MIN_SCORE
    if review["score"] <= 0:
        # Score 0 = de review kon helemaal niet draaien (LLM/quota down), geen
        # oordeel over het artikel. NIET als verbeter-poging tellen — anders
        # raakt een artikel 'stuck' door drie provider-storingen zonder dat er
        # één echte ronde is gedraaid (incident 2026-07-17, OpenModel-403-uren).
        logger.info("[content-pipeline] Regenerate zonder werkende review (score 0) "
                    "— bestaande versie en teller ongemoeid.")
        return job_id
    # Teller pas optellen als we écht een verbeter-cyclus hebben gedraaid; een
    # no-op (bestaande versie behouden) telt niet als nieuwe poging.
    new_attempts = attempts + (1 if review["score"] != old_score or review["score"] < CONTENT_MIN_SCORE else 0)
    if review["score"] < old_score and not continued_from_existing:
        # Nooit een slechtere versie terugschrijven dan er al stond. Deze
        # vergelijking geldt alleen voor een blanco herschrijving: die kan écht
        # slechter uitvallen dan wat er lag.
        #
        # Bij doorverbeteren mág hij niet gelden. `review_and_improve` beoordeelt
        # de bestaande tekst als eerste en geeft de béste versie terug, dus die
        # is per constructie al minstens zo goed als wat er stond. De opgeslagen
        # `old_score` komt bovendien uit een eerdere — soms andere — meting; de
        # reviewer varieert flink en scoorde tot juli 2026 stelselmatig lager
        # door een meta-aftrek die niet in het artikel zat. Vergelijken met dat
        # verouderde cijfer betekende dat het werk van elke ronde werd weggegooid
        # en alleen de pogingenteller opliep: het artikel bevroor op zijn oude
        # score tot het 'stuck' raakte, zonder dat er ooit iets werd opgeslagen.
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


async def save_manual_edit(job_id: str, html_body: str, force: bool = False) -> Dict:
    """Sla een handmatig (in Claude/Gemini of inline) bewerkte artikel-body terug
    op. De body wordt opnieuw door dezelfde kwaliteitsgate gehaald als automatische
    content; haalt die de grens, dan gaat de job naar 'pending_review' (klaar om te
    publiceren), anders blijft 'needs_work' staan en krijgt de mens de feedback terug.
    De herscoreslag is time-out-beschermd: als de LLM (Claude/Hermes) in quota-backoff
    hangt, wordt de body wél opgeslagen en krijgt de caller scored=False terug i.p.v.
    dat de request eeuwig blijft hangen.

    force=True: sla de LLM-score over en zet de job direct op 'pending_review'
    (handmatig beoordeeld door de mens — nuttig als de quota in backoff zit maar de
    redacteur zeker weet dat het artikel goed is).
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
    if force:
        scored = True  # force telt als "beoordeeld door mens"
        status = "pending_review"
        _update_job(job_id, seo_score=score, status=status)
        _log_activity(
            site.get("name", "?"), "content-handmatig-verbeterd",
            f"'{title}' handmatig aangepast en door jou vrijgegeven naar de Wachtrij (score overslagen).",
            artifact=job_id, status="ok",
        )
        return {"job_id": job_id, "score": score, "passed": True, "scored": True,
                "forced": True, "feedback": "Door jou vrijgegeven naar de Wachtrij (score overslagen).",
                "status": status}

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
                    "'Handmatig aanpassen' → 'Opslaan' om alsnog te scoren, of gebruik "
                    "'Toch naar Wachtrij' als je zeker weet dat het artikel goed is.")

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

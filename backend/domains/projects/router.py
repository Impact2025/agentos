"""
Projects API — leest projecten uit Obsidian vault (10_Projects/).
Als OBSIDIAN_VAULT_PATH niet gezet is, valt het terug op de projects/ map.

  GET /api/projects             → lijst alle projecten
  GET /api/projects/{name}      → SKILL.md + metadata van 1 project
"""

from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
import logging
import re, os
from typing import List, Dict, Optional

from ...shared.database import get_conn
from ...shared.projects import squash_project
from ..rituals.models import has_project_bridge_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

# Response-cache voor /advice: de dashboard-banner pollt dit elke 8s. De
# GSC-feiten zijn al gecached (600s), maar de rest van de advice-opbouw
# (goals, trends) kost bij elke call alsnog ~6s. Met een 60s response-TTL
# serveert de poll daarna <1ms en verdwijnt de laadvertraging.
_ADVICE_TTL_SECONDS = 60
_advice_cache: Dict[str, tuple] = {}

# Primary: lees uit Obsidian vault /10_Projects/
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "")
if VAULT_PATH:
    PROJECTS_DIR = Path(VAULT_PATH) / "10_Projects"
else:
    # Fallback: lees uit projects/ map (oude situatie)
    PROJECTS_DIR = Path(__file__).parent.parent.parent.parent / "projects"


def _parse_skill_md(path: Path) -> Dict:
    """Lees SKILL.md en parse de YAML frontmatter + body."""
    content = path.read_text("utf-8")
    frontmatter = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_fm = parts[1].strip()
            body = parts[2].strip()
            for line in raw_fm.split("\n"):
                line = line.strip()
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    frontmatter[key] = val

    return {
        "name": frontmatter.get("name", path.parent.name),
        "description": frontmatter.get("description", ""),
        "tags": [t.strip() for t in frontmatter.get("tags", "").split(",") if t.strip()],
        "body": body,
    }


def _find_project_dir(name: str) -> Optional[Path]:
    """Vind project directory case-insensitive in vault."""
    if not PROJECTS_DIR.exists():
        return None
    norm = lambda x: x.lower().replace("-", "").replace(" ", "")
    target = norm(name)
    for entry in PROJECTS_DIR.iterdir():
        if not entry.is_dir():
            continue
        if norm(entry.name) == target:
            return entry
    return None


#: content_jobs-statussen die "hier wordt al aan gewerkt" betekenen. Een job die
#: is afgewezen telt niet mee — dan mag het zoekwoord opnieuw voorgesteld worden.
_ACTIVE_JOB_STATUSES = ("pending_review", "needs_work", "stuck",
                        "publish_failed", "published")


def _pipeline_keywords(site_id: str) -> set:
    """Zoekwoorden die al in de contentmotor zitten (content_jobs).

    `_written_keywords` kijkt alleen in de vault naar wat áf en gepubliceerd is.
    Alles wat nog in de Wachtrij hangt of onder de kwaliteitsgate is blijven
    steken, is daar onzichtbaar — waardoor het dashboard hetzelfde zoekwoord
    bleef voorstellen terwijl er een artikel voor vaststond op `needs_work`
    (25 jul 2026). Deze bron dekt dat gat.
    """
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT keyword, title FROM content_jobs WHERE site_id = ? "
                f"AND status IN ({','.join('?' * len(_ACTIVE_JOB_STATUSES))})",
                (site_id, *_ACTIVE_JOB_STATUSES),
            ).fetchall()
    except Exception:
        return set()
    out = set()
    for r in rows:
        kw = (r["keyword"] or "").strip().lower()
        if kw:
            out.add(kw)
        # Ook de titel meenemen: goal-gestagede jobs hebben een leeg keyword-veld,
        # maar hun titel bevat het zoekwoord wel ("Jubileum cadeau ideeën die …").
        title = (r["title"] or "").strip().lower()
        if title:
            out.add(title)
    return out


#: Stopwoorden tellen niet mee bij het vergelijken van zoekwoorden.
_KEYWORD_STOPWORDS = {
    "de", "het", "een", "en", "of", "in", "op", "voor", "van", "met", "bij",
    "je", "jouw", "wat", "hoe", "waarom", "is", "zijn", "die", "dat",
}


def _keyword_already_covered(keyword: str, covered: set) -> bool:
    """True als dit zoekwoord al gedekt wordt door iets in de contentmotor.

    Exacte match, of voldoende overlap in kernwoorden: minstens twee gedeelde
    kernwoorden én minstens twee derde van de kernwoorden van het zoekwoord.
    Zonder die soepelheid werd 'origineel jubileum cadeau' opnieuw voorgesteld
    terwijl er al een job 'Jubileum cadeau ideeën die écht verbinden' liep; mét
    een losser criterium zou één gedeeld woord ('cadeau') hele onderwerpen
    onterecht wegdrukken.
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return False
    if kw in covered:
        return True
    kern = {w for w in re.findall(r"[a-z0-9]+", kw) if w not in _KEYWORD_STOPWORDS}
    if len(kern) < 2:
        return False
    for item in covered:
        woorden = set(re.findall(r"[a-z0-9]+", item))
        overlap = kern & woorden
        if len(overlap) >= 2 and len(overlap) * 3 >= len(kern) * 2:
            return True
    return False


def _written_keywords(name: str) -> set:
    """Keywords waarvoor al een artikel is geschreven, uit content/*.html|*.md frontmatter.
    Bestanden komen uit verschillende schrijf-pipelines met andere frontmatter-vorm:
    zowel een los 'keyword: "..."' veld als een lijst 'keywords: ["...", "..."]'.
    """
    project_dir = _find_project_dir(name)
    if not project_dir:
        return set()
    content_dir = project_dir / "content"
    if not content_dir.exists():
        return set()
    from .weareimpact import _split_frontmatter
    keywords = set()
    for f in list(content_dir.glob("*.html")) + list(content_dir.glob("*.md")):
        try:
            meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        single = meta.get("keyword", "").strip().lower()
        if single:
            keywords.add(single)
        multi = meta.get("keywords", "")
        for m in re.findall(r'"([^"]+)"', multi):
            keywords.add(m.strip().lower())
    return keywords


def _scan_projects() -> List[Dict]:
    """Scan de projects/ map en retourneer metadata per project."""
    if not PROJECTS_DIR.exists():
        return []

    projects = []
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name.startswith("_") or entry.name == "sjabloon":
            continue
        skill_path = entry / "SKILL.md"
        has_skill = skill_path.exists()
        content_files = (
            list(entry.glob("content/*.md")) +
            list(entry.glob("content/*.html")) +
            list(entry.glob("log/*.md")) +
            list(entry.glob("zzp-opdrachten/*.md")) +
            list(entry.glob("SEO/*.md"))
        )
        prospecting_runs = list(entry.glob("prospecting/run-*"))

        meta = {
            "name": entry.name,
            "has_skill": has_skill,
            "content_count": len(content_files),
            "prospecting_runs": len(prospecting_runs),
            # Fase 2 deel 2: toont de 'Rituelen'-tab in de frontend als dit project een
            # klant-bridge-token heeft (project_bridge_tokens, zie rituals/models.py).
            "has_client_bridge": has_project_bridge_token(squash_project(entry.name)),
        }
        if has_skill:
            meta["skill"] = _parse_skill_md(skill_path)
        projects.append(meta)

    return projects


@router.get("")
def list_projects() -> List[Dict]:
    return _scan_projects()


@router.get("/{name}")
def get_project(name: str) -> Dict:
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' niet gevonden in vault")

    # Zoek naar SKILL.md in de project map
    skill_path = project_dir / "SKILL.md"
    if not skill_path.exists():
        # Als er geen SKILL.md is, zoek naar een .md bestand met de projectnaam
        md_files = list(project_dir.glob("*.md"))
        if md_files:
            # Gebruik eerste .md als SKILL.md-equivalent
            return _parse_skill_md(md_files[0])
        return {
            "name": project_dir.name,
            "has_skill": False,
            "message": "Geen SKILL.md of project .md gevonden"
        }

    return _parse_skill_md(skill_path)


# ── Dashboard routes (hier in router.py om import-conflicten te voorkomen) ──

from datetime import date, datetime, timedelta, timezone
from ..seo import gsc, sites as sites_service

def _find_site(name: str):
    norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
    target = norm(name)
    sites = sites_service.list_sites()
    for s in sites:
        if norm(s["name"]) == target:
            return s
    # Tolerante match: een projectsleutel als "daarwebsite" of "daarsite" hoort
    # bij de site "Daar" (daar.nl). Strip een suffix en probeer ook de domein-root,
    # zodat het dashboard niet leeg blijft door een cosmetisch naamverschil.
    stripped = target
    for suf in ("website", "site"):
        if stripped.endswith(suf) and len(stripped) > len(suf) + 1:
            stripped = stripped[: -len(suf)]
            break
    for s in sites:
        if norm(s["name"]) == stripped:
            return s
        host = (s.get("base_url") or "").split("//")[-1].split("/")[0]
        root = norm(host.split(".")[0]) if host else ""
        if root and len(root) >= 3 and (root == target or root == stripped):
            return s
    return None


@router.get("/{name}/dashboard")
def project_dashboard(name: str, days: int = Query(28, ge=7, le=365)):
    site = _find_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")

    gsc_prop = site.get("gsc_property", "")
    if not gsc_prop or not gsc.is_configured():
        return {
            "site": {"name": site["name"], "url": site.get("base_url", ""), "gsc_property": gsc_prop},
            "error": "GSC niet geconfigureerd voor dit project",
            "summary": None,
        }

    try:
        pages = gsc.fetch_page_performance(gsc_prop, days=days, row_limit=1000)
        queries = gsc.fetch_query_performance(gsc_prop, days=days, row_limit=1000)
        prev_pages = gsc.fetch_page_performance(gsc_prop, days=days, row_limit=1000, end_offset=days)
    except Exception as e:
        return {
            "site": {"name": site["name"], "url": site.get("base_url", ""), "gsc_property": gsc_prop},
            "error": str(e)[:200],
            "summary": None,
        }

    cur_clicks = sum(p["clicks"] for p in pages)
    cur_imps = sum(p["impressions"] for p in pages)
    cur_ctr = round((cur_clicks / cur_imps * 100), 2) if cur_imps else 0
    cur_pos = round(sum(p["position"] * p["impressions"] for p in pages) / cur_imps, 1) if cur_imps else 0
    prev_clicks = sum(p["clicks"] for p in prev_pages)
    prev_imps = sum(p["impressions"] for p in prev_pages)
    prev_pos = round(sum(p["position"] * p["impressions"] for p in prev_pages) / prev_imps, 1) if prev_imps else 0

    by_url = {p["page"]: p for p in pages}
    prev_by = {p["page"]: p for p in prev_pages}
    comparison = []
    for url, cur in by_url.items():
        prv = prev_by.get(url)
        if prv:
            comparison.append({
                "page": url,
                "clicks_current": cur["clicks"], "clicks_prev": prv["clicks"],
                "clicks_change": cur["clicks"] - prv["clicks"],
                "position_current": cur["position"], "position_prev": prv["position"],
                "position_change": round(prv["position"] - cur["position"], 1),
            })
    comparison.sort(key=lambda x: abs(x["clicks_change"]), reverse=True)

    return {
        "site": {"name": site["name"], "url": site.get("base_url", ""), "gsc_property": gsc_prop},
        "period": {"days": days},
        "summary": {
            "indexed_pages": len(pages), "indexed_pages_prev": len(prev_pages),
            "indexed_pages_change": len(pages) - len(prev_pages),
            "total_clicks": cur_clicks, "total_clicks_prev": prev_clicks,
            "total_clicks_change": cur_clicks - prev_clicks,
            "total_impressions": cur_imps, "avg_ctr": cur_ctr,
            "avg_position": cur_pos, "avg_position_prev": prev_pos,
            "avg_position_change": round(prev_pos - cur_pos, 1),
        },
        "top_pages": sorted(pages, key=lambda x: x["clicks"], reverse=True)[:20],
        "top_queries": sorted(queries, key=lambda x: x["clicks"], reverse=True)[:20],
        "page_comparison": comparison[:20],
    }


# ── Project Advice (AI-vrij — op basis van data) ────────────────────


#: Doelstatussen die een alert NIET mogen dempen. 'partial' hoort er sinds
#: 25 jul 2026 bij: een doel waarvan de publisher-taak de échte actie niet kon
#: uitvoeren eindigt als 'partial'. Zo'n doel heeft rapporten opgeleverd maar
#: niets aan de site veranderd — dempen zou de alert 14 dagen wegdrukken op
#: bewijs van activiteit in plaats van bewijs van effect. Precies zo verdween
#: "gemiddelde positie 45.6" van het dashboard terwijl er niets was gebeurd.
_NON_DAMPENING_STATUSES = ("failed", "cancelled", "partial")


def _goal_addresses(goals: List[Dict], *phrases: str, days: int = 14) -> bool:
    """True als er de afgelopen `days` dagen al een doel is aangemaakt dat het
    onderwerp aantoonbaar heeft opgepakt (draait of volledig is afgerond) en
    waarvan objective/titel één van de zinsdelen bevat. Dempt cijfer-alerts:
    GSC-data loopt dagen achter, dus direct na 'Oplossen' zou dezelfde alert
    anders blijven terugkomen alsof er niets gebeurd is."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for g in goals:
        if g.get("status") in _NON_DAMPENING_STATUSES:
            continue
        haystack = ((g.get("objective") or "") + " " + (g.get("title") or "")).lower()
        if not any(p.lower() in haystack for p in phrases):
            continue
        try:
            created = datetime.fromisoformat(g.get("created_at") or "")
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            return True
    return False


# Vanaf zoveel vastgelopen pogingen op hetzelfde onderwerp is "nog een keer
# proberen" geen actie meer maar een gewoonte. Twee is de grens: één mislukking
# kan pech zijn (een LLM-timeout, een lege gateway), twee is een patroon.
_VASTGELOPEN_DREMPEL = 2
_VASTGELOPEN_VENSTER_DAGEN = 30

# Zoveel tekens van een vault-actiepunt komen in de doeltitel terecht. Deze
# waarde staat óók in `frontend/js/shell.js` (`solveAlert`); ze horen gelijk te
# blijven, want de dedupe vergelijkt precies die titel.
_ACTIEPUNT_TITELCAP = 60


def _vastgelopen_pogingen(goals: List[Dict], *phrases: str) -> List[Dict]:
    """Doelen die dit onderwerp al probeerden en op `partial`/`failed` strandden.

    Tegenhanger van `_goal_addresses`. Die dempt terecht níét op 'partial' — het
    werk is aantoonbaar niet gedaan, dus de cijfer-alert hoort te blijven staan.
    Het gevolg was alleen dat dezelfde knop dezelfde vastloper bleef starten:
    'Verbeter de CTR van WeAreImpact' werd tussen 15 en 17 juli 2026 zeven keer
    aangemaakt en strandde zeven keer op 'partial' (4 aug 2026 gemeten: 28
    Actiepunt-doelen, 14 unieke titels). Het probleem is echt en de alert moet
    blijven — maar de knop eronder moet naar de vastloper wijzen in plaats van
    er een achtste van te maken.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_VASTGELOPEN_VENSTER_DAGEN)
    uit: List[Dict] = []
    for g in goals:
        if g.get("status") not in ("partial", "failed"):
            continue
        haystack = ((g.get("objective") or "") + " " + (g.get("title") or "")).lower()
        if not any(p.lower() in haystack for p in phrases):
            continue
        try:
            created = datetime.fromisoformat(g.get("created_at") or "")
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            uit.append(g)
    return uit


def _knop_of_blokkade(alert: Dict, goals: List[Dict], *phrases: str) -> Dict:
    """Laat de alert staan, maar vervang de knop zodra het vastloopt.

    Een dashboard-tip die niet uitvoerbaar is, is ruis — en een knop die
    aantoonbaar tot dezelfde vastloper leidt is erger dan ruis: hij kost elke
    klik opnieuw LLM-budget en levert een extra 'partial' doel op dat de
    volgende meting vertroebelt.
    """
    vast = _vastgelopen_pogingen(goals, *phrases)
    if len(vast) < _VASTGELOPEN_DREMPEL:
        return alert
    laatste = vast[0]
    alert = dict(alert)
    alert["type"] = "danger"
    alert["icon"] = "🔁"
    alert["text"] = (
        f"{alert.get('text', '')} — {len(vast)} eerdere pogingen strandden op "
        f"'{laatste.get('status')}'. Nog een doel starten verandert dat niet: "
        f"kijk eerst waaróm '{(laatste.get('title') or '')[:60]}' vastliep."
    )
    alert["action"] = f"open_goal:{laatste.get('id')}"
    alert["action_label"] = "Bekijk de vastloper"
    return alert


# ── GSC-feiten voor het advies (met TTL-cache) ──────────────────────────────
# De dashboard-banner pollt `/advice` elke 8 seconden. Zonder cache betekent
# dat drie Search Console-calls per 8 seconden per open tabblad — richting de
# 20.000 API-calls per dag voor cijfers die één keer per etmaal veranderen, en
# een dashboard dat op elke verversing seconden staat te wachten. De TTL is
# ruim: GSC levert sowieso alleen 'final' data van twee dagen geleden.
_GSC_FACTS_TTL_SECONDS = 600
_gsc_facts_cache: Dict[str, tuple] = {}


def _ranking_page_from_history(site_id: str) -> Dict[str, Dict]:
    """Welke pagina rankt al voor welk zoekwoord — uit de dagelijks
    gesynchroniseerde `gsc_history` (scope=page), niet uit een tweede live
    GSC-call.

    Vóór 13 aug 2026 haalde `_gsc_facts` deze vraag met een eigen live
    `fetch_page_query_performance`-call op, met een `except Exception:
    page_queries = []` eronder. Die stille terugval liet het advies "schrijf
    een artikel" zeggen voor 'voorbeeld korte biografie schrijven' terwijl
    /kennisbank/memoires-schrijven-voorbeelden-en-tips er al 69 impressies/dag
    op haalde (positie 17,8) — exact de kannibalisatie die `zero_click_advice`
    hierboven claimt te voorkomen. Dezelfde vraag ("rankt hier al iets?") is
    al voor de Kansen-lijst opgelost via `gsc_history`
    (`opportunity_quality._gsc_coverage`, 3 aug 2026: 'rankt-al'); twee
    antwoorden op één vraag is hoe zulke fouten ontstaan, dus leest dit
    dezelfde bron.
    """
    ranking: Dict[str, Dict] = {}
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT h.page_url, h.top_query, h.impressions, h.position "
                "FROM gsc_history h JOIN ("
                "  SELECT page_url, MAX(date) AS d FROM gsc_history "
                "  WHERE site_id = ? AND scope = 'page' GROUP BY page_url"
                ") l ON l.page_url = h.page_url AND l.d = h.date "
                "WHERE h.site_id = ? AND h.scope = 'page'",
                (site_id, site_id),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.debug("ranking_page uit gsc_history niet beschikbaar voor %s: %s",
                     site_id, str(e)[:150])
        return ranking
    for r in rows:
        q = (r["top_query"] or "").strip().lower()
        if not q:
            continue
        try:
            impressions = int(r["impressions"] or 0)
        except (TypeError, ValueError):
            impressions = 0
        best = ranking.get(q)
        if best is None or impressions > best["impressions"]:
            ranking[q] = {
                "impressions": impressions,
                "position": r["position"],
                "url": r["page_url"],
            }
    return ranking


def _gsc_facts(site: Dict, days: int) -> Optional[Dict]:
    """Alles wat het advies uit Search Console nodig heeft, in één keer.

    Inclusief de pagina-per-zoekwoord-dimensie: zonder die vraag kan het
    dashboard niet weten óf er al een pagina rankt voor een zoekwoord, en dan
    is elk advies over dat zoekwoord een gok. Dat was precies de fout achter
    "optimaliseer titel en meta description" met een knop "Artikel schrijven".
    """
    import time
    from ..seo import gsc

    gsc_prop = (site.get("gsc_property") or "").strip()
    if not gsc_prop or not gsc.is_configured():
        return None
    key = f"{site['id']}|{days}"
    hit = _gsc_facts_cache.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]

    pages = gsc.fetch_page_performance(gsc_prop, days=days, row_limit=500)
    queries = gsc.fetch_query_performance(gsc_prop, days=days, row_limit=500)
    ranking_page = _ranking_page_from_history(site["id"])

    cur_imps = sum(p["impressions"] for p in pages)
    cur_clicks = sum(p["clicks"] for p in pages)
    facts = {
        "pages": pages,
        "queries": queries,
        "ranking_page": ranking_page,
        "clicks": cur_clicks,
        "impressions": cur_imps,
        "position": (round(sum(p["position"] * p["impressions"] for p in pages) / cur_imps, 1)
                     if cur_imps else 0),
        "ctr": round(cur_clicks / cur_imps * 100, 2) if cur_imps else 0,
    }
    _gsc_facts_cache[key] = (now + _GSC_FACTS_TTL_SECONDS, facts)
    return facts


def zero_click_advice(query: str, position: float, impressions: int,
                      has_ranking_page: bool, page_url: str = "") -> Dict[str, str]:
    """Diagnose én knop voor een zoekwoord met impressies maar nul klikken.

    Eén functie, want dit is één beslissing. Vóór 2 aug 2026 stonden diagnose
    en knop los van elkaar: de tekst zei "optimaliseer titel en meta
    description" en de knop eronder heette "Artikel schrijven". Wie het advies
    opvolgde kreeg een tweede pagina voor een zoekwoord waar er al één voor
    rankte — kannibalisatie als beloning voor het gehoorzamen van je eigen
    dashboard. Zolang die twee op verschillende plekken worden bepaald, lopen
    ze vroeg of laat weer uit elkaar.

    16 aug 2026: dezelfde fout stond nog in de derde tak. Die keek helemaal
    niet naar `has_ranking_page` en beweerde onvoorwaardelijk "er is nog geen
    pagina die hierop mikt". Gemeten op WeAreImpact: het dashboard zei dat over
    'impact strategy' (pos 78.2) terwijl /ai-strategie-consultant er in
    `gsc_history` mét 45 impressies op stond. De knop eronder zou een tweede
    pagina hebben geschreven voor een zoekwoord dat er al één had — precies de
    kannibalisatie waar `cluster_kannibalisatie` op aanslaat. Buiten klikbereik
    mét rankende pagina is een autoriteitsprobleem van díé pagina: niet de
    snippet (die wordt op positie 50 niet gezien) en niet een tweede artikel.
    """
    if position <= 20 and has_ranking_page:
        return {
            "tekst": (f"'{query}' heeft {impressions} impressies maar 0 klikken "
                      f"(pos {position}). De pagina rankt al — dit is een "
                      "snippet-probleem, geen contentprobleem."),
            "action": f"optimize_page:{query}",
            "action_label": "Optimaliseer pagina",
        }
    if position <= 20:
        # Binnen klikbereik, maar geen pagina in de pagina/zoekwoord-dimensie.
        # Dan is "schrijf er een" het eerlijkste advies dat de data toelaat.
        return {
            "tekst": (f"'{query}' heeft {impressions} impressies maar 0 klikken "
                      f"(pos {position}) en geen pagina die er specifiek op mikt: "
                      "schrijf er een."),
            "action": f"write_article:{query}",
            "action_label": "Artikel schrijven",
        }
    if has_ranking_page:
        welke = f" ({page_url})" if page_url else ""
        return {
            "tekst": (f"'{query}' heeft {impressions} impressies op positie {position} — "
                      f"te ver weg om klikken te krijgen. Er staat al een pagina op dit "
                      f"zoekwoord{welke}; die moet sterker worden (contentdiepte, interne "
                      "links, autoriteit). Een tweede artikel kannibaliseert alleen."),
            "action": "open_tab:Optimalisatie",
            "action_label": "Versterk de pagina",
        }
    return {
        "tekst": (f"'{query}' heeft {impressions} impressies op positie {position} — "
                  "te ver weg om klikken te krijgen. Er is nog geen pagina die "
                  "hierop mikt: schrijf er een."),
        "action": f"write_article:{query}",
        "action_label": "Artikel schrijven",
    }


def _queue_pressure(site_id: str) -> Dict[str, int]:
    """Hoeveel werk staat er al klaar te wachten op een mens?

    Het dashboard adviseerde "schrijf 11 artikelen" terwijl er 53 concepten
    in de Wachtrij lagen. Meer produceren lost een doorvoerprobleem niet op —
    het maakt het groter, en verstopt tegelijk de plek waar de opbrengst
    vandaan moet komen.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM content_jobs WHERE site_id = ? "
            "AND status IN ('pending_review', 'needs_work', 'publish_failed') "
            "GROUP BY status", (site_id,),
        ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    return {
        "pending_review": counts.get("pending_review", 0),
        "needs_work": counts.get("needs_work", 0),
        "publish_failed": counts.get("publish_failed", 0),
        "totaal": sum(counts.values()),
    }


@router.get("/{name}/advice")
def project_advice(name: str, days: int = Query(28)):
    """Data-gedreven advies voor het dashboard — geen LLM call."""
    # Response-cache: zie _advice_cache hierboven.
    import time as _time
    _key = f"{name}|{days}"
    _hit = _advice_cache.get(_key)
    if _hit and _hit[0] > _time.time():
        return _hit[1]

    site = _find_site(name)
    if not site:
        raise HTTPException(404, f"Project '{name}' niet gevonden")

    gsc_prop = site.get("gsc_property", "")
    advice = {
        "status": "ok",
        "banner": None,
        "alerts": [],
        "next_step": "",
        "quick_actions": [],
    }

    # 1. Running goals check
    from ..goal import service as goal_service
    project_goals = goal_service.list_goals(limit=30, project=name)
    running = [g for g in project_goals if g["status"] == "running"]
    failed = [g for g in project_goals if g["status"] == "failed"]

    if running:
        g = running[0]
        total = g["task_count"] or 1
        done = g["completed_tasks"] or 0
        pct = round(done / total * 100) if total else 0
        advice["running_goal"] = {
            "id": g["id"],
            "title": g["title"],
            "progress": f"{done}/{total}",
            "percent": pct,
        }
        advice["banner"] = {
            "type": "running",
            "text": f"Bezig: {g['title']} — {done}/{total} taken ({pct}%)",
        }
    elif failed:
        g = failed[0]
        advice["banner"] = {
            "type": "failed",
            "text": f"❌ Doel '{g['title']}' is mislukt — klik om te herstellen",
            "action": f"retry_goal:{g['id']}",
        }

    # 2. GSC data analysis
    if gsc_prop and gsc.is_configured():
        try:
            facts = _gsc_facts(site, days)
            if facts is None:
                raise RuntimeError("geen GSC-feiten")
            pages, queries = facts["pages"], facts["queries"]
            cur_clicks, cur_imps = facts["clicks"], facts["impressions"]
            cur_pos, cur_ctr = facts["position"], facts["ctr"]

            # De recente reeks náást het periode-aggregaat. Die twee kunnen
            # tegengesteld zijn, en dan is het aggregaat de misleidende: op
            # 2 aug 2026 stond WeAreImpact op "positie 18,9 (-4,2)" — een
            # verbetering t.o.v. de vorige 28 dagen — terwijl de laatste zeven
            # GSC-dagen op 22,5 lagen en de klikken van 7 naar 3 waren gezakt.
            # Een dashboard dat alleen het eerste toont, meldt vooruitgang
            # tijdens een terugval.
            from ..seo import history as seo_history
            trend = seo_history.site_trend(site["id"])
            recent_pos = (trend or {}).get("last7", {}).get("avg_position")
            pos_worsening = bool(trend and (trend.get("delta_position") or 0) > 1.0)
            # Voor elk oordeel over "waar staan we nu" telt de verse meting,
            # niet het 28-daags gemiddelde waar de slechte week in wegvalt.
            judged_pos = recent_pos if recent_pos is not None else cur_pos
            advice["trend"] = trend

            def _trend_suffix() -> str:
                if not trend or recent_pos is None:
                    return ""
                d = trend.get("delta_position")
                if d is None:
                    return f" (laatste 7 dagen: {recent_pos})"
                richting = "gezakt" if d > 0 else "verbeterd"
                return (f" (laatste 7 dagen: {recent_pos}, "
                        f"{richting} met {abs(d)} t.o.v. de week ervoor)")

            # Positie alert — gedempt zolang er al een doel voor loopt of recent
            # is gestart (GSC-cijfers reageren pas dagen later op het werk).
            position_addressed = _goal_addresses(
                project_goals,
                f"Optimaliseer de bestaande content van {name}",
                f"Werk de striking-distance zoekwoorden van {name}",
            )
            if position_addressed:
                pass
            elif judged_pos > 15:
                advice["alerts"].append(_knop_of_blokkade({
                    "type": "danger",
                    "icon": "⚠️",
                    "text": f"Gemiddelde positie {judged_pos} — te laag{_trend_suffix()}. "
                            "Buiten klikbereik: dit vraagt om betere rankings, niet om snippets.",
                    "action": f"fix_alert:Optimaliseer de bestaande content van {name} voor betere zoekposities "
                               f"(interne links, contentdiepte, autoriteit). Huidige gemiddelde positie: {judged_pos}.",
                    "action_label": "Oplossen",
                }, project_goals, f"Optimaliseer de bestaande content van {name}"))
            elif judged_pos > 10 or pos_worsening:
                advice["alerts"].append(_knop_of_blokkade({
                    "type": "warning",
                    "icon": "📉",
                    "text": f"Gemiddelde positie {judged_pos}{_trend_suffix()}. "
                            "Werk aan striking-distance zoekwoorden.",
                    "action": f"fix_alert:Werk de striking-distance zoekwoorden van {name} bij (posities 10-20) "
                               f"door bestaande pagina's te optimaliseren. Huidige gemiddelde positie: {judged_pos}.",
                    "action_label": "Oplossen",
                }, project_goals, f"Werk de striking-distance zoekwoorden van {name}"))

            # CTR-alert — zelfde demping als de positie-alert, én positie-bewust.
            # Een vaste ondergrens van 3% is zinloos: op positie 45 ís 0% CTR de
            # verwachte waarde, en meta descriptions herschrijven verandert daar
            # niets aan (25 jul 2026 — deze alert stond bovenaan bij pos 45.6).
            # We gebruiken daarom dezelfde benchmark als de SEO-Optimizer, en
            # zwijgen zodra de gemiddelde positie buiten klikbereik ligt: dan is
            # het een ranking-probleem en dekt de positie-alert het al.
            from ..seo.optimizer import _expected_ctr
            ctr_addressed = _goal_addresses(project_goals, f"Verbeter de CTR van {name}")
            expected_ctr = _expected_ctr(judged_pos)
            if (not ctr_addressed and cur_imps > 100 and judged_pos <= 20
                    and cur_ctr < expected_ctr * 0.7):
                advice["alerts"].append(_knop_of_blokkade({
                    "type": "warning",
                    "icon": "🎯",
                    "text": f"CTR {cur_ctr}% op positie {judged_pos} — benchmark is ~{expected_ctr}%. "
                            f"Verbeter meta descriptions en titels.",
                    "action": f"fix_alert:Verbeter de CTR van {name} door meta descriptions en titels te herschrijven "
                               f"voor de best presterende pagina's. Huidige CTR: {cur_ctr}% op positie {judged_pos} "
                               f"(benchmark ~{expected_ctr}%).",
                    "action_label": "Oplossen",
                }, project_goals, f"Verbeter de CTR van {name}"))

            # Indexed pages alert — zelfde demping
            if len(pages) < 10 and not _goal_addresses(
                    project_goals, f"Schrijf en publiceer nieuwe content voor {name}"):
                advice["alerts"].append(_knop_of_blokkade({
                    "type": "info",
                    "icon": "📝",
                    "text": f"Slechts {len(pages)} pagina's geïndexeerd — maak meer content aan.",
                    "action": f"fix_alert:Schrijf en publiceer nieuwe content voor {name} — nu slechts {len(pages)} "
                              f"pagina's geïndexeerd. Kies onderwerpen op basis van zoekwoordkansen.",
                    "action_label": "Doen",
                }, project_goals, f"Schrijf en publiceer nieuwe content voor {name}"))

            # Top queries with 0 clicks = striking distance. GSC-data loopt ~2 dagen achter,
            # dus "0 klikken" kan hier nog kloppen terwijl er al een artikel voor is
            # geschreven — filter daarom op keywords waar al content voor bestaat, anders
            # blijft dezelfde suggestie terugkomen ondanks dat het werk al gedaan is.
            zero_click = [q for q in queries if q["clicks"]
                          == 0 and q["impressions"] >= 20]
            covered = _written_keywords(name) | _pipeline_keywords(site["id"])
            zero_click = [q for q in zero_click
                          if not _keyword_already_covered(q["query"], covered)]
            if zero_click:
                top = zero_click[0]
                rankt = facts["ranking_page"].get((top["query"] or "").strip().lower()) or {}
                diagnose = zero_click_advice(
                    top["query"], top["position"], top["impressions"],
                    has_ranking_page=bool(rankt), page_url=rankt.get("url") or "",
                )
                advice["alerts"].append({
                    "type": "opportunity",
                    "icon": "💡",
                    "text": diagnose["tekst"],
                    "action": diagnose["action"],
                    "action_label": diagnose["action_label"],
                })

            advice["dash_kpi"] = {
                "clicks": cur_clicks,
                "impressions": cur_imps,
                "ctr": cur_ctr,
                "position": cur_pos,
                "pages": len(pages),
                # De verse meting apart, zodat de tegel de terugval kan tonen
                # die in het 28-daags gemiddelde wegvalt.
                "recent_position": recent_pos,
                "recent_clicks": (trend or {}).get("last7", {}).get("clicks"),
                "delta_position_7d": (trend or {}).get("delta_position"),
                "delta_clicks_7d": (trend or {}).get("delta_clicks"),
            }

            # ── Beste volgende stap ────────────────────────────────────────
            # Volgorde = wat het meeste oplevert, niet wat het makkelijkst te
            # starten is. Doorvoer staat bewust bóven productie: op 2 aug 2026
            # adviseerde dit dashboard "schrijf 11 artikelen" terwijl er 53
            # concepten op goedkeuring wachtten. Nog eens elf schrijven maakt
            # de rij langer en levert geen enkele klik op — publiceren wel.
            #
            # 16 aug 2026: die volgorde stond er wél, maar `if running:` ging er
            # nog vóór — en een lopend contentdoel ís productie. Gemeten op
            # WeAreImpact: 41 concepten in `pending_review` terwijl de beste
            # volgende stap "doel G2 loopt, 5/9 taken" was. De doorvoer-tak was
            # daarmee per constructie onbereikbaar zolang er een doel draaide,
            # precies in de situatie waarvoor hij bedoeld is. Een lopend doel is
            # een status, geen actie voor een mens; de Wachtrij is dat wel.
            queue = _queue_pressure(site["id"])
            advice["queue"] = queue
            if queue["pending_review"] >= 5:
                extra = ""
                if queue["needs_work"]:
                    extra = f" ({queue['needs_work']} daarvan halen de kwaliteitsgate niet)"
                advice["next_step"] = (
                    f"✅ Beoordeel de Wachtrij — {queue['pending_review']} concept(en) wachten op "
                    f"jouw goedkeuring{extra}. Niets hiervan levert een klik op zolang het blijft liggen.")
                advice["next_step_action"] = "open_tab:Wachtrij"
            elif running:
                advice["next_step"] = (f"▶️ Doel '{running[0]['title']}' loopt — "
                                       f"{running[0]['completed_tasks']}/{running[0]['task_count']} taken voltooid")
            elif zero_click:
                # Zelfde beslissing als de alert hierboven, uit dezelfde functie:
                # anders adviseert de tip "optimaliseer" terwijl de hoofdknop
                # eronder een artikel schrijft.
                top = zero_click[0]
                rankt = facts["ranking_page"].get((top["query"] or "").strip().lower()) or {}
                diagnose = zero_click_advice(
                    top["query"], top["position"], top["impressions"],
                    has_ranking_page=bool(rankt), page_url=rankt.get("url") or "",
                )
                if diagnose["action"].startswith("optimize_page:"):
                    advice["next_step"] = (
                        f"🔧 Optimaliseer de pagina voor '{top['query']}' — "
                        f"{top['impressions']} impressies op positie {top['position']}, nul klikken")
                elif diagnose["action"] == "open_tab:Optimalisatie":
                    advice["next_step"] = (
                        f"🔧 Versterk de pagina voor '{top['query']}' — "
                        f"{top['impressions']} impressies op positie {top['position']}, "
                        "te ver weg voor klikken")
                else:
                    advice["next_step"] = (f"📝 Schrijf een artikel voor '{top['query']}' — "
                                           f"{top['impressions']} onbenutte impressies")
                advice["next_step_action"] = diagnose["action"]
            elif judged_pos > 10 and not position_addressed:
                advice["next_step"] = "🔧 Optimaliseer bestaande pagina's voor betere posities (interne links, contentdiepte)"
                advice["next_step_action"] = "open_tab:Optimalisatie"
            else:
                advice["next_step"] = "📈 Voer een kansen-scan uit om nieuwe striking-distance kansen te vinden"
                advice["next_step_action"] = "run_scan"

        except Exception as e:  # noqa: BLE001
            # Niet stil inslikken: een dashboard dat zijn cijfers niet kon
            # ophalen ziet er precies zo uit als een dashboard zonder
            # problemen, en dat is hoe je dagenlang naar oude waarheden kijkt.
            # Wel alleen loggen + één regel in het antwoord — deze route wordt
            # elke acht seconden gepolld, dus een uitkomstkaart per poging zou
            # het Actiecentrum onbruikbaar maken.
            logger.exception("[advies] GSC-analyse mislukt voor %s", name)
            advice["status"] = "gsc_error"
            advice["alerts"].append({
                "type": "danger",
                "icon": "🔌",
                "text": f"Search Console-cijfers konden niet worden opgehaald: {str(e)[:160]}",
            })

    # 3. Kansen check — truth-modus: status afgeleid uit content_jobs
    # (wat er écht live staat), zodat "in behandeling" niet liegt.
    try:
        from ..seo import engine as demand_engine
        from ..seo import potential as demand_potential
        kansen = demand_engine.list_opportunities_truth(site_id=site["id"], status="new")
        if kansen:
            # Zeg wat het waard is, niet alleen hoeveel het er zijn. "11
            # nieuwe kansen" klinkt als winst; "samen ≈ 10 klikken per maand"
            # laat zien dat elf artikelen schrijven daar geen goede ruil voor
            # is — en dát is de afweging die een mens hier moet maken.
            gemeten = [k for k in kansen if k.get("demand") == "gemeten"]
            winst = demand_potential.total_potential(gemeten)
            # Alleen groen vieren wat gemeten vraag heeft — puur speculatieve
            # kansen (geen GSC-signaal) krijgen dezelfde neutrale styling als
            # een informatieve melding, anders oogt "0 gemeten vraag" als
            # goed nieuws door dezelfde groene kaart als een échte kans.
            alert_type = "opportunity" if gemeten else "info"
            alert_icon = "🎯" if gemeten else "🌱"
            if gemeten and winst:
                tekst = (f"{len(kansen)} nieuwe kansen — waarvan {len(gemeten)} met gemeten "
                         f"vraag, samen goed voor ≈ {winst} klikken per maand")
            elif gemeten:
                tekst = f"{len(kansen)} nieuwe kansen, waarvan {len(gemeten)} met gemeten vraag"
            else:
                tekst = (f"{len(kansen)} kandidaat-kansen — allemaal speculatief, "
                         "nul gemeten vraag in Search Console")
            advice["alerts"].append({
                "type": alert_type,
                "icon": alert_icon,
                "text": tekst,
                "action": f"open_tab:Kansen",
                "action_label": "Bekijk kansen",
            })
            # De bulkknop is geen hoofdactie zolang de Wachtrij vol staat (dan
            # is schrijven precies het werk dat de rij langer maakt) of zolang
            # geen van de kansen gemeten vraag heeft — zes artikelen schrijven
            # op een gok is geen "beste volgende stap".
            queue_jam = (advice.get("queue") or {}).get("pending_review", 0) >= 5
            advice["quick_actions"].append({
                "label": f"Schrijf {len(kansen)} kansen",
                "action": "write_all_kansen",
                "primary": bool(gemeten) and not queue_jam,
            })
        in_prog = demand_engine.list_opportunities_truth(site_id=site["id"], status="in_progress")
        if in_prog:
            advice["quick_actions"].append({
                "label": f"{len(in_prog)} kansen in behandeling",
                "action": "open_tab:Kansen",
            })
    except Exception:
        pass

    # 4. Vault context: actiepunten + analytics
    try:
        from ...shared.vault_reader import VaultReader
        vr = VaultReader()
        if vr.is_configured:
            actions = vr.get_pending_actions(site["name"])
            if actions:
                # Een vault-actiepunt is een los "- [ ]"-vinkje in Obsidian; dat wordt nooit
                # automatisch afgevinkt als het onderliggende doel al is uitgevoerd. Filter
                # daarom actiepunten weg waarvoor al een voltooid doel "Actiepunt: <tekst>"
                # bestaat voor dit project, anders blijft de app hetzelfde werk voorstellen.
                # `_ACTIEPUNT_TITELCAP` moet gelijk zijn aan de slice waarmee
                # `shell.js:solveAlert` de doeltitel bouwt — wijkt hij af, dan
                # matcht de dedupe nooit en stelt het dashboard hetzelfde
                # actiepunt eeuwig opnieuw voor. Ook 'running' telt mee: een
                # doel dat nú aan dit actiepunt werkt is geen reden om er een
                # tweede naast te zetten.
                bestaande_titels = [
                    (g["title"] or "").strip().lower()
                    for g in goal_service.list_goals(limit=500, project=name)
                    if g["status"] in ("completed", "running", "ready")
                ]
                pending = [
                    a for a in actions
                    if f"actiepunt: {a[:_ACTIEPUNT_TITELCAP]}".strip().lower()
                    not in bestaande_titels
                ]
                for a in pending[:3]:
                    advice["alerts"].append(_knop_of_blokkade({
                        "type": "info",
                        "icon": "📋",
                        "text": f"Actiepunt: {a[:80]}",
                        "action": f"fix_alert:{a[:200]}",
                        "action_label": "Doen",
                    }, project_goals, f"Actiepunt: {a[:_ACTIEPUNT_TITELCAP]}"))
            analytics = vr.get_recent_analytics()
            if analytics:
                # Extract key insight from analytics
                lines = analytics.split("\n")
                insight_lines = [l for l in lines if "Sessies" in l or "Trend" in l or "Kerncijfers" in l or "sessies" in l]
                if insight_lines:
                    # Kap op woordgrens af, niet midden in een woord
                    insight = insight_lines[0].strip()
                    if len(insight) > 160:
                        insight = insight[:160].rsplit(" ", 1)[0] + "…"
                    advice["alerts"].append({
                        "type": "analytics",
                        "icon": "📊",
                        "text": f"Analytics: {insight}",
                    })
    except Exception:
        pass

    # Quick actions always available
    if not running:
        queue = advice.get("queue") or {}
        if queue.get("pending_review"):
            # Vooraan, want dit is het enige werk in dit rijtje dat iets
            # bestaands naar buiten brengt in plaats van iets nieuws bij te maken.
            advice["quick_actions"].insert(0, {
                "label": f"Beoordeel {queue['pending_review']} concepten",
                "action": "open_tab:Wachtrij",
                "primary": True,
            })
        advice["quick_actions"].insert(0 if not queue.get("pending_review") else 1, {
            "label": "Voer scan uit",
            "action": "run_scan",
            "primary": not queue.get("pending_review"),
        })
        advice["quick_actions"].append({
            "label": "Genereer blog suggesties",
            "action": "generate_suggestions",
        })
        advice["quick_actions"].append({
            "label": "Nieuw doel",
            "action": "new_goal",
        })

    _advice_cache[_key] = (_time.time() + _ADVICE_TTL_SECONDS, advice)
    return advice


@router.post("/{name}/action/done")
def mark_action_done(name: str, payload: Dict = {}):
    """Vink een actiepunt aan in de Obsidian vault (bron van waarheid).

    De dashboard-actiepunten komen uit de '- [ ]' vinkjes in de vault.
    Als de gebruiker op 'Doen' drukt, moet het vinkje direct dicht —
    anders blijft het item eeuwig in de todo staan (de oude bug: 'Doen'
    startte alleen een agent en vinkte nooit de bron aan). De frontend
    vinkt hiermee de vault af EN start daarna optioneel de agent.
    """
    action_text = (payload or {}).get("text", "")
    if not action_text:
        raise HTTPException(400, "Geen 'text' meegegeven")
    from ...shared.vault_reader import VaultReader
    vr = VaultReader()
    if not vr.is_configured:
        raise HTTPException(409, "Obsidian vault niet geconfigureerd")
    ok = vr.mark_action_done(name, action_text)
    return {"ok": ok, "text": action_text}


@router.get("/{name}/skill")
def project_skill(name: str):
    """Lees de SKILL.md voor een project (frontmatter + body)."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(404, detail=f"Project '{name}' niet gevonden")
    skill_path = project_dir / "SKILL.md"
    if not skill_path.exists():
        # Fallback: eerste .md in project directory
        files = list(project_dir.glob("*.md"))
        if files:
            skill_path = files[0]
        else:
            raise HTTPException(404, detail=f"Geen SKILL.md voor project '{name}'")
    content = skill_path.read_text("utf-8")
    frontmatter = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip().strip('"').strip("'")
            body = parts[2].strip()
    return {"name": name, "frontmatter": frontmatter, "body": body[:500]}

"""
Projects API — leest projecten uit Obsidian vault (10_Projects/).
Als OBSIDIAN_VAULT_PATH niet gezet is, valt het terug op de projects/ map.

  GET /api/projects             → lijst alle projecten
  GET /api/projects/{name}      → SKILL.md + metadata van 1 project
"""

from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
import re, os
from typing import List, Dict, Optional

router = APIRouter(prefix="/api/projects", tags=["projects"])

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

from datetime import date, timedelta
from ..seo import gsc, sites as sites_service

def _find_site(name: str):
    norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
    target = norm(name)
    for s in sites_service.list_sites():
        if norm(s["name"]) == target:
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


@router.get("/{name}/advice")
def project_advice(name: str, days: int = Query(28)):
    """Data-gedreven advies voor het dashboard — geen LLM call."""
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
    project_goals = goal_service.list_goals(limit=10, project=name)
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
            pages = gsc.fetch_page_performance(gsc_prop, days=days, row_limit=500)
            queries = gsc.fetch_query_performance(gsc_prop, days=days, row_limit=500)
            cur_clicks = sum(p["clicks"] for p in pages)
            cur_imps = sum(p["impressions"] for p in pages)
            cur_pos = round(sum(p["position"] * p["impressions"]
                           for p in pages) / cur_imps, 1) if cur_imps else 0
            cur_ctr = round((cur_clicks / cur_imps * 100), 2) if cur_imps else 0

            # Positie alert
            if cur_pos > 15:
                advice["alerts"].append({
                    "type": "danger",
                    "icon": "⚠️",
                    "text": f"Gemiddelde positie {cur_pos} — te laag. Optimaliseer bestaande content voor CTR en kwaliteit.",
                    "action": f"fix_alert:Optimaliseer de bestaande content van {name} voor betere zoekposities "
                               f"(interne links, meta descriptions, contentdiepte). Huidige gemiddelde positie: {cur_pos}.",
                    "action_label": "Oplossen",
                })
            elif cur_pos > 10:
                advice["alerts"].append({
                    "type": "warning",
                    "icon": "📉",
                    "text": f"Gemiddelde positie {cur_pos} — kan beter. Werk aan striking-distance zoekwoorden.",
                    "action": f"fix_alert:Werk de striking-distance zoekwoorden van {name} bij (posities 10-20) "
                               f"door bestaande pagina's te optimaliseren. Huidige gemiddelde positie: {cur_pos}.",
                    "action_label": "Oplossen",
                })

            # CTR alert
            if cur_ctr < 3.0 and cur_imps > 100:
                advice["alerts"].append({
                    "type": "warning",
                    "icon": "🎯",
                    "text": f"CTR {cur_ctr}% is laag — verbeter meta descriptions en titels.",
                    "action": f"fix_alert:Verbeter de CTR van {name} door meta descriptions en titels te herschrijven "
                               f"voor de best presterende pagina's. Huidige CTR: {cur_ctr}%.",
                    "action_label": "Oplossen",
                })

            # Indexed pages alert
            if len(pages) < 10:
                advice["alerts"].append({
                    "type": "info",
                    "icon": "📝",
                    "text": f"Slechts {len(pages)} pagina's geïndexeerd — maak meer content aan.",
                    "action": f"fix_alert:Schrijf en publiceer nieuwe content voor {name} — nu slechts {len(pages)} "
                              f"pagina's geïndexeerd. Kies onderwerpen op basis van zoekwoordkansen.",
                    "action_label": "Doen",
                })

            # Top queries with 0 clicks = striking distance. GSC-data loopt ~2 dagen achter,
            # dus "0 klikken" kan hier nog kloppen terwijl er al een artikel voor is
            # geschreven — filter daarom op keywords waar al content voor bestaat, anders
            # blijft dezelfde suggestie terugkomen ondanks dat het werk al gedaan is.
            zero_click = [q for q in queries if q["clicks"]
                          == 0 and q["impressions"] >= 20]
            written = _written_keywords(name)
            zero_click = [q for q in zero_click if q["query"].strip().lower() not in written]
            if zero_click:
                top = zero_click[0]
                advice["alerts"].append({
                    "type": "opportunity",
                    "icon": "💡",
                    "text": f"'{top['query']}' heeft {top['impressions']} impressies maar 0 klikken (pos {top['position']}). Optimaliseer deze pagina.",
                    "action": f"write_article:{top['query']}",
                    "action_label": "Artikel schrijven",
                })

            advice["dash_kpi"] = {
                "clicks": cur_clicks,
                "impressions": cur_imps,
                "ctr": cur_ctr,
                "position": cur_pos,
                "pages": len(pages),
            }

            # Next step suggestion
            next_action = None
            if not running and zero_click:
                kw = zero_click[0]['query']
                advice["next_step"] = f"📝 Schrijf een artikel voor '{kw}' — {zero_click[0]['impressions']} onbenutte impressies"
                advice["next_step_action"] = f"write_article:{kw}"
                # Check of dit keyword al een kans is
                try:
                    from ..seo import engine as demand_engine
                    existing_kansen = demand_engine.list_opportunities(site_id=site["id"], status="new")
                    existing_kansen += demand_engine.list_opportunities(site_id=site["id"], status="in_progress")
                    if not any(k.get("query","").lower() == kw.lower() for k in existing_kansen):
                        advice["next_step_action"] = f"write_article:{kw}"
                except Exception:
                    pass
            elif not running and cur_pos > 10:
                advice["next_step"] = "🔧 Optimaliseer bestaande pagina's voor betere posities (meta descriptions, interne links)"
            elif not running:
                advice["next_step"] = "📈 Voer een kansen-scan uit om nieuwe striking-distance kansen te vinden"
                advice["next_step_action"] = "run_scan"
            else:
                advice["next_step"] = f"▶️ Doel '{running[0]['title']}' loopt — {running[0]['completed_tasks']}/{running[0]['task_count']} taken voltooid"

        except Exception:
            pass

    # 3. Kansen check — truth-modus: status afgeleid uit content_jobs
    # (wat er écht live staat), zodat "in behandeling" niet liegt.
    try:
        from ..seo import engine as demand_engine
        kansen = demand_engine.list_opportunities_truth(site_id=site["id"], status="new")
        if kansen:
            advice["alerts"].append({
                "type": "opportunity",
                "icon": "🎯",
                "text": f"{len(kansen)} nieuwe kansen gevonden — schrijf er een artikel voor",
                "action": f"open_tab:Kansen",
                "action_label": "Bekijk kansen",
            })
            advice["quick_actions"].append({
                "label": f"Schrijf {len(kansen)} kansen",
                "action": "write_all_kansen",
                "primary": True,
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
                completed_titles = [
                    g["title"].strip().lower()
                    for g in goal_service.list_goals(limit=50, project=name)
                    if g["status"] == "completed"
                ]
                pending = [
                    a for a in actions
                    if f"actiepunt: {a[:60]}".strip().lower() not in completed_titles
                ]
                for a in pending[:3]:
                    advice["alerts"].append({
                        "type": "info",
                        "icon": "📋",
                        "text": f"Actiepunt: {a[:80]}",
                        "action": f"fix_alert:{a[:200]}",
                        "action_label": "Doen",
                    })
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
        advice["quick_actions"].insert(0, {
            "label": "Voer scan uit",
            "action": "run_scan",
            "primary": True,
        })
        advice["quick_actions"].append({
            "label": "Genereer blog suggesties",
            "action": "generate_suggestions",
        })
        advice["quick_actions"].append({
            "label": "Nieuw doel",
            "action": "new_goal",
        })

    return advice


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

"""
FB→SEO impact-meting — de gesloten meetlus.

Voor elke FB-post die de agent plaatste (gelogd in `fb_posts`, mét query +
article_url) meten we de GSC-positie van de gelinkte pagina:
  - positie ~7 dagen VÓÓR de post (baseline)
  - positie ~7 dagen NÁ de post (effect)
  - delta (negatief = winst, want lagere positie is beter)

Bron: gsc_history(scope='page') koppelt page_url → top_query → position → date.
Zo kan Iris hard zeggen: "FB-post over 'vindliefde' tilde de pagina van pos X
naar Y" — in plaats van te gokken dat FB 'wel helpt'.

Faalveilig: geen GSC-historie → expliciete 'geen_data'-regel, geen lege claims.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from ...shared.database import get_conn

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> Optional[str]:
    """Normaliseer elke datum-string naar YYYY-MM-DD (voor SQLite date()-opties)."""
    if not s:
        return None
    s = s[:10]
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        return None


def _position_around(site_id: str, page_url: Optional[str], query: Optional[str], post_date: str,
                      before_days: int = 7, after_days: int = 7) -> Dict[str, Any]:
    """GSC-positie van de gelinkte pagina net vóór en net ná `post_date`.

    Matcht op page_url ÉN (falls dat faalt) op top_query — want GSC-historie
    kent per pagina een top_query, en de FB-post is aan diezelfde query gekoppeld.
    Zo vindt ook een post op /blog/slug de positie van de homepage-pagina waarvan
    de top_query gelijk is aan de post-query.
    """
    pd = _parse_date(post_date)
    if not pd:
        return {"baseline": None, "effect": None, "available": False}
    with get_conn() as conn:
        # Zoek de pagina: eerst op page_url, anders op top_query.
        page_row = None
        if page_url:
            page_row = conn.execute(
                "SELECT page_url FROM gsc_history WHERE site_id = ? AND scope = 'page' "
                "AND page_url = ? LIMIT 1", (site_id, page_url)
            ).fetchone()
        if not page_row and query:
            q = (query or "").lower().strip()
            page_row = conn.execute(
                "SELECT page_url FROM gsc_history WHERE site_id = ? AND scope = 'page' "
                "AND LOWER(top_query) = ? LIMIT 1", (site_id, q)
            ).fetchone()
        matched_url = page_row[0] if page_row else None

        if not matched_url:
            return {"baseline": None, "effect": None, "available": False,
                    "matched_url": None}

        base = conn.execute(
            "SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page' "
            "AND page_url = ? AND date <= date(?, ?)",
            (site_id, matched_url, pd, f"-{before_days} days"),
        ).fetchone()[0]
        eff = conn.execute(
            "SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page' "
            "AND page_url = ? AND date >= date(?, ?)",
            (site_id, matched_url, pd, f"+{after_days} days"),
        ).fetchone()[0]
        base_pos = conn.execute(
            "SELECT position FROM gsc_history WHERE site_id = ? AND scope = 'page' "
            "AND page_url = ? AND date = ?", (site_id, matched_url, base)
        ).fetchone() if base else None
        eff_pos = conn.execute(
            "SELECT position FROM gsc_history WHERE site_id = ? AND scope = 'page' "
            "AND page_url = ? AND date = ?", (site_id, matched_url, eff)
        ).fetchone() if eff else None
    return {
        "baseline_date": base, "baseline": base_pos[0] if base_pos else None,
        "effect_date": eff, "effect": eff_pos[0] if eff_pos else None,
        "available": bool(base or eff), "matched_url": matched_url,
    }


def compute_fb_seo_impact(site_name: Optional[str] = None) -> Dict[str, Any]:
    """Bereken de FB→SEO-impact voor alle gelogde posts (of één site)."""
    from ..seo import sites as sites_service
    from .facebook_store import get_fb_posts

    rows = get_fb_posts(site_name)
    if not rows:
        return {"success": True, "site_name": site_name, "rows": 0,
                "impact": [],
                "note": "Nog geen FB-posts gelogd — plaats eerst posts via de agent "
                        "(met query/artikel-link) om de meetlus te vullen."}

    # site_name → site_id voor gsc_history (naam-normalisatie: spaties/hoofdletters
    # variëren tussen de sites-tabel en wat de agent logt).
    def _sq(n):
        return (n or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    site_id_map = {_sq(s.get("name", "")): s.get("id") for s in sites_service.list_sites()}
    # Ook op site_id direct kunnen zoeken als er al een match is.
    def _resolve_sid(name):
        if not name:
            return None
        return site_id_map.get(_sq(name)) or site_id_map.get(name)

    impact = []
    for p in rows:
        sn = p.get("site_name", "")
        sid = _resolve_sid(sn)
        art = p.get("article_url")
        rec = {
            "post_id": p.get("post_id"), "site_name": sn,
            "query": p.get("query"), "article_url": art,
            "placed_at": p.get("placed_at"),
        }
        if sid and art:
            pos = _position_around(sid, art, p.get("query"), p.get("placed_at"))
            rec.update({
                "gsc_baseline_pos": pos.get("baseline"),
                "gsc_effect_pos": pos.get("effect"),
                "gsc_baseline_date": pos.get("baseline_date"),
                "gsc_effect_date": pos.get("effect_date"),
                "gsc_available": pos.get("available"),
            })
            if pos.get("baseline") and pos.get("effect"):
                rec["delta_position"] = round(pos["effect"] - pos["baseline"], 1)
                # Negatieve delta = positie omlaag = winst
                rec["verdict"] = "verbeterd" if rec["delta_position"] < 0 else (
                    "verslechterd" if rec["delta_position"] > 0 else "onveranderd")
            else:
                rec["delta_position"] = None
                rec["verdict"] = "onvoldoende_data"
        else:
            rec["gsc_available"] = False
            rec["verdict"] = "geen_koppeling"
        impact.append(rec)

    # Samenvatting: hoeveel posts tilden de positie op
    improved = [r for r in impact if r.get("verdict") == "verbeterd"]
    worsened = [r for r in impact if r.get("verdict") == "verslechterd"]
    no_data = [r for r in impact if r.get("verdict") in ("onvoldoende_data", "geen_koppeling")]
    return {
        "success": True,
        "site_name": site_name,
        "rows": len(impact),
        "impact": impact,
        "summary": {
            "verbeterd": len(improved),
            "verslechterd": len(worsened),
            "onvoldoende_data": len(no_data),
            "avg_delta_position": round(
                sum(r["delta_position"] for r in impact if r.get("delta_position") is not None)
                / max(1, len([r for r in impact if r.get("delta_position") is not None])), 1),
        },
    }


def fb_seo_impact_block(site_name: Optional[str] = None) -> str:
    """Leesbare block voor Iris / UI.”"""
    r = compute_fb_seo_impact(site_name)
    if not r.get("success"):
        return f"FB→SEO-impact: fout ({r.get('error', 'onbekend')})."
    if r.get("rows") == 0:
        return ("FB→SEO-impact: nog geen FB-posts gelogd. Plaats posts via de agent "
                "(met query + artikel-link) en wacht ~7 dagen op GSC-data om het effect te meten.")
    lines = ["FB→SEO-impact (per geplaatste post, GSC-positie vóór vs. ná):"]
    for i in r.get("impact", []):
        if i.get("verdict") == "verbeterd":
            lines.append(f"- ✓ '{i.get('query')}' tilde {i.get('article_url','?')[-40:]} "
                          f"van pos {i.get('gsc_baseline_pos')} → {i.get('gsc_effect_pos')} "
                          f"(Δ {i.get('delta_position')})")
        elif i.get("verdict") == "verslechterd":
            lines.append(f"- ↓ '{i.get('query')}' zakte van pos {i.get('gsc_baseline_pos')} "
                          f"→ {i.get('gsc_effect_pos')} (Δ {i.get('delta_position')})")
        else:
            lines.append(f"- '{i.get('query') or i.get('article_url','?')}': {i.get('verdict')} "
                          f"(geen GSC-vergelijking beschikbaar)")
    s = r.get("summary", {})
    lines.append(f"Totaal: {s.get('verbeterd')} verbeterd, {s.get('verslechterd')} verslechterd, "
                 f"{s.get('onvoldoende_data')} onvoldoende data. "
                 f"Gem. Δ-positie: {s.get('avg_delta_position')}.")
    return "\n".join(lines)

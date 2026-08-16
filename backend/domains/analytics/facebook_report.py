"""
Facebook-rapportage voor Iris — vertaalt de opgeslagen Facebook-snapshots
(backend/domains/analytics/facebook_store.py) naar een leesbaar blok dat Iris
meekrijgt in haar prompt, naast het SEO-weekbeeld.

Bewust GEEN live Graph API hier: de geplande job `facebook_snapshot` trekt de
data 1x/etmaal en schrijft naar `fb_insights`. Iris leest uit die tabel —
instant, rate-limit-vrij, en met een expliciete `captured_at` zodat verouderde
data niet op "rustig" lijkt. Net als insights.py faalveilig: een niet-leesbare
pagina krijgt een statusregel, geen stilte.
"""
import logging
from typing import Dict, Any

from ...shared.database import get_conn
from ..seo import sites as sites_service
from .facebook_store import get_all_snapshots

logger = logging.getLogger(__name__)


def _has_fb_site() -> bool:
    try:
        for s in sites_service.list_sites():
            if (s.get("facebook_page_id") or "").strip():
                return True
    except Exception:
        pass
    return False


async def fb_prompt_block() -> str:
    """Facebook-beeld als tekst voor Iris' prompt. Altijd een zin, nooit leeg."""
    if not _has_fb_site():
        return ("Facebook: geen enkele site heeft een facebook_page_id ingesteld — "
                "er is dus géén Facebook-beeld. Configureer eerst een pagina in de "
                "sites-tabel (facebook_page_id + facebook_page_token).")

    snaps = get_all_snapshots()
    if not snaps:
        return ("Facebook: nog geen enkele snapshot opgeslagen (de geplande "
                "facebook_snapshot-job heeft nog niet gedraaid, of geen enkele "
                "pagina is leesbaar). Trek handmatig een snapshot op via "
                "/api/facebook/snapshot/run.")

    regels = ["Facebook-overzicht (snapshot via Graph API, 1x/etmaal):"]
    ok = 0
    for s in snaps:
        name = s.get("site_name", "?")
        captured = (s.get("captured_at") or "")[:10]
        if s.get("status") != "ok" or not s.get("snapshot"):
            regels.append(f"- {name}: niet leesbaar ({s.get('error', 'onbekend')[:90]}) "
                          f"[snapshot {captured}]")
            continue
        ok += 1
        a = s["snapshot"]
        page = a.get("page", {})
        fans = a.get("fans_now")
        fan_adds = a.get("fan_adds_window")
        regels.append(
            f"- {name} ({page.get('name', '?')}, {page.get('fan_count', '?')} fans): "
            f"{a.get('posts_analysed')} posts/28d, {a.get('total_engagement')} interacties, "
            f"avg {a.get('avg_engagement_per_post')}/post, "
            f"fan-groei {fan_adds if fan_adds is not None else 'n/b'}, "
            f"beste dag: {a.get('best_posting_day') or 'n/b'} [snapshot {captured}]."
        )
        top = a.get("top_posts", [])[:3]
        if top:
            best = top[0]
            regels.append(f"  Top-post: {(best.get('message') or '')[:80]} "
                          f"({best.get('engagement')} interacties).")
    if ok == 0:
        regels.append("Geen enkele Facebook-pagina is op dit moment leesbaar "
                      "(geen token / geen scope). Los de token-scope eerst op.")
    # FB→SEO-impact (gesloten meetlus): welke posts tilden de GSC-positie op?
    try:
        from .fb_seo_impact import fb_seo_impact_block
        block = fb_seo_impact_block()
        if block and "nog geen FB-posts" not in block:
            regels.append("")
            regels.append(block)
    except Exception as e:  # nooit de rapportage breken om impact
        logger.warning("[fb_report] fb_seo_impact overgeslagen: %s", e)

    # Wereldklasse-uitbreiding: trend + cross-project benchmark uit de
    # dagelijkse historie (fb_trends.py) — zonder extra Meta-calls.
    try:
        from .facebook_trends import trends_prompt_block
        regels.append("")
        regels.append(trends_prompt_block())
    except Exception as e:  # nooit de rapportage breken om trends
        logger.warning("[fb_report] trends_prompt_block overgeslagen: %s", e)
    return "\n".join(regels)

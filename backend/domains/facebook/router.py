"""
Facebook Agent "Deluxe" API — volledig pagina-beheer en analyse vanuit ImpactOS.

  GET  /api/facebook/{site}/connection     → verbinding + scope-test
  GET  /api/facebook/pages                 → alle pagina's van het globale token (debug, site-onafhankelijk)
  GET  /api/facebook/{site}/info           → pagina-basisinfo
  GET  /api/facebook/{site}/settings       → lees instellingen
  POST /api/facebook/{site}/settings       → wijzig instellingen (naam, about, ...)
  GET  /api/facebook/{site}/posts          → recente posts + engagement
  POST /api/facebook/{site}/posts          → nieuwe post (tekst/link/foto/gepland)
  DELETE /api/facebook/{site}/posts/{id}   → verwijder post
  GET  /api/facebook/{site}/insights       → pagina-insights (reach/engagement/fans)
  GET  /api/facebook/{site}/analyse        → gecombineerd analyserapport
  GET  /api/facebook/{site}/comments/{post_id}   → comments op een post
  POST /api/facebook/{site}/comments/{comment_id}/reply   → reageer
  POST /api/facebook/{site}/comments/{comment_id}/hide    → verberg/toon
  DEL  /api/facebook/{site}/comments/{comment_id}         → verwijder

`site` is de project/ site-naam (bijv. "LiefdeVoorIedereen"); ontbreekt die,
dan wordt de globale FACEBOOK_PAGE_ID/TOKEN gebruikt.
"""
import functools
import logging
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from . import agent as fb

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/facebook", tags=["facebook-agent"])


def _site(site: Optional[str]) -> Optional[str]:
    return (site or None) and site.strip()


def _handle_config_errors(fn):
    """Elk endpoint kan `_token_for` treffen (geen page-id/token voor de
    site) — dat gooit een ValueError. Zonder deze wrapper crasht dat als
    kale 500 i.p.v. de bedoelde 400 met uitleg."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ValueError as e:
            raise HTTPException(400, detail=str(e)[:300])
    return wrapper


@router.get("/pages")
async def pages():
    """Alle pagina's van het globale token (debug) — site-onafhankelijk,
    vandaar geen {site} in het pad."""
    return await fb.list_pages()


@router.get("/{site}/connection")
@_handle_config_errors
async def connection(site: str):
    return await fb.test_connection(_site(site))


@router.get("/{site}/info")
@_handle_config_errors
async def info(site: str):
    return await fb.get_page_info(_site(site))


@router.get("/{site}/settings")
@_handle_config_errors
async def get_settings(site: str):
    r = await fb.get_settings(_site(site))
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.post("/{site}/settings")
@_handle_config_errors
async def update_settings(site: str, body: dict):
    r = await fb.update_settings(_site(site), **body)
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.get("/{site}/posts")
@_handle_config_errors
async def get_posts(site: str, limit: int = Query(20, ge=1, le=100),
                    with_metrics: bool = Query(True)):
    r = await fb.get_posts(_site(site), limit=limit, with_metrics=with_metrics)
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.post("/{site}/posts")
@_handle_config_errors
async def create_post(site: str, body: dict):
    r = await fb.create_post(
        text=body.get("text", ""),
        site_name=_site(site),
        link=body.get("link"),
        image_path=body.get("image_path"),
        scheduled_publish_time=body.get("scheduled_publish_time"),
        published=body.get("published", True),
        query=body.get("query"),
        article_url=body.get("article_url"),
    )
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.delete("/{site}/posts/{post_id}")
@_handle_config_errors
async def delete_post(site: str, post_id: str):
    r = await fb.delete_post(post_id, _site(site))
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.get("/{site}/impact")
async def impact(site: str):
    """FB→SEO-impact: per geplaatste post de GSC-positie vóór vs. ná de post."""
    from ..analytics.fb_seo_impact import compute_fb_seo_impact
    try:
        return await asyncio.to_thread(compute_fb_seo_impact, _site(site))
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])
@_handle_config_errors
async def insights(site: str, days: int = Query(28, ge=1, le=90),
                  metrics: Optional[str] = Query(None)):
    m = metrics.split(",") if metrics else None
    r = await fb.get_insights(_site(site), metrics=m, days=days)
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.get("/{site}/analyse")
@_handle_config_errors
async def analyse(site: str, days: int = Query(28, ge=1, le=90)):
    r = await fb.analyse_page(_site(site), days=days)
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.get("/{site}/comments/{post_id}")
@_handle_config_errors
async def comments(site: str, post_id: str, limit: int = Query(25, ge=1, le=100)):
    r = await fb.get_comments(post_id, _site(site), limit=limit)
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.post("/{site}/comments/{comment_id}/reply")
@_handle_config_errors
async def reply(site: str, comment_id: str, body: dict):
    r = await fb.reply_comment(comment_id, body.get("message", ""), _site(site))
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.post("/{site}/comments/{comment_id}/hide")
@_handle_config_errors
async def hide(site: str, comment_id: str, body: dict = {}):
    r = await fb.hide_comment(comment_id, bool(body.get("hide", True)), _site(site))
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.get("/{site}/suggest")
async def suggest(site: str, days: int = Query(28, ge=1, le=90),
                  limit: int = Query(5, ge=1, le=20), write: bool = Query(False)):
    """Genereer FB-post-ideeën uit echte data (GSC-topqueries, Demand-kansen, FB-engagement).
    Met ?write=true worden de posts ook daadwerkelijk geschreven via de LLM-backend."""
    from ..analytics.facebook_content import suggest_facebook_content
    try:
        r = await suggest_facebook_content(site, days=days, limit=limit, write=write)
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))


@router.get("/snapshot")
async def snapshot_get(site: Optional[str] = Query(None)):
    """Lees de laatst opgeslagen snapshot uit de DB (geen live Graph API)."""
    from ..analytics.facebook_store import get_snapshot, get_all_snapshots
    if site:
        s = get_snapshot(site)
        if not s:
            raise HTTPException(404, detail=f"Geen snapshot voor {site}")
        return s
    return {"snapshots": get_all_snapshots()}


@router.post("/snapshot/run")
async def snapshot_run():
    """Trek handmatig een frisse snapshot voor alle FB-sites (zet de geplande job voort)."""
    from ..analytics.facebook_store import snapshot_all_facebook
    try:
        result = await snapshot_all_facebook()
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])
    return {"success": True, **result}


@router.get("/{site}/trend")
async def trend(site: str, days: int = Query(90, ge=7, le=365)):
    """Tijdreeks-trend voor één site (uit fb_history, geen live Graph API)."""
    from ..analytics.facebook_trends import compute_trend
    try:
        return compute_trend(_site(site), limit_days=days)
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])


@router.get("/benchmark")
async def benchmark():
    """Cross-project benchmark van alle FB-sites (fan-groei, engagement/1k fans)."""
    from ..analytics.facebook_trends import benchmark_projects
    try:
        return benchmark_projects()
    except Exception as e:
        raise HTTPException(400, detail=str(e)[:300])


@router.delete("/{site}/comments/{comment_id}")
@_handle_config_errors
async def delete_comment(site: str, comment_id: str):
    r = await fb.delete_comment(comment_id, _site(site))
    if r.get("success"):
        return r
    raise HTTPException(400, detail=r.get("error"))

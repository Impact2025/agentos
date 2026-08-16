"""
Facebook Agent "Deluxe" — volledige pagina-beheer- en analyse-laag via de
Meta Graph API v19.0.

In tegenstelling tot backend/shared/facebook.py (dat alleen post) kan deze
agent een Facebook-pagina volledig beheren en analyseren:

  * VERBINDING    test_connection / get_page_info / list_pages
  * ANALYSE       get_posts (likes/comments/shares/impressions per post)
                  get_insights (page_impressions, page_post_engagements,
                  page_fans, reach, video_views ...)
                  analyse_page (gecombineerd rapport: top-posts, engagement-rate,
                  groei, beste posting-tijd)
  * INSTELLINGEN  get_settings / update_settings (naam, about, beschrijving,
                  website, call_to_action, messaging, automatisering ...)
  * POSTS         create_post / delete_post / schedule_post
  * COMMENTS/INBOX get_comments / reply_comment / hide_comment / delete_comment
  * MEDIA         get_media (foto's/video's van de pagina)

Token-resolutie volgt hetzelfde patroon als facebook.py / instagram.py:
per-site facebook_page_id + facebook_page_token uit de `sites`-tabel, met
fallback op FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN in .env.

BELANGRIJK (scope): een Page Access Token is gebonden aan de pagina('s) waarvoor
het is uitgegeven (granular_scopes.target_ids). Roep je een pagina aan waarvoor
het token géén scope heeft, dan antwoordt Graph API met (#100) "Object does not
exist … requires pages_read_engagement". De functies hier vertalen dat naar een
helder {"success": False, "error": "NO_SCOPE: token heeft geen toegang tot pagina
…"} zodat de agent en de UI precies weten wat er mis is — en niet stil falen.

Vereiste scopes voor een volledige deluxe-agent:
  pages_manage_posts, pages_read_engagement, pages_manage_engagement,
  pages_manage_metadata, pages_read_user_content, pages_show_list,
  business_management (voor insights soms page ID-gebonden).
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List

import httpx

from ...shared.config import FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN
# Eén tokenresolutie voor heel Facebook — shared/facebook.py is canoniek,
# zodat de simpele post-only flow en deze deluxe-agent nooit uit elkaar
# kunnen lopen over welk token bij welke site hoort.
from ...shared.facebook import _get_site_data, is_configured, GRAPH_API  # noqa: F401

logger = logging.getLogger(__name__)

# Velden die we veilig mogen schrijven via POST /{page-id} (page settings).
# Zie https://developers.facebook.com/docs/graph-api/reference/page/#edges
PAGE_SETTINGS_WRITABLE = {
    "name", "about", "description", "website", "phone", "email",
    "single_line_address", "category", "hours", "username",
    "mission", "products", "general_info", "bio", "impressum",
    "company_overview",
}


# ─────────────────────────────────────────────────────────────────────────────
# Token / pagina-resolutie
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Interne Graph API-helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _g(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Eén Graph API-call. Geeft altijd een dict terug: {ok, status, json, error}."""
    params = params or {}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method, f"{GRAPH_API}/{path}", params=params, data=data,
                files=files, timeout=timeout,
            )
        except httpx.HTTPError as e:
            return {"ok": False, "status": 0, "json": None, "error": f"HTTP-fout: {e}"}

    try:
        body = resp.json()
    except Exception:
        body = None

    if resp.status_code == 200 and isinstance(body, dict) and "error" not in body:
        return {"ok": True, "status": resp.status_code, "json": body, "error": None}

    fb_err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
    code = fb_err.get("code")
    msg = fb_err.get("message", resp.text[:300])
    # Scope-mismatch herkenning: token mag deze pagina niet lezen/schrijven.
    if code == 100 or "does not exist" in msg or "requires" in msg and "permission" in msg:
        err = f"NO_SCOPE: token heeft geen toegang tot deze pagina ({code}: {msg[:200]})"
    elif code == 190 or "session has expired" in msg.lower() or "invalid" in msg.lower():
        err = f"TOKEN_EXPIRED: access token verlopen/ongeldig ({code}: {msg[:200]})"
    else:
        err = f"GRAPH_ERROR {code}: {msg[:300]}"
    return {"ok": False, "status": resp.status_code, "json": body, "error": err}


def _token_for(site_name: Optional[str]) -> tuple:
    page_id, token = _get_site_data(site_name)
    if not page_id or not token:
        raise ValueError(f"Geen Facebook page-id/token voor {site_name or 'globale config'}")
    return page_id, token


# ─────────────────────────────────────────────────────────────────────────────
# VERBINDING
# ─────────────────────────────────────────────────────────────────────────────

async def test_connection(site_name: Optional[str] = None) -> Dict[str, Any]:
    """Verbinding + scope test. Zegt expliciet of het token deze pagina mag lezen."""
    page_id, token = _token_for(site_name)
    r = await _g("GET", page_id, params={"fields": "name,fan_count", "access_token": token})
    if r["ok"]:
        return {"success": True, "page_id": page_id, "page": r["json"], "site_name": site_name}
    return {"success": False, "page_id": page_id, "error": r["error"], "site_name": site_name}


async def get_page_info(site_name: Optional[str] = None) -> Dict[str, Any]:
    page_id, token = _token_for(site_name)
    fields = ("name,about,description,website,fan_count,followers_count,"
              "phone,emails,single_line_address,category,username,link,"
              "mission,products,general_info,bio,impressum,cover,location,"
              "verification_status,talking_about_count")
    r = await _g("GET", page_id, params={"fields": fields, "access_token": token})
    if r["ok"]:
        return r["json"]
    raise ValueError(r["error"])


async def list_pages() -> Dict[str, Any]:
    """Toon alle pagina's waarvoor het globale token scope heeft (handig voor debug)."""
    token = FACEBOOK_PAGE_TOKEN
    if not token:
        return {"success": False, "error": "Geen globaal FACEBOOK_PAGE_TOKEN"}
    # /me/accounts geeft de pagina's van de gebruiker achter het token.
    r = await _g("GET", "me/accounts",
                 params={"fields": "id,name,access_token,category,fan_count",
                         "access_token": token, "limit": 100})
    if not r["ok"]:
        return {"success": False, "error": r["error"]}
    pages = r["json"].get("data", [])
    return {"success": True, "count": len(pages),
            "pages": [{"id": p.get("id"), "name": p.get("name"),
                       "category": p.get("category"), "fan_count": p.get("fan_count")}
                      for p in pages]}


# ─────────────────────────────────────────────────────────────────────────────
# INSTELLINGEN
# ─────────────────────────────────────────────────────────────────────────────

async def get_settings(site_name: Optional[str] = None) -> Dict[str, Any]:
    """Lees de huidige pagina-instellingen (publieke + beheer-velden)."""
    page_id, token = _token_for(site_name)
    fields = ("name,about,description,website,phone,emails,single_line_address,"
              "category,username,mission,products,general_info,bio,impressum,"
              "company_overview,location")
    r = await _g("GET", page_id, params={"fields": fields, "access_token": token})
    if r["ok"]:
        return {"success": True, "settings": r["json"], "site_name": site_name}
    return {"success": False, "error": r["error"], "site_name": site_name}


async def update_settings(site_name: Optional[str] = None, **changes) -> Dict[str, Any]:
    """Werk pagina-instellingen bij. Alleen velden uit PAGE_SETTINGS_WRITABLE."""
    page_id, token = _token_for(site_name)
    clean = {k: v for k, v in changes.items() if k in PAGE_SETTINGS_WRITABLE}
    if not clean:
        return {"success": False,
                "error": f"Geen schrijfbare velden opgegeven. Toegestaan: {sorted(PAGE_SETTINGS_WRITABLE)}"}
    payload = {**clean, "access_token": token}
    r = await _g("POST", page_id, data=payload)
    if r["ok"]:
        logger.info("✅ FB settings update OK — site=%s, velden=%s", site_name, list(clean))
        return {"success": True, "updated": list(clean), "site_name": site_name}
    return {"success": False, "error": r["error"], "site_name": site_name}


# ─────────────────────────────────────────────────────────────────────────────
# POSTS
# ─────────────────────────────────────────────────────────────────────────────

async def create_post(
    text: str,
    site_name: Optional[str] = None,
    link: Optional[str] = None,
    image_path: Optional[str] = None,
    scheduled_publish_time: Optional[int] = None,
    published: bool = True,
    query: Optional[str] = None,
    article_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Plaats een post (tekst / link / foto). Optioneel geplaned (unpublished + tijdstip).

    `query` en `article_url` zijn de FB→SEO-brug: ze koppelen de post aan het
    zoekwoord + artikel op de site, zodat fb_seo_impact.py de GSC-positie van de
    gelinkte pagina vóór/après de post kan meten. Worden alleen gelogd bij succes.
    """
    page_id, token = _token_for(site_name)
    async with httpx.AsyncClient() as client:
        if image_path:
            try:
                with open(image_path, "rb") as fh:
                    up = await client.post(
                        f"{GRAPH_API}/{page_id}/photos",
                        data={"message": text[:63000], "access_token": token},
                        files={"source": fh}, timeout=60,
                    )
                if up.status_code == 200:
                    pid = up.json().get("id", "")
                    from ..analytics.facebook_store import log_fb_post
                    log_fb_post(pid, site_name, query=query, article_url=article_url, message=text[:200])
                    url = f"https://www.facebook.com/{pid}"
                    _record_in_social_ledger(site_name, text, pid, url)
                    return {"success": True, "post_id": pid, "url": url, "site": site_name}
                logger.warning("FB foto-upload mislukt (%s), val terug op tekst/link", up.status_code)
            except Exception as e:
                logger.warning("FB foto-upload fout (%s), val terug op tekst/link", e)

        payload: Dict[str, Any] = {"message": text[:63000], "access_token": token,
                                   "published": "true" if published else "false"}
        if link:
            payload["link"] = link
        if scheduled_publish_time:
            payload["scheduled_publish_time"] = str(scheduled_publish_time)
            payload["published"] = "false"
        resp = await client.post(f"{GRAPH_API}/{page_id}/feed", data=payload, timeout=30)

    if resp.status_code == 200:
        result = resp.json()
        post_id = result.get("id", "")
        from ..analytics.facebook_store import log_fb_post
        log_fb_post(post_id, site_name, query=query, article_url=article_url, message=text[:200])
        logger.info("✅ FB post OK — site=%s, post_id=%s", site_name, post_id)
        url = f"https://www.facebook.com/{post_id}"
        _record_in_social_ledger(site_name, text, post_id, url)
        return {"success": True, "post_id": post_id, "url": url, "site": site_name}
    try:
        fb = resp.json().get("error", {})
        msg, code = fb.get("message", resp.text[:300]), fb.get("code")
    except Exception:
        msg, code = resp.text[:300], None
    if code == 190 or "expired" in msg.lower():
        return {"success": False, "error": f"TOKEN_EXPIRED: {msg[:200]}"}
    return {"success": False, "error": f"HTTP {resp.status_code}: {msg[:300]}"}


async def delete_post(post_id: str, site_name: Optional[str] = None) -> Dict[str, Any]:
    page_id, token = _token_for(site_name)
    r = await _g("DELETE", post_id, params={"access_token": token})
    if r["ok"]:
        return {"success": True, "post_id": post_id, "site_name": site_name}
    return {"success": False, "error": r["error"], "site_name": site_name}


async def get_posts(
    site_name: Optional[str] = None,
    limit: int = 20,
    with_metrics: bool = True,
) -> Dict[str, Any]:
    """Lijst recente posts met engagement-metrics (likes/comments/shares)."""
    page_id, token = _token_for(site_name)
    # 'type' is sinds Graph API v3.3 een deprecated post-veld (#12) en laat de
    # HELE call falen, niet alleen dat veld — attachments.media_type is de
    # vervanger die nog wél bestaat.
    fields = "id,message,created_time,attachments{media_type},permalink_url,full_picture"
    if with_metrics:
        fields += (",likes.summary(true),comments.summary(true),"
                   "shares,reactions.summary(true)")
    r = await _g("GET", f"{page_id}/posts",
                 params={"fields": fields, "limit": limit, "access_token": token})
    if not r["ok"]:
        return {"success": False, "error": r["error"], "site_name": site_name}

    posts = []
    for p in r["json"].get("data", []):
        likes = (p.get("likes") or {}).get("summary", {}).get("total_count", 0)
        comments = (p.get("comments") or {}).get("summary", {}).get("total_count", 0)
        shares = (p.get("shares") or {}).get("count", 0)
        reactions = (p.get("reactions") or {}).get("summary", {}).get("total_count", 0)
        media_type = ((p.get("attachments") or {}).get("data") or [{}])[0].get("media_type")
        posts.append({
            "id": p.get("id"),
            "message": (p.get("message") or "")[:500],
            "created_time": p.get("created_time"),
            "type": media_type,
            "permalink_url": p.get("permalink_url"),
            "likes": likes, "comments": comments, "shares": shares,
            "reactions": reactions,
            "engagement": likes + comments + shares + reactions,
        })
    return {"success": True, "count": len(posts), "posts": posts, "site_name": site_name}


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHTS / ANALYSE
# ─────────────────────────────────────────────────────────────────────────────

async def get_insights(
    site_name: Optional[str] = None,
    metrics: Optional[List[str]] = None,
    period: str = "day",
    days: int = 28,
) -> Dict[str, Any]:
    """Haal pagina-insights op (reach, impressions, engagement, fans)."""
    page_id, token = _token_for(site_name)
    if metrics is None:
        # De oorspronkelijke lijst (page_impressions, page_impressions_unique,
        # page_fans, page_fan_adds, page_content_activity) bestaat niet meer in
        # de huidige Graph API — Meta wijst dat af met "(#100) The value must
        # be a valid insights metric" en dat laat de HELE call falen, niet
        # alleen die velden. Livegetest tegen een echte pagina op 15 aug 2026.
        metrics = ["page_post_engagements", "page_views_total",
                   "page_follows", "page_daily_follows", "page_video_views"]
    since = (await _now_iso(days)) if days else None
    params: Dict[str, Any] = {
        "metric": ",".join(metrics),
        "period": period,
        "access_token": token,
    }
    if since:
        params["since"] = since
        params["until"] = await _now_iso(0)
    r = await _g("GET", f"{page_id}/insights", params=params)
    if not r["ok"]:
        return {"success": False, "error": r["error"], "site_name": site_name}
    series = []
    for blk in r["json"].get("data", []):
        metric = blk.get("name")
        vals = blk.get("values", [])
        total = sum((v.get("value", 0) for v in vals if isinstance(v.get("value"), (int, float))))
        series.append({"metric": metric, "total": total, "points": len(vals),
                       "last": vals[-1].get("value") if vals else None})
    return {"success": True, "insights": series, "site_name": site_name}


async def analyse_page(site_name: Optional[str] = None, days: int = 28) -> Dict[str, Any]:
    """Gecombineerd analyserapport: posts + insights + top-performers + groei."""
    info_r, posts_r, ins_r = await asyncio.gather(
        test_connection(site_name),
        get_posts(site_name, limit=50, with_metrics=True),
        get_insights(site_name, days=days),
    )
    if not info_r.get("success"):
        return {"success": False, "error": info_r.get("error"), "site_name": site_name}

    posts = posts_r.get("posts", []) if posts_r.get("success") else []
    insights = ins_r.get("insights", []) if ins_r.get("success") else []

    # Top posts op engagement
    top = sorted(posts, key=lambda p: p.get("engagement", 0), reverse=True)[:5]
    total_eng = sum(p.get("engagement", 0) for p in posts)
    avg_eng = round(total_eng / len(posts), 1) if posts else 0

    # Beste posting-dag + -uur (weekdag/uur) uit created_time. Binnen-snapshot
    # momentum: eerste helft vs tweede helft van het venster — dat is echte,
    # dag-1 inzicht (loopt de recente posting beter dan de oudere?).
    from collections import Counter
    import datetime as dt
    wd = Counter()
    wh = Counter()
    dated = []
    for p in posts:
        ct = p.get("created_time")
        if ct:
            try:
                d = dt.datetime.fromisoformat(ct.replace("Z", "+00:00"))
                wd[d.strftime("%A")] += 1
                wh[d.hour] += 1
                dated.append((d, p.get("engagement", 0)))
            except Exception:
                pass
    best_day = wd.most_common(1)[0][0] if wd else None
    best_hour = wh.most_common(1)[0][0] if wh else None
    hour_histogram = {str(h): c for h, c in sorted(wh.items())}

    # Window-momentum: split op chronologie in twee helften.
    momentum = None
    if len(dated) >= 4:
        dated.sort(key=lambda x: x[0])
        half = len(dated) // 2
        first = dated[:half]
        last = dated[half:]
        f_eng = sum(e for _, e in first)
        l_eng = sum(e for _, e in last)
        f_posts = len(first)
        l_posts = len(last)
        momentum = {
            "first_half_posts": f_posts, "first_half_engagement": f_eng,
            "last_half_posts": l_posts, "last_half_engagement": l_eng,
            "engagement_delta_pct": round((l_eng - f_eng) / f_eng * 100, 1) if f_eng else None,
            "trend": ("stijgend" if l_eng > f_eng else "dalend" if l_eng < f_eng else "stabiel"),
        }

    fans_now = next((i["last"] for i in insights if i["metric"] == "page_follows"), None)
    fan_adds = next((i["total"] for i in insights if i["metric"] == "page_daily_follows"), None)

    return {
        "success": True,
        "site_name": site_name,
        "page": info_r.get("page", {}),
        "window_days": days,
        "posts_analysed": len(posts),
        "total_engagement": total_eng,
        "avg_engagement_per_post": avg_eng,
        "best_posting_day": best_day,
        "best_posting_hour": best_hour,
        "hour_histogram": hour_histogram,
        "window_momentum": momentum,
        "fans_now": fans_now,
        "fan_adds_window": fan_adds,
        "top_posts": top,
        "insights_summary": insights,
        "insights_available": ins_r.get("success", False),
        # "0 posts"/"geen insights" mag nooit stil hetzelfde lezen als een
        # mislukte deelcall — anders leest een kapotte Graph-fetch als een
        # legitieme nulmeting (zie shared/websearch.py-conventie: nooit stil
        # een lege lijst bij een falende bron).
        "posts_error": None if posts_r.get("success") else posts_r.get("error"),
        "insights_error": None if ins_r.get("success") else ins_r.get("error"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# COMMENTS / INBOX
# ─────────────────────────────────────────────────────────────────────────────

async def get_comments(object_id: str, site_name: Optional[str] = None,
                       limit: int = 25) -> Dict[str, Any]:
    """Haal comments op een post/object (object_id = post-id)."""
    _, token = _token_for(site_name)
    r = await _g("GET", f"{object_id}/comments",
                 params={"fields": "id,message,created_time,from,like_count,"
                                    "comment_count,parent,can_hide,can_delete",
                         "limit": limit, "access_token": token})
    if not r["ok"]:
        return {"success": False, "error": r["error"]}
    return {"success": True,
            "comments": [{"id": c.get("id"), "message": c.get("message"),
                          "from": (c.get("from") or {}).get("name"),
                          "created_time": c.get("created_time"),
                          "like_count": c.get("like_count", 0),
                          "can_hide": c.get("can_hide", False),
                          "can_delete": c.get("can_delete", False)}
                         for c in r["json"].get("data", [])]}


def _record_in_social_ledger(site_name: Optional[str], text: str, post_id: str, url: str) -> None:
    """Registreer een Deluxe-post ook in `social_posts` (best-effort). fb_posts +
    fb_seo_impact.py meten of hij werkte; dit houdt de Social Creatie-tab
    compleet — anders toont die alleen de helft van wat er echt gepost is."""
    try:
        from ...shared.social_content import record_external_post
        record_external_post(site_name or "", "facebook", text, post_id=post_id, url=url)
    except Exception as e:  # noqa: BLE001
        logger.debug("Social-ledger sync (Deluxe) mislukt (niet fataal): %s", e)


def _sync_gated_inbox(site_name: Optional[str], comment_id: str, body: str = "") -> None:
    """Als deze comment ook als concept in de gated Social-inbox staat, zet 'm op
    'sent' — anders blijft hij daar voor altijd 'pending_review' wachten terwijl
    er via deze Deluxe-tool allang gereageerd/opgeruimd is. Best-effort: nooit
    de eigenlijke actie laten falen op een boekhoud-detail."""
    try:
        from ...shared.social_inbox import mark_answered_externally
        mark_answered_externally(site_name or "", "facebook", comment_id, body)
    except Exception as e:  # noqa: BLE001
        logger.debug("Social-inbox sync (Deluxe) mislukt (niet fataal): %s", e)


async def reply_comment(comment_id: str, message: str,
                        site_name: Optional[str] = None) -> Dict[str, Any]:
    """Reageer op een comment (of op een andere comment als reply-thread)."""
    _, token = _token_for(site_name)
    r = await _g("POST", comment_id, data={"message": message[:1000], "access_token": token})
    if r["ok"]:
        _sync_gated_inbox(site_name, comment_id, message)
        try:
            from ...shared.outcomes import log_outcome
            log_outcome(project=site_name or "Social", action="facebook_reply_deluxe",
                        detail=f"Handmatig beantwoord via de Facebook-tab: {message[:150]}",
                        status="ok")
        except Exception:
            pass
        return {"success": True, "comment_id": r["json"].get("id"), "site_name": site_name}
    return {"success": False, "error": r["error"], "site_name": site_name}


async def hide_comment(comment_id: str, hide: bool = True,
                       site_name: Optional[str] = None) -> Dict[str, Any]:
    """Verberg (of toon) een comment."""
    _, token = _token_for(site_name)
    r = await _g("POST", comment_id, data={"is_hidden": "true" if hide else "false",
                                            "access_token": token})
    if r["ok"]:
        if hide:
            _sync_gated_inbox(site_name, comment_id, "[verborgen via de Facebook-tab]")
        return {"success": True, "hidden": hide, "site_name": site_name}
    return {"success": False, "error": r["error"], "site_name": site_name}


async def delete_comment(comment_id: str, site_name: Optional[str] = None) -> Dict[str, Any]:
    _, token = _token_for(site_name)
    r = await _g("DELETE", comment_id, params={"access_token": token})
    if r["ok"]:
        _sync_gated_inbox(site_name, comment_id, "[verwijderd via de Facebook-tab]")
        return {"success": True, "site_name": site_name}
    return {"success": False, "error": r["error"], "site_name": site_name}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _now_iso(days_ago: int = 0) -> int:
    import datetime as dt
    return int((dt.datetime.utcnow() - dt.timedelta(days=days_ago)).timestamp())

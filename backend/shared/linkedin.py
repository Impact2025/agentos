"""
LinkedIn service — post content naar LinkedIn via de User Generated Content API.

Ondersteunt per-site tokens opgeslagen in de `sites` tabel.
Valt terug op LINKEDIN_ACCESS_TOKEN in .env als globale fallback.

Token aanmaken: https://www.linkedin.com/developers/tools/oauth/token-generator
Scopes nodig: w_member_social (voor posten), openid profile email (voor URN detectie)
"""

import httpx
import logging
import asyncio
from typing import Optional, Dict, Any

from ..shared.config import LINKEDIN_ACCESS_TOKEN, LINKEDIN_USER_URN

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com/v2"

# Cached numeric member IDs per token (keyed by token[:16])
_member_id_cache: Dict[str, str] = {}


# ── Site-specific token lookup ─────────────────────────────────────

def _get_site_data(site_name: Optional[str] = None) -> tuple:
    """Haal (token, user_urn) op voor een site, of globaal als fallback."""
    token = LINKEDIN_ACCESS_TOKEN
    user_urn = LINKEDIN_USER_URN

    site_row = None
    if site_name:
        try:
            from ..domains.seo import sites as sites_service
            for s in sites_service.list_sites():
                norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
                if norm(s.get("name", "")) == norm(site_name):
                    site_full = sites_service.get_site(s["id"])
                    if site_full:
                        st = (site_full.get("linkedin_token") or "").strip()
                        su = (site_full.get("linkedin_user_urn") or "").strip()
                        if st:
                            token = st
                        if su:
                            user_urn = su
                        site_row = site_full
                    break
        except Exception:
            pass

    # Harde override: als de site LinkedIn-expliciet geblokkeerd heeft
    # (bv. DatingAssistent mag nooit via AgentOS op LinkedIn posten),
    # geven we leeg token/URN terug — zelfs als de globale .env-token aanwezig is.
    if site_row is not None and site_row.get("block_linkedin"):
        return "", ""

    return token, user_urn


def _make_headers(token: str) -> Dict[str, str]:
    if not token:
        raise ValueError("Geen LinkedIn access token.")
    return {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def is_configured(site_name: Optional[str] = None) -> bool:
    """Check of er een LinkedIn token is voor deze site (of globaal).

    Een site met `block_linkedin=1` in de sites-tabel geeft altijd False —
    ook al staat er een globale LINKEDIN_ACCESS_TOKEN in .env. Dit maakt het
    mogelijk om per project (bv. DatingAssistent) het platform uitsluitend
    offline te houden terwijl WeAreImpact er wél mag posten (met toestemming).
    """
    token, _ = _get_site_data(site_name)
    return bool(token) and token.strip() != ""


async def get_member_id(site_name: Optional[str] = None) -> str:
    """Haal het LinkedIn author-ID op (cached per token).

    BELANGRIJK — OpenID vs numeriek:
    Een token mét `openid`-scope levert via `/v2/userinfo` een NIET-numerieke
    `sub` (bv. 'hvqZoVM8Mm'). Voor dat type token is de juiste author-URN
    `urn:li:person:{sub}` — en die werkt wél op de UGC-API (ondanks de regex
    die alleen member/company zou toelaten). Een token zónder openid-scope
    geeft geen userinfo (403) en moet een numeriek `urn:li:member:{id}` hebben,
    op te halen via `/v2/me` (r_liteprofile) of handmatig in .env.

    We detecteren het type hier en geven de *kloppende* ID terug; de caller
    (post_update) bouwt de URN op basis van `get_author_urn()`.

    Resolutievolgorde:
    1. Opgeslagen 'linkedin_user_urn' in sites DB of .env
    2. /userinfo endpoint (openid scope → sub field)
    3. /me endpoint (r_liteprofile scope → id field)
    """
    token, stored_urn = _get_site_data(site_name)

    if not token:
        raise ValueError("Geen LinkedIn access token.")

    cache_key = token[:16]
    if cache_key in _member_id_cache:
        return _member_id_cache[cache_key]

    # 1. Opgeslagen URN — accepteer alle drie de vormen
    if stored_urn:
        if stored_urn.startswith("urn:li:person:"):
            # OpenID-sub (niet-numeriek) — gecached + direct bruikbaar
            _member_id_cache[cache_key] = stored_urn.replace("urn:li:person:", "")
            return _member_id_cache[cache_key]
        if stored_urn.startswith("urn:li:member:"):
            _member_id_cache[cache_key] = stored_urn.replace("urn:li:member:", "")
            return _member_id_cache[cache_key]
        if stored_urn.isdigit():
            _member_id_cache[cache_key] = stored_urn
            return stored_urn

    # 2. Auto-detect via LinkedIn API (volgorde: openid eerst)
    headers = _make_headers(token)
    # (url, veld) — userinfo geeft de openid-sub, me geeft numeriek id
    endpoints = [
        (f"{API_BASE}/userinfo", "sub"),
        (f"{API_BASE}/me", "id"),
    ]
    for url, field in endpoints:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                val = data.get(field, "")
                if val:
                    _member_id_cache[cache_key] = val
                    logger.info(f"LinkedIn author ID resolved: {val} (via {url[:50]})")
                    return val
        except Exception as e:
            logger.debug(f"LinkedIn {url} failed: {e}")

    raise ValueError(
        "Kan LinkedIn author ID niet ophalen via API. "
        "Zorg dat het token scopes heeft: openid, profile, email "
        "of geef het numerieke member ID op.\n\n"
        "Vind je member ID:\n"
        "1. Ga naar linkedin.com/in/jouw-profiel\n"
        "2. View page source (Ctrl+U)\n"
        "3. Zoek naar 'memberId' of een 10+ cijferig nummer\n"
        "4. Zet LINKEDIN_USER_URN=urn:li:member:dat_nummer in .env"
    )


def _is_openid_sub(val: str) -> bool:
    """Een OpenID-sub is niet-numeriek (bv. 'hvqZoVM8Mm'); een numeriek
    member-ID is 7-10 cijfers. Hiermee kiezen we de juiste author-URN."""
    return bool(val) and not val.isdigit()


# Gecachte author-URN per token-hash — voorkomt dat elke post een
# /userinfo- of /me-call doet (LinkedIn ratelimited daarop bij verse tokens).
_author_urn_cache: Dict[str, str] = {}


async def get_author_urn(site_name: Optional[str] = None) -> str:
    """Geef de kloppende author-URN voor dit token.

    OpenID-token  → urn:li:person:{sub}   (werkt op UGC-API)
    Numeriek token → urn:li:member:{id}   (klassiek)
    Gecached per token zodat post_update() niet bij élke post de API raadt.
    """
    token, _ = _get_site_data(site_name)
    cache_key = (token or "")[:16]
    if cache_key in _author_urn_cache:
        return _author_urn_cache[cache_key]
    val = await get_member_id(site_name)
    urn = f"urn:li:person:{val}" if _is_openid_sub(val) else f"urn:li:member:{val}"
    _author_urn_cache[cache_key] = urn
    return urn


async def post_update(text: str, article_url: Optional[str] = None,
                      site_name: Optional[str] = None) -> Dict[str, Any]:
    """Post een LinkedIn update voor een specifieke site."""
    token, _ = _get_site_data(site_name)
    if not token:
        return {"success": False, "error": f"Geen LinkedIn token voor {site_name or 'globale config'}"}

    author = await get_author_urn(site_name)
    member_id = author.split(":")[-1]  # voor logging/compatibiliteit
    commentary = text[:3000]
    headers = _make_headers(token)

    content = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": commentary},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }

    if article_url:
        # LinkedIn kan (met een personal token) GEEN reactie plaatsen, dus de
        # link blijft in de body — maar WEL met UTM (standaard: meetbaarheid).
        # Opmerking: het "vastpinnen" van een eerste reactie is op LinkedIn
        # alleen handmatig mogelijk; de API ondersteunt geen comment-post.
        _u = article_url
        if "utm_" not in _u:
            _slug = (site_name or "").lower().replace(" ", "").replace("-", "").replace("_", "")
            _camp = "social" if not _slug else f"{_slug}_social"
            _sep = "&" if "?" in _u else "?"
            _u = f"{_u}{_sep}utm_source=linkedin&utm_medium=organic&utm_campaign={_camp}"
        content["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "ARTICLE"
        content["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
            {
                "status": "READY",
                "description": {"text": commentary[:256]},
                "originalUrl": _u,
            }
        ]

    # LinkedIn ratelimit op verse tokens geeft soms 403 (ipv 429) op /author —
    # transient. Eén retry met korte backoff vangt dat op zonder valse fout.
    last_err = ""
    for attempt in range(2):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/ugcPosts",
                headers=headers,
                json=content,
                timeout=30,
            )
        if resp.status_code == 201:
            break
        last_err = resp.text[:500]
        if attempt == 0:
            logger.warning("LinkedIn post 403/transient — 1 retry na backoff: %s", last_err[:120])
            await asyncio.sleep(8)
    else:
        # beide pogingen mislukt
        error_body = last_err
        logger.error(f"❌ LinkedIn post failed ({site_name}): {resp.status_code}")
        return {"success": False, "error": f"HTTP {resp.status_code}: {error_body}"}

    if resp.status_code == 201:
        result = resp.json()
        post_id = result.get("id", "")
        logger.info(f"✅ LinkedIn post OK — site={site_name}, post_id={post_id}")
        # Log naar lokale analyse-tabel (statistieken via API zijn niet beschikbaar
        # voor een personal token, dus houden we onze eigen posts bij).
        try:
            from ..shared.database import get_conn
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO linkedin_posts(id, project, text, url, posted_at, source) "
                    "VALUES(?,?,?,?,datetime('now'),?)",
                    (post_id, site_name or "", text,
                     f"https://www.linkedin.com/feed/update/{post_id}", "api"),
                )
        except Exception as e:
            logger.debug("linkedin_posts log mislukt: %s", e)
        return {
            "success": True,
            "post_id": post_id,
            "url": f"https://www.linkedin.com/feed/update/{post_id}",
            "site": site_name,
        }

    error_body = resp.text[:500]
    logger.error(f"❌ LinkedIn post failed ({site_name}): {resp.status_code}")
    return {"success": False, "error": f"HTTP {resp.status_code}: {error_body}"}


async def share_article(url: str, title: str, description: str = "",
                        site_name: Optional[str] = None) -> Dict[str, Any]:
    """Deel een artikel naar LinkedIn voor een specifieke site."""
    text = title
    if description:
        text += f"\n\n{description}"
    text += f"\n\n{url}"
    return await post_update(text, article_url=url, site_name=site_name)


async def get_my_posts(site_name: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """Haal je eigen recente LinkedIn-posts op mét statistieken.

    BELANGRIJK — API-grens: de LinkedIn Posts API (statistieken lezen van
    eigen posts: impressions/likes/comments) vereist Marketing/partner-toegang.
    Een gewone personal token (w_member_social + openid, zoals Vincent die
    heeft) krijgt GEEN toegang tot /rest/posts of /v2/shares?q=owners — die
    geven 404 / 400. Daarom lezen we de analytics uit onze EIGEN database:
    elke post die AgentOS plaatst (via post_update / publish_pack) wordt
    hieronder gelogd, zodat je wél een overzicht + analyse in AgentOS hebt.

    We proberen eerst de API (voor wie wél partner-toegang heeft), en vallen
    anders terug op de lokale `linkedin_posts`-tabel.
    """
    token, _ = _get_site_data(site_name)
    if not token:
        return {"success": False, "error": f"Geen LinkedIn token voor {site_name or 'globale config'}",
                "posts": [], "source": "none"}

    # 1) API-poging (werkt alleen met partner/marketing token)
    author = await get_author_urn(site_name)
    headers = _make_headers(token)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{API_BASE}/posts",
                headers={**headers, "LinkedIn-Version": "202401"},
                params={
                    "author": author,
                    "pageSize": min(limit, 50),
                    "fields": "id,commentary,createdAt,permalink,author,"
                              "stats(impressionCount,likeCount,commentCount,repostCount)",
                },
                timeout=25,
            )
        if r.status_code == 200:
            out = []
            for el in r.json().get("elements", []):
                stats = (el.get("stats") or {})
                out.append({
                    "id": el.get("id", ""),
                    "text": (el.get("commentary") or {}).get("text", ""),
                    "created": el.get("createdAt", ""),
                    "url": el.get("permalink", ""),
                    "stats": {
                        "impressions": stats.get("impressionCount", 0),
                        "likes": stats.get("likeCount", 0),
                        "comments": stats.get("commentCount", 0),
                        "reposts": stats.get("repostCount", 0),
                    },
                })
            return {"success": True, "count": len(out), "posts": out,
                    "author_urn": author, "source": "api"}
    except Exception:
        pass  # API niet beschikbaar — val terug op lokale logging

    # 2) Lokale fallback: wat AgentOS zélf heeft geplaatst
    try:
        from ..shared.database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM linkedin_posts ORDER BY posted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        posts = [dict(r) for r in rows]
        return {"success": True, "count": len(posts), "posts": posts,
                "author_urn": author, "source": "local",
                "note": "API-statistieken vereisen partner-toegang; tonen lokale AgentOS-posts."}
    except Exception as e:
        return {"success": False, "error": str(e)[:300], "posts": [], "source": "none"}

"""
Facebook service — post content naar een Facebook-pagina via de Graph API.

Ondersteunt per-site page-id/token opgeslagen in de `sites` tabel.
Valt terug op FACEBOOK_PAGE_ID/FACEBOOK_PAGE_TOKEN in .env als globale fallback.

Token aanmaken: developers.facebook.com > je app > Graph API Explorer
Nodig: een Page Access Token (geen user token) met scope `pages_manage_posts`
+ `pages_read_engagement`. Voor langdurig gebruik: wissel om naar een
long-lived page token (verloopt niet, tenzij de app-review verloopt).
"""

import httpx
import logging
import os
import re
from typing import Optional, Dict, Any

from .config import FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN

logger = logging.getLogger(__name__)

# ── Leeftijdsframing-gate (21 aug 2026) ────────────────────────────────────
# DatingAssistent.nl is de ENIGE site die via de `sites`-tabel loopt en dus de
# algemene hoofdpagina; de 30+/40+/50+-doelgroeppagina's bestaan bewust NIET
# als eigen sites-rij (die worden buiten deze module om beheerd door
# scripts/da_post_engine.py, met eigen page-id's per doelgroep). Aanleiding:
# op 21 aug 2026 landde een 40+-gerichte post ("al een heel leven achter je")
# via het reguliere pad op de hoofdpagina omdat de sites-rij toevallig naar de
# 30+-pagina wees. Deze gate blokkeert leeftijdsframing op de hoofdpagina
# voortaan hard, deterministisch (geen LLM — een gate die zelf een gateway
# nodig heeft valt stil precies wanneer je hem nodig hebt).
_GEEN_LEEFTIJDSFRAMING_SITES = {"datingassistent"}
_LEEFTIJDSPATROON = re.compile(
    r"\b\d{2}\+|\bop je \d{2}e\b|\bna je \d{2}e\b|\bvanaf je \d{2}e\b|"
    r"\b(twintiger|dertiger|veertiger|vijftiger|zestiger)s?\b",
    re.IGNORECASE,
)


def _squash(x: Optional[str]) -> str:
    return (x or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def check_age_targeting(site_name: Optional[str], text: str) -> Optional[str]:
    """Foutmelding als `text` leeftijdsframing bevat voor de algemene
    DatingAssistent-pagina; anders None. Gebruikt door élke Facebook-post-ingang
    (deze module én facebook/agent.py) zodat er maar één plek is die dit weet."""
    if _squash(site_name) not in _GEEN_LEEFTIJDSFRAMING_SITES:
        return None
    hit = _LEEFTIJDSPATROON.search(text or "")
    if not hit:
        return None
    return (f"LEEFTIJDSFRAMING: '{hit.group(0)}' hoort niet op de algemene "
            f"DatingAssistent-pagina (die is voor alle leeftijden). "
            f"Leeftijdsspecifieke content gaat via de 30+/40+/50+-campagne "
            f"(scripts/da_post_engine.py), niet via dit pad.")

# v19.0 nog wel bereikbaar (Meta redirect't stil naar de actuele versie),
# maar zit al voorbij zijn 2-jaars deprecatievenster (jan 2024) — vastgepind
# op de versie die bij livetest (15 aug 2026) daadwerkelijk bediend werd
# (zichtbaar in de paging-URL's van een insights-call).
GRAPH_API = "https://graph.facebook.com/v25.0"


def build_utm_url(url: str, source: str, site_name: Optional[str] = None,
                  campaign: Optional[str] = None) -> str:
    """Voeg UTM-zoekparams toe aan een URL zodat GA4 de social-bron meet.

    Conform de universele social-post standaard (skill: social-post-standard):
    elke link die in een reactie (of body) belandt krijgt
    utm_source=<platform>, utm_medium=organic en utm_campaign=<project>_<naam>.
    Zonder UTM is een post niet te meten en dus niet te optimaliseren.
    """
    if not url:
        return url
    if "utm_" in url:
        return url  # al van UTM voorzien — niet dubbel plakken
    slug = (site_name or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    camp = campaign or ("social" if not slug else f"{slug}_social")
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source={source}&utm_medium=organic&utm_campaign={camp}"


def _get_site_data(site_name: Optional[str] = None) -> tuple:
    """Haal (page_id, token) op voor een site, of globaal als fallback."""
    page_id = FACEBOOK_PAGE_ID
    token = FACEBOOK_PAGE_TOKEN

    if site_name:
        try:
            from ..domains.seo import sites as sites_service
            for s in sites_service.list_sites():
                norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
                if norm(s.get("name", "")) == norm(site_name):
                    site_full = sites_service.get_site(s["id"])
                    if site_full:
                        sp = (site_full.get("facebook_page_id") or "").strip()
                        st = (site_full.get("facebook_page_token") or "").strip()
                        if sp:
                            page_id = sp
                        if st:
                            token = st
                    break
        except Exception:
            pass

    return page_id, token


def is_configured(site_name: Optional[str] = None) -> bool:
    page_id, token = _get_site_data(site_name)
    return bool(page_id) and bool(token)


async def post_update(text: str, article_url: Optional[str] = None,
                      site_name: Optional[str] = None,
                      image_path: Optional[str] = None,
                      query: Optional[str] = None,
                      cta_url: Optional[str] = None) -> Dict[str, Any]:
    """Post een update naar de Facebook-pagina van een specifieke site.

    Optioneel `image_path`: een lokaal bestand dat als foto wordt meegepost
    (Graph API 'source' multipart-upload — werkt zonder publieke host, in
    tegenstelling tot IG dat een publieke image_url vereist).

    Optioneel `query`: het zoekwoord waar deze post over gaat (als hij uit een
    datagedreven idee komt — social_auto._pick_grounded_idea). Samen met
    `article_url` voedt dit dezelfde FB→SEO-meetlus (fb_posts / fb_seo_impact.py)
    als de Facebook Deluxe-agent — anders was de auto-poster onzichtbaar voor de
    enige meting die zegt of een post ook daadwerkelijk iets opleverde.

    Optioneel `cta_url`: alléén gebruikt voor de eerste-reactie-link, nooit voor
    de FB→SEO-meting. Nodig omdat niet elke post een datagedreven `article_url`
    heeft (bv. een evergreen CTA-post) — zonder deze fallback bleef zo'n post
    zonder enige link, en schreef de tekstgenerator het domein soms als kale
    tekst in de caption i.p.v. in de comment (21 aug 2026, DatingAssistent.nl).
    De universele regel geldt voor élke ImpactOS-post: geen link in de body, wél
    in de eerste reactie.
    """
    page_id, token = _get_site_data(site_name)
    if not page_id or not token:
        return {"success": False, "error": f"Geen Facebook page-id/token voor {site_name or 'globale config'}"}
    guard_err = check_age_targeting(site_name, text)
    if guard_err:
        logger.error("❌ FB post geweigerd (%s): %s", site_name, guard_err)
        return {"success": False, "error": guard_err}

    def _log(post_id: str) -> None:
        if not post_id:
            return
        try:
            from ..domains.analytics.facebook_store import log_fb_post
            log_fb_post(post_id, site_name or "", query=query, article_url=article_url,
                        message=text[:200])
        except Exception as e:  # noqa: BLE001 — loggen mag de post zelf nooit breken
            logger.debug("FB-post loggen voor de impact-meting mislukt: %s", e)

    async with httpx.AsyncClient() as client:
        if image_path and os.path.exists(image_path):
            # Foto-post: upload de afbeelding eerst, deel daarna met de tekst.
            try:
                with open(image_path, "rb") as fh:
                    up = await client.post(
                        f"{GRAPH_API}/{page_id}/photos",
                        data={"message": text[:63000], "access_token": token},
                        files={"source": fh},
                        timeout=60,
                    )
                if up.status_code == 200:
                    pid = up.json().get("id", "")
                    logger.info(f"✅ FB foto-post OK — site={site_name}, post_id={pid}")
                    _log(pid)
                    # Standaard: link + UTM in eerste reactie (i.p.v. body).
                    # article_url wint (voedt ook de FB→SEO-meting); zonder dat
                    # valt terug op cta_url zodat élke post een linkje krijgt.
                    cid = ""
                    comment_url = article_url or cta_url
                    if comment_url:
                        cr = await comment_on_post(
                            pid, build_utm_url(comment_url, "facebook", site_name),
                            site_name=site_name)
                        cid = cr.get("comment_id", "")
                    return {"success": True, "post_id": pid, "comment_id": cid,
                            "url": f"https://www.facebook.com/{pid}", "site": site_name}
                # Val terug op tekst-only als de foto-upload faalt.
                logger.warning("FB foto-upload mislukt (%s), val terug op tekst-only: %s",
                               up.status_code, up.text[:200])
            except Exception as e:
                logger.warning("FB foto-upload fout (%s), val terug op tekst-only", e)

        # Universele social-post standaard: GEEN link in de body. De link
        # (met UTM) gaat in de EERSTE reactie via comment_on_post hieronder.
        payload: Dict[str, Any] = {"message": text[:63000], "access_token": token}
        resp = await client.post(f"{GRAPH_API}/{page_id}/feed", data=payload, timeout=30)

    if resp.status_code == 200:
        result = resp.json()
        post_id = result.get("id", "")
        logger.info(f"✅ Facebook post OK — site={site_name}, post_id={post_id}")
        _log(post_id)
        # Standaard: link + UTM in eerste reactie (i.p.v. body).
        # article_url wint (voedt ook de FB→SEO-meting); zonder dat valt terug
        # op cta_url zodat élke post een linkje krijgt.
        cid = ""
        comment_url = article_url or cta_url
        if comment_url:
            cr = await comment_on_post(
                post_id, build_utm_url(comment_url, "facebook", site_name),
                site_name=site_name)
            cid = cr.get("comment_id", "")
        return {
            "success": True,
            "post_id": post_id,
            "comment_id": cid,
            "url": f"https://www.facebook.com/{post_id}",
            "site": site_name,
        }
    else:
        try:
            err_json = resp.json()
            fb_msg = err_json.get("error", {}).get("message", resp.text[:300])
            fb_code = err_json.get("error", {}).get("code")
        except Exception:
            fb_msg, fb_code = resp.text[:300], None
        if fb_code == 190 or "session has expired" in fb_msg.lower() \
                or "error validating access token" in fb_msg.lower():
            logger.error(
                "❌ Facebook post mislukt (%s): access token verlopen/on­geldig "
                "(FB code %s). Verleng via developers.facebook.com → Graph API "
                "Explorer (Page Access Token, scope pages_manage_posts + "
                "pages_read_engagement), zet 'm in .env (FACEBOOK_PAGE_TOKEN) en "
                "herstart Impact OS.",
                site_name, fb_code,
            )
        else:
            logger.error(
                "❌ Facebook post mislukt (%s): HTTP %s — %s",
                site_name, resp.status_code, fb_msg[:300],
            )
        return {
            "success": False,
            "error": f"HTTP {resp.status_code}: {fb_msg[:500]}",
        }


async def get_page_info(site_name: Optional[str] = None) -> Dict[str, Any]:
    """Haal paginanaam op — gebruikt om de verbinding te testen."""
    page_id, token = _get_site_data(site_name)
    if not page_id or not token:
        raise ValueError("Geen Facebook page-id/token geconfigureerd.")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_API}/{page_id}", params={"fields": "name", "access_token": token}, timeout=15
        )
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def share_article(url: str, title: str, description: str = "",
                        site_name: Optional[str] = None) -> Dict[str, Any]:
    """Deel een artikel op de Facebook-pagina voor een specifieke site."""
    text = title
    if description:
        text += f"\n\n{description}"
    return await post_update(text, article_url=url, site_name=site_name)


async def comment_on_post(post_id: str, text: str,
                         site_name: Optional[str] = None,
                         token_override: Optional[str] = None) -> Dict[str, Any]:
    """Plaats een (first) comment op een eerder geplaatste post.

    Gebruikt door de auto-comment flow: de CTA-link + hashtags komen in de
    EERSTE reactie i.p.v. in de post-caption. Facebook onderdrukt bereik van
    posts mét uitgaande link in de caption; een link in de eerste reactie houdt
    het organische bereik hoog én geeft de CTA. Legaal, geen TOS-schending.

    `token_override`: voor pagina's zonder eigen sites-rij (bv. DatingAssistent
    30+/40+/50+, zie scripts/da_post_engine.py) — anders resolveert `site_name`
    hier naar de verkeerde pagina en faalt de comment op de post die zojuist
    op een ándere pagina geplaatst is.
    """
    page_id, token = _get_site_data(site_name)
    token = token_override or token
    if not post_id or not token:
        return {"success": False, "error": "Geen post_id of token"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GRAPH_API}/{post_id}/comments",
            data={"message": text[:6000], "access_token": token},
            timeout=30,
        )
    if resp.status_code == 200:
        cid = resp.json().get("id", "")
        logger.info("✅ FB comment OK — post=%s comment=%s", post_id, cid)
        return {"success": True, "comment_id": cid, "post_id": post_id}
    try:
        err = resp.json().get("error", {}).get("message", resp.text[:300])
    except Exception:
        err = resp.text[:300]
    logger.error("❌ FB comment mislukt (%s): %s", site_name, err[:200])
    return {"success": False, "error": f"HTTP {resp.status_code}: {err[:400]}"}


async def resolve_page_token(page_id: str) -> Optional[str]:
    """Vers page-token voor een specifieke page_id via /me/accounts, voor
    pagina's die bewust GEEN sites-rij hebben (de DA-leeftijdspagina's, zie
    check_age_targeting hierboven) en dus niet via _get_site_data/
    refresh_page_tokens ververst worden. Gebruikt door de Social Inbox
    (social_inbox.py:fb_fetch) zodat elke poll een geldig token heeft zonder
    dat het kortlevende (~1u) token ergens statisch bewaard hoeft te worden."""
    _, glob_token = _get_site_data(None)
    if not glob_token:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{GRAPH_API}/me/accounts",
                             params={"fields": "id,access_token", "access_token": glob_token, "limit": 200},
                             timeout=20)
    if r.status_code != 200:
        return None
    for acc in r.json().get("data", []):
        if acc.get("id") == page_id and acc.get("access_token"):
            return acc["access_token"]
    return None


async def refresh_page_tokens() -> Dict[str, str]:
    """Haal verse per-pagina access-tokens op uit /me/accounts en bewaar ze
    in de sites-tabel. Page-tokens zijn kortlevend (~1u); roep dit vlak vóór
    een post-ronde aan. Retourneert {site_name: page_token}.
    """
    from ..domains.seo import sites as sites_service
    glob_id, glob_token = _get_site_data(None)
    out = {}
    if not glob_token:
        return out
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{GRAPH_API}/me/accounts",
                             params={"fields": "id,access_token,name", "access_token": glob_token},
                             timeout=30)
    if r.status_code != 200:
        return out
    for acc in r.json().get("data", []):
        pid = acc.get("id", "")
        ptok = acc.get("access_token", "")
        if not pid or not ptok:
            continue
        for s in sites_service.list_sites():
            norm = lambda x: x.lower().replace(" ", "").replace("-", "").replace("_", "")
            if norm(s.get("name", "")) == norm(s.get("facebook_page_id", "")) or \
               s.get("facebook_page_id", "") == pid:
                sites_service.update_site(s["id"], {"facebook_page_id": pid, "facebook_page_token": ptok})
                out[s["name"]] = ptok
    return out


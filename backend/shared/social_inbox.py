"""
Social Inbox — lees + beantwoord sociale media in de merkstem van een project.

Gespiegeld aan de mail-helpdesk (domains/mail/): de agent haalt ongelezen
reacties/DM's/mentions op per kanaal, classificeert ze, en schrijft — waar
nodig — een concept-antwoord in Vincent's Schrijf-DNA. Alles landt in de
`social_inbox_msg`-tabel met status `pending_review`. De mens keurt in de UI
en pas dan gaat er iets het kanaal op. Nooit auto-antwoorden.

Kanalen:
  facebook  — Graph API: comments + DM (conversations). Posten via facebook.py.
  instagram — Graph API: comments op eigen posts + DM (IG Messaging). Posten via instagram.py.
  linkedin  — UGC API posten (linkedin.py). Reacties/DM vereisen partner-toegang:
              daarom een "plak-adapter" (manual=1): AgentOS toont het antwoord +
              "kopieer"/"open LinkedIn", géén nep-API-call.
  tiktok    — Display/Content Posting API (eigen geregistreerde app). Code klaar;
              actief zodra TIKTOK_CLIENT_KEY/SECRET + goedgekeurde app aanwezig zijn.

De drafter hergebruikt de mail-logica (drafter.py) met een korter, platform-
passend system-prompt, gevoed door Schrijf-DNA + project-SKILL.md.
"""
import asyncio
import json
import logging
import os
from typing import List, Dict, Optional

import httpx

from .config import OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL
from .database import get_conn
from .failures import describe_exception as _describe

logger = logging.getLogger(__name__)

PLATFORMS = ("linkedin", "facebook", "instagram", "tiktok")

# Max woorden per kanaal — LinkedIn nuchter-pro, IG warm/kort, TT casual/micro.
_PLATFORM_TONE = {
    "linkedin": "Schrijf nuchter-professioneel, eerste persoon, zonder jargon. Max 60 woorden.",
    "facebook": "Schrijf warm en menselijk, alsof je tegen een bekende praat. Max 50 woorden.",
    "instagram": "Schrijf warm en kort, emoji-light (max 1 emoji). Max 40 woorden.",
    "tiktok": "Schrijf casual en kort, alsof je met een vriend praat. Max 30 woorden, geen hashtag-salvo.",
}


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


# ── Classificatie (gedeeld met mail, aangepast voor social) ─────────────────

_SPAM_HINTS = (
    "kopen", "followers kopen", "crypto", "casino", "loan", "sex", "dm voor",
    "click here", "gratis iphone", "win een",
)
_QUESTION_HINTS = (
    "?", "hoe", "wat", "kan", "kunt", "help", "vraag", "werkt", "niet",
    "waar", "wanneer", "prijs", "kost", "kopen", "bestellen", "leveren",
    "account", "inloggen", "reset", "probleem", "fout",
)
_PRAISE_HINTS = (
    "top", "super", "mooi", "geweldig", "dankjewel", "bedankt", "lieve",
    "leuk", "fijn", "troost", "ontroerend", "prachtig", "thanks", "love this",
)


def classify(text: str) -> str:
    t = (text or "").lower()
    if any(h in t for h in _SPAM_HINTS):
        return "spam"
    if "?" in t or any(h in t for h in _QUESTION_HINTS):
        return "question"
    if any(h in t for h in _PRAISE_HINTS):
        return "praise"
    # klacht: negatieve signalen zonder vraagteken
    if any(h in t for h in ("niet", "fout", "werkt", "probleem", "klacht", "boos", "slecht")):
        return "complaint"
    return "other"


# ── Drafter (merkstem, hergebruikt OpenModel-flash + Claude-vangnet) ────────

SOCIAL_SYSTEM_TEMPLATE = (
    "Je bent de social-media-stem van {brand}. "
    "Schrijf als de eigenaar van {brand} (Vincent van Munster) — eerste persoon "
    "(ik/wij), warm en nuchter, geen robot-taal, geen uitroeptekens-geweld. "
    "Je kent de Schrijf-DNA-regels: direct, geen jargon, mens centraal, "
    "technologie als stille achtergrond. "
    "{tone}\n"
    "Antwoord in de taal van de klant (herken NL/EN automatisch). "
    "Verzin geen prijzen, features of feiten die niet in de merkcontext staan. "
    "Weet je iets niet? Zeg het eerlijk en bied aan het te checken. "
    "Geen aandachtstreepjes (— / –). Geen bullet lists in het antwoord.\n"
)


def _sync_openmodel(system: str, user: str) -> str:
    url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
    payload = {
        "model": OPENMODEL_MODEL or "deepseek-v4-flash",
        "max_tokens": 400,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            url,
            headers={
                "x-api-key": OPENMODEL_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        if resp.status_code == 403 and "quota" in resp.text.lower():
            from .outcomes import note_llm_quota_exhausted
            note_llm_quota_exhausted(backend="openmodel", model=payload["model"], route="social")
            raise RuntimeError("OpenModel-quota op")
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage") or {}
    if usage:
        from .outcomes import log_llm_usage
        log_llm_usage(
            backend="openmodel", model=payload["model"], route="social",
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        )
    if "content" in data:
        return "".join(p.get("text", "") for p in data["content"] if p.get("type") == "text")
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return data.get("text", "")


def draft_reply(platform: str, brand_context: str, msg_text: str,
                author_name: str = "", thread: str = "") -> str:
    """Schrijf een concept-antwoord in de merkstem. Bij geen backend: leesbare
    placeholder (review-gate vangt dit op, net als bij mail)."""
    brand = brand_context or "dit project"
    tone = _PLATFORM_TONE.get(platform, _PLATFORM_TONE["facebook"])
    system = SOCIAL_SYSTEM_TEMPLATE.format(brand=brand, tone=tone)
    user = ""
    if thread:
        user += f"— THREAD (oms oud -> nieuw) —\n{thread}\n\n"
    user += f"Van: {author_name or 'iemand'}\n\n{msg_text}\n\nSchrijf alleen het antwoord."
    if not OPENMODEL_API_KEY:
        return (
            f"[Concept niet gegenereerd (geen LLM-backend). Beantwoord handmatig.]\n\n"
            f"Origineel: {msg_text}"
        )
    try:
        return _sync_openmodel(system, user).strip()
    except Exception as e:
        logger.warning("Social draft mislukt: %s", e)
        return f"[Concept niet gegenereerd: {e}. Beantwoord handmatig.]\n\nOrigineel: {msg_text}"


# ── Adapter-laag: fetch_new + post_reply per platform ───────────────────────
#
# Elke adapter krijgt de `creds`-dict (uit social_inboxes.creds_json) en het
# platform. fetch_new retourneert een lijst raw-messages; de caller doet
# dedupe + classify + draft + insert. post_reply plaatst het goedgekeurde
# antwoord en retourneert {"success": bool, "url"?: str, "error"?: str}.

def _creds(inbox: dict) -> dict:
    raw = inbox.get("creds_json") or "{}"
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


# ── Facebook ────────────────────────────────────────────────────────────────

_FB_GRAPH = "https://graph.facebook.com/v19.0"
# Tijdstip (epoch) van de laatste 'token verlopen'-WARNING — throttlet de
# 30-minuten-spam van een verlopen FB-token tot max. 1× per uur.
_FB_TOKEN_WARN_TS = 0.0


class SocialAuthError(RuntimeError):
    """Het kanaal wees ons af: token verlopen, ingetrokken of te weinig rechten.

    Bewust een eigen type. Een verlopen token is het tegenovergestelde van een
    netwerk-blip: opnieuw proberen lost niets op, alleen een mens die het token
    vernieuwt doet dat. Vóór 25 jul 2026 gaf de fetch bij zo'n 400 stil `[]`
    terug — het IG-token van BewaardVoorJou was toen al twaalf dagen dood zonder
    dat iemand het zag, terwijl een onschuldige nachtelijke TLS-blip wél een
    rode kaart opleverde.
    """


def _graph_auth_error(resp: "httpx.Response") -> Optional[SocialAuthError]:
    """Vertaal een Graph-foutantwoord naar een SocialAuthError, of None als dit
    geen authenticatieprobleem is (dan hoort het niet bij de mens thuis)."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:  # noqa: BLE001 — een niet-JSON body is zelf al het signaal
        err = {}
    code = err.get("code")
    msg = (err.get("message") or resp.text[:200] or "").strip()
    if resp.status_code in (401, 403) or code in (190, 102, 10, 200) or "OAuth" in str(err.get("type", "")):
        return SocialAuthError(msg or f"HTTP {resp.status_code} van het kanaal")
    return None


async def fb_fetch(inbox: dict) -> List[dict]:
    c = _creds(inbox)
    page_id = c.get("page_id")
    token = c.get("token")
    if not page_id or not token:
        # Val terug op globale config
        from . import facebook as fb_svc
        if fb_svc.is_configured():
            page_id, token = fb_svc._get_site_data(inbox.get("project"))
    if not page_id or not token:
        return []
    out: List[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        # Reacties op de pagina's posts. Een Page heeft GEEN directe /comments
        # edge (Graph geeft "nonexisting field comments"); comments hangen onder
        # posts. Haal daarom recente posts op mét genest comments-veld, en vlak
        # dat uit tot losse berichten. Vereist een PAGE-token (geen user-token).
        try:
            r = await client.get(
                f"{_FB_GRAPH}/{page_id}/posts",
                params={
                    "access_token": token,
                    "fields": "id,permalink_url,comments.limit(50){id,message,from,created_time}",
                    "limit": 25,
                },
            )
            if r.status_code == 200:
                for post in r.json().get("data", []):
                    post_url = post.get("permalink_url", "")
                    for cm in (post.get("comments") or {}).get("data", []):
                        if not cm.get("message"):
                            continue
                        out.append({
                            "external_id": cm.get("id", ""),
                            "author_name": (cm.get("from") or {}).get("name", ""),
                            "author_handle": (cm.get("from") or {}).get("id", ""),
                            "text": cm.get("message", ""),
                            "parent_url": post_url,
                            "thread": "",
                        })
            else:
                # Een verlopen/ongeldig token (OAuthException code 190) blijft
                # anders elke scheduler-tick (30 min) dezelfde WARNING spammen.
                # Log 'm daarom max. 1× per uur zodat de logs leesbaar blijven —
                # de gebruiker moet het Page-token vernieuwen om dit echt op te
                # lossen.
                import time as _time
                _body = r.text[:200]
                auth_err = _graph_auth_error(r)
                if auth_err is not None:
                    global _FB_TOKEN_WARN_TS
                    _now = _time.time()
                    if _now - _FB_TOKEN_WARN_TS > 3600:
                        _FB_TOKEN_WARN_TS = _now
                        logger.warning(
                            "FB posts fetch: access token verlopen/ongeldig — "
                            "vernieuw het Page-token (%s)", auth_err)
                    # Doorgooien, niet stil inslikken: alleen een mens kan dit
                    # oplossen, dus hoort het in het Actiecentrum te staan.
                    raise auth_err
                logger.warning("FB posts fetch HTTP %s: %s", r.status_code, _body)
        except SocialAuthError:
            raise
        except Exception as e:
            logger.warning("FB comments fetch: %s", _describe(e))
        # DM's (conversations) — vereist pages_messaging scope
        try:
            r = await client.get(
                f"{_FB_GRAPH}/{page_id}/conversations",
                params={"access_token": token, "fields": "id,messages{message,from,id,created_time}", "limit": 20},
            )
            if r.status_code == 200:
                for conv in r.json().get("data", []):
                    for m in (conv.get("messages") or {}).get("data", []):
                        out.append({
                            "external_id": f"dm_{m.get('id','')}",
                            "author_name": (m.get("from") or {}).get("name", "DM"),
                            "author_handle": (m.get("from") or {}).get("id", ""),
                            "text": m.get("message", ""),
                            "parent_url": "",
                            "thread": "",
                        })
        except Exception as e:
            logger.debug("FB DM fetch (mogelijk geen scope): %s", e)
    return out


async def fb_post_reply(inbox: dict, msg: dict, text: str) -> dict:
    c = _creds(inbox)
    token = c.get("token") or ""
    if not token:
        # Val terug op globale config (page-token uit .env)
        from . import facebook as fb_svc
        if fb_svc.is_configured():
            _, token = fb_svc._get_site_data(inbox.get("project"))
    if not token:
        return {"success": False, "error": "Geen FB-token"}
    async with httpx.AsyncClient(timeout=30) as client:
        # Reactie onder de originele comment (parent_id = msg.external_id)
        parent = msg.get("external_id", "")
        if parent and not parent.startswith("dm_"):
            r = await client.post(
                f"{_FB_GRAPH}/{parent}/comments",
                data={"message": text[:1000], "access_token": token},
            )
        else:
            return {"success": False, "error": "FB DM-antwoord vereist extra scope (pages_messaging)"}
        if r.status_code == 200:
            cid = r.json().get("id", "")
            return {"success": True, "url": f"https://facebook.com/{cid}"}
        return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


# ── Instagram ────────────────────────────────────────────────────────────────

async def ig_fetch(inbox: dict) -> List[dict]:
    c = _creds(inbox)
    ig_id = c.get("ig_id")
    token = c.get("token")
    if not ig_id or not token:
        from . import instagram as ig_svc
        if ig_svc.is_configured():
            ig_id, token = ig_svc._get_site_data(inbox.get("project"))
    if not ig_id or not token:
        return []
    out: List[dict] = []
    # Transiente netwerk-blips (DNS-timeout, connect-reset, 5xx) mogen géén
    # harde 'WACHT OP JOU'-fout opleveren — die horen te verdwijnen bij de
    # volgende poll. We proberen het daarom een paar keer met korte backoff
    # voordat we de fout doorsluizen naar run_inbox().
    _last_err: Exception | None = None
    for _attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Media van het account
                m = await client.get(
                    f"{_FB_GRAPH}/{ig_id}/media",
                    params={"access_token": token, "fields": "id,permalink,caption", "limit": 30},
                )
                if m.status_code != 200:
                    auth_err = _graph_auth_error(m)
                    if auth_err is not None:
                        # Verlopen IG-token: geen retry (helpt niet), wél zicht-
                        # baar maken. Stil `[]` teruggeven hield dit twaalf dagen
                        # verborgen.
                        raise auth_err
                    logger.warning("IG media fetch HTTP %s: %s", m.status_code, m.text[:200])
                    return out
                for media in m.json().get("data", []):
                    # Reacties op elk media-object
                    rc = await client.get(
                        f"{_FB_GRAPH}/{media['id']}/comments",
                        params={"access_token": token, "fields": "id,text,username,timestamp,permalink", "limit": 50},
                    )
                    if rc.status_code != 200:
                        continue
                    for cm in rc.json().get("data", []):
                        out.append({
                            "external_id": cm.get("id", ""),
                            "author_name": cm.get("username", ""),
                            "author_handle": cm.get("username", ""),
                            "text": cm.get("text", ""),
                            "parent_url": media.get("permalink", ""),
                            "thread": "",
                        })
            return out
        except SocialAuthError:
            # Een afgewezen token wordt niet beter van nog twee pogingen.
            raise
        except Exception as e:  # noqa: BLE001 — we loggen + retrien bewust
            _last_err = e
            from .failures import is_transient as _is_transient_err
            if not _is_transient_err(e) or _attempt == 2:
                break
            logger.warning(
                "IG fetch poging %d mislukt (transient), opnieuw: %s",
                _attempt + 1, _describe(e),
            )
            await asyncio.sleep(2 * (_attempt + 1))
    if _last_err is not None:
        logger.warning("IG fetch: %s", _describe(_last_err))
        raise _last_err
    return out


async def ig_post_reply(inbox: dict, msg: dict, text: str) -> dict:
    c = _creds(inbox)
    token = c.get("token") or ""
    if not token:
        from . import instagram as ig_svc
        if ig_svc.is_configured():
            _, token = ig_svc._get_site_data(inbox.get("project"))
    if not token:
        return {"success": False, "error": "Geen IG-token"}
    parent = msg.get("external_id", "")
    if not parent:
        return {"success": False, "error": "Geen parent-comment"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_FB_GRAPH}/{parent}/replies",
            data={"message": text[:1000], "access_token": token},
        )
        if r.status_code == 200:
            cid = r.json().get("id", "")
            return {"success": True, "url": f"https://instagram.com/p/{cid}"}
        return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


# ── LinkedIn ────────────────────────────────────────────────────────────────

async def li_fetch(inbox: dict) -> List[dict]:
    # Reacties/DM vereisen Marketing/Compliance partner-toegang — niet voor een
    # solo founder. Daarom: leeg ophalen (geen nep-data), en de UI biedt een
    # "plak-reactie"-adapter voor handmatig antwoorden op zichtbare reacties.
    return []


async def li_post_reply(inbox: dict, msg: dict, text: str) -> dict:
    # LinkedIn toestaat GEEN comment-reply via de standaard UGC API zonder
    # partner-toegang. We markeren dit in de UI als 'manual' (plak-adapter).
    return {"success": False, "error": "manual", "manual": True}


# ── TikTok ───────────────────────────────────────────────────────────────────

async def tiktok_fetch(inbox: dict) -> List[dict]:
    c = _creds(inbox)
    if not c.get("client_key") or not c.get("client_secret"):
        # Geen geregistreerde app — stil (geen fout in de log, want verwacht).
        return []
    # Een geregistreerde TikTok-app kan via /comment/list de comments op eigen
    # video's ophalen. Hier de placeholder voor de echte implementatie zodra de
    # app is goedgekeurd (display + video.list + comment scopes).
    logger.info("TikTok fetch: app-gegevens gevonden maar comment-endpoint nog niet geactiveerd.")
    return []


async def tiktok_post_reply(inbox: dict, msg: dict, text: str) -> dict:
    c = _creds(inbox)
    if not c.get("client_key"):
        return {"success": False, "error": "TikTok-app niet geconfigureerd (TIKTOK_CLIENT_KEY ontbreekt)"}
    return {"success": False, "error": "manual", "manual": True}


# ── Dispatcher ───────────────────────────────────────────────────────────────

_ADAPTERS = {
    "facebook": (fb_fetch, fb_post_reply),
    "instagram": (ig_fetch, ig_post_reply),
    "linkedin": (li_fetch, li_post_reply),
    "tiktok": (tiktok_fetch, tiktok_post_reply),
}


async def fetch_new(inbox: dict) -> List[dict]:
    fn = _ADAPTERS.get(inbox.get("platform"), (None, None))[0]
    if not fn:
        return []
    try:
        return await fn(inbox)
    except Exception:
        logger.exception("Social fetch mislukt voor %s/%s", inbox.get("project"), inbox.get("platform"))
        raise


async def post_reply(inbox: dict, msg: dict, text: str) -> dict:
    fn = _ADAPTERS.get(inbox.get("platform"), (None, None))[1]
    if not fn:
        return {"success": False, "error": "Onbekend platform"}
    return await fn(inbox, msg, text)


# ── Runner (aangeroepen vanuit de scheduler, per inbox) ─────────────────────

async def run_inbox(inbox_id: str) -> int:
    """Haal ongelezen berichten op, classificeer, en zet concepten klaar.
    Retourneert aantal nieuwe concept-antwoorden.

    Belangrijk: de DB-connectie wordt NOOIT opengehouden tijdens het trage
    netwerk-ophalen (`fetch_new`) of de LLM-drafts (`draft_reply`). Deze poll
    draait continu; hield hij de write-lock vast over die trage stappen, dan
    verhongert elke andere schrijver (scheduler `_record_run`, goal-plan,
    calendar-sync) tot "database is locked". Lezen → trage werk zonder lock →
    één korte schrijf-transactie.
    """
    # 1. Config + reeds-geziene berichten in één korte lees-transactie.
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM social_inboxes WHERE id=?", (inbox_id,)).fetchone()
        if not row:
            return 0
        inbox = dict(row)
        if not inbox.get("enabled"):
            return 0
        seen = {
            r["external_id"]
            for r in conn.execute(
                "SELECT external_id FROM social_inbox_msg WHERE inbox_id=?", (inbox_id,)
            )
        }

    # 2. Netwerk-ophalen — zonder open connectie.
    #
    # Niet elke mislukking is een taak voor een mens. Een TLS-blip om 02:00 is
    # weg bij de volgende poll; een verlopen token is dat nooit. `failures`
    # kent het verschil, de faal-reeks bepaalt wanneer "het lukt echt niet meer"
    # begint, en pas dán komt er een kaart in het Actiecentrum.
    from . import failures as _fail
    streak_key = f"social_fetch:{inbox_id}"
    try:
        raw = await fetch_new(inbox)
    except Exception as e:
        from .outcomes import log_outcome
        desc = _fail.describe_exception(e)
        klass = _fail.classify(e)
        count = _fail.note_failure(streak_key, desc, klass)
        if not _fail.should_escalate(streak_key, e):
            logger.warning(
                "[social] %s/%s ophalen mislukt (%s, poging %d) — Iris probeert "
                "het bij de volgende poll opnieuw: %s",
                inbox.get("project"), inbox["platform"], klass, count, desc,
            )
            return 0
        if klass == _fail.CLASS_AUTH:
            # Een verlopen token herstelt zich NOOIT vanzelf. Zonder pauze
            # escaleert dezelfde fout elke poll opnieuw en spamt het
            # Actiecentrum dagelijks een identieke kaart (bewezen 01/03/04-08).
            # Daarom: kanaal pauzeren en één heldere kaart achterlaten.
            with get_conn() as conn:
                conn.execute(
                    "UPDATE social_inboxes SET enabled=0 WHERE id=?", (inbox_id,)
                )
            detail = (
                f"Het {inbox['platform']}-kanaal van {inbox.get('project', '')} wijst ons af: "
                f"{desc}. Dit kan geen agent oplossen — alleen een nieuw token helpt. "
                "Het kanaal is gepauzeerd zodat deze melding niet elke dag terugkomt."
            )
            next_step = (
                f"Vernieuw het {inbox['platform']}-token in de Social-tab "
                "(Meta Business → Toegangstokens → nieuw token genereren en plakken) "
                "en zet het kanaal daarna weer aan."
            )
        else:
            detail = (
                f"Ophalen van {inbox['platform']} lukt al {count} pogingen op rij niet: {desc}. "
                "Iris heeft het zelf opnieuw geprobeerd; het probleem houdt aan."
            )
            next_step = (
                "Controleer de internetverbinding en daarna de kanaal-tokens in de Social-tab."
            )
        log_outcome(
            project=inbox.get("project", "Social"),
            action="social_fetch",
            detail=detail,
            next_step=next_step,
            status="error",
        )
        _fail.mark_escalated(streak_key)
        return 0
    # Geslaagd na een storing: meld het herstel één keer, zodat een openstaande
    # foutkaart uit het Actiecentrum een zichtbaar einde krijgt.
    healed = _fail.note_success(streak_key)
    if healed:
        from .outcomes import log_outcome
        log_outcome(
            project=inbox.get("project", "Social"),
            action="social_fetch",
            detail=(
                f"Ophalen van {inbox['platform']} werkt weer na {healed} mislukte "
                "poging(en) — vanzelf hersteld, geen actie nodig."
            ),
            status="ok",
        )
    if not raw:
        return 0

    # 3. Classificeren + LLM-drafts — óók zonder open connectie. We verzamelen
    #    de rijen en schrijven ze pas daarna in één keer weg.
    pending: List[tuple] = []
    for m in raw:
        ext = m.get("external_id")
        if not ext or ext in seen:
            continue
        kind = classify(m.get("text", ""))
        draft = ""
        manual = 0
        # Alleen echte vragen/klachten krijgen een concept; lof/spam/overig
        # worden gelogd maar niet gedraft (geen token-verlies, geen ruis).
        if kind in ("question", "complaint"):
            draft = draft_reply(
                inbox["platform"], inbox.get("brand_context", ""),
                m.get("text", ""), m.get("author_name", ""), m.get("thread", ""),
            )
        pending.append((
            inbox_id, inbox["platform"], ext, m.get("author_name", ""),
            m.get("author_handle", ""), m.get("text", ""), kind,
            m.get("parent_url", ""), m.get("thread", "") or "[]", draft, manual,
        ))
        seen.add(ext)

    if not pending:
        return 0

    # 4. Eén korte schrijf-transactie.
    with get_conn() as conn:
        for params in pending:
            conn.execute(
                "INSERT OR IGNORE INTO social_inbox_msg("
                "inbox_id,platform,external_id,author_name,author_handle,text,kind,"
                "parent_url,thread_json,draft_body,manual) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                params,
            )
    created = len(pending)

    if created:
        from .outcomes import log_outcome
        log_outcome(
            project=inbox.get("project", "Social"),
            action="social_ontvangen",
            detail=f"{created} nieuwe bericht(en) op {inbox['platform']} — "
                   f"concepten klaar in de Social-inbox.",
            next_step="Open de Social-tab en keur de antwoorden goed.",
            status="ok",
        )
    return created


def run_all_inboxes(inbox_id: Optional[str] = None) -> Dict[str, int]:
    import asyncio
    with get_conn() as conn:
        if inbox_id:
            rows = conn.execute(
                "SELECT id FROM social_inboxes WHERE enabled=1 AND id=?", (inbox_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM social_inboxes WHERE enabled=1").fetchall()
    results: Dict[str, int] = {}
    for r in rows:
        try:
            n = asyncio.run(run_inbox(r["id"]))
            results[r["id"]] = n
        except Exception as e:
            results[r["id"]] = -1
            logger.exception("Social inbox %s mislukt", r["id"])
    return results

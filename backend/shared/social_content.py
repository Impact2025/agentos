"""Social Content Creatie — agents die posts, beeld-briefs en TikTok-scriptpacks maken.

Dit is de CREATIE-laag (maken i.p.v. reageren — social_inbox.py doet het
omgekeerde: community-management). Eén "content pack" = één post-idee met:

  - per-platform tekst (linkedin / facebook / instagram / tiktok)
  - een image-brief (Canva-ready specs + Midjourney-prompt)  — als with_image
  - een TikTok-scriptpack (hook, script, shotlist, voiceover, captions) — als with_video

Alles wordt geschreven in de merkstem van het project (Schrijf-DNA via VaultReader)
en landt achter een review-gate in de `social_posts`-tabel. Niets wordt automatisch
gepost — de mens keurt eerst (zelfde discipline als content_jobs / mail_reply /
social_inbox_msg). Bij goedkeuring kan het pack geëxporteerd worden als een
"plak-klaar" bundel voor Canva / Midjourney / TikTok / de bestaande posting-router.

LLM: hergebruikt de bewezen OpenModel-sync-helper uit social_inbox.py. Zonder
backend levert generate_content_pack een deterministisch, duidelijk gemarkeerd
CONCEPT (net als _local_template_fill in agent_runner) zodat de review-gate het
herkent als niet-productieklaar.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from .config import OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL
from .database import get_conn

logger = logging.getLogger(__name__)

PLATFORMS = ("linkedin", "facebook", "instagram", "tiktok")

# Warm amber merkkleur (Vincent's huisstijl, zie Welzijnsklik/MJ-briefs).
BRAND_AMBER = "#e5a500"

# Per-platform tone-instructie voor de drafter (parallel aan social_inbox).
_PLATFORM_TONE = {
    "linkedin": "Nuchter-professioneel, eerste persoon, zonder jargon. Max 60 woorden. "
                "Eindig met een open vraag om engagement uit te lokken.",
    "facebook": "Warm en menselijk, alsof je tegen een bekende praat. Max 50 woorden.",
    "instagram": "Warm en kort, emoji-light (max 1 emoji). Max 40 woorden. Casual caption-toon.",
    "tiktok": "Casual en kort, alsof je met een vriend praat. Max 30 woorden. Geen hashtag-salvo "
              "in de body (hashtags komen in het aparte veld).",
}


# ── Dataklassen voor een pack ──────────────────────────────────────────────

@dataclass
class ImageBrief:
    """Canva-ready beeld-brief + Midjourney-prompt. Geen bewegend beeld."""
    template_type: str = "quote-card"          # quote-card | carousel-slide | cover
    dimensions: str = "1080x1080"              # vierkant IG/FB; 1080x1920 voor TT/Reels
    headline: str = ""
    subtext: str = ""
    color_palette: List[str] = field(default_factory=lambda: [BRAND_AMBER, "#1f2937", "#ffffff"])
    font: str = "Inter / Montserrat, vet voor headline"
    layout: str = "Centreer headline boven, subtext onder, 1 accent-element (amber) rechtsonder."
    midjourney_prompt: str = ""
    canva_note: str = (
        "Open Canva > Templates > zoek een passende 'quote' of 'social post'-template, "
        "vervang tekst door headline/subtext, zet de merkkleur op amber (#e5a500)."
    )


@dataclass
class TikTokPack:
    """Script + shotlist + voiceover voor een TikTok/Reels-filmpje. Geen generatie van beeld."""
    hook: str = ""                  # eerste 2-3 sec, pak aandacht
    script: str = ""                # volledige voiceover/spreektekst
    shotlist: List[str] = field(default_factory=list)
    voiceover_cues: str = ""        # waar op het scherm iets moet gebeuren
    captions: str = ""              # on-screen tekst
    hashtags: List[str] = field(default_factory=list)
    duration_sec: int = 30
    music_cue: str = "Upbeat, niet te druk — past bij merktoon."


@dataclass
class SocialPack:
    id: str = ""
    project: str = ""
    theme: str = ""
    angle: str = ""
    brand_context: str = ""
    copy: Dict[str, str] = field(default_factory=dict)     # platform -> tekst
    image_brief: Optional[Dict] = None
    tiktok_pack: Optional[Dict] = None
    status: str = "pending_review"                          # pending_review|approved|rejected|posted
    concept: bool = False                                   # True = lokale fallback, niet productieklaar
    created_at: str = ""
    approved_at: str = ""
    posted_result: Dict = field(default_factory=dict)


# ── LLM-helper (sync, gespiegeld aan social_inbox._sync_openmodel) ─────────

def _sync_openmodel(system: str, user: str, max_tokens: int = 900) -> str:
    url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
    payload = {
        "model": OPENMODEL_MODEL or "deepseek-v4-flash",
        "max_tokens": max_tokens,
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
        resp.raise_for_status()
        data = resp.json()
    if "content" in data:
        return "".join(p.get("text", "") for p in data["content"] if p.get("type") == "text")
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return data.get("text", "")


def _brand_voice(project: str, brand_context: str) -> str:
    """Bouw een korte merkstem-hint uit VaultReader + meegegeven context."""
    brand = brand_context or project
    voice = (
        f"Je bent de social-media-stem van {brand}. "
        f"Schrijf als de eigenaar (Vincent van Munster) — eerste persoon (ik/wij), "
        f"warm en nuchter, geen robot-taal, geen uitroeptekens-geweld. "
        f"Direct, geen jargon, mens centraal, technologie als stille achtergrond. "
        f"Geen aandachtstreepjes (— / –). Geen bullet lists in de post zelf."
    )
    try:
        from .vault_reader import VaultReader
        vr = VaultReader()
        if vr.is_configured:
            core = vr._read_core_folder(project)  # dict van bestandsnaam->inhoud
            if core:
                # Neem de eerste ~1500 tekens van de grootste core-note als context.
                biggest = max(core.values(), key=len)[:1500]
                voice += f"\n\nMerkcontext uit de vault:\n{biggest}"
    except Exception as e:
        logger.debug("Vault-lezen voor merkstem mislukt (genegeerd): %s", e)
    return voice


# ── Parsers (robust tegen deepseek-v4 tekst-output) ───────────────────────

def _norm_header(line: str) -> str:
    """Stripped een regel tot een header-sleutel: '**LinkedIn:**' -> 'linkedin'."""
    s = line.strip().lower()
    s = s.strip("*").strip()
    if s.endswith(":"):
        s = s[:-1].strip()
    return s


def _parse_platform_blocks(text: str, platforms: List[str]) -> Dict[str, str]:
    """Haal per-platform tekst uit een LLM-response (headers als 'LinkedIn:' of
    '**LinkedIn:**'). Robuust tegen markdown-vetgedrukte headers."""
    out: Dict[str, str] = {}
    # Accepteer NL/EN headers (zonder dubbele punten — die zijn eraf gestript)
    aliases = {
        "linkedin": ["linkedin", "li"],
        "facebook": ["facebook", "fb"],
        "instagram": ["instagram", "ig"],
        "tiktok": ["tiktok", "tt"],
    }
    current = None
    buf: List[str] = []
    for line in text.splitlines():
        hdr = _norm_header(line)
        hit = None
        for p, al in aliases.items():
            if p in platforms and any(hdr == a or hdr.startswith(a + " ") for a in al):
                hit = p
                break
        if hit:
            if current and buf:
                out[current] = "\n".join(buf).strip()
            current = hit
            # rest van de regel na de header (als de tekst op dezelfde regel staat)
            rest = line.split(":", 1)[1].strip().strip("*").strip() if ":" in line else ""
            buf = [rest] if rest else []
        else:
            if current:
                buf.append(line)
    if current and buf:
        out[current] = "\n".join(buf).strip()
    # Vul missende platforms met een lege fallback zodat de UI niet crasht.
    for p in platforms:
        out.setdefault(p, "")
    return out


def _parse_image_brief(text: str, theme: str) -> ImageBrief:
    """Parse een beeld-brief uit LLM-output; valt terug op een sensibele default."""
    b = ImageBrief()
    b.headline = (theme or "Jouw boodschap")[:60]
    for line in text.splitlines():
        hdr = _norm_header(line)
        if hdr in ("headline", "kop"):
            val = line.split(":", 1)[-1].strip().strip("*").strip()[:80]
            b.headline = val or b.headline
        elif hdr in ("subtext", "onderschrift"):
            b.subtext = line.split(":", 1)[-1].strip().strip("*").strip()[:120]
        elif "midjourney" in hdr or hdr in ("mj",):
            b.midjourney_prompt = line.split(":", 1)[-1].strip().strip("*").strip()
        elif hdr in ("layout", "opmaak"):
            b.layout = line.split(":", 1)[-1].strip().strip("*").strip()[:160]
    if not b.midjourney_prompt:
        b.midjourney_prompt = (
            f"{b.headline}, warm amber accent (#e5a500), clean minimal typography, "
            f"soft neutral background, professional Dutch brand style, high contrast, "
            f"--ar 1:1 --style raw --v 6"
        )
    return b


def _parse_tiktok_pack(text: str) -> TikTokPack:
    p = TikTokPack()
    section = None
    buf: List[str] = []
    for line in text.splitlines():
        hdr = _norm_header(line)
        if hdr == "hook":
            section, buf = "hook", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("script", "script:"):
            if section and buf:
                setattr(p, section, "\n".join(buf).strip())
            section, buf = "script", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("shotlist", "shot", "shots"):
            if section and buf:
                setattr(p, section, "\n".join(buf).strip())
            section, buf = "shotlist", []
        elif hdr in ("voiceover", "voice-over", "cue", "cues"):
            if section and buf:
                setattr(p, section, "\n".join(buf).strip())
            section, buf = "voiceover_cues", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("captions", "caption", "onderschrift"):
            if section and buf:
                setattr(p, section, "\n".join(buf).strip())
            section, buf = "captions", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("hashtags", "hashtag", "tags"):
            tags = line.split(":", 1)[-1].strip().strip("*").replace("#", "").replace(",", " ").split()
            p.hashtags = tags[:6]
        elif section == "shotlist":
            if line.strip():
                buf.append(line.strip().lstrip("-").strip())
        else:
            if section and buf is not None and line.strip():
                buf.append(line)
    if section and buf:
        setattr(p, section, "\n".join(buf).strip())
    if not p.hook and p.script:
        p.hook = p.script.split(".")[0][:80]
    if not p.hashtags:
        p.hashtags = ["#automatisering", "#ai", "#ondernemen", "#tipvandedag"]
    return p


# ── Generator ─────────────────────────────────────────────────────────────

def generate_content_pack(
    project: str,
    theme: str,
    angle: str = "",
    platforms: Optional[List[str]] = None,
    with_image: bool = True,
    with_video: bool = True,
    brand_context: str = "",
) -> SocialPack:
    """Genereer één content-pack (posts + optioneel beeld/TikTok) in de merkstem.

    Schrijft de row met status 'pending_review' en retourneert het pack. Bij geen
    LLM-backend: deterministisch CONCEPT (concept=True) zodat de review-gate het
    herkent als niet-productieklaar.
    """
    platforms = [p for p in (platforms or list(PLATFORMS)) if p in PLATFORMS]
    if not platforms:
        platforms = list(PLATFORMS)
    brand = brand_context or project
    voice = _brand_voice(project, brand_context)
    concept = False

    copy: Dict[str, str] = {p: "" for p in platforms}
    image_brief: Optional[ImageBrief] = None
    tiktok_pack: Optional[TikTokPack] = None

    if not OPENMODEL_API_KEY:
        # Deterministische fallback (geen LLM).
        concept = True
        for p in platforms:
            copy[p] = (
                f"[CONCEPT — geen LLM-backend]\n\n{theme}\n"
                f"{angle and (angle + '. ')}"
                f"(Schrijf uit in de merkstem van {brand}.)"
            )
        if with_image:
            image_brief = ImageBrief(headline=(theme or "Boodschap")[:60])
        if with_video:
            tiktok_pack = TikTokPack(hook=(theme or "Korte hook")[:80],
                                     script=f"{theme}. {angle}")
    else:
        try:
            # 1) Posts per platform
            sys_posts = (
                f"{voice}\n\nSchrijf voor elk platform een aparte post over het thema. "
                f"Gebruik duidelijke headers: 'LinkedIn:', 'Facebook:', 'Instagram:', 'TikTok:'. "
                f"Volg per platform de toon: "
                f"LinkedIn: {_PLATFORM_TONE['linkedin']} "
                f"Facebook: {_PLATFORM_TONE['facebook']} "
                f"Instagram: {_PLATFORM_TONE['instagram']} "
                f"TikTok: {_PLATFORM_TONE['tiktok']}"
            )
            user_posts = f"Thema: {theme}\n{angle and ('Invalshoek: ' + angle + chr(10))}Geef de 4 posts."
            raw = _sync_openmodel(sys_posts, user_posts, max_tokens=1600)
            copy = _parse_platform_blocks(raw, platforms)

            # 2) Beeld-brief
            if with_image:
                sys_img = (
                    f"{voice}\n\nMaak een Canva-ready beeld-brief voor een social post over het thema. "
                    f"Geef velden: 'Headline:', 'Subtext:', 'Layout:', 'Midjourney:'. "
                    f"Kleur: amber accent (#e5a500)."
                )
                raw_img = _sync_openmodel(sys_img, f"Thema: {theme}", max_tokens=400)
                image_brief = _parse_image_brief(raw_img, theme)

            # 3) TikTok-pack
            if with_video:
                sys_tt = (
                    f"{voice}\n\nSchrijf een TikTok/Reels-scriptpack over het thema. "
                    f"Geef duidelijke secties: 'Hook:', 'Script:', 'Shotlist:' (lijst met streepjes), "
                    f"'Voiceover:', 'Captions:', 'Hashtags:'. Max 30 sec, casual toon."
                )
                raw_tt = _sync_openmodel(sys_tt, f"Thema: {theme}\n{angle and ('Invalshoek: ' + angle)}",
                                         max_tokens=700)
                tiktok_pack = _parse_tiktok_pack(raw_tt)
        except Exception as e:
            logger.warning("Social content LLM mislukt, val terug op concept: %s", e)
            concept = True
            for p in platforms:
                if not copy.get(p):
                    copy[p] = f"[CONCEPT — LLM-fout: {e}]\n\n{theme}"
            if with_image and image_brief is None:
                image_brief = ImageBrief(headline=(theme or "Boodschap")[:60])
            if with_video and tiktok_pack is None:
                tiktok_pack = TikTokPack(hook=(theme or "Hook")[:80], script=f"{theme}. {angle}")

    pack = SocialPack(
        id=f"sp_{uuid.uuid4().hex[:12]}",
        project=project,
        theme=theme,
        angle=angle,
        brand_context=brand,
        copy=copy,
        image_brief=asdict(image_brief) if image_brief else None,
        tiktok_pack=asdict(tiktok_pack) if tiktok_pack else None,
        status="pending_review",
        concept=concept,
        created_at=datetime.now().isoformat(),
    )
    _persist(pack)
    return pack


# ── Persistentie ──────────────────────────────────────────────────────────

def _persist(pack: SocialPack) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO social_posts(id, project, theme, angle, brand_context, copy_json, "
            "image_brief_json, tiktok_pack_json, status, concept, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (pack.id, pack.project, pack.theme, pack.angle, pack.brand_context,
             json.dumps(pack.copy, ensure_ascii=False),
             json.dumps(pack.image_brief or {}, ensure_ascii=False),
             json.dumps(pack.tiktok_pack or {}, ensure_ascii=False),
             pack.status, int(pack.concept), pack.created_at),
        )
    try:
        from .outcomes import log_outcome
        log_outcome(
            project=pack.project,
            action="social_content_aangemaakt",
            detail=f"Content-pack '{pack.theme}' klaar voor review"
                   + (" (CONCEPT — geen LLM)" if pack.concept else ""),
            next_step="Open de Social Creatie-tab en keur de posts goed.",
            status="ok",
        )
    except Exception as e:
        logger.debug("log_outcome mislukt: %s", e)


def _row_to_pack(row) -> SocialPack:
    p = SocialPack(
        id=row["id"], project=row["project"], theme=row["theme"], angle=row["angle"],
        brand_context=row["brand_context"], status=row["status"],
        concept=bool(row["concept"]), created_at=row["created_at"],
        approved_at=row["approved_at"] or "",
        posted_result=json.loads(row["posted_result_json"] or "{}"),
    )
    try:
        p.copy = json.loads(row["copy_json"] or "{}")
        p.image_brief = json.loads(row["image_brief_json"] or "{}") or None
        p.tiktok_pack = json.loads(row["tiktok_pack_json"] or "{}") or None
    except Exception:
        pass
    return p


def list_packs(project: Optional[str] = None, status: Optional[str] = None) -> List[SocialPack]:
    with get_conn() as conn:
        q = "SELECT * FROM social_posts"
        clauses, params = [], []
        if project:
            clauses.append("project=?")
            params.append(project)
        if status:
            clauses.append("status=?")
            params.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
    return [_row_to_pack(r) for r in rows]


def get_pack(pack_id: str) -> Optional[SocialPack]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM social_posts WHERE id=?", (pack_id,)).fetchone()
    return _row_to_pack(row) if row else None


def approve_pack(pack_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE social_posts SET status='approved', approved_at=datetime('now') WHERE id=?",
            (pack_id,),
        )
        return cur.rowcount > 0


def reject_pack(pack_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE social_posts SET status='rejected' WHERE id=?", (pack_id,))
        return cur.rowcount > 0


def export_pack(pack_id: str) -> Dict:
    """Geef een plak-klaar bundel terug voor Canva / Midjourney / TikTok / posting."""
    p = get_pack(pack_id)
    if not p:
        return {}
    return {
        "id": p.id,
        "project": p.project,
        "theme": p.theme,
        "copy": p.copy,
        "image_brief": p.image_brief,
        "tiktok_pack": p.tiktok_pack,
        "concept": p.concept,
    }


async def publish_pack(pack_id: str, platform: str) -> Dict:
    """Plaats een goedgekeurd pack op één platform.

    FB/IG/Twitter: echte posting via de bestaande social-services (als geconfigureerd).
    LinkedIn/TikTok: plak-adapter (manual=True) — consistent met social_inbox.
    Markeert het pack als 'posted' bij succes.
    """
    if platform not in PLATFORMS:
        return {"success": False, "error": f"Onbekend platform: {platform}"}
    p = get_pack(pack_id)
    if not p:
        return {"success": False, "error": "Pack niet gevonden"}
    if p.status != "approved":
        return {"success": False, "error": "Pack is niet goedgekeurd (status=%s)" % p.status}

    text = (p.copy or {}).get(platform, "")
    if not text:
        return {"success": False, "error": f"Geen {platform}-tekst in dit pack"}

    result: Dict = {"success": False}
    try:
        if platform == "facebook":
            from . import facebook as svc
            result = await svc.post_update(text, None, p.project)
        elif platform == "instagram":
            # IG vereist een image_url; zonder beeld-publicatie geen post.
            result = {"success": False, "error": "manual",
                      "manual": True,
                      "detail": "Instagram vereist een afbeelding. Gebruik de image-brief "
                                "om in Canva/Midjourney een beeld te maken en post handmatig."}
        elif platform == "twitter":
            from . import twitter as svc
            result = await svc.post_update(text, None, p.project)
        elif platform == "linkedin":
            result = {"success": False, "error": "manual", "manual": True,
                      "detail": "LinkedIn staat geen API-post toe zonder partner-toegang. "
                                "Kopieer de tekst en plaats handmatig."}
        elif platform == "tiktok":
            result = {"success": False, "error": "manual", "manual": True,
                      "detail": "TikTok-video vereist het scriptpack + beeld. Monteer in "
                                "CapCut/TikTok en post handmatig (of gebruik de posting-API "
                                "met een kant-en-klare clip)."}
    except Exception as e:
        result = {"success": False, "error": str(e)[:300]}

    # Sla het resultaat op; markeer als posted als het écht (niet-manual) lukte.
    posted = bool(result.get("success")) and not result.get("manual")
    with get_conn() as conn:
        conn.execute(
            "UPDATE social_posts SET posted_result_json=?, status=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False),
             "posted" if posted else "approved", pack_id),
        )
    return result

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
import re
import uuid
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from .config import (
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL, SCRIPT_WRITER_MODEL,
)
from .database import get_conn
from . import social_style

logger = logging.getLogger(__name__)

PLATFORMS = ("linkedin", "facebook", "instagram", "tiktok", "twitter")

# Warm amber merkkleur (Vincent's huisstijl, zie Welzijnsklik/MJ-briefs).
BRAND_AMBER = "#e5a500"

# Per-platform tone-instructie voor de drafter (parallel aan social_inbox).
# Dit is de generieke terugval; een project met een eigen huisstijl-profiel
# (`projects/<project>/social/style.json`) overschrijft deze per platform —
# zie social_style.py voor waarom één stem voor twaalf merken niet werkt.
_PLATFORM_TONE = dict(social_style.DEFAULT_TONE)


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
    image_url: str = ""               # gegenereerde, on-brand afbeelding (public of local)
    image_path: str = ""              # lokaal pad naar de opgeslagen asset (mét merk-overlay)
    image_raw_path: str = ""          # dezelfde crop ZONDER overlay-tekst — video-achtergrond
    image_source: str = ""            # 'pexels' | 'fal' | '' (geen beeld beschikbaar)
    canva_note: str = (
        "Open Canva > Templates > zoek een passende 'quote' of 'social post'-template, "
        "vervang tekst door headline/subtext, zet de merkkleur op amber (#e5a500)."
    )
    canva_design_id: str = ""                  # gevuld als Canva Connect een design aanmaakte
    canva_edit_url: str = ""                   # directe 'open in Canva'-link
    canva_method: str = ""                     # 'autofill' | 'create' | '' (geen api)
    canva_template_url: str = ""               # link naar je vaste basis-template


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
    video_path: str = ""                                    # projectrelatief pad naar gerenderde 9:16 short
    created_at: str = ""
    approved_at: str = ""
    posted_result: Dict = field(default_factory=dict)
    # ── Herkomst (één ledger voor elke post, ongeacht de bron) ──────────────
    origin: str = "pipeline"          # 'pipeline' (gegenereerd pack) | 'deluxe_manual' (Facebook-tab)
    idea_source: str = ""             # 'gsc_top_queries' | 'demand_kansen' | 'fb_engagement' | 'vault' | ''
    idea_query: str = ""              # het zoekwoord waar het idee vandaan kwam (indien van toepassing)
    idea_evidence: str = ""           # het bewijs waarop het idee gekozen is (impressies/engagement/...)
    idea_url: str = ""                # live artikel waar de post naar linkt (FB→SEO-brug)
    # ── Campagne (een uitgeschreven plan met een volgorde en een datum) ──────
    campaign: str = ""                # naam van het plan ('socialplan-6weken')
    campaign_post: str = ""           # stabiele sleutel uit dat plan ('3.1')
    scheduled_for: str = ""           # ISO-datumtijd waarop deze post hoort te staan
    post_type: str = ""               # 'emotie' | 'product' | 'activatie' | ...


# ── LLM-helper (sync, gespiegeld aan social_inbox._sync_openmodel) ─────────

#: Reasoning-modellen (deepseek-v4-flash) sturen eerst een `thinking`-blok en dán
#: het echte `text`-blok. Dat denk-blok eet uit hetzelfde max_tokens-budget: bij
#: een te laag budget is de respons HTTP 200 met stop_reason=max_tokens en ALLEEN
#: thinking, dus text="" — de caller zag een "lege" respons en viel stil terug op
#: zijn fallback (de blogvideo sprak daardoor de blogtitel in i.p.v. een script).
#: Gemeten 19-8-2026 met een echte lange prompt: 400/1200/1600 → alleen thinking;
#: 3000 → thinking (≈1350 tok) + volledige text. Vandaar de bodem én de escalatie.
_MIN_REASONING_TOKENS = 3200
_MAX_REASONING_TOKENS = 8000


def _sync_openmodel(system: str, user: str, max_tokens: int = 900) -> str:
    """Vraag OpenModel om tekst; verhoog het budget als alleen `thinking` terugkomt.

    Nooit een lege string teruggeven zolang het model nog binnen
    `_MAX_REASONING_TOKENS` een tekst-blok kan afmaken: een stille lege respons
    laat elke caller op zijn fallback vallen zonder dat iemand het merkt.
    """
    budget = max(max_tokens, _MIN_REASONING_TOKENS)
    last = ""
    while True:
        last = _openmodel_once(system, user, budget, route="social_content")
        if last.strip() or budget >= _MAX_REASONING_TOKENS:
            return last
        logger.info("OpenModel gaf alleen een thinking-blok bij max_tokens=%d, verdubbel budget", budget)
        budget = min(budget * 2, _MAX_REASONING_TOKENS)


def _openmodel_once(system: str, user: str, max_tokens: int, model: str = "",
                    route: str = "social_content") -> str:
    url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
    payload = {
        "model": model or OPENMODEL_MODEL or "deepseek-v4-flash",
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
    usage = data.get("usage") or {}
    try:
        from .outcomes import log_llm_usage
        log_llm_usage(
            backend="openmodel", model=payload["model"], route=route,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        )
    except Exception:
        pass
    if "content" in data:
        return "".join(p.get("text", "") for p in data["content"] if p.get("type") == "text")
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return data.get("text", "")


def _sync_script_writer(system: str, user: str, max_tokens: int = 900) -> str:
    """Schrijf een video-/TikTok-script op écht Claude i.p.v. DeepSeek.

    Bewuste, smalle uitzondering (Vincent, 21 aug 2026): Claude is duur, dus
    ALLEEN voor deze scriptregels — nooit voor platform-copy of beeld-briefs,
    die blijven op `_sync_openmodel` (DeepSeek). Budget-guard vóór de call
    (`require_llm_budget`), en elke fout valt terug op DeepSeek in plaats van
    de caller te laten crashen — een duur script is beter dan geen script,
    maar geen script is nooit het antwoord."""
    try:
        from .outcomes import require_llm_budget
        require_llm_budget(route="video_script")
        budget = max(max_tokens, _MIN_REASONING_TOKENS)
        out = ""
        while True:
            out = _openmodel_once(system, user, budget, model=SCRIPT_WRITER_MODEL,
                                  route="video_script")
            if out.strip() or budget >= _MAX_REASONING_TOKENS:
                break
            budget = min(budget * 2, _MAX_REASONING_TOKENS)
        if out.strip():
            return out
        logger.warning("Script-writer (Claude) gaf een lege respons, val terug op DeepSeek")
    except Exception as e:
        logger.warning("Script-writer (Claude) mislukt (%s), val terug op DeepSeek", str(e)[:200])
    return _sync_openmodel(system, user, max_tokens)


def _brand_voice(project: str, brand_context: str) -> str:
    """Bouw een korte merkstem-hint uit het huisstijl-profiel + VaultReader.

    Het profiel wint van de generieke stem. Zonder profiel is de tekst hieronder
    woordelijk gelijk aan wat hier vóór 16 aug 2026 stond, zodat projecten zonder
    eigen huisstijl exact blijven schrijven zoals ze deden.
    """
    brand = brand_context or project
    style = social_style.load_style(project)
    voice = f"Je bent de social-media-stem van {brand}. {style.voice}"
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
    """Stripped een regel tot een header-sleutel: '**LinkedIn:**' -> 'linkedin'.

    Werkt zowel voor 'Header:' (gevolgd door waarde) als 'Header:' op zichzelf,
    en strippt eventuele waarde die op dezelfde regel staat.
    """
    s = line.strip().lower()
    s = s.strip("*").strip()
    # Verwijder een eventuele waarde na de eerste dubbele punt: 'voiceover: lees'
    # -> 'voiceover'. Als er geen dubbele punt is, blijft de hele regel staan.
    if ":" in s:
        s = s.split(":", 1)[0].strip()
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
        "twitter": ["twitter", "x", "tweet", "twitter/x", "x (twitter)"],
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


def apply_hashtags(copy: Dict[str, str], project: str, post_type: str = "") -> Dict[str, str]:
    """Plak de vaste hashtag-set van dit project onder elke post.

    Alleen tags die er nog niet staan: het model schrijft er soms zelf een paar,
    en '#familie #familie' onder een post is het soort slordigheid dat een merk
    amateuristisch laat lijken. Zonder huisstijl-profiel gebeurt er niets — de
    generieke pipeline plakte nooit hashtags en dat blijft zo.
    """
    style = social_style.load_style(project)
    out = dict(copy)
    for plat, tekst in out.items():
        tags = style.hashtags_for(plat, post_type)
        if not tags or not (tekst or "").strip():
            continue
        laag = tekst.lower()
        nieuw = [t for t in tags if t.lower() not in laag]
        if nieuw:
            out[plat] = tekst.rstrip() + "\n" + " ".join(nieuw)
    return out


def _truncate_words(text: str, limit: int) -> str:
    """Knip tekst af op een woordgrens i.p.v. midden in een woord.

    Een kale `[:60]` sneed "levensverhaal losmaakte" af tot "levensverhaal
    lo" — leesbaar noch bruikbaar als kop op een gepubliceerde afbeelding.
    Zoekt de laatste spatie vóór de limiet; is er geen (één lang woord), dan
    blijft de kale afkap de enige optie.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    return cut[:sp].rstrip() if sp > 0 else cut


def _parse_image_brief(text: str, theme: str, project: str = "") -> ImageBrief:
    """Parse een beeld-brief uit LLM-output; valt terug op een sensibele default.

    Behandelt zowel 'Header: waarde' (op één regel) als de net zo gangbare
    markdown-vorm '**Header:**' gevolgd door de waarde op de vólgende regel(s)
    (deepseek/Claude doen dit beide vaak bij een lijst met meerdere velden).
    Vóór deze fix las de parser alleen tekst ná de dubbele punt op DEZELFDE
    regel, dus bleef 'Headline:' met een lege rest achter de dubbele punt
    zonder waarde — en viel de kop stil terug op de afgekapte werktitel
    (gemeten 22 aug 2026, DatingAssistent: 3 van de 4 packs kregen zo de kale
    thema-tekst als kop i.p.v. de wél door het model geschreven zin)."""
    b = ImageBrief()
    b.headline = _truncate_words(theme or "Jouw boodschap", 60)
    _KEYS = {"headline": "headline", "kop": "headline",
             "subtext": "subtext", "onderschrift": "subtext",
             "layout": "layout", "opmaak": "layout",
             "mj": "midjourney"}
    fields: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        hdr = _norm_header(line)
        key = _KEYS.get(hdr) or ("midjourney" if "midjourney" in hdr else None)
        if key:
            current = key
            rest = line.split(":", 1)[-1].strip().strip("*").strip() if ":" in line else ""
            fields.setdefault(current, [])
            if rest:
                fields[current].append(rest)
        elif current and line.strip():
            fields[current].append(line.strip().strip("*").strip())
        elif not line.strip():
            current = None  # lege regel sluit het vervolg van het huidige veld af

    def _joined(key: str) -> str:
        return " ".join(fields.get(key, [])).strip()

    val = _joined("headline")
    if val:
        b.headline = _truncate_words(val, 80)
    val = _joined("subtext")
    if val:
        b.subtext = _truncate_words(val, 120)
    val = _joined("midjourney")
    if val:
        b.midjourney_prompt = val
    val = _joined("layout")
    if val:
        b.layout = _truncate_words(val, 160)
    style = social_style.load_style(project)
    if not b.midjourney_prompt:
        # Het vaste stijlblok van het project achter het onderwerp — zo blijft de
        # hele serie herkenbaar. Zonder profiel is dit de oude amber-prompt.
        b.midjourney_prompt = style.image_prompt(b.headline)
    if style.bron == "style.json":
        # Het beeldformaat hoort bij de huisstijl, niet bij de renderer: het plan
        # schrijft 4:5 voor (staand vult meer scherm in de feed), de oude default
        # was vierkant.
        b.dimensions = _dimensions_for(style.aspect)
        ov = style.overlay
        # Wereldklasse: de project-specifieke kleuren horen in de beeld-brief,
        # niet de generieke BRAND_AMBER-default. Zonder dit bleef elk pack op
        # de goud-default (#e5a500) hangen en zag Bijeen-er geen enkele post
        # uit in het eigen oranje/crème/nacht-palet uit style.json. 18 aug 2026.
        b.color_palette = [
            ov.accent_kleur or BRAND_AMBER,
            ov.nacht_kleur or "#1f2937",
            ov.achtergrond_kleur or "#ffffff",
        ]
        if ov.footer_tekst or ov.logo_path:
            b.layout = (
                f"Kop in serif-goud op een donker transparant vlak in het onderste "
                f"derde deel, onderschrift eronder in warm wit"
                + (f", logo {ov.logo_positie}" if ov.logo_path else "")
                + (f", '{ov.footer_tekst}' onderaan." if ov.footer_tekst else ".")
            )
    return b


def _dimensions_for(aspect: str) -> str:
    """'4:5' → '1080x1350'. Onbekende verhouding houdt het vierkant."""
    known = {"1:1": "1080x1080", "4:5": "1080x1350", "9:16": "1080x1920", "16:9": "1920x1080"}
    return known.get((aspect or "").strip(), "1080x1080")


def _finalize_section(section: str, buf: List[str]):
    """Zet een verzamelde buffer om in het juiste type voor die sectie.

    shotlist → echte lijst (geen string!); andere secties → samengevoegde tekst.
    """
    if not buf:
        return None
    if section == "shotlist":
        return list(buf)
    return "\n".join(buf).strip()


_MD_RULE_RE = re.compile(r"^[-*_]{3,}$")


def _is_markdown_rule(line: str) -> bool:
    """Herken een markdown-scheidingsstreep ('---', '***', ...).

    De LLM zet zo'n regel soms tussen secties (bv. na de hook, vóór het
    script). Ongefilterd landt die letterlijk in de scène-tekst — en dus in
    de video: een karaoke-caption die eindigt op "---" en een stem die het
    ook probeert uit te spreken.
    """
    return bool(_MD_RULE_RE.match(line.strip()))


def _parse_tiktok_pack(text: str) -> TikTokPack:
    p = TikTokPack()
    section = None
    buf: List[str] = []
    for line in text.splitlines():
        hdr = _norm_header(line)
        if hdr == "hook":
            if section and buf:
                setattr(p, section, _finalize_section(section, buf))
            section, buf = "hook", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("script", "script:"):
            if section and buf:
                setattr(p, section, _finalize_section(section, buf))
            section, buf = "script", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("shotlist", "shot", "shots"):
            if section and buf:
                setattr(p, section, _finalize_section(section, buf))
            section, buf = "shotlist", []
        elif hdr in ("voiceover", "voice-over", "cue", "cues"):
            if section and buf:
                setattr(p, section, _finalize_section(section, buf))
            section, buf = "voiceover_cues", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("captions", "caption", "onderschrift"):
            if section and buf:
                setattr(p, section, _finalize_section(section, buf))
            section, buf = "captions", [line.split(":", 1)[-1].strip().strip("*").strip()]
        elif hdr in ("hashtags", "hashtag", "tags"):
            tags = line.split(":", 1)[-1].strip().strip("*").replace("#", "").replace(",", " ").split()
            p.hashtags = tags[:6]
        elif section == "shotlist":
            # Alleen echte bullets (lijnen die met -/* beginnen) horen in de shotlist.
            # Niet-bullet regels (bijv. een header die niet herkend werd) negeren we.
            stripped = line.strip()
            if stripped.startswith(("-", "*")) and not _is_markdown_rule(stripped):
                item = stripped.lstrip("-*").strip()
                if item:
                    buf.append(item)
        else:
            if section and buf is not None and line.strip() and not _is_markdown_rule(line):
                buf.append(line)
    if section and buf:
        setattr(p, section, _finalize_section(section, buf))
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
    idea_source: str = "",
    idea_query: str = "",
    idea_evidence: str = "",
    idea_url: str = "",
    post_type: str = "",
) -> SocialPack:
    """Genereer één content-pack (posts + optioneel beeld/TikTok) in de merkstem.

    Schrijft de row met status 'pending_review' en retourneert het pack. Bij geen
    LLM-backend: deterministisch CONCEPT (concept=True) zodat de review-gate het
    herkent als niet-productieklaar.

    idea_source/idea_query/idea_evidence/idea_url: herkomst van het thema als het
    uit een datagedreven idee komt (social_auto._pick_grounded_idea, gevoed door
    GSC/Demand/FB-engagement i.p.v. een gok uit de vault) — puur ter registratie,
    zodat later meetbaar is welke herkomst tot welke prestatie leidt. idea_url
    wordt bovendien meegegeven aan de schrijfprompt als link-kans (FB→SEO-brug).

    post_type ('emotie' / 'product' / 'activatie' / ...) bepaalt wélke hashtag-set
    van het project onder de post komt. Leeg = de vaste platform-set, en zonder
    huisstijl-profiel helemaal geen hashtags (het oude gedrag).
    """
    from .projects import canonical_project
    project = canonical_project(project)
    platforms = [p for p in (platforms or list(PLATFORMS)) if p in PLATFORMS]
    if not platforms:
        platforms = list(PLATFORMS)
    brand = brand_context or project
    voice = _brand_voice(project, brand_context)
    style = social_style.load_style(project)
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
            image_brief = ImageBrief(headline=_truncate_words(theme or "Boodschap", 60))
        if with_video:
            tiktok_pack = TikTokPack(hook=(theme or "Korte hook")[:80],
                                     script=f"{theme}. {angle}")
    else:
        try:
            # 1) Posts per platform (headers/tonen dynamisch op de gevraagde platforms)
            _labels = {"linkedin": "LinkedIn", "facebook": "Facebook",
                       "instagram": "Instagram", "tiktok": "TikTok", "twitter": "Twitter"}
            headers = ", ".join(f"'{_labels[p]}:'" for p in platforms)
            tones = " ".join(f"{_labels[p]}: {style.tone_for(p)}" for p in platforms)
            sys_posts = (
                f"{voice}\n\nSchrijf voor elk platform een aparte post over het thema. "
                f"Gebruik duidelijke headers: {headers}. "
                f"Volg per platform de toon: {tones}"
            )
            link_hint = (
                f"Er bestaat al een live pagina over dit onderwerp: {idea_url}. "
                f"Verwijs er waar natuurlijk naar (bijv. 'lees het volledige verhaal op de site'), "
                f"forceer het niet in elk platform.\n" if idea_url else ""
            )
            user_posts = (f"Thema: {theme}\n{angle and ('Invalshoek: ' + angle + chr(10))}"
                          f"{link_hint}Geef de {len(platforms)} posts.")
            raw = _sync_openmodel(sys_posts, user_posts, max_tokens=1600)
            copy = _parse_platform_blocks(raw, platforms)

            # 2) Beeld-brief + ECHTE on-brand afbeelding
            if with_image:
                if style.overlay.modus == "stelling":
                    # 'Thema' is hier een interne werktitel (bv. '"liefde" — wat je
                    # écht wil weten', zie social_auto.py:_pick_grounded_idea) die
                    # zelf al op een kop lijkt — zonder expliciet verbod schrijft het
                    # model 'm klakkeloos over als Headline, en dat kwam letterlijk
                    # meerdere keren identiek in de Wachtrij terecht (19+21 aug 2026).
                    # De kop op een Stelling-kaart is de hele post: geen samenvatting
                    # van het thema, maar een concrete, prikkelbare uitspraak waar een
                    # lezer het spontaan mee eens of oneens kan zijn.
                    sys_img = (
                        f"{voice}\n\nSchrijf de kop voor een 'Stelling'-kaart (géén foto, "
                        f"alleen tekst) over dit thema. Geef velden: 'Headline:', 'Subtext:'. "
                        f"Headline = één scherpe, concrete stelling in de je/jij-vorm of "
                        f"neutrale bewering (max 90 tekens, geen vraagteken, geen aanhalingstekens "
                        f"eromheen) — NOOIT de letterlijke werktitel of het zoekwoord overnemen, "
                        f"altijd een eigen, specifieke uitspraak die een mening uitlokt. "
                        f"Subtext = een korte uitnodiging om te reageren, bv. 'Eens of oneens? "
                        f"Laat je reactie achter.' (mag variëren, blijft kort)."
                    )
                else:
                    sys_img = (
                        f"{voice}\n\nMaak een Canva-ready beeld-brief voor een social post over het thema. "
                        f"Geef velden: 'Headline:', 'Subtext:', 'Layout:', 'Midjourney:'. "
                        f"Headline = max 80 tekens, één scherpe zin die zelf de boodschap ís — "
                        f"nooit een samenvatting van het thema, altijd een eigen, concrete uitspraak. "
                        f"Subtext = MAX ÉÉN korte, complete zin (max 90 tekens) die op het beeld onder "
                        f"de headline komt te staan — géén alinea, géén meerdere zinnen: dat wordt op "
                        f"het beeld hard afgekapt en leest dan als een afgebroken gedachte. "
                        + (f"Beeldstijl (zet dit stijlblok achter de Midjourney-prompt zodat alle "
                           f"beelden bij elkaar passen): {style.stijlblok}"
                           if style.bron == "style.json" else "Kleur: amber accent (#e5a500).")
                    )
                raw_img = _sync_openmodel(sys_img, f"Thema: {theme}", max_tokens=400)
                image_brief = _parse_image_brief(raw_img, theme, project)
                # Echte, on-brand afbeelding genereren (Pexels eerst, FAL fallback)
                # en de gouden merk-overlay erin branden. Zonder sleutel: image_url
                # blijft leeg en toont de review-gate "genereer handmatig".
                try:
                    from . import social_image as img_svc
                    img_res = img_svc.generate_social_image(
                        theme, project,
                        headline=image_brief.headline,
                        subtext=image_brief.subtext,
                    )
                    if img_res.get("success"):
                        image_brief.image_url = img_res["url"]
                        image_brief.image_path = img_res.get("path", "")
                        image_brief.image_raw_path = img_res.get("raw_path", "")
                        image_brief.image_source = img_res.get("source", "")
                        logger.info("Social-afbeelding gegenereerd (%s): %s",
                                    img_res.get("source"), img_res.get("url"))
                    else:
                        logger.warning("Social-afbeelding mislukt (brief blijft leidend): %s",
                                       img_res.get("error"))
                except Exception as e:
                    logger.warning("Social-image-generatie overgeslagen: %s", e)
                # Echte Canva-design aanmaken als Connect geconfigureerd is.
                try:
                    from . import canva as canva_svc
                    if canva_svc.canva_ready():
                        # Voorkeur: Autofill van de vaste Brand Template; anders
                        # een leeg design; zonder credentials blijft de brief leidend.
                        cr = canva_svc.fill_or_create(asdict(image_brief))
                        if cr.get("design_id"):
                            image_brief.canva_design_id = cr["design_id"]
                            image_brief.canva_edit_url = cr.get("edit_url", "")
                            image_brief.canva_method = cr.get("method", "none")
                            logger.info("Canva-design aangemaakt via %s: %s",
                                        cr.get("method"), cr["design_id"])
                        elif cr.get("error"):
                            logger.warning("Canva-aanmaak overgeslagen: %s", cr["error"])
                    # Link naar je vaste basis-template (altijd handig om te tonen).
                    image_brief.canva_template_url = canva_svc.canva_template_edit_url()
                except Exception as e:
                    logger.warning("Canva-integratie mislukt (brief blijft fallback): %s", e)

            # 3) TikTok-pack
            if with_video:
                sys_tt = (
                    f"{voice}\n\nSchrijf een TikTok/Reels-scriptpack over het thema. "
                    f"Geef duidelijke secties: 'Hook:', 'Script:', 'Shotlist:' (lijst met streepjes), "
                    f"'Voiceover:', 'Captions:', 'Hashtags:'. Max 30 sec, casual toon."
                )
                raw_tt = _sync_script_writer(sys_tt, f"Thema: {theme}\n{angle and ('Invalshoek: ' + angle)}",
                                             max_tokens=700)
                tiktok_pack = _parse_tiktok_pack(raw_tt)
        except Exception as e:
            logger.warning("Social content LLM mislukt, val terug op concept: %s", e)
            concept = True
            for p in platforms:
                if not copy.get(p):
                    copy[p] = f"[CONCEPT — LLM-fout: {e}]\n\n{theme}"
            if with_image and image_brief is None:
                image_brief = ImageBrief(headline=_truncate_words(theme or "Boodschap", 60))
            if with_video and tiktok_pack is None:
                tiktok_pack = TikTokPack(hook=(theme or "Hook")[:80], script=f"{theme}. {angle}")

    # De huisstijl geldt óók op het terugval-pad. Een CONCEPT-pack zonder de
    # merk-hashtags en met de generieke amber-prompt is geen kleiner probleem
    # dan een verkeerd geschreven post: het is precies het pack dat een mens
    # daarna met de hand afmaakt, en dan sluipt de verkeerde stijl er alsnog in.
    copy = apply_hashtags(copy, project, post_type)
    if image_brief is not None and not image_brief.midjourney_prompt:
        image_brief.midjourney_prompt = style.image_prompt(image_brief.headline or theme)

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
        origin="pipeline",
        idea_source=idea_source,
        idea_query=idea_query,
        idea_evidence=idea_evidence,
        idea_url=idea_url,
    )
    _persist(pack)
    return pack


# ── Persistentie ──────────────────────────────────────────────────────────

def _persist(pack: SocialPack) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO social_posts(id, project, theme, angle, brand_context, copy_json, "
            "image_brief_json, tiktok_pack_json, status, concept, created_at, "
            "origin, idea_source, idea_query, idea_evidence, idea_url) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pack.id, pack.project, pack.theme, pack.angle, pack.brand_context,
             json.dumps(pack.copy, ensure_ascii=False),
             json.dumps(pack.image_brief or {}, ensure_ascii=False),
             json.dumps(pack.tiktok_pack or {}, ensure_ascii=False),
             pack.status, int(pack.concept), pack.created_at,
             pack.origin, pack.idea_source, pack.idea_query, pack.idea_evidence, pack.idea_url),
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


def record_external_post(project: str, platform: str, text: str,
                         post_id: str = "", url: str = "",
                         origin: str = "deluxe_manual") -> SocialPack:
    """Registreer een post die BUITEN generate_content_pack()/publish_pack() om is
    geplaatst (bijv. de Facebook Deluxe-composer) in hetzelfde ledger.

    Zonder dit is elke analyse (analyse_page, toekomstige content_learning voor
    social) blind voor de helft van wat er daadwerkelijk gepost is — precies de
    fout die dit ledger moest oplossen. De rij komt binnen als 'posted' (het
    ding staat al live; hier is niets meer te keuren) en telt mee in dezelfde
    tabel als de gated packs, met `origin` als onderscheid.
    """
    from .projects import canonical_project
    project = canonical_project(project)
    pack = SocialPack(
        id=f"sp_{uuid.uuid4().hex[:12]}",
        project=project,
        theme=text[:120],
        brand_context=project,
        copy={platform: text},
        status="posted",
        concept=False,
        created_at=datetime.now().isoformat(),
        approved_at=datetime.now().isoformat(),
        posted_result={platform: {"success": True, "url": url, "post_id": post_id},
                       "_platforms": [platform]},
        origin=origin,
    )
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO social_posts(id, project, theme, angle, brand_context, copy_json, "
            "image_brief_json, tiktok_pack_json, status, concept, created_at, approved_at, "
            "posted_result_json, origin, idea_source, idea_query, idea_evidence, idea_url) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pack.id, pack.project, pack.theme, pack.angle, pack.brand_context,
             json.dumps(pack.copy, ensure_ascii=False), "{}", "{}",
             pack.status, 0, pack.created_at, pack.approved_at,
             json.dumps(pack.posted_result, ensure_ascii=False),
             pack.origin, "", "", "", ""),
        )
    return pack


def _row_to_pack(row) -> SocialPack:
    p = SocialPack(
        id=row["id"], project=row["project"], theme=row["theme"], angle=row["angle"],
        brand_context=row["brand_context"], status=row["status"],
        concept=bool(row["concept"]), created_at=row["created_at"],
        approved_at=row["approved_at"] or "",
        video_path=(row["video_path"] if "video_path" in row.keys() else "") or "",
        posted_result=json.loads(row["posted_result_json"] or "{}"),
        origin=(row["origin"] if "origin" in row.keys() else "pipeline") or "pipeline",
        idea_source=(row["idea_source"] if "idea_source" in row.keys() else "") or "",
        idea_query=(row["idea_query"] if "idea_query" in row.keys() else "") or "",
        idea_evidence=(row["idea_evidence"] if "idea_evidence" in row.keys() else "") or "",
        idea_url=(row["idea_url"] if "idea_url" in row.keys() else "") or "",
        campaign=(row["campaign"] if "campaign" in row.keys() else "") or "",
        campaign_post=(row["campaign_post"] if "campaign_post" in row.keys() else "") or "",
        scheduled_for=(row["scheduled_for"] if "scheduled_for" in row.keys() else "") or "",
        post_type=(row["post_type"] if "post_type" in row.keys() else "") or "",
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
            # De aanroeper (frontend-URL, bridge-commando) levert niet altijd
            # exact de spelling uit `sites.name` aan ('Datingassistent' i.p.v.
            # 'DatingAssistent') — dezelfde storing die shared/projects.py al
            # voor goals/radar oploste. Zonder dit toont de Social Creatie-tab
            # "nog geen content packs" terwijl ze er wél liggen, alleen onder
            # de sites-spelling (22 aug 2026).
            from .projects import canonical_project
            clauses.append("project=?")
            params.append(canonical_project(project))
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


def mark_posted_manually(pack_id: str, platforms: Optional[List[str]] = None) -> Dict:
    """Leg vast dat een mens dit pack zélf heeft geplaatst.

    Voor kanalen die Impact OS niet kan bedienen — LinkedIn vanaf een persoonlijk
    profiel kan per definitie niet geautomatiseerd, en een project zonder eigen
    Facebook/Instagram-token mag nooit op de pagina van een ánder project posten
    (zie social_auto). Zonder deze weg blijft zo'n pack eeuwig `pending_review`
    en meldt `campagnepost_over_datum` een gemiste post die gewoon live staat:
    een alarm dat aantoonbaar liegt leert een mens alle alarmen te negeren.

    Bewust géén `approve_pack`: goedgekeurd betekent 'mag naar buiten', geplaatst
    betekent 'is naar buiten'. Alleen het tweede telt als uitvoering, en het
    verschil is het enige waarop je later kunt terugkijken. De uitkomst draagt
    `via: 'handmatig'`, zodat een latere analyse een zelf-geplaatste post niet
    verwart met een die via de API is gegaan.
    """
    pack = get_pack(pack_id)
    if not pack:
        return {"success": False, "error": "Pack niet gevonden"}
    kanalen = [p.strip().lower() for p in (platforms or list(pack.copy.keys())) if p.strip()]
    kanalen = [p for p in kanalen if p in PLATFORMS]
    nu = datetime.now().isoformat()
    resultaat = dict(pack.posted_result or {})
    for plat in kanalen:
        resultaat[plat] = {"success": True, "via": "handmatig", "at": nu}
    resultaat["_platforms"] = sorted(set(resultaat.get("_platforms", [])) | set(kanalen))
    with get_conn() as conn:
        conn.execute(
            "UPDATE social_posts SET status='posted', approved_at=?, posted_result_json=? "
            "WHERE id=?",
            (pack.approved_at or nu, json.dumps(resultaat, ensure_ascii=False), pack_id),
        )
    try:
        from .outcomes import log_outcome
        log_outcome(
            project=pack.project,
            action="social_post_geplaatst",
            detail=f"'{pack.theme}' handmatig geplaatst op: " + (", ".join(kanalen) or "geen kanaal"),
            next_step="Reageer binnen 24 uur op reacties — in deze fase is elk gesprek goud.",
            status="ok",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("log_outcome (handmatig geplaatst) mislukt: %s", e)
    return {"success": True, "pack_id": pack_id, "platforms": kanalen}


def reject_pack(pack_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE social_posts SET status='rejected' WHERE id=?", (pack_id,))
        return cur.rowcount > 0


_PHOTO_EXT = (".png", ".jpg", ".jpeg", ".webp")


def list_project_photos(project: str) -> List[Dict]:
    """Foto's die Vincent zelf in projects/<project>/photos/ heeft gezet
    (bijv. handmatig gegenereerde Midjourney-renders). Dit is dezelfde map
    als de video-B-roll-bibliotheek (`video/template.json:footage.fallback`),
    hergebruikt voor losse posts — geen tweede map, één bibliotheek per project.
    Squash-bewust via `social_style._project_dirs`, anders vindt 'DatingAssistent'
    een map genaamd 'datingassistent' niet."""
    out: List[Dict] = []
    seen = set()
    for base in social_style._project_dirs(project):
        photos_dir = base / "photos"
        if not photos_dir.is_dir():
            continue
        for f in sorted(photos_dir.iterdir()):
            if f.suffix.lower() in _PHOTO_EXT and f.name not in seen:
                seen.add(f.name)
                out.append({"filename": f.name, "size": f.stat().st_size})
    return out


def project_photo_path(project: str, filename: str) -> Optional[str]:
    """Absoluut pad naar één foto uit de projectbibliotheek, of None als hij
    er niet is (of iemand een pad-traversal probeert via de filename)."""
    safe_name = Path(filename).name
    if safe_name != filename:
        return None
    for base in social_style._project_dirs(project):
        cand = base / "photos" / safe_name
        if cand.is_file():
            return str(cand)
    return None


def assign_library_photo(pack_id: str, project: str, filename: str) -> Dict:
    """Koppel een bestaande foto uit de projectbibliotheek aan een pack —
    zelfde huisstijl-crop+overlay als een verse upload (`brand_uploaded_image`),
    alleen de bron is disk in plaats van een file-upload."""
    path = project_photo_path(project, filename)
    if not path:
        return {"success": False, "error": "Foto niet gevonden in de projectbibliotheek"}
    from . import social_image as img_svc
    p = get_pack(pack_id)
    if not p:
        return {"success": False, "error": "Pack niet gevonden"}
    raw = Path(path).read_bytes()
    brief = p.image_brief or {}
    res = img_svc.brand_uploaded_image(
        raw, project,
        headline=brief.get("headline", "") or p.theme,
        subtext=brief.get("subtext", ""),
    )
    if not res.get("success"):
        return res
    set_pack_image(pack_id, image_url=res["url"], image_path=res["path"],
                    image_source=f"library:{filename}", image_raw_path=res.get("raw_path", ""))
    return {"success": True, **res}


def set_pack_image(pack_id: str, *, image_url: str, image_path: str, image_source: str,
                   image_raw_path: str = "") -> bool:
    """Vervang het beeld van een pack (bijv. een geüploade Midjourney-render).

    Update alleen de beeld-velden binnen `image_brief_json` — headline/subtext/
    layout/midjourney_prompt blijven staan, want die zijn de instructie voor het
    beeld en horen niet te verdwijnen zodra het beeld zelf handmatig geleverd is.
    `image_raw_path` (de ongebrande crop) voedt de videorender — zonder deze
    parameter blijft een oudere/lege waarde staan i.p.v. per ongeluk gewist te
    worden.
    """
    p = get_pack(pack_id)
    if not p:
        return False
    brief = dict(p.image_brief or {})
    brief["image_url"] = image_url
    brief["image_path"] = image_path
    if image_raw_path:
        brief["image_raw_path"] = image_raw_path
    brief["image_source"] = image_source
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE social_posts SET image_brief_json=? WHERE id=?",
            (json.dumps(brief, ensure_ascii=False), pack_id),
        )
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
        "status": p.status,
        "copy": p.copy,
        "image_brief": p.image_brief,
        "tiktok_pack": p.tiktok_pack,
        "concept": p.concept,
        "campaign": p.campaign,
        "campaign_post": p.campaign_post,
        "scheduled_for": p.scheduled_for,
        "post_type": p.post_type,
        "angle": p.angle,
        "video_path": p.video_path,
        # Directe stream-URL voor de <video>-preview (leeg als er nog geen video is).
        "video_url": (f"/api/social-content/packs/{p.id}/video" if p.video_path else ""),
    }


# ── Video-render (9:16 short uit het scriptpack) ───────────────────────────

def _cta_line(project: str) -> str:
    """Korte merk-CTA voor de slotscène (per project; anders generiek)."""
    if project.lower().replace(" ", "").replace("-", "") == "bewaardvoorjou":
        return "Begin vandaag gratis en leg jouw verhaal vast voor de generaties na jou."
    return "Benieuwd? Neem vandaag nog een kijkje."


def video_file_path(pack_id: str) -> Optional["Path"]:
    """Absoluut pad naar de gerenderde video van een pack (None als er geen is)."""
    from pathlib import Path
    from .video_template import REPO_ROOT
    p = get_pack(pack_id)
    if not p or not p.video_path:
        return None
    cand = REPO_ROOT / p.video_path
    return cand if cand.exists() else None


def render_pack_video(pack_id: str) -> Dict:
    """Render een 9:16 short uit het TikTok-scriptpack van dit pack.

    Bouwt scènes uit het scriptpack (hook + zinnen + CTA), rendert met de
    project-template (kleuren/font/logo/stem/muziek) en slaat het projectrelatieve
    pad op in social_posts.video_path. Volledig zelf-voorzienend (edge-tts +
    Pillow + ffmpeg); geen automatische publicatie — het pack blijft achter de
    review-gate. Retourneert {success, video_url|error, duration, scenes}.
    """
    from pathlib import Path
    from .video_template import REPO_ROOT, load_template
    from . import video_render as vr

    p = get_pack(pack_id)
    if not p:
        return {"success": False, "error": "Pack niet gevonden"}

    # 1) Scènes uit het scriptpack; anders een minimale hook+CTA uit thema/angle.
    scenes: List = []
    if p.tiktok_pack:
        scenes = vr.scenes_from_scriptpack(p.tiktok_pack)
    if not scenes:
        hook = p.theme or "Jouw verhaal telt"
        scenes = [vr.Scene(narration=hook, caption=hook, kind="hook")]
        if p.angle:
            scenes.append(vr.Scene(narration=p.angle, kind="body"))
    # Sluit altijd af met een merk-CTA-scène.
    scenes.append(vr.Scene(narration=_cta_line(p.project),
                           caption="Start vandaag", kind="cta"))

    # 2) Renderen naar projects/<project>/video/<pack_id>.mp4.
    # Zelfde reden als in blog_video.make_blog_video: schrijf in de projectmap die
    # de brand-assets/template draagt, niet in een nieuwe map met de UI-spelling.
    from .video_template import _project_dir
    rel = f"projects/{_project_dir(p.project).name}/video/{p.id}.mp4"
    out = REPO_ROOT / rel
    try:
        tpl = load_template(p.project)
        # Het pack heeft al een on-brand foto (auto-gezocht of Vincents eigen
        # Midjourney-upload) — gebruik precies díé als video-achtergrond in
        # plaats van tpl.footage's eigen, losstaande Pexels-zoekopdracht (21 aug
        # 2026: beeld en video toonden tot dan toe twee onafhankelijk gekozen
        # foto's van hetzelfde onderwerp). De ongebrande crop (`image_raw_path`)
        # heeft de voorkeur — die draagt geen tekst, want de video brandt zijn
        # eigen ondertitels erover; oudere packs zonder dat veld vallen terug op
        # de gebrande versie (`image_path`) zodat ze niet stilzwijgend leeg
        # uitkomen. `render_short` geeft "eigen beeld" altijd voorrang op Pexels
        # zodra `footage.images` gevuld is.
        brief = p.image_brief or {}
        own_photo = brief.get("image_raw_path") or brief.get("image_path") or ""
        if own_photo and Path(own_photo).is_file():
            tpl = replace(tpl, footage=replace(tpl.footage, images=[Path(own_photo)],
                                               local_videos=[]))
        res = vr.render_short(p.project, scenes, out, template=tpl)
    except Exception as e:  # noqa: BLE001
        logger.warning("render_pack_video mislukt: %s", e)
        _log_video_outcome(p, ok=False, detail=str(e)[:200])
        return {"success": False, "error": str(e)[:300]}

    if not res.ok:
        _log_video_outcome(p, ok=False, detail=res.error or "onbekende renderfout")
        return {"success": False, "error": res.error or "renderen mislukt"}

    # 3) Pad opslaan + uitkomstkaart.
    with get_conn() as conn:
        conn.execute("UPDATE social_posts SET video_path=? WHERE id=?", (rel, pack_id))
    _log_video_outcome(p, ok=True, detail=f"{res.scenes} scènes, {round(res.duration,1)}s",
                       artifact=str(out))
    return {
        "success": True,
        "video_url": f"/api/social-content/packs/{pack_id}/video",
        "video_path": rel,
        "duration": res.duration,
        "scenes": res.scenes,
        "voice": res.voice,
    }


def _log_video_outcome(pack: SocialPack, *, ok: bool, detail: str, artifact: str = "") -> None:
    try:
        from .outcomes import log_outcome
        log_outcome(
            project=pack.project,
            action="social_video_gerenderd",
            detail=f"Video voor '{pack.theme}': {detail}",
            artifact=artifact,
            next_step=("Bekijk de video in Social Creatie en keur het pack goed."
                       if ok else "Bekijk de fout en probeer opnieuw te renderen."),
            status="ok" if ok else "error",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("log_outcome (video) mislukt: %s", e)


def _allowed_platforms_for(project: str) -> List[str]:
    """Lees de per-site platform-restrictie uit de `sites`-tabel.

    Leeg/None = geen restrictie (alle PLATFORMS). Een komma-gescheiden waarde
    in `auto_social_platforms` (bv. 'linkedin') begrenst waarheen gepost mag
    worden. Zo dwingen we 'Bijeen → alleen LinkedIn' af op infrastructuurniveau,
    niet alleen in de UI.
    """
    try:
        from ..domains.seo import sites as sites_svc
        s = sites_svc.find_site_by_project(project)
        if not s:
            return []
        raw = (s.get("auto_social_platforms") or "").strip()
        if not raw:
            return []
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    except Exception:
        return []


async def publish_pack(pack_id: str, platform: str) -> Dict:
    """Plaats een goedgekeurd pack op één platform.

    FB/IG/Twitter: echte posting via de bestaande social-services (als geconfigureerd).
    LinkedIn/TikTok: plak-adapter (manual=True) — consistent met social_inbox.

    Een pack kan naar MEERDERE platformen tegelijk (auto-poster plaatst FB + IG
    in één run). Daarom blokkeert de guard NIET op status='posted' — een pack dat
    al op FB staat mag nog steeds op IG. We houden per-platform bij WELKE kanalen
    al gepost hebben in posted_result_json['_platforms'], zodat een kanaal nooit
    dubbel gepost wordt. Een pack dat nog 'pending_review'/'rejected' is, blijft
    geblokkeerd — je moet eerst keuren.
    """
    if platform not in PLATFORMS:
        return {"success": False, "error": f"Onbekend platform: {platform}"}
    p = get_pack(pack_id)
    if not p:
        return {"success": False, "error": "Pack niet gevonden"}
    # Per-site platform-restrictie (wereldklasse, 18 aug 2026). Sommige sites
    # mogen alleen op bepaalde kanalen plaatsen (bv. Bijeen → alleen LinkedIn).
    # Leeg veld = geen restrictie (alle PLATFORMS toegestaan). Een niet-toegestaan
    # platform wordt geweigerd i.p.v. stiekem op een verkeerd kanaal te posten.
    allowed = _allowed_platforms_for(p.project)
    if allowed and platform not in allowed:
        return {"success": False, "error":
                f"{p.project} mag alleen op {', '.join(allowed)} plaatsen — "
                f"{platform} is geblokkeerd."}
    # Alleen expliciet niet-goedgekeurde packs blokkeren. 'approved' én 'posted'
    # (al op een ander kanaal geplaatst) zijn beide toegestaan.
    if p.status in ("pending_review", "rejected"):
        return {"success": False, "error": "Pack is niet goedgekeurd (status=%s)" % p.status}
    # Dubbel-post op hetzelfde kanaal voorkomen.
    prev = p.posted_result or {}
    done = prev.get("_platforms") or []
    if platform in done:
        return {"success": True, "manual": prev.get(platform, {}).get("manual", False),
                "url": prev.get(platform, {}).get("url", ""),
                "detail": f"{platform} al geplaatst — overgeslagen.", "already": True}

    text = (p.copy or {}).get(platform, "")
    if not text:
        return {"success": False, "error": f"Geen {platform}-tekst in dit pack"}

    result: Dict = {"success": False}
    try:
        if platform == "facebook":
            from . import facebook as svc
            ib = p.image_brief or {}
            img_path = ib.get("image_path", "")
            # idea_url/idea_query (gezet door social_auto._pick_grounded_idea)
            # voeden dezelfde FB→SEO-meetlus als de Facebook Deluxe-agent — zonder
            # dit was een auto-post onzichtbaar voor fb_seo_impact.py.
            # cta_url: alleen als noodgreep als er géén idea_url is (evergreen/
            # CTA-post zonder datagedreven onderwerp) — anders bleef zo'n post
            # zonder énige link in de eerste reactie staan (21 aug 2026).
            cta_url = None
            if not p.idea_url:
                try:
                    from ..domains.seo.sites import find_site_by_project
                    site = find_site_by_project(p.project)
                    cta_url = (site or {}).get("base_url") or None
                except Exception:
                    cta_url = None
            result = await svc.post_update(text, p.idea_url or None, p.project,
                                           image_path=img_path or None,
                                           query=p.idea_query or None,
                                           cta_url=cta_url)
        elif platform == "instagram":
            # IG vereist een publieke image_url. Als de pack een gegenereerde
            # asset heeft (image_brief.image_url) én die publiek bereikbaar is,
            # posten we die direct. Anders: plak-adapter met de lokale file.
            ib = p.image_brief or {}
            img_url = ib.get("image_url", "")
            if img_url and img_url.startswith("http"):
                from . import instagram as svc
                result = await svc.post_image(img_url, text, p.project)
            else:
                result = {"success": False, "error": "manual",
                          "manual": True,
                          "detail": "Instagram vereist een publieke afbeelding. " +
                                    (f"Asset lokaal: {ib.get('image_path','')}. " if ib.get("image_path") else "") +
                                    "Zet IMPACTOS_PUBLIC_HOST (of NETLIFY_TOKEN) zodat de asset " +
                                    "publiek wordt, of post handmatig met de gegenereerde file."}
        elif platform == "twitter":
            from . import twitter as svc
            result = await svc.post_update(text, None, p.project)
        elif platform == "linkedin":
            from . import linkedin as li
            try:
                li_res = await li.post_update(text, None, p.project)
                if li_res.get("success"):
                    result = li_res
                else:
                    # Posten mislukt (bv. token verlopen) — val terug op plak-adapter
                    # zodat de gebruiker de tekst tenminste kan kopiëren.
                    result = {"success": False, "error": "manual", "manual": True,
                              "detail": f"LinkedIn-post mislukt: {li_res.get('error','')}. "
                                        f"Kopieer de tekst en plaats handmatig."}
            except Exception as e:
                result = {"success": False, "error": "manual", "manual": True,
                          "detail": f"LinkedIn-post fout: {e}. Kopieer de tekst en plaats handmatig."}
        elif platform == "tiktok":
            result = {"success": False, "error": "manual", "manual": True,
                      "detail": "TikTok-video vereist het scriptpack + beeld. Monteer in "
                                "CapCut/TikTok en post handmatig (of gebruik de posting-API "
                                "met een kant-en-klare clip)."}
    except Exception as e:
        result = {"success": False, "error": str(e)[:300]}

    # Sla het resultaat op per-platform, zonder eerdere kanalen te overschrijven.
    # We bewaren een dict {platform: result, ..., "_platforms": [..lijst..]}.
    posted = bool(result.get("success")) and not result.get("manual")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT posted_result_json FROM social_posts WHERE id=?", (pack_id,)
        ).fetchone()
        prev = {}
        try:
            prev = json.loads((row[0] or "{}")) if row else {}
        except Exception:
            prev = {}
        # Behoud eerdere platform-resultaten (bv. FB al geplaatst, nu IG).
        merged = {k: v for k, v in prev.items() if k != "_platforms"}
        merged[platform] = result
        done = list(prev.get("_platforms") or [])
        if platform not in done:
            done.append(platform)
        merged["_platforms"] = done
        # Status: 'posted' zodra één écht kanaal live is; anders 'approved'.
        any_live = any(
            bool(merged[p].get("success")) and not merged[p].get("manual")
            for p in done
        )
        conn.execute(
            "UPDATE social_posts SET posted_result_json=?, status=? WHERE id=?",
            (json.dumps(merged, ensure_ascii=False),
             "posted" if any_live else "approved", pack_id),
        )
    return result

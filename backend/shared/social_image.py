"""Social Image — wereldklasse beeld-generatie voor social posts.

Primaire bron: Pexels (echte, rechtenvrije documentaire-fotografie — geen
AI-gezichtshallucinaties, geschikt voor een merk dat met échte mensen werkt).
Fallback: FAL/FLUX (alleen als Pexels leeg is of FAL_KEY gezet is).

Daarna wordt de merk-overlay BRANDING erin gebrand (Pillow): warm amber
(#e5a500), vignet voor leesbaarheid, display-font voor de kop, exact de
huisstijl die BewaardVoorJou al op Instagram gebruikt ("Elk verhaal telt"
in goud op een warme foto). Zo wordt elke AI/stock-foto een on-brand asset
in plaats van een losse stock-afbeelding.

De asset wordt opgeslagen in data/uploads/ en de (lokale) URL + pad worden
teruggegeven zodat social_content.py / de publish-routers ze kunnen gebruiken.

Review-gate: deze module genereert ALLEEN een asset; plaatsen gebeurt pas na
menselijke goedkeuring in de Social Creatie-tab (net als tekst/video).
"""
from __future__ import annotations

import io
import json
import logging
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# ── Merk-stijl (BewaardVoorJou) ────────────────────────────────────────────
# Warm amber merkkleur + lichte, warme tekst. Gouden overlay zoals op IG-feed.
BRAND = {
    "bewaardvoorjou": {
        "amber": (229, 165, 0),       # #e5a500
        "gold_text": (245, 222, 130),  # zacht goud voor overlay-tekst
        "warm_white": (255, 250, 240),
        "shadow": (20, 14, 8),
    },
}
_DEFAULT_BRAND = {"amber": (229, 165, 0), "gold_text": (245, 222, 130),
                  "warm_white": (255, 250, 240), "shadow": (20, 14, 8)}


def _brand_for(project: str) -> dict:
    key = (project or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    return BRAND.get(key, _DEFAULT_BRAND)


UPLOAD_ROOT = Path(os.environ.get("IMPACTOS_UPLOAD_ROOT", os.environ.get("AGENTOS_UPLOAD_ROOT", "D:/APPS/agentos/data/uploads")))
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

# Lokale beeld-cache: echte, rechtenvrije Pexels-foto's die al in de repo staan.
# Deze bron werkt ALTJD (geen API-key nodig) en is de primaire fallback wanneer
# Pexels/FAL niet beschikbaar zijn. Voor BewaardVoorJou staan hier documentaire
# foto's van familietaferelen — exact de merk-esthetiek.
REPO_ROOT = Path(os.environ.get("IMPACTOS_REPO_ROOT", os.environ.get("AGENTOS_REPO_ROOT", "D:/APPS/agentos")))
_LOCAL_PHOTO_DIRS = [
    REPO_ROOT / "projects" / "bewaardvoorjou" / "photos",
    REPO_ROOT / "projects" / "bewaardvoorjou" / "video" / "_pexels_cache",
]


def _local_photo(project: str = "") -> Tuple[Optional[bytes], str]:
    """Kies een willekeurige echte foto uit de lokale cache (round-robin-achtig).

    Zoekt EERST in de eigen fotobibliotheek van het project
    (`projects/<project>/photos/`, squash-bewust via `social_style._project_dirs`
    — dezelfde map als `social_content.list_project_photos`). Tot 21 aug 2026
    was deze fallback hardgecodeerd op `_LOCAL_PHOTO_DIRS` (BewaardVoorJou),
    ongeacht welk project aanriep: LiefdeVoorIedereen heeft 24 eigen documentaire
    foto's in `projects/liefde voor iedereen/photos/` liggen die nooit werden
    gebruikt, terwijl elke Pexels-miss in plaats daarvan een willekeurige
    BewaardVoorJou-stockfoto opleverde — verkeerd merk, en een kleine gedeelde
    pool die posts op elkaar liet lijken. Alleen als het project zélf geen
    foto's heeft, valt dit terug op de generieke BewaardVoorJou-cache (het
    gedrag van vóór deze fix), zichtbaar gemaakt via de teruggegeven bron-label.
    """
    import random
    from . import social_style

    def _scan(dirs) -> list:
        out = []
        for d in dirs:
            if d.exists():
                for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                    out.extend(d.glob(ext))
        # filter de eerder gegenereerde proof-bestanden eruit
        return [p for p in out if "_proof" not in p.name and "_overlay" not in p.name]

    cands: list = []
    source = "local_cache"
    if project:
        cands = _scan(base / "photos" for base in social_style._project_dirs(project))
    if not cands:
        cands = _scan(_LOCAL_PHOTO_DIRS)
        source = "local_cache_fallback" if project else "local_cache"
    if not cands:
        return None, source
    pick = random.choice(cands)
    try:
        return pick.read_bytes(), source
    except Exception:
        return None, source


# ── Pexels (echte fotografie) ───────────────────────────────────────────────

def _pexels_search(query: str, per_page: int = 1,
                   orientation: str = "square") -> Optional[str]:
    """Zoek één foto-URL op Pexels. Retourneert de grootste beschikbare URL of None.

    De oriëntatie volgt het beeldformaat van het merk: een staande 4:5-post uit
    een vierkante bron center-croppen snijdt hoofden af, en juist bij portretten
    van mensen is dat het hele beeld.
    """
    if not PEXELS_KEY:
        return None
    q = urllib.parse.quote(query)
    url = (f"https://api.pexels.com/v1/search?query={q}&per_page={per_page}"
           f"&size=large&orientation={orientation}")
    req = urllib.request.Request(url, headers={"Authorization": PEXELS_KEY})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        logger.warning("Pexels search mislukt: %s", e)
        return None
    photos = data.get("photos") or []
    if not photos:
        return None
    # Neem de eerst relevante; grootste beschikbare variant.
    src = photos[0].get("src", {})
    return src.get("large2x") or src.get("large") or src.get("original")


def _download(url: str) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        logger.warning("Download mislukt (%s): %s", url, e)
        return None


# ── FAL/FLUX (fallback voor sfeer) ──────────────────────────────────────────

def _fal_generate(prompt: str) -> Optional[bytes]:
    """Genereer een beeld via FAL REST. Vereist FAL_KEY. Valt anders terug op None."""
    if not FAL_KEY:
        return None
    # FAL queue + result endpoints (FLUX.1 schnell / dev).
    import httpx
    try:
        with httpx.Client(timeout=120) as client:
            qr = client.post(
                "https://queue.fal.run/fal-ai/flux/dev",
                headers={"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"},
                json={"prompt": prompt, "num_images": 1, "image_size": "square_hd"},
            )
            if qr.status_code not in (200, 201):
                logger.warning("FAL queue HTTP %s: %s", qr.status_code, qr.text[:200])
                return None
            status_url = qr.headers.get("location")
            # Voor flux/dev is het resultaat vaak direct in de body.
            try:
                body = qr.json()
                img = (body.get("images") or [{}])[0].get("url")
                if img:
                    return _download(img)
            except Exception:
                pass
            if status_url:
                # polling zou hier moeten gebeuren; voor nu: niet ondersteund zonder status.
                logger.warning("FAL async (status-url) niet ondersteund in fallback-path")
                return None
    except Exception as e:  # noqa: BLE001
        logger.warning("FAL generatie mislukt: %s", e)
        return None
    return None


# ── Overlay / branding (Pillow) ─────────────────────────────────────────────

def _find_font(bold: bool = True) -> Optional[Path]:
    for f in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
              r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        p = Path(f)
        if p.exists():
            return p
    return None


def _load_font(size: int, bold: bool = True,
               path: Optional[Path] = None) -> ImageFont.ImageFont:
    """Laad een font; een meegegeven pad (uit het huisstijl-profiel) gaat vóór.

    Valt een eigen merk-font weg, dan komt het systeem-font terug — nooit een
    lege render. Wel luid in de log: een serif-merk dat stil in Arial verschijnt
    is een huisstijlfout die op het beeld zelf niet als fout te zien is.
    """
    if path:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as e:
            logger.warning("Merk-font onbruikbaar (%s), systeem-font gebruikt: %s", path, e)
    p = _find_font(bold)
    if p:
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), trial, font=font)[2] - draw.textbbox((0, 0), trial, font=font)[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _paste_logo(img: Image.Image, logo_path: Path, positie: str, breedte: int) -> Image.Image:
    """Plak het merk-logo in het beeld (transparantie blijft behouden)."""
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except OSError as e:
        logger.warning("Logo onleesbaar (%s): %s", logo_path, e)
        return img
    W, H = img.size
    breedte = max(40, min(int(breedte), W // 2))
    hoogte = max(1, int(logo.height * breedte / logo.width))
    logo = logo.resize((breedte, hoogte))
    m = int(W * 0.055)
    plek = {
        "top-left": (m, m),
        "top-right": (W - breedte - m, m),
        "bottom-left": (m, H - hoogte - m),
        "bottom-right": (W - breedte - m, H - hoogte - m),
    }.get((positie or "top-left").lower(), (m, m))
    img.paste(logo, plek, logo)
    return img


def _brand_overlay(img: Image.Image, headline: str, subtext: str, project: str) -> Image.Image:
    """Brand de merk-overlay in een foto volgens het huisstijl-profiel.

    Het plan van BewaardVoorJou schrijft dit letterlijk voor: "gouden serif-titel
    + witte serif-onderschrift op een donker transparant vlak, logo linksboven,
    www.BewaardVoorJou.nl onderaan". Dat stond hier tot 16 aug 2026 niet — de
    kop kwam in Arial Bold zonder vlak, zonder logo en zonder URL, terwijl het
    merk-font (Playfair Display) al in de projectmap lag.

    Zonder profiel is de render identiek aan wat hier eerder stond: vignet-verloop,
    systeem-font, goud + warm wit. Zo verandert er voor de andere elf projecten
    niets.
    """
    from . import social_style

    b = _brand_for(project)
    style = social_style.load_style(project)
    ov = style.overlay
    heeft_profiel = style.bron == "style.json"

    kop_kleur = social_style.hex_to_rgb(ov.kop_kleur, b["gold_text"]) if heeft_profiel else b["gold_text"]
    sub_kleur = social_style.hex_to_rgb(ov.subtekst_kleur, b["warm_white"]) if heeft_profiel else b["warm_white"]
    kop_font_pad = style.resolve(ov.font_path) if heeft_profiel else None
    sub_font_pad = style.resolve(ov.font_path_regular or ov.font_path) if heeft_profiel else None

    W, H = img.size

    # 1) Vignet / onderste verloop voor contrast (tekst komt onderin).
    grad = Image.new("L", (W, H), 0)
    gdraw = ImageDraw.Draw(grad)
    for y in range(H):
        alpha = int(200 * max(0, (y - H * 0.45) / (H * 0.55)) ** 1.3)
        gdraw.line([(0, y), (W, y)], fill=alpha)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.putalpha(grad)
    img = Image.alpha_composite(img.convert("RGBA"), shadow)

    draw = ImageDraw.Draw(img, "RGBA")
    margin = int(W * 0.08)
    max_w = W - 2 * margin

    # 2) Meten vóór tekenen: het donkere vlak moet weten hoe hoog de tekst wordt.
    head_size = max(40, int(W * 0.085))
    head_font = _load_font(head_size, bold=True, path=kop_font_pad)
    head_lines = _wrap(draw, headline, head_font, max_w)[:3]
    lh = int(head_size * 1.15)

    sub_size = max(24, int(W * 0.04))
    sub_font = _load_font(sub_size, bold=False, path=sub_font_pad)
    sub_lines = _wrap(draw, subtext, sub_font, max_w)[:2] if subtext else []
    slh = int(sub_size * 1.3)

    footer = ov.footer_tekst if heeft_profiel else ""
    footer_size = max(20, int(W * 0.030))
    footer_font = _load_font(footer_size, bold=False, path=sub_font_pad)
    footer_h = int(footer_size * 1.8) if footer else 0

    blok_h = len(head_lines) * lh + (int(head_size * 0.5) + len(sub_lines) * slh if sub_lines else 0)
    y = H - margin - footer_h - blok_h

    # 3) Donker transparant vlak achter de tekst (uit het profiel).
    if heeft_profiel and ov.vlak and (head_lines or sub_lines):
        pad = int(W * 0.035)
        alpha = max(0, min(255, int(255 * ov.vlak_opacity)))
        draw.rectangle(
            [(margin - pad, y - pad), (W - margin + pad, y + blok_h + pad)],
            fill=(*b["shadow"], alpha),
        )

    # 4) Kop, met schaduw voor leesbaarheid ook zonder vlak.
    for line in head_lines:
        draw.text((margin + 2, y + 2), line, font=head_font, fill=(*b["shadow"], 180))
        draw.text((margin, y), line, font=head_font, fill=kop_kleur)
        y += lh

    if sub_lines:
        y += int(head_size * 0.5)  # ruimte tussen kop en onderschrift
        for line in sub_lines:
            draw.text((margin + 2, y + 2), line, font=sub_font, fill=(*b["shadow"], 160))
            draw.text((margin, y), line, font=sub_font, fill=sub_kleur)
            y += slh

    # 5) Vaste merk-URL onderaan.
    if footer:
        fy = H - margin // 2 - footer_size
        draw.text((margin + 1, fy + 1), footer, font=footer_font, fill=(*b["shadow"], 160))
        draw.text((margin, fy), footer, font=footer_font, fill=kop_kleur)

    img = img.convert("RGB")

    # 6) Logo (als laatste, zodat het vignet er niet overheen ligt).
    if heeft_profiel and ov.logo_path:
        logo_pad = style.resolve(ov.logo_path)
        if logo_pad:
            img = _paste_logo(img.convert("RGBA"), logo_pad, ov.logo_positie,
                              int(W * ov.logo_breedte / 1080)).convert("RGB")
        else:
            logger.warning("Logo-pad uit style.json niet gevonden voor %s: %s",
                           project, ov.logo_path)
    return img


# ── Publieke URL-strategie ─────────────────────────────────────────────────
# ImpactOS draait op localhost; Meta/IG bereiken die niet. Als er een publieke
# host is (Netlify/Vercel via env), uploaden we daarheen. Anders blijft de
# asset lokaal en toont de review-gate "post handmatig met deze file".
def _public_url_for(path: Path) -> str:
    host = os.getenv("IMPACTOS_PUBLIC_HOST", "").rstrip("/")
    if host:
        return f"{host}/uploads/{path.name}"
    return f"/uploads/{path.name}"


# ── Hoofd-entry ─────────────────────────────────────────────────────────────

# Thema -> Pexels-zoekquery (documentair, mensgericht, warm). Geen gezichts-
# hallucinatie-risk omdat dit echte stock-fotografie is.
_THEME_QUERIES = {
    "verhaal": "elderly person holding old photograph album",
    "ouder": "grandmother grandson kitchen storytelling",
    "grootouder": "grandparents family warm living room",
    "familie": "family three generations together laughing",
    "herinnering": "old hands holding letters memories",
    "luisteren": "granddaughter listening to grandfather storytelling",
    "verjaardag": "elderly birthday family celebration",
    "koffie": "old couple coffee table warm window light",
}

_FAL_FALLBACK_PROMPT = (
    "documentary photography, warm natural light, an elderly person at a kitchen "
    "table sharing a life story with a younger family member, candid, film grain, "
    "shallow depth of field, nostalgic, no text, editorial style --ar 1:1"
)


def _target_size(aspect: str) -> Tuple[int, int]:
    """'4:5' → (1080, 1350). Onbekende verhouding houdt het vierkant."""
    known = {"1:1": (1080, 1080), "4:5": (1080, 1350),
             "9:16": (1080, 1920), "16:9": (1920, 1080)}
    return known.get((aspect or "").strip(), (1080, 1080))


def _render_typographic(project: str, headline: str, subtext: str,
                        doel_w: int, doel_h: int) -> Dict:
    """Bijeen 'Inzicht'-formaat: typografische poster op warme crème achtergrond.

    Geen foto-onderlaag — de kracht zit in de typografie (exact de stijl van
    projects/bijeen/social/posters/post_2_1.png):
      - crème achtergrond (#FAF6F0)
      - serif-hoofdkop in inkt (#22303C), één accentwoord in terracotta (#E4603B)
      - veel whitespace, links uitgelijnd
      - sans-serif footer (logo linksonder + bijeen.app rechtsonder)
    Gedeeld met de losse render_poster.py zodat ImpactOS dezelfde look levert.
    """
    from . import social_style
    style = social_style.load_style(project)
    ov = style.overlay
    INK = social_style.hex_to_rgb(ov.kop_kleur, (34, 48, 60))
    ORANGE = social_style.hex_to_rgb(ov.accent_kleur, (228, 96, 59))
    CREAM = social_style.hex_to_rgb(ov.achtergrond_kleur, (250, 246, 240))
    GREY = (110, 110, 110)
    footer_txt = ov.footer_tekst or "bijeen.app"

    # fonts (Windows); serif voor kop, sans voor footer
    F_SERIF_B = r"C:/Windows/Fonts/georgiab.ttf"
    F_SANS = r"C:/Windows/Fonts/arial.ttf"
    F_SANS_B = r"C:/Windows/Fonts/arialbd.ttf"
    LOGO_PATH = r"D:\APPS\Bijeen\welzijnsevent-starter\welzijnsevent\public\Bijeen-logo-icon.png"

    def _font(p, s):
        try:
            return ImageFont.truetype(p, s)
        except Exception:
            return ImageFont.load_default()

    def _tw(d, s, f):
        b = d.textbbox((0, 0), s, font=f)
        return b[2] - b[0]

    img = Image.new("RGB", (doel_w, doel_h), CREAM)
    d = ImageDraw.Draw(img)
    L = int(doel_w * 0.089)  # ~96px op 1080

    # BIJEEN-tag bovenaan (gewijd, oranje)
    tag_f = _font(F_SANS_B, int(doel_w * 0.037))
    d.text((L, int(doel_h * 0.11)), "BIJEEN", font=tag_f, fill=ORANGE)

    # Grote serif-kop: elk woord op eigen regel, accentwoord in oranje.
    title = headline or "Jouw verhaal telt"
    n_words = len(title.split())
    start_size = (int(doel_w * 0.10) if n_words <= 4
                  else int(doel_w * 0.085) if n_words <= 6
                  else int(doel_w * 0.072))
    y = int(doel_h * 0.20)
    lh = int(start_size * 1.08)
    base_f = _font(F_SERIF_B, start_size)
    for word in title.split():
        f = base_f
        while _tw(d, word, f) > (doel_w - 2 * L) and f.size > int(doel_w * 0.044):
            f = _font(F_SERIF_B, f.size - 4)
        is_acc = word.lower().strip(".,:?!") in ("bijeen", "welzijn", "verbinding",
                                                  "impact", "gratis", "buurt")
        d.text((L, y), word, font=f, fill=ORANGE if is_acc else INK)
        y += lh
        if y > doel_h - int(doel_h * 0.28):
            break

    # onderschrift (grijs, sans)
    if subtext:
        y += int(doel_h * 0.03)
        sub_f = _font(F_SANS, int(doel_w * 0.035))
        words = subtext.split()
        line, lines = "", []
        for w in words:
            test = (line + " " + w).strip()
            if _tw(d, test, sub_f) > (doel_w - 2 * L) and line:
                lines.append(line); line = w
            else:
                line = test
        if line:
            lines.append(line)
        for ln in lines[:3]:
            d.text((L, y), ln, font=sub_f, fill=GREY)
            y += int(doel_w * 0.046)

    # footer rechtsonder (oranje, sans-bold)
    foot_f = _font(F_SANS_B, int(doel_w * 0.031))
    fw = _tw(d, footer_txt.upper(), foot_f)
    fx = min(doel_w - L, doel_w - int(doel_w * 0.037)) - fw
    fx = max(L, fx)
    fy = doel_h - int(doel_h * 0.07)
    d.text((fx, fy), footer_txt.upper(), font=foot_f, fill=ORANGE)

    # logo linksonder (echt Bijeen-logo) + 'Bijeen' ernaast
    logo_s = int(doel_w * 0.06)
    if os.path.exists(LOGO_PATH):
        try:
            lg = Image.open(LOGO_PATH).convert("RGBA").resize((logo_s, logo_s), Image.LANCZOS)
            img.paste(lg, (int(L), int(fy)), lg)
        except Exception:
            pass
    wm_f = _font(F_SANS_B, int(doel_w * 0.028))
    d.text((int(L + logo_s + doel_w * 0.013), int(fy + doel_w * 0.017)),
           "Bijeen", font=wm_f, fill=INK)

    name = f"{uuid.uuid4().hex}.png"
    out = UPLOAD_ROOT / name
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return {"success": True, "url": _public_url_for(out), "path": str(out),
            "source": "typografisch"}


def _render_stelling(project: str, headline: str, subtext: str,
                     doel_w: int, doel_h: int) -> Dict:
    """LiefdeVoorIedereen 'Stelling'-formaat: koraal badge-pil + grote stelling-kop
    op een crème achtergrond, géén foto (21 aug 2026).

    Repliceert het ontwerp dat Vincent al buiten ImpactOS om handmatig plaatste
    (Facebook, 18 aug 2026: rode 'Stelling'-badge, vetgedrukte kop, logo
    rechtsonder). Dat sjabloon bestond tot dan nergens in code — `ImageBrief.
    canva_note` beschrijft het alleen als instructie aan een mens — waardoor
    élke geautomatiseerde LVI-post via de generieke foto-overlay liep (donkere
    stockfoto + kop onderin) in plaats van de echte huisstijl. Net als
    `_render_typographic` (Bijeen) rendert dit zonder foto-onderlaag: de kracht
    zit in de typografie, niet in stockfotografie.
    """
    from . import social_style
    style = social_style.load_style(project)
    ov = style.overlay
    CREAM = social_style.hex_to_rgb(ov.achtergrond_kleur, (250, 246, 240))
    INK = social_style.hex_to_rgb(ov.kop_kleur, (15, 30, 41))
    BADGE = social_style.hex_to_rgb(ov.accent_kleur, (214, 81, 106))
    GREY = (90, 94, 98)
    badge_tekst = (ov.badge_tekst or "Stelling").upper()

    kop_font_pad = style.resolve(ov.font_path) if ov.font_path else None
    sub_font_pad = style.resolve(ov.font_path_regular or ov.font_path) if ov.font_path else None

    img = Image.new("RGB", (doel_w, doel_h), CREAM)
    draw = ImageDraw.Draw(img)
    margin = int(doel_w * 0.09)
    max_w = doel_w - 2 * margin

    # Meten vóór tekenen: een korte stelling (2 regels) en een lange (5 regels)
    # moeten allebei goed ogen — vaste top-ankering liet een korte kop hoog
    # bovenin staan met een lege onderhelft. Het blok wordt daarom verticaal
    # gecentreerd in de ruimte tussen de badge en de logo-zone.
    badge_font = _load_font(int(doel_w * 0.036), bold=True, path=kop_font_pad)
    bb = draw.textbbox((0, 0), badge_tekst, font=badge_font)
    bt_w, bt_h = bb[2] - bb[0], bb[3] - bb[1]
    pad_x, pad_y = int(doel_w * 0.035), int(doel_w * 0.018)
    badge_w, badge_h = bt_w + 2 * pad_x, bt_h + 2 * pad_y

    head_size = max(40, int(doel_w * 0.088))
    head_font = _load_font(head_size, bold=True, path=kop_font_pad)
    head_lines = _wrap(draw, headline or "", head_font, max_w)[:5]
    lh = int(head_size * 1.18)

    sub_lines: list = []
    sub_font = None
    sub_size = 0
    if subtext:
        sub_size = max(22, int(doel_w * 0.036))
        sub_font = _load_font(sub_size, bold=False, path=sub_font_pad)
        sub_lines = _wrap(draw, subtext, sub_font, max_w)[:3]

    gap_badge_head = int(doel_h * 0.06)
    gap_head_sub = int(doel_h * 0.02) if sub_lines else 0
    content_h = (badge_h + gap_badge_head + len(head_lines) * lh
                + gap_head_sub + len(sub_lines) * int(sub_size * 1.35))

    top_bound = int(doel_h * 0.09)
    bottom_bound = doel_h - int(doel_h * 0.16)  # laat ruimte voor het logo
    beschikbaar = max(content_h, bottom_bound - top_bound)
    start_y = top_bound + max(0, (beschikbaar - content_h) // 2)

    # 1) Badge-pil linksboven het blok.
    badge_y = start_y
    draw.rounded_rectangle(
        [(margin, badge_y), (margin + badge_w, badge_y + badge_h)],
        radius=badge_h // 2, fill=BADGE,
    )
    draw.text((margin + pad_x, badge_y + pad_y - bb[1]), badge_tekst,
              font=badge_font, fill=(255, 255, 255))

    # 2) Grote stelling-kop.
    y = badge_y + badge_h + gap_badge_head
    for line in head_lines:
        draw.text((margin, y), line, font=head_font, fill=INK)
        y += lh

    # 3) Onderschrift (grijs, kleiner) — bv. 'Eens of oneens? Laat je reactie achter.'
    if sub_lines:
        y += gap_head_sub
        for line in sub_lines:
            draw.text((margin, y), line, font=sub_font, fill=GREY)
            y += int(sub_size * 1.35)

    # 4) Merk-logo rechtsonder (alleen als het profiel er een heeft — geen
    # placeholder-icoon, want een ontbrekend logo hoort zichtbaar te zijn in
    # de log, niet stil vervangen).
    if ov.logo_path:
        logo_pad = style.resolve(ov.logo_path)
        if logo_pad:
            img = _paste_logo(img.convert("RGBA"), logo_pad,
                              ov.logo_positie or "bottom-right",
                              int(doel_w * ov.logo_breedte / 1080)).convert("RGB")
        else:
            logger.warning("Logo-pad uit style.json niet gevonden voor %s: %s",
                           project, ov.logo_path)

    name = f"{uuid.uuid4().hex}.png"
    out = UPLOAD_ROOT / name
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return {"success": True, "url": _public_url_for(out), "path": str(out),
            "source": "stelling"}


def _crop_and_brand(raw: bytes, project: str, headline: str, subtext: str) -> Dict:
    """Crop naar het formaat van het merk en brand de overlay erin.

    Gedeeld door `generate_social_image` (automatisch gezocht beeld) en
    `brand_uploaded_image` (een eigen render — Midjourney, een foto, een stock-
    aankoop). Vóór dit bestaan kende de review-gate maar één weg naar een
    on-brand asset: automatisch zoeken. Voor BewaardVoorJou is dat precies
    verkeerd — het plan draait op een vast Midjourney-stijlblok, en die beelden
    komen per definitie van buiten het systeem.
    """
    from . import social_style
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    doel_w, doel_h = _target_size(social_style.load_style(project).aspect)
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Afbeelding kon niet gelezen worden: {e}"}

    ratio = doel_w / doel_h
    if img.width / img.height > ratio:
        crop_h = img.height
        crop_w = int(crop_h * ratio)
    else:
        crop_w = img.width
        crop_h = int(crop_w / ratio)
    left = (img.width - crop_w) // 2
    top = (img.height - crop_h) // 2
    img = img.crop((left, top, left + crop_w, top + crop_h))
    img = img.resize((doel_w, doel_h))

    # Bewaar de ongebrande crop apart, vóór de overlay-tekst erin wordt gebrand:
    # de blogvideo/TikTok-video van hetzelfde pack gebruikt dit beeld als
    # achtergrond (21 aug 2026 — de video toonde tot dan toe een willekeurige
    # Pexels-stockfoto die niets met de merkafbeelding te maken had). Het bevat
    # zelf géén tekst, want de video brandt er zijn eigen ondertitels overheen —
    # de gebrande versie zou dubbele tekst geven.
    raw_name = f"{uuid.uuid4().hex}.png"
    raw_out = UPLOAD_ROOT / raw_name
    img.save(raw_out, format="PNG")

    style = social_style.load_style(project)
    if style.overlay.modus == "foto-onderschrift":
        img = _brand_photo_caption(img, headline or "", subtext or "", project, style)
    else:
        img = _brand_overlay(img, headline or "", subtext or "", project)

    name = f"{uuid.uuid4().hex}.png"
    out = UPLOAD_ROOT / name
    img.save(out, format="PNG")
    return {"success": True, "url": _public_url_for(out), "path": str(out),
            "raw_path": str(raw_out)}


def _brand_photo_caption(img: Image.Image, headline: str, subtext: str,
                         project: str, style) -> Image.Image:
    """Foto boven, accentbalk, crème onderschrift-vlak met kop + logo/wordmark/
    tagline onderaan (DatingAssistent-stijl, 21 aug 2026, zie de bestaande
    Facebook-post 'Alleen zijn is niet hetzelfde...' als referentie — die had
    wél de foto+kop-opbouw maar nog geen logo in het beeld zelf gebakken)."""
    from . import social_style
    ov = style.overlay
    INK = social_style.hex_to_rgb(ov.kop_kleur, (26, 23, 20))
    CREAM = social_style.hex_to_rgb(ov.achtergrond_kleur, (247, 243, 236))
    ACCENT = social_style.hex_to_rgb(ov.accent_kleur, (242, 98, 14))
    GREY = (110, 105, 98)

    W, H = img.size
    bar_h = max(4, int(H * 0.009))

    margin = int(W * 0.075)
    max_w = W - 2 * margin
    kop_font_pad = style.resolve(ov.font_path) if ov.font_path else None
    sub_font_pad = style.resolve(ov.font_path_regular or ov.font_path) if ov.font_path else None

    head_size = max(36, int(W * 0.068))
    head_font = _load_font(head_size, bold=True, path=kop_font_pad)
    sub_size = max(20, int(W * 0.032))
    sub_font = _load_font(sub_size, bold=False, path=sub_font_pad) if subtext else None
    footer_h = int(H * 0.10)

    # Meten vóór tekenen (zelfde discipline als _render_stelling): een lange
    # kop (tot 4 regels) duwde het onderschrift tot in de logo/wordmark-zone
    # eronder — beide tekstblokken overlapten zichtbaar op het beeld (22 aug
    # 2026, DatingAssistent 40+). De paneelhoogte hing vast op 40% en groeide
    # nooit mee met de tekst. `_wrap` heeft alleen een ImageDraw nodig, geen
    # kant-en-klaar canvas, dus meten we de kop/onderschrift-hoogte eerst en
    # laten de fotoband (nooit onder 40% van het beeld, anders oogt het niet
    # meer als een foto-post) plaats maken voor wat er werkelijk nodig is.
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    head_lines = _wrap(measure, headline, head_font, max_w)[:4]
    lh = int(head_size * 1.18)
    sub_lines = _wrap(measure, subtext, sub_font, max_w)[:2] if subtext else []
    content_h = len(head_lines) * lh
    if sub_lines:
        content_h += int(head_size * 0.25) + len(sub_lines) * int(sub_size * 1.3)

    top_pad = int(H * 0.045)
    bottom_pad = int(H * 0.025)
    needed_panel_h = top_pad + content_h + bottom_pad + footer_h
    photo_h = min(int(H * 0.60), max(int(H * 0.40), H - bar_h - needed_panel_h))

    canvas = Image.new("RGB", (W, H), CREAM)
    # Foto-band: verticaal gecentreerde crop uit de al-vierkant-gemaakte foto.
    band_top = max(0, (H - photo_h) // 3)
    band = img.crop((0, band_top, W, min(H, band_top + photo_h)))
    if band.height < photo_h:
        band = band.resize((W, photo_h))
    canvas.paste(band, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(0, photo_h), (W, photo_h + bar_h)], fill=ACCENT)

    text_top = photo_h + bar_h + top_pad
    y = text_top
    for line in head_lines:
        draw.text((margin, y), line, font=head_font, fill=INK)
        y += lh

    if sub_lines:
        y += int(head_size * 0.25)
        for line in sub_lines:
            draw.text((margin, y), line, font=sub_font, fill=GREY)
            y += int(sub_size * 1.3)

    # Logo + wordmark + tagline, onderaan het crème-vlak.
    fy = H - int(footer_h * 0.72)
    logo_s = int(W * 0.075)
    text_x = margin
    if ov.logo_path:
        logo_pad = style.resolve(ov.logo_path)
        if logo_pad:
            try:
                logo = Image.open(logo_pad).convert("RGBA").resize((logo_s, logo_s), Image.LANCZOS)
                canvas.paste(logo, (margin, fy - int(logo_s * 0.15)), logo)
                text_x = margin + logo_s + int(W * 0.02)
            except OSError as e:
                logger.warning("Logo onleesbaar (%s): %s", logo_pad, e)
    if ov.wordmark:
        wm_font = _load_font(int(W * 0.038), bold=True, path=kop_font_pad)
        draw.text((text_x, fy - int(logo_s * 0.05)), ov.wordmark, font=wm_font, fill=INK)
        if ov.footer_tekst:
            tag_font = _load_font(int(W * 0.024), bold=False, path=sub_font_pad)
            draw.text((text_x, fy + int(W * 0.045)), ov.footer_tekst, font=tag_font, fill=GREY)
    elif ov.footer_tekst:
        tag_font = _load_font(int(W * 0.026), bold=False, path=sub_font_pad)
        draw.text((text_x, fy), ov.footer_tekst, font=tag_font, fill=GREY)

    return canvas


def brand_uploaded_image(raw: bytes, project: str, *,
                         headline: str = "", subtext: str = "") -> Dict:
    """Neem een eigen render (Midjourney/foto) en pas de huisstijl toe.

    Zelfde crop + overlay als `generate_social_image`, alleen zonder de
    zoek-fallbackketen — het beeld is er al. `source` is altijd 'upload', zodat
    later te zien blijft welke packs een handgekozen beeld hebben in plaats van
    een automatisch gezochte stockfoto.
    """
    res = _crop_and_brand(raw, project, headline, subtext)
    if res.get("success"):
        res["source"] = "upload"
    return res


def generate_social_image(theme: str, project: str,
                          headline: str = "", subtext: str = "") -> Dict:
    """Genereer een on-brand social afbeelding en sla deze op.

    Retourneert {success, url, path, source, error?}.
    - source: 'pexels' | 'fal' | 'none'
    """
    from . import social_style
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    style = social_style.load_style(project)
    doel_w, doel_h = _target_size(style.aspect)

    # Typografische modus (Bijeen 'Inzicht'-formaat): géén foto-onderlaag,
    # direct op crème renderen in de post_2_1.png-stijl.
    if style.overlay.modus == "typografisch":
        head = headline or (theme or "Jouw verhaal telt")
        return _render_typographic(project, head, subtext or "", doel_w, doel_h)

    # 'Stelling'-modus (LiefdeVoorIedereen): idem geen foto-onderlaag nodig.
    if style.overlay.modus == "stelling":
        head = headline or (theme or "Wat vind jij?")
        return _render_stelling(project, head, subtext or "", doel_w, doel_h)

    # Bepaal zoekquery uit thema (keyword-match, anders generiek).
    q = "elderly family warm storytelling"
    tl = (theme or "").lower()
    for kw, query in _THEME_QUERIES.items():
        if kw in tl:
            q = query
            break

    raw: Optional[bytes] = None
    source = "none"
    if PEXELS_KEY:
        url = _pexels_search(q, orientation="portrait" if doel_h > doel_w else "square")
        if url:
            raw = _download(url)
            if raw:
                source = "pexels"
    if raw is None:
        # Lokale cache: echte foto's uit de repo (geen API nodig). Altijd beschikbaar.
        raw, local_source = _local_photo(project)
        if raw:
            source = local_source
    if raw is None and FAL_KEY:
        raw = _fal_generate(_FAL_FALLBACK_PROMPT)
        if raw:
            source = "fal"

    if raw is None:
        return {"success": False, "error": "Geen beeldbron beschikbaar (Pexels leeg, geen FAL_KEY)"}

    head = headline or (theme or "Jouw verhaal telt")
    res = _crop_and_brand(raw, project, head, subtext or "")
    if res.get("success"):
        res["source"] = source
    return res

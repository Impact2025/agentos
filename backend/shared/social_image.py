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


UPLOAD_ROOT = Path(os.environ.get("AGENTOS_UPLOAD_ROOT", "D:/APPS/agentos/data/uploads"))
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

# Lokale beeld-cache: echte, rechtenvrije Pexels-foto's die al in de repo staan.
# Deze bron werkt ALTJD (geen API-key nodig) en is de primaire fallback wanneer
# Pexels/FAL niet beschikbaar zijn. Voor BewaardVoorJou staan hier documentaire
# foto's van familietaferelen — exact de merk-esthetiek.
REPO_ROOT = Path(os.environ.get("AGENTOS_REPO_ROOT", "D:/APPS/agentos"))
_LOCAL_PHOTO_DIRS = [
    REPO_ROOT / "projects" / "bewaardvoorjou" / "photos",
    REPO_ROOT / "projects" / "bewaardvoorjou" / "video" / "_pexels_cache",
]


def _local_photo() -> Optional[bytes]:
    """Kies een willekeurige echte foto uit de lokale cache (round-robin-achtig)."""
    import random
    cands = []
    for d in _LOCAL_PHOTO_DIRS:
        if d.exists():
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                cands.extend(d.glob(ext))
    # filter de eerder gegenereerde proof-bestanden eruit
    cands = [p for p in cands if "_proof" not in p.name and "_overlay" not in p.name]
    if not cands:
        return None
    pick = random.choice(cands)
    try:
        return pick.read_bytes()
    except Exception:
        return None


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
# AgentOS draait op localhost; Meta/IG bereiken die niet. Als er een publieke
# host is (Netlify/Vercel via env), uploaden we daarheen. Anders blijft de
# asset lokaal en toont de review-gate "post handmatig met deze file".
def _public_url_for(path: Path) -> str:
    host = os.getenv("AGENTOS_PUBLIC_HOST", "").rstrip("/")
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
    img = _brand_overlay(img, headline or "", subtext or "", project)

    name = f"{uuid.uuid4().hex}.png"
    out = UPLOAD_ROOT / name
    img.save(out, format="PNG")
    return {"success": True, "url": _public_url_for(out), "path": str(out)}


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
    doel_w, doel_h = _target_size(social_style.load_style(project).aspect)
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
        raw = _local_photo()
        if raw:
            source = "local_cache"
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

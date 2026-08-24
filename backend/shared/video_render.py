"""Video Render — turn een script in een échte, gerenderde .mp4 (geen scriptpack).

Dit is de MONTAGE-laag die `social_content.py` mist: die schrijft een TikTok-
scriptpack (tekst), dit rendert er een compleet filmpje van. Volledig zelf-
voorzienend op deze machine — nul betaalde API's:

  script → per-scene voiceover (edge-tts, gratis NL neural stem)
         → merk-slide per scene (Pillow, huisstijl uit image_gen)
         → Ken-Burns beweging + ondertitel-branding (ffmpeg)
         → concat + optionele achtergrondmuziek
         → 1080x1920 (9:16) .mp4

Elke scene bepaalt zijn eigen duur uit de lengte van de voiceover (ffprobe),
zodat beeld en spraak synchroon lopen. Beeld komt in v1 uit merk-slides;
Pexels/Midjourney-beeld is een latere upgrade (footage_provider-haak).

Afhankelijkheden: ffmpeg + ffprobe op PATH, edge-tts (pip), Pillow. Geen moviepy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Hergebruik de merk-stijl (kleuren per project) uit de bestaande image-generator.
from .image_gen import _style_for, _load_font, _wrap_to_width, _mix
from .video_template import VideoTemplate, load_template

logger = logging.getLogger(__name__)

# Portrait 9:16 — TikTok/Reels/Shorts.
PORTRAIT = (1080, 1920)

# Standaard NL neural stem. Maarten = mannelijk (Vincent spreekt in de ik-vorm).
# Warme alternatieven: nl-NL-ColetteNeural / nl-NL-FennaNeural (vrouwelijk).
DEFAULT_VOICE = "nl-NL-MaartenNeural"

FPS = 30


# ── Datamodel ──────────────────────────────────────────────────────────────

@dataclass
class Scene:
    """Eén scène: wat de stem vertelt en wat er op het scherm staat."""
    narration: str                 # voiceover-tekst (wat je hoort)
    caption: str = ""              # on-screen tekst (default = eerste zin narration)
    kind: str = "body"             # hook | body | cta — stuurt de slide-opmaak


@dataclass
class RenderResult:
    ok: bool = False
    path: str = ""
    duration: float = 0.0
    scenes: int = 0
    size: str = f"{PORTRAIT[0]}x{PORTRAIT[1]}"
    voice: str = DEFAULT_VOICE
    error: str = ""
    log: List[str] = field(default_factory=list)
    attributions: List[str] = field(default_factory=list)  # bv. Pexels-credits


# ── ffmpeg / ffprobe helpers ────────────────────────────────────────────────

def _run(cmd: List[str], log: List[str]) -> subprocess.CompletedProcess:
    """Draai een commando; gooi met leesbare stderr bij falen."""
    log.append(" ".join(Path(c).name if c.endswith((".png", ".mp3", ".mp4", ".txt")) else c
                        for c in cmd[:2]))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-600:]
        raise RuntimeError(f"{cmd[0]} faalde (code {proc.returncode}): {tail}")
    return proc


def _probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float((proc.stdout or "0").strip())
    except ValueError:
        return 0.0


def _tts(text: str, voice: str, out: Path, rate: str = "+0%", pitch: str = "+0Hz") -> None:
    """Synthetiseer NL-voiceover naar een mp3 met edge-tts (gratis, geen key).

    `rate`/`pitch` zijn edge-tts SSML-prosody-strings (bv. "-12%", "-2Hz") —
    het spreektempo en de toonhoogte, template-instelbaar (zie `VideoTemplate`).
    """
    import edge_tts  # lokaal geïmporteerd zodat de module laadt zonder de dep

    async def _go() -> None:
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await comm.save(str(out))

    asyncio.run(_go())


def _tts_with_timings(text: str, voice: str, out: Path,
                      rate: str = "+0%", pitch: str = "+0Hz") -> List[dict]:
    """Synthetiseer voiceover én lever de woord-voor-woord timing (voor karaoke-captions).

    edge-tts stuurt naast de audio-chunks "WordBoundary"-events (offset/duration in
    100ns-eenheden) — precies wat nodig is om ondertiteling exact met de stem te
    laten meelopen i.p.v. de hele zin statisch te tonen. Faalt de stream-API (oudere
    edge-tts-versie, netwerkhik), dan valt terug op `_tts()` zonder timing — de
    aanroeper toont dan de hele caption als één blok, nooit een kapotte render.
    """
    import edge_tts

    words: List[dict] = []

    async def _go() -> None:
        # boundary="WordBoundary" is verplicht — edge-tts levert standaard alleen
        # SentenceBoundary-events, en dan komt er hier stilzwijgend 0 woorden uit
        # (geen exception) en valt de caption terug op één blok voor de hele zin.
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
        with open(out, "wb") as f:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    f.write(chunk["data"])
                elif chunk.get("type") == "WordBoundary":
                    words.append({
                        "text": str(chunk.get("text", "")).strip(),
                        "start": chunk.get("offset", 0) / 1e7,
                        "end": (chunk.get("offset", 0) + chunk.get("duration", 0)) / 1e7,
                    })

    try:
        asyncio.run(_go())
    except Exception as e:  # noqa: BLE001 — timing is een bonus, geen harde eis
        logger.warning("Woord-timing via edge-tts mislukt (%s), caption toont als één blok", e)
        _tts(text, voice, out, rate=rate, pitch=pitch)
        return []
    if not out.exists() or out.stat().st_size == 0:
        # Stream-API leverde geen audio (oude edge-tts-versie); terugval op .save().
        _tts(text, voice, out, rate=rate, pitch=pitch)
        return []
    return [w for w in words if w["text"]]


def _synthesize(text: str, tpl: VideoTemplate, out: Path) -> List[dict]:
    """Kies de stem-provider: ElevenLabs (natuurlijker, betaald) als het template
    dat vraagt én er een key is; anders edge-tts (gratis). Faalt ElevenLabs
    (netwerk, quota, geen key), dan valt dezelfde regel automatisch terug op
    edge-tts — nooit een kapotte render om een stem-upgrade.
    """
    if tpl.tts_provider == "elevenlabs":
        from . import elevenlabs_client as el
        words = el.synth_with_timings(text, out, voice_id=tpl.elevenlabs_voice_id)
        if words is not None:
            return words
        logger.info("ElevenLabs niet beschikbaar/mislukt, val terug op edge-tts voor deze scène")
    return _tts_with_timings(text, tpl.voice, out, rate=tpl.voice_rate, pitch=tpl.voice_pitch)


def _normalize_audio(path: Path, log: List[str]) -> None:
    """Normaliseer de voiceover-luidheid (ffmpeg `loudnorm`).

    Stemmen verschillen fors in opgenomen volume — ElevenLabs' Marianne staat
    bijvoorbeeld beduidend zachter dan edge-tts' output. Draait ongeacht
    provider: consistente luidheid hoort bij elke render, niet bij één stem.
    Mislukt de normalisatie (zeldzaam), dan blijft het bronbestand gewoon
    staan — een zachte stem is beter dan een kapotte render.
    """
    tmp = path.with_suffix(".norm.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
        "-ar", "44100", str(tmp),
    ]
    try:
        _run(cmd, log)
        tmp.replace(path)
    except RuntimeError as e:
        log.append(f"luidheid-normalisatie mislukt (stem blijft ongewijzigd): {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# ── Slide-rendering (portrait, merk-huisstijl) ──────────────────────────────

def _template_font(tpl: Optional[VideoTemplate], size: int, bold: bool) -> ImageFont.ImageFont:
    """Laad het merk-font uit de template; val terug op het systeem-font."""
    path = None
    if tpl is not None:
        path = tpl.font_bold if bold else tpl.font_regular
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            logger.warning("Merk-font niet te laden (%s), systeem-font gebruikt", path)
    return _load_font(size, bold=bold)


def _paste_logo(img: Image.Image, tpl: VideoTemplate) -> None:
    """Plak het merk-logo (met alpha/opaciteit) in beeld.

    Positie (template.logo.position):
      top-left  → linksboven (standaard voor BewaardVoorJou)
      top-right → rechtsboven
      top       → gecentreerd bovenin
      bottom    → gecentreerd onderin
    """
    spec = tpl.logo
    if not spec.resolved:
        return
    try:
        logo = Image.open(spec.resolved).convert("RGBA")
    except OSError as e:
        logger.warning("Logo niet te openen: %s", e)
        return
    w, h = img.size
    lw = max(1, int(spec.width))
    lh = max(1, int(logo.height * lw / logo.width))
    logo = logo.resize((lw, lh), Image.LANCZOS)
    if spec.opacity < 1.0:
        alpha = logo.getchannel("A").point(lambda a: int(a * max(0.0, min(1.0, spec.opacity))))
        logo.putalpha(alpha)
    margin = 90
    pos = (spec.position or "bottom").lower()
    # Verticaal: boven (met ademruimte voor de titel) of onder.
    y = margin if pos.startswith("top") else h - lh - 210
    # Horizontaal: links / rechts / gecentreerd.
    if pos == "top-left":
        x = margin
    elif pos == "top-right":
        x = w - lw - margin
    elif pos == "top":
        x = (w - lw) // 2
    else:  # bottom (gedefault)
        x = (w - lw) // 2
    img.paste(logo, (x, y), logo)


def render_slide(caption: str, project: str, kind: str, index: int, total: int,
                 subtitle: str = "", tpl: Optional[VideoTemplate] = None,
                 background: Optional[Path] = None,
                 attribution: str = "", show_text: bool = True,
                 transparent: bool = False) -> Image.Image:
    """Render één portrait-slide (1080x1920).

    hook → grote, gecentreerde tekst op accent-achtergrond.
    body → tekst op merk-achtergrond (of foto) met accent-balk.
    cta  → tekst + duidelijke merk-URL onderaan.

    Als `background` wordt meegegeven (familie-foto/stock), dan wordt die gebruikt
    als Ken-Burns-vriendelijke achtergrond: cover/contain passend gemaakt,
    verduisterd met een warme overlay + vignet zodat de witte onderschriften
    altijd leesbaar blijven. `attribution` (bijv. "Foto: … via Pexels") wordt
    klein en leesbaar onderaan gebrand — vereist voor correcte stock-licentie.
    Kleuren, fonts, groottes, logo en footer komen uit `tpl`.

    `show_text=False` slaat de hoofdtekst (caption/subtitle) over — gebruikt
    wanneer de tekst als aparte, met de stem gesynchroniseerde captionlaag
    over de clip heen komt (zie `_overlay_captions`) i.p.v. hier statisch
    ingebrand te worden.

    `transparent=True` levert een RGBA-canvas met alléén logo/footer/
    attributie/voortgangsstippen, geen achtergrondkleur — de "chrome"-laag
    die over een bewegende video-achtergrond wordt gelegd (zie
    `_video_scene_clip`), want die achtergrond bestaat al uit eigen pixels.
    """
    if tpl is None:
        tpl = load_template(project)
    w, h = PORTRAIT
    is_hook = kind == "hook"
    is_cta = kind == "cta"

    c_bg, c_fg, c_accent = tpl.colors["bg"], tpl.colors["fg"], tpl.colors["accent"]
    bg = c_accent if is_hook else c_bg
    fg = c_bg if is_hook else c_fg
    accent = c_bg if is_hook else c_accent

    if transparent:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    elif background is not None:
        try:
            photo = Image.open(background).convert("RGB")
            if (tpl.footage.fit or "cover") == "contain":
                # Pas de foto in (geen bijsnijden); vul de rest met de merk-kleur.
                img = Image.new("RGB", (w, h), bg)
                scale = min(w / photo.width, h / photo.height)
                nw, nh = max(1, int(photo.width * scale)), max(1, int(photo.height * scale))
                photo = photo.resize((nw, nh), Image.LANCZOS)
                img.paste(photo, ((w - nw) // 2, (h - nh) // 2))
            else:  # cover
                scale = max(w / photo.width, h / photo.height)
                nw, nh = max(1, int(photo.width * scale)), max(1, int(photo.height * scale))
                photo = photo.resize((nw, nh), Image.LANCZOS)
                img = photo.crop(((nw - w) // 2, (nh - h) // 2, (nw + w) // 2, (nh + h) // 2))
            # Warme sfeer-tint — géén zware verduistering meer nodig, want
            # captions liggen tegenwoordig als eigen donkere pil-laag overheen
            # (zie `_render_sentence_frames`), niet meer ingebrand op de foto
            # zelf. (De vorige lower-third-bar + vignet waren bovendien stuk:
            # de vignet-ellips was per ongeluk groter dan het canvas, waardoor
            # "zachte hoek-vignet" bijna het hele beeld bijna zwart maakte —
            # onzichtbaar zolang er felgekleurde tekst overheen stond, maar
            # een kapotte foto zodra die tekst wegviel.)
            warm = Image.new("RGB", (w, h), (74, 44, 18))   # diep amber
            img = Image.blend(img, warm, 0.10)
            # Zachte vignet: alleen de échte hoeken iets donkerder, midden en
            # randen blijven helder. De ellips is groter dan het canvas zodat
            # alleen de hoeken erbuiten vallen (i.p.v. bijna het hele beeld).
            vig = Image.new("L", (w, h), 0)
            vd = ImageDraw.Draw(vig)
            mx, my = int(w * 0.18), int(h * 0.12)
            vd.ellipse([-mx, -my, w + mx, h + my], fill=255)
            vig = vig.filter(ImageFilter.GaussianBlur(260))
            vig = vig.point(lambda a: 150 + int(a * 105 / 255))  # nooit donkerder dan 150/255
            dark = Image.new("RGB", (w, h), (0, 0, 0))
            img = Image.composite(img, dark, vig)
        except OSError as e:
            logger.warning("Achtergrond-foto mislukt (%s), merk-slide gebruikt: %s", background, e)
            img = Image.new("RGB", (w, h), bg)
    else:
        img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    # Tekstkleur: op een foto-achtergrond gebruiken we crème voor body en
    # helder goud (logo-goud, niet het template-accent) voor de hook-headline.
    GOLD = (229, 165, 0)   # logo-goud (#e5a500), matcht de ad
    if background is not None or transparent:
        fg = (245, 238, 224)            # warm crème (niet hard wit)
        gold = GOLD
    else:
        fg = c_fg
        gold = c_accent

    margin = 110
    max_width = w - 2 * margin

    if show_text:
        # Subtiele verticale vignet zodat tekst altijd leesbaar is.
        if not is_hook:
            top = _mix(bg, (0, 0, 0), 0.18)
            for y in range(0, 260):
                k = 1 - y / 260
                draw.line([(0, y), (w, y)], fill=_mix(bg, top, k))

        # Accent-balk (behalve op de hook, die is al vol accent).
        if not is_hook:
            draw.rectangle([(margin, 300), (margin + 120, 312)], fill=accent)

        size = tpl.size("hook") if is_hook else (tpl.size("cta") if is_cta else tpl.size("body"))
        font = _template_font(tpl, size, bold=True)
        lines = _wrap_to_width(draw, caption.strip(), font, max_width)[:8]
        line_h = int(size * 1.24)
        total_h = len(lines) * line_h
        # Plaats de tekst in de lower-third (zoals de ad): onderin, niet gecentreerd.
        if background is not None:
            y = int(h * 0.60) - (total_h // 2)
        else:
            y = (h - total_h) // 2
        head_fill = gold if is_hook else fg
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            x = (w - lw) // 2 if is_hook else margin
            # Hook-headline in goud met zachte schaduw voor leesbaarheid.
            if is_hook and background is not None:
                draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=head_fill)
            y += line_h

        if subtitle:
            sub_size = tpl.size("caption")
            sub_font = _template_font(tpl, sub_size, bold=False)
            for line in _wrap_to_width(draw, subtitle.strip(), sub_font, max_width)[:2]:
                bbox = draw.textbbox((0, 0), line, font=sub_font)
                draw.text(((w - (bbox[2] - bbox[0])) // 2, y + 20), line,
                          font=sub_font, fill=_mix(fg, bg, 0.25))
                y += int(sub_size * 1.3)

    # Merk-footer (template-tekst > projectnaam / cta-variant).
    footer_font = _template_font(tpl, tpl.size("footer"), bold=False)
    if is_cta:
        footer = tpl.cta_footer_text or tpl.footer_text or project
    else:
        footer = tpl.footer_text or project
    bbox = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(((w - (bbox[2] - bbox[0])) // 2, h - 150), footer,
              font=footer_font, fill=(fg if is_hook else accent))

    # Stock-attributie (vereist voor Pexels/stock-licentie). Subtiel, boven footer.
    if attribution:
        attr_font = _template_font(tpl, max(22, tpl.size("footer") - 14), bold=False)
        ab = draw.textbbox((0, 0), attribution, font=attr_font)
        ax = (w - (ab[2] - ab[0])) // 2
        ax = max(margin, min(ax, w - (ab[2] - ab[0]) - margin))
        draw.text((ax, h - 200), attribution, font=attr_font,
                  fill=_mix(fg, bg, 0.45))

    # Optioneel merk-logo/watermerk.
    _paste_logo(img, tpl)

    # Voortgangsstippen onderaan.
    if total > 1:
        dot_r = 7
        gap = 34
        tw = total * gap - (gap - dot_r * 2)
        sx = (w - tw) // 2
        for i in range(total):
            cx = sx + i * gap + dot_r
            col = accent if i == index else _mix(bg, fg, 0.35)
            draw.ellipse([(cx - dot_r, h - 90 - dot_r), (cx + dot_r, h - 90 + dot_r)], fill=col)

    return img


# ── Scene → clip (Ken-Burns beweging) ───────────────────────────────────────

def _scene_clip(slide_png: Path, audio: Path, out: Path, duration: float,
                zoom_in: bool, log: List[str], motion: bool = True) -> None:
    """Maak één scène-clip: slide + voiceover, met optionele Ken-Burns-beweging.

    Truc: upscale de slide fors vóór zoompan zodat de beweging niet schokt.
    """
    w, h = PORTRAIT
    frames = max(1, int(round(duration * FPS)))
    if not motion:
        vf = f"scale={w}:{h},format=yuv420p"
    else:
        # 0.15% zoom per frame; ~1.10x over een scène van ~2 sec.
        if zoom_in:
            zexpr = "min(zoom+0.0010,1.12)"
        else:
            zexpr = "if(lte(zoom,1.0),1.12,max(zoom-0.0010,1.0))"
        vf = (
            f"scale={w*4}:{h*4},"
            f"zoompan=z='{zexpr}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={w}x{h}:fps={FPS},format=yuv420p"
        )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(slide_png),
        "-i", str(audio),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-shortest", "-movflags", "+faststart",
        str(out),
    ]
    try:
        _run(cmd, log)
    except RuntimeError as e:
        # Val terug op een stilstaand beeld als zoompan struikelt.
        log.append(f"zoompan-terugval: {e}")
        cmd_fallback = [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(slide_png),
            "-i", str(audio),
            "-vf", f"scale={w}:{h},format=yuv420p",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-shortest", "-movflags", "+faststart",
            str(out),
        ]
        _run(cmd_fallback, log)


def _wrap_words_with_positions(draw: ImageDraw.ImageDraw, words: List[str],
                               font: ImageFont.ImageFont, max_width: int) -> List[List[dict]]:
    """Woordterugloop die per woord zijn x-positie binnen de regel onthoudt.

    Anders dan `_wrap_to_width` (dat losse regel-strings teruggeeft) heeft de
    hele-zin-caption per woord een vaste plek nodig, zodat alleen de kleur van
    het actieve woord hoeft te wisselen — de rest van de zin staat stil.
    """
    space_w = draw.textlength(" ", font=font)
    lines: List[List[dict]] = []
    cur: List[dict] = []
    cur_w = 0.0
    for word in words:
        ww = draw.textlength(word, font=font)
        add_w = ww if not cur else space_w + ww
        if cur and cur_w + add_w > max_width:
            lines.append(cur)
            cur, cur_w = [], 0.0
            add_w = ww
        x = cur_w if not cur else cur_w + space_w
        cur.append({"text": word, "x": x, "width": ww})
        cur_w = x + ww
    if cur:
        lines.append(cur)
    return lines


def _render_sentence_frames(scene_words: List[dict], tpl: VideoTemplate, kind: str) -> List[dict]:
    """Render de hele-zin-caption: de volledige scènetekst blijft zichtbaar,
    alleen het net-gesproken woord kleurt anders.

    Dat leest als een lopende zin i.p.v. steeds wisselende losse woordbrokjes,
    en is precies wat er nodig is om de video als één geheel te laten voelen
    i.p.v. een reeks losse flitsjes. De lay-out (regelverdeling, x-posities)
    wordt één keer berekend en voor elk woord hergebruikt — alleen de kleur
    verandert, dus de zin "springt" niet bij elke highlight-wissel.

    Retourneert per woord {"image", "start", "end"} met de timing van dát
    woord; de aanroeper rekt elk frame op tot het volgende begint (geen
    flikkering in de korte stiltes tussen woorden).
    """
    if not scene_words:
        return []
    w, h = PORTRAIT
    words_text = [wd["text"] for wd in scene_words]

    base_size = tpl.size("hook") if kind == "hook" else tpl.size("body")
    size = max(34, int(base_size * 0.62))
    font = _template_font(tpl, size, bold=True)
    margin = 100
    max_width = w - 2 * margin

    probe = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(probe)
    lines = _wrap_words_with_positions(draw, words_text, font, max_width)[:3]
    # Woorden die niet meer pasten (zin > 3 regels) horen bij het laatste
    # zichtbare woord — anders heeft dat woord geen highlight-frame.
    shown = sum(len(ln) for ln in lines)

    line_h = int(size * 1.32)
    total_h = len(lines) * line_h
    pad_x, pad_y = 50, 30
    line_widths = [(ln[-1]["x"] + ln[-1]["width"] if ln else 0.0) for ln in lines]
    box_w = min(max_width, max(line_widths, default=0.0)) + pad_x * 2
    box_h = total_h + pad_y * 2
    cy = int(h * 0.66)
    box_x0 = (w - box_w) / 2
    box_y0 = cy - box_h / 2

    base_color = (255, 255, 255, 255)
    active_color = (255, 210, 90, 255) if kind != "hook" else (255, 255, 255, 255)
    hook_active = (255, 255, 255, 255)

    frames: List[dict] = []
    for active_idx in range(len(scene_words)):
        highlight_idx = min(active_idx, shown - 1) if shown else 0
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(
            [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h], radius=28, fill=(10, 8, 5, 195)
        )
        wi = 0
        y = box_y0 + pad_y
        for line in lines:
            lw = line[-1]["x"] + line[-1]["width"] if line else 0
            lx0 = box_x0 + pad_x + (box_w - 2 * pad_x - lw) / 2
            for item in line:
                is_active = wi == highlight_idx
                color = (hook_active if kind == "hook" else active_color) if is_active else base_color
                d.text((lx0 + item["x"], y), item["text"], font=font, fill=color)
                wi += 1
            y += line_h
        frames.append({
            "image": img,
            "start": scene_words[active_idx]["start"],
            "end": scene_words[active_idx]["end"],
        })
    return frames


def _caption_frames_for_scene(scene_words: List[dict], seg_dur: float,
                              tpl: VideoTemplate, kind: str) -> List[dict]:
    """Bouw de caption-frames voor één scène en rek de zichtbaarheid op zodat
    de zin nooit knippert tussen twee woorden of vlak voor het einde verdwijnt.
    """
    frames = _render_sentence_frames(scene_words, tpl, kind)
    if not frames:
        return frames
    for i in range(len(frames) - 1):
        frames[i]["end"] = frames[i + 1]["start"]
    frames[-1]["end"] = max(frames[-1]["end"], seg_dur)
    return frames


def _render_caption_chunk(text: str, tpl: VideoTemplate, kind: str) -> Image.Image:
    """Render één caption-blok als transparante PNG (pill-vorm) — de karaoke-laag.

    Losse laag i.p.v. ingebrand in de achtergrond-slide, zodat elk blokje precies
    op zijn eigen [start,end]-venster over de clip kan worden overlaid.
    """
    w, h = PORTRAIT
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    base_size = tpl.size("hook") if kind == "hook" else tpl.size("body")
    size = max(40, int(base_size * 0.8))
    font = _template_font(tpl, size, bold=True)
    margin = 90
    max_width = w - 2 * margin
    lines = _wrap_to_width(draw, text.strip(), font, max_width)[:2] or [text.strip()]
    line_h = int(size * 1.22)
    total_h = len(lines) * line_h

    pad_x, pad_y = 46, 26
    widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widths.append(bbox[2] - bbox[0])
    box_w = min(max_width, (max(widths) if widths else 0) + pad_x * 2)
    box_h = total_h + pad_y * 2
    cy = int(h * 0.66)
    box_x0 = (w - box_w) // 2
    box_y0 = cy - box_h // 2
    draw.rounded_rectangle(
        [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h], radius=28, fill=(10, 8, 5, 195)
    )

    text_color = (255, 210, 90, 255) if kind == "hook" else (255, 255, 255, 255)
    y = box_y0 + pad_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = (w - lw) // 2
        draw.text((x, y), line, font=font, fill=text_color)
        y += line_h
    return img


def _overlay_image_frames(base_clip: Path, frames: List[dict], work: Path, index: int,
                          log: List[str]) -> Path:
    """Leg tijdgebonden PNG-frames (elk met "image"/"start"/"end") over een clip.

    Generiek: gebruikt voor zowel de hele-zin-captions (`_caption_frames_for_scene`)
    als de terugval-tekstkaart zonder woord-timing. Geen frames → clip blijft
    ongewijzigd.
    """
    if not frames:
        return base_clip
    out = work / f"cap_{index}.mp4"
    inputs: List[str] = ["-i", str(base_clip)]
    for j, fr in enumerate(frames):
        png = work / f"cap_{index}_{j}.png"
        fr["image"].save(png, "PNG")
        inputs += ["-i", str(png)]

    prev = "0:v"
    parts = []
    for j, fr in enumerate(frames):
        label = "vout" if j == len(frames) - 1 else f"v{j}"
        parts.append(
            f"[{prev}][{j + 1}:v]overlay=0:0:enable='between(t,{fr['start']:.3f},{fr['end']:.3f})'[{label}]"
        )
        prev = label
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ]
    try:
        _run(cmd, log)
    except RuntimeError as e:
        log.append(f"caption-overlay mislukt, clip zonder captions: {e}")
        return base_clip
    return out


def _video_scene_clip(video_path: Path, chrome_png: Path, audio: Path, out: Path,
                      duration: float, log: List[str]) -> None:
    """Maak één scène-clip uit écht bewegend beeld (Pexels b-roll) i.p.v. een
    stilstaande foto met Ken-Burns-zoom.

    De clip wordt geloopt/afgekapt tot exact `duration` (b-roll is zelden lang
    genoeg voor een hele scène), cover-geschaald naar 1080x1920, en krijgt
    dezelfde warme kleurgrade als de foto-achtergrond (subtieler — een video
    kan niet met PIL gecomposit worden zoals een foto, dus dit gebeurt via
    ffmpeg's eq/colorbalance i.p.v. de PIL-overlay-lagen). De "chrome"-laag
    (logo/footer/attributie/dots, uit `render_slide(transparent=True)`) komt
    er in dezelfde ffmpeg-pass overheen — één encode i.p.v. twee.
    """
    w, h = PORTRAIT
    vf = (
        f"scale={w}:-2:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"eq=saturation=1.06:contrast=1.03,"
        f"colorbalance=rs=0.05:gs=0.01:bs=-0.04:rm=0.03:bm=-0.02"
    )
    filter_complex = f"[0:v]{vf}[bg];[bg][2:v]overlay=0:0:format=auto[vout]"
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(video_path),
        "-i", str(audio),
        "-i", str(chrome_png),
        "-filter_complex", filter_complex,
        "-t", f"{duration:.3f}",
        "-map", "[vout]", "-map", "1:a",
        "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-shortest", "-movflags", "+faststart",
        str(out),
    ]
    _run(cmd, log)


def _concat_copy(clips: List[Path], out: Path, log: List[str]) -> None:
    """Plak scène-clips achter elkaar zónder overgang (stream copy, hard cut).

    Terugval voor `_xfade_join` — en de enige route bij precies 1 scène.
    """
    listfile = out.parent / "concat.txt"
    listfile.write_text(
        "".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8"
    )
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
          "-c", "copy", str(out)], log)


def _xfade_join(clips: List[Path], durations: List[float], out: Path, log: List[str]) -> None:
    """Plak scène-clips met een korte crossfade i.p.v. een harde cut.

    Een harde cut tussen elke scène is precies wat een montage "geknipt" laat
    voelen; short-form video die pro oogt gebruikt bijna altijd een korte
    (~0,3s) overvloeier. `xfade` vergt hercoderen (geen stream copy meer, dat
    kan alleen bij een harde cut) — vandaar een aparte functie i.p.v. dit in
    `_concat_copy` te forceren.

    Alleen het BEELD faadt over — het geluid wordt gewoon aaneengeplakt
    (`concat`, geen `acrossfade`). De audio is inmiddels één doorlopende
    voiceover, in aaneensluitende stukken geknipt (`_extract_audio_segment`);
    die twee stukken laten overlappen zou dezelfde stem twee keer verschoven
    over elkaar heen leggen — een echo, geen overvloeier. Het beeld overlapt
    wél zichtbaar, maar dat gebeurt nu onder één ononderbroken stem door, wat
    precies het "vloeiende" effect geeft in plaats van de vorige losse hapjes.

    De overgangsduur wordt geklemd op de kortste scène, anders faalt xfade
    (offset zou negatief worden) bij een heel korte laatste zin.
    """
    n = len(clips)
    trans = min(0.35, max(0.12, min(durations) * 0.35))
    inputs: List[str] = []
    for c in clips:
        inputs += ["-i", str(c)]

    vparts = []
    prev_v = "0:v"
    cumulative = durations[0]
    for i in range(1, n):
        offset = max(0.0, cumulative - trans)
        vlabel = f"v{i}"
        vparts.append(
            f"[{prev_v}][{i}:v]xfade=transition=fade:duration={trans:.3f}:offset={offset:.3f}[{vlabel}]"
        )
        prev_v = vlabel
        cumulative += durations[i] - trans

    a_inputs = "".join(f"[{i}:a]" for i in range(n))
    aparts = [f"{a_inputs}concat=n={n}:v=0:a=1[aout]"]

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(vparts + aparts),
        "-map", f"[{prev_v}]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart", str(out),
    ]
    _run(cmd, log)


def _concat(clips: List[Path], durations: List[float], out: Path,
           music: Optional[Path], log: List[str]) -> None:
    """Plak scène-clips (met crossfade) en meng optioneel achtergrondmuziek zacht bij."""
    tmp = out.parent
    if len(clips) < 2:
        joined = clips[0]
    else:
        joined = tmp / "_joined.mp4"
        try:
            _xfade_join(clips, durations, joined, log)
        except RuntimeError as e:
            log.append(f"crossfade mislukt, harde cuts gebruikt: {e}")
            _concat_copy(clips, joined, log)

    if not music or not music.exists():
        _run(["ffmpeg", "-y", "-i", str(joined), "-c", "copy",
              "-movflags", "+faststart", str(out)], log)
        return

    _run([
        "ffmpeg", "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex",
        "[1:a]volume=0.12[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(out),
    ], log)


def _split_words_by_scene(words: List[dict], scenes: List["Scene"]) -> List[List[dict]]:
    """Verdeel de woord-timing van de dóórlopende voiceover over de scènes.

    De volledige tekst werd aan de stem-provider gegeven als één string
    (scène-narraties met spaties aaneengeplakt), dus de N'de scène claimt
    precies zoveel woorden als zijn eigen tekst telt (whitespace-split — dat
    is ook hoe de provider zelf woordgrenzen aflevert). Restwoorden (afronding,
    afwijkende tokenisatie) gaan naar de laatste scène i.p.v. te verdwijnen.
    """
    counts = [len((sc.narration or sc.caption).split()) for sc in scenes]
    out: List[List[dict]] = []
    idx = 0
    for c in counts:
        out.append(words[idx:idx + c])
        idx += c
    if idx < len(words) and out:
        out[-1] = out[-1] + words[idx:]
    return out


def _extract_audio_segment(src: Path, start: float, duration: float, out: Path,
                           log: List[str]) -> None:
    """Knip een precies stuk uit de doorlopende voiceover (accurate seek —
    `-ss` als output-optie ná `-i`, niet als snelle-maar-onnauwkeurige input-seek).
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.05, duration):.3f}",
        "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "2",
        str(out),
    ]
    _run(cmd, log)


# ── Publieke render-functie ─────────────────────────────────────────────────

def render_short(
    project: str,
    scenes: List[Scene],
    out_path: Path,
    *,
    voice: Optional[str] = None,
    music_path: Optional[Path] = None,
    template: Optional[VideoTemplate] = None,
) -> RenderResult:
    """Render een 9:16 short uit een lijst scènes. Retourneert een RenderResult.

    De hele scène-tekst wordt in ÉÉN doorlopende voiceover ingesproken (niet
    per scène apart) — alleen het beeld wisselt nog per scène, op basis van
    de woord-timing binnen die ene opname. Zo klinkt het als één verhaal i.p.v.
    een reeks losse, elk opnieuw ingesproken zinnetjes. Lukt dat niet (geen
    woord-timing beschikbaar), dan valt elke scène terug op zijn eigen
    onafhankelijke TTS-aanroep — nooit een kapotte render.

    Template (`projects/<project>/video/template.json`) stuurt kleuren, fonts,
    logo, footer, stem en muziek. Expliciete `voice`/`music_path` overrulen het.
    """
    tpl = template or load_template(project)
    voice = voice or tpl.voice
    tpl.voice = voice  # expliciete override moet ook _synthesize() bereiken
    if music_path is None:
        music_path = tpl.music
    result = RenderResult(voice=voice)
    scenes = [s for s in scenes if (s.narration or s.caption).strip()]
    if not scenes:
        result.error = "geen scènes met tekst"
        return result

    # Footage ophalen vóór de render: eigen beeld (bv. Midjourney-stills/-Animate-
    # clips in de footage-map) heeft ALTIJD voorrang op generieke Pexels-stock —
    # merk-echt beeld van een concrete stijl verslaat onbekende mensen in stock-
    # footage. Pexels is de vangnet-laag voor wanneer die map leeg is.
    bg_videos: List[Path] = []
    bg_images: List[Path] = []
    attributions: List[str] = []
    local_imgs = list(tpl.footage.images) if (tpl.footage and tpl.footage.images) else []
    local_vids = list(tpl.footage.local_videos) if (tpl.footage and tpl.footage.local_videos) else []
    if local_vids or local_imgs:
        bg_videos = local_vids
        bg_images = local_imgs
        result.log.append(
            f"eigen beeld: {len(local_vids)} video('s), {len(local_imgs)} foto('s) uit '{tpl.footage.path}'"
        )
    elif tpl.footage and tpl.footage.provider == "pexels":
        try:
            from . import pexels_client as pex
            if pex.pexels_ready():
                query = tpl.footage.query or "family memories storytelling"
                proj_dir = Path(__file__).resolve().parent.parent.parent / "projects" / project / "video"

                found_v = pex.search_videos(query, per_page=15)
                if found_v:
                    dlv = pex.download_videos(found_v, proj_dir / "_pexels_video_cache",
                                              limit=max(4, len(scenes)))
                    bg_videos = [p for p, _ in dlv]
                    if bg_videos:
                        attributions = [a for _, a in dlv]
                        result.log.append(f"pexels: {len(bg_videos)} stock-video's voor '{query}'")

                if not bg_videos:
                    found_p = pex.search_photos(query, per_page=30)
                    if found_p:
                        dl = pex.download_photos(found_p, proj_dir / "_pexels_cache",
                                                 limit=max(4, len(scenes)))
                        bg_images = [p for p, _ in dl]
                        attributions = [a for _, a in dl]
                        result.log.append(f"pexels: geen video, {len(bg_images)} stock-foto's voor '{query}'")
                    else:
                        result.log.append("pexels: geen resultaten (rate-limit) — local fallback")
                result.attributions = attributions
            else:
                result.log.append("pexels: PEXELS_API_KEY ontbreekt — local fallback")
        except Exception as e:
            logger.warning("Pexels-footage mislukt, val terug op de merk-slide: %s", e)
            result.log.append(f"pexels-fout: {e}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="agentos_vid_") as td:
        work = Path(td)
        clips: List[Path] = []
        durations: List[float] = []
        try:
            # 1) ÉÉN doorlopende voiceover voor het hele script — geen aparte
            #    TTS-call per scène. Elke scène apart laten inspreken gaf een
            #    nieuwe opname met een eigen intonatie-start, dus klonk het
            #    nooit als één vloeiend verhaal, hoe kort de stilte ertussen
            #    ook was. Nu leest de stem het hele script in één take, met
            #    zijn eigen natuurlijke pauzes tussen zinnen; alleen het beeld
            #    wisselt nog per scène.
            full_text = " ".join((sc.narration or sc.caption).strip() for sc in scenes)
            master_audio = work / "voice_full.mp3"
            master_words = _synthesize(full_text, tpl, master_audio)
            _normalize_audio(master_audio, result.log)
            total_voice_dur = _probe_duration(master_audio)
            continuous = bool(master_words) and total_voice_dur > 0
            scene_word_lists = _split_words_by_scene(master_words, scenes) if continuous else []

            for i, sc in enumerate(scenes):
                narration = (sc.narration or sc.caption).strip()
                caption = (sc.caption or sc.narration).strip()
                if not sc.caption:
                    caption = narration.split(". ")[0].strip().rstrip(".")

                # 1b) Dit scène-segment: bij de doorlopende opname is dat een
                #     precies uitgeknipt stuk van `master_audio` (inclusief de
                #     natuurlijke pauze ná deze zin, tot de volgende begint —
                #     géén kunstmatige 0,6s-stilte meer nodig). Zonder bruikbare
                #     woord-timing (zeldzame terugval) krijgt elke scène gewoon
                #     zijn eigen, onafhankelijke TTS-aanroep, zoals voorheen.
                audio = work / f"vo_{i}.mp3"
                sw: List[dict] = []
                if continuous:
                    sw = scene_word_lists[i]
                    seg_start = 0.0 if i == 0 else (sw[0]["start"] if sw else None)
                    if seg_start is None:
                        seg_start = durations and sum(durations) or 0.0
                    next_sw = scene_word_lists[i + 1] if i + 1 < len(scenes) else None
                    seg_end = next_sw[0]["start"] if next_sw else total_voice_dur
                    dur = max(0.3, seg_end - seg_start)
                    _extract_audio_segment(master_audio, seg_start, dur, audio, result.log)
                    rel_words = [
                        {"text": w["text"], "start": max(0.0, w["start"] - seg_start),
                         "end": max(0.0, w["end"] - seg_start)}
                        for w in sw
                    ]
                else:
                    words = _synthesize(narration, tpl, audio)
                    _normalize_audio(audio, result.log)
                    dur = _probe_duration(audio)
                    if dur <= 0:
                        raise RuntimeError(f"scène {i}: voiceover leverde 0 sec op")
                    dur += 0.6  # kleine adempauze na de zin
                    rel_words = words

                # 2) Achtergrond — écht bewegend beeld (Pexels b-roll) heeft
                #    voorrang; anders een foto/stock-slide met Ken-Burns-zoom.
                #    Tekst komt nooit hier in mee, want die komt als gesynchro-
                #    niseerde captionlaag (stap 4) over de clip heen.
                attr = attributions[i % len(attributions)] if attributions else ""
                bg_video = bg_videos[i % len(bg_videos)] if bg_videos else None
                bg_clip = work / f"bg_{i}.mp4"
                used_video = False
                if bg_video is not None:
                    try:
                        chrome = render_slide(caption, project, sc.kind, i, len(scenes),
                                             tpl=tpl, attribution=attr,
                                             show_text=False, transparent=True)
                        chrome_png = work / f"chrome_{i}.png"
                        chrome.save(chrome_png, "PNG")
                        _video_scene_clip(bg_video, chrome_png, audio, bg_clip, dur, result.log)
                        used_video = True
                    except RuntimeError as e:
                        result.log.append(f"scène {i}: video-b-roll mislukt, val terug op foto: {e}")

                if not used_video:
                    bg_photo = bg_images[i % len(bg_images)] if bg_images else None
                    slide = render_slide(caption, project, sc.kind, i, len(scenes),
                                        tpl=tpl, background=bg_photo, attribution=attr,
                                        show_text=False)
                    slide_png = work / f"slide_{i}.png"
                    slide.save(slide_png, "PNG")
                    _scene_clip(slide_png, audio, bg_clip, dur,
                                zoom_in=(i % 2 == 0), motion=tpl.motion, log=result.log)

                # De clip is vaak korter dan de aangevraagde `dur`: -shortest
                # in _scene_clip/_video_scene_clip knipt af op de kale
                # audiolengte. De crossfade-offsets ná dit punt moeten op de
                # écht gerenderde lengte rekenen — anders faadt xfade voorbij
                # het einde van de vorige clip en valt een groot deel van de
                # video stil weg.
                actual_dur = _probe_duration(bg_clip) or dur

                # 4) Captions: de hele zin blijft zichtbaar, alleen het net-
                #    gesproken woord kleurt anders — leest als lopende tekst
                #    i.p.v. wisselende losse brokjes. Zonder woord-timing
                #    (terugval) toont één statisch blok de hele caption.
                if rel_words:
                    frames = _caption_frames_for_scene(rel_words, actual_dur, tpl, sc.kind)
                elif caption:
                    frames = [{"image": _render_caption_chunk(caption, tpl, sc.kind),
                              "start": 0.0, "end": actual_dur}]
                else:
                    frames = []
                clip = _overlay_image_frames(bg_clip, frames, work, i, result.log)
                clips.append(clip)
                durations.append(actual_dur)

            # 5) Concat (met crossfade tussen scènes) + muziek.
            _concat(clips, durations, out_path, music_path, result.log)

            result.ok = out_path.exists() and out_path.stat().st_size > 0
            result.path = str(out_path)
            result.duration = _probe_duration(out_path)
            result.scenes = len(scenes)
        except Exception as e:  # noqa: BLE001 — we willen de fout in het resultaat
            logger.warning("render_short mislukt: %s", e)
            result.error = str(e)[:400]

    return result


def scenes_from_scriptpack(pack: dict) -> List[Scene]:
    """Zet een TikTok-scriptpack (uit social_content) om in render-scènes.

    hook → 1 scène, script → gesplitst per zin (max ~6), plus CTA-scène.
    """
    scenes: List[Scene] = []
    hook = (pack.get("hook") or "").strip()
    if hook:
        scenes.append(Scene(narration=hook, caption=hook, kind="hook"))
    script = (pack.get("script") or "").strip()
    if script:
        sentences = [s.strip() for s in script.replace("\n", " ").split(". ") if s.strip()]
        for s in sentences[:6]:
            scenes.append(Scene(narration=s if s.endswith(".") else s + ".", kind="body"))
    return scenes


if __name__ == "__main__":  # handmatige smoke-test
    import sys
    demo = [
        Scene("Jouw verhaal is goud waard.", kind="hook"),
        Scene("Ik zag hoe snel mijn eigen vader vastliep achter de computer toen hij zijn "
              "levensverhaal wilde vastleggen.", kind="body"),
        Scene("Daarom bouwde ik een warme, Nederlandse manier om herinneringen te bewaren.",
              kind="body"),
        Scene("Begin vandaag gratis. Leg jouw verhaal vast voor de generaties na jou.",
              caption="Start vandaag gratis", kind="cta"),
    ]
    res = render_short("bewaardvoorjou", demo, Path(sys.argv[1] if len(sys.argv) > 1 else "demo.mp4"))
    print(json.dumps(res.__dict__, ensure_ascii=False, indent=2, default=str))

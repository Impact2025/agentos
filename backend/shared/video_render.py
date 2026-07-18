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


def _tts(text: str, voice: str, out: Path) -> None:
    """Synthetiseer NL-voiceover naar een mp3 met edge-tts (gratis, geen key)."""
    import edge_tts  # lokaal geïmporteerd zodat de module laadt zonder de dep

    async def _go() -> None:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(str(out))

    asyncio.run(_go())


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
                 attribution: str = "") -> Image.Image:
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

    if background is not None:
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
            # Warme gouden color-grade (zoals de BewaardVoorJou-ad):
            #  - foto blijft HELDER/warm en toont de mensen (geen zware verduistering)
            #  - tekst rust op een gelokaliseerde donkere lower-third-bar (goud blijft leesbaar)
            # 1) Zeer lichte warme tint over de hele foto (sfeer, niet donkerder maken).
            warm = Image.new("RGB", (w, h), (74, 44, 18))   # diep amber
            img = Image.blend(img, warm, 0.12)
            # 2) Donkere lower-third BAR onderin (waar de tekst komt) — lokaal,
            #    niet de hele foto. Goud leest hierop, foto blijft elders helder.
            grad = Image.new("L", (w, h), 0)
            gd = ImageDraw.Draw(grad)
            gd.rectangle([(0, int(h * 0.48)), (w, h)], fill=255)
            grad = grad.filter(ImageFilter.GaussianBlur(200))
            lower = Image.new("RGB", (w, h), (10, 7, 3))
            img = Image.composite(img, lower, grad)
            # 3) Zeer zachte hoek-vignet (centrum volledig vrij, foto zichtbaar).
            vig = Image.new("L", (w, h), 255)
            vd = ImageDraw.Draw(vig)
            vd.ellipse([-w * 0.40, -h * 0.40, w * 1.40, h * 1.40], fill=0)
            vig = vig.filter(ImageFilter.GaussianBlur(380))
            vd2 = ImageDraw.Draw(vig)
            vd2.ellipse([-w * 0.40, -h * 0.40, w * 1.40, h * 1.40], fill=30)
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
    if background is not None:
        fg = (245, 238, 224)            # warm crème (niet hard wit)
        gold = GOLD
    else:
        fg = c_fg
        gold = c_accent

    # Subtiele verticale vignet zodat tekst altijd leesbaar is.
    if not is_hook:
        top = _mix(bg, (0, 0, 0), 0.18)
        for y in range(0, 260):
            k = 1 - y / 260
            draw.line([(0, y), (w, y)], fill=_mix(bg, top, k))

    margin = 110
    max_width = w - 2 * margin

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


def _concat(clips: List[Path], out: Path, music: Optional[Path], log: List[str]) -> None:
    """Plak scène-clips achter elkaar; meng optioneel achtergrondmuziek zacht bij."""
    tmp = out.parent
    listfile = tmp / "concat.txt"
    listfile.write_text(
        "".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8"
    )
    if not music or not music.exists():
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
              "-c", "copy", "-movflags", "+faststart", str(out)], log)
        return

    # Eerst zonder muziek concatten (stream copy), daarna muziek onder de mix.
    joined = tmp / "_joined.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
          "-c", "copy", str(joined)], log)
    _run([
        "ffmpeg", "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex",
        "[1:a]volume=0.12[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(out),
    ], log)


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

    Volledig zelf-voorzienend: voiceover via edge-tts, beeld via merk-slides,
    montage via ffmpeg. Elke scène duurt zo lang als zijn voiceover (+ marge).

    Template (`projects/<project>/video/template.json`) stuurt kleuren, fonts,
    logo, footer, stem en muziek. Expliciete `voice`/`music_path` overrulen het.
    """
    tpl = template or load_template(project)
    voice = voice or tpl.voice
    if music_path is None:
        music_path = tpl.music
    result = RenderResult(voice=voice)
    scenes = [s for s in scenes if (s.narration or s.caption).strip()]
    if not scenes:
        result.error = "geen scènes met tekst"
        return result

    # Footage achtergrond-foto's ophalen vóór de render (lokaal of Pexels).
    bg_images: List[Path] = []
    attributions: List[str] = []
    # Lokale fallback-foto's (klaarzetten, pas gebruikt als Pexels leeg is).
    local_imgs = list(tpl.footage.images) if (tpl.footage and tpl.footage.images) else []
    if tpl.footage and tpl.footage.provider == "pexels":
        try:
            from . import pexels_client as pex
            if pex.pexels_ready():
                query = tpl.footage.query or "family memories storytelling"
                found = pex.search_photos(query, per_page=30)
                if found:
                    cache = Path(__file__).resolve().parent.parent.parent / "projects" / project / "video" / "_pexels_cache"
                    dl = pex.download_photos(found, cache, limit=max(4, len(scenes)))
                    bg_images = [p for p, _ in dl]
                    attributions = [a for _, a in dl]
                    result.attributions = attributions
                    result.log.append(f"pexels: {len(bg_images)} stock-foto's voor '{query}'")
                else:
                    result.log.append("pexels: geen resultaten (rate-limit) — local fallback")
            else:
                result.log.append("pexels: PEXELS_API_KEY ontbreekt — local fallback")
        except Exception as e:
            logger.warning("Pexels-footage mislukt, val terug op lokale foto's: %s", e)
            result.log.append(f"pexels-fout: {e}")
        # Fallback naar lokale foto's als Pexels niets opleverde.
        if not bg_images and local_imgs:
            bg_images = local_imgs
            result.log.append(f"footage-fallback: {len(bg_images)} lokale foto('s)")
    elif local_imgs:
        bg_images = local_imgs            # local_photos (geen pexels)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="agentos_vid_") as td:
        work = Path(td)
        clips: List[Path] = []
        try:
            for i, sc in enumerate(scenes):
                narration = (sc.narration or sc.caption).strip()
                caption = (sc.caption or sc.narration).strip()
                # Op-scherm tekst kort houden: eerste zin als er geen caption is.
                if not sc.caption:
                    caption = narration.split(". ")[0].strip().rstrip(".")

                # 1) Voiceover → duur.
                audio = work / f"vo_{i}.mp3"
                _tts(narration, voice, audio)
                dur = _probe_duration(audio)
                if dur <= 0:
                    raise RuntimeError(f"scène {i}: voiceover leverde 0 sec op")
                dur += 0.6  # kleine adempauze na de zin

                # 2) Slide (eventueel met foto/stock als achtergrond).
                bg_photo = bg_images[i % len(bg_images)] if bg_images else None
                attr = attributions[i % len(attributions)] if attributions else ""
                slide = render_slide(caption, project, sc.kind, i, len(scenes),
                                    tpl=tpl, background=bg_photo, attribution=attr)
                slide_png = work / f"slide_{i}.png"
                slide.save(slide_png, "PNG")

                # 3) Scène-clip met (optionele) Ken-Burns.
                clip = work / f"clip_{i}.mp4"
                _scene_clip(slide_png, audio, clip, dur,
                            zoom_in=(i % 2 == 0), motion=tpl.motion, log=result.log)
                clips.append(clip)

            # 4) Concat + muziek.
            _concat(clips, out_path, music_path, result.log)

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

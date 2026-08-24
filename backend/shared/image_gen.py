"""
Image Gen — eenvoudige "quote card"-generator voor social posts die een afbeelding
vereisen (vooral Instagram; Facebook/LinkedIn/X kunnen zonder).

Geen Canva-koppeling in v1 — dit is een lichte Pillow-render: titel over een
merk-kleur-achtergrond + project-naam onderaan. Genoeg om een geldige, on-brand
afbeelding te hebben zonder een aparte design-stap in de pijplijn.
"""
from __future__ import annotations

import io
import textwrap
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

CARD_SIZE = (1080, 1080)

# Simpele per-project stijl (achtergrondkleur, tekstkleur). Onbekende projecten
# vallen terug op _DEFAULT_STYLE. Uit te breiden zodra er een echt merk-kleurenpalet
# per project vastligt (bijv. via een vault-config).
_STYLES = {
    "weareimpact":    {"bg": (17, 24, 39), "fg": (255, 255, 255), "accent": (99, 102, 241)},
    "bewaardvoorjou":  {"bg": (63, 47, 40), "fg": (255, 250, 240), "accent": (212, 165, 116)},
    "pootgelukkig":   {"bg": (22, 78, 60), "fg": (255, 255, 255), "accent": (134, 239, 172)},
    "bijeen":         {"bg": (23, 31, 39), "fg": (250, 246, 240), "accent": (228, 96, 59)},
}
_DEFAULT_STYLE = {"bg": (30, 41, 59), "fg": (255, 255, 255), "accent": (56, 189, 248)}


def _style_for(project_name: str) -> dict:
    key = (project_name or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    return _STYLES.get(key, _DEFAULT_STYLE)


def _find_font(bold: bool = True) -> Optional[Path]:
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    return None


def _load_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    path = _find_font(bold)
    if path:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


INFOGRAPHIC_SIZE = (1080, 1350)


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], weight: float) -> Tuple[int, int, int]:
    """Meng kleur a richting b (weight 0 = a, 1 = b)."""
    return tuple(int(round(ca + (cb - ca) * weight)) for ca, cb in zip(a, b))


def generate_infographic(title: str, blocks: list, project_name: str) -> bytes:
    """Render een staande (1080x1350) infographic als PNG-bytes: titel bovenaan
    en genummerde blokken (kop + boodschap), in dezelfde per-project stijl als
    de quote-card. `blocks` is een lijst dicts met 'heading' en 'text'.

    Portrait-formaat rankt goed in Google Images en is direct bruikbaar als
    social-afbeelding (4:5)."""
    style = _style_for(project_name)
    width, height = INFOGRAPHIC_SIZE
    img = Image.new("RGB", (width, height), style["bg"])
    draw = ImageDraw.Draw(img)

    margin = 80
    max_width = width - 2 * margin

    # Accent-balk + titel
    draw.rectangle([(margin, 68), (margin + 90, 76)], fill=style["accent"])
    title_font = _load_font(52, bold=True)
    y = 100
    for line in _wrap_to_width(draw, title.strip(), title_font, max_width)[:3]:
        draw.text((margin, y), line, font=title_font, fill=style["fg"])
        y += int(52 * 1.2)
    y += 28

    blocks = [b for b in blocks if (b.get("heading") or b.get("text"))][:7]
    n = max(len(blocks), 1)
    footer_h = 80
    block_h = (height - footer_h - y) // n

    # Bij 6-7 blokken: kleinere fonts + minder regels zodat niets overlapt.
    compact = n > 5
    head_size = 30 if compact else 34
    text_size = 23 if compact else 26
    head_font = _load_font(head_size, bold=True)
    text_font = _load_font(text_size, bold=False)
    num_font = _load_font(30, bold=True)
    muted_fg = _mix(style["fg"], style["bg"], 0.3)

    text_x = margin + 78
    text_w = width - margin - text_x
    for i, block in enumerate(blocks):
        by = y + i * block_h
        # Nummer-cirkel in accentkleur
        r = 26
        cx, cy = margin + r, by + r + 4
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=style["accent"])
        num = str(i + 1)
        bbox = draw.textbbox((0, 0), num, font=num_font)
        draw.text((cx - (bbox[2] - bbox[0]) / 2 - bbox[0], cy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                  num, font=num_font, fill=style["bg"])

        ty = by
        heading = (block.get("heading") or "").strip()
        if heading:
            for line in _wrap_to_width(draw, heading, head_font, text_w)[:1 if compact else 2]:
                draw.text((text_x, ty), line, font=head_font, fill=style["fg"])
                ty += int(head_size * 1.2)
        text = (block.get("text") or "").strip()
        if text:
            ty += 4
            for line in _wrap_to_width(draw, text, text_font, text_w)[:2 if compact else 3]:
                draw.text((text_x, ty), line, font=text_font, fill=muted_fg)
                ty += int(text_size * 1.25)

    footer_font = _load_font(28, bold=False)
    footer = (project_name or "").strip()
    if footer:
        bbox = draw.textbbox((0, 0), footer, font=footer_font)
        draw.text(((width - (bbox[2] - bbox[0])) // 2, height - 62),
                  footer, font=footer_font, fill=style["accent"])

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_quote_card(title: str, project_name: str, subtitle: str = "") -> bytes:
    """Render een vierkante (1080x1080) quote-card als PNG-bytes."""
    style = _style_for(project_name)
    img = Image.new("RGB", CARD_SIZE, style["bg"])
    draw = ImageDraw.Draw(img)

    margin = 90
    max_width = CARD_SIZE[0] - 2 * margin

    # Accent-balk bovenaan
    draw.rectangle([(margin, margin), (margin + 90, margin + 8)], fill=style["accent"])

    title_font = _load_font(64, bold=True)
    lines = _wrap_to_width(draw, title.strip(), title_font, max_width)[:6]

    line_height = int(title_font.size * 1.25) if hasattr(title_font, "size") else 78
    total_h = len(lines) * line_height
    y = (CARD_SIZE[1] - total_h) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        w = bbox[2] - bbox[0]
        x = (CARD_SIZE[0] - w) // 2
        draw.text((x, y), line, font=title_font, fill=style["fg"])
        y += line_height

    footer_font = _load_font(32, bold=False)
    footer = (project_name or "").strip()
    if subtitle:
        footer = f"{footer} · {subtitle}" if footer else subtitle
    if footer:
        bbox = draw.textbbox((0, 0), footer, font=footer_font)
        w = bbox[2] - bbox[0]
        x = (CARD_SIZE[0] - w) // 2
        draw.text((x, CARD_SIZE[1] - margin - 20), footer, font=footer_font, fill=style["accent"])

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

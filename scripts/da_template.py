"""DA Pro Template v5 — schoon.

- Geen angle/subtekst meer op het beeld (geen 'reactivatie alle pagina's').
- Geen 'Gepubliceerd door ImpactOS' meer.
- Kop wordt dynamisch gewrapt zodat hij netjes binnen het witte vak valt.
- 50+ (leeftijd-)badge blijft verbonden aan het logo (rechts ervan).
"""
import os
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = "C:/Windows/Fonts"
LOGO_PATH = "D:/APPS/DatingAssistent/DatingAssistentApp/public/images/LogoDA.png"

CREAM  = (247, 243, 236)
INK    = (26, 23, 20)
ROSE   = (181, 131, 141)
ORANGE = (242, 98, 14)
GREY   = (78, 72, 68)


def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except Exception:
        return ImageFont.load_default()


def _fit(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _paste_logo(bg, x, y, h):
    logo = Image.open(LOGO_PATH).convert("RGBA")
    w, hh = logo.size
    wnew = int(h * w / hh)
    logo = logo.resize((wnew, h))
    bg.paste(logo, (x, y), logo)


def render_da_card(photo_path, title, subtitle, age_label, brand, out):
    W, H = 1080, 1350
    bg = Image.new("RGBA", (W, H), (*CREAM, 255))
    draw = ImageDraw.Draw(bg, "RGBA")

    photo_h = int(H * 0.55)
    if photo_path and os.path.exists(photo_path):
        src = Image.open(photo_path).convert("RGB")
        sw, sh = src.size
        scale = max(W / sw, photo_h / sh)
        nw, nh = int(sw * scale), int(sh * scale)
        src = src.resize((nw, nh))
        bg.paste(src.crop(((nw - W) // 2, 0, (nw - W) // 2 + W, photo_h)), (0, 0))
        fade = Image.new("L", (W, 240), 0)
        ImageDraw.Draw(fade).rectangle([0, 100, W, 240], fill=255)
        bg.paste(Image.composite(Image.new("RGB", (W, 240), CREAM),
                                 bg.crop((0, photo_h - 240, W, photo_h)),
                                 fade.point(lambda p: int(p * 0.92))), (0, photo_h - 240))
    else:
        draw.rectangle([0, 0, W, photo_h], fill=CREAM)
        draw.rectangle([0, photo_h - 6, W, photo_h], fill=ORANGE)

    panel_top = photo_h
    draw.rectangle([0, panel_top, W, H], fill=CREAM)
    draw.rectangle([0, panel_top - 5, W, panel_top], fill=ORANGE)  # dunne accent-lijn

    # Logo + verbonden leeftijd-badge, bovenaan paneel
    logo_h = 96
    logo_x = 64
    logo_y = panel_top + 54
    _paste_logo(bg, logo_x, logo_y, logo_h)
    if age_label:
        bx = logo_x + int(logo_h * 1.05) + 18
        by = logo_y + logo_h // 2
        br = 44
        draw.ellipse([bx - br, by - br, bx + br, by + br], fill=ORANGE)
        f = _font("arialbd.ttf", 40)
        tb = draw.textbbox((0, 0), age_label, font=f)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text((bx - tw / 2, by - th / 2), age_label, font=f, fill=(255, 255, 255))

    # Kop — dynamische grootte zodat ie netjes in het vak past
    margin = 64
    max_w = W - 2 * margin
    base = 72
    if len(title) > 70:
        base = 58
    if len(title) > 110:
        base = 48
    tf = _font("arialbd.ttf", base)
    lines = _fit(draw, title, tf, max_w)
    max_lines = 4
    while len(lines) > max_lines and base > 34:
        base -= 6
        tf = _font("arialbd.ttf", base)
        lines = _fit(draw, title, tf, max_w)
    y = logo_y + logo_h + 64
    line_h = int(base * 1.14)
    has_plus = any("+" in w for w in title.split())
    for i, ln in enumerate(lines[:max_lines]):
        col = ROSE if (has_plus and i == len(lines[:max_lines]) - 1) else INK
        draw.text((margin, y), ln, font=tf, fill=col)
        y += line_h

    # Merknaam onderaan (zonder "Gepubliceerd door ImpactOS")
    draw.rectangle([0, H - 70, W, H - 67], fill=ORANGE)
    bf = _font("arialbd.ttf", 30)
    draw.text((margin, H - 54), brand, font=bf, fill=INK)

    bg.convert("RGB").save(out, "PNG")
    return out


if __name__ == "__main__":
    os.makedirs("data/uploads", exist_ok=True)
    render_da_card(None, "Alleen zijn is niet hetzelfde als eenzaam zijn. Tijd voor een nieuw hoofdstuk.",
                   "", "50+", "DatingAssistent voor 50-plussers", "data/uploads/proof_da_50v5.png")
    print("v5 proef: data/uploads/proof_da_50v5.png")

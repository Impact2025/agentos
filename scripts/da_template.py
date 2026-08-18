"""DA Pro Template v4 — wereldklasse, echt logo, gefotografeerd beeld.

Layout (1080x1350, IG/FB):
  - Boven 60%: wereldklasse-foto (door jou/MJ of image_generate), cover-crop, zacht
  - Onder 40%: clean crème paneel, kop + subtekst + echt logo + merknaam
  - Oranje accent-lijn (dun) tussen foto en paneel
  - Oranje leeftijd-badge verbonden aan het logo (rechts naast logo, niet los)
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
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

    photo_h = int(H * 0.60)
    if photo_path and os.path.exists(photo_path):
        src = Image.open(photo_path).convert("RGB")
        sw, sh = src.size
        scale = max(W / sw, photo_h / sh)
        nw, nh = int(sw*scale), int(sh*scale)
        src = src.resize((nw, nh))
        bg.paste(src.crop(((nw-W)//2, 0, (nw-W)//2+W, photo_h)), (0, 0))
        # geleidelijke crème-veilving onderaan foto
        fade = Image.new("L", (W, 200), 0)
        ImageDraw.Draw(fade).rectangle([0, 80, W, 200], fill=255)
        bg.paste(Image.composite(Image.new("RGB", (W,200), CREAM),
                                 bg.crop((0, photo_h-200, W, photo_h)),
                                 fade.point(lambda p: int(p*0.9))), (0, photo_h-200))
    else:
        draw.rectangle([0, 0, W, photo_h], fill=CREAM)
        draw.rectangle([0, photo_h-6, W, photo_h], fill=ORANGE)

    panel_top = photo_h
    draw.rectangle([0, panel_top, W, H], fill=CREAM)
    draw.rectangle([0, panel_top-5, W, panel_top], fill=ORANGE)  # dunne accent-lijn

    # Logo (scherp, goede grootte) + verbonden leeftijd-badge ernaast
    logo_h = 110
    logo_y = panel_top + 64
    _paste_logo(bg, 64, logo_y, logo_h)
    if age_label:
        bx = 64 + int(logo_h * 1.05) + 22
        by = logo_y + logo_h//2
        br = 50
        draw.ellipse([bx-br, by-br, bx+br, by+br], fill=ORANGE)
        f = _font("arialbd.ttf", 46)
        tb = draw.textbbox((0,0), age_label, font=f)
        tw, th = tb[2]-tb[0], tb[3]-tb[1]
        draw.text((bx-tw/2, by-th/2), age_label, font=f, fill=(255,255,255))

    # Kop
    tf = _font("arialbd.ttf", 76)
    lines = _fit(draw, title, tf, W - 140)
    y = panel_top + 200
    rose_last = any("+" in w for w in title.split())
    for i, ln in enumerate(lines[:3]):
        col = ROSE if (rose_last and i == len(lines[:3]) - 1) else INK
        draw.text((64, y), ln, font=tf, fill=col)
        y += 86

    # Subtekst
    if subtitle:
        sf = _font("segoeui.ttf", 34)
        slines = _fit(draw, subtitle, sf, W - 140)
        y += 14
        for ln in slines[:2]:
            draw.text((64, y), ln, font=sf, fill=GREY)
            y += 48

    # Merknaam onderaan (ruimte, niet afgeknipt)
    draw.rectangle([0, H-92, W, H-89], fill=ORANGE)
    bf = _font("arialbd.ttf", 32)
    draw.text((64, H-70), brand, font=bf, fill=INK)
    draw.text((64 + draw.textlength(brand, font=bf) + 16, H-66),
              "• Gepubliceerd door Agentos", font=_font("segoeui.ttf", 26), fill=GREY)

    bg.convert("RGB").save(out, "PNG")
    return out

if __name__ == "__main__":
    os.makedirs("data/uploads", exist_ok=True)
    render_da_card(None, "De 14e eerste date", "herkenning voor de swipe-moe", "30+",
                   "DatingAssistent voor 30-plussers", "data/uploads/proof_da_30v4.png")
    print("v4 proef: data/uploads/proof_da_30v4.png")

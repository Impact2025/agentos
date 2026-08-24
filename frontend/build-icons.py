"""Favicon voor Impact OS genereren — de Iris-aperture, lichte variant.

Impact OS had tot 13 aug 2026 geen favicon (alleen het losse `logo.png` in de
zijbalk, een generiek beeld zonder relatie tot de rest van het merk). Iris
Remote (`remote/build-icons.py`) had al een bewust motief: acht spaken rond
een pupil, het "oog" van de AI-manager. Dat motief hoort ook hier, niet een
tweede logo ernaast — Impact OS en Iris Remote zijn hetzelfde product op twee
schermen.

Enige verschil met de Remote-versie: die is getekend voor een donkere
achtergrond (`--surface-bg #121118`); de Impact OS-zijbalk is wit, dus dit
script gebruikt Impact OS' eigen tokens (`--bg`/`--accent` uit app.css) i.p.v.
de Remote-kleuren simpelweg te hergebruiken op een verkeerde ondergrond.

Draaien:  .venv/Scripts/python.exe frontend/build-icons.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent

BG = (255, 255, 255)        # --card-bg #ffffff — favicons staan op een lichte tabblad-balk
ACCENT = (79, 70, 229)      # --accent #4f46e5 — de pupil
ACCENT_DIM = (129, 140, 248)  # indigo-400 — de spaken, iets lichter voor contrast t.o.v. de pupil

SS = 4  # supersampling: 4× tekenen en terugschalen = gladde randen zonder AA-code


def _draw_aperture(img: Image.Image, size: int, glyph_ratio: float) -> None:
    d = ImageDraw.Draw(img)
    c = size / 2
    r = c * glyph_ratio
    r_inner = r * 0.42
    r_pupil = r * 0.24
    line_w = max(1, int(r * 0.13))  # iets dikker dan de Remote-versie: leest beter op een licht vlak
    for i in range(8):
        a = math.radians(i * 45 - 90)
        x0, y0 = c + r_inner * math.cos(a), c + r_inner * math.sin(a)
        x1, y1 = c + r * math.cos(a), c + r * math.sin(a)
        d.line([x0, y0, x1, y1], fill=ACCENT_DIM, width=line_w)
    d.ellipse([c - r_pupil, c - r_pupil, c + r_pupil, c + r_pupil], fill=ACCENT)


def render(size: int, glyph_ratio: float) -> Image.Image:
    big = Image.new("RGB", (size * SS, size * SS), BG)
    _draw_aperture(big, size * SS, glyph_ratio)
    return big.resize((size, size), Image.LANCZOS)


def main() -> None:
    jobs = [
        ("favicon-32.png", 32, 0.72),
        # iOS rondt apple-touch-icon zelf af en accepteert geen transparantie.
        ("apple-touch-icon.png", 180, 0.62),
    ]
    for name, size, ratio in jobs:
        render(size, ratio).save(OUT / name, "PNG", optimize=True)
        print(f"  {name}  ({size}×{size})")
    print(f"Klaar — {len(jobs)} iconen in {OUT}")


if __name__ == "__main__":
    main()

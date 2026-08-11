"""App-iconen voor Iris Remote genereren.

Waarom dit bestand bestaat: `manifest.json` had een lege `icons`-lijst. Android
Chrome biedt installeren dan niet eens aan (er is minimaal één 192px-icoon
nodig) en iOS zet een screenshot van de pagina op je beginscherm. Voor een app
die alleen op een telefoon bestaat is dat de voordeur.

Zelfde afspraak als de fonts en tailwind.css: het script staat in de repo, de
uitkomst staat in git. Een deploy hangt dus niet af van een build-stap die
iemand moet onthouden.

Draaien:  .venv/Scripts/python.exe remote/build-icons.py

Het motief is de Iris-aperture — acht spaken rond een pupil, dezelfde
geometrie als het topbar-merkteken (index.html) en app.js:apertureMark().
Tot 6 aug 2026 stond hier het generieke `hub`-symbool (een centrale knoop
met zes satellieten) — geen merk, alleen het eerste Material-icoon dat
bij de hand was toen het manifest een icoon nodig had.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "icons"

BG = (18, 17, 24)           # --surface-bg #121118
BG_GLOW = (30, 26, 40)      # subtiele halo achter de aperture
IRIS = (156, 143, 255)      # --iris-blue #9c8fff
IRIS_DIM = (124, 111, 232)  # --iris-blue-deep #7c6fe8, voor de spaken

SS = 4  # supersampling: 4× tekenen en terugschalen = gladde randen zonder AA-code


def _draw_aperture(img: Image.Image, size: int, glyph_ratio: float) -> None:
    """Tekent de aperture gecentreerd, met een straal van `glyph_ratio` × halve zijde."""
    d = ImageDraw.Draw(img)
    c = size / 2
    r = c * glyph_ratio          # buitenrand van de spaken
    r_inner = r * 0.42           # binnenrand van de spaken (waar ze beginnen)
    r_pupil = r * 0.24           # centrale pupil
    line_w = max(1, int(r * 0.11))

    # Halo: geeft diepte op een donker beginscherm zonder een tweede kleur.
    for i in range(14, 0, -1):
        f = i / 14
        rr = r * (1.25 + f * 0.55)
        d.ellipse([c - rr, c - rr, c + rr, c + rr],
                  fill=tuple(int(BG[k] + (BG_GLOW[k] - BG[k]) * (1 - f) * 0.5) for k in range(3)))

    for i in range(8):
        a = math.radians(i * 45 - 90)
        x0, y0 = c + r_inner * math.cos(a), c + r_inner * math.sin(a)
        x1, y1 = c + r * math.cos(a), c + r * math.sin(a)
        d.line([x0, y0, x1, y1], fill=IRIS_DIM, width=line_w)
    d.ellipse([c - r_pupil, c - r_pupil, c + r_pupil, c + r_pupil], fill=IRIS)


def render(size: int, glyph_ratio: float) -> Image.Image:
    big = Image.new("RGB", (size * SS, size * SS), BG)
    _draw_aperture(big, size * SS, glyph_ratio)
    return big.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # `any`-iconen mogen tot de rand; het maskable-icoon wordt door Android
    # bijgesneden tot een cirkel van ~80%, dus daar moet de glyph kleiner.
    jobs = [
        ("icon-192.png", 192, 0.62),
        ("icon-512.png", 512, 0.62),
        ("icon-maskable-512.png", 512, 0.42),
        # iOS rondt apple-touch-icon zelf af en accepteert geen transparantie.
        ("apple-touch-icon.png", 180, 0.62),
        ("favicon-32.png", 32, 0.70),
    ]
    for name, size, ratio in jobs:
        render(size, ratio).save(OUT / name, "PNG", optimize=True)
        print(f"  {name}  ({size}×{size})")
    print(f"Klaar — {len(jobs)} iconen in {OUT}")


if __name__ == "__main__":
    main()

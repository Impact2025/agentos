"""App-iconen voor Iris Remote genereren.

Waarom dit bestand bestaat: `manifest.json` had een lege `icons`-lijst. Android
Chrome biedt installeren dan niet eens aan (er is minimaal één 192px-icoon
nodig) en iOS zet een screenshot van de pagina op je beginscherm. Voor een app
die alleen op een telefoon bestaat is dat de voordeur.

Zelfde afspraak als de fonts en tailwind.css: het script staat in de repo, de
uitkomst staat in git. Een deploy hangt dus niet af van een build-stap die
iemand moet onthouden.

Draaien:  .venv/Scripts/python.exe remote/build-icons.py

Het motief is het `hub`-symbool uit de header — een centrale knoop met zes
satellieten: de lokale machine en de plekken waar hij naartoe reikt.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "icons"

BG = (16, 20, 21)          # --surface-bg #101415
BG_GLOW = (24, 37, 45)     # subtiele halo achter de knoop
IRIS = (142, 213, 255)     # --iris-blue #8ed5ff
IRIS_DIM = (56, 189, 248)  # --iris-blue-deep, voor de verbindingslijnen

SS = 4  # supersampling: 4× tekenen en terugschalen = gladde randen zonder AA-code


def _draw_hub(img: Image.Image, size: int, glyph_ratio: float) -> None:
    """Tekent de hub gecentreerd, met een straal van `glyph_ratio` × halve zijde."""
    d = ImageDraw.Draw(img)
    c = size / 2
    r = c * glyph_ratio          # afstand tot de satellieten
    r_center = r * 0.30          # centrale knoop
    r_node = r * 0.17            # satellieten
    line_w = max(1, int(r * 0.075))

    # Halo: geeft diepte op een donker beginscherm zonder een tweede kleur.
    for i in range(14, 0, -1):
        f = i / 14
        rr = r * (1.25 + f * 0.55)
        d.ellipse([c - rr, c - rr, c + rr, c + rr],
                  fill=tuple(int(BG[k] + (BG_GLOW[k] - BG[k]) * (1 - f) * 0.5) for k in range(3)))

    nodes = []
    for i in range(6):
        a = math.radians(-90 + i * 60)
        nodes.append((c + r * math.cos(a), c + r * math.sin(a)))

    for x, y in nodes:
        d.line([c, c, x, y], fill=IRIS_DIM, width=line_w)
    for x, y in nodes:
        d.ellipse([x - r_node, y - r_node, x + r_node, y + r_node], fill=IRIS)
    d.ellipse([c - r_center, c - r_center, c + r_center, c + r_center], fill=IRIS)


def render(size: int, glyph_ratio: float) -> Image.Image:
    big = Image.new("RGB", (size * SS, size * SS), BG)
    _draw_hub(big, size * SS, glyph_ratio)
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

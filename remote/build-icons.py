"""App-iconen voor Impact OS Remote genereren, uit het WeAreImpact-hart-logo.

Bron: `icons/logo-source.png` (het officiële hart-netwerk-logo, ook gebruikt
in de hoofd-app en op de website). Tot 25 aug 2026 tekende dit script zelf een
Iris-aperture (acht spaken rond een pupil) — dat was een placeholder-merkteken
voordat de app een eigen naam/logo had. Nu de app "Impact OS Remote" heet,
hoort hier het echte logo te staan, niet een zelfgetekend motief.

Zelfde afspraak als de fonts en tailwind.css: het script staat in de repo, de
uitkomst staat in git. Een deploy hangt dus niet af van een build-stap die
iemand moet onthouden.

Draaien:  .venv/Scripts/python.exe remote/build-icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent / "icons" / "logo-source.png"
OUT = Path(__file__).parent / "icons"

BG = (18, 17, 24, 255)  # --surface-bg #121118 — zelfde donkere achtergrond als de app


def render(size: int, content_ratio: float) -> Image.Image:
    """Zet het logo gecentreerd op een vierkant canvas van `size`px.

    `content_ratio` is hoeveel van het canvas het logo zelf inneemt (de rest
    is achtergrond-marge); de maskable variant heeft een kleinere ratio omdat
    Android 'm bijsnijdt tot een cirkel van ~80% en anders de randen van het
    hart wegvallen.
    """
    logo = Image.open(SRC).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), BG)
    target = int(size * content_ratio)
    scale = target / max(logo.width, logo.height)
    resized = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
    pos = ((size - resized.width) // 2, (size - resized.height) // 2)
    canvas.alpha_composite(resized, pos)
    return canvas


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Bronlogo ontbreekt: {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, 0.72, "RGBA"),
        ("icon-512.png", 512, 0.72, "RGBA"),
        ("icon-maskable-512.png", 512, 0.5, "RGBA"),
        # iOS rondt apple-touch-icon zelf af en accepteert geen transparantie.
        ("apple-touch-icon.png", 180, 0.72, "RGB"),
        ("favicon-32.png", 32, 0.82, "RGBA"),
    ]
    for name, size, ratio, mode in jobs:
        img = render(size, ratio)
        if mode == "RGB":
            flat = Image.new("RGB", img.size, BG[:3])
            flat.paste(img, mask=img.split()[3])
            img = flat
        img.save(OUT / name, "PNG", optimize=True)
        print(f"  {name}  ({size}×{size})")
    print(f"Klaar — {len(jobs)} iconen in {OUT}")


if __name__ == "__main__":
    main()

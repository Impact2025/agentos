"""Favicon voor Impact OS genereren — uit het WeAreImpact-hart-logo.

Tot 24 aug 2026 gebruikte dit script een los getekende "Iris-aperture" (acht
spaken rond een pupil) als favicon, terwijl de zijbalk zelf óók die aperture
toonde in plaats van een echt logo. Vincent wil nu één herkenbaar beeldmerk
door de hele app: het WeAreImpact-hart (`logo-hart.png`, de kleurovergang-
lijntekening van hart naar netwerk) — hetzelfde merkteken dat ook op
weareimpact.nl staat. Favicon/apple-touch-icon worden uit dát bronbestand
gerenderd i.p.v. los getekend, zodat een toekomstige logo-wijziging maar op
één plek hoeft (dit bestand overschrijven, dan opnieuw draaien).

Draaien:  .venv/Scripts/python.exe frontend/build-icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

OUT = Path(__file__).parent
SRC = OUT / "logo-hart.png"

BG = (255, 255, 255)  # --card-bg #ffffff — favicons staan op een lichte tabblad-balk


def _fit(src: Image.Image, size: int, pad_ratio: float, bg=None, alpha_boost: bool = False) -> Image.Image:
    """Schaalt het logo (met transparantie behouden) binnen `size`x`size`,
    met `pad_ratio` marge rondom. `bg=None` behoudt transparantie (favicon),
    een kleur vult 'm op (apple-touch-icon — iOS accepteert geen alpha).
    `alpha_boost`: het bronlogo is een fijne lijntekening — op een klein
    canvas (32px) verdunt LANCZOS-resampling die lijnen tot bijna onzichtbaar.
    Een gamma-curve op het alfakanaal trekt de halfdekkende randpixels weer
    op naar zichtbaar, zónder de vorm te verzwaren (kleur blijft ongemoeid)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0) if bg is None else bg + (255,))
    inner = int(size * (1 - 2 * pad_ratio))
    logo = src.copy()
    logo.thumbnail((inner, inner), Image.LANCZOS)
    if alpha_boost:
        r, g, b, a = logo.split()
        a = a.point(lambda v: int(255 * (v / 255) ** 0.45))
        logo = Image.merge("RGBA", (r, g, b, a))
    x = (size - logo.width) // 2
    y = (size - logo.height) // 2
    canvas.paste(logo, (x, y), logo)
    return canvas if bg is None else canvas.convert("RGB")


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Bronlogo ontbreekt: {SRC}")
    src = Image.open(SRC).convert("RGBA")

    jobs = [
        ("favicon-32.png", 32, 0.04, None, True),
        # iOS rondt apple-touch-icon zelf af en accepteert geen transparantie.
        ("apple-touch-icon.png", 180, 0.14, BG, False),
    ]
    for name, size, pad, bg, boost in jobs:
        _fit(src, size, pad, bg, boost).save(OUT / name, "PNG", optimize=True)
        print(f"  {name}  ({size}×{size})")
    print(f"Klaar — {len(jobs)} iconen in {OUT}")


if __name__ == "__main__":
    main()

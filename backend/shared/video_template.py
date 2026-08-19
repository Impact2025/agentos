"""Video-template — één bewerkbaar profiel per project, zonder code.

Elke video-render leest `projects/<project>/video/template.json`. Ontbreekt dat
bestand, dan gelden veilige defaults (kleuren uit image_gen `_STYLES`, systeem-
font, standaard NL-stem) zodat de render nooit stukloopt op een ontbrekend of
half-ingevuld profiel — onbekende velden vallen terug, bekende overschrijven.

Vier knoppen die je zonder Python kunt draaien:
  - colors : achtergrond / tekst / accent (RGB-tripels)
  - font   : eigen .ttf + groottes per slide-soort (hook/body/cta/caption/footer)
  - logo   : merk-logo/watermerk in beeld (pad, positie, breedte, opaciteit)
  - voice + music : NL neural stem en achtergrondmuziek-track

Padden in het profiel mogen absoluut zijn, relatief aan de projectmap, óf
relatief aan de repo-root — in die volgorde geprobeerd.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .image_gen import _style_for

logger = logging.getLogger(__name__)

# Repo-root = twee mappen boven backend/shared/.
REPO_ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_RELPATH = "video/template.json"

DEFAULT_VOICE = "nl-NL-MaartenNeural"

# Standaard fontgroottes per slide-soort (px).
_DEFAULT_SIZES = {
    "hook": 96,
    "body": 76,
    "cta": 76,
    "caption": 40,
    "footer": 38,
}


@dataclass
class LogoSpec:
    path: str = ""                     # leeg = geen logo
    position: str = "bottom"           # top-left | top-right | top | bottom
    width: int = 220                   # px; hoogte schaalt mee
    opacity: float = 1.0               # 0..1
    resolved: Optional[Path] = None    # gevuld door de loader als het bestand bestaat


@dataclass
class FootageSpec:
    """Achtergrond-beeld per scène (familie-foto's i.p.v. effen merk-slide).

    provider:
      local_photos → map met .jpg/.png die per scène als Ken-Burns-achtergrond
                     dienen (met vignet + onderschrift). Ontbreekt de map of is
                     hij leeg, dan valt de render terug op de merk-slide.
    path:    map relatief aan de projectmap (bijv. 'photos') of absoluut.
    fit:     'cover' (vult, bijsnijden) | 'contain' (past, randen vullen).
    """
    provider: str = ""                # leeg = geen footage (merk-slide)
    path: str = ""
    fit: str = "cover"
    query: str = ""                    # vaste zoekterm (bijv. bij pexels)
    fallback: str = ""                 # provider om op terug te vallen (bijv. "local_photos")
    resolved: Optional[Path] = None    # gevuld door de loader als de map bestaat
    images: List[Path] = field(default_factory=list)
    local_videos: List[Path] = field(default_factory=list)  # bv. Midjourney Animate-clips

@dataclass
class VideoTemplate:
    project: str = ""
    colors: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)  # bg/fg/accent
    font_regular: Optional[Path] = None    # None = systeem-font (image_gen fallback)
    font_bold: Optional[Path] = None
    sizes: Dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_SIZES))
    logo: LogoSpec = field(default_factory=LogoSpec)
    footage: FootageSpec = field(default_factory=FootageSpec)
    footer_text: str = ""                  # leeg = projectnaam
    cta_footer_text: str = ""              # leeg = footer_text
    voice: str = DEFAULT_VOICE
    # Milde defaults: edge-tts is niet getraind om ver van +0% te spreken, dus
    # een grote tempo/toonhoogte-shift klinkt eerder róbotischer dan warmer.
    # Voor écht natuurlijke spraak is `tts_provider="elevenlabs"` de juiste hendel.
    voice_rate: str = "-5%"                # edge-tts spreektempo
    voice_pitch: str = "+0Hz"              # edge-tts toonhoogte
    tts_provider: str = "edge"             # "edge" (gratis) of "elevenlabs" (betaald, natuurlijker)
    elevenlabs_voice_id: str = ""          # leeg = ELEVENLABS_VOICE_ID uit .env
    music: Optional[Path] = None
    motion: bool = True                    # Ken-Burns aan/uit
    source: str = "defaults"               # 'template.json' of 'defaults' (voor logging)

    def size(self, kind: str) -> int:
        return int(self.sizes.get(kind, _DEFAULT_SIZES.get(kind, 60)))


# ── Padden ──────────────────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def _project_dir(project: str) -> Path:
    """De projectmap — exacte naam eerst, anders de map die dezelfde squash-vorm heeft.

    `projects/` bevat zowel 'liefde voor iedereen' als 'liefdevooriedereen', en de
    aanroeper geeft soms 'Liefde voor Iedereen' (hoofdletters uit de UI). Zonder
    deze normalisatie bestaat de map niet, valt `load_template()` stil terug op
    de generieke defaults en rendert de video zonder logo/font/stem — precies de
    fout die je pas ziet als je naar het beeld kijkt, niet in de logs.
    """
    exact = REPO_ROOT / "projects" / (project or "")
    if exact.is_dir() and (exact / TEMPLATE_RELPATH).exists():
        # Windows is case-insensitief: 'Liefde voor Iedereen' opent de map
        # 'liefde voor iedereen'. resolve() geeft de ECHTE schrijfwijze terug,
        # zodat afgeleide paden (video_path in de database) consistent blijven.
        return exact.resolve()
    root = REPO_ROOT / "projects"
    doel = _norm_name(project)
    if doel and root.is_dir():
        # Map met een template.json wint van een lege restmap met dezelfde naam.
        kandidaten = [d for d in sorted(root.iterdir())
                      if d.is_dir() and _norm_name(d.name) == doel]
        for d in kandidaten:
            if (d / TEMPLATE_RELPATH).exists():
                return d
        if kandidaten:
            return kandidaten[0]
    return exact


def _resolve_path(raw: str, project: str) -> Optional[Path]:
    """Zoek een pad: absoluut → relatief aan projectmap → relatief aan repo-root."""
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p if p.exists() else None
    for base in (_project_dir(project), REPO_ROOT):
        cand = base / raw
        if cand.exists():
            return cand
    return None


def _as_rgb(val, fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """Accepteer [r,g,b] of '#rrggbb'; val terug op fallback bij onzin."""
    try:
        if isinstance(val, str) and val.startswith("#") and len(val) == 7:
            return (int(val[1:3], 16), int(val[3:5], 16), int(val[5:7], 16))
        if isinstance(val, (list, tuple)) and len(val) == 3:
            return tuple(max(0, min(255, int(c))) for c in val)
    except (ValueError, TypeError):
        pass
    return fallback


# ── Loader ────────────────────────────────────────────────────────────────

def load_template(project: str) -> VideoTemplate:
    """Laad het template-profiel voor een project (defaults als het ontbreekt)."""
    style = _style_for(project)
    tpl = VideoTemplate(
        project=project,
        colors={"bg": style["bg"], "fg": style["fg"], "accent": style["accent"]},
    )

    path = _project_dir(project) / TEMPLATE_RELPATH
    if not path.exists():
        return tpl

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("template.json onleesbaar voor %s (defaults gebruikt): %s", project, e)
        return tpl

    tpl.source = "template.json"

    colors = data.get("colors") or {}
    for k in ("bg", "fg", "accent"):
        if k in colors:
            tpl.colors[k] = _as_rgb(colors[k], tpl.colors[k])

    font = data.get("font") or {}
    tpl.font_regular = _resolve_path(font.get("path", ""), project)
    tpl.font_bold = _resolve_path(font.get("path_bold", ""), project) or tpl.font_regular
    for kind in _DEFAULT_SIZES:
        key = f"{kind}_size"
        if isinstance(font.get(key), (int, float)):
            tpl.sizes[kind] = int(font[key])

    logo = data.get("logo") or {}
    if logo.get("path"):
        tpl.logo = LogoSpec(
            path=logo.get("path", ""),
            position=(logo.get("position") or "bottom").lower(),  # top-left | top-right | top | bottom
            width=int(logo.get("width", 220) or 220),
            opacity=float(logo.get("opacity", 1.0) or 1.0),
            resolved=_resolve_path(logo.get("path", ""), project),
        )
        if tpl.logo.resolved is None:
            logger.warning("Logo-pad niet gevonden voor %s: %s", project, logo.get("path"))

    # Footage (eigen beeld / stock als achtergrond).
    footage = data.get("footage") or {}
    fprovider = (footage.get("provider") or "").lower()
    if fprovider == "local_photos" and footage.get("path"):
        fdir = _resolve_path(footage.get("path", ""), project)
        # _resolve_path zoekt het pad zelf; voor een map willen we de map vinden.
        if fdir is None:
            cand = _project_dir(project) / footage.get("path", "")
            fdir = cand if cand.is_dir() else None
        if fdir and fdir.is_dir():
            imgs = sorted(
                p for p in fdir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            )
            if imgs:
                tpl.footage = FootageSpec(
                    provider="local_photos",
                    path=footage.get("path", ""),
                    fit=(footage.get("fit") or "cover").lower(),
                    resolved=fdir,
                    images=imgs,
                )
            else:
                logger.warning("Footage-map is leeg voor %s: %s", project, fdir)
        else:
            logger.warning("Footage-map niet gevonden voor %s: %s", project, footage.get("path"))
    elif fprovider == "pexels":
        # Stock-foto's/video's worden bij render-tijd opgehaald uit het thema/script.
        tpl.footage = FootageSpec(
            provider="pexels",
            path=footage.get("path", ""),      # optioneel: vaste zoekterm
            fit=(footage.get("fit") or "cover").lower(),
            query=(footage.get("query") or "").strip(),
            fallback=(footage.get("fallback") or "").lower(),
        )
        # Eigen beeld in `path` (bv. Midjourney-stills of Animate-clips) krijgt
        # ALTIJD voorrang op generieke stock, zodra er iets in staat — niet pas
        # als noodgreep bij een mislukte Pexels-call. Merk-echt beeld verslaat
        # onbekende mensen in stockfoto's, en de render-laag (video_render.py)
        # gebruikt Pexels alleen nog als deze map leeg is.
        if footage.get("path"):
            fdir = _resolve_path(footage.get("path", ""), project)
            if fdir is None:
                cand = _project_dir(project) / footage.get("path", "")
                fdir = cand if cand.is_dir() else None
            if fdir and fdir.is_dir():
                imgs = sorted(
                    p for p in fdir.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                )
                vids = sorted(
                    p for p in fdir.iterdir()
                    if p.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v")
                )
                if imgs:
                    tpl.footage.images = imgs
                if vids:
                    tpl.footage.local_videos = vids
    # Onbekende/lege provider → geen footage (merk-slide).

    tpl.footer_text = (data.get("footer") or {}).get("text", "") or ""
    tpl.cta_footer_text = (data.get("footer") or {}).get("cta_text", "") or ""

    tpl.voice = (data.get("voice") or DEFAULT_VOICE).strip()
    if isinstance(data.get("voice_rate"), str) and data["voice_rate"].strip():
        tpl.voice_rate = data["voice_rate"].strip()
    if isinstance(data.get("voice_pitch"), str) and data["voice_pitch"].strip():
        tpl.voice_pitch = data["voice_pitch"].strip()
    if isinstance(data.get("tts_provider"), str) and data["tts_provider"].strip():
        tpl.tts_provider = data["tts_provider"].strip().lower()
    if isinstance(data.get("elevenlabs_voice_id"), str):
        tpl.elevenlabs_voice_id = data["elevenlabs_voice_id"].strip()
    tpl.music = _resolve_path(data.get("music", ""), project)
    if data.get("music") and tpl.music is None:
        logger.warning("Muziek-pad niet gevonden voor %s: %s", project, data.get("music"))
    if isinstance(data.get("motion"), bool):
        tpl.motion = data["motion"]

    return tpl


# Beschikbare NL neural stemmen (edge-tts) — handig voor UI/documentatie.
NL_VOICES: List[Dict[str, str]] = [
    {"id": "nl-NL-MaartenNeural", "label": "Maarten (man, nuchter)"},
    {"id": "nl-NL-ColetteNeural", "label": "Colette (vrouw, warm)"},
    {"id": "nl-NL-FennaNeural", "label": "Fenna (vrouw, helder)"},
    {"id": "nl-BE-ArnaudNeural", "label": "Arnaud (man, Vlaams)"},
    {"id": "nl-BE-DenaNeural", "label": "Dena (vrouw, Vlaams)"},
]


def write_default_template(project: str, *, overwrite: bool = False) -> Path:
    """Schrijf een volledig ingevuld voorbeeld-template.json (huidige huisstijl).

    Retourneert het pad. Overschrijft niet tenzij overwrite=True.
    """
    style = _style_for(project)
    path = _project_dir(project) / TEMPLATE_RELPATH
    if path.exists() and not overwrite:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "_comment": "Video-template voor dit project. Padden mogen absoluut, "
                    "relatief aan de projectmap, of relatief aan de repo-root zijn.",
        "colors": {
            "bg": list(style["bg"]),
            "fg": list(style["fg"]),
            "accent": list(style["accent"]),
        },
        "font": {
            "path": "",
            "path_bold": "",
            "hook_size": _DEFAULT_SIZES["hook"],
            "body_size": _DEFAULT_SIZES["body"],
            "cta_size": _DEFAULT_SIZES["cta"],
            "caption_size": _DEFAULT_SIZES["caption"],
            "footer_size": _DEFAULT_SIZES["footer"],
        },
        "logo": {"path": "", "position": "bottom", "width": 220, "opacity": 1.0},
        "footer": {"text": "", "cta_text": ""},
        "voice": DEFAULT_VOICE,
        "voice_rate": "-5%",
        "voice_pitch": "+0Hz",
        "tts_provider": "edge",
        "elevenlabs_voice_id": "",
        "music": "",
        "motion": True,
    }
    path.write_text(json.dumps(example, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

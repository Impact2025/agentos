"""Social huisstijl per project — als data, niet als Python-lijst.

Aanleiding (16 aug 2026). Het zes-weken-socialmediaplan voor BewaardVoorJou legt
een complete huisstijl vast: toon per platform, vier vaste hashtag-sets, een
Midjourney-stijlblok, gouden serif-tekst op een donker vlak, en een vast ritme
(ma emotie / wo product / vr-za activatie). Niets daarvan bestond in de code, en
wat er wél stond sprak het tegen:

  - `social_content._brand_voice` schreef élk project als "Vincent van Munster,
    eerste persoon, geen bullet lists, geen emoji-salvo". Voor WeAreImpact klopt
    dat; BewaardVoorJou is een consumentenmerk dat juist met opsommingen, emoji
    en een je/jij-toon werkt. Eén hardgecodeerde stem voor twaalf merken betekent
    dat elf merken de verkeerde stem krijgen.
  - De terugval-prompt voor beeld was "clean minimal typography, soft neutral
    background, amber accent, --ar 1:1" — precies het tegenovergestelde van het
    vastgelegde "warm cinematic photography, golden hour, muted earthy tones,
    --ar 4:5".
  - Hashtags kwamen alleen in het TikTok-pack voor. De vier sets uit het plan
    (A emotie / B product / C LinkedIn / D X) bereikten geen enkele post.

Waarom data en niet code: dit is de derde plek in deze codebase waar een
merkregel als Python-lijst begon (zie `mail_sender_rules`, `sites.profile`) en
het patroon is elke keer hetzelfde — onzichtbaar, niet aan te passen zonder
programmeur, en zonder terugwerkende kracht. Een profiel in
`projects/<project>/social/style.json` is te lezen, te wijzigen en per project
te verschillen zonder dat iemand deze module aanraakt.

**Ontbreekt het profiel, dan verandert er niets.** `DEFAULTS` is letterlijk het
gedrag van vóór dit bestand, zodat de elf andere projecten exact blijven draaien
zoals ze deden. Een half of onleesbaar profiel valt per veld terug, nooit in zijn
geheel — een merk dat alleen zijn hashtags vastlegt hoort niet ineens zijn stem
kwijt te zijn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
STYLE_RELPATH = "social/style.json"

# Het gedrag van vóór dit bestand — de generieke WeAreImpact-stem. Elk project
# zonder eigen profiel krijgt exact dit, woord voor woord.
DEFAULT_VOICE = (
    "Schrijf als de eigenaar (Vincent van Munster) — eerste persoon (ik/wij), "
    "warm en nuchter, geen robot-taal, geen uitroeptekens-geweld. "
    "Direct, geen jargon, mens centraal, technologie als stille achtergrond. "
    "Geen aandachtstreepjes (— / –). Geen bullet lists in de post zelf."
)

DEFAULT_TONE = {
    "linkedin": "Nuchter-professioneel, eerste persoon, zonder jargon. Max 60 woorden. "
                "Eindig met een open vraag om engagement uit te lokken.",
    "facebook": "Warm en menselijk, alsof je tegen een bekende praat. Max 50 woorden.",
    "instagram": "Warm en kort, emoji-light (max 1 emoji). Max 40 woorden. Casual caption-toon.",
    "tiktok": "Casual en kort, alsof je met een vriend praat. Max 30 woorden. Geen hashtag-salvo "
              "in de body (hashtags komen in het aparte veld).",
    "twitter": "Scherp en direct, één kerninzicht per tweet. Max 250 tekens (er komt nog een "
               "link bij). Geen hashtags, geen emoji-salvo — de eerste zin is de hook.",
}

# De generieke beeld-terugval van vóór dit bestand (amber, vierkant, minimalistisch).
DEFAULT_STIJLBLOK = ("warm amber accent (#e5a500), clean minimal typography, "
                     "soft neutral background, professional Dutch brand style, high contrast")
DEFAULT_ASPECT = "1:1"


@dataclass
class OverlaySpec:
    """Hoe tekst in het beeld gebrand wordt (Pillow, `social_image._brand_overlay`).

    Leeg font_path = systeem-font (het oude gedrag). Een project dat een eigen
    .ttf meelevert (BewaardVoorJou heeft Playfair Display in `fonts/`) krijgt
    zijn eigen letter zonder dat de renderer iets van dat merk hoeft te weten.
    """
    font_path: str = ""             # relatief aan de projectmap of repo-root
    font_path_regular: str = ""
    kop_kleur: str = "#f5de82"      # zacht goud
    subtekst_kleur: str = "#fffaf0"  # warm wit
    accent_kleur: str = "#e5a500"   # merk-accent (Bijeen: oranje #E4603B)
    nacht_kleur: str = "#171F27"     # donker vlak / nacht-achtergrond
    achtergrond_kleur: str = "#FAF6F0"  # lichte achtergrond (crème)
    vlak: bool = False              # donker transparant vlak achter de tekst
    vlak_opacity: float = 0.55
    logo_path: str = ""
    logo_positie: str = "top-left"
    logo_breedte: int = 180
    footer_tekst: str = ""          # bijv. 'www.BewaardVoorJou.nl' of een tagline
    wordmark: str = ""              # merknaam naast het logo in het onderschrift-vlak
    badge_tekst: str = ""           # pil-label boven de kop, modus 'stelling' (bv. 'Stelling')
    modus: str = "foto"             # 'foto' = vignet-overlay op stockfoto;
                                    # 'typografisch' = crème achtergrond, serif-kop,
                                    # geen foto (Bijeen "Inzicht"-formaat);
                                    # 'foto-onderschrift' = foto boven, accent-balk,
                                    # crème onderschrift-vlak met kop + logo/wordmark/
                                    # tagline onderaan (DatingAssistent, 21 aug 2026);
                                    # 'stelling' = crème achtergrond, gekleurde badge-pil
                                    # ('Stelling') linksboven, grote stelling-kop, grijs
                                    # onderschrift, logo rechtsonder — geen foto
                                    # (LiefdeVoorIedereen, 21 aug 2026)


@dataclass
class RitmeSlot:
    """Eén vast postmoment: welke dag, hoe laat, welk soort post."""
    weekdag: int          # 0=maandag .. 6=zondag
    tijd: str             # 'HH:MM'
    post_type: str        # 'emotie' | 'product' | 'activatie' | ...


@dataclass
class SocialStyle:
    project: str = ""
    bron: str = "defaults"                       # 'style.json' of 'defaults'
    voice: str = DEFAULT_VOICE
    tone: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TONE))
    hashtag_sets: Dict[str, List[str]] = field(default_factory=dict)
    # platform -> set-naam die standaard geldt (bijv. linkedin -> 'C')
    platform_set: Dict[str, str] = field(default_factory=dict)
    # post-type -> {platform: set-naam}; overschrijft platform_set
    type_set: Dict[str, Dict[str, str]] = field(default_factory=dict)
    stijlblok: str = DEFAULT_STIJLBLOK
    aspect: str = DEFAULT_ASPECT
    overlay: OverlaySpec = field(default_factory=OverlaySpec)
    ritme: List[RitmeSlot] = field(default_factory=list)
    utm: Dict[str, str] = field(default_factory=dict)
    site_url: str = ""

    # ── Wat de aanroepers vragen ───────────────────────────────────────────

    def tone_for(self, platform: str) -> str:
        return self.tone.get(platform, DEFAULT_TONE.get(platform, ""))

    def hashtags_for(self, platform: str, post_type: str = "") -> List[str]:
        """De hashtag-set voor dit platform bij dit soort post.

        Volgorde: een expliciete regel voor dit post-type wint van de vaste
        platform-set. LinkedIn en X hebben in het plan géén type-afhankelijkheid
        (altijd set C resp. D) en horen dus alleen in `platform_set` te staan.
        Onbekende combinatie = geen hashtags, nooit een gegokte set: een verkeerde
        hashtag onder een post is zichtbaarder dan geen.
        """
        naam = ""
        if post_type:
            naam = (self.type_set.get(post_type) or {}).get(platform, "")
        if not naam:
            naam = self.platform_set.get(platform, "")
        if not naam:
            return []
        return list(self.hashtag_sets.get(naam, []))

    def hashtag_regel(self, platform: str, post_type: str = "") -> str:
        """De hashtags als één plak-klare regel ('' als er geen set geldt)."""
        tags = self.hashtags_for(platform, post_type)
        return " ".join(tags) if tags else ""

    def image_prompt(self, onderwerp: str) -> str:
        """Bouw een volledige beeld-prompt: onderwerp + het vaste stijlblok.

        Het stijlblok staat achteraan, precies zoals het plan het voorschrijft
        ("gebruik dit vaste stijlblok achter elke prompt zodat alle beelden bij
        elkaar passen") — zo blijft de serie herkenbaar terwijl het onderwerp
        per post verschilt.
        """
        kern = (onderwerp or "").strip().rstrip(",")
        blok = self.stijlblok.strip().rstrip(",")
        prompt = f"{kern}, {blok}" if kern else blok
        return f"{prompt} --ar {self.aspect.replace(':', ':')} --style raw --v 6"

    def utm_url(self, url: str, kanaal: str) -> str:
        """Plak UTM-tags aan een link (leeg utm-blok = de URL onveranderd).

        Het plan vraagt hier expliciet om: zonder bron-tag is achteraf niet te
        zien welk kanaal verkoopt, en dan is 'meten = weten' een voornemen.
        """
        if not url or not self.utm:
            return url
        source = kanaal or self.utm.get("source", "")
        parts = []
        if source:
            parts.append(f"utm_source={source}")
        if self.utm.get("medium"):
            parts.append(f"utm_medium={self.utm['medium']}")
        if self.utm.get("campaign"):
            parts.append(f"utm_campaign={self.utm['campaign']}")
        if not parts:
            return url
        sep = "&" if "?" in url else "?"
        return url + sep + "&".join(parts)

    def resolve(self, relpath: str) -> Optional[Path]:
        """Zoek een bestand: absoluut → elke gelijkgespelde projectmap → repo-root."""
        if not relpath:
            return None
        p = Path(relpath)
        if p.is_absolute():
            return p if p.exists() else None
        for base in _project_dirs(self.project) + [REPO_ROOT]:
            cand = base / relpath
            if cand.exists():
                return cand
        return None


def _project_dir(project: str) -> Path:
    return REPO_ROOT / "projects" / (project or "")


def _norm(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def _project_dirs(project: str) -> List[Path]:
    """Alle projectmappen die dezelfde squash-vorm delen, exacte naam eerst.

    `projects/` bevat zowel 'bewaard voor jou' als 'bewaardvoorjou' — dezelfde
    spellingssplitsing die `shared/projects.py:squash_project` elders opruimt.
    Beide normaliseren naar hetzelfde, dus is niet met normaliseren alléén te
    kiezen welke de échte inhoud draagt (assets, style.json) en welke een lege
    restmap is; `resolve()`/`_style_path()` proberen daarom élke match, niet
    zomaar de eerste — anders laadt het profiel wel, maar wijst elk relatief
    pad erin (logo, font) in het niets, stil, want het beeld rendert gewoon
    alleen zonder logo."""
    dirs: List[Path] = []
    direct = _project_dir(project)
    if direct.is_dir():
        dirs.append(direct)
    root = REPO_ROOT / "projects"
    doel = _norm(project)
    if root.is_dir() and doel:
        for d in sorted(root.iterdir()):
            if d.is_dir() and _norm(d.name) == doel and d not in dirs:
                dirs.append(d)
    return dirs


def _style_path(project: str) -> Optional[Path]:
    """Vind het profiel in de eerste projectmap die het écht bevat.

    Een profiel dat alleen bij exacte mapnaam gevonden wordt, is voor de helft
    van de aanroepers onvindbaar, en dan valt de huisstijl stil terug op de
    generieke stem zonder dat iemand het merkt."""
    for d in _project_dirs(project):
        cand = d / STYLE_RELPATH
        if cand.exists():
            return cand
    return None


_CACHE: Dict[str, SocialStyle] = {}


def load_style(project: str, *, refresh: bool = False) -> SocialStyle:
    """Laad het huisstijl-profiel van een project (defaults als het ontbreekt)."""
    key = _norm(project)
    if not refresh and key in _CACHE:
        return _CACHE[key]

    style = SocialStyle(project=project)
    path = _style_path(project)
    if path is None:
        _CACHE[key] = style
        return style

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("social/style.json onleesbaar voor %s (defaults gebruikt): %s", project, e)
        _CACHE[key] = style
        return style

    style.bron = "style.json"

    stem = data.get("stem") or {}
    basis = (stem.get("basis") or "").strip()
    regels = [r.strip() for r in (stem.get("regels") or []) if str(r).strip()]
    if basis or regels:
        style.voice = "\n".join([basis] + [f"- {r}" for r in regels]).strip()

    for plat, spec in (data.get("platform") or {}).items():
        plat = plat.lower()
        if isinstance(spec, dict):
            if spec.get("toon"):
                style.tone[plat] = str(spec["toon"])
            if spec.get("hashtag_set"):
                style.platform_set[plat] = str(spec["hashtag_set"])
        elif isinstance(spec, str):
            style.tone[plat] = spec

    for naam, tags in (data.get("hashtag_sets") or {}).items():
        if isinstance(tags, list):
            style.hashtag_sets[str(naam)] = [
                t if str(t).startswith("#") else f"#{t}" for t in tags if str(t).strip()
            ]

    for ptype, mapping in (data.get("post_types") or {}).items():
        if isinstance(mapping, dict):
            style.type_set[str(ptype)] = {
                k.lower(): str(v) for k, v in (mapping.get("hashtag_set") or {}).items()
            }

    beeld = data.get("beeld") or {}
    if beeld.get("stijlblok"):
        # Een meerregelig stijlblok uit het plan wordt één prompt-regel.
        style.stijlblok = " ".join(str(beeld["stijlblok"]).split())
    if beeld.get("aspect"):
        style.aspect = str(beeld["aspect"])
    ov = beeld.get("overlay") or {}
    if ov:
        style.overlay = OverlaySpec(
            font_path=str(ov.get("font", "") or ""),
            font_path_regular=str(ov.get("font_regular", "") or ""),
            kop_kleur=str(ov.get("kop_kleur", OverlaySpec.kop_kleur)),
            subtekst_kleur=str(ov.get("subtekst_kleur", OverlaySpec.subtekst_kleur)),
            accent_kleur=str(ov.get("accent_kleur", OverlaySpec.accent_kleur)),
            nacht_kleur=str(ov.get("nacht_kleur", OverlaySpec.nacht_kleur)),
            achtergrond_kleur=str(ov.get("achtergrond_kleur", OverlaySpec.achtergrond_kleur)),
            vlak=bool(ov.get("vlak", False)),
            vlak_opacity=float(ov.get("vlak_opacity", 0.55) or 0.55),
            logo_path=str(ov.get("logo", "") or ""),
            logo_positie=str(ov.get("logo_positie", "top-left")),
            logo_breedte=int(ov.get("logo_breedte", 180) or 180),
            footer_tekst=str(ov.get("footer", "") or ""),
            wordmark=str(ov.get("wordmark", "") or ""),
            badge_tekst=str(ov.get("badge", "") or ""),
            modus=str(beeld.get("modus", "foto") or "foto").lower(),
        )

    for slot in (data.get("ritme") or []):
        try:
            style.ritme.append(RitmeSlot(
                weekdag=int(slot["weekdag"]),
                tijd=str(slot.get("tijd", "12:00")),
                post_type=str(slot.get("type", "")),
            ))
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Ritme-slot overgeslagen voor %s: %s (%s)", project, slot, e)

    style.utm = {k: str(v) for k, v in (data.get("utm") or {}).items()}
    style.site_url = str(data.get("site_url", "") or "")

    _CACHE[key] = style
    return style


def hex_to_rgb(value: str, fallback: tuple) -> tuple:
    """'#rrggbb' → (r,g,b); onzin valt terug (zelfde regel als video_template)."""
    try:
        v = value.strip()
        if v.startswith("#") and len(v) == 7:
            return (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
    except (AttributeError, ValueError):
        pass
    return fallback

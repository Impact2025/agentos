"""Pexels-footage client — gratis, gelicenseerde stock-foto's voor video-scènes.

Open Montage doet dit tegen Pexels/NASA; wij koppelen rechtstreeks aan de
Pexels API (https://www.pexels.com/api/). Eén gratis key (geen creditcard)
volstaat: 200 requests/uur, ruim genoeg voor de paar foto's per video.

Licentie: alle Pexels-foto's mogen gratis gebruikt worden (ook commercieel),
met attribution. Deze client geeft de vereiste creditering terug
(photographer + link) zodat de render die in de video-footer kan tonen.

Zonder PEXELS_API_KEY levert deze module geen resultaten en valt de caller
keurig terug op lokale foto's / merk-slides.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from .config import PEXELS_API_KEY

logger = logging.getLogger(__name__)

API_BASE = "https://api.pexels.com/v1"
VIDEO_API_BASE = "https://api.pexels.com/videos"
_PER_PAGE = 30
_CACHE_SUBDIR = "pexels_cache"


@dataclass
class PexelsPhoto:
    id: int
    url: str                 # Pexels-pagina (voor attributie-link)
    src: str                 # directe afbeeldings-URL (grootste beschikbaar)
    photographer: str
    photographer_url: str
    width: int
    height: int

    @property
    def attribution(self) -> str:
        return f"Foto: {self.photographer} via Pexels"


@dataclass
class PexelsVideo:
    id: int
    url: str                 # Pexels-pagina (voor attributie-link)
    src: str                 # directe mp4-URL (gekozen bestand, portrait, ~1080 breed)
    photographer: str
    photographer_url: str
    width: int
    height: int
    duration: float

    @property
    def attribution(self) -> str:
        return f"Video: {self.photographer} via Pexels"


def pexels_ready() -> bool:
    return bool(PEXELS_API_KEY)


def search_photos(query: str, per_page: int = _PER_PAGE) -> List[PexelsPhoto]:
    """Zoek foto's op een Nederlandse/Engelse query. Retourneert [] zonder key."""
    if not PEXELS_API_KEY:
        return []
    q = (query or "family memories").strip() or "family memories"
    try:
        resp = httpx.get(
            f"{API_BASE}/search",
            params={"query": q, "per_page": per_page, "orientation": "portrait"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=20,
        )
        if resp.status_code == 429:
            logger.warning("Pexels rate-limit (429) — val terug op lokale footage")
            return []
        if resp.status_code != 200:
            logger.warning("Pexels search HTTP %s: %s", resp.status_code, resp.text[:160])
            return []
        data = resp.json()
        out: List[PexelsPhoto] = []
        for it in data.get("photos", []):
            src = it.get("src", {})
            big = src.get("large2x") or src.get("large") or src.get("original") or ""
            if not big:
                continue
            out.append(PexelsPhoto(
                id=it.get("id", 0),
                url=it.get("url", ""),
                src=big,
                photographer=it.get("photographer", "onbekend"),
                photographer_url=it.get("photographer_url", ""),
                width=int(it.get("width", 0) or 0),
                height=int(it.get("height", 0) or 0),
            ))
        return out
    except Exception as e:
        logger.warning("Pexels search mislukt: %s", e)
        return []


def _pick_video_file(files: List[dict]) -> Optional[dict]:
    """Kies het beste mp4-bestand: portrait, breedte zo dicht mogelijk bij 1080.

    Pexels levert per video meerdere resoluties (soms tot 4K) — een 4K-download
    per scène zou de render onnodig traag maken voor een beeld dat toch naar
    1080x1920 wordt geschaald. Portrait heeft voorrang; ontbreekt die, dan het
    minst-brede landscape-bestand (wordt later bijgesneden).
    """
    mp4s = [f for f in files if f.get("file_type") == "video/mp4" and f.get("link")]
    if not mp4s:
        return None
    portrait = [f for f in mp4s if (f.get("height") or 0) > (f.get("width") or 0)]
    pool = portrait or mp4s
    return min(pool, key=lambda f: abs((f.get("width") or 0) - 1080))


def search_videos(query: str, per_page: int = 15) -> List[PexelsVideo]:
    """Zoek video-clips (b-roll) op een query. Retourneert [] zonder key/resultaat."""
    if not PEXELS_API_KEY:
        return []
    q = (query or "family memories").strip() or "family memories"
    try:
        resp = httpx.get(
            f"{VIDEO_API_BASE}/search",
            params={"query": q, "per_page": per_page, "orientation": "portrait"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=25,
        )
        if resp.status_code == 429:
            logger.warning("Pexels video rate-limit (429) — val terug op foto's/lokaal")
            return []
        if resp.status_code != 200:
            logger.warning("Pexels video-search HTTP %s: %s", resp.status_code, resp.text[:160])
            return []
        data = resp.json()
        out: List[PexelsVideo] = []
        for it in data.get("videos", []):
            chosen = _pick_video_file(it.get("video_files") or [])
            if not chosen:
                continue
            user = it.get("user") or {}
            out.append(PexelsVideo(
                id=it.get("id", 0),
                url=it.get("url", ""),
                src=chosen.get("link", ""),
                photographer=user.get("name", "onbekend"),
                photographer_url=user.get("url", ""),
                width=int(chosen.get("width", 0) or 0),
                height=int(chosen.get("height", 0) or 0),
                duration=float(it.get("duration", 0) or 0),
            ))
        return out
    except Exception as e:
        logger.warning("Pexels video-search mislukt: %s", e)
        return []


def download_videos(videos: List[PexelsVideo], cache_dir: Path,
                    limit: int = 6) -> List[Tuple[Path, str]]:
    """Download tot `limit` video-clips naar cache_dir. Retourneert (pad, attribution).

    Zelfde overslaan-bij-falen-gedrag als `download_photos`: mislukte downloads
    worden overgeslagen, bij 0 successen valt de caller terug op stilstaand beeld.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: List[Tuple[Path, str]] = []
    for v in videos[:limit]:
        dest = cache_dir / f"pexels_{v.id}.mp4"
        if dest.exists() and dest.stat().st_size > 10_000:
            out.append((dest, v.attribution))
            continue
        try:
            r = httpx.get(v.src, headers={"Authorization": PEXELS_API_KEY},
                          timeout=60, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 10_000:
                dest.write_bytes(r.content)
                out.append((dest, v.attribution))
            else:
                logger.warning("Pexels video-download HTTP %s voor %s", r.status_code, v.id)
        except Exception as e:
            logger.warning("Pexels video-download mislukt (%s): %s", v.id, e)
        time.sleep(0.2)
    return out


def download_photos(photos: List[PexelsPhoto], cache_dir: Path,
                    limit: int = 6) -> List[Tuple[Path, str]]:
    """Download tot `limit` foto's naar cache_dir. Retourneert (pad, attribution).

    Mislukkende downloads worden overgeslagen; bij 0 successen is de lijst leeg
    en valt de caller terug op lokale footage.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: List[Tuple[Path, str]] = []
    for ph in photos[:limit]:
        dest = cache_dir / f"pexels_{ph.id}.jpg"
        if dest.exists() and dest.stat().st_size > 1000:
            out.append((dest, ph.attribution))
            continue
        try:
            r = httpx.get(ph.src, headers={"Authorization": PEXELS_API_KEY},
                          timeout=30, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 1000:
                dest.write_bytes(r.content)
                out.append((dest, ph.attribution))
            else:
                logger.warning("Pexels download HTTP %s voor %s", r.status_code, ph.id)
        except Exception as e:
            logger.warning("Pexels download mislukt (%s): %s", ph.id, e)
        time.sleep(0.2)  # poliete spreiding binnen de rate-limit
    return out

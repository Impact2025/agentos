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

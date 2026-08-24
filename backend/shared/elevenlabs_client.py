"""ElevenLabs voiceover — betaalde, veel natuurlijkere stem dan gratis edge-tts.

edge-tts (Microsoft's gratis neurale NL-stemmen) heeft een plafond: het klinkt
herkenbaar synthetisch, en tempo/toonhoogte forceren maakt het eerder róbotischer
dan natuurlijker (het model is niet getraind om zo langzaam te spreken, en
toonhoogte-shift is een DSP-truc, geen echte stem). ElevenLabs' meertalige model
levert een merkbaar natuurlijkere Nederlandse uitspraak.

`with-timestamps` levert naast de audio een karakter-voor-karakter alignment,
waaruit we dezelfde woord-timing halen als edge-tts' WordBoundary-events — de
karaoke-captions werken dus ongewijzigd door, ongeacht welke provider sprak.

Zonder ELEVENLABS_API_KEY levert deze module geen resultaat en valt de caller
(video_render._synthesize) terug op edge-tts.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import List, Optional

import httpx

from .config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL, ELEVENLABS_VOICE_ID

logger = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io/v1"


def elevenlabs_ready() -> bool:
    return bool(ELEVENLABS_API_KEY)


def _characters_to_words(alignment: dict) -> List[dict]:
    """Groepeer de karakter-alignment tot woord-timings (zelfde vorm als edge-tts)."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: List[dict] = []
    cur_text: List[str] = []
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    for ch, s, e in zip(chars, starts, ends):
        if ch.strip() == "":
            if cur_text:
                words.append({"text": "".join(cur_text), "start": cur_start, "end": cur_end})
                cur_text, cur_start, cur_end = [], None, None
            continue
        if cur_start is None:
            cur_start = s
        cur_text.append(ch)
        cur_end = e
    if cur_text:
        words.append({"text": "".join(cur_text), "start": cur_start, "end": cur_end})
    return words


def synth_with_timings(text: str, out: Path, voice_id: str = "",
                       model: str = "") -> Optional[List[dict]]:
    """Synthetiseer voiceover via ElevenLabs; schrijft mp3 naar `out`.

    Retourneert de woord-timings, of None bij elke storing (geen key, netwerk,
    HTTP-fout, onverwacht antwoord) — de caller valt dan terug op edge-tts.
    Nooit een exception naar buiten: dit is een kwaliteits-upgrade, geen harde
    eis, net als de Pexels-footage.
    """
    if not ELEVENLABS_API_KEY:
        return None
    voice_id = voice_id or ELEVENLABS_VOICE_ID
    model = model or ELEVENLABS_MODEL
    try:
        resp = httpx.post(
            f"{API_BASE}/text-to-speech/{voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": model,
                # Lagere stability + wat style-variatie klinkt minder monotoon/
                # robotisch dan de (hogere) default — ElevenLabs' eigen advies
                # voor natuurlijke spraak i.p.v. een vlakke voorleesstem.
                "voice_settings": {
                    "stability": 0.45,
                    "similarity_boost": 0.8,
                    "style": 0.15,
                    "use_speaker_boost": True,
                },
            },
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning("ElevenLabs TTS HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        audio_b64 = data.get("audio_base64")
        alignment = data.get("alignment")
        if not audio_b64 or not alignment:
            logger.warning("ElevenLabs TTS leverde geen audio/alignment")
            return None
        out.write_bytes(base64.b64decode(audio_b64))
        if not out.exists() or out.stat().st_size == 0:
            return None
        words = _characters_to_words(alignment)
        return [w for w in words if w["text"]]
    except Exception as e:  # noqa: BLE001 — kwaliteits-upgrade, nooit hard falen
        logger.warning("ElevenLabs TTS mislukt, val terug op edge-tts: %s", e)
        return None

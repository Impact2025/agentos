"""Voice-TTS — gratis Nederlandse spraaksynthese via edge-tts.

We houden het bewust simpel en kostenloos: edge-tts (Microsofts gratis
neurale NL-stemmen) heeft geen API-key nodig. Als er wél een
ELEVENLABS_API_KEY in .env staat, prefereren we die voor een natuurlijker
resultaat — maar de voice-laag in de frontend werkt ook volledig zónder
backend, puur met de ingebouwde SpeechSynthesis van de browser.

Dit bestand is de fallback/upgrade: de frontend roept /api/voice/speak aan
wanneer de browser-STT/TTS ongewenst is (bijv. Safari zonder webkitSpeech)
of wanneer je de mooiere edge-tts-stem wilt.
"""
from __future__ import annotations

import asyncio
import io
import logging
import tempfile
from pathlib import Path

from .config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

logger = logging.getLogger(__name__)

# edge-tts neurale Nederlandse stemmen (gratis, geen key).
# "Fenna" = warme vrouwenstem, "Maarten" = mannenstem. We defaulten naar Fenna.
EDGE_NL_VOICE = "nl-NL-FennaNeural"
EDGE_NL_VOICE_ALT = "nl-NL-MaartenNeural"


async def synthesize_edge(text: str, voice: str = EDGE_NL_VOICE) -> bytes:
    """Synthiseer tekst naar MP3-bytes via edge-tts (async, draait in threadpool)."""
    import edge_tts  # lokaal importeren: alleen nodig bij gebruik

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(tmp_path))
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def elevenlabs_ready() -> bool:
    return bool(ELEVENLABS_API_KEY)


def tts(text: str, voice: str = "") -> bytes:
    """Synthiseer tekst -> MP3 bytes.

    Volgorde: ElevenLabs (indien key + gevraagd) -> anders edge-tts NL.
    Draait edge-tts' interne asyncio in een eigen loop (de caller zit al in
    de FastAPI-threadpool, dus asyncio.run is hier veilig).
    """
    text = (text or "").strip()
    if not text:
        return b""

    # Optioneel: ElevenLabs boven edge-tts wanneer een key bekend is.
    if elevenlabs_ready():
        try:
            from ..shared.elevenlabs_client import synth_bytes

            audio = synth_bytes(text, voice or ELEVENLABS_VOICE_ID)
            if audio:
                return audio
        except Exception as e:  # nooit de hele call laten stuklopen op TTS
            logger.warning("ElevenLabs TTS mislukt, val terug op edge-tts: %s", e)

    return asyncio.run(synthesize_edge(text, voice or EDGE_NL_VOICE))

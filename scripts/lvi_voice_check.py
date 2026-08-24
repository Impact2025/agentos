"""Bewijs dat de LVI-render ElevenLabs (Emma, NL) gebruikt en niet stil terugvalt op edge-tts."""
import sys
from pathlib import Path

sys.path.insert(0, r"D:/APPS/impactos")

from backend.shared import elevenlabs_client as el  # noqa: E402
from backend.shared.video_template import load_template  # noqa: E402

tpl = load_template("Liefde voor Iedereen")
out = Path(r"D:/APPS/impactos/_scratch/cmp/lvi_voice_check.mp3")
words = el.synth_with_timings(
    "Daten voelt spannend genoeg. Dan wil je niet ook nog twijfelen of iemand echt is.",
    out, voice_id=tpl.elevenlabs_voice_id)
print("provider:", tpl.tts_provider, "| voice_id:", tpl.elevenlabs_voice_id)
print("elevenlabs ok:", bool(words), "| woorden:", len(words or []))
print("mp3:", out.exists(), out.stat().st_size if out.exists() else 0, "bytes")

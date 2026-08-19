"""Smoke-render DatingAssistent met de nieuwe template (logo, Poppins, ElevenLabs NL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared.video_render import render_short, Scene  # noqa: E402
from backend.shared.video_template import load_template  # noqa: E402

SCENES = [
    Scene(narration="Je profiel staat online, maar er gebeurt niets. Dat is bijna nooit pech.",
          caption="Er gebeurt niets. Hoe kan dat?", kind="hook"),
    Scene(narration="In de eerste drie seconden beslist iemand of hij doorleest. Je openingszin doet het werk.",
          caption="Je openingszin doet het werk", kind="body"),
    Scene(narration="Wij kijken met je mee en herschrijven je profiel tot het klinkt als jou op je best.",
          caption="Wij kijken met je mee", kind="body"),
    Scene(narration="Begin vandaag met DatingAssistent.", caption="Start vandaag", kind="cta"),
]


def main() -> None:
    project = "DatingAssistent"
    tpl = load_template(project)
    print("template:", tpl.source, "| logo:", tpl.logo.resolved, "| tts:", tpl.tts_provider,
          "| eigen beeld:", len(tpl.footage.images))
    out = Path(r"D:/APPS/agentos/projects/datingassistent/video/_smoke_pro.mp4")
    res = render_short(project, SCENES, out, template=tpl)
    print(json.dumps(res.__dict__, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

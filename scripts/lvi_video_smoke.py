"""Smoke-render: Liefde Voor Iedereen 9:16 short met de nieuwe brand-template."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared.video_render import render_short, Scene  # noqa: E402
from backend.shared.video_template import load_template  # noqa: E402

SCENES = [
    Scene(narration="Daten voelt spannend genoeg. Dan wil je niet ook nog twijfelen of iemand echt is.",
          caption="Daten is al spannend genoeg", kind="hook"),
    Scene(narration="Bij ons wordt elk profiel handmatig gecontroleerd. Geen nepfoto's, geen verrassingen.",
          caption="Elk profiel handmatig gecheckt", kind="body"),
    Scene(narration="En jij bepaalt zelf wat je deelt, en wanneer. Ook als je daar meer tijd voor nodig hebt.",
          caption="Jij bepaalt je eigen tempo", kind="body"),
    Scene(narration="Liefde voor iedereen. Het datingplatform waar eerlijkheid wint.",
          caption="Eerlijkheid wint", kind="cta"),
]


def main() -> None:
    project = "Liefde voor Iedereen"
    tpl = load_template(project)
    print("template:", tpl.source, "| logo:", tpl.logo.resolved, "| tts:", tpl.tts_provider,
          tpl.elevenlabs_voice_id, "| eigen beeld:", len(tpl.footage.images))
    out = Path(r"D:/APPS/agentos/projects/liefde voor iedereen/video/_smoke_pro.mp4")
    res = render_short(project, SCENES, out, template=tpl)
    print(json.dumps(res.__dict__, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

"""Re-render de LVI Wachtrij-video via het ECHTE pad (blog_video.make_blog_video).

Bewijst dat de nieuwe brand-template (logo, Poppins, ElevenLabs NL-stem, eigen
fotobeeld) ook geldt voor de 'Maak video'-knop in de Wachtrij, niet alleen voor
een losse smoke-render.
"""
import json
import sqlite3
import sys

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared import blog_video  # noqa: E402

JOB_ID = "c8054d4c-c100-44c5-bf0e-0b88103dfde1"
PROJECT = "Liefde voor Iedereen"

db = sqlite3.connect(r"D:/APPS/agentos/data/agentos.db")
db.row_factory = sqlite3.Row
row = db.execute("SELECT title, blog_html FROM content_jobs WHERE id=?", (JOB_ID,)).fetchone()
db.close()
if not row:
    raise SystemExit("job niet gevonden")

res = blog_video.make_blog_video(JOB_ID, PROJECT, row["title"], row["blog_html"],
                                 register_pack=False)
print(json.dumps(res, ensure_ascii=False, indent=2, default=str)[:2500])

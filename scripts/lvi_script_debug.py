"""Waarom valt het blogvideo-script terug op de fallback? Toon de RUWE LLM-respons."""
import sqlite3
import sys

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared import blog_video as bv  # noqa: E402
from backend.shared import social_content as sc  # noqa: E402

JOB_ID = "c8054d4c-c100-44c5-bf0e-0b88103dfde1"
db = sqlite3.connect(r"D:/APPS/agentos/data/agentos.db")
db.row_factory = sqlite3.Row
row = db.execute("SELECT title, blog_html FROM content_jobs WHERE id=?", (JOB_ID,)).fetchone()
db.close()

text = bv._strip_html(row["blog_html"])
voice = sc._brand_voice("Liefde voor Iedereen", "Liefde voor Iedereen")
print("brand_voice lengte:", len(voice))
system = (
    f"{voice}\n\n"
    "Je schrijft een kort, spreekbaar script voor een verticale video (9:16, ~30-40s) "
    "op basis van een blogartikel. Geef precies 4 scènes met headers HOOK:, BODY:, BODY:, CTA:."
)
user = f"Titel van het blog: {row['title']}\n\nInhoud van het blog:\n{text[:1800]}"
for i in range(2):
    try:
        raw = sc._sync_openmodel(system, user, max_tokens=400)
        print(f"--- poging {i+1}: len={len(raw)} ---")
        print(repr(raw[:800]))
    except Exception as e:
        print(f"--- poging {i+1} EXCEPTION: {type(e).__name__}: {e}")

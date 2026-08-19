"""Toon de RUWE _write_script-respons na de max_tokens-fix (en hoe de parser hem leest)."""
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
system = (
    f"{voice}\n\n"
    "Je schrijft een kort, spreekbaar script voor een verticale video (9:16, ~30-40s) "
    "op basis van een blogartikel. GEEN letterlijke blog-tekst citeren. "
    "Geef precies 4 scènes, elk met een header:\n"
    "HOOK: <één pakkende zin, max 12 woorden>\n"
    "BODY: <één zin, het kerninzicht, max 18 woorden>\n"
    "BODY: <één zin, een concreet voorbeeld of gevolg, max 18 woorden>\n"
    "CTA: <één zin met een zachte oproep, max 14 woorden>\n"
    "Schrijf in het Nederlands, actieve zinnen, alsof je het uitspreekt."
)
user = f"Titel van het blog: {row['title']}\n\nInhoud van het blog:\n{text[:1800]}"
raw = sc._sync_openmodel(system, user, max_tokens=400)
print("RAW len:", len(raw))
print(repr(raw[:1200]))
print("--- geparseerd ---")
for s in bv._parse_script(raw, row["title"]):
    print(f"[{s.kind}] {s.narration!r}")

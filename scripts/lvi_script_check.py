"""Check of het LLM-script voor de blogvideo ECHT geschreven wordt (of stil terugvalt op de titel)."""
import sqlite3
import sys

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared import blog_video as bv  # noqa: E402

JOB_ID = "c8054d4c-c100-44c5-bf0e-0b88103dfde1"
db = sqlite3.connect(r"D:/APPS/agentos/data/agentos.db")
db.row_factory = sqlite3.Row
row = db.execute("SELECT title, blog_html FROM content_jobs WHERE id=?", (JOB_ID,)).fetchone()
db.close()

text = bv._strip_html(row["blog_html"])
print("blog-tekst lengte:", len(text))
scenes = bv._write_script(row["title"], text, "Liefde voor Iedereen")
for s in scenes:
    print(f"[{s.kind}] narration={s.narration!r} caption={s.caption!r}")

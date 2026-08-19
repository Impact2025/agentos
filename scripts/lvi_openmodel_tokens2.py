"""Meet met de ECHTE (lange) prompt hoeveel tokens nodig zijn voor een text-blok."""
import sqlite3
import sys

import httpx

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared import blog_video as bv  # noqa: E402
from backend.shared import social_content as sc  # noqa: E402
from backend.shared.config import (  # noqa: E402
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
)

JOB_ID = "c8054d4c-c100-44c5-bf0e-0b88103dfde1"
db = sqlite3.connect(r"D:/APPS/agentos/data/agentos.db")
db.row_factory = sqlite3.Row
row = db.execute("SELECT title, blog_html FROM content_jobs WHERE id=?", (JOB_ID,)).fetchone()
db.close()
text = bv._strip_html(row["blog_html"])
voice = sc._brand_voice("Liefde voor Iedereen", "Liefde voor Iedereen")
system = (f"{voice}\n\nJe schrijft een kort videoscript. Geef precies 4 regels: "
          "HOOK:, BODY:, BODY:, CTA:. Geen uitleg, geen inleiding.")
user = f"Titel van het blog: {row['title']}\n\nInhoud van het blog:\n{text[:1800]}"

url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
for mt in (1600, 3000, 6000):
    payload = {"model": OPENMODEL_MODEL or "deepseek-v4-flash", "max_tokens": mt,
               "system": system, "messages": [{"role": "user", "content": user}]}
    with httpx.Client(timeout=300) as c:
        r = c.post(url, headers={"x-api-key": OPENMODEL_API_KEY,
                                 "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"}, json=payload)
    d = r.json()
    blocks = d.get("content") or []
    txt = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    think = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
    print(f"max_tokens={mt} stop={d.get('stop_reason')} out={d.get('usage', {}).get('output_tokens')} "
          f"thinking_chars={len(think)} text_chars={len(txt)}")
    if txt:
        print("  TEXT:", repr(txt[:500]))
        break

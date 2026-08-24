"""Hoeveel max_tokens is nodig voordat deepseek-v4-flash naast 'thinking' ook echte 'text' levert?"""
import sys

import httpx

sys.path.insert(0, r"D:/APPS/impactos")

from backend.shared.config import (  # noqa: E402
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
)

url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"

for mt in (1500, 2500, 4000):
    payload = {
        "model": OPENMODEL_MODEL or "deepseek-v4-flash",
        "max_tokens": mt,
        "system": "Je schrijft een kort videoscript. Geef precies 4 regels: HOOK:, BODY:, BODY:, CTA:. Geen uitleg.",
        "messages": [{"role": "user", "content":
                      "Titel: Zo weet ik of het liefde is. Onderwerp: eerlijk daten, wederkerigheid."}],
    }
    with httpx.Client(timeout=240) as c:
        r = c.post(url, headers={"x-api-key": OPENMODEL_API_KEY,
                                 "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"}, json=payload)
    d = r.json()
    blocks = d.get("content") or []
    kinds = [b.get("type") for b in blocks]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    think = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
    print(f"max_tokens={mt} | stop={d.get('stop_reason')} | out={d.get('usage', {}).get('output_tokens')} "
          f"| blocks={kinds} | thinking={len(think)} | text={len(text)}")
    if text:
        print("  TEXT:", repr(text[:400]))

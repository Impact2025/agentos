"""Ruwe OpenModel-respons inspecteren: wat komt er precies terug bij een leeg script?"""
import json
import sys

import httpx

sys.path.insert(0, r"D:/APPS/agentos")

from backend.shared.config import (  # noqa: E402
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
)

url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
print("url:", url, "| model:", OPENMODEL_MODEL, "| key:", bool(OPENMODEL_API_KEY))

for label, mt, sysmsg in [
    ("kort", 400, "Antwoord kort in het Nederlands."),
    ("script-400", 400, "Je schrijft een kort script. Geef 4 scenes: HOOK:, BODY:, BODY:, CTA:."),
    ("script-1200", 1200, "Je schrijft een kort script. Geef 4 scenes: HOOK:, BODY:, BODY:, CTA:."),
]:
    payload = {
        "model": OPENMODEL_MODEL or "deepseek-v4-flash",
        "max_tokens": mt,
        "system": sysmsg,
        "messages": [{"role": "user", "content":
                      "Titel: Zo weet ik of het liefde is. Schrijf het script."}],
    }
    with httpx.Client(timeout=180) as c:
        r = c.post(url, headers={"x-api-key": OPENMODEL_API_KEY,
                                 "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"}, json=payload)
    print(f"\n=== {label}: HTTP {r.status_code}")
    try:
        d = r.json()
        print("keys:", list(d.keys()))
        print("stop_reason:", d.get("stop_reason"), "| usage:", d.get("usage"))
        print("content:", json.dumps(d.get("content"), ensure_ascii=False)[:900])
    except Exception as e:
        print("geen json:", e, r.text[:300])

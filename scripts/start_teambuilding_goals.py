#!/usr/bin/env python3
"""Confirm + start de 3 teambuildingmetimpact-doelen zodat de agent zelfstandig
draait (de 'als een pro'-stap). Confirm schrijft fasen/taken naar DB; start trapt
de achtergrond-executieloop af. Doet dit voor alle draft-goals van het project."""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
PROJECT = "teambuildingmetimpact"


def post(path, payload, timeout=120):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def main():
    # Haal de draft-goals op
    with urllib.request.urlopen(f"{BASE}?project={PROJECT}&limit=20", timeout=20) as r:
        goals = json.loads(r.read().decode())
    draft = [g for g in goals if g.get("status") in ("draft", "planned")]
    print(f"[confirm+start] {len(draft)} draft-doelen gevonden")
    for g in draft:
        gid, title = g["id"], g["title"]
        try:
            s1, b1 = post("/confirm", {"goal_id": gid})
            print(f"  [confirm] {s1} | {title[:50]}")
        except urllib.error.HTTPError as e:
            print(f"  [confirm] HTTP {e.code} | {title[:50]} -> {e.read().decode()[:120]}")
            continue
        try:
            s2, b2 = post("/start", {"goal_id": gid}, timeout=60)
            print(f"  [start]   {s2} | {title[:50]}")
        except urllib.error.HTTPError as e:
            print(f"  [start]   HTTP {e.code} | {title[:50]} -> {e.read().decode()[:120]}")
    # Verificatie
    with urllib.request.urlopen(f"{BASE}?project={PROJECT}&limit=20", timeout=20) as r:
        after = json.loads(r.read().decode())
    print("\n[status na confirm+start]")
    for g in after:
        print(f"  - {g.get('status'):12} | {g['title'][:50]}")


if __name__ == "__main__":
    main()

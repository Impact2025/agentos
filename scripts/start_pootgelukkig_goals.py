#!/usr/bin/env python3
"""Bevestig + start alle DRAFT doelen van Pootgelukkig (zet de agent 'aan het werk').
Per goal: POST /api/goals/confirm -> POST /api/goals/start. Idempotent: slaat
al 'running' goals over."""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:1250/api/goals"
PROJECT = "pootgelukkig"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


def post(path, payload=None, timeout=120):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def main():
    goals = get(f"?project={PROJECT}")
    draft = [g for g in goals if g.get("status") == "draft"]
    print(f"{len(goals)} goal(s) totaal, {len(draft)} in draft.\n")
    for g in draft:
        gid = g["id"]
        title = g.get("title", "?")
        try:
            s1, _ = post(f"/confirm", {"goal_id": gid}, timeout=120)
            s2, _ = post(f"/start", {"goal_id": gid}, timeout=120)
            print(f"[ok] {title[:50]} -> confirm={s1} start={s2}")
        except urllib.error.HTTPError as e:
            print(f"[http {e.code}] {title[:50]} -> {e.read().decode()[:120]}")
        except Exception as e:
            print(f"[err] {title[:50]} -> {e}")


if __name__ == "__main__":
    main()

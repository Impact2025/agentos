#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BewaardVoorJou — SEO/Publish Director (onderdeel van de Agent Workforce).

Neemt een goedgekeurde draft en pushed naar de live BewaardVoorJou-API
(Railway). HARDE GUARDRAIL: publiceren vereist expliciete go/no-go (--approve).
Zonder --approve stopt de director bij status 'ready_to_publish' en schrijft een
escalatie-queue-item — jij beslist of het de live site op gaat.

Stdlib + requests. Gebruikt dezelfde API-endpoints als je seed-scripts:
  POST /auth/login → token
  POST /blog      → artikel aanmaken (draft)
  POST /blog/{id}/publish → publiceren

Werkwijze:
  python3 director_publish.py list                 # drafts klaar om te publiceren
  python3 director_publish.py push --slug X        # push als DRAFT (geen publish)
  python3 director_publish.py push --slug X --approve   # push + PUBLISH (externe write!)
"""
import argparse, datetime, json, os, sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DRAFTS = HERE / "drafts" / "bewaardvoorjou"
QUEUE = HERE / "escalation_queue"
QUEUE.mkdir(parents=True, exist_ok=True)

API_BASE = "https://bewaardvoorjou-production.up.railway.app/api/v1"

def _load_creds():
    # Creds NOOIT in repo. Lees uit env of een apart, niet-gecommit bestand.
    email = os.environ.get("BVJ_EMAIL")
    pw = os.environ.get("BVJ_PASSWORD")
    if not (email and pw):
        creds = HERE / "bvj_creds.json"
        if creds.exists():
            d = json.loads(creds.read_text(encoding="utf-8"))
            email, pw = d.get("email"), d.get("password")
    return email, pw

def _token(email, pw):
    r = requests.post(f"{API_BASE}/auth/login",
                      json={"email": email, "password": pw}, timeout=30)
    r.raise_for_status()
    return r.json().get("token") or r.json().get("access_token")

def push(slug, approve):
    f = DRAFTS / f"{slug}.json"
    if not f.exists():
        print(f"[publish] draft '{slug}' niet gevonden")
        return
    art = json.loads(f.read_text(encoding="utf-8"))
    email, pw = _load_creds()
    if not (email and pw):
        print("[publish] GEEN CREDS — kan niet naar live API. "
              "Zet BVJ_EMAIL/BVJ_PASSWORD of bvj_creds.json. Draft blijft staan.")
        _escalate(slug, "publish", "ontbrekende credentials")
        return
    try:
        token = _token(email, pw)
    except Exception as e:
        print(f"[publish] login mislukt: {e}")
        _escalate(slug, "publish", f"login mislukt: {e}")
        return

    # bouw payload (aligneert op je seed-script structuur)
    payload = {
        "slug": art["slug"],
        "title": art["title"],
        "section": art.get("section", "knowledge"),
        "tags": art.get("tags", ""),
        "meta_title": art.get("meta_title", art["title"]),
        "meta_description": art.get("meta_description", ""),
        "keywords": art.get("keywords", ""),
        "header_color": art.get("header_color", "#3B82F6"),
        "body_html": art.get("body_html", ""),
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = requests.post(f"{API_BASE}/blog", json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        post_id = r.json().get("id") or r.json().get("post_id")
    except Exception as e:
        print(f"[publish] aanmaken mislukt: {e}")
        _escalate(slug, "publish", f"aanmaken mislukt: {e}")
        return

    art["status"] = "pushed_draft"
    art["post_id"] = post_id
    f.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[publish] draft aangemaakt op live API (post_id={post_id})")

    if not approve:
        print("[publish] NIET gepubliceerd — wacht op jouw go/no-go. "
              "Draai met --approve om te publiceren.")
        _escalate(slug, "publish_approve",
                  f"artikel '{slug}' staat klaar als draft (post_id={post_id}) — goedkeuring gevraagd")
        return
    # expliciete go/no-go gegeven
    try:
        rp = requests.post(f"{API_BASE}/blog/{post_id}/publish", headers=headers, timeout=60)
        rp.raise_for_status()
        art["status"] = "published"
        art["published_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        f.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
        url = f"https://bewaardvoorjou.nl/kennisbank/{slug}"
        print(f"[publish] GEPUBLICEERD → {url}")
        # social-campagne aanstoten
        _trigger_social(art)
    except Exception as e:
        print(f"[publish] publiceren mislukt: {e}")
        _escalate(slug, "publish", f"publiceren mislukt: {e}")

def _escalate(slug, kind, msg):
    item = {
        "slug": slug, "kind": kind, "msg": msg,
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": "pending_review",
    }
    p = QUEUE / f"{slug}_{kind}.json"
    p.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[escalatie] {kind}: {msg} → {p}")

def _trigger_social(art):
    # roep Social Director aan voor een campagne bij dit artikel
    import subprocess
    try:
        subprocess.run([sys.executable, str(HERE / "director_social.py"),
                        "create", "--slug", art["slug"]], check=True, timeout=120)
    except Exception as e:
        print(f"[publish] social-campagne niet aangemaakt: {e}")

def list_pending():
    for f in sorted(DRAFTS.glob("*.json")):
        art = json.loads(f.read_text(encoding="utf-8"))
        if art.get("status") in ("draft_pending_review", "pushed_draft"):
            print(f"  {art['slug']}  [{art.get('status')}]  {art.get('title','')[:50]}")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("push"); p.add_argument("--slug", required=True); p.add_argument("--approve", action="store_true")
    args = ap.parse_args()
    if args.cmd == "list":
        list_pending()
    elif args.cmd == "push":
        push(args.slug, args.approve)

if __name__ == "__main__":
    main()

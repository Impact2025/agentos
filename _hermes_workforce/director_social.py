#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BewaardVoorJou — Outreach/Social Director (onderdeel van de Agent Workforce).

Genereert per gepubliceerd artikel een social-media campagne (Facebook, Instagram,
LinkedIn) in Vincent's Schrijf-DNA-stijl. HARDE GUARDRAIL: alle posts landen als
`pending_review` — géén enkele POST verlaat het systeem zonder jouw goedkeuring
(zie SOCIAL-AGENT-PLAN: "de agent schrijft, de mens publiceert").

Stdlib + requests (generatie via gateway :8899).

Werkwijze:
  python3 director_social.py create --slug X     # campagne genereren (pending_review)
  python3 director_social.py list                # openstaande campagnes
"""
import argparse, datetime, json, sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DRAFTS = HERE / "drafts" / "bewaardvoorjou"
CAMPAIGNS = HERE / "campaigns" / "bewaardvoorjou"
CAMPAIGNS.mkdir(parents=True, exist_ok=True)

GATEWAY = "http://127.0.0.1:8899/v1/chat/completions"
GATEWAY_KEY = "x"

SCHRIJF_DNA = """Vincent's stem: 1e persoon, ervaringsgericht, Nederlands, warm en concreet.
Geen hype, geen clickbait, geen 'duikt in'-taal. Emotioneel eerlijk over familie,
herinneringen en nalatenschap. Zakelijke CTA naar bewaardvoorjou.nl mag zacht."""

PLATFORMS = ["facebook", "instagram", "linkedin"]

def _genereer(prompt, max_tokens=1200, model="google/gemini-2.5-flash:free"):
    payload = {"model": model, "max_tokens": max_tokens, "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(GATEWAY, headers={"Content-Type": "application/json",
                                            "x-api-key": GATEWAY_KEY},
                          json=payload, timeout=300)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        sys.stderr.write(f"[social] gateway-fout: {e}\n")
    return ""

def create(slug):
    f = DRAFTS / f"{slug}.json"
    if not f.exists():
        print(f"[social] draft '{slug}' niet gevonden — publiceer eerst via director_publish")
        return
    art = json.loads(f.read_text(encoding="utf-8"))
    url = f"https://bewaardvoorjou.nl/kennisbank/{slug}"
    prompt = f"""BewaardVoorJou heeft een nieuw artikel gepubliceerd:
Titel: {art.get('title','')}
URL: {url}
Thema: {art.get('thema','')}

{SCHRIJF_DNA}

Schrijf voor elk platform een post (Nederlands). Gebruik EXACT deze markers, elk op een nieuwe regel, gevolgd door de tekst:
FACEBOOK:
<warme, verhalende post 100-200 woorden, eindig met 1 vraag om interactie>
INSTAGRAM:
<kortere caption ≤120 woorden, daaronder 3-5 hashtags op een nieuwe regel beginnend met #>
LINKEDIN:
<professioneler, focus op waarde/kennis, 150-220 woorden>

Schrijf alleen de drie markers met hun tekst. Geen uitleg ervoor of erna."""
    raw = _genereer(prompt)
    if not raw:
        print("[social] generatie mislukt (gateway down?)")
        return
    # parse per-platform tekst tussen markers
    posts = {}
    cur = None
    buf = []
    for line in raw.split("\n"):
        low = line.strip().upper()
        if low.startswith("FACEBOOK:"):
            if cur:
                posts[cur] = "\n".join(buf).strip()
            cur = "facebook"; buf = []; continue
        if low.startswith("INSTAGRAM:"):
            if cur:
                posts[cur] = "\n".join(buf).strip()
            cur = "instagram"; buf = []; continue
        if low.startswith("LINKEDIN:"):
            if cur:
                posts[cur] = "\n".join(buf).strip()
            cur = "linkedin"; buf = []; continue
        if cur:
            buf.append(line)
    if cur:
        posts[cur] = "\n".join(buf).strip()
    # hashtags voor instagram
    for p in PLATFORMS:
        if p not in posts:
            posts[p] = ""
    ig = posts.get("instagram", "")
    hashtags = [w for w in ig.split() if w.startswith("#")]
    posts["instagram_text"] = ig
    campaign = {
        "slug": slug, "url": url, "title": art.get("title", ""),
        "platforms": {}, "status": "pending_review",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    for p in PLATFORMS:
        text = posts.get(p, "") if p != "instagram" else posts.get("instagram_text", "")
        campaign["platforms"][p] = {
            "text": text,
            "hashtags": hashtags if p == "instagram" else [],
            "status": "pending_review",
        }
    out = CAMPAIGNS / f"{slug}.json"
    out.write_text(json.dumps(campaign, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[social] campagne klaar → {out}  (3 platforms, wachten op jouw goedkeuring)")

def list_pending():
    for f in sorted(CAMPAIGNS.glob("*.json")):
        c = json.loads(f.read_text(encoding="utf-8"))
        n = sum(1 for p in c["platforms"].values() if p["status"] == "pending_review")
        print(f"  {c['slug']}  [{c['status']}]  {n} posts pending_review")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--slug", required=True)
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "create":
        create(args.slug)
    elif args.cmd == "list":
        list_pending()

if __name__ == "__main__":
    main()

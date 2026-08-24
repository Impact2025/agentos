#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BewaardVoorJou — Content Director (onderdeel van de Agent Workforce).

Genereert TOP-content (geen 85%-grens, wél E-E-A-T + Vincent's Schrijf-DNA) voor
3 artikelen per week, uit de vastgestelde thema's. Schrijft naar lokale drafts —
publicatie loopt via de Publish Director + jouw go/no-go (geen externe write zonder
expliciete escalatie).

Stdlib + requests (ImpactOS-venv heeft requests). Gebruikt het OpenModel-gateway
(:8899) voor generatie, met Ollama-fallback.

Werkwijze:
  python3 director_content.py plan            # toon deze week (3 artikelen)
  python3 director_content.py generate [--slug X]   # genereer draft(s)
  python3 director_content.py status          # welke drafts wachten op review
"""
import argparse, datetime, json, os, sys, uuid
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DRAFTS = HERE / "drafts" / "bewaardvoorjou"
DRAFTS.mkdir(parents=True, exist_ok=True)

GATEWAY = "http://127.0.0.1:8899/v1/messages"
GATEWAY_KEY = "x"  # gateway verwacht een header, waarde irrelevant

THEMAS = [
    "dementie", "mantelzorg", "erfgoed", "nalatenschap",
    "baby", "jubileum", "familieverhalen", "levensverhaal",
]

# Vincent's Schrijf-DNA (samenvatting — volledige regels staan in vault)
SCHRIJF_DNA = """Schrijf in Vincent's stem: 1e persoon, ervaringsgericht, Nederlands,
niet afgezaagd. E-E-A-T: toon expertise en betrouwbaarheid. Structureer met
tussenkoppen, een intro die het probleem raakt, en een zachte CTA naar
bewaardvoorjou.nl. Geen clickbait, geen AI-woordgebruik ('duikt in', 'in het
huidige landschap'). Warm, concreet, deelbaar."""

def _genereer(prompt, max_tokens=2200, model="deepseek-v4-flash", route="chat"):
    if route == "chat":
        payload = {
            "model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            r = requests.post(f"{GATEWAY.replace('/v1/messages','/v1/chat/completions')}",
                              headers={"Content-Type": "application/json", "x-api-key": GATEWAY_KEY},
                              json=payload, timeout=300)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            sys.stderr.write(f"[content] gateway-fout: {e}\n")
        return ""
    # anthropic route (fallback)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = requests.post(GATEWAY, headers={"Content-Type": "application/json",
                                           "x-api-key": GATEWAY_KEY},
                          json=payload, timeout=120)
        if r.status_code == 200:
            for blk in r.json().get("content", []):
                if blk.get("type") == "text":
                    return blk["text"]
    except Exception as e:
        sys.stderr.write(f"[content] gateway-fout: {e}\n")
    return ""

def week_slug(index):
    """Bepaalde, leesbare slug voor artikel #index deze week."""
    week = datetime.date.today().isocalendar()[1]
    return f"week{week}-{index:02d}"

def plan_week():
    today = datetime.date.today()
    # 3 artikelen, ma/wo/vr cadans
    dagen = [0, 2, 4]
    out = []
    for i, d in enumerate(dagen):
        pub = today + datetime.timedelta(days=d)
        out.append({
            "nr": i + 1,
            "slug": week_slug(i + 1),
            "thema": THEMAS[(today.day + i) % len(THEMAS)],
            "publish": pub.isoformat(),
        })
    return out

def generate(slug=None):
    plan = plan_week()
    targets = [p for p in plan if (slug is None or p["slug"] == slug)]
    if slug and not targets:
        print(f"[content] geen artikel met slug '{slug}' in deze week-plan")
        return
    for p in targets:
        if (DRAFTS / f"{p['slug']}.json").exists():
            print(f"[content] {p['slug']} bestaat al — overslaan (verwijder om te forceren)")
            continue
        prompt = f"""Schrijf een Nederlands artikel voor BewaardVoorJou (thema: {p['thema']}).

{SCHRIJF_DNA}

Schrijf het artikel als PLATTE TEKST (geen HTML, geen markdown). Structuur:
Eerst een titelregel.
Dan alinea's. Gebruik "## " vóór elke tussenkop.
Minstens 600 woorden, warm en concreet, één zachte verwijzing naar bewaardvoorjou.nl.

Schrijf ALLEEN het artikel. Eindig je antwoord met precies deze regel op een nieuwe lijn:
###EIND###"""
        text = _genereer(prompt, max_tokens=2600, model="google/gemini-2.5-flash:free", route="chat")
        if not text:
            print(f"[content] {p['slug']}: generatie mislukt (gateway down?)")
            continue
        # chat-route: CoT staat vooraan, echte content erna, sentinel achteraan
        body = text
        if "###EIND###" in text:
            body = text[:text.rfind("###EIND###")]
        # snij CoT vooraan weg: zoek eerste regel na 25% die eruitziet als content
        lines = body.split("\n")
        start = 0
        cot_markers = ("we need", "let's", "ik zal", "analyze", "1.", "so final",
                       "dus:", "here's", "here is", "the user", "need to", "i need")
        for i, ln in enumerate(lines):
            if i < len(lines) * 0.25:
                continue
            s = ln.strip().lower()
            if s and not s.startswith(cot_markers) and len(s.split()) >= 2:
                start = i
                break
        body = "\n".join(lines[start:]).strip()
        html = _text_to_html(body, p["thema"])
        if not html:
            print(f"[content] {p['slug']}: geen leesbare content uit response")
            continue
        art = _derive_meta(html, p["slug"], p["thema"])
        art["slug"] = p["slug"]
        art["thema"] = p["thema"]
        art["status"] = "draft_pending_review"
        art["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        with open(DRAFTS / f"{p['slug']}.json", "w", encoding="utf-8") as f:
            json.dump(art, f, ensure_ascii=False, indent=2)
        print(f"[content] {p['slug']}: draft geschreven → {DRAFTS / (p['slug']+'.json')}")

def _text_to_html(body, thema):
    """Converteer platte tekst (titelregel + '## ' koppen + alinea's) naar schone HTML."""
    import re
    lines = [l.rstrip() for l in body.split("\n")]
    # strip eventuele plan-rommel bovenaan (regels zonder titel/kop die nergens op lijken)
    html_parts = []
    first = True
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("## "):
            html_parts.append(f"<h2>{_esc(line[3:].strip())}</h2>")
        elif first:
            # eerste niet-lege regel = titel
            html_parts.append(f"<h1>{_esc(line.strip())}</h1>")
            first = False
        else:
            html_parts.append(f"<p>{_esc(line.strip())}</p>")
    return "\n".join(html_parts)

def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _derive_meta(html, slug, thema):
    """Haal titel/meta/keywords deterministisch uit de HTML (model-onafhankelijk)."""
    import re
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = re.sub("<[^>]+>", "", m.group(1)).strip() if m else slug
    # intro = eerste <p> na de h1
    paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    intro = re.sub("<[^>]+>", "", paras[0]).strip() if paras else ""
    meta = (intro[:152] + "…") if len(intro) > 155 else intro
    words = re.findall(r"\b\w+\b", re.sub("<[^>]+>", " ", html).lower())
    # eenvoudige keyword-extractie: unieke, relevante nl-woorden
    stop = set("de het een en van in op met voor aan als is dat we onze je zijn wordt naar".split())
    kw = []
    for w in words:
        if len(w) > 5 and w not in stop and w not in kw:
            kw.append(w)
        if len(kw) >= 5:
            break
    return {
        "title": title,
        "meta_title": f"{title} | BewaardVoorJou",
        "meta_description": meta,
        "keywords": ", ".join(kw),
        "tags": f"{thema},levensverhaal,herinneringen",
        "section": "knowledge",
        "header_color": "#3B82F6",
        "body_html": html,
    }

def status():
    files = sorted(DRAFTS.glob("*.json"))
    if not files:
        print("(geen drafts)")
        return
    for f in files:
        art = json.loads(f.read_text(encoding="utf-8"))
        print(f"  [{art.get('status','?')}] {art['slug']} — {art.get('title','')[:50]}")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    g = sub.add_parser("generate"); g.add_argument("--slug", default=None)
    sub.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "plan":
        for p in plan_week():
            print(f"  #{p['nr']} {p['slug']}  thema={p['thema']}  publish={p['publish']}")
    elif args.cmd == "generate":
        generate(args.slug)
    elif args.cmd == "status":
        status()

if __name__ == "__main__":
    main()

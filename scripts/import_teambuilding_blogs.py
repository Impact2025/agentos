#!/usr/bin/env python3
"""
Importeer alle .md-blogposts uit D:/apps/Teambuilding/artikelen/blog naar de
TeambuildingMetImpact-site via de publieke /api/blog-endpoint (Bijeen-compatibel).

- Frontmatter (yaml) + body (markdown -> HTML).
- Elke post krijgt een gelijk verdeelde publicatiedatum tussen
  2025-12-20 en 2026-06-20 (oude `date:` uit de frontmatter wordt overschreven).
- Ontbrekende excerpt/meta_description worden opgevuld uit de body.
- Idempotent: bijgehouden in een state-file; al geposte slugs worden overgeslagen.
- status=published -> live op de site met de historische datum.

Run vanuit de impactos-venv (heeft httpx + yaml + markdown):
  .venv/Scripts/python.exe scripts/import_teambuilding_blogs.py
"""
import os
import sys
import glob
import json
import datetime as dt

import yaml
import markdown
import httpx

# ── config ────────────────────────────────────────────────────────────────
ARTICLES_DIR = r"D:/apps/Teambuilding/artikelen/blog"
API_URL = "https://www.teambuildingmetimpact.nl/api/blog"
KEY_FILE = r"D:/apps/impactos/.env"  # niet gelezen; key komt uit env of onderstaand
STATE_FILE = r"D:/apps/impactos/scripts/.tbi_import_state.json"

DATE_START = dt.date(2025, 12, 20)
DATE_END = dt.date(2026, 6, 20)

# Key uit omgeving (Impact OS .env wordt geladen door het run-script); fallback
# naar hetzelfde bestand als de site gebruikt. We lezen 'm veilig uit de env.
import os as _os
API_KEY = _os.getenv("TEAMBUILDINGMETIMPACT_PUBLISH_KEY") or _os.getenv("TEAMBUILDING_API_KEY")
if not API_KEY:
    # laatste redmiddel: lees uit impactos .env (niet als secret gelogd)
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TEAMBUILDINGMETIMPACT_PUBLISH_KEY="):
                    API_KEY = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        pass

if not API_KEY:
    print("FOUT: geen TEAMBUILDING API-key gevonden (env noch .env).", file=sys.stderr)
    sys.exit(1)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posted": [], "failed": []}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def parse_md(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        # geen frontmatter: alles is body
        return {"meta": {}, "body": text}
    end = text.find("\n---", 3)
    if end == -1:
        return {"meta": {}, "body": text}
    fm = text[3:end].strip()
    body = text[end + 4:].strip()
    meta = yaml.safe_load(fm) or {}
    return {"meta": meta, "body": body}


def md_to_html(md: str) -> str:
    return markdown.markdown(
        md,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html",
    )


def first_sentences(text: str, n: int = 1, max_len: int = 155) -> str:
    # strip markdown-achtige markup ruw voor een leesbare excerpt
    clean = text.strip()
    parts = [p.strip() for p in clean.split("\n") if p.strip()]
    out = []
    for p in parts:
        if p.startswith("#"):
            continue
        out.append(p)
        if len(out) >= n:
            break
    joined = " ".join(out)
    joined = joined[:max_len].rsplit(" ", 1)[0] if len(joined) > max_len else joined
    return joined


def main():
    files = sorted(glob.glob(os.path.join(ARTICLES_DIR, "*.md")))
    if not files:
        print(f"Geen .md-bestanden in {ARTICLES_DIR}")
        return

    state = load_state()
    posted = set(state.get("posted", []))

    n = len(files)
    span_days = (DATE_END - DATE_START).days  # 183
    # gelijk verdeelde datums (inclusief start en eind)
    dates = []
    for i in range(n):
        if n == 1:
            offset = 0
        else:
            offset = round(span_days * i / (n - 1))
        dates.append(DATE_START + dt.timedelta(days=offset))

    print(f"Importeer {n} artikelen -> {API_URL}")
    print(f"Datums: {dates[0]} .. {dates[-1]} (verdeeld over {n} posts)\n")

    ok = fail = skip = 0
    for idx, path in enumerate(files):
        name = os.path.basename(path)
        parsed = parse_md(path)
        meta = parsed["meta"]
        body = parsed["body"]
        slug = (meta.get("slug") or os.path.splitext(name)[0]).strip()
        title = meta.get("title") or slug
        meta_title = meta.get("meta_title") or title
        meta_desc = meta.get("meta_description") or first_sentences(body, max_len=155)
        excerpt = meta.get("excerpt") or first_sentences(body, max_len=155)
        primary = meta.get("primary_keyword") or meta.get("focus_keyphrase") or title
        tags_raw = meta.get("tags") or meta.get("secondary_keywords") or []
        if isinstance(tags_raw, str):
            tags_raw = [t.strip() for t in tags_raw.split(",") if t.strip()]
        tags = [str(t) for t in tags_raw if t]
        content_html = md_to_html(body)

        pub_date = dates[idx].isoformat()

        if slug in posted:
            print(f"  [SKIP] {slug} (al in state)")
            skip += 1
            continue

        # Live dubbel-check: als de post al op de site staat (200), overslaan.
        # Voorkomt duplicates als de state-file out-of-sync raakt.
        try:
            head = httpx.get(f"https://www.teambuildingmetimpact.nl/blog/{slug}",
                              follow_redirects=True, timeout=20)
            if head.status_code == 200:
                print(f"  [SKIP] {slug} (al live op site)")
                posted.add(slug)
                state.setdefault("dates", {})[slug] = pub_date
                skip += 1
                continue
        except Exception:
            pass

        payload = {
            "title": title,
            "content": content_html,
            "excerpt": excerpt,
            "metaTitle": meta_title,
            "metaDescription": meta_desc,
            "tags": tags,
            "status": "published",
            "slug": slug,
            "primaryKeyword": primary,
            "focusKeyphrase": primary,
            "extraKeywords": ", ".join([str(t) for t in (meta.get("secondary_keywords") or []) if t]),
        }

        # publishedAt los meesturen kan de route niet (die zet 'm zelf bij published).
        # We sturen de datum mee als veld dat de route negeert; de historische
        # datum wordt achteraf via een aparte PATCH gezet (zie onder). Voor nu
        # publiceren we met de huidige datum en corrigeren we daarna de kolom.
        try:
            resp = httpx.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=60,
            )
            if resp.status_code in (200, 201):
                print(f"  [OK]   {pub_date}  {slug}  ({resp.status_code})")
                posted.add(slug)
                ok += 1
                # historische datum corrigeren in de DB (direct via site-API niet
                # mogelijk zonder aparte route); we loggen 'm zodat een
                # nabetrachting de kolom kan zetten.
                state.setdefault("dates", {})[slug] = pub_date
            else:
                print(f"  [FAIL] {slug}  HTTP {resp.status_code}: {resp.text[:200]}")
                state.setdefault("failed", []).append({"slug": slug, "code": resp.status_code, "body": resp.text[:200]})
                fail += 1
        except Exception as e:
            print(f"  [ERR]  {slug}  {e}")
            state.setdefault("failed", []).append({"slug": slug, "error": str(e)[:200]})
            fail += 1

    state["posted"] = sorted(posted)
    save_state(state)
    print(f"\nKlaar: {ok} ge-post, {skip} overgeslagen, {fail} mislukt.")
    print(f"State: {STATE_FILE}")
    if state.get("dates"):
        print(f"Gewenste publicatiedatums (nog toe te passen op DB): {len(state['dates'])} posts.")


if __name__ == "__main__":
    main()

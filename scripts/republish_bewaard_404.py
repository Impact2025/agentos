"""Re-publiceer Bewaard-voor-Jou content_jobs die in Agent OS 'published' zijn
maar op bewaardvoorjou.nl 404 geven (phantom-publish).

- Leest de opgeslagen blog_html uit data/agentos.db.
- Draait dezelfde cleaners als backend/domains/publish/content_pipeline.py
  (_strip_meta_and_suggestions, _strip_duplicate_header, _smart_truncate).
- Bouwt de niet-BIJEEN payload en POST naar BEWAARDVOORJOU_PUBLISH_URL.
- Stuurt slug=None als de opgeslagen slug ongeldig is, zodat de backend een
  geldige slug afleidt (afgekapte/trailing-dash slugs => 404).

Gebruik:  python scripts/republish_bewaard_404.py            # echte run
          python scripts/republish_bewaard_404.py --dry-run  # alleen tonen
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO, "data", "agentos.db")
ENV = os.path.join(REPO, ".env")


# ── .env laden ────────────────────────────────────────────────────────────
def load_env(path: str) -> dict:
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


# ── Sanitizers (1:1 gekopieerd uit content_pipeline.py) ───────────────────
_META_BLOCK_RE = re.compile(
    r"<h2[^>]*>\s*(?:meta[- ]?titel|meta[- ]?title|meta[- ]?beschrijving|"
    r"meta[- ]?description|suggesties? (?:voor )?interne links?)\s*</h2>.*?"
    r"(?=<h2|$)"
    r"|<h3[^>]*>\s*(?:meta[- ]?titel|meta[- ]?title|meta[- ]?beschrijving|"
    r"meta[- ]?description|suggesties? (?:voor )?interne links?)\s*</h3>.*?"
    r"(?=<h2|<h3|$)"
    r"|<!--[^-]*\bmeta[- ]?(?:titel|title|beschrijving|description)[^-]*-->"
    r"|<p>\s*<strong>\s*meta[- ]?(?:titel|title|beschrijving|description)\s*:"
    r".*?</p>",
    re.IGNORECASE | re.DOTALL,
)
_META_COMMENT_ATTR_RE = re.compile(
    r"<!\s*--\s*META\s+title=\"([^\"]*)\"\s+description=\"([^\"]*)\"\s*--\s*>",
    re.IGNORECASE | re.DOTALL,
)
_META_COMMENT_COLON_RE = re.compile(
    r"<!\s*--\s*meta[- ]?(titel|title|beschrijving|description)\s*:\s*(.*?)\s*--\s*>",
    re.IGNORECASE | re.DOTALL,
)
_META_P_COLON_RE = re.compile(
    r"<p>\s*<strong>\s*meta[- ]?(titel|title|beschrijving|description)\s*:\s*</strong>"
    r"\s*(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
_META_H_COLON_RE = re.compile(
    r"<h[23][^>]*>\s*meta[- ]?(titel|title|beschrijving|description)\s*</h[23]>\s*<p>(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_meta_and_suggestions(html_body):
    if not html_body:
        return html_body, "", ""
    meta_title = ""
    meta_desc = ""
    mc = _META_COMMENT_ATTR_RE.search(html_body)
    if mc:
        meta_title = mc.group(1).strip()
        meta_desc = mc.group(2).strip()
        html_body = html_body.replace(mc.group(0), "")
    for kind, val in _META_COMMENT_COLON_RE.findall(html_body):
        if "titel" in kind.lower() or "title" in kind.lower():
            meta_title = meta_title or val.strip()
        else:
            meta_desc = meta_desc or val.strip()
    html_body = _META_COMMENT_COLON_RE.sub("", html_body)
    for kind, val in _META_P_COLON_RE.findall(html_body):
        if "titel" in kind.lower() or "title" in kind.lower():
            meta_title = meta_title or val.strip()
        else:
            meta_desc = meta_desc or val.strip()
    html_body = _META_P_COLON_RE.sub("", html_body)
    for kind, val in _META_H_COLON_RE.findall(html_body):
        if "titel" in kind.lower() or "title" in kind.lower():
            meta_title = meta_title or val.strip()
        else:
            meta_desc = meta_desc or val.strip()
    html_body = _META_H_COLON_RE.sub("", html_body)
    cleaned = _META_BLOCK_RE.sub("", html_body).strip()
    return cleaned, meta_title, meta_desc


def _smart_truncate(text, max_len):
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.5:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


def _strip_duplicate_header(html):
    if not html:
        return html
    out = html.strip()
    out = re.sub(r"^\s*<!--[\s\S]*?-->\s*", "", out, flags=re.I)
    out = re.sub(r"^\s*<h1\b[^>]*>[\s\S]*?<\/h1>\s*", "", out, flags=re.I)
    out = re.sub(r"^\s*<p\b[^>]*>(?:(?!<\/p>)[\s\S])*?gepubliceerd op[\s\S]*?<\/p>\s*", "", out, flags=re.I)
    out = re.sub(r"^\s*<p\b[^>]*>\s*(?:<strong>)?\s*samenvatting\s*:?\s*(?:<\/strong>)?\s*[\s\S]*?<\/p>\s*", "", out, flags=re.I)
    out = re.sub(r"^\s*(?:<hr\s*/?>\s*)+", "", out, flags=re.I)
    for _ in range(4):
        before = out
        out = re.sub(r"^\s*<p\b[^>]*>\s*(?:publicatiedatum|project|auteur|datum|door)\b[^\n<]*?</p>\s*", "", out, flags=re.I)
        out = re.sub(r"^\s*(?:publicatiedatum|project|auteur|datum)\s*:\s*[^\n]*\n", "", out, flags=re.I)
        if out == before:
            break
    return out.strip()


VALID_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Voor posts die al live zijn onder een ándere (geldige) slug: stuur die slug
# mee zodat de backend upsert (overschrijft) ipv een duplicate post te maken.
# (De opgeslagen Agent OS-slug is ongeldig/afgekapt → zou anders een 2e post maken.)
SLUG_OVERRIDE = {
    "levensverhaal vastleggen: complete gids + casestudy anton (127 projecten)":
        "levensverhaal-vastleggen-complete-gids-casestudy-anton-127-projecten",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env(ENV)
    publish_url = env.get("BEWAARDVOORJOU_PUBLISH_URL", "").strip()
    publish_key = env.get("BEWAARDVOORJOU_PUBLISH_KEY", "").strip()
    if not publish_url or not publish_key:
        print("FOUT: BEWAARDVOORJOU_PUBLISH_URL/_KEY ontbreken in .env"); sys.exit(1)
    print(f"Publish URL: {publish_url}")

    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    cur = c.cursor()
    cur.execute("SELECT id,name FROM sites WHERE lower(name) LIKE '%bewaard%'")
    site = cur.fetchone()
    sid = site["id"]
    cur.execute("SELECT id,title,slug,status,blog_html,seo_score,publish_result FROM content_jobs WHERE site_id=?", (sid,))
    jobs = cur.fetchall()

    targets = []
    for j in jobs:
        if j["status"] != "published":
            continue
        if not (j["blog_html"] or "").strip():
            continue
        slug = j["slug"] or ""
        invalid_slug = not VALID_SLUG.match(slug)
        # Decideren of deze job een echte live-post heeft:
        #  - ongeldige slug  -> backend kan de route nooit matchen -> 404
        #  - publish_result.site.success != true  -> nooit verzonden/gefaald
        #  - geen echte url in publish_result en geen 'site'-key -> ongeverifieerd
        needs_repair = invalid_slug
        pr = (j["publish_result"] or "").strip()
        site_ok = None
        has_url = False
        if pr:
            try:
                d = json.loads(pr)
                site_ok = d.get("site", {}).get("success") is True if isinstance(d.get("site"), dict) else None
                has_url = bool(d.get("url") or (isinstance(d.get("post"), dict) and d["post"].get("url")))
                has_site = "site" in d
                if invalid_slug:
                    needs_repair = True
                elif has_site and site_ok is False:
                    needs_repair = True
                elif not has_site and not has_url:
                    needs_repair = True
            except Exception:
                if invalid_slug:
                    needs_repair = True
        flag = "REPAIR" if needs_repair else "ok    "
        targets.append((j["id"], j["title"], slug, invalid_slug, len(j["blog_html"]), j["seo_score"], flag))

    print(f"\nGevonden 'published' Bewaard-jobs met body: {len(targets)}")
    for t in targets:
        print(f"  [{t[6]}] seo={t[5]:>4}  {t[4]:>6}c  {t[1][:58]}")

    repair = [t for t in targets if t[6] == "REPAIR"]
    print(f"\nTe her-publiceren (phantom 404 / nooit verzonden): {len(repair)}")
    for t in repair:
        print(f"  - {t[1][:70]}\n      oud slug: {t[2]!r}")

    if args.dry_run:
        print("\n[DRY-RUN] geen POST verzonden."); return

    import httpx
    for tid, title, slug, invalid, _, seo, _flag in repair:
        cur.execute("SELECT blog_html FROM content_jobs WHERE id=?", (tid,))
        html_body = cur.fetchone()["blog_html"]
        html_body, parsed_title, parsed_desc = _strip_meta_and_suggestions(html_body)
        html_body = _strip_duplicate_header(html_body)
        text = re.sub(r"<[^>]+>", " ", html_body or "")
        text = re.sub(r"\s+", " ", text).strip()
        meta_desc = parsed_desc or ((text[:155].rstrip() + "…") if len(text) > 155 else text)
        first_p = re.search(r"<p>(.*?)</p>", html_body or "", re.S)
        raw_excerpt = re.sub(r"<[^>]+>", "", first_p.group(1)).strip() if first_p else ""
        excerpt = _smart_truncate(raw_excerpt, 200)

        send_slug = slug
        if tid in SLUG_OVERRIDE:
            send_slug = SLUG_OVERRIDE[tid]
        elif invalid:
            send_slug = None  # backend leidt geldige slug af
        payload = {
            "title": title,
            "content": (html_body or "").strip(),
            "slug": send_slug,   # backend leidt geldige slug af indien None
            "seoTitle": (parsed_title or title)[:60],
            "seoDescription": meta_desc,
            "tags": [],
            "source": "agent-os",
        }
        try:
            resp = httpx.post(publish_url, json=payload,
                              headers={"Authorization": f"Bearer {publish_key}"},
                              timeout=90, follow_redirects=True)
            ok = resp.status_code in (200, 201)
            try:
                data = resp.json()
            except Exception:
                data = {}
            url = data.get("url") or data.get("post", {}).get("url") or ""
            result = {"success": ok, "status_code": resp.status_code,
                      "url": url, "detail": (data if not isinstance(data, dict) else None)}
            print(f"\n→ {title[:60]}\n    HTTP {resp.status_code}  url={url}")
            if not ok:
                print(f"    BODY: {resp.text[:300]}")
            # update publish_result
            cur.execute("UPDATE content_jobs SET publish_result=? WHERE id=?",
                        (json.dumps(result, ensure_ascii=False), tid))
            c.commit()
        except Exception as e:
            print(f"\n→ {title[:60]}\n    EXCEPTION: {e}")
            cur.execute("UPDATE content_jobs SET publish_result=? WHERE id=?",
                        (json.dumps({"success": False, "error": str(e)[:200]}, ensure_ascii=False), tid))
            c.commit()

    print("\nKlaar. Verifieer daarna op bewaardvoorjou.nl/blog/<slug> (HTTP 200).")
    c.close()


if __name__ == "__main__":
    main()

"""Schrijf de ge-upgrade Bijeen pending_review-jobs (uit Gauntlet) terug naar de vault
als schone SEO-artikelen. Alleen Bijeen-project. Idempotent: overschrijft bestaande."""
import sqlite3, os, sys, re
sys.path.insert(0, r"D:\APPS\impactos")

VAULT = r"D:\APPS\Hermes Brein\Hermes Breind\10_Projects\Bijeen\content"

def slugify(s):
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s[:70]

conn = sqlite3.connect(r"D:\APPS\impactos\data\impactos.db")
conn.row_factory = sqlite3.Row

# Alle Bijeen content_jobs met status pending_review die uit een gauntlet-run komen
rows = conn.execute("""
    SELECT cj.id, cj.title, cj.seo_score, cj.slug, cj.blog_html, cj.keyword
    FROM content_jobs cj
    JOIN sites s ON cj.site_id = s.id
    WHERE s.name='Bijeen' AND cj.status='pending_review' AND cj.blog_html IS NOT NULL AND cj.blog_html != ''
""").fetchall()

written = 0
for r in rows:
    html = r["blog_html"] or ""
    if len(html) < 200:
        continue
    slug = r["slug"].replace("herschrijf-het-artikel-voor-bijeen-", "").replace("herschrijf-het-artikel-", "")
    slug = slugify(slug)[:70]
    if not slug:
        continue
    fm = f"""---
title: "{r['title']}"
slug: {slug}
keyword: "{r['keyword'] or ''}"
description: "{r['title']} — wereldklasse Bijeen-content (Vincent van Munster)."
created_at: 2026-08-14
word_count: {len(html.split())}
seo_score: {r['seo_score']}
source: gauntlet-loop
---

"""
    path = os.path.join(VAULT, slug + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(fm + html)
    written += 1
    print(f"OK {slug}.html  score={r['seo_score']}  {len(html)} chars")

print(f"KLAAR — {written} Bijeen artikelen naar vault.")

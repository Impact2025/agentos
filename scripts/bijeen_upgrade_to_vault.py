"""Schrijf de 6 ge-upgrade Gauntlet-content_jobs terug naar de vault als schone
SEO-artikelen (HTML + YAML-frontmatter) zodat ze geseed kunnen worden naar de repo.
Draait in de ImpactOS venv."""
import sqlite3, os, sys, re
sys.path.insert(0, r"D:\APPS\impactos")

VAULT = r"D:\APPS\Hermes Brein\Hermes Breind\10_Projects\Bijeen\content"

# run_id -> (schone slug, keyword, title voor frontmatter)
MAP = {
    "gaunt-20260814-083252-cc76aa": ("wmo-rapportage-evenement-software", "WMO rapportage evenement software",
                                     "WMO-rapportage voor je evenement: zo meet je impact met Bijeen"),
    "gaunt-20260814-083253-40b14d": ("deelnemersbeheer-buurtfestival-gratis", "deelnemersbeheer buurtfestival gratis",
                                     "Deelnemersbeheer voor een buurtfestival: gratis en zonder gedoe"),
    "gaunt-20260814-083254-8649fd": ("vrijwilligersdag-organiseren-checklist-2026", "vrijwilligersdag organiseren checklist",
                                     "Vrijwilligersdag organiseren: de complete checklist (2026)"),
    "gaunt-20260814-083254-64e2b3": ("buurtinitiatieven-impact-meetbaar-maken", "buurtinitiatieven evenementen meten impact",
                                     "Buurtinitiatieven: meet de impact van je evenementen"),
    "gaunt-20260814-083255-757a02": ("gemeente-evenement-aanmelden-systeem", "gemeente evenement aanmelden systeem",
                                     "Gemeente-evenement aanmelden: bespaar tot 2 uur administratie per bijeenkomst"),
    "gaunt-20260814-083255-3393c2": ("geslaagd-evenement-organiseren-van-plan-tot-nazorg", "Alle stappen om een geslaagd evenement te organiseren",
                                     "Van plan tot nazorg: een geslaagd evenement organiseer je zo"),
}

conn = sqlite3.connect(r"D:\APPS\impactos\data\impactos.db")
conn.row_factory = sqlite3.Row

def slugify(s):
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s[:70]

for run_id, (slug, kw, title) in MAP.items():
    r = conn.execute("SELECT published_job_id FROM gauntlet_runs WHERE id=?", (run_id,)).fetchone()
    jid = r["published_job_id"]
    j = conn.execute("SELECT blog_html, seo_score FROM content_jobs WHERE id=?", (jid,)).fetchone()
    html = j["blog_html"] or ""
    # strip eventuele wrapper, bewaar alleen body
    fm = f"""---
title: "{title}"
slug: {slug}
keyword: "{kw}"
description: "{title} — praktische gids van Vincent van Munster (Bijeen.app) voor welzijnsorganisaties."
created_at: 2026-08-14
word_count: {len(html.split())}
seo_score: {j['seo_score']}
source: gauntlet-loop
---

"""
    out = fm + html
    path = os.path.join(VAULT, slug + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"OK {slug}.html  score={j['seo_score']}  {len(html)} chars")

print("KLAAR — 6 artikelen in vault.")
